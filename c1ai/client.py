"""Platform client for the shared CLI/MCP owner."""
import sys

if sys.platform == "darwin":
    from .macos_ipc import call, send_request, RequestUncertain, launch_owner
else:
    from .windows_client import *
