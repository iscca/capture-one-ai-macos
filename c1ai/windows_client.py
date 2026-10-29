"""Thin owner client. Connection setup may retry; requests are sent once only."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .windows_owner import (MAX_FRAME, PROTOCOL, PROTOCOL_VERSION, PipeError, PipeTimeout,
                    ProtocolMismatch, connect_pipe, endpoint, receive_frame,
                    send_frame, state_directory, win32)


class RequestUncertain(PipeError):
    def __init__(self, request_id: str, cause: Exception):
        self.request_id = request_id
        self.cause = cause
        super().__init__(f"Connection lost after request dispatch; query request_id {request_id}. Do not replay: {cause}")

    def as_result(self):
        return {"status": "outcome_unknown", "request_id": self.request_id,
                "error": {"code": "CLIENT_RESULT_UNKNOWN", "message": str(self)},
                "replay_allowed": False,
                "query": {"command": "operations", "action": "get", "request_id": self.request_id}}


def launch_owner():
    root = state_directory()
    root.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    # Spawn independently of the MCP stdio process; never inherit its streams.
    with (root / "owner.log").open("ab", buffering=0) as log:
        subprocess.Popen([sys.executable, "-m", "c1ai", "owner", "--serve"],
                         cwd=project, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         env={**os.environ, "PYTHONUTF8": "1"},
                         close_fds=True)


def _connect(address: str, *, autostart: bool, timeout: float):
    deadline, started, last = time.monotonic() + timeout, False, None
    while True:
        try:
            return connect_pipe(address)
        except OSError as error:
            last = error
            if getattr(error, "winerror", None) not in (2, 231):
                raise
            if autostart and not started:
                launch_owner()
                started = True
            if time.monotonic() >= deadline:
                raise PipeTimeout(f"Owner did not become available: {last}") from error
            # No request bytes have been sent. Waiting/connecting is safe here.
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))


def send_request(request: dict, *, timeout: float = 120, autostart: bool = True,
                 address: str | None = None, connect_timeout: float = 15) -> dict:
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    if timeout <= 0 or connect_timeout <= 0:
        raise ValueError("Timeouts must be positive")
    payload = dict(request)
    payload.setdefault("request_id", uuid.uuid4().hex)
    if not isinstance(payload["request_id"], str) or not payload["request_id"]:
        raise ValueError("request_id must be a nonempty string")
    envelope = {"protocol": PROTOCOL, "version": PROTOCOL_VERSION,
                "kind": "request", "request": payload}
    encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME:
        raise ValueError("IPC request exceeds 4 MiB")
    if address is not None and autostart:
        raise ValueError("Custom test endpoints cannot launch a production owner")
    handle = _connect(address or endpoint(), autostart=autostart, timeout=connect_timeout)
    possibly_sent = False
    try:
        deadline = time.monotonic() + timeout
        send_frame(handle, {"protocol": PROTOCOL, "version": PROTOCOL_VERSION, "kind": "hello"}, deadline)
        hello = receive_frame(handle, deadline)
        server_pid = w.ULONG()
        win32().check(win32().GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid)))
        if (hello.get("protocol") != PROTOCOL or hello.get("version") != PROTOCOL_VERSION
                or hello.get("kind") != "hello" or hello.get("owner_pid") != server_pid.value):
            raise ProtocolMismatch("Owner protocol/identity mismatch; no request was dispatched")
        possibly_sent = True
        send_frame(handle, envelope, deadline)
        response = receive_frame(handle, deadline)
        if (response.get("protocol") != PROTOCOL or response.get("version") != PROTOCOL_VERSION
                or response.get("kind") != "result" or not isinstance(response.get("result"), dict)):
            raise ProtocolMismatch("Invalid owner result envelope")
        try:
            send_frame(handle, {"kind": "received"}, time.monotonic() + 1)
        except (OSError, PipeError):
            # A complete result is already known; a lost receipt cannot undo it.
            pass
        return response["result"]
    except (OSError, PipeError, ValueError, UnicodeError) as error:
        if possibly_sent:
            raise RequestUncertain(payload["request_id"], error) from error
        raise
    finally:
        win32().CloseHandle(handle)


def call(request: dict, *, timeout: float = 120, autostart: bool = True) -> dict:
    """Convenience API used by both CLI and MCP; reports transport errors as data."""
    try:
        return send_request(request, timeout=timeout, autostart=autostart)
    except RequestUncertain as error:
        return error.as_result()
    except (OSError, PipeError, ValueError) as error:
        return {"status": "refused", "request_id": request.get("request_id"),
                "error": {"code": "OWNER_UNAVAILABLE", "message": str(error)}}
