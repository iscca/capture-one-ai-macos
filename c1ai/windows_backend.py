"""Bounded native Windows adapter. No legacy workflow or WindowsDriver imports.

All database/sidecar access is read-only. UIA values are control observations,
never silently promoted to current model parameters.
"""
from __future__ import annotations
import ctypes
from contextlib import closing
from ctypes import wintypes as W
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from .uia_transport import UiaWorker


class NativeError(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


IDENTITY = {
    'selection': {'automation_id': 'tb_VariantTitle', 'control_type': 'Text'},
    'edit_scope': {'automation_id': 'SummaryText', 'control_type': 'Text'},
    'base_image': {'automation_id': 'ViewerToolbarLayersComboBox', 'control_type': 'ComboBox'},
}
FIELD_INFO = {
    'exposure': ('ExposureToolView', 'ExposureToolView', '曝光', -4, 4, .01),
    'contrast': ('ExposureToolView', 'ExposureToolView', '对比度', -50, 50, .1),
    'brightness': ('ExposureToolView', 'ExposureToolView', '亮度', -50, 50, .1),
    'saturation': ('ExposureToolView', 'ExposureToolView', '饱和度', -100, 100, .1),
    'temperature': ('WhiteBalanceToolView', 'WhiteBalanceView', '色温', 800, 14000, 1),
    'tint': ('WhiteBalanceToolView', 'WhiteBalanceView', '色调', -50, 50, .1),
    'highlight_recovery': ('ShadowHighlightToolView', 'ShadowHighlightToolView', '高光', -100, 100, .1),
    'shadow_recovery': ('ShadowHighlightToolView', 'ShadowHighlightToolView', '阴影', -100, 100, .1),
    'white_recovery': ('ShadowHighlightToolView', 'ShadowHighlightToolView', '白色', -100, 100, .1),
    'black_recovery': ('ShadowHighlightToolView', 'ShadowHighlightToolView', '黑色', -100, 100, .1),
}


def selector(field):
    aid, cls, label, *_ = FIELD_INFO[field]
    return {'automation_id': 'ToolSliderDecimalUpDown', 'control_type': 'Edit',
            'ancestors': [{'automation_id': aid, 'class_name': cls, 'control_type': 'Custom'}],
            'previous_sibling': {'name': label, 'control_type': 'Text', 'class_name': 'TextBlock'}}


def process_started(pid):
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; k.OpenProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle: raise NativeError('PROCESS_UNAVAILABLE', 'Cannot verify process creation time')
    try:
        times = [W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise NativeError('PROCESS_UNAVAILABLE', 'Cannot verify process creation time')
        return str((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime)
    finally: k.CloseHandle(handle)


def resolve_identity(session_path, label):
    """A bounded UI ordinal + unique Session record, not native selected UUID."""
    session = Path(session_path).resolve()
    if session.suffix.lower() != '.cosessiondb' or not session.is_file():
        raise NativeError('SESSION_REQUIRED', 'Configure one existing Capture One Session')
    match = re.fullmatch(r'(.+) \[(\d+)\]', label)
    filename, ordinal = (match.group(1), int(match.group(2))) if match else (label, 1)
    with closing(sqlite3.connect(session.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute('SELECT i.ZIMAGEFILENAME,i.ZIMAGEUUID,p.ZISRELATIVE,p.ZRELATIVEPATH,p.ZWINROOT,v.ZVARIANTUUID,v.Z_PK,v.ZINDEX '
                              'FROM ZIMAGE i JOIN ZPATHLOCATION p ON p.Z_PK=i.ZIMAGELOCATION '
                              'JOIN ZVARIANT v ON v.ZIMAGE=i.Z_PK WHERE i.ZIMAGEFILENAME=? AND v.ZINDEX=?', (filename, ordinal-1)))
    if len(rows) != 1:
        raise NativeError('AMBIGUOUS_IMAGE', 'Session filename and variant ordinal are not unique', {'count':len(rows),'label':label})
    row = dict(rows[0])
    folder = (session.parent / row['ZRELATIVEPATH']) if row['ZISRELATIVE'] else (Path(row['ZWINROOT']) / row['ZRELATIVEPATH'])
    image = (folder / filename).resolve()
    if not image.is_file(): raise NativeError('SOURCE_UNAVAILABLE', 'Selected source file is missing')
    return {'document': {'path': str(session)}, 'image': {'path': str(image)},
            'variant': {'label': label, 'id': row['ZVARIANTUUID'].lower()},
            'identity_evidence': {'source': 'unique_session_filename_and_ui_ordinal', 'ordinal':ordinal,
                                  'database_variant_id':row['Z_PK'], 'selected_uuid_native_readback':False}}


class WindowsBackend:
    def __init__(self, state_dir=None):
        self.state_dir = Path(state_dir or Path(os.environ['LOCALAPPDATA'])/'CaptureOneAI')
        self.config_path = self.state_dir/'config.json'
        self.config = json.loads(self.config_path.read_text('utf-8')) if self.config_path.exists() else {}
        self.native_dir = Path(__file__).parent/'native'
        self.worker = UiaWorker(self.native_dir/'uia-host.ps1')
        self.window = None
        self.action_hook = None

    def validate_request(self, request):
        if request.get('module',request.get('tool'))=='film_grain':
            from .contracts import Refused
            raise Refused('unsupported','Film grain is currently implemented by the macOS backend only')
        if request.get('command')=='adjust' and request.get('module')=='masks' and 'native_amount' in request.get('patch',{}):
            from .contracts import Refused
            raise Refused('unsupported','native_amount is a macOS-only feather parameter; Windows uses pixel radius')

    def close(self): self.worker.close()

    def call(self, operation, **payload):
        allowed = {'windows','status','snapshot','read','inspect','guard','dialogs','set','invoke','activate','save_style_file','dismiss_info','inspect_folder_picker','choose_export_folder','confirm_export_folder','select_current_export_folder','layers','layer_action','prepare_layers','curve_state','curve_select','curve_add_point','curve_move_point','mixer_state','mixer_select','mixer_set','reveal_tool','navigate'}
        if operation not in allowed: raise NativeError('UNSUPPORTED_NATIVE_ACTION', operation)
        if operation != 'windows' and not self.window: self._connect()
        # Explicit legacy UI operations only. Parameter edits use the internal model.
        request = {'op':operation, 'background_only':False}
        if operation != 'windows': request.update(pid=self.window['pid'],hwnd=self.window['hwnd'])
        request.update(payload)
        exe = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(exe),'-NoLogo','-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',str(self.worker.host_path)]
        completed = self.worker.request(command,json.dumps(request,ensure_ascii=False,allow_nan=False).encode('utf-8'),90 if operation=='layer_action' else 30)
        response = json.loads(completed.stdout.decode('utf-8-sig'))
        if not isinstance(response,dict) or type(response.get('ok')) is not bool:
            raise NativeError('NATIVE_PROTOCOL_ERROR','Native response envelope is invalid')
        if response['ok'] is False:
            e=response.get('error',{})
            if (not isinstance(e,dict) or not isinstance(e.get('code'),str) or not e['code']
                    or not isinstance(e.get('message'),str) or not e['message']):
                raise NativeError('NATIVE_PROTOCOL_ERROR','Native error response is incomplete')
            error=NativeError(e.get('code','UIA_ERROR'),e.get('message','Native command failed'),e)
            if operation in {'set','invoke','activate','save_style_file','dismiss_info','choose_export_folder','confirm_export_folder','select_current_export_folder','layer_action','prepare_layers','curve_select','curve_add_point','curve_move_point','mixer_select','mixer_set','navigate'} and e.get('possibly_mutated') is False:
                error.dispatched=False
            raise error
        if not isinstance(response.get('result'),dict): raise NativeError('NATIVE_PROTOCOL_ERROR','Native result is not an object')
        return response['result']

    def _connect(self):
        from .native_windows import find_native_main
        session=self.config.get('session_path')
        if not session:raise NativeError('SESSION_REQUIRED','Set session_path in the user config')
        self.window=find_native_main(Path(session).stem)

    def action(self, name, func, intent=None):
        if not callable(self.action_hook): raise NativeError('JOURNAL_REQUIRED','Native side effect requires the core operation journal')
        return self.action_hook(name,func,intent)

    def observe(self, fields, *, allow_layer=False):
        if set(fields)-set(FIELD_INFO): raise NativeError('UNSUPPORTED_FIELD','Unqualified native fields requested')
        if self.window:
            try:alive=process_started(self.window['pid'])==self.window['started_at']
            except NativeError:alive=False
            if not alive:self.window=None
        if not self.window: self._connect()
        native=self._model_call({'command':'context'})
        if native['selected_count']!=1:raise NativeError('SINGLE_SELECTION_REQUIRED','Exactly one native selected variant required')
        if Path(native['target']['document_path'])!=Path(self.config['session_path']):
            raise NativeError('DOCUMENT_CHANGED','Native document differs from configured Session')
        from .native_modules import layer_reference
        selected=[row for row in native['layers'] if row['selected']]
        if len(selected)!=1:raise NativeError('SINGLE_LAYER_REQUIRED','Exactly one native selected layer required')
        layer='Background' if selected[0]['is_image_layer'] else layer_reference(selected[0])
        target={'instance':{k:self.window[k] for k in ('pid','started_at','version')},
            'document':{'path':native['target']['document_path']},'image':{'path':native['image_path']},
            'variant':{'id':native['target']['variant_uuid'],'label':native['variant_label']},'layer':layer}
        observation={'target':target,'values':{},'raw_state':{'window':self.window},'native_context':native,
            'native_layers':{'layers':native['layers'],'selected':selected},
            'evidence':{'source':'c1_internal_model','atomic':True,'model_verified':True,
                'adjustment_scope':'background' if layer=='Background' else 'active_adjustment_layer',
                'photo_identity_native_readback':True,'single_selected_count':1,'layer_identity_source':'native_mask_guid'}}
        if fields:
            result=self.read_native_parameters(fields,observation)
            observation.update(values=result['values'],native_readback=result)
            observation['evidence'].update(parameter_readback_verified=True)
        return observation

    def _model_call(self, request, observation=None):
        from .internal_transport import call
        from .client import RequestUncertain
        from .windows_owner import PipeError
        instance=(observation['target']['instance'] if observation else self.window)
        payload={**request,'expected_instance':{k:instance[k] for k in ('pid','started_at')}}
        action_context=getattr(self,'action_context',None)
        if request['command'] in ('write','layer','select','clone','module') and action_context:
            payload.setdefault('request_id',action_context['action_id'])
        if observation:
            native=observation['native_context']
            payload['expected_target']={**native['target'],'layer_uuid':native['layer_uuid']}
            if request['command']=='layer':payload['before_layers']=native['layers']
        try:result=call(payload)
        except Exception as cause:
            error=NativeError('INTERNAL_MODEL_UNAVAILABLE','C1 internal model bridge is unavailable; no UI editing fallback',str(cause))
            error.dispatched=request['command'] in ('write','layer','select','clone','module') and (isinstance(cause,RequestUncertain) or not isinstance(cause,(OSError,PipeError,ValueError,UnicodeError)))
            raise error from cause
        if result.get('status')!='ok':
            error=NativeError(result.get('error',{}).get('code','INTERNAL_MODEL_ERROR'),'C1 internal model call did not complete',result)
            error.dispatched=result.get('dispatched') is not False
            raise error
        return result['data']

    def read_model(self, fields, observation):
        return self._model_call({'command':'read','fields':list(fields)},observation)['values']

    def write_model(self, patch, before, observation):
        context=getattr(self,'action_context',None)
        if not context:
            error=NativeError('JOURNAL_REQUIRED','A model write requires the current Core action');error.dispatched=False;raise error
        result=self._model_call({'command':'write','request_id':context['action_id'],'fields':list(patch),
            'patch':patch,'before':{k:before[k] for k in patch},'readback_fields':list(before)},observation)
        return {'status':'completed','accepted':True,'readback_received':True,'dispatched':True,
            'backend':'c1_internal_model','model_verified':True,'values':result['values'],'target':result['target']}

    def context(self):
        from .native_windows import native_window_inventory
        self._connect()
        inventory=native_window_inventory(self.window['pid'],self.window['hwnd'],
            expected_started_at=self.window['started_at'])
        if inventory['blocking_modal_possible'] or inventory.get('main_enabled') is False or inventory.get('enumeration_complete') is False:
            reason='owned_window_blocks_main' if inventory['blocking_modal_possible'] else 'native_window_state_unavailable'
            return {'target':None,'editing_ready':False,'reason':reason,
                    'window':self.window,'native_windows':inventory,
                    'capabilities':{'modules':{},'fields':{}},'evidence':{'source':'native_window_inventory','model_verified':False}}
        try:obs=self.observe([])
        except NativeError as e:
            if e.code not in ('CONTROL_NOT_FOUND','SINGLE_SELECTION_REQUIRED','SINGLE_LAYER_REQUIRED'):raise
            return {'target':None,'editing_ready':False,'reason':e.code,'window':self.window,
                    'native_windows':inventory,'capabilities':{'modules':{},'fields':{}},
                    'evidence':{'source':'native_window_inventory','model_verified':False}}
        return {'target':obs['target'],'capabilities':self.capabilities_for(obs),
                'editing_ready':True,'evidence':obs['evidence'],'native_windows':inventory,
                'native_worker_starts':self.worker.starts}

    def capabilities_for(self, obs):
        """Derive dispatch eligibility from a fresh Core observation; no UI call."""
        qualified=self.config.get('qualified_version')==obs['target']['instance']['version']
        path=self.state_dir/'candidates.json'
        records=json.loads(path.read_text('utf-8')) if path.exists() else {}
        candidate=records.get(obs['target']['variant']['id'],{})
        qualified=qualified and candidate.get('document')==obs['target']['document'] and candidate.get('image')==obs['target']['image']
        def declared(name):
            value=self.config.get(name,[])
            if not isinstance(value,list) or any(not isinstance(f,str) or f not in FIELD_INFO for f in value):
                raise NativeError('INVALID_QUALIFICATION_CONFIG','Field qualification must be an explicit list of known fields')
            return set(value)
        from .tangent_transport import PARAMETER_RULES
        live,experimental=declared('native_qualified_fields'),declared('experimental_fields')
        fields={f:{'readable':True,'writable':qualified and f in (live|experimental),'min':m[3],'max':m[4],'precision':m[5],
                   'tolerance':PARAMETER_RULES[f][2],
                   'backend':'c1_internal_model','qualification':('native_live_verified_bounded_scope' if f in live else
                       'experimental_pending_live' if f in experimental else 'implemented_not_enabled')} for f,m in FIELD_INFO.items()}
        module_names={'curves','levels','color_mixer','color_balance','layers','masks'}
        enabled=self.config.get('qualified_modules',[])
        experimental_modules=self.config.get('experimental_modules',[])
        if any(not isinstance(v,list) or any(m not in module_names for m in v) for v in (enabled,experimental_modules)):
            raise NativeError('INVALID_QUALIFICATION_CONFIG','Structured modules require an explicit known-module list')
        routes={name:'c1_internal_model' for name in module_names}
        return {'fields':fields,
                 'modules':{m:{'readable':True,'writable':qualified and m in enabled+experimental_modules,
                    'backend':routes[m], 'availability_requires_fresh_observation':True,
                    'qualification':'live_verified_bounded_scope' if m in enabled else 'experimental_pending_live' if m in experimental_modules else 'implemented_not_enabled'} for m in module_names},
                 'images':{'source':'c1_internal_model','collection_inventory':True,'uuid_selection':True,
                           'offscreen_selection':True,'pagination':True},
                 'preview':{'default':'native','native':{'source':'capture_one_native_render',
                     'export_required':False,'render_verified':True,'coordinate_mapping':True},
                     'mask':{'source':'capture_one_native_mask','render_verified':True},
                     'viewer':{'source':'native_window_viewer_capture','export_required':False,
                     'render_verified':False,'whole_image_verified':False},
                     'render':{'source':'native_export_recipe','export_required':True}},
                 'batch':{'writable':False}}

    def guard(self, observation):
        raw=observation['raw_state']
        if 'values' not in raw:
            raw=self.call('snapshot',bindings=IDENTITY)
            mapped=resolve_identity(observation['target']['document']['path'],raw['values']['selection']['name'])
            if mapped['variant']['id']!=observation['target']['variant']['id']:raise NativeError('TARGET_CHANGED','UI action target differs from native selected variant')
        base=raw['values']['base_image'].get('image_layer_selected') is True
        guard={'window_title':raw['window']['title'],'bindings':IDENTITY.copy(),'expected':{
            'selection':{'name':raw['values']['selection']['name']},
            'edit_scope':{'name':raw['values']['edit_scope']['name']},
            'base_image':{'image_layer_selected':base}}}
        if not base:
            guard['selected_layer_runtime_id']=observation['target']['layer']['runtime_id']
        return guard

    def observe_module(self, module):
        from .native_modules import observe_module
        return observe_module(self,module)

    def adjust_module(self, module, patch, observation):
        from .native_modules import adjust_module
        return adjust_module(self,module,patch,observation)

    def read_native_parameters(self, fields, observation):
        from .tangent_transport import PARAMETER_RULES
        values=self.read_model(fields,observation)
        for field,value in values.items():
            if field not in PARAMETER_RULES or type(value) not in (int,float) or not math.isfinite(value):
                raise NativeError('NATIVE_PARAMETER_UNAVAILABLE','Native parameter value is invalid')
        return {'values':values,'parameters':{f:{'value':v,'available':True} for f,v in values.items()},
            'backend':'c1_internal_model','model_verified':True}

    def write_native_parameter(self, field, before, value, observation):
        from .tangent_transport import PARAMETER_RULES
        tolerance=PARAMETER_RULES[field][2]
        result=self.write_model({field:value},{field:before},observation)
        actual=result['values'][field]
        if not math.isclose(actual,value,abs_tol=tolerance,rel_tol=0):
            error=NativeError('NATIVE_PARAMETER_OUTCOME_UNKNOWN','Native setter readback differs from requested value',result)
            error.dispatched=True;raise error
        return {**result,'verified':True,'before':{'value':before,'available':True},'after':{'value':actual,'available':True},
            'parameter_readback_verified':True}

    def set_value(self, field, value, expected_observation):
        return self.write_native_parameter(field,expected_observation['values'][field],value,expected_observation)

    def set_values(self, patch, observation):
        return self.write_model(patch,observation['values'],observation)

    def image_action(self, action, request, observation):
        if action=='prepare_tool':
            from .native_modules import prepare_tool
            return prepare_tool(self,request['tool'],observation)
        if action=='list':
            self._connect()
            options={k:request[k] for k in ('scope','offset','limit','collection_token') if request.get(k) is not None}
            result=self._model_call({'command':'read','scope':'images','options':options})
            native=result.pop('navigation')
            if Path(native['document_path'])!=Path(self.config['session_path']):
                raise NativeError('DOCUMENT_CHANGED','Native collection differs from configured Session')
            observation={'navigation':True,'target':{
                'instance':{k:self.window[k] for k in ('pid','started_at','version')},
                'document':{'path':native['document_path']},'collection':{'id':result['collection_uuid']},
                'image':{'path':None},'variant':{'id':None,'label':None},'layer':None},
                'values':{'selected_uuids':native['selected_uuids']},'native_context':{
                    'target':{**native,'variant_uuid':None},'layer_uuid':None,
                    'collection_uuid':result['collection_uuid']},
                'evidence':{'source':'c1_internal_model','model_verified':True}}
            return {'status':'ok',**result,'_navigation_observation':observation}
        if action in {'select','next','previous'}:
            payload={'command':'select','collection_uuid':observation['native_context']['collection_uuid']}
            if observation.get('navigation'):payload['selected_uuids']=observation['values']['selected_uuids']
            if action=='select':
                names=[k for k in ('variant_uuid','variant_id') if request.get(k) is not None]
                if len(names)!=1:raise NativeError('IMAGE_ID_REQUIRED','Select exactly one native variant_uuid or legacy database variant_id')
                payload[names[0]]=request[names[0]]
            else:payload['direction']=action
            result=self.action('images.'+action,lambda:self._model_call(payload,observation))
            after=self.observe([])
            if after['target']['variant']['id']!=result['target']['variant_uuid']:
                raise NativeError('SELECTION_CHANGED','Native selection changed before readback')
            return {'status':'completed','after':after,'navigation':result,'model_verified':True}
        if action=='clone':
            receipt=self.action('images.clone',lambda:self._model_call({'command':'clone'},observation))
            after=self.observe([])
            if (after['target']['variant']['id']!=receipt['created_uuid'] or
                    any(after['target'][k]!=observation['target'][k] for k in ('instance','document','image'))):
                raise NativeError('CLONE_ID_UNVERIFIED','Native clone readback belongs to another photo or document')
            result={'after':after,'candidate_variant_id':receipt['created_uuid'],
                'new_source_variant_verified':True,'selected_variant_id_readback_verified':True,
                'identity_basis':'c1_internal_model','dispatch':receipt}
            target=result['after']['target']
            if not target['variant']['id'] or target['variant']['id']==observation['target']['variant']['id']:
                raise NativeError('CLONE_ID_UNVERIFIED','New candidate needs a distinct saved identity')
            path=self.state_dir/'candidates.json'
            records=json.loads(path.read_text('utf-8')) if path.exists() else {}
            records[target['variant']['id']]={'document':target['document'],'image':target['image'],
                'source_variant':observation['target']['variant'],'created_at':time.time(),
                'qualification':'native_clone_uuid_and_image_identity'}
            temp=path.with_suffix('.tmp')
            with temp.open('w',encoding='utf-8') as out:
                json.dump(records,out,ensure_ascii=False,indent=2); out.flush(); os.fsync(out.fileno())
            temp.replace(path)
            return {**result,'status':'completed'}
        if action=='prepare_adjustments':
            close={'automation_id':'cancel','name':'关闭','control_type':'Button',
                   'ancestors':[{'automation_id':'export','control_type':'Window'}]}
            try:
                self.call('read',selector=close)
            except NativeError as exc:
                if exc.code!='CONTROL_NOT_FOUND': raise
            else:
                self.action('images.close_export',lambda:self.call('invoke',selector=close,mode='invoke',guard=self.guard(observation)))
            s={'automation_id':'Root','control_type':'Button','help_text':'调整（拖动以重新排序）'}
            return {**self.action('images.prepare_adjustments',lambda:self.call('invoke',selector=s,mode='invoke',guard=self.guard(observation))),'status':'completed'}
        raise NativeError('UNSUPPORTED_IMAGE_ACTION',action)

    def variant_inventory(self, observation):
        target=observation['target']
        mapped=resolve_identity(target['document']['path'],target['variant']['label'])
        if any(mapped[key]!=target[key] for key in ('document','image','variant')):
            raise NativeError('VARIANT_INVENTORY_UNAVAILABLE','Saved source no longer matches the observed identity')
        with closing(sqlite3.connect(Path(target['document']['path']).as_uri()+'?mode=ro',uri=True)) as db:
            anchors=list(db.execute('SELECT ZIMAGE FROM ZVARIANT WHERE lower(ZVARIANTUUID)=?',(target['variant']['id'].lower(),)))
            if len(anchors)!=1: raise NativeError('VARIANT_INVENTORY_UNAVAILABLE','Saved variant UUID is not unique')
            rows=list(db.execute('SELECT ZVARIANTUUID,ZINDEX,Z_PK FROM ZVARIANT WHERE ZIMAGE=? ORDER BY ZINDEX',(anchors[0][0],)))
        if not rows: raise NativeError('VARIANT_INVENTORY_UNAVAILABLE','No saved variants match the bound source')
        return [{'id':row[0].lower(),'index':row[1],'browser_variant_id':row[2]} for row in rows]

    def preview(self, request, observation):
        if request.get('kind','native') in {'native','mask'}:
            from .model_preview import preview
            return preview(self,request,observation)
        if request.get('kind')=='viewer':
            from .fast_preview import capture_viewer
            return capture_viewer(self,observation,viewer_selector={
                'automation_id':'Viewer','class_name':'VariantGridItemWpf','control_type':'Custom'},verified_preflight=True)
        from .native_preview import prepare_export, submit_export, read_export, restore_destination
        action=request.get('action','render')
        if action=='restore_destination':
            return {**restore_destination(self,observation),'status':'completed','render_verified':False}
        if action not in ('prepare','render'):
            raise NativeError('UNSUPPORTED_PREVIEW_ACTION',action)
        plan=prepare_export(self,self.config.get('preview_recipe'),observation=observation,
                            output_root=str(self.state_dir/'export-slot'))
        if action=='prepare':
            return {'status':'completed','settings':plan['settings'],'target':observation['target'],
                    'destination':plan.get('destination'),'render_verified':False}
        receipt=submit_export(self,plan)
        return {**read_export(self,receipt,timeout=40,archive_dir=self.state_dir/'renders'),'status':'completed'}

    def reconcile(self, record):
        if record.get('request',{}).get('action')=='restart_for_native_transport':
            return {'outcome':'unknown','target':record['target'],'evidence':{
                'scope':'historical_application_setup','reason':'Automatic application recovery was retired'}}
        if record.get('request',{}).get('module') in {'curves','levels','color_mixer','color_balance','layers','masks'}:
            from .native_modules import reconcile_module
            return reconcile_module(self,record)
        from .native_reconcile import reconcile
        return reconcile(self,record)
