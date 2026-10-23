"""Synthetic geometry and revision tests; no proprietary slot catalog rows."""
import copy
import math
import pytest
from dcsj import slots, library, projects
from dcsj.parameters import InputError


def document(**changes):
    return {'side':'rotor','module':'main','drawing':'SYN-R-1','source':'Synthetic geometry',
            'parameters':{'Q2':28,'zc':1,'bc02':2,'hr0':1,'zs2':0,'br1':2,'br2':2,'hr12':8},
            'geometry':{'bore_mm':100,'gap_mm':.5,'rotor_outer_mm':99,'hr0_mm':1}, **changes}


def test_slot_revisions_and_search_keep_fixed_reference(client, monkeypatch):
    monkeypatch.setattr(slots, 'resource_entries', lambda: [])
    one=library.save('slots','Synthetic shape',document())
    updated=document();updated['parameters']['br2']=3
    two=library.save('slots','Synthetic shape',updated,one['id'],1)
    assert slots.get(one['reference'])['document']['parameters']['br2']==2
    assert slots.get(two['reference'])['document']['parameters']['br2']==3
    assert slots.search('SYN-R-1',side='rotor')['total']==1
    assert slots.search('SYN-R-1',side='stator')['total']==0
    assert slots.search('SYN-R-1',module='tx')['total']==0
    library.archive('slots',one['id'],2,True)
    assert slots.search()['total']==0
    assert slots.search(include_archived=True)['total']==1
    assert slots.get(one['reference'])['revision']==1
    assert client.put('/api/v1/library/slots/'+one['id'],json={'name':'Conflict','document':document(),'expected_revision':1}).status_code==409


def test_apply_preserves_unrelated_parameters_and_tracks_modified_snapshot(client, parameters):
    entry=library.save('slots','Synthetic shape',document())
    original=copy.deepcopy(parameters)
    result=slots.apply(entry['reference'],parameters)
    assert parameters==original
    assert result['parameters']['Di1']==parameters['Di1']  # Geometry is opt in.
    assert result['parameters']['Q1']==parameters['Q1']
    applied=result['parameters']
    snap=slots.snapshot(applied,result['binding'])
    assert snap['entries'][1]['status']=='matched'
    assert snap['entries'][1]['area']['value_mm2']==pytest.approx(18)
    applied['hr12']=9
    changed=slots.snapshot(applied,result['binding'])
    assert changed['entries'][1]['changed_fields']==['hr12']
    assert changed['entries'][1]['area']['value_mm2']==pytest.approx(20)
    assert snap['entries'][1]['area']['value_mm2']==pytest.approx(18)


def test_reference_only_does_not_invent_slot_dimensions_and_freezes_application(client, parameters):
    d=document(module='reference',parameters={},geometry={},applications=[{
        'series':'Synthetic','frame':'F','geometry':{'bore_mm':100,'gap_mm':.5,'rotor_outer_mm':99,'stator_outer_mm':150}}])
    item=library.save('slots','Drawing only',d)
    result=slots.apply(item['reference'],parameters,application=0,include_geometry=True)
    assert result['parameters']['hr0']==parameters['hr0']
    assert result['parameters']['g']==.5 and result['parameters']['Di1']==100
    assert result['binding']['rotor'].endswith('#app0')
    snap=slots.snapshot(result['parameters'],result['binding'])
    assert snap['entries'][1]['status']=='reference_only'
    assert not snap['geometry_check']['hr0_reference_available']
    assert any(c['key']=='reference_diameter' for c in snap['geometry_check']['checks'])


def test_geometry_link_is_conditional_and_accounts_for_bore_change():
    reference={'rotor_outer_mm':99,'hr0_mm':1}
    result=slots.geometry_check({'Di1':100,'g':.7,'hr0':1},reference=reference)
    link=next(c for c in result['checks'] if c['key']=='hr0_link')
    assert link['expected_hr0_mm']==pytest.approx(.8) and not link['passed']
    ok=slots.geometry_check({'Di1':101,'g':.7,'hr0':1.3},reference=reference)
    assert next(c for c in ok['checks'] if c['key']=='hr0_link')['passed']
    bad=slots.geometry_check({'Di1':100,'g':2,'hr0':1},reference=reference)
    assert next(c for c in bad['checks'] if c['key']=='hr0_link')['expected_hr0_mm']<0


def test_tx_geometry_uses_cm_but_library_reference_and_area_are_mm(client):
    item=library.save('slots','Reference',document(module='reference',parameters={}))
    p={'D3':10.,'G':.05,'D1':15,'hr0':.1}
    result=slots.apply(item['reference'],p,module='tx',include_geometry=True)
    assert result['parameters']['D3']==10 and result['parameters']['G']==.05
    assert slots.geometry(p,'tx')['rotor_outer_mm']==99
    main=library.save('slots','Main only',document())
    with pytest.raises(InputError):slots.apply(main['reference'],p,module='tx')
    snap=slots.snapshot(p,module='tx',details={'stator_slot_area_mm2':37})
    assert snap['entries'][0]['area']['value_mm2']==37
    assert snap['entries'][1]['area']['value_mm2'] is None


def test_stator_flat_and_round_areas_and_unsupported_geometry():
    p={'b01':2,'hs0':1,'zs1':45,'bs1':4,'bs2':-4,'hs12':10}
    # Neck 2 + shoulder (2+4)/2*1 + body 4*9.
    assert slots.areas(p)['stator']['value_mm2']==pytest.approx(41)
    p['bs2']=4
    assert slots.areas(p)['stator']['value_mm2']==pytest.approx(41+2*math.pi,rel=0.0001)
    assert slots.areas({'zc':1,'bc02':-2})['rotor']['value_mm2'] is None


def test_project_binding_roundtrip_version_guard_and_frozen_result(client, parameters, snapshot, monkeypatch):
    import dcsj.api as api
    monkeypatch.setattr(api,'compute',lambda p:copy.deepcopy(snapshot))
    entry=library.save('slots','Synthetic shape',document())
    binding={'rotor':entry['reference']}
    project=client.post('/api/v1/projects',json={'name':'Design','parameters':parameters,'slot_bindings':binding}).json()
    assert project['slot_bindings']==binding
    calc=client.post('/api/v1/calculate',json={'parameters':parameters,'project_id':project['id'],'project_revision':1}).json()
    assert calc['slot_bindings']==binding
    frozen=copy.deepcopy(calc['slots'])
    d=document();d['parameters']['br2']=3
    library.save('slots','Changed',d,entry['id'],1)
    assert client.get('/api/v1/calculations/'+calc['calculation_id']).json()['slots']==frozen
    assert client.post('/api/v1/calculate',json={'parameters':parameters,'project_id':project['id'],'project_revision':1,'slot_bindings':{}}).status_code==409
    newer=client.put('/api/v1/projects/'+project['id'],json={'expected_revision':1,'name':'Design','parameters':parameters}).json()
    assert newer['slot_bindings']==binding
    detached=client.put('/api/v1/projects/'+project['id'],json={'expected_revision':2,'name':'Design','parameters':parameters,'slot_bindings':{}}).json()
    assert detached['slot_bindings']=={}
    assert projects.get_revision(project['id'],1)['slot_bindings']==binding


@pytest.mark.parametrize('change',[{'parameters':{'Ufai':123}}, {'module':'reference'}, {'parameters':{'Q2':2.5}}, {'side':'stator'}, {'reference_area_mm2':-1}])
def test_invalid_slot_document_returns_422(client,change):
    result=client.post('/api/v1/library/slots',json={'name':'Invalid','document':document(**change)})
    assert result.status_code==422


def test_slot_api_and_report_escape_reference(client,parameters):
    from dcsj.report_summary import slot_rows
    entry=library.save('slots','<script>synthetic</script>',document())
    result=client.post('/api/v1/slots/preview',json={'parameters':parameters,'slot_bindings':{'rotor':entry['reference']}})
    assert result.status_code==200
    assert slot_rows({'slots':result.json()})[1][1].startswith('<script>')  # Renderers escape all cells.
    assert client.post('/api/v1/slots/apply',json={'parameters':parameters,'reference':entry['reference'],'application':1}).status_code==404
