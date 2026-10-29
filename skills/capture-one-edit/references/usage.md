# Bridge setup and requests

Source and installation: [Capture One AI repository](https://github.com/iscca/capture-one-ai-macos). Install the bridge separately; copying this skill alone does not provide MCP tools or the `c1ai` command. The bridge does not require any model-provider API key. The vision client is configured separately by its user.

## Platform setup

- macOS experimental: follow `docs/MACOS.md`; verified Apple Silicon / Capture One 16.8.6.25, Python 3.11+. Run `Install.command`, open a Session and select a photo. Only after the user's choice, enable that Session with `scripts/configure_macos.py --enable-current-session --enable-experimental-modules`; temporary JPEG previews additionally require `--allow-preview-export`. Normal OS automation authorization is required. Other versions are unverified.
- The Mac installer generates machine-local `config/mcp-server.local.json`. Add it to a client supporting local MCP stdio and image blocks. Server command is the installed Python interpreter with arguments `-m c1ai mcp`; never publish the generated configuration.

## Minimal requests

Examples show tool arguments; use the actual returned values, never placeholder credentials. `context()` identifies capabilities and pending operations. `read(fields=[])` returns the current target and observation token. `images(action=create_candidate, target=..., observation_token=..., request_id=...)` clones/registers/selects a candidate.

```python
import uuid
from c1ai.client import call

before = call({'command': 'read', 'module': 'exposure'})
# Use only after context and candidate verification.
result = call({
    'command': 'adjust', 'module': 'exposure',
    'patch': {'exposure': -0.15},
    'target': before['target'],
    'observation_token': before['observation_token'],
    'request_id': uuid.uuid4().hex,
})
```

The exposure value above illustrates an absolute request, not a suggested grade. If a request has uncertain outcome, use `operations(action=get, request_id=original_id)`; `reconcile` performs the supported read-only reconciliation. Do not repeat the mutation under a new ID. `operations` is not a universal undo command. Restore intentionally with fresh observations and saved values, or supported module `paste` of the saved observation token.

CLI: `c1ai context --compact`, `c1ai read --json '{"module":"exposure"}'`; use `c1ai request --file request.json` for structured requests. CLI returns a preview path; open the actual image to review it. Prefer MCP image blocks when available.

## Adjustment conventions

- Exposure is EV; white balance is temperature/tint. Read the runtime ranges and units.
- Curves use `channel` (`rgb`, `red`, `green`, `blue`, `luma`) and complete `points` in normalized 0..1 coordinates. A patch replaces that channel's points.
- Levels use `channel` (`rgb`, `red`, `green`, `blue`), input/output endpoints 0..255 and midtone −1..1. Keep endpoint order valid.
- Basic mixer uses `mode=basic`, named `color`, and hue/saturation/lightness. `all` has no lightness on Mac. Advanced uses native indexed corrections; read existing state first. Do not equate preview sRGB sampling with native Device RGB selection.
- Color balance uses `wheel` (`master`, `shadows`, `midtones`, `highlights`), `x`/`y`, and applicable `lightness`. Mac hue=0 lies on +x; do not infer the UI's rotated wheel preference. Master has no lightness.
- Mac grain: `module=film_grain`, `patch={type,impact,granularity}`. Types: `fine`, `silver_rich`, `soft`, `cubic`, `tabular`, `harsh`; impact/granularity 0..100, impact=0 disables it. Background only.
- Mask feather `native_amount` 0..100 on Mac is not a pixel `radius`. Check supported actions before issuing a structural mutation.

Mac gaps: Skin parameters, Advanced inversion, layer move/duplicate, gradients/combinations/submask selection, direct mask pixels, no-export native preview and preview↔RAW mapping. An AI mask may be imperfect even when the command succeeds. Runtime verification is host/version-specific; `not_recorded` is not proof of a failed feature, nor proof of local validation.
