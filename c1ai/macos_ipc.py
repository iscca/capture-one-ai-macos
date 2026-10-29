"""Private Unix socket owner. Accepted writes survive client disconnects."""
from __future__ import annotations

import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

PROTOCOL = "capture-one-ai"
PROTOCOL_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024


class PipeError(RuntimeError):
    pass


class PipeTimeout(PipeError):
    pass


class ProtocolMismatch(PipeError):
    pass


class RequestUncertain(PipeError):
    def __init__(self, request_id, cause):
        self.request_id, self.cause = request_id, cause
        super().__init__(f"Query request_id {request_id}; do not replay: {cause}")

    def as_result(self):
        return {"status": "outcome_unknown", "request_id": self.request_id,
                "error": {"code": "CLIENT_RESULT_UNKNOWN", "message": str(self)},
                "replay_allowed": False,
                "query": {"command": "operations", "action": "get", "request_id": self.request_id}}


def state_directory():
    return Path.home() / "Library" / "Application Support" / "CaptureOneAI"


def private_directory(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PipeError("Owner directory must be a real directory owned by the current user")
    root.chmod(0o700)


def endpoint():
    return str(state_directory() / "owner.sock")


def _timeout(conn, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PipeTimeout("IPC deadline exceeded")
    conn.settimeout(remaining)


def send_frame(conn, value, deadline):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise PipeError("IPC frame exceeds 4 MiB")
    _timeout(conn, deadline)
    conn.sendall(struct.pack("<I", len(payload)) + payload)


def receive_frame(conn, deadline):
    def exact(count):
        parts = bytearray()
        while len(parts) < count:
            _timeout(conn, deadline)
            chunk = conn.recv(count - len(parts))
            if not chunk:
                raise PipeError("Owner connection closed")
            parts.extend(chunk)
        return bytes(parts)
    size, = struct.unpack("<I", exact(4))
    if not 0 < size <= MAX_FRAME:
        raise PipeError("Invalid IPC frame size")
    result = json.loads(exact(size))
    if not isinstance(result, dict):
        raise ProtocolMismatch("Expected a JSON object")
    return result


def launch_owner():
    root = state_directory()
    private_directory(root)
    with (root / "owner.log").open("ab", buffering=0) as log:
        subprocess.Popen([sys.executable, "-m", "c1ai", "owner", "--serve"],
                         cwd=Path(__file__).resolve().parent.parent,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


def send_request(request, *, timeout=120, autostart=True, address=None, connect_timeout=15):
    if not isinstance(request, dict) or timeout <= 0 or connect_timeout <= 0:
        raise ValueError("Use a request object and positive timeouts")
    if address is not None and autostart:
        raise ValueError("Custom endpoints cannot autostart a production owner")
    payload = dict(request)
    payload.setdefault("request_id", uuid.uuid4().hex)
    if not isinstance(payload["request_id"], str) or not payload["request_id"].strip():
        raise ValueError("request_id must be a nonempty string")
    envelope = {"protocol": PROTOCOL, "version": PROTOCOL_VERSION, "kind": "request", "request": payload}
    if len(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()) > MAX_FRAME:
        raise ValueError("IPC request exceeds 4 MiB")
    address = address or endpoint()
    deadline, started = time.monotonic() + connect_timeout, False
    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            parent = Path(address).parent.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                    or parent.st_mode & 0o077):
                raise PipeError("Socket parent must be private to the current user")
            info = Path(address).lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PipeError("Socket must be private to the current user")
            conn.settimeout(max(0.001, deadline - time.monotonic()))
            conn.connect(address)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            conn.close()
            if autostart and not started:
                launch_owner()
                started = True
            if time.monotonic() >= deadline:
                raise PipeTimeout("Owner did not become available")
            time.sleep(0.05)  # Connection only, before any request bytes.
        except BaseException:
            conn.close()
            raise
    possibly_sent = False
    try:
        deadline = time.monotonic() + timeout
        hello = {"protocol": PROTOCOL, "version": PROTOCOL_VERSION, "kind": "hello"}
        send_frame(conn, hello, deadline)
        reply = receive_frame(conn, deadline)
        if any(reply.get(k) != v for k, v in hello.items()) or type(reply.get("owner_pid")) is not int:
            raise ProtocolMismatch("Owner protocol mismatch; no request dispatched")
        possibly_sent = True
        send_frame(conn, envelope, deadline)
        reply = receive_frame(conn, deadline)
        if (reply.get("protocol") != PROTOCOL or reply.get("version") != PROTOCOL_VERSION
                or reply.get("kind") != "result" or not isinstance(reply.get("result"), dict)):
            raise ProtocolMismatch("Invalid result envelope")
        return reply["result"]
    except (OSError, PipeError, ValueError) as error:
        if possibly_sent:
            raise RequestUncertain(payload["request_id"], error) from error
        raise
    finally:
        conn.close()


def call(request, *, timeout=120, autostart=True):
    try:
        return send_request(request, timeout=timeout, autostart=autostart)
    except RequestUncertain as error:
        return error.as_result()
    except (OSError, PipeError, ValueError) as error:
        return {"status": "refused", "request_id": request.get("request_id") if isinstance(request, dict) else None,
                "error": {"code": "OWNER_UNAVAILABLE", "message": str(error)}}


def serve_owner():
    from .core import Core
    from .macos_backend import MacOSBackend
    root = state_directory()
    private_directory(root)
    os.umask(0o077)
    with (root / "owner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        path = Path(endpoint())
        if path.exists():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise PipeError("Refusing to replace a foreign socket path")
            path.unlink()
        core = Core(MacOSBackend(root), state_dir=root)
        # Exactly one worker performs C1 calls. Progress queries read committed SQLite state.
        slots = threading.BoundedSemaphore(32)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def handle(conn):
            try:
                hello = {"protocol": PROTOCOL, "version": PROTOCOL_VERSION, "kind": "hello"}
                if receive_frame(conn, time.monotonic() + 10) != hello:
                    raise ProtocolMismatch("Invalid hello")
                send_frame(conn, {**hello, "owner_pid": os.getpid()}, time.monotonic() + 5)
                envelope = receive_frame(conn, time.monotonic() + 10)
                if (envelope.get("protocol") != PROTOCOL or envelope.get("version") != PROTOCOL_VERSION
                        or envelope.get("kind") != "request" or not isinstance(envelope.get("request"), dict)):
                    raise ProtocolMismatch("Invalid request")
                req = envelope["request"]
                if req.get("command") == "operations" and req.get("action", "get") in {"get", "list"}:
                    result = core.peek(req)
                else:
                    result = executor.submit(core.dispatch, req).result()
                send_frame(conn, {"protocol": PROTOCOL, "version": PROTOCOL_VERSION,
                                  "kind": "result", "result": result}, time.monotonic() + 10)
            except (OSError, PipeError, ValueError):
                pass
            finally:
                conn.close()
                slots.release()

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                path.chmod(0o600)
                server.listen(32)
                while True:
                    conn, _ = server.accept()
                    if slots.acquire(blocking=False):
                        threading.Thread(target=handle, args=(conn,), daemon=True).start()
                    else:
                        conn.close()
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
            path.unlink(missing_ok=True)
    return 0
