"""One serialized execution path; backend observations are evidence, not promises."""
import math
from collections import Counter
import threading
from types import MappingProxyType

from .contracts import (COMMANDS, IMAGE_ACTIONS, MODULES, STRUCTURED_MODULES, Refused, fields_for,
                        validate_patch, validate_patch_schema, validate_structured_patch, validate_module_action, layer_ref, digest)
from .journal import Journal, PENDING, same_scope
from .guidance import workflow, read_guidance

NUMERIC_RESTORE_SCOPE = {"restore_scope": "observed_numeric_values", "full_model_restore_verified": False,
                         "pixel_restore_verified": None}


class Core:
    def __init__(self, backend, state_dir=None):
        self.backend = backend
        self.journal = Journal(state_dir)
        self._lock = threading.RLock()
        from .editing_tasks import EditingTasks
        self.tasks = EditingTasks(self)

    def dispatch(self, request):
        with self._lock:
            try:
                if not isinstance(request, dict) or request.get("command") not in COMMANDS:
                    raise Refused("invalid_command", "An explicit supported command is required")
                if request['command']=='preview' and 'kind' not in request:
                    request={**request,'kind':'native'}
                digest(request)  # Reject non-JSON values, including NaN, before any work.
                command = request["command"]
                if command == "context":
                    context = self.backend.context()
                    context["editing_tasks"] = self.tasks.summary(context)
                    if request.get("include_dialogs") is True:
                        native_windows = context.get("native_windows")
                        if isinstance(native_windows, dict) and (
                                native_windows.get("blocking_modal_possible") is True
                                or native_windows.get("main_enabled") is False
                                or native_windows.get("enumeration_complete") is False):
                            context["dialogs"] = {"source": "native_window_inventory",
                                "candidates": native_windows.get("modal_candidates", []),
                                "uia_queried": False, "uia_skipped_reason": "owned_window_blocks_main"
                                if native_windows.get("blocking_modal_possible") is True else "native_window_state_unavailable"}
                        else:
                            context["dialogs"] = self.backend.call("dialogs")
                    return dict(context, status="ok", workflow=context.get("workflow", workflow()), pending_operations=self.journal.pending(context.get("target")))
                if command == "read":
                    if request.get("module") in STRUCTURED_MODULES:
                        if "fields" in request or "modules" in request:
                            raise Refused("invalid_fields", "A structured read names one module without fields/modules")
                        observation = self._observe_module(request["module"])
                    else:
                        observation = self._observe(fields_for(request))
                    self._target(request.get("target"), observation["target"])
                    return dict(observation, status="ok", guidance=read_guidance(request,observation))
                if command == "operations":
                    if request.get("action") == "task":
                        return self.tasks.dispatch(request)
                    return self._public_result(self._operations(request))
                if command == "images" and request.get("action") == "list":
                    if (request.get('scope','filtered') not in {'filtered','all'}
                            or type(request.get('offset',0)) is not int or request.get('offset',0)<0
                            or type(request.get('limit',200)) is not int or not 1<=request.get('limit',200)<=500):
                        raise Refused('invalid_inventory','Use scope filtered/all, nonnegative offset and limit 1..500')
                    result=self.backend.image_action("list", request, None)
                    observation=result.pop('_navigation_observation',None)
                    if observation is not None:
                        result.update(target=observation['target'],observation_token=self.journal.observe(observation))
                    return result
                return self._public_result(self._write(request))
            except Refused as error:
                return {"status": "refused", "error": {"code": error.code, "message": str(error)}}
            except Exception as error:
                return {"status": "refused", "error": self._error(error)}

    def peek(self, request):
        """Read committed progress without taking the execution lock or touching C1."""
        try:
            if not isinstance(request, dict) or request.get("command") != "operations":
                raise Refused("readonly_only", "Progress lookup only supports operations get/list")
            action = request.get("action", "get")
            if action not in {"get", "list"}:
                raise Refused("readonly_only", "Progress lookup cannot execute recovery or writes")
            digest(request)
            result = self.journal.peek(action, request.get("operation_id"), request.get("request_id"))
            return self._public_result({"status": "ok", "operations": result} if action == "list" else result)
        except Refused as error:
            return {"status": "refused", "error": {"code": error.code, "message": str(error)}}
        except Exception as error:
            return {"status": "refused", "error": self._error(error)}

    @staticmethod
    def _public_result(result):
        """Describe legacy terminal receipts without rewriting or re-executing them."""
        if isinstance(result.get("operations"), list):
            return dict(result, operations=[Core._public_result(record) for record in result["operations"]])
        if (result.get("status") == "restored" and not result.get("legacy")
                and result.get("request", {}).get("command") == "adjust"
                and result.get("request", {}).get("module") in MODULES):
            result = dict(result)
            for key, value in NUMERIC_RESTORE_SCOPE.items():
                result.setdefault(key, value)
        return result

    @staticmethod
    def _error(error):
        return {"code": getattr(error, "code", "backend_error"), "message": str(error),
                "details": getattr(error, "details", None), "dispatched": getattr(error, "dispatched", None)}

    def _observe(self, fields):
        observation = self.backend.observe(fields)
        if not isinstance(observation, dict) or not isinstance(observation.get("target"), dict):
            raise Refused("invalid_observation", "Backend did not identify the observation target")
        values = observation.get("values")
        if not isinstance(values, dict) or any(field not in values for field in fields):
            raise Refused("incomplete_observation", "Backend did not read all requested fields")
        observation = dict(observation, values={field: values[field] for field in fields})
        observation["observation_token"] = self.journal.observe(observation)
        return observation

    def _observe_module(self, module):
        observe = getattr(self.backend, "observe_module", None)
        if module not in STRUCTURED_MODULES or not callable(observe):
            raise Refused("unsupported", "Backend has no structured observation route")
        observation = observe(module)
        if (not isinstance(observation, dict) or observation.get("module") != module
                or not isinstance(observation.get("target"), dict)
                or not isinstance(observation.get("state"), dict)):
            raise Refused("invalid_observation", "A structured observation requires module, target and complete state")
        if module in {"layers", "masks"}:
            state = observation["state"]
            inventory, selected = state.get("layers"), state.get("selected_layer")
            if not isinstance(inventory, list) or not inventory:
                raise Refused("incomplete_observation", "Layer observations require a nonempty native inventory")
            identifiers = [tuple(layer_ref(item)["runtime_id"]) for item in inventory]
            layer_ref(selected)
            target_layer = observation["target"].get("layer")
            if (len(set(identifiers)) != len(inventory) or selected not in inventory
                    or sum(item["is_image_layer"] for item in inventory) != 1
                    or not (target_layer == selected or target_layer == "Background" and selected["is_image_layer"])):
                raise Refused("invalid_observation", "Layer inventory, selected reference and target must agree")
        observation = dict(observation, values={module: observation["state"]})
        observation["observation_token"] = self.journal.observe(observation)
        return observation

    @staticmethod
    def _target(expected, actual):
        if expected is not None and expected != actual:
            raise Refused("target_changed", "The selected target or application instance changed")

    def _preflight(self, request):
        if not isinstance(request.get("target"), dict) or not request.get("observation_token"):
            raise Refused("missing_precondition", "target and observation_token are required")
        saved = self.journal.observation(request["observation_token"])
        if saved.get('navigation'):
            if request['command']!='images' or request.get('action')!='select':
                raise Refused('navigation_only','A list token can select a photo; read the photo before editing')
            current=self.backend.image_action('list',{'limit':1,'scope':saved.get('native_inventory_scope','filtered')},None)['_navigation_observation']
            self._target(request['target'],current['target'])
            if self.journal.observe(current)!=request['observation_token']:
                raise Refused('stale_selection','Collection or selection changed after listing')
            return current
        module = saved.get("module")
        if request.get("command") == "adjust" and ((request.get("module") in STRUCTURED_MODULES or module)
                                                   and request.get("module") != module):
            raise Refused("wrong_observation", "Observe the same adjustment module before writing")
        if module:
            # Reuse the caller's module read. Each native adapter compares its
            # expected controls/parameters immediately at dispatch and reads back
            # once afterward. Only the photo target needs a fresh check here.
            current = self.backend.observe([])
            self._target(saved["target"], current["target"])
            self._target(request["target"], current["target"])
            observation = dict(saved, observation_token=request["observation_token"])
        else:
            observation = self._observe(list(saved["values"]))
            self._target(request["target"], observation["target"])
            if observation["observation_token"] != request["observation_token"]:
                raise Refused("stale_observation", "Values changed after the supplied observation")
        return observation

    def _write(self, request):
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise Refused("missing_request_id", "A nonempty request_id is required")
        existing = self.journal.existing(request)
        if existing:
            return dict(existing, replayed=False, existing=True)
        command = request["command"]
        validate_backend = getattr(self.backend, "validate_request", None)
        if callable(validate_backend):
            validate_backend(request)
        if command == "images" and request.get("action") not in IMAGE_ACTIONS:
            raise Refused("unsupported", "Unsupported image action")
        if command == "images" and request.get("action") == "prepare_tool" and request.get("tool") not in STRUCTURED_MODULES:
            raise Refused("unsupported", "Prepare one supported structured tool")
        if command == "preview":
            kind, action = request.get("kind", "native"), request.get("action", "render")
            if not ((kind == "render" and action in {"prepare", "render", "restore_destination"})
                    or (kind in {'native','mask'} and action in {'render','map_points'})
                    or (kind == "viewer" and action == "capture")):
                raise Refused("unsupported", "Use native/mask render/map_points, legacy render export, or viewer capture")
            if kind in {'native','mask'}:
                if type(request.get('max_size',1600)) is not int or not 128<=request.get('max_size',1600)<=1600:
                    raise Refused('invalid_preview_size','Native preview max_size must be an integer in 128..1600')
                if action=='map_points' and (not isinstance(request.get('preview_id'),str)
                        or not isinstance(request.get('points'),list) or len(request['points'])>256
                        or request.get('direction','preview_to_raw') not in {'preview_to_raw','raw_to_preview'}):
                    raise Refused('invalid_points','Use the returned preview_id and up to 256 coordinate pairs')
        structured = command == "adjust" and request.get("module") in STRUCTURED_MODULES
        module_action = command == 'adjust' and isinstance(request.get('patch'),dict) and request['patch'].get('action') in {'reset','paste'}
        patch = (validate_module_action(request) if module_action else validate_structured_patch(request) if structured else validate_patch_schema(request)) if command == "adjust" else None
        observation = self._preflight(request)
        metadata = self._metadata(observation) if patch and not structured else {}
        if structured:
            capability = self._capabilities(observation).get("modules", {}).get(request["module"], {})
            if capability.get("writable") is not True or not callable(getattr(self.backend, "adjust_module", None)):
                raise Refused("unsupported", "This structured module is not qualified for writing")
            if not observation["state"]:
                raise Refused("incomplete_observation", "A structured write requires nonempty observed state")
            self._layer_preconditions(request, observation)
        elif patch and not module_action:
            validate_patch(request, metadata)
        if patch and not structured and not module_action and any(field not in observation["values"] for field in patch):
            raise Refused("incomplete_observation", "Observe every field in the patch first")
        record, created = self.journal.begin(request, observation["target"], observation)
        if not created:
            return dict(record, replayed=False, existing=True)
        operation_id = record["operation_id"]
        old_hook = getattr(self.backend, "action_hook", None)
        self.backend.action_hook = lambda name, func, intent=None: self._action(
            operation_id, name, {"action": name, **(intent or {})}, func)
        try:
            if module_action:
                from .native_modules import apply_module_action
                source=self.journal.observation(patch['source_observation_token']) if patch['action']=='paste' else None
                adapter_apply=getattr(self.backend,'apply_module_action',None)
                result=adapter_apply(request['module'],patch,observation,source) if callable(adapter_apply) else apply_module_action(self.backend,request['module'],patch,observation,source)
                after=result['after'];self._target(observation['target'],after['target'])
                after['observation_token']=self.journal.observe(after)
                return self.journal.update(operation_id,status='control_confirmed',after=after,result=result,model_verified=True)
            if command == "adjust" and not structured:
                return self._adjust(record, patch, metadata, observation)
            image_action = "clone" if request.get("action") == "create_candidate" else request.get("action")
            if structured:
                result = self.backend.adjust_module(request["module"], patch, observation)
            else:
                result = (self.backend.image_action(image_action, request, observation) if command == "images"
                          else self.backend.preview(request, observation))
            if not isinstance(result, dict) or result.get("status") not in {
                    "completed", "partial", "outcome_unknown", "refused", "control_confirmed"}:
                raise Refused("invalid_backend_result", "Backend must return an explicit supported operation status")
            status = result["status"]
            actions = self.journal.get(operation_id=operation_id)["actions"]
            if (status in {"completed", "control_confirmed"}
                    and any(action["status"] in {"dispatching", "outcome_unknown"} for action in actions)):
                raise Refused("invalid_backend_result", "A success response cannot close an unresolved action")
            if status == "refused" and any(action["status"] != "not_dispatched" for action in actions):
                raise Refused("invalid_backend_result", "A refusal cannot erase already dispatched actions")
            if structured and status in {"completed", "control_confirmed"}:
                if result.get("requested_result_verified") is not True:
                    raise Refused("requested_result_unverified", "The module adapter must explicitly verify the requested patch, not only read back a state")
                after = result.get("after")
                if (not isinstance(after, dict) or after.get("module") != request["module"]
                        or not isinstance(after.get("state"), dict) or not result.get("evidence")):
                    raise Refused("invalid_backend_result", "Structured success requires a module readback and evidence")
                self._structured_target(record, after, result.get("target_transition"))
                # The module adapter has already read back its result. Reuse
                # it instead of immediately scanning the same controls again.
                fresh = dict(after, values={request["module"]: after["state"]})
                fresh["observation_token"] = self.journal.observe(fresh)
                if not actions and (fresh["target"] != observation["target"] or fresh["state"] != observation["state"]):
                    return self.journal.update(operation_id, status="outcome_unknown", model_verified=False,
                        error={"code": "missing_actions", "message": "A changed structured result has no durable action receipt"})
                result = dict(result, after=fresh)
                return self.journal.update(operation_id, status=status, after=fresh, result=result,
                    model_verified=result.get('evidence',{}).get('model_verified') is True)
            changes = {"status": status, "result": result, "model_verified": result.get('model_verified') is True}
            if isinstance(result.get("after"), dict) and isinstance(result["after"].get("target"), dict):
                result['after']['observation_token']=self.journal.observe(result['after'])
                changes["after"] = result["after"]
            return self.journal.update(operation_id, **changes)
        except Exception as error:
            return self._uncertain(operation_id, error)
        finally:
            self.backend.action_hook = old_hook

    def _action(self, operation_id, name, intent, func):
        action = self.journal.start_action(operation_id, name, intent)
        old_context = getattr(self.backend, "action_context", None)
        self.backend.action_context = MappingProxyType({"operation_id": operation_id, "action_id": action["action_id"]})
        try:
            result = func()
            if isinstance(result, dict) and result.get("status") in {"dispatching", "outcome_unknown", "refused", "not_dispatched"}:
                error = Refused("unresolved_backend_action", "A backend action returned an explicit unsuccessful or unresolved receipt")
                error.dispatched = not (result["status"] in {"refused", "not_dispatched"} and result.get("dispatched") is False)
                error.details = result
                raise error
        except BaseException as error:
            # Only the trusted adapter or this core may certify dispatch. Raw
            # error details/message text cannot certify an enclosing action.
            dispatched = getattr(error, "dispatched", None)
            known_not_dispatched = dispatched is False or (
                dispatched is not True and getattr(error, "not_dispatched", None) is True)
            status = "not_dispatched" if known_not_dispatched else "outcome_unknown"
            self.journal.finish_action(action, status=status, error=self._error(error))
            raise
        finally:
            self.backend.action_context = old_context
        self.journal.finish_action(action, status="completed", result=result)
        return result

    def _uncertain(self, operation_id, error):
        try:
            record = self.journal.get(operation_id=operation_id)
            status = ("refused" if all(a["status"] == "not_dispatched" for a in record["actions"]) else
                      "outcome_unknown" if any(a["status"] in {"dispatching", "outcome_unknown"}
                                               for a in record["actions"]) else "partial")
            return self.journal.update(operation_id, status=status, error=self._error(error), model_verified=False)
        except Exception:
            return {"operation_id": operation_id, "status": "outcome_unknown", "error": self._error(error),
                    "model_verified": False}

    @staticmethod
    def _matches(actual, expected, metadata):
        tolerance = metadata.get("tolerance", 0)
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(
            actual, expected, rel_tol=0, abs_tol=tolerance)

    def _metadata(self, observation):
        return self._capabilities(observation).get("fields", {})

    def _capabilities(self, observation):
        derive = getattr(self.backend, "capabilities_for", None)
        return derive(observation) if callable(derive) else self.backend.context().get("capabilities", {})

    def _adjust(self, record, patch, metadata, observation):
        operation_id=record['operation_id']
        changed={field:value for field,value in patch.items() if not self._matches(observation['values'][field],value,metadata[field])}
        if changed:
            result=self._action(operation_id,'parameters.set',{'patch':changed},
                lambda:self.backend.set_values(changed,observation))
            if (isinstance(result,dict) and result.get('model_verified') is True
                    and observation.get('evidence',{}).get('source')=='c1_internal_model'
                    and result.get('target')==observation['native_context']['target']
                    and isinstance(result.get('values'),dict) and result['values'].keys()==observation['values'].keys()):
                # Native setters return the complete module in the same UI-thread call.
                after={**observation,'values':result['values'],'native_readback':result}
                after['observation_token']=self.journal.observe(after)
            else:after=self._observe(list(observation['values']))
            self._target(observation['target'],after['target'])
            expected={**observation['values'],**changed}
            verified=all(self._matches(after['values'][field],value,metadata[field]) for field,value in expected.items())
            if not verified:
                return self.journal.update(operation_id,status='partial',after=after,model_verified=False,
                    error={'code':'native_readback_mismatch','message':'Native parameter readback differs from requested module values'})
        else:after=observation
        return self.journal.update(operation_id,status='control_confirmed',after=after,
            progress={field:'control_confirmed' if field in changed else 'unchanged' for field in patch},
            model_verified=after.get('evidence',{}).get('model_verified') is True)

    def _layer_preconditions(self, request, observation):
        patch = request["patch"]
        if request['module']=='masks':
            if not patch['action'].startswith('create_') and observation['state']['selected_layer']['is_image_layer']:
                raise Refused('adjustment_layer_required','Select an adjustment layer before changing its mask')
            if 'source_layer' in patch and (patch['source_layer'] not in observation['state']['layers'] or patch['source_layer']['is_image_layer'] or patch['source_layer']==observation['state']['selected_layer']):
                raise Refused('invalid_mask_source','Use a different adjustment layer from the current observation')
        if request["module"] != "layers" or "layer_ref" not in patch:
            return
        if patch["layer_ref"] not in observation["state"].get("layers", []):
            raise Refused("stale_layer_ref", "The requested layer is absent from the observed inventory")
        if patch["action"] != 'select' and (patch['layer_ref']['is_image_layer'] or patch["layer_ref"] != observation["state"].get("selected_layer")):
            raise Refused("layer_not_selected", "Select and re-observe the exact layer before editing or removing it")
        if patch["action"] == "remove_created":
            origin = self.journal.get(operation_id=patch["creation_operation_id"])
            original_request = origin.get("request", {})
            if (origin["status"] not in {"completed", "control_confirmed", "applied_not_restored"}
                    or original_request.get("command") != "adjust"
                    or original_request.get("module") not in {"layers", "masks"}
                    or original_request.get("patch", {}).get("action") not in {
                        "create_empty", "create_subject", "create_background", "duplicate"}):
                raise Refused("unowned_layer", "Only a confirmed layer created by this owner can be removed")
            for key in ("instance", "document", "image", "variant"):
                if origin["target"].get(key) != observation["target"].get(key):
                    raise Refused("unowned_layer", "The layer creation belongs to another target or application instance")
            proof = origin.get("result", {})
            for key in ("reconciliation", "recovery"):
                if origin.get(key, {}).get("outcome") == "applied_not_restored":
                    proof = origin[key]
            transition = proof.get("target_transition", {})
            created=transition.get("evidence", {}).get("selected_layer",{})
            same_id=created.get('native_id')==patch['layer_ref'].get('native_id')
            # C1 replaces the mask GUID when copying a mask, but retains the
            # native layer object. This identity is scoped to the same instance.
            same_object=created.get('object_id') is not None and created['object_id']==patch['layer_ref'].get('object_id')
            if not (same_id or same_object):
                raise Refused("unowned_layer", "The requested layer does not match its original creation receipt")

    @staticmethod
    def _structured_target(record, after, transition=None):
        """Layer changes require a bounded inventory transition, never relaxed target equality."""
        before, target = record["before"], after.get("target")
        request, expected = record["request"], record["target"]
        module, patch = request["module"], request["patch"]
        if not isinstance(target, dict):
            raise Refused("invalid_observation", "Structured readback has no target")
        for key in ("instance", "document", "image", "variant"):
            if expected.get(key) != target.get(key):
                raise Refused("target_changed", "Structured edits cannot change application, document, image or variant")
        action = patch.get("action")
        structural = module in {"layers", "masks"} and action != "set_opacity"
        if not structural:
            Core._target(expected, target)
            return
        if (not isinstance(transition, dict) or transition.get("before") != expected
                or transition.get("after") != target or not isinstance(transition.get("evidence"), dict)):
            raise Refused("missing_transition", "A layer action requires explicit before/after inventory evidence")
        evidence = transition["evidence"]
        old, new, selected = evidence.get("before_layers"), evidence.get("after_layers"), evidence.get("selected_layer")
        if (not isinstance(old, list) or not isinstance(new, list)
                or old != before.get("state", {}).get("layers") or new != after.get("state", {}).get("layers")
                or selected != after.get("state", {}).get("selected_layer")):
            raise Refused("invalid_transition", "Layer inventories must match both module observations")

        def inventory(items):
            mapping = {tuple(layer_ref(item)["runtime_id"]): item for item in items}
            if len(mapping) != len(items):
                raise Refused("invalid_transition", "Layer runtime references must be unique")
            return mapping

        old_map, new_map = inventory(old), inventory(new)
        layer_ref(selected)
        if selected not in new or not (target.get("layer") == selected
                or target.get("layer") == "Background" and selected["is_image_layer"]):
            raise Refused("invalid_transition", "Selected layer must bind the resulting target")
        if action in {"create_empty", "create_subject", "create_background", "duplicate"}:
            valid = (len(new) == len(old) + 1 and old_map.keys() <= new_map.keys()
                     and all(new_map[k]==v for k,v in old_map.items())
                     and not selected['is_image_layer'] and tuple(selected['runtime_id']) not in old_map)
            if not valid:
                raise Refused("invalid_transition", "Created layer must have one new native identity and preserve existing layers")
            return
        if action=='rename':
            changed={**patch['layer_ref'],'name':patch['name']}
            if new != [changed if n==patch['layer_ref'] else n for n in old] or selected!=changed:
                raise Refused('invalid_transition','Rename must preserve layer identity and order')
            return
        if module=='masks' and action=='copy':
            old_selected=before['state']['selected_layer']
            untouched=[n for n in old if n!=old_selected]
            if len(old)!=len(new) or [n for n in new if n!=selected]!=untouched or selected.get('object_id')!=old_selected.get('object_id'):
                raise Refused('invalid_transition','Mask copy must preserve the destination layer and unrelated layers')
            return
        common = old_map.keys() & new_map.keys()
        if any(old_map[key] != new_map[key] for key in common):
            raise Refused("invalid_transition", "An unrelated layer changed during the operation")
        added, removed = new_map.keys() - old_map.keys(), old_map.keys() - new_map.keys()
        if action == "select":
            valid = not added and not removed and selected == patch["layer_ref"]
        elif action == "remove_created":
            valid = not added and removed == {tuple(patch["layer_ref"]["runtime_id"])} and not patch["layer_ref"]["is_image_layer"]
        elif action=='move':
            reordered=[n for n in old if n!=patch['layer_ref']]
            reordered.insert(patch['index']+1,patch['layer_ref'])
            valid=new==reordered and selected==patch['layer_ref']
        elif action=='set_enabled' or module=='masks':
            valid=old==new
        else:
            valid = False
        if not valid:
            raise Refused("invalid_transition", "Observed layer changes do not match the requested action")

    def _operations(self, request):
        action = request.get("action", "get")
        if action == "list":
            return {"status": "ok", "operations": self.journal.list()}
        record = self.journal.get(request.get("operation_id"), request.get("request_id"))
        if action == "get":
            return record
        if action == "reconcile":
            return self._reconcile(record)
        if action in {"restore", "recover"}:
            raise Refused("unsupported", "Automatic recovery is disabled; read current values and submit an explicit adjustment")
        raise Refused("unsupported", "Unsupported operation action")

    @staticmethod
    def _validated_outcome(record, result):
        if not isinstance(result, dict) or not result.get("evidence"):
            raise Refused("missing_evidence", "Recovery requires backend evidence")
        if not same_scope(record.get("target"), result.get("target")):
            raise Refused("target_changed", "Reconciliation evidence belongs to another document/image")
        outcome = result.get("outcome")
        if outcome == 'observed_deviation':
            # A read-only receipt may describe a known host failure without certifying intent.
            # Limit this case to a native move which removed exactly its requested source.
            request=record.get('request',{})
            observed=result['evidence'].get('observation',{})
            old=record.get('before',{}).get('state',{}).get('layers',[])
            state=observed.get('state',{})
            removed=request.get('patch',{}).get('layer_ref')
            if (request.get('command')!='adjust' or request.get('module')!='layers'
                    or request.get('patch',{}).get('action')!='move'
                    or not isinstance(removed,dict) or removed.get('is_image_layer') is not False
                    or removed not in old or state.get('layers')!=[item for item in old if item!=removed]
                    or observed.get('target')!=result['target']
                    or result['evidence'].get('native_error_state_matches_current') is not True
                    or result['evidence'].get('requested_result_verified') is not False):
                raise Refused('invalid_evidence','Unknown structural deviations cannot be terminalized')
            if any(record['target'].get(k)!=result['target'].get(k) for k in ('instance','document','image','variant')):
                raise Refused('target_changed','Deviation evidence changed the photo identity')
            selected=state.get('selected_layer')
            if selected not in state['layers'] or not (result['target'].get('layer')==selected
                    or selected['is_image_layer'] and result['target'].get('layer')=='Background'):
                raise Refused('invalid_evidence','Deviation has inconsistent selected-layer identity')
            return 'applied_not_restored'
        if outcome not in {"applied_not_restored", "not_applied", "restored", "partial", "unknown"}:
            raise Refused("invalid_evidence", "Unrecognized reconciliation outcome")
        if record.get("request", {}).get("module") in STRUCTURED_MODULES:
            if outcome == "restored":
                raise Refused("unsupported", "Structured automatic restoration has been retired")
            observed = result["evidence"].get("observation")
            if outcome == "not_applied":
                if (not isinstance(observed, dict) or observed.get("target") != record["target"]
                        or result["target"] != record["target"] or observed.get("module") != record["request"]["module"]
                        or observed.get("state") != record["before"].get("state")):
                    raise Refused("invalid_evidence", "Not-applied requires unchanged complete structured state")
            elif outcome != "unknown":
                if (not isinstance(observed, dict) or observed.get("module") != record["request"]["module"]
                        or observed.get("target") != result["target"] or not isinstance(observed.get("state"), dict)):
                    raise Refused("invalid_evidence", "Structured reconciliation requires a complete module observation")
                Core._structured_target(record, observed, result.get("target_transition"))
            if outcome != "unknown":
                return outcome
        expected_target, actual_target = record["target"], result["target"]
        for address_part in ("variant", "layer"):
            if expected_target.get(address_part) != actual_target.get(address_part):
                raise Refused("target_changed", "Reconciliation evidence belongs to another variant/layer")
        return "outcome_unknown" if outcome == "unknown" else outcome

    def _reconcile(self, record):
        if record["status"] not in PENDING:
            return dict(record, existing=True, replayed=False)
        if not callable(getattr(self.backend, "reconcile", None)):
            raise Refused("unsupported", "Backend has no qualified read-only reconciliation")
        result = self.backend.reconcile(record)
        status = self._validated_outcome(record, result)
        outcome = result["outcome"]
        changes = {"status": status, "reconciliation": result, "reconciled_target": result["target"],
                   "reconciled_action_count": len(record["actions"]), "reconciled_numeric_after": False,
                   "model_verified": False}
        if record.get("request", {}).get("module") in STRUCTURED_MODULES and outcome in {
                "applied_not_restored", "partial", "not_applied", "observed_deviation"}:
            observed = result["evidence"]["observation"]
            changes["after"] = dict(observed, values={record["request"]["module"]: observed["state"]})
        if (not record.get("legacy") and record.get("request", {}).get("command") == "adjust"
                and record.get("request", {}).get("module") in MODULES):
            observed = result["evidence"].get("observation")
            baseline, patch = record["before"]["values"], record["request"]["patch"]
            if outcome == "restored":
                if (not isinstance(observed, dict) or observed.get("target") != result["target"]
                        or observed.get("values") != baseline):
                    raise Refused("invalid_evidence", "A restored numeric result requires complete baseline readback")
                changes["restore_observation"] = observed
                changes.update(NUMERIC_RESTORE_SCOPE)
            if isinstance(observed, dict) and observed.get("target") == result["target"]:
                values = observed.get("values", {})
                bounded = values.keys() == baseline.keys() and all(
                    values[field] in (value, patch.get(field, value)) for field, value in baseline.items())
                if bounded and outcome in {"applied_not_restored", "partial"}:
                    changes.update(after=observed, restore_observation=observed, reconciled_numeric_after=True)
        return self.journal.update(record["operation_id"], **changes)

    def import_legacy(self, record, source_path, source_sha256=None):
        with self._lock:
            return self.journal.import_legacy(record, source_path, source_sha256)

    def close(self):
        with self._lock:
            self.journal.close()
