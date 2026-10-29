"""Compact presentation of owner responses; durable records remain untouched."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_LOCATORS = {"raw_state", "nodes", "node", "selector", "selectors", "bindings",
             "guard", "post_guard", "runtime_id", "ancestors", "previous_sibling"}
_OBSERVATION = {"status", "target", "values", "evidence", "observation_token",
                "source", "scope", "limits", "model_verified", "render_verified",
                "error", "errors", "warnings", "missing", "module", "state", "guidance"}
_WRITE = {"operation_id", "request_id", "status", "progress", "after", "target",
          "result", "error", "errors", "model_verified", "model_commit_verified",
          "render_verified", "control_readback_verified", "quality_approved", "evidence",
          "reconciliation", "recovery", "reconciled_target", "reconciled_numeric_after", "restored",
          "replayed", "replay_allowed", "existing", "legacy", "query", "source_path",
          "source_sha256", "source", "scope", "limits", "image_delivery"}
_WRITE |= {"focus", "focus_changed", "focus_policy", "side_effects", "warnings"}
_WRITE |= {"restore_scope", "full_model_restore_verified", "pixel_restore_verified",
           "restore_observation", "restore_values", "module", "state", "target_transition", "requested_result_verified", "requested_patch"}


def _focus_summary(value: Any) -> Any:
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {key: deepcopy(item) for key, item in value.items() if key != "changes"}
    if isinstance(value.get("changes"), list):
        result["recorded_changes"] = len(value["changes"])
    # Keep both before/after, unchanged and availability facts. Unchanged=False
    # alone does not prove a change when the monitor could not read focus.
    result["observation_complete"] = (value.get("hooks_installed") is True
        and isinstance(value.get("before"), dict) and value["before"].get("readable") is True
        and isinstance(value.get("after"), dict) and value["after"].get("readable") is True)
    return result


def _prune(value: Any) -> Any:
    if isinstance(value, list):
        return [_prune(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    if "target" in value and ("values" in value or "module" in value and "state" in value):
        return {key: (deepcopy(item) if key in {"target", "error", "errors", "state"}
                     or key == "values" and "module" in value else _prune(item))
                for key, item in value.items() if key in _OBSERVATION}
    result = {}
    for key, item in value.items():
        # Error payloads are deliberately not filtered: possibly_mutated,
        # recovery receipts and other failure qualifications must survive.
        if key in {"error", "errors", "target", "reconciled_target"}:
            result[key] = deepcopy(item)
        elif key in {"layer_ref", "selected_layer", "target_transition"}:
            result[key] = deepcopy(item)
        elif key == "focus":
            result[key] = _focus_summary(item)
        elif key in _LOCATORS or key == "before" and isinstance(item, dict):
            continue
        elif key == "browser_evidence" and isinstance(item, dict):
            result[key] = _prune({name: item[name] for name in item if name not in {"items", "observation"}})
        else:
            result[key] = _prune(item)
    return result


def _action_summary(action: dict) -> dict:
    result = {key: deepcopy(action[key]) for key in ("action_id", "name", "status", "error") if key in action}
    intent = action.get("intent", {})
    if isinstance(intent, dict):
        numeric_intent = {key: deepcopy(intent[key]) for key in ("field", "before", "after")
                          if key in intent and not isinstance(intent[key], (dict, list))}
        if numeric_intent:
            result["intent"] = numeric_intent
    outcome = action.get("result")
    if isinstance(outcome, dict):
        facts = {key: _prune(outcome[key]) for key in (
            "accepted", "control_confirmed", "model_verified", "render_verified",
            "unexpected_changes", "error", "focus_changed", "focus_policy", "side_effects") if key in outcome}
        if "focus" in outcome:
            facts["focus"] = _focus_summary(outcome["focus"])
        backend = outcome.get("backend")
        if isinstance(backend, dict):
            evidence = {key: deepcopy(backend[key]) for key in (
                "backend", "accepted", "verified", "identity_verified", "control_readback_verified",
                "model_verified", "render_verified", "focus_changed", "focus_policy", "input_sent") if key in backend}
            if "focus" in backend:
                evidence["focus"] = _focus_summary(backend["focus"])
            if evidence:
                facts["backend_evidence"] = evidence
        if facts:
            result["result"] = facts
    return result


def compact_response(response: dict, *, command: str | None = None,
                     action: str | None = None) -> dict:
    """Keep decision facts; operations/get is the explicit full-log escape hatch."""
    if command == "operations" and action in (None, "get"):
        return deepcopy(response)
    if command == "operations" and action == "task":
        return deepcopy(response)
    if command == "read":
        return {key: (deepcopy(value) if key in {"error", "errors", "target", "state"}
                     or key == "values" and "module" in response else _prune(value))
                for key, value in response.items()
                if key in _OBSERVATION | {"error", "errors", "request_id", "operation_id", "query"}}
    if command == "operations" and action == "list":
        result = {key: _prune(value) for key, value in response.items() if key != "operations"}
        result["operations"] = [compact_response(item, command="operation_summary")
                                for item in response.get("operations", [])]
        return result
    if "operation_id" in response:
        result = {key: (deepcopy(value) if key in {"error", "errors"} else _prune(value))
                  for key, value in response.items() if key in _WRITE}
        actual = response.get("result")
        module = response.get("request", {}).get("module")
        if module:
            result["module"] = module
            if "patch" in response["request"]:
                result["requested_patch"] = deepcopy(response["request"]["patch"])
        # A clone's actual after observation is inside its result; the original
        # journal's generic after may still contain the pre-clone observation.
        if isinstance(actual, dict) and isinstance(actual.get("after"), dict):
            if "target" in actual["after"] and "values" in actual["after"]:
                result["after"] = _prune(actual["after"])
        if (response.get("request", {}).get("action") == "restart_for_native_transport"
                and response.get("status") == "applied_not_restored"):
            for key in ("reconciliation", "recovery"):
                proof = response.get(key, {})
                observed = proof.get("evidence", {}).get("observation") if isinstance(proof, dict) else None
                if (proof.get("outcome") == "applied_not_restored" and isinstance(observed, dict)
                        and observed.get("target") == proof.get("target") and isinstance(observed.get("values"), dict)):
                    result["after"] = _prune(observed)
        restored = response.get("restore_observation")
        # Older receipts can retain a partial restore_observation as well as
        # their original post-adjustment `after`. A terminal reconciliation
        # that established restoration is newer evidence than either one.
        reconciliation = response.get("reconciliation")
        if (response.get("status") == "restored" and isinstance(reconciliation, dict)
                and reconciliation.get("outcome") == "restored"):
            evidence = reconciliation.get("evidence")
            terminal = evidence.get("observation") if isinstance(evidence, dict) else None
            if (isinstance(terminal, dict) and isinstance(terminal.get("target"), dict)
                    and isinstance(terminal.get("values"), dict) and terminal["values"]):
                restored = terminal
        if isinstance(restored, dict) and isinstance(restored.get("values"), dict):
            result["restore_values"] = deepcopy(restored["values"])
            result["restore_observation"] = _prune(restored)
            if response.get("status") == "restored" and "target" in restored:
                result["after"] = _prune(restored)
        actions = response.get("actions", [])
        if actions:
            result["actions"] = [_action_summary(item) for item in actions]
        return result
    result = _prune(response)
    if isinstance(response.get("pending_operations"), list):
        result["pending_operations"] = [compact_response(item, command="operation_summary")
                                        for item in response["pending_operations"]]
    return result
