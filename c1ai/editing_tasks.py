"""Local photographic briefs and render-backed checkpoints, over the existing core.

Visual reviews are client declarations. Checkpoints preserve selected-layer color
parameters, never mask pixels or an entire Capture One undo history.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import os
import time
import uuid

from .contracts import MODULES, Refused, canonical, digest

COLOR_MODULES = (*MODULES, 'curves', 'levels', 'color_mixer', 'color_balance', 'film_grain')
READ_ACTIONS = {'get', 'compare', 'diagnose'}
WRITE_ACTIONS = {'start', 'bind', 'update', 'checkpoint', 'review', 'feedback', 'accept', 'restore', 'stop',
                 'reference_plan', 'recognize', 'anchor_analysis'}
BRIEF_KEYS = {'mode', 'subject', 'goals', 'protections', 'strength', 'usage', 'references',
              'reference_analysis', 'acceptance', 'allowed_local', 'starting_point'}


def text(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > 4000:
        raise Refused('invalid_task', f'{name} must be a nonempty string of at most 4000 characters')
    return value


def texts(value, name):
    if not isinstance(value, list) or len(value) > 32:
        raise Refused('invalid_task', f'{name} must contain at most 32 strings')
    return list(dict.fromkeys(text(v, name) for v in value))


def item_results(value, expected, statuses, name):
    """Require exactly one declared result for every captured intent, before changing state."""
    if not isinstance(value, list) or len(value) != len(expected):
        raise Refused('invalid_review', f'{name} must cover every captured intent exactly once')
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'id', 'status', 'note'}:
            raise Refused('invalid_review', f'{name} items need id, status, note')
        identifier = text(item['id'], name + ' id')
        if identifier not in expected or identifier in seen or not isinstance(item['status'], str) or item['status'] not in statuses:
            raise Refused('invalid_review', f'{name} contains an unknown/duplicate id or unsupported status')
        seen.add(identifier)
        text(item['note'], name + ' note')
    return deepcopy(value)


def brief(value, previous=None):
    if not isinstance(value, dict) or value.keys() - BRIEF_KEYS:
        raise Refused('invalid_task', 'Use documented photographic brief fields')
    result = {**(previous or {'mode': 'optimize', 'strength': 'light', 'starting_point': 'current',
                            'protections': [], 'references': [], 'allowed_local': True}), **value}
    for name, options in {'mode': {'optimize', 'reference_match'},
                          'strength': {'light', 'close', 'maximum'}, 'starting_point': {'current'}}.items():
        if result[name] not in options:
            raise Refused('invalid_task', f'Unsupported {name}')
    for name in ('goals', 'protections', 'acceptance'):
        if name in result:
            result[name] = texts(result[name], name)
    if not result.get('goals'):
        raise Refused('invalid_task', 'Describe at least one visual goal')
    for name in ('subject', 'usage'):
        if name in result:
            text(result[name], name)
    if type(result['allowed_local']) is not bool:
        raise Refused('invalid_task', 'allowed_local must be boolean')
    refs = result['references']
    if not isinstance(refs, list) or len(refs) > 8:
        raise Refused('invalid_task', 'references must be a list of at most eight client references')
    identifiers = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'id', 'role', 'kind'}:
            raise Refused('invalid_task', 'Each reference requires id, role, kind')
        identifiers.append(text(ref['id'], 'reference id'))
        text(ref['role'], 'reference role')
        if ref['kind'] not in {'same_scene', 'cross_scene'}:
            raise Refused('invalid_task', 'Reference kind must be same_scene or cross_scene')
    if len(set(identifiers)) != len(identifiers):
        raise Refused('invalid_task', 'Reference identifiers must be unique')
    if result['mode'] == 'reference_match' and not refs:
        raise Refused('invalid_task', 'Reference matching needs a reference and its intended role')
    if 'reference_analysis' in result:
        analysis = result['reference_analysis']
        if (not isinstance(analysis, dict) or not analysis
                or analysis.keys() - {'tone', 'color', 'texture', 'limitations', 'priority'}):
            raise Refused('invalid_task', 'Analyze tone, color, texture, limitations and priority as text')
        for key, value in analysis.items():
            text(value, key)
    return deepcopy(result)


class EditingTasks:
    def __init__(self, core):
        self.core, self.journal = core, core.journal
        self.db = self.journal.db
        self.db.execute('CREATE TABLE IF NOT EXISTS editing_tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()

    def _load(self, task_id):
        import json
        row = self.db.execute('SELECT payload FROM editing_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise Refused('task_not_found', 'Read a known task_id')
        return json.loads(row[0])

    def _save(self, task):
        task['updated_at'] = time.time()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO editing_tasks VALUES (?, ?)', (task['id'], canonical(task)))

    def summary(self, context):
        target = context.get('target')
        import json
        tasks = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM editing_tasks ORDER BY rowid DESC')]
        relevant = [t for t in tasks if target and all(t['source_target'].get(k) == target.get(k)
                    for k in ('instance', 'document', 'image'))]
        return {'available': callable(getattr(self.core.backend, 'render_revision', None)),
                'entry': 'operations(action=task)',
                'features': ['reference_plan', 'feedback_interpretation', 'partial_recognition', 'intent_review', 'anchor_analysis'],
                'tasks': [{'id': t['id'], 'phase': t['phase'], 'current': t.get('current'),
                           'accepted': t.get('accepted'), 'candidate_target': t.get('candidate_target'),
                           'brief': t['brief']} for t in relevant[:10]],
                'restore_scope': 'selected_layer_color_parameters', 'mask_pixels_restorable': False}

    def diagnose(self):
        try:
            context = self.core.backend.context()
        except Exception as error:
            return {'status': 'ok', 'ready': False,
                    'checks': [{'id': 'connection', 'ready': False,
                                'message': '请打开 Capture One，并在 Session 中选择一张照片'}],
                    'error': self.core._error(error), 'authorization_changed': False,
                    'client_image_support': 'client_must_confirm'}
        target = context.get('target')
        capabilities = context.get('capabilities', {})
        config = self.core.backend.config() if callable(getattr(self.core.backend, 'config', None)) else {}
        checks = [
            {'id': 'connection', 'ready': True, 'message': 'Capture One 已连接'},
            {'id': 'selection', 'ready': bool(target), 'message': '请选择 Session 中的一张照片' if not target else '已识别当前照片'},
            {'id': 'session', 'ready': bool(target and target.get('document', {}).get('kind') == 'session'),
             'message': '当前首版仅支持 Session'},
            {'id': 'write_permission', 'ready': capabilities.get('images', {}).get('create_candidate') is True,
             'message': '允许在候选副本调整；未启用时请运行 Session 配置'},
            {'id': 'preview_permission', 'ready': config.get('allow_preview_export') is True,
             'message': '实际预览需要已明确允许的临时低分辨率 JPEG 导出'},
        ]
        pending = self.journal.pending(target)
        checks.append({'id': 'pending', 'ready': not pending, 'message': '先核对未完成的原操作' if pending else '没有待核对操作'})
        return {'status': 'ok', 'checks': checks, 'ready': all(c['ready'] for c in checks),
                'client_image_support': 'client_must_confirm', 'pending_operations': pending,
                'privacy': '本机执行；云端视觉客户端可能把预览发送至模型服务商。',
                'authorization_changed': False}

    def dispatch(self, request):
        action = request.get('task_action', 'get')
        if action not in READ_ACTIONS | WRITE_ACTIONS:
            raise Refused('invalid_task_action', 'Use a documented task_action')
        if action == 'diagnose':
            return self.diagnose()
        if action in READ_ACTIONS:
            task = self._load(request.get('task_id'))
            return self._compare(task, request.get('payload', {})) if action == 'compare' else {'status': 'ok', 'task': task}
        if not isinstance(request.get('request_id'), str) or not request['request_id'].strip():
            raise Refused('missing_request_id', 'Task mutations require a unique request_id')
        existing = self.journal.existing(request)
        if existing:
            return dict(existing, existing=True, replayed=False)
        if not callable(getattr(self.core.backend, 'render_revision', None)):
            raise Refused('unsupported', 'Render-backed editing tasks currently require the Mac backend')
        payload = request.get('payload', {})
        if not isinstance(payload, dict):
            raise Refused('invalid_task', 'payload must be an object')
        # Stop is local metadata and remains available after selection changes or C1 exits.
        if action == 'stop':
            task = self._load(request.get('task_id'))
            observed = {'target': task.get('candidate_target') or task['source_target'], 'values': {}}
        else:
            observed = self.core._preflight(request)
            if self.journal.pending(observed['target']):
                raise Refused('pending_operation', 'Query/reconcile the original unfinished request before continuing')
            task = None if action == 'start' else self._load(request.get('task_id'))
            if task and task['phase'] == 'stopped':
                raise Refused('task_stopped', 'Start a new task after stopping')
            if task and action != 'bind':
                expected = task.get('candidate_target') or task['source_target']
                self.core._target({k: v for k, v in expected.items() if k != 'layer'},
                                  {k: v for k, v in observed['target'].items() if k != 'layer'})
        # Validate the brief before beginning a durable mutation receipt.
        if action in {'start', 'update'}:
            parsed_brief = brief(payload, task['brief'] if task else None)
        record, _ = self.journal.begin(request, observed['target'], observed)
        try:
            if action == 'start':
                task = {'id': uuid.uuid4().hex, 'phase': 'editing', 'source_target': observed['target'],
                        'brief': parsed_brief, 'checkpoints': [], 'feedback': [], 'no_improvement_rounds': 0}
            elif action == 'bind':
                if task.get('candidate_target'):
                    raise Refused('candidate_already_bound', 'Reuse this task candidate; start another task for a new direction')
                clone = self.journal.get(request_id=payload.get('candidate_request_id'))
                after = clone.get('result', {}).get('after', {}).get('target')
                if (clone['status'] != 'completed' or clone['request'].get('command') != 'images'
                        or clone['request'].get('action') != 'create_candidate'
                        or clone['target'] != task['source_target'] or after != observed['target']):
                    raise Refused('wrong_candidate', 'Bind the completed candidate created from this task starting target')
                task['candidate_target'] = after
            elif action == 'update':
                task['brief'] = parsed_brief
                if 'references' in payload or 'mode' in payload:
                    task.pop('reference_plan_id', None)
                    task.pop('anchor_analysis_id', None)
            elif action == 'anchor_analysis':
                self._anchor_analysis(task, payload, observed['target'])
            elif action == 'reference_plan':
                self._reference_plan(task, payload, observed['target'])
            elif action == 'recognize':
                self._recognize(task, payload)
            elif action == 'checkpoint':
                self._checkpoint(task, payload, observed)
            elif action == 'review':
                self._review(task, payload, observed['target'])
            elif action == 'feedback':
                self._feedback(task, payload, observed['target'])
            elif action == 'accept':
                cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
                if cp['id'] != task.get('current') or not cp.get('review'):
                    raise Refused('review_required', 'Visually review the current checkpoint before accepting it')
                self._unchanged(cp, observed['target'])
                cp['user_confirmation'] = text(payload.get('user_confirmation'), 'user confirmation')
                task.update(accepted=cp['id'], phase='accepted')
            elif action == 'restore':
                result = self._restore(task, payload, observed, record)
                self._save(task)
                return self.journal.update(record['operation_id'], **result)
            elif action == 'stop':
                task['phase'] = 'stopped'
            self._save(task)
            return self.journal.update(record['operation_id'], status='completed',
                                       result={'task': task, 'visual_assessment_by': 'client',
                                               'backend_aesthetic_verification': False})
        except Exception as error:
            if action == 'restore':
                # A parent interrupted after a child dispatch must never erase
                # the possibility of applied writes or be replayed automatically.
                steps = self.journal.get(operation_id=record['operation_id']).get('result', {}).get('steps', [])
                return self.journal.update(record['operation_id'], status='outcome_unknown' if steps else 'refused',
                                           error=self.core._error(error), replay_allowed=False)
            self.journal.update(record['operation_id'], status='refused', error=self.core._error(error))
            raise

    @staticmethod
    def _checkpoint_by_id(task, checkpoint_id):
        for checkpoint in task['checkpoints']:
            if checkpoint['id'] == checkpoint_id:
                return checkpoint
        raise Refused('checkpoint_not_found', 'Use a checkpoint belonging to this task')

    def _unchanged(self, checkpoint, target):
        self.core._target(checkpoint['target'], target)
        if self.core.backend.render_revision(target) != checkpoint['revision']:
            raise Refused('checkpoint_stale', 'Photo changed; generate a fresh preview and checkpoint before review/accept')

    def _checkpoint(self, task, payload, observed):
        target = observed['target']
        label = text(payload.get('label', '当前版本'), 'checkpoint label')
        preview = self.journal.get(request_id=payload.get('preview_request_id'))
        artifact = preview.get('result', {})
        if (preview['status'] != 'completed' or preview['request'].get('command') != 'preview'
                or artifact.get('source') != 'capture_one_export' or artifact.get('render_verified') is not True
                or artifact.get('target') != target or not artifact.get('revision')):
            raise Refused('preview_required', 'Use a completed actual C1 render for this exact target')
        revision = self.core.backend.render_revision(target)
        if revision != artifact['revision']:
            raise Refused('preview_stale', 'Current settings differ from the preview; render again')
        source = Path(artifact['path'])
        archive = (self.journal.state_dir / 'renders').resolve()
        if source.is_symlink() or not source.resolve().is_relative_to(archive) or source.stat().st_size > 128 * 1024 * 1024:
            raise Refused('invalid_preview', 'Checkpoint source must be in the private C1 render archive')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact['image']['sha256']:
            raise Refused('invalid_preview', 'Preview file no longer matches its receipt')
        snapshots = {}
        for module in COLOR_MODULES:
            if module == 'film_grain' and target['layer'] != 'Background':
                continue
            obs = self.core._observe(list(MODULES[module])) if module in MODULES else self.core._observe_module(module)
            self.core._target(target, obs['target'])
            snapshots[module] = obs['observation_token']
        layers = self.core._observe_module('layers')
        self.core._target(target, layers['target'])
        if self.core.backend.render_revision(target) != revision:
            raise Refused('checkpoint_changed', 'Photo changed while saving the checkpoint')
        checkpoint_id = uuid.uuid4().hex
        directory = self.journal.state_dir / 'task-previews'
        directory.mkdir(mode=0o700, exist_ok=True)
        destination = directory / (checkpoint_id + '.jpg')
        with destination.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o600)
        cp = {'id': checkpoint_id, 'label': label, 'created_at': time.time(), 'target': target,
              'revision': revision, 'snapshots': snapshots, 'layers': layers['state'],
              'artifact': {**artifact, 'path': str(destination)},
              'preview_request_id': preview['request_id'], 'restore_scope': 'selected_layer_color_parameters',
              'mask_pixels_restorable': False, 'full_image_restore_verified': False}
        cp['intent'] = self._intent(task)
        task['checkpoints'].append(cp)
        task['previous'] = task.get('current')
        task['current'] = checkpoint_id
        task.setdefault('initial', checkpoint_id)

    def _review(self, task, payload, target):
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
        self._unchanged(cp, target)
        if cp['id'] != task.get('current') or cp.get('review'):
            raise Refused('invalid_review', 'Review the current checkpoint once; capture another round for another review')
        if payload.get('seen_sha256') != cp['artifact']['image']['sha256']:
            raise Refused('image_not_identified', 'Identify the actually viewed checkpoint image by its returned sha256')
        assessment = payload.get('assessment')
        required = {'goal', 'protections', 'light_color', 'edges', 'remaining'}
        if cp.get('intent', {}).get('mode', task['brief']['mode']) == 'reference_match':
            required.add('reference')
        if not isinstance(assessment, dict) or set(assessment) != required:
            raise Refused('invalid_review', 'Supply goal, protections, light_color, edges, remaining; reference for matching')
        for key, value in assessment.items():
            text(value, key)
        improved = payload.get('improved')
        if type(improved) is not bool:
            raise Refused('invalid_review', 'improved must be boolean, based on the actual previous render')
        intent = cp.get('intent', {})
        # Older tasks/checkpoints retain their original review contract. New P1
        # reviews freeze the intentions at capture, rather than rewriting history.
        targets = intent.get('reference_targets', [])
        protections = intent.get('protections', []) if intent.get('review_required') or 'protection_results' in payload else []
        feedback = intent.get('feedback', [])
        results = {}
        for name, items, statuses in (
            ('target_results', targets, {'pending', 'close', 'conflict', 'limited'}),
            ('protection_results', protections, {'preserved', 'changed', 'uncertain'}),
            ('feedback_results', feedback, {'resolved', 'improved', 'unchanged', 'worse', 'conflict'}),
        ):
            results[name] = item_results(payload.get(name, []), {i['id'] for i in items}, statuses, name)
        cp['review'] = {'assessment': assessment, 'improved': improved, 'source': 'client_visual_declaration',
                        'seen_sha256': payload['seen_sha256'], 'backend_verified': False, **results}
        for result in results['feedback_results']:
            item = next(f for f in task['feedback'] if f.get('id') == result['id'])
            item.update(status=result['status'], latest_result={**result, 'checkpoint_id': cp['id']})
        if intent.get('reference_plan_id'):
            plan = next(p for p in task['reference_plans'] if p['id'] == intent['reference_plan_id'])
            plan['latest_review'] = {'checkpoint_id': cp['id'], 'results': results['target_results']}
        task['no_improvement_rounds'] = 0 if improved else task['no_improvement_rounds'] + 1
        task['phase'] = 'needs_direction' if task['no_improvement_rounds'] >= 2 else 'editing'
        if (any(r['status'] == 'changed' for r in results['protection_results'])
                or any(r['status'] in {'worse', 'conflict'} for r in results['feedback_results'])
                or any(r['status'] == 'conflict' for r in results['target_results'])):
            task['phase'] = 'needs_direction'
        elif any(r['status'] == 'uncertain' for r in results['protection_results']):
            task['phase'] = 'needs_verification'

    def _feedback(self, task, payload, target):
        kind = payload.get('kind', 'custom')
        if kind not in {'brightness', 'temperature', 'strength', 'subject', 'background', 'prefer_previous', 'custom'}:
            raise Refused('invalid_feedback', 'Use a documented feedback kind')
        message = text(payload.get('message'), 'feedback message')
        protections = texts(payload.get('protect', []), 'protect')
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id', task.get('current')))
        if cp['id'] != task.get('current'):
            raise Refused('stale_feedback', 'Attach feedback to the current checkpoint')
        self._unchanged(cp, target)
        interpretation = payload.get('interpretation')
        if interpretation is not None:
            if not isinstance(interpretation, dict) or set(interpretation) != {'problem', 'region', 'direction', 'method', 'risks'}:
                raise Refused('invalid_feedback', 'Interpretation requires problem, region, direction, method, risks')
            for key, value in interpretation.items():
                text(value, key)
            if interpretation['method'] not in {'global', 'color_range', 'local', 'inspect_only'}:
                raise Refused('invalid_feedback', 'Unsupported intended adjustment method')
            if interpretation['method'] == 'local' and not task['brief'].get('allowed_local', True):
                raise Refused('local_not_allowed', 'This brief excludes local adjustments')
        entry = {'checkpoint_id': cp['id'], 'kind': kind, 'message': message,
                 'protect': protections, 'created_at': time.time()}
        if interpretation is not None:
            entry.update(id=uuid.uuid4().hex, interpretation=deepcopy(interpretation), status='pending')
        task['feedback'].append(entry)
        task['brief']['protections'] = list(dict.fromkeys(task['brief']['protections'] + protections))
        task['phase'] = 'editing'

    def _reference_plan(self, task, payload, target):
        if task['brief']['mode'] != 'reference_match':
            raise Refused('reference_required', 'Use a reference_match brief with identified client references')
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
        if cp['id'] != task.get('current'):
            raise Refused('stale_plan', 'Analyze the current checkpoint before planning another round')
        self._unchanged(cp, target)
        targets = payload.get('targets')
        if not isinstance(targets, list) or not 1 <= len(targets) <= 12:
            raise Refused('invalid_reference_plan', 'Plan one to twelve concrete reference traits')
        references = {r['id'] for r in task['brief']['references']}
        seen = set()
        required = {'id', 'reference_id', 'observation', 'uncertainty', 'region', 'direction', 'protect', 'priority', 'limitation'}
        for item in targets:
            if not isinstance(item, dict) or set(item) != required:
                raise Refused('invalid_reference_plan', 'Each trait needs id/reference_id/observation/uncertainty/region/direction/protect/priority/limitation')
            for key in required - {'protect', 'priority'}:
                text(item[key], key)
            texts(item['protect'], 'protect')
            if item['reference_id'] not in references or item['id'] in seen:
                raise Refused('invalid_reference_plan', 'Use unique trait IDs and references in the current brief')
            seen.add(item['id'])
            if type(item['priority']) is not int or not 1 <= item['priority'] <= 5:
                raise Refused('invalid_reference_plan', 'Trait priority must be an integer in 1..5 (1 is highest)')
        analysis_id = payload.get('anchor_analysis_id')
        if analysis_id is not None:
            analysis = next((a for a in task.get('anchor_analyses', []) if a['id'] == analysis_id), None)
            if not analysis or analysis['checkpoint_id'] != cp['id'] or analysis_id != task.get('anchor_analysis_id'):
                raise Refused('analysis_stale', 'Link a current anchor analysis from this task and checkpoint')
        plan = {'id': uuid.uuid4().hex, 'checkpoint_id': cp['id'], 'anchor_analysis_id': analysis_id,
                'references': deepcopy(task['brief']['references']),
                'targets': deepcopy(targets), 'created_at': time.time(), 'source': 'client_visual_analysis',
                'backend_verified': False}
        task.setdefault('reference_plans', []).append(plan)
        task['reference_plan_id'] = plan['id']

    def _anchor_analysis(self, task, payload, target):
        from .reference_color import analyze, image_bytes, load_srgb, validate_anchors
        if task['brief']['mode'] != 'reference_match':
            raise Refused('reference_required', 'Anchor mapping requires a reference_match task')
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
        if cp['id'] != task.get('current'):
            raise Refused('analysis_stale', 'Analyze the current actual-render checkpoint')
        self._unchanged(cp, target)
        allowed = {'checkpoint_id', 'baseline_analysis_id', 'alignment_confirmed', 'reference_id',
                   'reference_checkpoint_id', 'reference_path', 'reference_sha256', 'assume_srgb', 'anchors'}
        if payload.keys() - allowed:
            raise Refused('invalid_anchor_analysis', 'Use documented anchor-analysis fields')
        baseline = None
        if 'baseline_analysis_id' in payload:
            if set(payload) != {'checkpoint_id', 'baseline_analysis_id', 'alignment_confirmed'} or payload['alignment_confirmed'] is not True:
                raise Refused('anchor_alignment_required', 'Recheck corresponding detail locations, then reuse with alignment_confirmed=true')
            baseline = next((a for a in task.get('anchor_analyses', []) if a['id'] == payload['baseline_analysis_id']), None)
            if baseline is None:
                raise Refused('analysis_not_found', 'Reuse an anchor analysis from this task')
            source = deepcopy(baseline['reference_source'])
            entry, anchors, assumptions = baseline['reference_entry'], baseline['anchors'], baseline['assume_srgb']
        else:
            entry = next((r for r in task['brief']['references'] if r['id'] == payload.get('reference_id')), None)
            if entry is None:
                raise Refused('reference_required', 'Identify a reference in the current brief')
            if ('reference_checkpoint_id' in payload) == ('reference_path' in payload):
                raise Refused('invalid_anchor_analysis', 'Use exactly one explicit reference image path or same-task checkpoint')
            if 'reference_checkpoint_id' in payload:
                ref_cp = self._checkpoint_by_id(task, payload['reference_checkpoint_id'])
                source = {'path': ref_cp['artifact']['path'], 'sha256': ref_cp['artifact']['image']['sha256'], 'checkpoint_id': ref_cp['id']}
            else:
                source = {'path': text(payload.get('reference_path'), 'reference path'),
                          'sha256': text(payload.get('reference_sha256'), 'reference sha256')}
            anchors, assumptions = payload.get('anchors'), payload.get('assume_srgb', {})
        if entry not in task['brief']['references']:
            raise Refused('reference_changed', 'The reference or role changed; start a new anchor analysis')
        validate_anchors(anchors)
        if not isinstance(assumptions, dict) or assumptions.keys() - {'reference', 'target'}:
            raise Refused('invalid_color_assumption', 'assume_srgb names reference/target booleans')
        archive = (self.journal.state_dir / 'task-previews').resolve()
        target_path = Path(cp['artifact']['path'])
        if not target_path.resolve().is_relative_to(archive):
            raise Refused('invalid_preview', 'Analyze the retained actual render, not a display screenshot')
        if source.get('checkpoint_id') and not Path(source['path']).resolve().is_relative_to(archive):
            raise Refused('invalid_preview', 'Reference checkpoint is outside its private archive')
        ref_data = image_bytes(source['path'], source['sha256'])
        target_data = image_bytes(target_path, cp['artifact']['image']['sha256'])
        reference, reference_color = load_srgb(ref_data, assumptions.get('reference', False))
        current, target_color = load_srgb(target_data, assumptions.get('target', False))
        if baseline:
            bw, bh = baseline['target_color']['oriented_dimensions']
            if abs(current.width / current.height - bw / bh) > .005:
                raise Refused('anchor_geometry_changed', 'Photo aspect ratio changed; choose new corresponding patches')
        report = analyze(reference, current, anchors)
        self._unchanged(cp, target)
        record = {'id': uuid.uuid4().hex, 'checkpoint_id': cp['id'], 'created_at': time.time(),
                  'reference_entry': deepcopy(entry), 'reference_source': source, 'anchors': deepcopy(anchors),
                  'target_sha256': cp['artifact']['image']['sha256'], 'assume_srgb': deepcopy(assumptions),
                  'reference_color': reference_color, 'target_color': target_color, 'report': report,
                  'baseline_analysis_id': baseline['id'] if baseline else None,
                  'alignment_source': 'client_visual_declaration', 'C1_parameters_written': False}
        task.setdefault('anchor_analyses', []).append(record)
        task['anchor_analysis_id'] = record['id']

    def _recognize(self, task, payload):
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
        if not cp.get('review'):
            raise Refused('review_required', 'Use a visually reviewed checkpoint for partial recognition')
        aspect = text(payload.get('aspect'), 'recognized aspect')
        confirmation = text(payload.get('user_confirmation'), 'user confirmation')
        supersedes = texts(payload.get('supersedes', []), 'supersedes')
        active = {r['id']: r for r in task.get('recognitions', []) if r['active']}
        if any(identifier not in active for identifier in supersedes):
            raise Refused('unknown_recognition', 'Only explicitly supersede active recognitions in this task')
        for identifier in supersedes:
            active[identifier]['active'] = False
        task.setdefault('recognitions', []).append({'id': uuid.uuid4().hex, 'checkpoint_id': cp['id'],
            'aspect': aspect, 'user_confirmation': confirmation, 'active': True, 'supersedes': supersedes,
            'created_at': time.time(), 'source': 'user_declaration', 'pixel_lock': False})

    @staticmethod
    def _intent(task):
        plan = next((p for p in task.get('reference_plans', []) if p['id'] == task.get('reference_plan_id')), None)
        protections = [{'id': 'brief:' + digest(p)[:16], 'description': p, 'source': 'brief'}
                       for p in task['brief']['protections']]
        if plan:
            for item in plan['targets']:
                for p in item['protect']:
                    identifier = 'brief:' + digest(p)[:16]
                    if not any(existing['id'] == identifier for existing in protections):
                        protections.append({'id': identifier, 'description': p, 'source': 'reference_plan'})
        protections.extend({'id': r['id'], 'description': r['aspect'], 'source': 'recognition',
                            'checkpoint_id': r['checkpoint_id']}
                           for r in task.get('recognitions', []) if r['active'])
        return {'version': 1, 'mode': task['brief']['mode'],
                'review_required': bool(plan or task.get('recognitions') or any(f.get('interpretation') for f in task['feedback'])),
                'reference_plan_id': plan['id'] if plan else None,
                'anchor_analysis_id': task.get('anchor_analysis_id'),
                'reference_targets': deepcopy(plan['targets']) if plan else [], 'protections': protections,
                'feedback': deepcopy([f for f in task['feedback'] if f.get('interpretation') and f['status'] != 'resolved'])}

    def _compare(self, task, payload):
        if not isinstance(payload, dict):
            raise Refused('invalid_task', 'Comparison payload must be an object')
        roles = payload.get('roles', ['initial', 'current'])
        if not isinstance(roles, list) or not 1 <= len(roles) <= 3 or any(r not in {'initial', 'previous', 'current', 'accepted'} for r in roles):
            raise Refused('invalid_task', 'Compare one to three roles: initial, previous, current, accepted')
        images = []
        requested = [(role, task[role]) for role in roles if task.get(role)]
        if 'checkpoint_ids' in payload:
            identifiers = texts(payload['checkpoint_ids'], 'checkpoint_ids')
            if 'roles' in payload or not 1 <= len(identifiers) <= 3:
                raise Refused('invalid_task', 'Compare one to three explicit checkpoints, without roles')
            requested = [('checkpoint', identifier) for identifier in identifiers]
        for role, identifier in requested:
            cp = self._checkpoint_by_id(task, identifier)
            images.append({'role': role, 'checkpoint_id': cp['id'], 'label': cp['label'],
                           'artifact': cp['artifact'], 'review': cp.get('review'), 'intent': cp.get('intent'),
                           'anchor_analyses': [a for a in task.get('anchor_analyses', []) if a['checkpoint_id'] == cp['id']],
                           'restore': {'scope': cp['restore_scope'], 'layer': cp['target']['layer'],
                                       'modules': list(cp['snapshots']), 'full_image_verified': False}})
        return {'status': 'ok', 'task_id': task['id'], 'comparison': images,
                'references': task['brief']['references'], 'reference_images': 'provided_by_client',
                'reference_plan': next((p for p in task.get('reference_plans', []) if p['id'] == task.get('reference_plan_id')), None),
                'recognitions': [r for r in task.get('recognitions', []) if r['active']],
                'limits': 'Historical actual renders, not necessarily the current C1 state. No pixel-level subject locks.'}

    def _restore(self, task, payload, observed, record):
        if not task.get('candidate_target'):
            raise Refused('candidate_required', 'Bind the registered candidate before restoration')
        cp = self._checkpoint_by_id(task, payload.get('checkpoint_id'))
        # The initial checkpoint can belong to the protected source variant.
        # Its parameters may only be pasted into the bound candidate, never the source.
        for key in ('instance', 'document', 'image', 'layer'):
            if cp['target'].get(key) != observed['target'].get(key):
                raise Refused('target_changed', 'Restore requires the same instance, photo and checkpoint layer')
        layers = self.core._observe_module('layers')
        self.core._target(observed['target'], layers['target'])
        if layers['state'] != cp['layers']:
            raise Refused('structure_changed', 'Layer inventory/settings changed; whole-image or mask restoration is unavailable')
        # Read all destinations before any write. Each native setter still checks
        # its exact target/old values; there is no transaction or automatic rollback.
        destinations = {}
        for module in cp['snapshots']:
            obs = self.core._observe(list(MODULES[module])) if module in MODULES else self.core._observe_module(module)
            self.core._target(observed['target'], obs['target'])
            destinations[module] = obs
        steps = []
        for module, token in cp['snapshots'].items():
            destination = destinations[module]
            source = self.journal.observation(token)
            if source['values'] == destination['values']:
                continue
            child_id = f"{record['operation_id']}:{module}"
            # Persist the child ID before dispatch so an interrupted parent is inspectable.
            steps.append({'module': module, 'request_id': child_id, 'status': 'dispatching'})
            self.journal.update(record['operation_id'], result={'task_id': task['id'], 'steps': steps})
            outcome = self.core.dispatch({'command': 'adjust', 'module': module,
                'patch': {'action': 'paste', 'source_observation_token': token},
                'target': destination['target'], 'observation_token': destination['observation_token'],
                'request_id': child_id})
            steps[-1]['status'] = outcome['status']
            if outcome['status'] != 'control_confirmed':
                task['phase'] = 'needs_verification'
                return {'status': 'partial', 'result': {'task_id': task['id'], 'steps': steps, 'last_result': outcome,
                        'restore_scope': cp['restore_scope'], 'replayed': False,
                        'next': 'Query the child request_id; do not replay this restore or start another restore.'}}
        # A later module/native coupling or manual change must not invalidate
        # an earlier readback while the parent still claims complete restoration.
        from .macos_tools import near
        for module, token in cp['snapshots'].items():
            final = self.core._observe(list(MODULES[module])) if module in MODULES else self.core._observe_module(module)
            self.core._target(observed['target'], final['target'])
            if not near(final['values'], self.journal.observation(token)['values'],
                        0.05 if module == 'white_balance' else 0.002):
                task['phase'] = 'needs_verification'
                return {'status': 'partial', 'result': {'task_id': task['id'], 'steps': steps,
                        'parameters_restored': False, 'mismatch_module': module,
                        'restore_scope': cp['restore_scope'], 'visual_restore_verified': False}}
        task['phase'] = 'needs_preview'
        task['restored_from'] = cp['id']
        task.pop('accepted', None)
        return {'status': 'completed', 'result': {'task_id': task['id'], 'steps': steps,
                'restore_scope': cp['restore_scope'], 'parameters_restored': True,
                'visual_restore_verified': False, 'full_image_restore_verified': False,
                'next': 'Render again, checkpoint and visually review; other layers and mask pixels are outside this restore.'}}
