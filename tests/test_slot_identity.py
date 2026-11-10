"""Synthetic complete-profile cleanup and automatic identity checks."""
import copy
import pytest
from dcsj import slots,slot_identity as ids,library


def document(side='rotor',**changes):
    p=({'Q2':28,'zc':2,'bc02':1,'hr0':.7,'zs2':20,'br1':5,'br2':2,'hr12':15,'hr3':0,'hr4':0}
       if side=='rotor' else {'Q1':36,'b01':2,'hs0':1,'zs1':30,'bs1':4,'bs2':6,'hs12':14})
    g={'rotor_outer_mm':119,'rotor_inner_mm':30} if side=='rotor' else {'stator_outer_mm':200,'bore_mm':120}
    return {'schema_version':2,'side':side,'module':'main','drawing':'SYN-1','parameters':p,'geometry':g,'aliases':[],**changes}


def inputs(doc):return {**doc['parameters'],'Di1':120,'g':.5,'Di2':30,'D1':200}


def test_same_lamination_with_gap_and_hr0_change_is_recognized_but_gap_alone_is_not(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    d=document();entry=ids.save_profile('Test rotor',d)
    p=inputs(d);changed={**p,'g':.8,'hr0':.4}
    assert ids.recognize(changed)['entries'][1]['name']=='Test rotor'
    assert ids.recognize({**p,'g':.8})['entries'][1]['status']=='unrecognized'
    other=copy.deepcopy(d);other['geometry']['rotor_outer_mm']=118.4;other['parameters']['hr0']=.4
    assert ids.signature(other)==ids.signature(d)
    copied=ids.save_profile('Test rotor',other,operation='copy',copied_from=entry['reference'])
    assert copied['id']!=entry['id'] and copied['revision']==1
    assert library.list_entries('slots')['total']==2
    assert slots.get(entry['reference'])['document']==d


def test_personal_copies_remain_separate_but_recognition_lists_all_matching_names(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    d=document('stator');library.save('slots','A',d)
    second=copy.deepcopy(d);second['drawing']='SYN-2';library.save('slots','B',second)
    library.save('slots','Incomplete',{'side':'stator','module':'reference','drawing':'bad'})
    profiles=ids.profiles();assert profiles['total']==2
    assert {i['drawing'] for item in profiles['items'] for i in ids.identities(item)}=={'SYN-1','SYN-2'}
    result=ids.recognize(inputs(d))['entries'][0]
    assert result['status']=='ambiguous' and len(result['identities'])==2
    assert ids.profiles(q='SYN-2')['total']==1


def test_save_rejects_missing_and_pending_dimensions_and_strips_removed_fields(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    d=document();d['parameters'].pop('hr12')
    assert client.post('/api/v1/slots/save',json={'name':'Bad','document':d}).status_code==422
    d=document();d.update(source='old text',reference_area_mm2=90,applications=[],notes='old note')
    result=client.post('/api/v1/slots/save',json={'name':'Clean','document':d})
    assert result.status_code==200,result.text
    assert set(result.json()['document'])=={'schema_version','side','module','drawing','parameters','geometry','aliases'}
    assert client.get('/api/v1/slots/profiles').json()['total']==1
    capture=client.post('/api/v1/slots/capture',json={'side':'rotor','parameters':inputs(d)})
    assert capture.status_code==200,capture.text
    assert capture.json()['geometry']=={'rotor_outer_mm':119,'rotor_inner_mm':30}
    from dcsj.parameters import InputError
    with pytest.raises(InputError):ids.validate_complete(document(module='reference',parameters={}))


def test_calculation_ignores_old_bindings_and_refreshes_names_even_with_cached_engine(client,monkeypatch,parameters,snapshot):
    import dcsj.api as api
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    d=document('stator');p={**parameters,**inputs(d)};result={**snapshot,'inputs':p}
    monkeypatch.setattr(api,'compute',lambda _:copy.deepcopy(result))
    saved=ids.save_profile('Before',d)
    body={'parameters':p,'persist':False,'slot_bindings':{'stator':'resource-slot:'+'f'*64+':missing'}}
    first=client.post('/api/v1/calculate',json=body)
    assert first.status_code==200,first.text
    assert first.json()['slots']['entries'][0]['name']=='Before'
    ids.save_profile('After',d,saved['id'],saved['revision'])
    second=client.post('/api/v1/calculate',json=body).json()
    assert second['slots']['entries'][0]['name']=='After'
    assert second['inputs']==p
    body['persist']=True
    frozen=client.post('/api/v1/calculate',json=body).json()
    ids.save_profile('Newest',d,saved['id'],2)
    assert client.get('/api/v1/calculations/'+frozen['calculation_id']).json()['slots']==frozen['slots']


def test_tx_dimensions_do_not_match_main_codes_or_units(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    ids.save_profile('Main',document('stator'))
    tx={'D1':20,'D3':12,'G':.05,'D4':3,'Q1':36,'b01':.2,'Hs0':.1,'DXJJ':30,'bs1':.4,'bs2':.6,'hs12':1.4}
    assert ids.recognize(tx,'tx')['entries'][0]['status']=='unrecognized'


def test_grouping_never_mutates_resource_aliases_or_retains_archived_names(client,monkeypatch):
    a={'id':'one','name':'Original','reference':'resource:one','origin':'resource','document':document()}
    b={**a,'id':'two','name':'Other','reference':'resource:two','document':document(drawing='SYN-2')}
    monkeypatch.setattr(slots,'resource_entries',lambda:[a,b])
    assert ids.profiles()['total']==1
    assert a['document']['aliases']==[] and b['document']['aliases']==[]
    user=library.save('slots','Temporary',document(drawing='USER'))
    assert any(i['name']=='Temporary' for i in ids.recognize(inputs(document()))['entries'][1]['identities'])
    library.archive('slots',user['id'],1,True)
    assert all(i['name']!='Temporary' for i in ids.recognize(inputs(document()))['entries'][1]['identities'])
    assert a['document']['aliases']==[]
