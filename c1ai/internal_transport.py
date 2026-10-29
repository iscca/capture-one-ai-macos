"""One model endpoint inside C1; transport only, no UI editing fallback."""
from __future__ import annotations

import uuid
from .client import send_request
from .windows_owner import state_directory, win32


def call(request: dict, *, timeout: float = 35) -> dict:
    payload = dict(request)
    payload.setdefault('request_id', uuid.uuid4().hex)
    payload['token'] = (state_directory() / 'internal-token.txt').read_text('utf-8-sig').strip()
    return send_request(payload, address=rf'\\.\pipe\CaptureOneAI.Internal.{win32().sid()}',
                        autostart=False, timeout=timeout, connect_timeout=2)
