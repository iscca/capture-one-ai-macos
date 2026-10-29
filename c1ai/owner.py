"""Select local IPC without loading Windows bindings on macOS."""
import sys

if sys.platform == "darwin":
    from .macos_ipc import *
else:
    from .windows_owner import *
