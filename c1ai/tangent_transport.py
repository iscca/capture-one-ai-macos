"""Persistent, bounded TUBE transport; no Core, UI actions, or target decisions.

Framing, decoder and Windows TCP-owner lookup are adapted from this project's
frozen predecessor c1bridge/tangent.py and native_observer.py. Protocol source:
Tangent TUBE API v3.12, ParameterChange 0x02: an increment, not an absolute value.
The old workflow, focus policy, fixture paths and file-request broker are not
imported. The owner must guard document/variant/layer before and after each call.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import select
import socket
import sqlite3
import struct
import threading
import time
import xml.etree.ElementTree as ET

from .windows_owner import NamedPipeServer, state_directory, win32

PORT = 64246
MAX_FRAME = 1024 * 1024
FIELDS = {"shadow_x": 5, "shadow_y": 6, "shadow_lightness": 7,
          "midtone_x": 8, "midtone_y": 9, "midtone_lightness": 10,
          "highlight_x": 11, "highlight_y": 12, "highlight_lightness": 13,
          "master_x": 14, "master_y": 15}
MAX_DELTA = 5.0
TOLERANCE = 0.0001
PARAMETERS = {**FIELDS, "exposure": 38, "contrast": 40, "brightness": 42,
              "saturation": 44, "temperature": 1, "tint": 3,
              "highlight_recovery": 46, "shadow_recovery": 48,
              "white_recovery": 592, "black_recovery": 593, "layer_opacity": 560}
# Fixed installed definitions: (range, maximum delta, absolute readback tolerance).
# TUBE values use these declared ranges, not the internal model's storage units.
PARAMETER_RULES = {**{field: ((-50.0, 50.0), MAX_DELTA, TOLERANCE) for field in FIELDS},
                   "exposure": ((-4.0, 4.0), 8.0, 0.0001),
                   "contrast": ((-50.0, 50.0), 100.0, 0.001),
                   "brightness": ((-50.0, 50.0), 100.0, 0.001),
                   "saturation": ((-100.0, 100.0), 200.0, 0.001),
                   "temperature": ((800.0, 14000.0), 13200.0, 0.1),
                   "tint": ((-50.0, 50.0), 100.0, 0.01),
                   "highlight_recovery": ((-100.0, 100.0), 200.0, 0.001),
                   "shadow_recovery": ((-100.0, 100.0), 200.0, 0.001),
                   "white_recovery": ((-100.0, 100.0), 200.0, 0.001),
                   "black_recovery": ((-100.0, 100.0), 200.0, 0.001),
                   "layer_opacity": ((0.0, 100.0), 100.0, 0.001)}


class TransportError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise TransportError(code, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def frame(payload):
    if not 4 <= len(payload) <= MAX_FRAME:
        fail("INVALID_FRAME", "Invalid TUBE payload length")
    return struct.pack(">I", len(payload)) + payload


def parameter_change(parameter_id, delta):
    return frame(struct.pack(">IIf", 2, parameter_id, delta))


def decode(data):
    """Decode complete frames only; reject unknown/truncated trailing commands."""
    offset = 0

    def take(size):
        nonlocal offset
        if size < 0 or offset + size > len(data):
            fail("TRUNCATED_FRAME", "Truncated TUBE command")
        result = data[offset:offset + size]
        offset += size
        return result

    def uint():
        return struct.unpack(">I", take(4))[0]

    def string(limit=1024):
        size = uint()
        if size > limit:
            fail("INVALID_FRAME", "TUBE string exceeds limit")
        return take(size).decode("utf-8", errors="strict")

    result = []
    while offset < len(data):
        code = uint()
        item = {"code": code}
        if code == 0x81:
            item.update(app=string(256), system_dir=string(), user_dir=string())
        elif code == 0x82:
            parameter_id = uint()
            value = struct.unpack(">f", take(4))[0]
            default = uint()
            if math.isinf(value) or default not in (0, 1):
                fail("INVALID_PARAMETER", "Invalid TUBE parameter response")
            item.update(parameter_id=parameter_id, value=None if math.isnan(value) else value,
                        available=not math.isnan(value), at_default=bool(default))
        elif code == 0x83:
            item.update(menu_id=uint(), value=string())
        elif code in (0x84, 0xA5, 0xAB, 0xAC):
            pass
        elif code == 0x85:
            item["mode_id"] = uint()
        elif code == 0xA2:
            item.update(control_id=uint(), name=string())
        elif code in (0xA3, 0xA4):
            item.update(control_id=uint(), state=uint())
        else:
            fail("UNSUPPORTED_MESSAGE", f"Unsupported TUBE command {code:#x}")
        result.append(item)
    return result


def process_identity(pid):
    """Read process image/start identity without loading or calling C1 code."""
    k = C.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        fail("PEER_UNAVAILABLE", "Cannot inspect the TUBE peer process")
    try:
        size = W.DWORD(32768)
        name = C.create_unicode_buffer(size.value)
        times = [W.FILETIME() for _ in range(4)]
        if (not k.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)) or
                not k.GetProcessTimes(handle, *(C.byref(value) for value in times))):
            fail("PEER_UNAVAILABLE", "Cannot verify peer image and creation time")
        return {"pid": pid, "started_at": str((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime),
                "executable": str(Path(name.value).resolve())}
    finally:
        k.CloseHandle(handle)


def tcp_client_pid(peer_port, server_port=PORT):
    api = C.WinDLL("iphlpapi").GetExtendedTcpTable
    api.argtypes = [C.c_void_p, C.POINTER(W.DWORD), W.BOOL, W.ULONG, C.c_int, W.ULONG]
    api.restype = W.DWORD
    size = W.DWORD()
    if api(None, C.byref(size), False, 2, 5, 0) not in (0, 122):
        fail("PEER_UNVERIFIED", "Cannot size TCP process table")
    buffer = C.create_string_buffer(size.value)
    if api(buffer, C.byref(size), False, 2, 5, 0):
        fail("PEER_UNVERIFIED", "Cannot read TCP process table")

    class Row(C.Structure):
        _fields_ = [(key, W.DWORD) for key in ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]

    count = W.DWORD.from_buffer(buffer).value
    if 4 + count * C.sizeof(Row) > size.value:
        fail("PEER_UNVERIFIED", "Malformed TCP process table")
    matches = []
    for index in range(count):
        row = Row.from_buffer(buffer, 4 + index * C.sizeof(Row))
        if (row.state == 5 and socket.ntohs(row.local_port & 65535) == peer_port
                and socket.ntohs(row.remote_port & 65535) == server_port
                and socket.inet_ntoa(bytes(W.DWORD(row.local_addr))) == "127.0.0.1"
                and socket.inet_ntoa(bytes(W.DWORD(row.remote_addr))) == "127.0.0.1"):
            matches.append(row.pid)
    if len(matches) != 1:
        fail("PEER_UNVERIFIED", "No unique Capture One loopback TCP peer")
    return matches[0]


class TubeSession:
    """One persistent framed stream. Timeouts preserve bytes, quarantine writes."""
    def __init__(self, client, identity, *, timeout=10):
        self.client, self.identity, self.timeout = client, deepcopy(identity), timeout
        self.client.settimeout(min(timeout, 0.25))
        self.buffer, self.pending, self.trace = bytearray(), [], []
        self.synchronized = True
        self.definition = None

    def _send(self, wire):
        self.trace.append({"direction": "hub_to_app", "hex": wire.hex(), "at": time.time()})
        self.client.sendall(wire)

    def _receive(self, deadline):
        while True:
            if len(self.buffer) >= 4:
                length = struct.unpack(">I", self.buffer[:4])[0]
                if not 4 <= length <= MAX_FRAME:
                    fail("INVALID_FRAME", "Invalid incoming TUBE frame length")
                if len(self.buffer) >= length + 4:
                    wire = bytes(self.buffer[:length + 4])
                    del self.buffer[:length + 4]
                    self.trace.append({"direction": "app_to_hub", "hex": wire.hex(), "at": time.time()})
                    return decode(wire[4:])
            if time.monotonic() >= deadline:
                fail("TUBE_TIMEOUT", "TUBE response timed out; no write may be replayed")
            try:
                block = self.client.recv(65536)
            except socket.timeout:
                continue
            if not block:
                fail("TUBE_DISCONNECTED", "Capture One closed its TUBE stream")
            self.buffer.extend(block)
            if len(self.buffer) > MAX_FRAME + 65540:
                fail("INVALID_FRAME", "TUBE buffered bytes exceed the bounded frame size")

    def _until(self, predicate):
        deadline, count = time.monotonic() + self.timeout, 0
        while True:
            items = self.pending or self._receive(deadline)
            self.pending = []
            for index, item in enumerate(items):
                count += 1
                if count > 4096:
                    fail("EXCESS_NOTIFICATIONS", "Too many TUBE notifications")
                if predicate(item):
                    self.pending = items[index + 1:]
                    return item

    def _drain(self):
        discarded = len(self.pending)
        self.pending = []
        while self.buffer or select.select([self.client], [], [], 0.02)[0]:
            discarded += len(self._receive(time.monotonic() + self.timeout))
            if discarded > 4096:
                fail("EXCESS_NOTIFICATIONS", "Too many queued TUBE notifications")
        return discarded

    def handshake(self):
        self._send(frame(struct.pack(">III", 1, 6, 0)))
        self.definition = self._until(lambda item: item["code"] == 0x81)
        return deepcopy(self.definition)

    def read(self, parameter_id):
        if not self.synchronized:
            fail("TUBE_DESYNCHRONIZED", "Earlier timeout/protocol failure requires independent recovery; stream retained")
        try:
            self._drain()
            self._send(frame(struct.pack(">II", 4, parameter_id)))
            return self._until(lambda item: item["code"] == 0x82 and item["parameter_id"] == parameter_id)
        except Exception:
            self.synchronized = False
            raise

    def change(self, parameter_id, delta):
        if not self.synchronized:
            fail("TUBE_DESYNCHRONIZED", "The TUBE stream is not synchronized")
        try:
            self._send(parameter_change(parameter_id, delta))
        except Exception:
            self.synchronized = False
            raise

    def close(self):
        self.client.close()


class TangentTransport:
    """Transport receipts only. Business authorization remains with the owner."""
    def __init__(self, config, state_dir=None, *, identity_reader=process_identity, peer_reader=tcp_client_pid):
        self.root = Path(state_dir) if state_dir is not None else state_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executable = Path(config["executable"]).resolve()
        self.controls_path = self.executable.parent / "TangentSettings" / "controls.xml"
        self.controls_hash = config["controls_sha256"].lower()
        if len(self.controls_hash) != 64 or any(c not in "0123456789abcdef" for c in self.controls_hash):
            fail("INVALID_CONFIG", "A verified controls.xml SHA-256 is required")
        self._check_controls()
        tree = ET.fromstring(self.controls_path.read_bytes())
        rows = {int(row.attrib["id"], 16): row for row in tree.iter("Parameter")}
        self.limits = {}
        for field, parameter_id in PARAMETERS.items():
            row = rows.get(parameter_id)
            if row is None:
                fail("CONTROLS_MISMATCH", f"The qualified parameter {field} is absent")
            low, high = float(row.findtext("MinValue")), float(row.findtext("MaxValue"))
            if not math.isfinite(low) or not math.isfinite(high) or (low, high) != PARAMETER_RULES[field][0]:
                fail("CONTROLS_MISMATCH", f"The limits for {field} differ from the qualified mapping")
            self.limits[field] = (low, high)
        self.identity_reader, self.peer_reader = identity_reader, peer_reader
        self.lock = threading.RLock()
        self.session = None
        self.last_connection_error = None
        self.listener = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.listener_thread = None
        self.db = sqlite3.connect(self.root / "tangent-receipts.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, action_id TEXT UNIQUE NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)")
        self.db.commit()

    def _check_controls(self):
        if hashlib.sha256(self.controls_path.read_bytes()).hexdigest() != self.controls_hash:
            fail("CONTROLS_MISMATCH", "Installed controls.xml no longer matches the verified hash")

    def attach(self, client, peer):
        """Accept only the exact locally observed C1 process and installation."""
        if peer[0] != "127.0.0.1":
            client.close()
            fail("PEER_UNVERIFIED", "TUBE peer is not loopback")
        session = None
        try:
            identity = self.identity_reader(self.peer_reader(peer[1]))
            if Path(identity["executable"]).resolve() != self.executable:
                fail("PEER_UNVERIFIED", "TUBE peer executable differs from the configured Capture One")
            self._check_controls()
            session = TubeSession(client, identity)
            definition = session.handshake()
            if (definition["app"] != "Capture One" or
                    Path(definition["system_dir"]).resolve() != self.controls_path.parent):
                fail("HANDSHAKE_MISMATCH", "TUBE application or controls directory differs")
            if self.identity_reader(identity["pid"]) != identity:
                fail("PEER_CHANGED", "Capture One process identity changed during handshake")
            with self.lock:
                self._retire_disconnected()
                if self.session is not None:
                    fail("ALREADY_CONNECTED", "An existing TUBE connection will not be replaced")
                self.session = session
                self.last_connection_error = None
        except Exception:
            client.close()
            raise

    def _retire_disconnected(self):
        """Only a dead peer/EOF releases a connection, never a call timeout."""
        if self.session is None:
            return
        dead = False
        try:
            dead = self.identity_reader(self.session.identity["pid"]) != self.session.identity
            if not dead and select.select([self.session.client], [], [], 0)[0]:
                dead = self.session.client.recv(1, socket.MSG_PEEK) == b""
        except (OSError, TransportError):
            dead = True
        if dead:
            self.session.close()
            self.session = None

    def start(self):
        """Bind before C1 startup. Never starts/stops C1 or changes its config."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            server.bind(("127.0.0.1", PORT))
            server.listen(1)
            server.settimeout(0.25)
        except Exception:
            server.close()
            raise
        self.listener = server

        def listen():
            self.ready.set()
            while not self.stop.is_set():
                try:
                    incoming, peer = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    self.attach(incoming, peer)
                except Exception as error:
                    self.last_connection_error = {"code": getattr(error, "code", "CONNECT_FAILED"), "message": str(error)}

        self.listener_thread = threading.Thread(target=listen, name="c1ai-tube-listener", daemon=True)
        self.listener_thread.start()

    def _identity(self, expected):
        self._check_controls()
        if self.session is None:
            fail("TUBE_NOT_CONNECTED", "No verified Capture One TUBE connection")
        actual = self.identity_reader(self.session.identity["pid"])
        if actual != self.session.identity:
            fail("PEER_CHANGED", "Capture One process image or start identity changed")
        if expected != {key: actual[key] for key in ("pid", "started_at")}:
            fail("PEER_CHANGED", "Request is not bound to the connected Capture One process")
        return actual

    def _base(self):
        return {"source": "tangent_tube", "controls_sha256": self.controls_hash,
                "photo_identity_verified": False, "model_verified": False, "render_verified": False,
                "target_guard_scope": "owner_required_before_and_after",
                "protocol_freshness": "serialized_parameter_id_response_without_request_sequence"}

    def _stored(self, request_id):
        row = self.db.execute("SELECT request,response FROM receipts WHERE request_id=?", (request_id,)).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else None

    def _save(self, record):
        self.db.execute("UPDATE receipts SET response=? WHERE request_id=?", (canonical(record), record["request_id"]))
        self.db.commit()

    @staticmethod
    def _number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def _validate(self, request):
        if not isinstance(request, dict):
            fail("INVALID_REQUEST", "Tangent request must be an object")
        command = request.get("command")
        allowed = {"status": {"command", "request_id"}, "query": {"command", "request_id"},
                   "read": {"command", "request_id", "fields", "expected_instance"},
                   "delta": {"command", "request_id", "operation_id", "action_id", "expected_instance", "field", "expected_before", "delta"}}
        if command not in allowed or set(request) - allowed[command]:
            fail("INVALID_REQUEST", "Unsupported command or unexpected Tangent argument")
        if command in ("query", "delta") and (not isinstance(request.get("request_id"), str) or not 1 <= len(request["request_id"]) <= 128):
            fail("INVALID_REQUEST", "A bounded unique request_id is required")
        if command in ("read", "delta"):
            instance = request.get("expected_instance")
            if (not isinstance(instance, dict) or set(instance) != {"pid", "started_at"}
                    or type(instance["pid"]) is not int or instance["pid"] <= 0
                    or not isinstance(instance["started_at"], str) or not instance["started_at"].isdigit()):
                fail("INVALID_REQUEST", "Exact PID and process start identity are required")
        if command == "read":
            fields = request.get("fields")
            if (not isinstance(fields, list) or not 1 <= len(fields) <= len(PARAMETERS)
                    or any(not isinstance(field, str) or field not in PARAMETERS for field in fields)
                    or len(set(fields)) != len(fields)):
                fail("INVALID_REQUEST", "Read accepts unique qualified parameter fields only")
        if command == "delta":
            if (any(not isinstance(request.get(key), str) or not 1 <= len(request[key]) <= 128 for key in ("operation_id", "action_id"))
                    or request.get("field") not in PARAMETERS or not self._number(request.get("expected_before"))
                    or not self._number(request.get("delta"))
                    or not 0 < abs(request["delta"]) <= PARAMETER_RULES[request["field"]][1]):
                fail("INVALID_REQUEST", "Delta requires original operation/action IDs, one qualified field and a finite nonzero increment within its limit")
            low, high = self.limits[request["field"]]
            if not low <= request["expected_before"] <= high or not low <= request["expected_before"] + request["delta"] <= high:
                fail("INVALID_REQUEST", "Delta would exceed the qualified parameter range")

    def dispatch(self, request):
        with self.lock:
            try:
                self._validate(request)
                command = request["command"]
                if command == "status":
                    self._retire_disconnected()
                    return {**self._base(), "status": "ok", "listening": self.ready.is_set(),
                            "connected": self.session is not None, "instance": deepcopy(self.session.identity) if self.session else None,
                            "stream_synchronized": bool(self.session and self.session.synchronized),
                            "last_connection_error": self.last_connection_error, "fields": deepcopy(PARAMETERS),
                            "limits": deepcopy(self.limits),
                            "delta_limits": {field: rule[1] for field, rule in PARAMETER_RULES.items()},
                            "absolute_tolerances": {field: rule[2] for field, rule in PARAMETER_RULES.items()}}
                if command == "query":
                    stored = self._stored(request["request_id"])
                    return dict(stored[1], replayed=False) if stored else {"status": "not_found", "request_id": request["request_id"]}
                if command == "delta":
                    return self._delta(request)
                identity = self._identity(request["expected_instance"])
                self.session.trace = []
                values = {field: self.session.read(PARAMETERS[field]) for field in request["fields"]}
                self._identity(request["expected_instance"])
                return {**self._base(), "status": "ok", "instance": identity,
                        "values": {field: value["value"] for field, value in values.items()},
                        "parameters": values, "wire": deepcopy(self.session.trace), "mutations_sent": False}
            except Exception as error:
                return {**self._base(), "status": "refused", "request_id": request.get("request_id") if isinstance(request, dict) else None,
                        "dispatched": False, "error": {"code": getattr(error, "code", "TRANSPORT_ERROR"), "message": str(error)}}

    def _delta(self, request):
        existing = self._stored(request["request_id"])
        if existing:
            if existing[0] != request:
                fail("REQUEST_CONFLICT", "The request_id already names a different transport intent")
            return dict(existing[1], replayed=False)
        if self.db.execute("SELECT 1 FROM receipts WHERE action_id=?", (request["action_id"],)).fetchone():
            fail("ACTION_CONFLICT", "This action_id already has a dispatch receipt; query its original request")
        record = {**self._base(), "request_id": request["request_id"], "operation_id": request["operation_id"],
                  "action_id": request["action_id"], "status": "not_dispatched", "phase": "preflight",
                  "dispatched": False, "accepted": False, "parameter_readback_verified": False,
                  "replay_allowed": False, "created_at": time.time(), "field": request["field"],
                  "intent": deepcopy(request), "wire": []}
        self.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (request["request_id"], request["action_id"], canonical(request), canonical(record)))
        self.db.commit()
        session = self.session
        try:
            identity = self._identity(request["expected_instance"])
            session = self.session
            session.trace = []
            tolerance = PARAMETER_RULES[request["field"]][2]
            before = session.read(PARAMETERS[request["field"]])
            value = before["value"]
            if value is None or not math.isclose(value, request["expected_before"], rel_tol=0, abs_tol=tolerance):
                fail("STALE_PARAMETER", "Current TUBE value differs from the owner's expected baseline")
            self._identity(request["expected_instance"])
            increment = struct.unpack(">f", struct.pack(">f", request["delta"]))[0]
            expected_after = value + increment
            low, high = self.limits[request["field"]]
            if not low <= expected_after <= high:
                fail("INVALID_REQUEST", "Float32 increment would exceed the native parameter range")
            record.update(status="dispatching", phase="change", dispatched=True, instance=identity,
                          before=before, delta=increment, expected_after=expected_after,
                          planned_wire=parameter_change(PARAMETERS[request["field"]], increment).hex(), wire=deepcopy(session.trace))
            self._save(record)  # Durable boundary before even a partial send is possible.
            session.change(PARAMETERS[request["field"]], increment)
            record["accepted"] = True  # Socket send only; never a model-commit assertion.
            record["acceptance_scope"] = "transport_send_completed"
            after = session.read(PARAMETERS[request["field"]])
            self._identity(request["expected_instance"])
            record["after"] = after
            if after["value"] is None or not math.isclose(after["value"], expected_after, rel_tol=0, abs_tol=tolerance):
                fail("DELTA_READBACK_MISMATCH", "Post-request parameter value does not match the bounded increment")
            record.update(status="completed", phase="readback", parameter_readback_verified=True)
        except Exception as error:
            record.update(status="outcome_unknown" if record["dispatched"] else "not_dispatched",
                          error={"code": getattr(error, "code", "TRANSPORT_ERROR"), "message": str(error)})
        record["wire"] = deepcopy(session.trace) if session is not None else []
        record["updated_at"] = time.time()
        try:
            self._save(record)
        except Exception as error:
            # A response-persistence failure after send must never become the
            # outer dispatcher's pre-dispatch refusal. The durable dispatching
            # receipt still prevents another send of this request/action.
            record.update(status="outcome_unknown" if record["dispatched"] else "not_dispatched",
                          persistence_error={"code": "RECEIPT_PERSIST_FAILED", "message": str(error)})
        return deepcopy(record)

    def close(self):
        """Explicit host shutdown only; client/owner disconnect never calls this."""
        self.stop.set()
        if self.listener:
            self.listener.close()
        if self.listener_thread:
            self.listener_thread.join(12)
        with self.lock:
            if self.session:
                self.session.close()
            self.db.close()


def endpoint():
    return rf"\\.\pipe\CaptureOneAI.Tangent.{win32().sid()}"


def mutex_name():
    return rf"Global\CaptureOneAI.Tangent.{win32().sid()}"


def call(request, *, timeout=45):
    from .client import send_request
    return send_request(request, timeout=timeout, autostart=False, address=endpoint())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Persistent Capture One TUBE transport; never starts or stops Capture One")
    parser.add_argument("--serve", action="store_true", required=True)
    args = parser.parse_args(argv)
    root = state_directory()
    config = json.loads((root / "tangent-config.json").read_text("utf-8"))
    holder = {}

    def build():
        transport = TangentTransport(config, root)
        holder["transport"] = transport
        transport.start()
        return transport.dispatch

    try:
        NamedPipeServer(dispatcher_factory=build, address=endpoint(), name=mutex_name()).serve()
    finally:
        if "transport" in holder:
            holder["transport"].close()


if __name__ == "__main__":
    main()
