"""Protection tests for experimental routes; live host evidence is documented separately."""
import copy

import pytest

from c1ai.contracts import Refused, validate_structured_patch
from c1ai.macos_backend import MacOSBackend


def test_advanced_rejects_host_clamping_before_dispatch(tmp_path):
    backend=MacOSBackend(tmp_path)
    observation={'state':{'advanced':[{'red':180,'green':100,'blue':80}]}}
    with pytest.raises(Refused,match='0..100'):
        backend._validate_tool_patch('color_mixer',{'mode':'advanced','index':0,'saturation_low':-100},observation)
    backend._validate_tool_patch('color_mixer',{'mode':'advanced','index':0,'saturation_low':-30,'saturation_high':30},observation)


def test_feather_never_silently_treats_pixels_as_native_amount(tmp_path):
    backend=MacOSBackend(tmp_path)
    with pytest.raises(Refused,match='pixel'):
        backend._validate_tool_patch('masks',{'action':'feather','radius':5},{})
    backend._validate_tool_patch('masks',{'action':'feather','native_amount':5},{})
    for patch in ({'action':'feather','radius':5,'native_amount':5},{'action':'feather'},{'action':'feather','native_amount':101}):
        with pytest.raises(Refused):validate_structured_patch({'module':'masks','patch':patch})


@pytest.mark.parametrize('action',['fill','clear','invert','copy','feather'])
def test_mask_command_reply_does_not_verify_pixels(tmp_path,action):
    backend=MacOSBackend(tmp_path)
    assert backend._verify_tool('masks',{'action':action},{'target':{}},{'target':{},'state':{}}) is False


def test_completed_pixel_command_is_not_reconciled_as_not_applied(tmp_path,monkeypatch):
    backend=MacOSBackend(tmp_path)
    target={'document':{'path':'/session'},'image':{'path':'/image'},'variant':{'id':'candidate'}}
    obs={'target':target,'state':{'mask':{'pixels_readable':False}},'module':'masks','values':{}}
    record={'request':{'command':'adjust','module':'masks','patch':{'action':'invert'}},
            'target':target,'before':obs,'after':obs,
            'result':{'after':obs,'evidence':{'native_command_completed':True}},'actions':[]}
    monkeypatch.setattr(backend,'observe_module',lambda module:copy.deepcopy(obs))
    result=backend.reconcile(record)
    assert result['outcome']=='applied_not_restored'
    assert result['evidence']['requested_pixel_result_verified'] is False
    assert result['evidence']['replayed'] is False


def test_failed_pixel_command_with_no_pixel_readback_stays_unknown(tmp_path,monkeypatch):
    backend=MacOSBackend(tmp_path)
    target={'document':{'path':'/session'},'image':{'path':'/image'},'variant':{'id':'candidate'}}
    obs={'target':target,'state':{'mask':{'pixels_readable':False}},'module':'masks','values':{}}
    record={'request':{'command':'adjust','module':'masks','patch':{'action':'invert'}},
            'target':target,'before':obs,'after':obs,'actions':[]}
    monkeypatch.setattr(backend,'observe_module',lambda module:copy.deepcopy(obs))
    assert backend.reconcile(record)['outcome']=='unknown'


def test_original_unqualified_layer_cannot_be_renamed(tmp_path):
    backend=MacOSBackend(tmp_path)
    with pytest.raises(Refused,match='tagged'):
        backend._validate_tool_patch('layers',{'action':'rename','layer_ref':{'native_id':'existing:User layer'}},{})


def test_levels_cannot_cross_unmodified_endpoint(tmp_path):
    backend=MacOSBackend(tmp_path)
    obs={'state':{'channels':{'rgb':{'input_black':0,'input_white':240,'output_black':0,'output_white':255,'midtone':0}}}}
    with pytest.raises(Refused,match='below'):
        backend._validate_tool_patch('levels',{'channel':'rgb','input_black':245},obs)


@pytest.mark.parametrize('patch',[
    {'type':'unknown'}, {'impact':-1}, {'granularity':101}, {'impact':True},
    {'impact':float('nan')}, {'type':'fine','extra':1},
])
def test_grain_contract_rejects_invalid_values(patch):
    with pytest.raises(Refused):validate_structured_patch({'module':'film_grain','patch':patch})


@pytest.mark.parametrize('kind',['fine','silver_rich','soft','cubic','tabular','harsh'])
def test_grain_contract_native_types(kind):
    patch={'type':kind,'impact':25,'granularity':50}
    assert validate_structured_patch({'module':'film_grain','patch':patch})==patch


def test_grain_target_layer_cannot_be_bypassed(tmp_path):
    backend=MacOSBackend(tmp_path)
    with pytest.raises(Refused,match='global'):
        backend._validate_tool_patch('film_grain',{'impact':20},{'target':{'layer':{'native_id':'local'}}})


def test_failed_layer_routes_refuse_before_native_call(tmp_path):
    backend=MacOSBackend(tmp_path)
    for action in ('move','duplicate'):
        with pytest.raises(Refused,match='failed'):
            backend._validate_tool_patch('layers',{'action':action},{})


@pytest.mark.parametrize('matched,event_time,outcome',[(True,101,'applied_not_restored'),(False,101,'unknown'),(True,99,'unknown')])
def test_export_reconcile_requires_native_history_and_time(tmp_path,monkeypatch,matched,event_time,outcome):
    from PIL import Image
    from c1ai.contracts import digest
    backend=MacOSBackend(tmp_path)
    target={'layer':'Background','document':{'path':'/session'},'image':{'path':'/image'},'variant':{'id':'candidate'}}
    record={'request':{'command':'preview','kind':'render','max_size':128},'request_id':'export-one',
            'target':target,'created_at':100.5}
    path=tmp_path/'renders'/digest({'request_id':'export-one'})/'preview.jpg'
    path.parent.mkdir(parents=True)
    Image.new('RGB',(8,8)).save(path)
    monkeypatch.setattr(backend,'observe',lambda fields:{'target':target,'values':{}})
    monkeypatch.setattr(backend,'_call',lambda action,**kw:{'matched':matched,'event_time':event_time})
    result=backend.reconcile(record)
    assert result['outcome']==outcome
    assert result['evidence']['render_revision_verified'] is False
    assert result['evidence']['replayed'] is False
