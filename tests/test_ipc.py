"""Actual Windows pipe tests using a test dispatcher; Capture One is never opened."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import pytest

from c1ai.client import RequestUncertain, send_request
from c1ai.windows_owner import (NamedPipeServer, OwnerAlreadyRunning, PROTOCOL,
                       UserSecurity, connect_pipe, receive_frame, send_frame, win32)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows named-pipe integration tests")


class Host:
    def __init__(self, dispatch):
        suffix = uuid.uuid4().hex
        self.stop = threading.Event()
        self.server = NamedPipeServer(dispatch, address=rf"\\.\pipe\C1AI.Test.{suffix}",
                                      name=rf"Local\C1AI.Test.{suffix}")
        self.errors = []

        def serve():
            try:
                self.server.serve(self.stop)
            except Exception as error:
                self.errors.append(error)

        self.thread = threading.Thread(target=serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        assert self.server.ready.wait(5), self.errors
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(15)
        assert not self.thread.is_alive(), "IPC host did not stop"
        assert not self.errors

    def call(self, request, **kwargs):
        return send_request(request, address=self.server.address, autostart=False, **kwargs)


def test_multiple_clients_are_serialized():
    active, maximum = 0, 0
    seen = []

    def dispatch(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.02)
        seen.append(request["request_id"])
        active -= 1
        return {"status": "verified", "request_id": request["request_id"]}

    with Host(dispatch) as host, ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(host.call, {"command": "test", "request_id": str(index)}) for index in range(8)]
        result = [future.result(timeout=10) for future in futures]
    assert maximum == 1
    assert sorted(seen) == list(map(str, range(8)))
    assert {item["request_id"] for item in result} == set(seen)


def test_disconnect_does_not_cancel_or_replay_accepted_operation(tmp_path):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    path = tmp_path / "durable-test-result.json"
    calls = []

    def dispatch(request):
        if request["command"] == "operations":
            return json.loads(path.read_text())
        calls.append(request["request_id"])
        started.set()
        assert release.wait(5)
        result = {"status": "verified", "request_id": request["request_id"], "value": 17}
        path.write_text(json.dumps(result))
        completed.set()
        return result

    with Host(dispatch) as host:
        with pytest.raises(RequestUncertain) as error:
            host.call({"command": "adjust", "request_id": "disconnect-test"}, timeout=0.15)
        assert started.wait(2)
        assert error.value.request_id == "disconnect-test"
        assert error.value.as_result()["replay_allowed"] is False
        release.set()
        assert completed.wait(3)
        result = host.call({"command": "operations", "action": "get", "request_id": "disconnect-test"})
        assert result["value"] == 17
    assert calls == ["disconnect-test"]


def test_startup_conflict_never_builds_second_dispatcher():
    calls = []
    with Host(lambda request: {"status": "ok"}) as host:
        contender = NamedPipeServer(dispatcher_factory=lambda: calls.append("built"),
                                    address=host.server.address, name=host.server.name)
        errors = []

        def compete():
            try:
                contender.serve(threading.Event())
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=compete)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], OwnerAlreadyRunning)
        assert calls == []
        assert host.call({"command": "context"}) == {"status": "ok"}


def test_incompatible_protocol_is_rejected_before_dispatch():
    seen = []
    with Host(lambda request: seen.append(request) or {}) as host:
        handle = connect_pipe(host.server.address)
        try:
            send_frame(handle, {"protocol": PROTOCOL, "version": 999, "kind": "hello"}, time.monotonic() + 3)
            result = receive_frame(handle, time.monotonic() + 3)
            assert result["error"] == "PROTOCOL_MISMATCH"
        finally:
            win32().CloseHandle(handle)
    assert not seen


def test_pipe_dacl_grants_access_only_to_current_user():
    import win32security

    with Host(lambda request: {"status": "ok"}) as host:
        handle = connect_pipe(host.server.address)
        try:
            security = win32security.GetSecurityInfo(int(handle), win32security.SE_KERNEL_OBJECT,
                                                     win32security.DACL_SECURITY_INFORMATION)
            dacl = security.GetSecurityDescriptorDacl()
            assert dacl is not None and dacl.GetAceCount() == 1
            ace = dacl.GetAce(0)
            assert ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
            assert win32security.ConvertSidToStringSid(ace[2]) == win32().sid()
        finally:
            win32().CloseHandle(handle)


def test_two_process_clients_share_the_same_owner(tmp_path):
    recorded = []

    def dispatch(request):
        recorded.append(request["request_id"])
        return {"status": "ok", "request_id": request["request_id"]}

    script = ("import json,sys; from c1ai.client import send_request; "
              "print(json.dumps(send_request({'command':'context','request_id':sys.argv[2]},"
              "address=sys.argv[1],autostart=False)))")
    with Host(dispatch) as host:
        children = [subprocess.Popen([sys.executable, "-c", script, host.server.address, f"child-{i}"],
                                    cwd=Path(__file__).resolve().parents[1],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
                    for i in range(2)]
        for i, child in enumerate(children):
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr.decode(errors="replace")
            assert json.loads(stdout)["request_id"] == f"child-{i}"
    assert set(recorded) == {"child-0", "child-1"}


def test_client_rejects_remote_endpoints():
    with pytest.raises(Exception, match="Only a local"):
        connect_pipe(r"\\remote-host\pipe\C1AI.Test")


def test_mcp_schema_has_six_explicit_tools():
    import asyncio
    from c1ai.mcp_server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {tool.name for tool in tools} == {"context", "images", "read", "adjust", "preview", "operations"}
    adjust = next(tool for tool in tools if tool.name == "adjust")
    assert {"target", "module", "patch", "observation_token"} <= set(adjust.inputSchema["required"])
    assert "payload" not in adjust.inputSchema["properties"]


def test_official_mcp_client_can_initialize_stdio_and_list_tools():
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        parameters = StdioServerParameters(command=sys.executable,
                                           args=["-m", "c1ai", "mcp"],
                                           cwd=str(Path(__file__).resolve().parents[1]))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                return await session.list_tools()

    result = asyncio.run(run())
    assert len(result.tools) == 6


def test_nonfinite_or_oversized_requests_are_refused_before_connect(monkeypatch):
    from c1ai import client

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input must be refused before any pipe connection")

    monkeypatch.setattr(client, "_connect", forbidden)
    with pytest.raises(ValueError):
        client.send_request({"command": "adjust", "patch": {"exposure": float("nan")}})
    with pytest.raises(ValueError, match="exceeds"):
        client.send_request({"command": "adjust", "large": "x" * (4 * 1024 * 1024)})


def test_mcp_preview_delivers_only_the_recorded_unchanged_artifact(tmp_path, monkeypatch):
    import hashlib
    from PIL import Image
    from c1ai import mcp_server

    archive = tmp_path / "renders"
    archive.mkdir()
    image_path = archive / "test.jpg"
    Image.new("RGB", (8, 8), (20, 40, 60)).save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    monkeypatch.setattr(mcp_server, "state_directory", lambda: tmp_path)
    result = {"status": "completed", "operation_id": "preview-test", "result": {
        "source": "capture_one_export", "path": str(image_path), "image": {"sha256": digest}}}
    delivered = mcp_server._preview_content(result)
    assert [item.type for item in delivered.content] == ["text", "image"]
    image_path.write_bytes(b"changed")
    refused = mcp_server._preview_content(result)
    assert [item.type for item in refused.content] == ["text"]
    assert refused.structuredContent["image_delivery"]["status"] == "unavailable"


def test_real_core_shared_by_pipe_clients_deduplicates_and_rejects_stale_edits(tmp_path):
    from c1ai.core import Core

    class Backend:
        def __init__(self):
            self.value, self.writes = 0.0, 0
            self.target = {"instance": {"pid": 1, "started_at": "test"},
                           "document": {"path": str(tmp_path / "test.cosessiondb")},
                           "image": {"path": str(tmp_path / "test.raw")},
                           "variant": {"id": "test-candidate"}, "layer": "Background"}

        def context(self):
            return {"target": self.target, "capabilities": {"fields": {
                "exposure": {"readable": True, "writable": True, "min": -4, "max": 4}}}}

        def observe(self, fields):
            return {"target": self.target, "values": {field: self.value for field in fields},
                    "evidence": {"source": "testdouble"}}

        def set_value(self, field, value, expected):
            time.sleep(0.03)
            self.writes += 1
            self.value = value
            return {"accepted": True}

    backend = Backend()
    core = Core(backend, state_dir=tmp_path)
    try:
        with Host(core.dispatch) as host, ThreadPoolExecutor(max_workers=2) as pool:
            initial = host.call({"command": "read", "fields": ["exposure"]})
            request = {"command": "adjust", "request_id": "same-intent", "module": "exposure",
                       "patch": {"exposure": 0.5}, "target": initial["target"],
                       "observation_token": initial["observation_token"]}
            first = pool.submit(host.call, request)
            second = pool.submit(host.call, request)
            results = [first.result(5), second.result(5)]
            assert {item["status"] for item in results} == {"control_confirmed"}
            assert len({item["operation_id"] for item in results}) == 1
            assert backend.writes == 1
            assert sum(item.get("existing", False) for item in results) == 1
            current = host.call({"command": "read", "fields": ["exposure"]})
            base = {**request, "observation_token": current["observation_token"]}
            one = pool.submit(host.call, {**base, "request_id": "new-one", "patch": {"exposure": 1.0}})
            two = pool.submit(host.call, {**base, "request_id": "new-two", "patch": {"exposure": 1.5}})
            statuses = [one.result(5), two.result(5)]
            assert sorted(item["status"] for item in statuses) == ["control_confirmed", "refused"]
            refused = next(item for item in statuses if item["status"] == "refused")
            assert refused["error"]["code"] == "stale_observation"
            assert backend.writes == 2
            assert len(core.journal.list()) == 2
    finally:
        core.journal.close()


def test_configured_session_window_uses_native_discovery_without_uia(tmp_path,monkeypatch):
    from c1ai.windows_backend import WindowsBackend

    # Construct without a real worker or app connection.
    backend = WindowsBackend.__new__(WindowsBackend)
    backend.config = {"session_path": str(tmp_path / "Test Session.cosessiondb")}
    def discover(title):
        assert title=='Test Session'
        return {"pid":10,"hwnd":100,"title":"Test Session","minimized":False}
    monkeypatch.setattr('c1ai.native_windows.find_native_main',discover)
    def no_uia(action):raise AssertionError('Native discovery must not traverse UIA')
    backend.call=no_uia
    backend._connect()
    assert backend.window["hwnd"] == 100


def test_mcp_preview_and_images_public_actions_are_bounded():
    import asyncio
    from c1ai.mcp_server import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    images = tools["images"].inputSchema
    assert "list" in images["properties"]["action"]["enum"]
    assert "target" not in images.get("required", [])
    preview = tools["preview"].inputSchema
    assert set(preview["properties"]["action"]["enum"]) == {"prepare", "render", "restore_destination", "capture"}
    assert "destination_options" not in json.dumps(preview)


def test_shipped_mcp_config_initializes_from_another_directory(tmp_path):
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config = json.loads((Path(__file__).resolve().parents[1] / "config" / "mcp-server.json").read_text())
    server = config["mcpServers"]["capture-one-ai"]
    assert Path(server["command"]).is_file()

    async def run():
        parameters = StdioServerParameters(command=server["command"], args=server["args"],
                                           cwd=str(tmp_path), env={**os.environ, **server["env"]})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                return await session.list_tools()

    assert len(asyncio.run(run()).tools) == 6


def test_receipt_peek_is_prompt_during_long_dispatch_but_writes_stay_serial():
    class Dispatcher:
        def __init__(self):
            self.started, self.release = threading.Event(), threading.Event()
            self.calls, self.peeks, self.active, self.maximum = [], [], 0, 0

        def dispatch(self, request):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append(request["request_id"])
            if request["request_id"] == "long-write":
                self.started.set()
                assert self.release.wait(5)
            self.active -= 1
            return {"status": "completed", "request_id": request["request_id"]}

        def peek(self, request):
            self.peeks.append(request.get("action", "get"))
            return {"status": "dispatching", "operation_id": "long-op", "request_id": "long-write"}

    dispatcher = Dispatcher()
    with Host(dispatcher.dispatch) as host, ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(host.call, {"command": "adjust", "request_id": "long-write"})
        assert dispatcher.started.wait(2)
        second = pool.submit(host.call, {"command": "adjust", "request_id": "queued-write"})
        try:
            for action in ("get", "list"):
                started = time.monotonic()
                result = host.call({"command": "operations", "action": action, "request_id": "long-write"}, timeout=1)
                assert time.monotonic() - started < 0.8
                assert result["status"] == "dispatching" and result["operation_id"] == "long-op"
            assert not first.done() and not second.done()
            assert dispatcher.calls == ["long-write"]
        finally:
            dispatcher.release.set()
        assert first.result(3)["status"] == "completed"
        assert second.result(3)["status"] == "completed"
    assert dispatcher.maximum == 1
    assert dispatcher.calls == ["long-write", "queued-write"]
    assert dispatcher.peeks == ["get", "list"]


def test_real_core_peek_reads_committed_dispatch_receipt_while_setter_is_busy(tmp_path):
    from c1ai.core import Core

    class Backend:
        def __init__(self):
            self.value, self.reads = 0.0, 0
            self.started, self.release = threading.Event(), threading.Event()
            self.target = {"document": {"path": str(tmp_path / "test.cosessiondb")},
                           "image": {"path": str(tmp_path / "test.raw")},
                           "variant": {"id": "candidate"}, "layer": "Background"}

        def context(self):
            self.reads += 1
            return {"target": self.target, "capabilities": {"fields": {
                "exposure": {"writable": True, "readable": True, "min": -4, "max": 4}}}}

        def observe(self, fields):
            self.reads += 1
            return {"target": self.target, "values": {field: self.value for field in fields},
                    "evidence": {"source": "testdouble"}}

        def set_value(self, field, value, expected):
            self.started.set()
            assert self.release.wait(5)
            self.value = value
            return {"accepted": True}

    backend = Backend()
    core = Core(backend, state_dir=tmp_path)
    try:
        with Host(core.dispatch) as host, ThreadPoolExecutor(max_workers=1) as pool:
            before = host.call({"command": "read", "fields": ["exposure"]})
            write = pool.submit(host.call, {"command": "adjust", "request_id": "busy-setter",
                "module": "exposure", "patch": {"exposure": 0.5}, "target": before["target"],
                "observation_token": before["observation_token"]})
            assert backend.started.wait(2)
            try:
                reads = backend.reads
                started = time.monotonic()
                receipt = host.call({"command": "operations", "action": "get", "request_id": "busy-setter"}, timeout=1)
                assert time.monotonic() - started < 0.8
                assert receipt["status"] == "dispatching"
                assert receipt["actions"][0]["status"] == "dispatching"
                assert receipt["actions"][0]["intent"]["after"] == 0.5
                listed = host.call({"command": "operations", "action": "list"}, timeout=1)
                assert listed["operations"][0]["operation_id"] == receipt["operation_id"]
                assert backend.reads == reads
                assert not write.done()
            finally:
                backend.release.set()
            assert write.result(3)["status"] == "control_confirmed"
            after = host.call({"command": "operations", "action": "get", "request_id": "busy-setter"})
            assert after["status"] == "control_confirmed" and after["after"]["values"]["exposure"] == 0.5
    finally:
        core.journal.close()


def test_queued_execution_requests_leave_capacity_for_progress_queries():
    class Dispatcher:
        def __init__(self):
            self.started, self.release = threading.Event(), threading.Event()

        def dispatch(self, request):
            self.started.set()
            assert self.release.wait(5)
            return {"status": "completed"}

        def peek(self, request):
            return {"status": "dispatching", "operation_id": "long-running"}

    dispatcher = Dispatcher()
    with Host(dispatcher.dispatch) as host, ThreadPoolExecutor(max_workers=24) as pool:
        requests = [pool.submit(host.call, {"command": "adjust", "request_id": str(i)}) for i in range(24)]
        assert dispatcher.started.wait(2)
        try:
            deadline = time.monotonic() + 2
            while sum(future.done() for future in requests) < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert any(future.done() for future in requests), "Overflow should be explicitly refused"
            started = time.monotonic()
            status = host.call({"command": "operations", "action": "get", "operation_id": "long-running"}, timeout=1)
            assert status["status"] == "dispatching" and time.monotonic() - started < 0.8
        finally:
            dispatcher.release.set()
        results = [future.result(3) for future in requests]
        assert any(result.get("error", {}).get("code") == "OWNER_BUSY" for result in results)


def test_owner_process_crash_and_restart_preserve_original_id_without_replay(tmp_path):
    suffix = uuid.uuid4().hex
    address, name = rf"\\.\pipe\C1AI.Crash.{suffix}", rf"Local\C1AI.Crash.{suffix}"
    marker = tmp_path / "dispatched-count.txt"
    script = r'''
import sys,time
from pathlib import Path
from c1ai.core import Core
from c1ai.owner import NamedPipeServer
state=Path(sys.argv[3]); marker=state/'dispatched-count.txt'
class Backend:
    def __init__(self):
        self.target={'document':{'path':str(state/'test.cosessiondb')},'image':{'path':str(state/'test.raw')},'variant':{'id':'candidate'},'layer':'Background'}
    def context(self):
        return {'target':self.target,'capabilities':{'fields':{'exposure':{'readable':True,'writable':True,'min':-4,'max':4}}}}
    def observe(self,fields):
        return {'target':self.target,'values':{field:0.0 for field in fields},'evidence':{'source':'testdouble'}}
    def set_value(self,field,value,expected):
        marker.write_text((marker.read_text() if marker.exists() else '')+'1')
        while True: time.sleep(0.1)
core=Core(Backend(),state_dir=state)
NamedPipeServer(core.dispatch,address=sys.argv[1],name=sys.argv[2]).serve()
'''

    def launch():
        return subprocess.Popen([sys.executable, "-c", script, address, name, str(tmp_path)],
                                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW)

    def stop(child):
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        if child.stderr:
            child.stderr.close()

    child, pool = launch(), ThreadPoolExecutor(max_workers=1)
    try:
        before = send_request({"command": "read", "fields": ["exposure"]}, address=address,
                              autostart=False, timeout=3, connect_timeout=5)
        request = {"command": "adjust", "request_id": "crash-once", "module": "exposure",
                   "patch": {"exposure": 0.5}, "target": before["target"],
                   "observation_token": before["observation_token"]}
        dispatched = pool.submit(send_request, request, address=address, autostart=False, timeout=10)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.read_text() == "1"
        stop(child)
        with pytest.raises(RequestUncertain):
            dispatched.result(timeout=3)
        child = launch()
        existing = send_request(request, address=address, autostart=False, timeout=3, connect_timeout=5)
        assert existing["status"] == "dispatching"
        assert existing["existing"] is True and existing["replayed"] is False
        receipt = send_request({"command": "operations", "action": "get", "request_id": "crash-once"},
                               address=address, autostart=False, timeout=3)
        assert receipt["operation_id"] == existing["operation_id"]
        assert receipt["actions"][0]["status"] == "dispatching"
        assert marker.read_text() == "1"
    finally:
        stop(child)
        pool.shutdown(wait=True)


def test_cli_json_stays_utf8_with_windows_locale_redirect():
    script = ("from c1ai import client,__main__; "
              "client.call=lambda *a,**k:{'status':'ok','path':'G:/中文路径/照片.NEF'}; "
              "__main__.main(['context'])")
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            env={**os.environ, "PYTHONUTF8": "0"},
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    assert json.loads(result.stdout.decode("utf-8"))["path"] == "G:/中文路径/照片.NEF"
