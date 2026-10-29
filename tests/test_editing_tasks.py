"""Real SQLite/file lifecycle and guard tests; fixtures are not C1 render evidence."""
from copy import deepcopy
import hashlib
import uuid

from PIL import Image
import pytest

from c1ai.contracts import FIELDS, MODULES, digest
from c1ai.core import Core
from c1ai.formatting import compact_response


class TaskBackend:
    def __init__(self):
        self.target = {'instance': {'pid': 1}, 'document': {'path': '/session', 'kind': 'session'},
                       'image': {'path': '/image'}, 'variant': {'id': 'source'}, 'layer': 'Background'}
        self.values = dict.fromkeys(FIELDS, 0)
        self.layer = {'runtime_id': [1], 'native_id': 'background', 'object_id': 'background',
                      'name': 'Background', 'is_image_layer': True}
        self.states = {m: {'value': 1} for m in ('curves', 'levels', 'color_mixer', 'color_balance', 'film_grain')}
        self.states['layers'] = {'layers': [self.layer], 'selected_layer': self.layer}
        self.writes = []

    def observe(self, fields):
        return deepcopy({'target': self.target, 'values': {f: self.values[f] for f in fields}})

    def observe_module(self, module):
        return deepcopy({'target': self.target, 'module': module, 'state': self.states[module]})

    def render_revision(self, target):
        assert target == self.target
        return digest({'values': self.values, 'states': self.states})

    def context(self):
        return {'target': deepcopy(self.target), 'capabilities': self.capabilities_for(None)}

    def capabilities_for(self, observation):
        return {'fields': {f: {'writable': True} for f in FIELDS},
                'modules': {m: {'writable': True} for m in self.states},
                'images': {'create_candidate': True}}

    def config(self):
        return {'allow_preview_export': True}

    def adjust_module(self, *args):
        raise AssertionError('Restore must use the existing module paste path')

    def apply_module_action(self, module, patch, before, source):
        def apply():
            self.writes.append(module)
            if module in MODULES:
                self.values.update(source['values'])
                return self.observe(list(MODULES[module]))
            self.states[module] = deepcopy(source['state'])
            return self.observe_module(module)
        after = self.action_hook(module + '.paste', apply)
        return {'after': after, 'requested_result_verified': True}


@pytest.fixture
def core(tmp_path):
    return Core(TaskBackend(), tmp_path)


def task_call(core, action, task_id=None, payload=None, **overrides):
    observed = core.dispatch({'command': 'read', 'fields': []})
    request = {'command': 'operations', 'action': 'task', 'task_action': action,
               'request_id': uuid.uuid4().hex, 'target': observed['target'],
               'observation_token': observed['observation_token']}
    if task_id:
        request['task_id'] = task_id
    if payload is not None:
        request['payload'] = payload
    request.update(overrides)
    return core.dispatch(request)


def start(core, **fields):
    result = task_call(core, 'start', payload={'goals': ['Preserve sunset'], **fields})
    assert result['status'] == 'completed', result
    return result['result']['task']['id']


def bind(core, task_id):
    source = core._observe([])
    request = {'command': 'images', 'action': 'create_candidate', 'request_id': uuid.uuid4().hex}
    record, _ = core.journal.begin(request, source['target'], source)
    core.backend.target['variant'] = {'id': 'candidate'}
    core.journal.update(record['operation_id'], status='completed', result={'after': core._observe([])})
    result = task_call(core, 'bind', task_id, {'candidate_request_id': request['request_id']})
    assert result['status'] == 'completed', result


def render_receipt(core):
    target = deepcopy(core.backend.target)
    request = {'command': 'preview', 'kind': 'render', 'request_id': uuid.uuid4().hex}
    record, _ = core.journal.begin(request, target, core._observe([]))
    path = core.journal.state_dir / 'renders' / request['request_id'] / 'preview.jpg'
    path.parent.mkdir(parents=True)
    Image.new('RGB', (16, 16), 'gray').save(path)
    artifact = {'source': 'capture_one_export', 'render_verified': True,
                'path': str(path), 'target': target, 'revision': core.backend.render_revision(target),
                'image': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'format': 'JPEG'}}
    core.journal.update(record['operation_id'], status='completed', result=artifact)
    return request['request_id'], artifact


def capture(core, task_id):
    request_id, _ = render_receipt(core)
    result = task_call(core, 'checkpoint', task_id, {'preview_request_id': request_id})
    assert result['status'] == 'completed', result
    return result['result']['task']['checkpoints'][-1]


def review_payload(cp, improved=True):
    return {'checkpoint_id': cp['id'], 'seen_sha256': cp['artifact']['image']['sha256'],
            'improved': improved, 'assessment': {k: 'Client assessment' for k in
             ('goal', 'protections', 'light_color', 'edges', 'remaining')}}


def test_brief_persists_across_core_reconnect_and_keeps_reference_roles(core):
    task_id = start(core, mode='reference_match', references=[{'id': 'attachment', 'role': 'tone', 'kind': 'cross_scene'}])
    reopened = Core(core.backend, core.journal.state_dir)
    result = reopened.dispatch({'command': 'operations', 'action': 'task', 'task_action': 'get', 'task_id': task_id})
    assert result['task']['brief']['references'][0]['role'] == 'tone'
    assert core.backend.writes == []


@pytest.mark.parametrize('fields', [{'mode': 'reference_match'}, {'goals': []}, {'strength': '95%'},
                                   {'references': [{'id': 'a', 'role': 'tone', 'kind': 'imaginary'}]}])
def test_incomplete_or_misleading_briefs_refused(core, fields):
    assert task_call(core, 'start', payload={'goals': ['goal'], **fields})['status'] == 'refused'


def test_task_request_deduplication_and_conflict(core):
    request_id = uuid.uuid4().hex
    first = task_call(core, 'start', payload={'goals': ['goal']}, request_id=request_id)
    again = core.dispatch(first['request'])
    assert again['existing'] is True and again['replayed'] is False
    conflict = core.dispatch({**first['request'], 'payload': {'goals': ['different']}})
    assert conflict['error']['code'] == 'request_id_conflict'


def test_checkpoint_retains_preview_after_render_cache_removed(core):
    task_id = start(core)
    request_id, artifact = render_receipt(core)
    result = task_call(core, 'checkpoint', task_id, {'preview_request_id': request_id})
    from pathlib import Path
    Path(artifact['path']).unlink()
    cp = result['result']['task']['checkpoints'][-1]
    assert Path(cp['artifact']['path']).is_file()
    assert cp['mask_pixels_restorable'] is False
    compare = task_call(core, 'compare', task_id)
    assert [i['role'] for i in compare['comparison']] == ['initial', 'current']
    assert compact_response(compare, command='operations', action='task') == compare


def test_manual_edit_invalidates_preview_and_review(core):
    task_id = start(core)
    cp = capture(core, task_id)
    request_id, _ = render_receipt(core)
    core.backend.values['exposure'] = 1
    assert task_call(core, 'checkpoint', task_id, {'preview_request_id': request_id})['error']['code'] == 'preview_stale'
    assert task_call(core, 'review', task_id, review_payload(cp))['error']['code'] == 'checkpoint_stale'


def test_preview_hash_is_checked_before_retention(core):
    task_id = start(core)
    request_id, artifact = render_receipt(core)
    from pathlib import Path
    Path(artifact['path']).write_bytes(b'changed')
    result = task_call(core, 'checkpoint', task_id, {'preview_request_id': request_id})
    assert result['error']['code'] == 'invalid_preview'


def test_wrong_photo_and_wrong_clone_refused(core):
    task_id = start(core)
    core.backend.target['image'] = {'path': '/other'}
    assert task_call(core, 'update', task_id, {'goals': ['other']})['error']['code'] == 'target_changed'
    assert core.backend.writes == []


def test_feedback_keeps_approved_details_and_review_does_not_accept(core):
    task_id = start(core, protections=['clothes'])
    cp = capture(core, task_id)
    reviewed = task_call(core, 'review', task_id, review_payload(cp))
    assert 'accepted' not in reviewed['result']['task']
    result = task_call(core, 'feedback', task_id, {'message': 'Greens quieter, face is good', 'protect': ['face', 'brightness']})
    assert result['result']['task']['brief']['protections'] == ['clothes', 'face', 'brightness']
    accepted = task_call(core, 'accept', task_id, {'checkpoint_id': cp['id'], 'user_confirmation': 'Looks good'})
    assert accepted['result']['task']['accepted'] == cp['id']


def test_two_failed_rounds_need_direction_without_double_counting(core):
    task_id = start(core)
    for _ in range(2):
        cp = capture(core, task_id)
        result = task_call(core, 'review', task_id, review_payload(cp, False))
        assert result['status'] == 'completed'
    assert result['result']['task']['phase'] == 'needs_direction'
    assert task_call(core, 'review', task_id, review_payload(cp, False))['status'] == 'refused'


def test_reference_review_requires_reference_comparison(core):
    task_id = start(core, mode='reference_match', references=[{'id': 'a', 'role': 'tone', 'kind': 'same_scene'}])
    cp = capture(core, task_id)
    assert task_call(core, 'review', task_id, review_payload(cp))['error']['code'] == 'invalid_review'


def test_restore_only_into_candidate_uses_guarded_child_receipts(core):
    task_id = start(core)
    cp = capture(core, task_id)
    assert task_call(core, 'restore', task_id, {'checkpoint_id': cp['id']})['status'] == 'refused'
    # A failed preflight for restore has not written anything.
    assert core.backend.writes == []
    bind(core, task_id)
    core.backend.values['exposure'] = 1
    restored = task_call(core, 'restore', task_id, {'checkpoint_id': cp['id']})
    assert restored['status'] == 'completed', restored
    assert restored['result']['parameters_restored'] is True
    assert restored['result']['visual_restore_verified'] is False
    assert core.backend.values['exposure'] == 0
    assert core.backend.writes == ['exposure']
    child = core.journal.get(request_id=restored['result']['steps'][0]['request_id'])
    assert child['request']['patch']['action'] == 'paste'
    again = core.dispatch(restored['request'])
    assert again['existing'] and core.backend.writes == ['exposure']


def test_changed_layer_structure_refuses_restore_before_writes(core):
    task_id = start(core)
    bind(core, task_id)
    cp = capture(core, task_id)
    core.backend.states['layers']['opacity'] = 80
    result = task_call(core, 'restore', task_id, {'checkpoint_id': cp['id']})
    assert result['error']['code'] == 'structure_changed'
    assert core.backend.writes == []


def test_stop_does_not_require_capture_one_online(core, monkeypatch):
    task_id = start(core)
    monkeypatch.setattr(core.backend, 'observe', lambda *_: (_ for _ in ()).throw(RuntimeError('offline')))
    stopped = core.dispatch({'command': 'operations', 'action': 'task', 'task_action': 'stop',
                             'task_id': task_id, 'request_id': uuid.uuid4().hex})
    assert stopped['result']['task']['phase'] == 'stopped'


def test_unknown_restore_child_is_durable_and_never_replayed(core, monkeypatch):
    task_id = start(core)
    cp = capture(core, task_id)
    bind(core, task_id)
    core.backend.values['exposure'] = 1
    def uncertain(module, patch, before, source):
        def fail():
            raise RuntimeError('connection lost after possible native dispatch')
        return core.backend.action_hook(module + '.paste', fail)
    monkeypatch.setattr(core.backend, 'apply_module_action', uncertain)
    result = task_call(core, 'restore', task_id, {'checkpoint_id': cp['id']})
    assert result['status'] == 'partial'
    child = core.journal.get(request_id=result['result']['steps'][0]['request_id'])
    assert child['status'] == 'outcome_unknown'
    replay = core.dispatch(result['request'])
    assert replay['existing'] and replay['replayed'] is False
    assert len(core.journal.get(request_id=child['request_id'])['actions']) == 1
    assert task_call(core, 'checkpoint', task_id, {})['error']['code'] == 'pending_operation'


def test_diagnose_reports_missing_app_without_granting_permission(core, monkeypatch):
    monkeypatch.setattr(core.backend, 'context', lambda: (_ for _ in ()).throw(RuntimeError('offline')))
    result = core.dispatch({'command': 'operations', 'action': 'task', 'task_action': 'diagnose'})
    assert result['ready'] is False and result['authorization_changed'] is False
    assert result['checks'][0]['id'] == 'connection'


def test_mcp_keeps_six_tools_and_preserves_get_contract(core, monkeypatch):
    import asyncio
    from c1ai import mcp_server
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert set(tools) == {'context', 'images', 'read', 'adjust', 'preview', 'operations'}
    assert 'task' in tools['operations'].inputSchema['properties']['action']['enum']
    original = {'status': 'completed', 'before': {'private': 'full receipt'}}
    monkeypatch.setattr(mcp_server, 'call', lambda _: original)
    assert mcp_server.operations('get', request_id='original') == original


def test_comparison_delivers_labelled_retained_images_and_rejects_tampering(core, monkeypatch):
    from pathlib import Path
    from c1ai import mcp_server
    task_id = start(core)
    cp = capture(core, task_id)
    monkeypatch.setattr(mcp_server, 'state_directory', lambda: core.journal.state_dir)
    comparison = task_call(core, 'compare', task_id)
    delivered = mcp_server._task_comparison_content(comparison)
    assert len([c for c in delivered.content if c.type == 'image']) == 2
    assert all(d['status'] == 'available' for d in delivered.structuredContent['image_delivery'])
    Path(cp['artifact']['path']).write_bytes(b'tampered')
    rejected = mcp_server._task_comparison_content(comparison)
    assert not any(c.type == 'image' for c in rejected.content)
    assert all(d['status'] == 'unavailable' for d in rejected.structuredContent['image_delivery'])


def reference_task(core):
    return start(core, mode='reference_match', references=[{'id': 'reference-one', 'role': 'warm light', 'kind': 'same_scene'}])


def plan_payload(cp):
    return {'checkpoint_id': cp['id'], 'targets': [{'id': 'light', 'reference_id': 'reference-one',
        'observation': 'Warm highlights', 'uncertainty': 'No corresponding skin region',
        'region': 'sunlit wall', 'direction': 'Preserve warm highlight relationships',
        'protect': ['Keep natural greens'], 'priority': 1, 'limitation': 'Different light direction cannot be recreated'}]}


def p1_review(cp):
    payload = review_payload(cp)
    if cp['intent']['mode'] == 'reference_match':
        payload['assessment']['reference'] = 'Compared the corresponding warm sunlit surfaces'
    for field, items, status in [('target_results', cp['intent']['reference_targets'], 'close'),
                                 ('protection_results', cp['intent']['protections'], 'preserved'),
                                 ('feedback_results', cp['intent']['feedback'], 'resolved')]:
        payload[field] = [{'id': i['id'], 'status': status, 'note': 'Client visual judgment'} for i in items]
    return payload


def interpreted_feedback():
    return {'kind': 'background', 'message': 'Greens quieter, keep brightness', 'protect': ['brightness'],
            'interpretation': {'problem': 'Distracting greens', 'region': 'background foliage',
                               'direction': 'Reduce saturation', 'method': 'color_range', 'risks': 'Check nearby warm colors'}}


def test_reference_plan_freezes_source_roles_and_progress_in_review(core):
    task_id = reference_task(core)
    initial = capture(core, task_id)
    planned = task_call(core, 'reference_plan', task_id, plan_payload(initial))
    assert planned['status'] == 'completed'
    cp = capture(core, task_id)
    reviewed = task_call(core, 'review', task_id, p1_review(cp))
    task = reviewed['result']['task']
    assert task['reference_plans'][0]['latest_review']['results'][0]['status'] == 'close'
    assert task['reference_plans'][0]['backend_verified'] is False
    compare = task_call(core, 'compare', task_id)
    assert compare['reference_plan']['references'][0]['role'] == 'warm light'
    assert cp['intent']['reference_targets'][0]['limitation']


def test_reference_plan_refuses_unknown_source_and_stale_picture(core):
    task_id = reference_task(core)
    cp = capture(core, task_id)
    payload = plan_payload(cp)
    payload['targets'][0]['reference_id'] = 'unknown'
    assert task_call(core, 'reference_plan', task_id, payload)['error']['code'] == 'invalid_reference_plan'
    core.backend.values['exposure'] = 1
    assert task_call(core, 'reference_plan', task_id, plan_payload(cp))['error']['code'] == 'checkpoint_stale'
    assert not core.tasks._load(task_id).get('reference_plans')


def test_reference_changes_invalidate_active_plan_without_rewriting_history(core):
    task_id = reference_task(core)
    initial = capture(core, task_id)
    task_call(core, 'reference_plan', task_id, plan_payload(initial))
    cp = capture(core, task_id)
    task_call(core, 'update', task_id, {'mode': 'optimize', 'references': []})
    task = core.tasks._load(task_id)
    assert 'reference_plan_id' not in task
    assert task['checkpoints'][-1]['intent']['reference_targets'] == cp['intent']['reference_targets']
    # An already captured reference round retains its reference review contract.
    assert task_call(core, 'review', task_id, p1_review(cp))['status'] == 'completed'
    assert capture(core, task_id)['intent']['reference_targets'] == []


def test_partial_recognition_is_user_record_not_whole_image_acceptance(core):
    task_id = start(core)
    cp = capture(core, task_id)
    task_call(core, 'review', task_id, review_payload(cp))
    result = task_call(core, 'recognize', task_id, {'checkpoint_id': cp['id'], 'aspect': 'face warmth',
                                                 'user_confirmation': 'The face is good'})
    assert 'accepted' not in result['result']['task']
    recognition = result['result']['task']['recognitions'][0]
    assert recognition['pixel_lock'] is False
    next_cp = capture(core, task_id)
    protection = next_cp['intent']['protections'][0]
    assert protection['id'] == recognition['id'] and protection['checkpoint_id'] == cp['id']
    task_call(core, 'review', task_id, p1_review(next_cp))
    compared = task_call(core, 'compare', task_id, {'checkpoint_ids': [cp['id'], next_cp['id']]})
    assert len(compared['comparison']) == 2 and compared['recognitions'][0]['active']
    assert compared['comparison'][0]['restore']['full_image_verified'] is False


def test_invalid_supersession_does_not_erase_recognition(core):
    task_id = start(core)
    cp = capture(core, task_id)
    task_call(core, 'review', task_id, review_payload(cp))
    body = {'checkpoint_id': cp['id'], 'aspect': 'contrast', 'user_confirmation': 'Keep contrast'}
    task_call(core, 'recognize', task_id, body)
    rejected = task_call(core, 'recognize', task_id, {**body, 'supersedes': ['not-in-this-task']})
    assert rejected['error']['code'] == 'unknown_recognition'
    assert core.tasks._load(task_id)['recognitions'][0]['active']


def test_interpreted_feedback_links_base_and_result_checkpoint(core):
    task_id = start(core)
    initial = capture(core, task_id)
    result = task_call(core, 'feedback', task_id, interpreted_feedback())
    feedback = result['result']['task']['feedback'][-1]
    assert feedback['checkpoint_id'] == initial['id']
    cp = capture(core, task_id)
    assert cp['intent']['feedback'][0]['id'] == feedback['id']
    result = task_call(core, 'review', task_id, p1_review(cp))
    assert result['result']['task']['feedback'][-1]['latest_result']['checkpoint_id'] == cp['id']
    assert capture(core, task_id)['intent']['feedback'] == []


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'unknown'])
def test_incomplete_intent_review_is_atomic(core, failure):
    task_id = reference_task(core)
    initial = capture(core, task_id)
    task_call(core, 'reference_plan', task_id, plan_payload(initial))
    cp = capture(core, task_id)
    payload = p1_review(cp)
    if failure == 'missing':
        payload.pop('protection_results')
    elif failure == 'duplicate':
        payload['target_results'] *= 2
    else:
        payload['target_results'][0]['id'] = 'invented'
    assert task_call(core, 'review', task_id, payload)['error']['code'] == 'invalid_review'
    task = core.tasks._load(task_id)
    assert not task['checkpoints'][-1].get('review') and task['no_improvement_rounds'] == 0
    assert not task['reference_plans'][0].get('latest_review')


def test_reported_regression_stops_trials_even_if_global_improvement_true(core):
    task_id = start(core)
    capture(core, task_id)
    task_call(core, 'feedback', task_id, interpreted_feedback())
    cp = capture(core, task_id)
    payload = p1_review(cp)
    payload['protection_results'][0]['status'] = 'changed'
    result = task_call(core, 'review', task_id, payload)
    assert result['result']['task']['phase'] == 'needs_direction'
    assert result['result']['task']['checkpoints'][-1]['review']['backend_verified'] is False


def test_feedback_respects_local_scope_and_manual_changes(core):
    task_id = start(core, allowed_local=False)
    capture(core, task_id)
    payload = interpreted_feedback()
    payload['interpretation']['method'] = 'local'
    assert task_call(core, 'feedback', task_id, payload)['error']['code'] == 'local_not_allowed'
    core.backend.values['exposure'] = 1
    assert task_call(core, 'feedback', task_id, interpreted_feedback())['error']['code'] == 'checkpoint_stale'
    assert core.tasks._load(task_id)['feedback'] == []


def test_legacy_task_without_intent_is_readable_and_reviewable(core):
    task_id = start(core, protections=['skin'])
    cp = capture(core, task_id)
    task = core.tasks._load(task_id)
    task['checkpoints'][-1].pop('intent')
    core.tasks._save(task)
    reopened = Core(core.backend, core.journal.state_dir)
    assert task_call(reopened, 'review', task_id, review_payload(cp))['status'] == 'completed'
    loaded = reopened.tasks._load(task_id)
    assert not loaded.get('recognitions') and not loaded.get('reference_plans')


def analysis_payload(cp):
    return {'checkpoint_id': cp['id'], 'reference_id': 'reference-one', 'reference_checkpoint_id': cp['id'],
            'assume_srgb': {'reference': True, 'target': True},
            'anchors': [{'id': 'surface', 'role': 'same neutral surface', 'kind': 'neutral',
                         'reference_box': [0, 0, 1, 1], 'target_box': [0, 0, 1, 1]}]}


def test_anchor_analysis_is_version_bound_and_does_not_write_capture_one(core):
    task_id = reference_task(core)
    cp = capture(core, task_id)
    result = task_call(core, 'anchor_analysis', task_id, analysis_payload(cp))
    assert result['status'] == 'completed', result
    measurement = result['result']['task']['anchor_analyses'][-1]
    assert measurement['report']['mappings'][0]['reference_minus_target']['preview_patch_deltaE76'] == 0
    assert measurement['C1_parameters_written'] is False and core.backend.writes == []
    planned = task_call(core, 'reference_plan', task_id, {**plan_payload(cp), 'anchor_analysis_id': measurement['id']})
    assert planned['status'] == 'completed'
    reopened = Core(core.backend, core.journal.state_dir)
    assert reopened.tasks._load(task_id)['anchor_analysis_id'] == measurement['id']


def test_anchor_reuse_requires_alignment_and_keeps_fixed_patches(core):
    task_id = reference_task(core);cp = capture(core, task_id)
    first = task_call(core, 'anchor_analysis', task_id, analysis_payload(cp))['result']['task']['anchor_analyses'][-1]
    next_cp = capture(core, task_id)
    body = {'checkpoint_id': next_cp['id'], 'baseline_analysis_id': first['id']}
    assert task_call(core, 'anchor_analysis', task_id, body)['error']['code'] == 'anchor_alignment_required'
    result = task_call(core, 'anchor_analysis', task_id, {**body, 'alignment_confirmed': True})
    assert result['status'] == 'completed'
    second = result['result']['task']['anchor_analyses'][-1]
    assert second['anchors'] == first['anchors'] and second['reference_source'] == first['reference_source']
    assert task_call(core, 'compare', task_id)['comparison'][-1]['anchor_analyses'][-1]['id'] == second['id']


def test_stale_anchor_analysis_and_reference_role_changes_are_rejected(core):
    task_id = reference_task(core);cp = capture(core, task_id)
    first = task_call(core, 'anchor_analysis', task_id, analysis_payload(cp))['result']['task']['anchor_analyses'][-1]
    core.backend.values['exposure'] = 1
    assert task_call(core, 'anchor_analysis', task_id, analysis_payload(cp))['error']['code'] == 'checkpoint_stale'
    next_cp = capture(core, task_id)
    task_call(core, 'update', task_id, {'references': [{'id': 'reference-one', 'role': 'different role', 'kind': 'same_scene'}]})
    body = {'checkpoint_id': next_cp['id'], 'baseline_analysis_id': first['id'], 'alignment_confirmed': True}
    assert task_call(core, 'anchor_analysis', task_id, body)['error']['code'] == 'reference_changed'
    assert core.backend.writes == []
