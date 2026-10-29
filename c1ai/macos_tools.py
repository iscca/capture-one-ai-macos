"""Structured Mac routes over the same native target guards and durable journal."""
from __future__ import annotations

import copy
import math
import uuid

from .contracts import MODULES, STRUCTURED_MODULES, Refused, digest

STRUCTURED = set(STRUCTURED_MODULES)
MASK_COMMANDS = {'invert', 'clear', 'fill', 'copy', 'feather', 'luma_range', 'create_subject', 'create_background'}
LAYER_COMMANDS = {'create_empty', 'select', 'remove_created', 'set_opacity', 'rename', 'set_enabled', 'move', 'duplicate'}


def near(actual, expected, tolerance=0.002):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int,float)) and isinstance(expected, (int,float)):
        return math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0)
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys()==expected.keys() and all(near(actual[k], expected[k], tolerance) for k in expected)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual)==len(expected) and all(near(a,b,tolerance) for a,b in zip(actual,expected))
    return actual==expected


def transition(before, after):
    return {'before': before['target'], 'after': after['target'], 'evidence': {
        'before_layers': before['state']['layers'], 'after_layers': after['state']['layers'],
        'selected_layer': after['state']['selected_layer']}}


class MacTools:
    @staticmethod
    def module_limits(module):
        return {
            'film_grain':['Background-only native film grain; type, impact and granularity'],
            'curves':['Experimental native curve-point replacement'],
            'levels':[],
            'color_balance':['Polar hue convention: +x = native hue 0; UI wheel rotation is not read'],
            'color_mixer':['Basic and Advanced only; Skin and Advanced inversion unavailable; native range units need verification'],
            'layers':['Native name inventory + UUID-tagged tool layers; duplicate native names are refused; move/duplicate failed validation and are disabled'],
            'masks':['AI subject/background, clear/invert/copy/luma and native_amount feather verified; bitmap fill verified; AI fill can retain generated-mask constraints; no direct mask pixels, gradients or combine']
        }.get(module, [])

    def _require_candidate(self, observation):
        if not self._writable_session(observation['target']) or not self._candidate(observation['target']):
            raise Refused('candidate_required', 'Structured writes require the configured Session and a registered candidate')
        if observation.get('module') in STRUCTURED and observation['module'] not in self.config().get('experimental_modules',[]):
            raise Refused('module_not_enabled','Enable the experimental module explicitly in Mac configuration')

    def observe_module(self, module):
        if module not in STRUCTURED:
            raise Refused('unsupported', 'Unknown structured module')
        if module=='film_grain' and self.observe([])['target']['layer']!='Background':
            raise Refused('background_only','Native film grain is global; select the Background layer')
        r = self._call('tool_read', module=module)
        return self._structured_observation(r)

    def _structured_observation(self, r):
        obs=self._observation(r)
        obs.update(module=r['module'], state=r['state'], values={r['module']:r['state']})
        obs['evidence'].update(experimental=True, layer_identity='native_name_inventory_and_tool_uuid_tags',
            mask_pixels_verified=False, parameter_readback_verified=True)
        return obs

    def adjust_module(self, module, patch, observation):
        self._require_candidate(observation)
        p=copy.deepcopy(patch)
        self._validate_tool_patch(module,p,observation)
        receipt=self.action_hook(module+'.'+p.get('action','set'),
            lambda:self._call('tool_adjust',module=module,expected=observation['target'],
                before_state=observation['state'],patch=p,new_id=uuid.uuid4().hex,
                new_name=p.get('name',{'create_subject':'AI Subject','create_background':'AI Background'}.get(p.get('action'),'Adjustment'))),
            {'patch':p})
        after=self._structured_observation(receipt)
        checked=self._verify_tool(module,p,observation,after)
        evidence={**after['evidence'],'native_command_completed':True,'experimental':True,
                  'verification_scope':'parameter_and_inventory_readback' if checked else 'native_command_reply_only'}
        result={'status':'control_confirmed' if checked else 'partial',
                'requested_result_verified':checked,'after':after,'evidence':evidence,
                'model_verified':checked}
        if module in {'layers','masks'}:
            result['target_transition']=transition(observation,after)
        if not checked:
            result['error']={'code':'MASK_PIXELS_UNVERIFIED','message':'Native command completed; Mac does not expose mask pixels/revision. Query/reconcile this operation; do not replay.'}
        return result

    def _validate_tool_patch(self,module,p,obs):
        if module=='film_grain' and obs['target']['layer']!='Background':
            raise Refused('background_only','Native film grain is global')
        if module=='color_mixer':
            if p['mode']=='skin':
                raise Refused('SKIN_NATIVE_ENTRY_MISSING','Skin Tone per-range properties are absent from the installed scripting dictionary')
            if p['mode']=='advanced':
                if 'inverted' in p:
                    raise Refused('ADVANCED_INVERT_UNAVAILABLE','Advanced inversion is absent from the scripting dictionary')
                if p.get('action','update')!='add' and not 0<=p['index']<len(obs['state']['advanced']):
                    raise Refused('stale_index','Use the native range index from a fresh read')
                p.setdefault('action','update')
                rgb=p.get('rgb')
                if rgb is None and p.get('action')!='add':
                    row=obs['state']['advanced'][p['index']]
                    rgb=[row[k] for k in ('red','green','blue')]
                if rgb is not None:
                    if any(v!=int(v) for v in rgb):raise Refused('native_rgb_precision','Mac origin RGB components must be integers')
                    high,low=max(rgb),min(rgb)
                    saturation=0 if high==0 else (high-low)/high*100
                    if p.get('saturation_low',0)<-saturation-0.002 or p.get('saturation_high',0)>100-saturation+0.002:
                        raise Refused('native_saturation_range','Mac native range offsets must keep absolute saturation inside 0..100')

        if module=='layers':
            if p['action'] in {'move','duplicate'}:
                raise Refused('NATIVE_ACTION_FAILED_VALIDATION','Mac native layer '+p['action']+' failed on the installed build and is disabled')
            if p['action'] not in LAYER_COMMANDS:
                raise Refused('unsupported','Layer command unavailable')
            if p['action']=='set_opacity' and p['opacity']!=int(p['opacity']):
                raise Refused('unsupported_precision','Mac native layer opacity is an integer')
            if p['action']=='rename' and p['layer_ref']['native_id'].startswith('existing:'):
                raise Refused('unstable_layer_identity','Only tool-tagged layer names can be safely renamed')
        if module=='masks':
            if p['action'] not in MASK_COMMANDS:
                raise Refused('MASK_NATIVE_ENTRY_MISSING','No Mac native scripting entry for '+p['action'])
            if p['action']=='fill' and p['density']!=1:
                raise Refused('MASK_DENSITY_UNAVAILABLE','Mac native fill supports density=1 only')
            if p['action']=='feather' and 'native_amount' not in p:
                raise Refused('MASK_UNIT_MISMATCH','Mac feather uses 0..100 native amount, while this API radius is pixels; no verified conversion')
        if module=='levels':
            desired={**obs['state']['channels'][p['channel']],**{k:v for k,v in p.items() if k!='channel'}}
            if desired['input_black']>=desired['input_white'] or desired['output_black']>=desired['output_white']:
                raise Refused('invalid_levels','Black point must be below white point')

    def _verify_tool(self,module,p,before,after):
        state=after['state']
        if module in {'curves','levels','color_balance','color_mixer','film_grain'}:
            if before['target']!=after['target']:
                raise Refused('target_changed','Structured adjustment changed target')
        if module=='film_grain':
            wanted={**before['state'],**p}
            if not near(state,wanted):raise RuntimeError('Film grain native readback mismatch')
        elif module=='curves':
            wanted=copy.deepcopy(before['state']);wanted['channels'][p['channel']]=p['points']
            if not near(state,wanted):raise RuntimeError('Curve native readback mismatch')
        elif module=='levels':
            wanted=copy.deepcopy(before['state']);wanted['channels'][p['channel']].update({k:v for k,v in p.items() if k!='channel'})
            if not near(state,wanted):raise RuntimeError('Levels native readback mismatch')
        elif module=='color_balance':
            prefix={'master':'master','shadows':'shadow','midtones':'midtone','highlights':'highlight'}[p['wheel']]
            wanted=copy.deepcopy(before['state'])
            for k in ('x','y','lightness'):
                if k in p:wanted['values'][prefix+'_'+k]=p[k]
            if not near(state,wanted,0.01):raise RuntimeError('Color balance native readback mismatch')
        elif module=='color_mixer':
            if p['mode']=='basic':
                wanted=copy.deepcopy(before['state'])
                wanted['colors'][p['color']].update({k:v for k,v in p.items() if k in {'hue','saturation','lightness'}})
                if not near(state,wanted):raise RuntimeError('Basic mixer native readback mismatch')
            else:
                old=before['state']['advanced'];new=state['advanced'];action=p['action'];i=p.get('index',len(old))
                if action=='remove':
                    if not near(new,old[:i]+old[i+1:]):raise RuntimeError('Advanced removal mismatch')
                else:
                    if len(new)!=len(old)+(action=='add'):raise RuntimeError('Advanced range count mismatch')
                    untouched=old if action=='add' else old[:i]+old[i+1:]
                    remaining=new[:-1] if action=='add' else new[:i]+new[i+1:]
                    if not near(untouched,remaining):raise RuntimeError('Unrelated Advanced ranges changed')
                    row=new[i];mapping={'hue':'hueChange','saturation':'saturationChange','lightness':'lightnessChange',
                        'smoothness':'smoothness','enabled':'enabled','hue_low':'hueStart','hue_high':'hueEnd',
                        'saturation_low':'saturationStart','saturation_high':'saturationEnd'}
                    if any(k in p and not near(row[v],p[k]) for k,v in mapping.items()):raise RuntimeError('Advanced parameter readback mismatch')
                    if 'rgb' in p and not near([row[x] for x in ('red','green','blue')],p['rgb']):raise RuntimeError('Advanced RGB readback mismatch')
        elif module=='layers':
            action=p['action']
            if action=='set_opacity' and state['opacity']!=p['opacity']:raise RuntimeError('Opacity readback mismatch')
            if action=='set_enabled' and state['enabled']!=p['enabled']:raise RuntimeError('Layer enabled readback mismatch')
            if action=='rename' and state['selected_layer']['name']!=p['name']:raise RuntimeError('Layer name readback mismatch')
            # Core independently validates identity/order transitions and created-layer ownership.
        elif module=='masks':
            if p['action'] in {'create_subject','create_background'}:
                return len(state['layers'])==len(before['state']['layers'])+1
            if p['action']=='luma_range':
                mapping={'shadow':'rangeLow','highlight':'rangeHigh','shadow_falloff':'falloffLow',
                    'highlight_falloff':'falloffHigh','radius':'radius','sensitivity':'sensitivity','inverted':'invert'}
                if any(not near(state['mask']['luma'][v],p[k]) for k,v in mapping.items()):raise RuntimeError('Luma native readback mismatch')
                return True
            return False
        return True

    def apply_module_action(self,module,patch,before,source):
        self._require_candidate(before)
        if module in MODULES:
            if patch['action']=='reset':
                if module=='white_balance':raise Refused('WB_DEFAULTS_UNAVAILABLE','Camera-neutral WB reset is not exposed as a module command; paste an observed snapshot')
                desired={k:0 for k in MODULES[module]}
            else:
                if source is None or not set(MODULES[module])<=source.get('values',{}).keys():raise Refused('wrong_source','Read a complete scalar module snapshot')
                desired={k:source['values'][k] for k in MODULES[module]}
            old=before['values']
        else:
            old=before['state']
            if patch['action']=='paste':
                if not source or source.get('module')!=module:raise Refused('wrong_source','Paste requires a complete snapshot of the same module')
                desired=copy.deepcopy(source['state'])
            elif module=='film_grain':desired={'type':'fine','impact':0,'granularity':50}
            elif module=='curves':desired={'channels':{c:[[0,0],[1,1]] for c in ('rgb','red','green','blue','luma')}}
            elif module=='levels':desired={'channels':{c:{'input_black':0,'input_white':255,'midtone':0,'output_black':0,'output_white':255} for c in ('rgb','red','green','blue')}}
            elif module=='color_balance':desired={'values':{k:0 for k in old['values']}}
            elif module=='color_mixer':
                desired=copy.deepcopy(old)
                desired['advanced']=[]
                for row in desired['colors'].values():row.update(hue=0,saturation=0,lightness=0)
            else:raise Refused('unsupported','This module has no reset/paste route')
        receipt=self.action_hook(module+'.'+patch['action'],lambda:self._call('module_apply',module=module,
            expected=before['target'],before_state=old,desired=desired,scalar_modules=MODULES),{'patch':patch})
        after=self._observation(receipt) if module in MODULES else self._structured_observation(receipt)
        actual=after['values'] if module in MODULES else after['state']
        if after['target']!=before['target'] or not near(actual,desired,0.05 if module=='white_balance' else 0.002):
            raise RuntimeError('Module reset/paste readback differs from requested snapshot')
        return {'status':'control_confirmed','after':after,'requested_result_verified':True,
                'evidence':after['evidence'],'model_verified':True}

    def navigation(self,action,request,observation):
        scope=request.get('scope','filtered')
        if observation and observation.get('navigation'):
            scope=observation['native_inventory_scope']
        inventory=self._call('inventory',scope=scope)
        fingerprint=digest({k:inventory[k] for k in ('instance','document','collection_id','items')})
        if request.get('collection_token') and request['collection_token']!=fingerprint:
            raise Refused('stale_collection','Collection membership, order or selection changed')
        if action=='list':
            offset,limit=request.get('offset',0),request.get('limit',200)
            rows=inventory['items'];end=min(offset+limit,len(rows))
            target={'instance':inventory['instance'],'document':inventory['document'],
                'collection':{'id':inventory['collection_id']},'image':{'path':None},'variant':{'id':None,'label':None},'layer':None}
            obs={'navigation':True,'target':target,'values':{'selected_ids':inventory['selected_ids'],'collection_token':fingerprint},
                 'native_inventory_scope':scope,'evidence':{'source':'capture_one_apple_events','model_verified':True}}
            return {'status':'ok','images':rows[offset:end],'collection_uuid':inventory['collection_id'],
                    'collection_token':fingerprint,'total':len(rows),'offset':offset,
                    'next_offset':end if end<len(rows) else None,'_navigation_observation':obs}
        if observation and observation.get('navigation'):
            if observation['values']['collection_token']!=fingerprint or observation['values']['selected_ids']!=inventory['selected_ids']:
                raise Refused('stale_selection','Collection or native selection changed')
        expected=None if observation.get('navigation') else observation['target']
        options={'variant_uuid':str(request.get('variant_uuid',request.get('variant_id','')))} if action=='select' else {'direction':action}
        receipt=self.action_hook('images.'+action,lambda:self._call('navigate',scope=scope,inventory={k:v for k,v in inventory.items() if k!='status'},expected=expected,**options))
        return {'status':'completed','after':self._observation(receipt),'model_verified':True}
