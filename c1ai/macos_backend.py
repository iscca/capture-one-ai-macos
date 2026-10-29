"""Capture One Apple Events adapter; no UI simulation or database writes.

Scalar editing is experimental and requires an explicitly configured Session.
Fast, adjustment-bound rendering is not yet available on this platform.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

from .contracts import FIELDS, MODULES, STRUCTURED_MODULES, Refused
from .macos_tools import MacTools

# Native names checked against Capture One 16.8.6.25's installed scripting dictionary.
PROPERTIES = {field: field for field in FIELDS}
PROPERTIES.update(highlight_recovery="highlightAdjustment", shadow_recovery="shadowRecovery",
                  white_recovery="whiteRecovery", black_recovery="blackRecovery")
RANGES = {"exposure": (-4, 4), "contrast": (-50, 50), "brightness": (-50, 50),
          "saturation": (-100, 100), "temperature": (800, 14000), "tint": (-50, 50),
          **{f: (-100, 100) for f in MODULES["hdr"]}}

# Every mutation rechecks the native selection, original path, process and layer.
# Apple Events are separate transactions: this does not claim UI-thread atomicity.
_NATIVE_DIR = Path(__file__).parent / "native"
SCRIPT = (_NATIVE_DIR / "macos.js").read_text("utf-8").replace(
    "    // MACOS_TOOLS", (_NATIVE_DIR / "macos_tools.js").read_text("utf-8"))



class NativeError(RuntimeError):
    def __init__(self, code, message, dispatched=None, details=None):
        super().__init__(message)
        self.code, self.dispatched, self.details = code, dispatched, details


class MacOSBackend(MacTools):
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.action_hook = None

    def _call(self, action, **payload):
        request = {"action": action, "properties": PROPERTIES, **payload}
        try:
            process = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", SCRIPT,
                                      json.dumps(request, ensure_ascii=False, allow_nan=False)],
                                     capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired as error:
            raise NativeError("APPLE_EVENT_TIMEOUT", "Query the original request_id before any further write",
                              dispatched=None if action in {"set", "clone", "export", "tool_adjust", "module_apply", "navigate"} else False) from error
        if process.returncode:
            raise NativeError("APPLE_EVENT_FAILED", process.stderr.strip(),
                              dispatched=None if action in {"set", "clone", "export", "tool_adjust", "module_apply", "navigate"} else False)
        result = json.loads(process.stdout)
        if result.get("status") != "ok":
            error = result.get("error", {})
            raise NativeError(error.get("code", "APPLE_EVENT_FAILED"), error.get("message", "Native call failed"),
                              dispatched=result.get("dispatched"), details=result)
        return result

    def validate_request(self, request):
        if request['command'] == 'preview' and (request.get('kind') != 'render'
                or request.get('action', 'render') != 'render'):
            raise Refused('MAC_PREVIEW_UNAVAILABLE', 'Mac previews require explicitly authorized kind=render exports; no native/mask renderer is exposed')

    def config(self):
        path = self.state_dir / "macos-config.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    def _writable_session(self, target):
        config = self.config()
        return (config.get("experimental_scalar_writes") is True
                and config.get("version") == target["instance"]["version"]
                and config.get("document") == target["document"]
                and target["document"].get("kind") == "session")

    def _candidate(self, target):
        path = self.state_dir / "macos-candidates.json"
        entries = json.loads(path.read_text("utf-8")) if path.exists() else {}
        # Native IDs may be numeric and reused by another Session, so scope the key.
        from .contracts import digest
        return entries.get(digest({k: target[k] for k in ("document", "image", "variant")}))

    def feature_verification(self, module, observation):
        path=self.state_dir/'macos-validation.json'
        if not path.exists():return {'status':'not_recorded'}
        profile=json.loads(path.read_text('utf-8'))
        version=observation.get('target',{}).get('instance',{}).get('version') if observation.get('target') else None
        if profile.get('capture_one_version')!=version:return {'status':'not_verified_for_this_version'}
        return profile.get('modules',{}).get(module,{'status':'not_recorded'})

    @staticmethod
    def feature_actions(module):
        if module=='layers':
            return {'create_empty':True,'select':True,'set_opacity':True,'set_enabled':True,
                    'rename_tool_created':True,'remove_created':True,'move':False,'duplicate':False}
        if module=='masks':
            return {'create_subject':True,'create_background':True,'fill_density_1':True,'clear':True,
                    'invert':True,'copy':True,'feather_native_amount':True,'luma_range':True,
                    'linear_gradient':False,'radial_gradient':False,'combine':False,'mask_pixels':False}
        if module=='color_mixer':return {'basic':True,'advanced':True,'skin':False,'advanced_inverted':False}
        return {}

    def capabilities_for(self, observation):
        target = observation.get("target")
        enabled = bool(target and self._writable_session(target) and self._candidate(target))
        return {"fields": {f: {"readable": True, "writable": enabled,
                               "min": RANGES[f][0], "max": RANGES[f][1], "tolerance": 0.05 if f == "temperature" else 0.002,
                               "backend": "capture_one_apple_events", "qualification": "experimental",
                               "verification": self.feature_verification(next(m for m, fs in MODULES.items() if f in fs), observation)}
                           for f in FIELDS},
                "modules": {m: {"readable": True, "writable": enabled and m in self.config().get("experimental_modules",[]) and (m!="film_grain" or target["layer"]=="Background"), "qualification": "experimental",
                               "limitations": self.module_limits(m), "verification": self.feature_verification(m, observation),
                               "actions": self.feature_actions(m)}
                            for m in STRUCTURED_MODULES},
                "images": {"create_candidate": bool(target and self._writable_session(target)),
                           "collection_inventory": True, "uuid_selection": True, "pagination": True},
                "preview": {"default": "render", "render": {"available": self.config().get("allow_preview_export") is True,
                                      "source": "capture_one_export", "export_required": True,
                                      "coordinate_mapping": False, "qualification": "experimental"},
                            "native": {"available": False, "render_verified": False,
                                       "reason": "No verified adjustment-bound native renderer on macOS"}},
                "batch": {"writable": False}}

    def context(self):
        result = self._call("context")
        result["platform"] = "macos"
        from .guidance import workflow
        result["workflow"] = workflow()
        result["workflow"].update(
            analysis_first="Mac: context → read(fields=[]) → preview(kind=render)，仅在显式授权低分辨率临时导出后查看实际渲染。",
            module_loop="Mac 当前活动层：read → adjust → 原生回读 → preview(kind=render)，所有新增模块仍为实验性。",
            module_order=["white_balance", "exposure", "hdr", "levels", "curves", "color_mixer", "color_balance"],
            local_after_global="Mac 图层与原生命令已实验接入，按 capabilities 限制操作；蒙版像素无法回读，不据命令完成宣称蒙版质量已通过。")
        result["capabilities"] = self.capabilities_for(result)
        result["limitations"] = ["All Mac writes are experimental, Session-scoped and candidate-only",
                                  "Use preview(kind=render) after export authorization; Skin per-field controls, native mask pixels and undocumented gradient/combine commands remain unavailable"]
        return result

    @staticmethod
    def _observation(result):
        return {"target": result["target"], "values": result["values"],
                "evidence": {"source": "capture_one_apple_events", "model_verified": True,
                             "atomic": False, "adjustment_scope": "background" if result["target"]["layer"] == "Background" else "active_adjustment_layer",
                             "single_selected_count": 1, "visual_validation": False, "native_unset_fields":result.get("native_unset_fields",[]),
                             "processing_engine":result.get("processing_engine")}}

    def observe(self, fields):
        if set(fields) - set(FIELDS):
            raise Refused("unsupported", "Unsupported Mac scalar fields")
        return self._observation(self._call("read", fields=fields))

    def set_values(self, patch, observation):
        target = observation["target"]
        if not self._writable_session(target) or not self._candidate(target):
            raise NativeError("CANDIDATE_REQUIRED", "Configure a Session and create a registered candidate first", False)
        for field, value in patch.items():
            if (field not in PROPERTIES or isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not RANGES[field][0] <= value <= RANGES[field][1]):
                raise NativeError("INVALID_VALUE", "Unsupported field or value", False)
        return self._call("set", expected=target, patch=patch,
                          before=observation["values"], fields=list(observation["values"]))

    def image_action(self, action, request, observation):
        if action in {"list", "select", "next", "previous"}:
            return self.navigation(action, request, observation)
        if action == 'prepare_tool':
            return {"status":"completed", "after":self.observe([]), "native_ready":True,
                    "message":"Native structured reads do not require a visible tool panel"}
        if action != "clone":
            raise Refused("unsupported", "Unsupported Mac compatibility image action")
        target = observation["target"]
        if not self._writable_session(target):
            raise NativeError("SESSION_NOT_CONFIGURED", "Enable the intended Session before creating a candidate", False)
        receipt = self.action_hook("images.clone", lambda: self._call("clone", expected=target))
        after = self._observation(receipt)
        self.register_candidate(after["target"], target)
        return {"status": "completed", "after": after, "candidate_variant_id": receipt["created_id"],
                "new_source_variant_verified": True, "model_verified": True}

    def register_candidate(self, new_target, target):
        from .contracts import digest
        path = self.state_dir / "macos-candidates.json"
        entries = json.loads(path.read_text("utf-8")) if path.exists() else {}
        entries[digest({k: new_target[k] for k in ("document", "image", "variant")})] = {
            "source": target, "created": new_target}
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            import os
            json.dump(entries, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)

    def preview(self, request, observation):
        if request.get("kind") != "render" or request.get("action", "render") != "render":
            raise NativeError("MAC_PREVIEW_UNAVAILABLE", "Use kind=render for explicitly enabled low-resolution exports; native/mask rendering is unavailable", False)
        if self.config().get("allow_preview_export") is not True:
            raise NativeError("EXPORT_NOT_AUTHORIZED", "Explicitly enable temporary preview exports in Mac configuration", False)
        size = request.get("max_size", 1200)
        if type(size) is not int or not 128 <= size <= 1600:
            raise NativeError("INVALID_SIZE", "Preview long edge must be 128..1600 pixels", False)
        import hashlib
        from .contracts import digest
        key = digest({"request_id": request["request_id"]})
        directory = self.state_dir / "renders" / key
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if any(directory.iterdir()):
            raise NativeError("EXISTING_EXPORT", "Export directory is not empty; query original operation", False)
        receipt = self.action_hook("preview.export", lambda: self._call("export", expected=observation["target"],
            recipe_name="C1AI-preview-" + key[:20], output_dir=str(directory), archive_root=str(directory.parent),
            output_name="preview", max_size=size))
        paths = list(directory.glob("*.jpg"))
        if len(paths) != 1:
            raise NativeError("EXPORT_MISSING", "C1 job finished without exactly one expected JPEG", True)
        from PIL import Image
        path = paths[0]
        with Image.open(path) as im:
            im.load()
            width, height = im.size
            if max(width, height) > size or im.format != "JPEG":
                raise NativeError("INVALID_EXPORT", "C1 export dimensions or format do not match recipe", True)
        result = {"source": "capture_one_export", "path": str(path), "export_required": True,
                  "render_verified": True, "coordinate_mapping": False,
                  "image": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "width": width, "height": height, "format": "JPEG"},
                  "target": receipt["target"], "preview_id": key,
                  "revision": hashlib.sha256(receipt["revision"].encode()).hexdigest(),
                  "limits": receipt["limits"]}
        # Keep only eight completed preview directories. Journal receipts remain durable.
        import shutil
        completed = sorted((p for p in directory.parent.iterdir() if p.is_dir() and (p / "complete").exists()),
                           key=lambda p: p.stat().st_mtime)
        (directory / "complete").touch()
        for stale in completed[:-7]:
            shutil.rmtree(stale)
        return {"status": "completed", **result}

    def call(self, operation, **payload):
        raise Refused("unsupported", "Mac dialog/UI automation is not implemented")

    def reconcile(self, record):
        request = record.get("request", {})
        if request.get('command')=='preview' and request.get('kind')=='render':
            import hashlib
            from .contracts import digest
            from PIL import Image
            observed=self.observe([])
            if observed['target']!=record['target']:
                raise Refused('target_changed','Read-only export reconciliation requires the original photo/layer')
            path=self.state_dir/'renders'/digest({'request_id':record['request_id']})/'preview.jpg'
            history=self._call('inspect_export',expected=record['target'],path=str(path))
            proven=(history.get('matched') is True and history.get('event_time',0)>=math.floor(record['created_at'])
                    and path.is_file() and not path.is_symlink())
            if proven:
                with Image.open(path) as im:
                    im.load()
                    proven=im.format=='JPEG' and max(im.size)<=request.get('max_size',1200)
            return {'outcome':'applied_not_restored' if proven else 'unknown','target':observed['target'],
                'evidence':{'source':'native_output_history_and_verified_file','export_completed':proven,
                    'path':str(path) if proven else None,'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if proven else None,
                    'render_revision_verified':False,'replayed':False}}
        if request.get('command')=='adjust' and request.get('module') in STRUCTURED_MODULES:
            observed=self.observe_module(request['module'])
            proof=record.get('result',{})
            accepted=proof.get('evidence',{}).get('native_command_completed') is True
            known=proof.get('after')
            for action in record.get('actions',[]):
                receipt=action.get('result',{})
                partial=action.get('error',{}).get('details',{}) or {}
                if receipt.get('module')==request['module'] and receipt.get('state') is not None:
                    known=self._structured_observation(receipt);accepted=True
                elif isinstance(partial.get('observation'),dict):
                    known=self._structured_observation(partial['observation'])
            same=isinstance(known,dict) and observed['target']==known['target'] and observed['state']==known['state']
            unchanged=observed['target']==record['target'] and observed['state']==record['before']['state']
            pixel_only=request['module']=='masks' and request.get('patch',{}).get('action') not in {'create_subject','create_background','luma_range'}
            outcome=('applied_not_restored' if accepted and same else 'unknown') if pixel_only else ('not_applied' if unchanged else 'applied_not_restored' if same else 'unknown')
            patch=request.get('patch',{})
            removed=patch.get('layer_ref')
            if (request['module']=='layers' and patch.get('action')=='move' and same
                    and isinstance(removed,dict) and removed in record['before']['state']['layers']
                    and observed['state']['layers']==[row for row in record['before']['state']['layers'] if row!=removed]):
                return {'outcome':'observed_deviation','target':observed['target'],
                    'evidence':{'observation':observed,'native_error_state_matches_current':True,
                        'requested_result_verified':False,'deviation':'native_move_removed_source',
                        'source':'capture_one_apple_events','replayed':False}}
            return {'outcome':outcome,
                'target':observed['target'], 'target_transition':proof.get('target_transition'),
                'evidence':{'observation':observed,'source':'capture_one_apple_events',
                    'native_command_completed':accepted,'mask_pixels_verified':False,
                    'requested_pixel_result_verified':False,'partial_result_known':same,'replayed':False}}
        baseline = record.get("before", {}).get("values", {})
        if request.get("command") != "adjust" or request.get("module") not in MODULES or not baseline:
            raise Refused("unsupported", "Only scalar parameter writes support read-only reconciliation on Mac")
        observed = self.observe(list(baseline))
        if observed["target"] != record["target"]:
            raise Refused("target_changed", "Reconciliation must read the same instance, document, photo and layer")
        patch = request["patch"]
        def near(field,a,b):
            if observed['target'].get('layer','Background')!='Background' and field not in {'temperature','tint'}:
                a=0 if a is None else a
                b=0 if b is None else b
            return isinstance(a,(int,float)) and isinstance(b,(int,float)) and math.isclose(a,b,rel_tol=0,abs_tol=0.05 if field=='temperature' else 0.002)
        values = observed["values"]
        bounded = all(near(f, values[f], old) or near(f, values[f], patch.get(f, old)) for f, old in baseline.items())
        unchanged = all(near(f, values[f], old) for f, old in baseline.items())
        requested = all(near(f, values[f], patch.get(f, old)) for f, old in baseline.items())
        # Resolve only when every field is known at baseline or the requested value.
        # Partial application is explicit; no setter, restoration or replay occurs here.
        outcome = "unknown" if not bounded else "not_applied" if unchanged else "applied_not_restored"
        return {"outcome": outcome, "target": observed["target"],
                "evidence": {"source": "capture_one_apple_events", "observation": observed,
                             "read_only": True, "replayed": False, "bounded_to_baseline_or_patch": bounded,
                             "requested_result_verified": requested,
                             "partial_application": bounded and not unchanged and not requested}}
