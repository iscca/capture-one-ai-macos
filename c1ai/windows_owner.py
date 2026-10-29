"""One per-user owner and local, current-user-only Windows named-pipe IPC.

The dispatcher is independent of each connection. Once enqueued, a request runs
to completion even when its client closes the pipe. IPC never replays a request.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import queue
import struct
import threading
import time
from typing import Callable

PROTOCOL = "capture-one-ai"
PROTOCOL_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024
INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF


class PipeError(RuntimeError):
    pass


class PipeTimeout(PipeError):
    pass


class OwnerAlreadyRunning(PipeError):
    pass


class ProtocolMismatch(PipeError):
    pass


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", w.LPVOID),
                ("bInheritHandle", w.BOOL)]


class Win32:
    """Small explicit ctypes binding; no default pipe DACL is relied upon."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("Capture One AI IPC requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)

        def bind(dll, name, result, *args):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)

        k, a = self.kernel, self.advapi
        bind(k, "GetCurrentProcess", w.HANDLE)
        bind(k, "CloseHandle", w.BOOL, w.HANDLE)
        bind(k, "LocalFree", w.HLOCAL, w.HLOCAL)
        bind(k, "CreateEventW", w.HANDLE, w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR)
        bind(k, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        bind(k, "CreateMutexW", w.HANDLE, ctypes.POINTER(SECURITY_ATTRIBUTES), w.BOOL, w.LPCWSTR)
        bind(k, "ReleaseMutex", w.BOOL, w.HANDLE)
        bind(k, "CreateNamedPipeW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
             w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES))
        bind(k, "ConnectNamedPipe", w.BOOL, w.HANDLE, ctypes.POINTER(OVERLAPPED))
        bind(k, "DisconnectNamedPipe", w.BOOL, w.HANDLE)
        bind(k, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
             w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
        bind(k, "WaitNamedPipeW", w.BOOL, w.LPCWSTR, w.DWORD)
        bind(k, "GetNamedPipeServerProcessId", w.BOOL, w.HANDLE, ctypes.POINTER(w.ULONG))
        bind(k, "ReadFile", w.BOOL, w.HANDLE, w.LPVOID, w.DWORD,
             ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED))
        bind(k, "WriteFile", w.BOOL, w.HANDLE, w.LPCVOID, w.DWORD,
             ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED))
        bind(k, "GetOverlappedResult", w.BOOL, w.HANDLE, ctypes.POINTER(OVERLAPPED),
             ctypes.POINTER(w.DWORD), w.BOOL)
        bind(k, "CancelIoEx", w.BOOL, w.HANDLE, ctypes.POINTER(OVERLAPPED))
        bind(a, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
        bind(a, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int,
             w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
        bind(a, "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR))
        bind(a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
             w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.DWORD))

    def check(self, value):
        if not value or value == INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def sid(self) -> str:
        token = w.HANDLE()
        self.check(self.OpenProcessToken(self.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
        try:
            size = w.DWORD()
            self.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            self.check(self.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)))
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(w.LPVOID))[0]
            string_sid = w.LPWSTR()
            self.check(self.ConvertSidToStringSidW(sid_pointer, ctypes.byref(string_sid)))
            try:
                return string_sid.value
            finally:
                self.LocalFree(ctypes.cast(string_sid, w.HLOCAL))
        finally:
            self.CloseHandle(token)


_win32: Win32 | None = None


def win32() -> Win32:
    global _win32
    if _win32 is None:
        _win32 = Win32()
    return _win32


def state_directory() -> Path:
    """Fixed across installations, clients, project directories and versions."""
    if os.name != "nt":
        raise OSError("Capture One AI owner requires Windows")
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "CaptureOneAI"


def endpoint() -> str:
    return rf"\\.\pipe\CaptureOneAI.Owner.{win32().sid()}"


def mutex_name() -> str:
    return rf"Global\CaptureOneAI.Owner.{win32().sid()}"


class UserSecurity:
    def __init__(self):
        api = win32()
        self.sid = api.sid()
        # Protected DACL: only the current token's user SID has any access.
        self.sddl = f"D:P(A;;GA;;;{self.sid})"
        self.descriptor = w.LPVOID()
        api.check(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            self.sddl, 1, ctypes.byref(self.descriptor), None))
        self.attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), self.descriptor, False)

    def close(self):
        if self.descriptor:
            win32().LocalFree(self.descriptor)
            self.descriptor = w.LPVOID()


class UserMutex:
    def __init__(self, name: str, security: UserSecurity):
        api = win32()
        self.handle = api.check(api.CreateMutexW(ctypes.byref(security.attributes), False, name))
        wait = api.WaitForSingleObject(self.handle, 0)
        if wait not in (0, 0x80):  # acquired or abandoned by a terminated owner
            api.CloseHandle(self.handle)
            self.handle = None
            if wait == WAIT_TIMEOUT:
                raise OwnerAlreadyRunning("The per-user owner is already running")
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            win32().ReleaseMutex(self.handle)
            win32().CloseHandle(self.handle)
            self.handle = None


def _remaining(deadline: float) -> int:
    value = deadline - time.monotonic()
    if value <= 0:
        raise PipeTimeout("IPC deadline exceeded")
    return min(max(1, int(value * 1000)), 0xFFFFFFFE)


def _transfer(handle, *, data: bytes | None = None, count: int = 0, deadline: float) -> bytes | int:
    _remaining(deadline)
    api = win32()
    event = api.check(api.CreateEventW(None, True, False, None))
    overlap = OVERLAPPED(hEvent=event)
    buffer = ctypes.create_string_buffer(data, len(data)) if data is not None else ctypes.create_string_buffer(count)
    transferred = w.DWORD()
    pending = False
    try:
        fn = api.WriteFile if data is not None else api.ReadFile
        ok = fn(handle, buffer, len(data) if data is not None else count,
                ctypes.byref(transferred), ctypes.byref(overlap))
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            pending = True
            wait = api.WaitForSingleObject(event, _remaining(deadline))
            if wait == WAIT_TIMEOUT:
                raise PipeTimeout("IPC deadline exceeded")
            if wait != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            api.check(api.GetOverlappedResult(handle, ctypes.byref(overlap), ctypes.byref(transferred), False))
            pending = False
        return transferred.value if data is not None else buffer.raw[:transferred.value]
    finally:
        if pending:
            # Cancel only this pipe I/O; never the owner dispatcher operation.
            api.CancelIoEx(handle, ctypes.byref(overlap))
            api.WaitForSingleObject(event, INFINITE)
        api.CloseHandle(event)


def send_frame(handle, value: dict, deadline: float):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise PipeError("IPC frame exceeds 4 MiB")
    wire = struct.pack("<I", len(payload)) + payload
    offset = 0
    while offset < len(wire):
        sent = _transfer(handle, data=wire[offset:], deadline=deadline)
        if not sent:
            raise PipeError("Pipe closed during write")
        offset += sent


def receive_frame(handle, deadline: float) -> dict:
    def exact(length):
        parts, size = [], 0
        while size < length:
            data = _transfer(handle, count=length - size, deadline=deadline)
            if not data:
                raise PipeError("Pipe closed during read")
            parts.append(data)
            size += len(data)
        return b"".join(parts)

    length = struct.unpack("<I", exact(4))[0]
    if length == 0 or length > MAX_FRAME:
        raise PipeError("Invalid IPC frame length")
    value = json.loads(exact(length).decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(value, dict):
        raise PipeError("IPC messages must be JSON objects")
    return value


def connect_pipe(address: str):
    if not address.startswith("\\\\.\\pipe\\"):
        raise PipeError("Only a local Windows named pipe is allowed")
    # FILE_FLAG_OVERLAPPED; SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION
    handle = win32().CreateFileW(address, 0xC0000000, 0, None, 3,
                                 0x40000000 | 0x00100000 | 0x00010000, None)
    return win32().check(handle)


class NamedPipeServer:
    """Testable IPC host. Production uses the fixed SID-scoped names above."""

    def __init__(self, dispatch: Callable[[dict], dict] | None = None, *,
                 dispatcher_factory: Callable[[], Callable[[dict], dict]] | None = None,
                 address: str | None = None, name: str | None = None):
        self.dispatch = dispatch
        self.dispatcher_factory = dispatcher_factory
        self.address = address or endpoint()
        self.name = name or mutex_name()
        self.ready = threading.Event()
        self.failure: Exception | None = None
        self._queue = queue.Queue(maxsize=64)
        self._handlers = set()
        self._handlers_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(32)
        # Long-running/queued writes must not consume every connection slot;
        # leave capacity for committed-receipt queries and protocol handshakes.
        self._dispatch_slots = threading.BoundedSemaphore(16)

    def _dispatch_loop(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            request, future = item
            try:
                result = self.dispatch(request)
                if not isinstance(result, dict):
                    raise TypeError("Core.dispatch must return a dictionary")
                future.set_result(result)
            except Exception as error:
                # Core normally catches and journals failures. A failure outside
                # that contract must not falsely claim that no write occurred.
                future.set_result({"status": "outcome_unknown", "request_id": request.get("request_id"),
                                   "error": {"code": "OWNER_DISPATCH_FAILED", "message": str(error)},
                                   "replay_allowed": False})

    def _handle(self, handle):
        try:
            hello = receive_frame(handle, time.monotonic() + 10)
            if hello != {"protocol": PROTOCOL, "version": PROTOCOL_VERSION, "kind": "hello"}:
                send_frame(handle, {"protocol": PROTOCOL, "version": PROTOCOL_VERSION,
                                    "error": "PROTOCOL_MISMATCH"}, time.monotonic() + 5)
                # DisconnectNamedPipe discards unread bytes. Wait for receipt or
                # the peer's close rather than disconnecting immediately.
                receive_frame(handle, time.monotonic() + 2)
                return
            send_frame(handle, {"protocol": PROTOCOL, "version": PROTOCOL_VERSION,
                                "kind": "hello", "owner_pid": os.getpid()}, time.monotonic() + 5)
            envelope = receive_frame(handle, time.monotonic() + 10)
            if (envelope.get("protocol") != PROTOCOL or envelope.get("version") != PROTOCOL_VERSION
                    or envelope.get("kind") != "request" or not isinstance(envelope.get("request"), dict)):
                raise ProtocolMismatch("Invalid request envelope")
            request = envelope["request"]
            # Read only committed receipts through the existing Core's separate
            # read connection. Never construct a second Core/backend/worker, and
            # never let observation, recovery or edits bypass the serial queue.
            peek = getattr(getattr(self.dispatch, "__self__", None), "peek", None)
            read_receipt = (request.get("command") == "operations"
                            and request.get("action", "get") in ("get", "list"))
            if read_receipt and callable(peek):
                try:
                    result = peek(request)
                    if not isinstance(result, dict):
                        raise TypeError("Core.peek must return a dictionary")
                except Exception as error:
                    result = {"status": "refused", "request_id": request.get("request_id"),
                              "error": {"code": "OWNER_PEEK_FAILED", "message": str(error)}}
            else:
                future = concurrent.futures.Future()
                if not self._dispatch_slots.acquire(blocking=False):
                    result = {"status": "refused", "request_id": request.get("request_id"),
                              "error": {"code": "OWNER_BUSY", "message": "Owner execution queue is full; request was not dispatched"}}
                else:
                    try:
                        self._queue.put_nowait((request, future))
                        # The peer's disappearance cannot cancel an accepted
                        # request or its durable journal transition.
                        result = future.result()
                    except queue.Full:
                        result = {"status": "refused", "request_id": request.get("request_id"),
                                  "error": {"code": "OWNER_BUSY", "message": "Owner queue is full; request was not dispatched"}}
                    finally:
                        self._dispatch_slots.release()
            send_frame(handle, {"protocol": PROTOCOL, "version": PROTOCOL_VERSION,
                                "kind": "result", "result": result}, time.monotonic() + 10)
            receive_frame(handle, time.monotonic() + 2)
        except (OSError, PipeError, ValueError, UnicodeError):
            pass
        finally:
            win32().DisconnectNamedPipe(handle)
            win32().CloseHandle(handle)
            self._slots.release()
            with self._handlers_lock:
                self._handlers.discard(threading.current_thread())

    def _listen(self, handle, stop: threading.Event) -> bool:
        api = win32()
        event = api.check(api.CreateEventW(None, True, False, None))
        overlap, pending = OVERLAPPED(hEvent=event), False
        try:
            if api.ConnectNamedPipe(handle, ctypes.byref(overlap)):
                return True
            error = ctypes.get_last_error()
            if error == 535:  # client connected between CreateNamedPipe and Connect
                return True
            if error != ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            pending = True
            while not stop.is_set():
                wait = api.WaitForSingleObject(event, 200)
                if wait == 0:
                    count = w.DWORD()
                    api.check(api.GetOverlappedResult(handle, ctypes.byref(overlap), ctypes.byref(count), False))
                    pending = False
                    return True
                if wait != WAIT_TIMEOUT:
                    raise ctypes.WinError(ctypes.get_last_error())
            return False
        finally:
            if pending:
                api.CancelIoEx(handle, ctypes.byref(overlap))
                api.WaitForSingleObject(event, INFINITE)
            api.CloseHandle(event)

    def serve(self, stop: threading.Event | None = None):
        stop = stop or threading.Event()
        security, mutex, dispatcher = UserSecurity(), None, None
        try:
            mutex = UserMutex(self.name, security)
            if self.dispatcher_factory is not None:
                self.dispatch = self.dispatcher_factory()
            if self.dispatch is None:
                raise ValueError("A dispatcher is required")
            dispatcher = threading.Thread(target=self._dispatch_loop, name="c1ai-dispatcher", daemon=False)
            dispatcher.start()
            first = True
            while not stop.is_set():
                # PIPE_REJECT_REMOTE_CLIENTS (8), byte mode, explicit user DACL.
                flags = 0x00000003 | 0x40000000 | (0x00080000 if first else 0)
                handle = win32().check(win32().CreateNamedPipeW(
                    self.address, flags, 8, 64, 65536, 65536, 0,
                    ctypes.byref(security.attributes)))
                first = False
                self.ready.set()
                try:
                    connected = self._listen(handle, stop)
                except Exception:
                    win32().CloseHandle(handle)
                    raise
                if not connected:
                    win32().CloseHandle(handle)
                    break
                if not self._slots.acquire(blocking=False):
                    win32().DisconnectNamedPipe(handle)
                    win32().CloseHandle(handle)
                    continue
                thread = threading.Thread(target=self._handle, args=(handle,), name="c1ai-client", daemon=False)
                with self._handlers_lock:
                    self._handlers.add(thread)
                thread.start()
        except Exception as error:
            self.failure = error
            raise
        finally:
            # Complete all accepted requests before releasing the owner mutex.
            with self._handlers_lock:
                handlers = list(self._handlers)
            for thread in handlers:
                thread.join()
            if dispatcher is not None:
                self._queue.put(None)
                dispatcher.join()
            if mutex is not None:
                mutex.close()
            security.close()


def serve_owner():
    def build_dispatcher():
        from .core import Core
        from .windows_backend import WindowsBackend
        root = state_directory()
        root.mkdir(parents=True, exist_ok=True)
        return Core(WindowsBackend(state_dir=root), state_dir=root).dispatch

    try:
        NamedPipeServer(dispatcher_factory=build_dispatcher).serve()
    except OwnerAlreadyRunning:
        return 0
    return 0
