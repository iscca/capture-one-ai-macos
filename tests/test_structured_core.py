"""Real core/journal, synthetic adapters only: no Capture One access."""
from copy import deepcopy

import pytest

from c1ai.contracts import Refused, STRUCTURED_MODULES, validate_structured_patch
from c1ai.core import Core
from c1ai.formatting import compact_response
from test_core import FakeBackend


BACKGROUND = {"runtime_id": [42, 1], "name": "Background", "is_image_layer": True}
LAYER = {"runtime_id": [42, 2], "name": "Adjustment 1", "is_image_layer": False}
PATCHES = {
    "film_grain": {"type": "fine", "impact": 25, "granularity": 50},
    "curves": {"channel": "rgb", "points": [[0, 0], [0.5, 0.6], [1, 1]]},
    "color_mixer": {"mode": "basic", "color": "red", "hue": 1},
    "color_balance": {"wheel": "shadows", "x": 2},
    "layers": {"action": "create_empty"},
    "masks": {"action": "create_subject"},
}


class StructuredBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.states = {module: {"value": 0} for module in STRUCTURED_MODULES}
        self.states["layers"] = {"layers": [deepcopy(BACKGROUND)], "selected_layer": deepcopy(BACKGROUND)}
        self.states["masks"] = deepcopy(self.states["layers"])
        self.reads = []
        self.writable = True
        self.fail_after = False
        self.mutate_on_read = None
        self.corrupt_transition = None

    def capabilities_for(self, observation):
        return {"modules": {module: {"writable": self.writable} for module in STRUCTURED_MODULES},
                "fields": self.context()["capabilities"]["fields"]}

    def observe_module(self, module):
        self.reads.append(module)
        if self.mutate_on_read:
            self.mutate_on_read(self)
        return {"module": module, "target": deepcopy(self.target), "state": deepcopy(self.states[module]),
                "evidence": {"scope": "synthetic_structure", "model_verified": False}}

    def adjust_module(self, module, patch, observation):
        previous = deepcopy(observation)

        def change():
            record = self.core.journal.list()[0]
            actions = self.core.journal.get(operation_id=record["operation_id"])["actions"]
            assert actions[-1]["status"] == "dispatching"
            assert actions[-1]["intent"]["patch"] == patch
            self.calls.append((module, deepcopy(patch)))
            if module in {"layers", "masks"}:
                state = self.states[module]
                action = patch["action"]
                if action in {"create_empty", "create_subject", "create_background"}:
                    state["layers"].append(deepcopy(LAYER))
                    state["selected_layer"] = deepcopy(LAYER)
                    self.target["layer"] = deepcopy(LAYER)
                elif action == "select":
                    state["selected_layer"] = deepcopy(patch["layer_ref"])
                    self.target["layer"] = ("Background" if patch["layer_ref"]["is_image_layer"]
                                            else deepcopy(patch["layer_ref"]))
                elif action == "remove_created":
                    state["layers"].remove(patch["layer_ref"])
                    state["selected_layer"] = deepcopy(BACKGROUND)
                    self.target["layer"] = "Background"
                else:
                    state["opacity"] = patch["opacity"]
            else:
                self.states[module] = deepcopy(patch)
            if self.fail_after:
                raise TimeoutError("lost structured native response")
            return {"accepted": True, "control_confirmed": True, "model_verified": False}

        self.action_hook("structured:" + module + ":step1", change, {"patch": patch})
        after = self.observe_module(module)
        result = {"status": "control_confirmed", "after": after, "evidence": {"native_readback": True},
                  "requested_result_verified": True}
        if module in {"layers", "masks"} and patch["action"] != "set_opacity":
            result["target_transition"] = {
                "before": previous["target"], "after": after["target"],
                "evidence": {"before_layers": previous["state"]["layers"], "after_layers": after["state"]["layers"],
                             "selected_layer": after["state"]["selected_layer"]}}
            if self.corrupt_transition:
                self.corrupt_transition(result)
        return result


@pytest.fixture
def system(tmp_path):
    backend = StructuredBackend()
    core = Core(backend, tmp_path)
    backend.core = core
    yield core, backend
    core.close()


def request(core, module="curves", patch=None, request_id="structured"):
    observed = core.dispatch({"command": "read", "module": module})
    assert observed["status"] == "ok", observed
    return {"command": "adjust", "module": module, "patch": deepcopy(patch or PATCHES[module]),
            "request_id": request_id, "target": observed["target"], "observation_token": observed["observation_token"]}


@pytest.mark.parametrize("module", sorted(STRUCTURED_MODULES))
def test_each_structure_uses_durable_actions_and_separate_model_scope(system, module):
    core, backend = system
    item = request(core, module)
    result = core.dispatch(item)
    assert result["status"] == "control_confirmed", result
    assert result["after"]["module"] == module and result["after"]["observation_token"]
    assert result["model_verified"] is False and len(result["actions"]) == 1
    assert core.dispatch(item)["existing"] is True
    assert len(backend.calls) == 1
    restored = core.dispatch({"command": "operations", "action": "restore", "operation_id": result["operation_id"]})
    assert restored["status"] == "refused" and len(backend.calls) == 1
    assert "restore_scope" not in result


@pytest.mark.parametrize("module,patch", [
    ("curves", {"channel": "rgb", "points": [[0, 0], [0, 0.5], [1, 1]]}),
    ("curves", {"channel": "rgb", "points": [[0.1, 0], [1, 1]]}),
    ("curves", {"channel": "rgb", "points": [[0, False], [1, 1]]}),
    ("curves", {"channel": "rgb", "points": [[0, 0], [1, 1]], "script": "x"}),
    ("color_mixer", {"mode": "advanced", "color": "red", "hue": 0}),
    ("color_mixer", {"action": "qualify", "mode": "basic"}),
    ("color_mixer", {"action": "qualify", "hue": 0}),
    ("color_mixer", {"action": "auto_qualify"}),
    ("color_mixer", {"mode": "basic", "color": "red"}),
    ("color_mixer", {"mode": "basic", "color": "red", "hue": 31}),
    ("color_mixer", {"mode": "basic", "color": "red", "saturation": 81}),
    ("color_mixer", {"mode": "basic", "color": "red", "saturation": -81}),
    ("color_mixer", {"mode": "basic", "color": "red", "lightness": 51}),
    ("color_mixer", {"mode": "basic", "color": "red", "lightness": -51}),
    ("color_mixer", {"mode": "basic", "color": "all", "lightness": 0}),
    ("color_balance", {"wheel": "master", "lightness": 1}),
    ("color_balance", {"wheel": "shadows", "x": True}),
    ("layers", {"action": "create_empty", "layer_ref": LAYER}),
    ("layers", {"action": "select", "layer_ref": {"name": "Adjustment 1"}}),
    ("layers", {"action": "remove_created", "layer_ref": LAYER}),
    ("layers", {"action": "set_opacity", "layer_ref": LAYER, "opacity": 101}),
    ("masks", {"action": "delete"}),
    ("masks", {"action": "create_subject", "points": [[1, 1]]}),
])
def test_bad_structure_refused_before_native_observation(system, module, patch):
    core, backend = system
    result = core.dispatch({"command": "adjust", "module": module, "patch": patch, "request_id": "bad"})
    assert result["status"] == "refused"
    assert not backend.calls and not backend.reads and not core.journal.list()


def test_token_covers_module_and_complete_state_and_mixed_requests_refused(system):
    core, backend = system
    one = core.dispatch({"command": "read", "module": "curves"})
    two = core.dispatch({"command": "read", "module": "color_balance"})
    assert one["observation_token"] != two["observation_token"]
    for token in (two["observation_token"], core.dispatch({"command": "read", "fields": []})["observation_token"]):
        result = core.dispatch({**request(core), "observation_token": token})
        assert result["error"]["code"] == "wrong_observation"
    result = core.dispatch({"command": "read", "module": "curves", "fields": []})
    assert result["status"] == "refused"
    item = request(core)
    backend.states["curves"]["another_channel"] = 9
    assert core.dispatch(item)["error"]["code"] == "stale_observation"
    assert not backend.calls


def test_basic_mixer_uses_native_bounded_ranges_and_explicit_public_color_names():
    for color in ("purple", "magenta"):
        for sign in (-1, 1):
            patch = {"mode": "basic", "color": color, "hue": sign * 30,
                     "saturation": sign * 80, "lightness": sign * 50}
            assert validate_structured_patch({"module": "color_mixer", "patch": patch}) == patch
    assert validate_structured_patch({"module": "color_mixer", "patch": {
        "mode": "basic", "color": "all", "hue": 1, "saturation": 1}})
    with pytest.raises(Refused):
        validate_structured_patch({"module": "color_mixer", "patch": {
            "mode": "basic", "color": "pink", "hue": 1}})


def test_structural_write_requires_qualification_and_candidate_identity(system):
    core, backend = system
    item = request(core)
    backend.writable = False
    assert core.dispatch(item)["status"] == "refused"
    backend.writable = True
    backend.target["variant"]["id"] = 999
    assert core.dispatch(item)["error"]["code"] == "target_changed"
    assert not backend.calls


def test_structural_timeout_persists_across_core_restart_and_blocks_other_writes(system, tmp_path):
    core, backend = system
    item = request(core, "layers")
    backend.fail_after = True
    first = core.dispatch(item)
    assert first["status"] == "outcome_unknown" and len(first["actions"]) == 1
    newer = Core(backend, tmp_path)
    try:
        existing = newer.dispatch(item)
        assert existing["existing"] is True and len(backend.calls) == 1
        other = request(newer, "curves", request_id="other")
        assert newer.dispatch(other)["error"]["code"] == "pending_operation"
        assert newer.peek({"command": "operations", "request_id": "structured"})["status"] == "outcome_unknown"
    finally:
        newer.close()


@pytest.mark.parametrize("corrupt", [
    lambda result: result.pop("target_transition"),
    lambda result: result["target_transition"]["evidence"]["before_layers"].append(deepcopy(LAYER)),
    lambda result: result["target_transition"]["evidence"].update(selected_layer=deepcopy(BACKGROUND)),
    lambda result: result["after"]["target"]["variant"].update(id=999),
])
def test_layer_transition_never_relaxes_other_target_or_inventory_checks(system, corrupt):
    core, backend = system
    backend.corrupt_transition = corrupt
    result = core.dispatch(request(core, "layers"))
    assert result["status"] == "partial", result
    assert len(result["actions"]) == 1 and len(backend.calls) == 1


def test_layer_remove_requires_actual_owned_creation_and_current_reference(system):
    core, backend = system
    created = core.dispatch(request(core, "layers"))
    patch = {"action": "remove_created", "layer_ref": deepcopy(LAYER), "creation_operation_id": "missing"}
    assert core.dispatch(request(core, "layers", patch, "remove-missing"))["status"] == "refused"
    patch["creation_operation_id"] = created["operation_id"]
    result = core.dispatch(request(core, "layers", patch, "remove-owned"))
    assert result["status"] == "control_confirmed", result
    assert result["after"]["target"]["layer"] == "Background"
    assert len(backend.calls) == 2


def test_structured_after_is_reobserved_and_changes_remain_pending(system):
    core, backend = system
    original = backend.adjust_module
    def change_after(module, patch, observed):
        result = original(module, patch, observed)
        backend.states[module]["external"] = 1
        return result
    backend.adjust_module = change_after
    result = core.dispatch(request(core))
    assert result["status"] == "partial" and result["error"]["code"] == "stale_observation"


def test_structured_reconcile_cannot_use_numeric_empty_or_baseline_from_another_target(system):
    core, backend = system
    backend.fail_after = True
    failed = core.dispatch(request(core))
    base = {"outcome": "not_applied", "target": failed["target"], "evidence": {"observation": backend.observe([])}}
    backend.reconcile = lambda record: deepcopy(base)
    queried = {"command": "operations", "action": "reconcile", "operation_id": failed["operation_id"]}
    assert core.dispatch(queried)["status"] == "refused"
    base["evidence"]["observation"] = deepcopy(failed["before"])
    base["target"] = deepcopy(base["target"])
    base["target"]["variant"]["id"] = 900
    assert core.dispatch(queried)["status"] == "refused"
    base.update(outcome="applied_not_restored", target=backend.target)
    base["evidence"]["observation"] = backend.observe_module("curves")
    assert core.dispatch(queried)["status"] == "applied_not_restored"
    assert len(backend.calls) == 1


def test_preparation_and_viewer_combinations_are_bounded(system):
    core, backend = system
    observed = core.dispatch({"command": "read", "fields": []})
    base = {"request_id": "prepare", "target": observed["target"], "observation_token": observed["observation_token"]}
    assert core.dispatch({**base, "command": "images", "action": "prepare_tool", "tool": "arbitrary"})["status"] == "refused"
    assert core.dispatch({**base, "command": "images", "action": "prepare_tool", "tool": "curves"})["status"] == "completed"
    for kind, action in (("viewer", "render"), ("render", "capture"), ("viewer", "prepare"), ("raw", "capture")):
        assert core.dispatch({**base, "request_id": kind + action, "command": "preview", "kind": kind,
                              "action": action})["status"] == "refused"
    viewer = core.dispatch({**base, "request_id": "viewer", "command": "preview", "kind": "viewer", "action": "capture"})
    assert viewer["status"] == "completed"


def test_compact_structure_retains_actionable_layer_ref_without_locator_leak(system):
    core, backend = system
    created = core.dispatch(request(core, "layers"))
    observed = core.dispatch({"command": "read", "module": "layers"})
    observed["raw_state"] = {"nodes": [1]}
    compact = compact_response(observed, command="read")
    assert compact["state"]["selected_layer"] == LAYER
    assert compact["values"]["layers"]["selected_layer"] == LAYER
    assert "raw_state" not in compact
    compact = compact_response(created, command="adjust")
    assert compact["module"] == "layers"
    assert compact["result"]["target_transition"] == created["result"]["target_transition"]


def test_layer_reference_does_not_implicitly_select_before_opacity(system):
    core, backend = system
    backend.states["layers"]["layers"].append(deepcopy(LAYER))
    result = core.dispatch(request(core, "layers", {"action": "set_opacity", "layer_ref": LAYER, "opacity": 50}))
    assert result["error"]["code"] == "layer_not_selected" and not backend.calls


def test_mixed_layer_snapshot_is_rejected_before_token_or_write(system):
    core, backend = system
    backend.states["layers"]["layers"].append(deepcopy(LAYER))
    backend.states["layers"]["selected_layer"] = deepcopy(LAYER)
    observed = core.dispatch({"command": "read", "module": "layers"})
    assert observed["status"] == "refused" and "observation_token" not in observed
    assert not backend.calls


def test_structured_recovery_unknown_step_is_never_replayed(system):
    core, backend = system
    backend.fail_after = True
    failed = core.dispatch(request(core))
    attempted = []

    def recover(record):
        def confirm():
            attempted.append("confirm")
            raise TimeoutError("lost fixed recovery result")
        backend.action_hook("curves.fixed_confirmation", confirm, {"module": "curves", "stage": "confirmation"})

    backend.recover = recover
    action = {"command": "operations", "action": "recover", "operation_id": failed["operation_id"]}
    first = core.dispatch(action)
    assert first["status"] == "outcome_unknown" and len(first["actions"]) == 2
    again = core.dispatch(action)
    assert again["status"] == "outcome_unknown" and len(again["actions"]) == 2
    assert again["error"]["code"] == "recovery_action_unresolved" and attempted == ["confirm"]


def test_layer_creation_reconciled_by_unique_delta_remains_owned(system):
    core, backend = system
    backend.fail_after = True
    failed = core.dispatch(request(core, "layers"))
    backend.fail_after = False
    after = backend.observe_module("layers")
    transition = {"before": failed["target"], "after": after["target"], "evidence": {
        "before_layers": failed["before"]["state"]["layers"], "after_layers": after["state"]["layers"],
        "selected_layer": after["state"]["selected_layer"]}}
    backend.reconcile = lambda record: {"outcome": "applied_not_restored", "target": after["target"],
        "evidence": {"observation": after, "scope": "unique_native_layer_delta"}, "target_transition": transition}
    reconciled = core.dispatch({"command": "operations", "action": "reconcile", "operation_id": failed["operation_id"]})
    assert reconciled["status"] == "applied_not_restored"
    assert reconciled["after"]["target"]["layer"] == LAYER and len(backend.calls) == 1
    removed = core.dispatch(request(core, "layers", {"action": "remove_created", "layer_ref": LAYER,
                                                     "creation_operation_id": failed["operation_id"]}, "remove"))
    assert removed["status"] == "control_confirmed", removed
    assert len(backend.calls) == 2


def test_structural_unknown_action_cannot_be_upgraded_by_adapter_success(system):
    core, backend = system
    def unsafe(module, patch, observation):
        try:
            backend.action_hook("curve.point.1", lambda: (_ for _ in ()).throw(TimeoutError("unknown")))
        except TimeoutError:
            pass
        return {"status": "completed", "after": backend.observe_module(module), "evidence": {"claimed": True}}
    backend.adjust_module = unsafe
    result = core.dispatch(request(core))
    assert result["status"] == "outcome_unknown" and result["error"]["code"] == "invalid_backend_result"


def test_unjournaled_structural_change_never_becomes_safe_refusal(system):
    core, backend = system
    def unsafe(module, patch, observation):
        backend.states[module] = deepcopy(patch)
        return {"status": "completed", "after": backend.observe_module(module), "evidence": {"claimed": True},
                "requested_result_verified": True}
    backend.adjust_module = unsafe
    result = core.dispatch(request(core))
    assert result["status"] == "outcome_unknown" and result["error"]["code"] == "missing_actions"
    assert core.journal.pending(result["target"])


def test_structure_noop_without_requested_result_proof_is_not_success(system):
    core, backend = system
    backend.adjust_module = lambda module, patch, observed: {
        "status": "completed", "after": backend.observe_module(module), "evidence": {"read_succeeded": True}}
    result = core.dispatch(request(core))
    assert result["status"] == "refused" and result["error"]["code"] == "requested_result_unverified"


@pytest.mark.parametrize("receipt,expected", [
    ({"status": "outcome_unknown", "dispatched": True}, "outcome_unknown"),
    ({"status": "dispatching"}, "outcome_unknown"),
    ({"status": "refused"}, "outcome_unknown"),
    ({"status": "not_dispatched", "dispatched": False}, "refused"),
    ({"status": "refused", "dispatched": False}, "refused"),
])
def test_explicit_transport_failure_cannot_turn_into_completed_action(system, receipt, expected):
    core, backend = system
    def adapter(module, patch, observation):
        backend.action_hook("transport.delta.1", lambda: deepcopy(receipt))
        raise AssertionError("An unresolved transport result must stop the adapter")
    backend.adjust_module = adapter
    result = core.dispatch(request(core))
    assert result["status"] == expected
    assert result["actions"][0]["status"] == ("not_dispatched" if expected == "refused" else expected)


def test_transport_context_uses_real_persisted_action_id_and_cannot_be_mutated(system):
    core, backend = system
    captured = []
    original = backend.adjust_module
    def adapter(module, patch, observed):
        def transport():
            context = backend.action_context
            record = core.journal.get(operation_id=context["operation_id"])
            assert record["actions"][-1]["action_id"] == context["action_id"]
            assert record["actions"][-1]["status"] == "dispatching"
            with pytest.raises(TypeError):
                context["action_id"] = "different"
            captured.append(dict(context))
            return {"accepted": True}
        backend.action_hook("transport.preparation", transport)
        return original(module, patch, observed)
    backend.adjust_module = adapter
    result = core.dispatch(request(core))
    assert result["status"] == "control_confirmed" and len(captured) == 1
    assert captured[0]["operation_id"] == result["operation_id"]
    assert backend.action_context is None


def test_fixed_restart_has_no_arbitrary_process_options_and_cannot_bypass_pending(system):
    core, backend = system
    observed = core.dispatch({"command": "read", "fields": []})
    base = {"command": "images", "action": "restart_for_native_transport", "request_id": "restart",
            "target": observed["target"], "observation_token": observed["observation_token"]}
    for options in ({"exe": "other.exe"}, {"session_path": "other"}, {"force": True}, {"kill": True}):
        assert core.dispatch({**base, **options})["error"]["code"] == "invalid_request"
    assert not backend.calls
    backend.fail_after = True
    core.dispatch(request(core))
    assert core.dispatch(base)["error"]["code"] == "pending_operation"


def test_fixed_restart_requires_background_and_deduplicates_original_request(system):
    core, backend = system
    backend.target["layer"] = deepcopy(LAYER)
    observed = core.dispatch({"command": "read", "fields": []})
    base = {"command": "images", "action": "restart_for_native_transport", "request_id": "restart",
            "target": observed["target"], "observation_token": observed["observation_token"]}
    assert core.dispatch(base)["error"]["code"] == "restart_target_unverified"
    backend.target["layer"] = "Background"
    observed = core.dispatch({"command": "read", "fields": []})
    base.update(target=observed["target"], observation_token=observed["observation_token"])
    first = core.dispatch(base)
    assert first["status"] == "completed"
    assert core.dispatch(base)["existing"] is True and len(backend.calls) == 1


@pytest.mark.parametrize("action", ["reconcile", "recover"])
def test_confirmed_restart_exposes_new_instance_and_separate_transport_readiness(system, action):
    core, backend = system
    def late(action, request, observation):
        backend.action_hook("restart.launch", lambda: {"accepted": True})
        raise TimeoutError("startup selection is not ready")
    backend.image_action = late
    observed = core.dispatch({"command": "read", "fields": []})
    item = {"command": "images", "action": "restart_for_native_transport", "request_id": "restart",
            "target": observed["target"], "observation_token": observed["observation_token"]}
    pending = core.dispatch(item)
    assert pending["status"] == "partial"
    backend.target["instance"].update(pid=124, started_at="second")
    def settled(record):
        return {"outcome": "applied_not_restored", "target": deepcopy(backend.target), "evidence": {
            "scope": "application_restart_completed", "observation": backend.observe([]),
            "native_transport_ready": False, "saved_source_unchanged": True}}
    backend.reconcile = backend.recover = settled
    result = core.dispatch({"command": "operations", "action": action, "operation_id": pending["operation_id"]})
    assert result["after"]["target"]["instance"]["pid"] == 124
    compact = compact_response(result, command="operations", action=action)
    assert compact["after"]["target"]["instance"]["pid"] == 124
    proof = compact["reconciliation" if action == "reconcile" else "recovery"]
    assert proof["evidence"]["native_transport_ready"] is False
    assert result["target"]["instance"]["pid"] == 123  # Immutable original operation identity.


def test_mcp_schema_accepts_structure_and_exposes_only_named_new_routes():
    import asyncio
    from c1ai.mcp_server import mcp
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert len(tools) == 6
    assert tools["adjust"].inputSchema["properties"]["patch"]["additionalProperties"] is True
    assert "prepare_tool" in tools["images"].inputSchema["properties"]["action"]["enum"]
    assert tools["preview"].inputSchema["properties"]["kind"]["enum"] == ["render", "viewer"]


def pending_calibration(core, backend):
    def adapter(module, patch, observed):
        backend.action_hook("mixer.qualify.0.probe", lambda: {"accepted": True})
        backend.action_hook("mixer.qualify.0.restore", lambda: {"accepted": True})
        raise RuntimeError("mapping incomplete after one safely restored probe")
    backend.adjust_module = adapter
    record = core.dispatch(request(core, "color_mixer"))
    assert record["status"] == "partial"
    evidence = {"scope": "mixer_qualification_probe_baseline", "observation": backend.observe_module("color_mixer"),
                "whole_matrix_baseline_verified": True, "baseline_ui_verified": True,
                "probes": [{"probe_action_id": record["actions"][0]["action_id"],
                            "restore_action_id": record["actions"][1]["action_id"], "baseline_verified": True}]}
    proof = {"outcome": "restored", "target": deepcopy(backend.target), "evidence": evidence}
    return record, proof


def test_only_qualified_probe_baseline_can_have_structured_restored_scope(system):
    core, backend = system
    record, proof = pending_calibration(core, backend)
    backend.reconcile = lambda record: deepcopy(proof)
    result = core.dispatch({"command": "operations", "action": "reconcile", "operation_id": record["operation_id"]})
    assert result["status"] == "restored" and result["restore_scope"] == "qualification_probe_baseline"
    assert result["requested_result_verified"] is False and result["full_model_restore_verified"] is False
    assert result["pixel_restore_verified"] is None
    assert result["restore_observation"]["state"] == record["before"]["state"]
    assert core.dispatch({"command": "operations", "action": "recover", "operation_id": record["operation_id"]})["existing"] is True


@pytest.mark.parametrize("change", [
    lambda evidence: evidence.update(scope="whole_model_restored"),
    lambda evidence: evidence.update(whole_matrix_baseline_verified=False),
    lambda evidence: evidence.update(baseline_ui_verified=False),
    lambda evidence: evidence.update(probes=[]),
    lambda evidence: evidence["probes"][0].update(restore_action_id="unrelated"),
    lambda evidence: evidence["probes"][0].update(baseline_verified=False),
    lambda evidence: evidence["observation"]["state"].update(value=9),
])
def test_probe_restoration_requires_complete_linked_baseline_evidence(system, change):
    core, backend = system
    record, proof = pending_calibration(core, backend)
    change(proof["evidence"])
    backend.reconcile = lambda record: deepcopy(proof)
    result = core.dispatch({"command": "operations", "action": "reconcile", "operation_id": record["operation_id"]})
    assert result["status"] == "refused"
    assert core.journal.get(operation_id=record["operation_id"])["status"] == "partial"


def test_probe_scope_cannot_erase_formal_apply_or_unknown_restore(system):
    core, backend = system
    record, proof = pending_calibration(core, backend)
    backend.reconcile = lambda record: deepcopy(proof)
    action = core.journal.start_action(record["operation_id"], "mixer.apply.red.hue", {})
    core.journal.finish_action(action, status="outcome_unknown")
    result = core.dispatch({"command": "operations", "action": "reconcile", "operation_id": record["operation_id"]})
    assert result["status"] == "refused"


def test_probe_recovery_validates_new_restore_against_fresh_authoritative_journal(system):
    core, backend = system
    def interrupted(module, patch, observed):
        backend.action_hook("mixer.qualify.0.probe", lambda: (_ for _ in ()).throw(TimeoutError("lost probe receipt")))
    backend.adjust_module = interrupted
    record = core.dispatch(request(core, "color_mixer"))
    assert record["status"] == "outcome_unknown"
    recovered = []
    def recover(record):
        def restore():
            recovered.append(backend.action_context["action_id"])
            return {"accepted": True, "baseline_verified": True}
        backend.action_hook("recover.mixer.qualify.0.restore", restore, {"probe_action_id": record["actions"][0]["action_id"]})
        return {"outcome": "restored", "target": deepcopy(backend.target), "evidence": {
            "scope": "mixer_qualification_probe_baseline", "observation": backend.observe_module("color_mixer"),
            "whole_matrix_baseline_verified": True, "baseline_ui_verified": True, "probes": [{
                "probe_action_id": record["actions"][0]["action_id"], "restore_action_id": recovered[-1], "baseline_verified": True}]}}
    backend.recover = recover
    action = {"command": "operations", "action": "recover", "operation_id": record["operation_id"]}
    result = core.dispatch(action)
    assert result["status"] == "restored", result
    assert result["restore_scope"] == "qualification_probe_baseline"
    assert len(result["actions"]) == 2 and len(recovered) == 1
    assert core.dispatch(action)["existing"] is True and len(recovered) == 1
