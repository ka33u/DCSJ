"""Synthetic drawing dimension and machining-reference regressions."""
import copy
import json
import sqlite3

import pytest
from dcsj import slots, library, slot_matching
from dcsj import slot_dimension_matching as matching
from dcsj.parameters import InputError


def item(side='rotor', **source_changes):
    p = ({'Q2':28,'zc':2,'bc02':1,'hr0':1.2,'zs2':20,'br1':5,'br2':2,'hr12':15,'hr3':0,'hr4':0}
         if side == 'rotor' else {'Q1':36,'b01':2,'hs0':1,'zs1':30,'bs1':4,'bs2':6,'hs12':14})
    g = {'rotor_outer_mm':120,'hr0_mm':1.2,'rotor_inner_mm':30} if side=='rotor' else {'stator_outer_mm':200,'bore_mm':120}
    source={'file':'synthetic.xlsx','sha256':'a'*64,'sheet':side,'row':8,'original_index':'1',
            'status':'尺寸已识别','parameters':dict(p),'hr0_basis':'blank_outer','hr0_diameter_mm':120,
            'blank_outer_mm':120,**source_changes}
    doc=slots.SlotDocument(side=side,module='main',drawing='TEST-SLOT',source='Synthetic workbook',
                          parameters=p,geometry=g,drawing_source=source).model_dump(mode='json')
    return {'name':'Synthetic slot','reference':'test','document':doc}


def model(entry, identity='one', **changes):
    p={**entry['document']['parameters'], 'D1':200,'Di1':120,'g':.5,'Di2':30,**changes}
    if entry['document']['side']=='rotor' and 'hr0' not in changes:p['hr0']=.7
    return {'record_id':identity,'model':'271TEST2','series':'TEST','primary':identity=='one',
            'parameters':p,'source':{'path':'synthetic.mdb','table':'TEST','row':2,'sha256':'b'*64}}


def compare(entry, *models):
    idx={'stator':{},'rotor':{}}
    for m in models:
        p=m['parameters'];key=p.get('Q1') if entry['document']['side']=='stator' else (p.get('Q2'),p.get('zc'))
        idx[entry['document']['side']].setdefault(key,[]).append(m)
    return matching.compare_item(entry,idx,'token')


def test_blank_hr0_converts_once_and_versions_and_shaft_conflicts_remain_visible():
    e=item();result=compare(e,model(e),model(e,'history'),model(e,'shaft',Di2=31))
    assert result['status']=='dimension_exact'
    assert result['matching_primary_versions']==result['matching_history_versions']==1
    assert result['matching_models']==['271TEST2']
    sources=result['candidates'][0]['sources']
    assert len(sources)==3
    assert sources[0]['hr0_adjustment']['adjusted_hr0']==pytest.approx(.7)
    conflict=next(s for s in sources if s['record_id']=='shaft')
    assert conflict['difference_count']==1 and not conflict['geometry_matches']
    assert [c['key'] for c in conflict['comparisons'] if not c['equal']]==['Di2']


def test_unknowns_pending_and_small_real_differences_never_become_confirmed():
    e=item('stator',review_required=True)
    assert compare(e,model(e))['status']=='dimension_partial'
    e=item('stator');del e['document']['parameters']['bs1']
    assert compare(e,model(item('stator')))['status']=='dimension_partial'
    e=item('stator')
    assert compare(e,model(e,bs1=4.02))['status']=='dimension_conflict'
    assert compare(e,model(e,bs2=-6))['status']=='dimension_conflict'
    assert compare(e,model(e,Q1=36.001))['status']=='dimension_unmatched'
    assert compare(e,model(e,bs1=4.01,zs1=30.1))['status']=='dimension_exact'
    e=item();e['document']['parameters']['zc']=4
    assert compare(e,model(e))['status']=='dimension_partial' # br3/br4 required for this type


def test_index_reads_all_names_and_numeric_strings_and_switches_catalog(tmp_path,monkeypatch):
    p=tmp_path/'test.sqlite';e=item('stator');m=model(e)
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE model_catalog (record_id,model,series,is_primary,raw_json,source_file,source_table,row_number,source_sha256)')
        db.execute('INSERT INTO model_catalog VALUES (?,?,?,?,?,?,?,?,?)',('one',m['model'],'TEST',1,json.dumps({k:str(v) for k,v in m['parameters'].items()}),'synthetic.mdb','TEST',2,'b'*64))
    monkeypatch.setenv('DCSJ_CATALOG_PATH',str(p));stamp=slot_matching._stamp()
    assert matching.matches(e,stamp,'a')['status']=='dimension_exact'
    with sqlite3.connect(p) as db:
        db.execute('UPDATE model_catalog SET raw_json=?',(json.dumps({**m['parameters'],'bs1':7}),))
    assert matching.matches(e,slot_matching._stamp(),'b')['status']=='dimension_conflict'


def test_apply_user_copy_application_and_snapshot_preserve_hr0_baseline(client,parameters):
    e=item();doc=e['document'];doc['applications']=[{'geometry':{'bore_mm':120,'gap_mm':.5,'rotor_outer_mm':119}}]
    saved=library.save('slots',e['name'],doc)
    applied=slots.apply(saved['reference'],parameters,application=0,include_geometry=True)
    p=applied['parameters']
    assert p['hr0']==pytest.approx(.7) and p['Di2']==30
    assert slots.get(applied['binding']['rotor'])['document']['geometry']['rotor_outer_mm']==120
    snap=slots.snapshot(p,applied['binding'])
    assert snap['entries'][1]['status']=='matched'
    assert snap['entries'][1]['document']['drawing_source']['parameters']['hr0']==1.2
    checks={c['key']:c for c in snap['geometry_check']['checks']}
    assert checks['hr0_link']['passed'] and checks['machining_allowance']['passed']
    assert slots.get(saved['reference'])['document']==doc or slots.get(saved['reference'])['document']['parameters']==doc['parameters']
    with pytest.raises(InputError):slots.apply(saved['reference'],{**p,'g':2})
    changed=copy.deepcopy(doc);changed['parameters']['hr0']=1.5
    second=library.save('slots','Edited',changed,saved['id'],1)
    newer=slots.apply(second['reference'],p)
    assert newer['parameters']['hr0']==pytest.approx(1)
    assert next(c for c in newer['geometry_check']['checks'] if c['key']=='hr0_link')['passed']
    assert slots.get(saved['reference'])['document']['parameters']['hr0']==1.2
    # Source dimensions cannot be replaced through the legacy application draft API.
    result=client.post('/api/v1/slots/matches/draft',json={'reference':saved['reference'],'record_id':'one','application':0,'catalog_token':'0'*64})
    assert result.status_code==422


def test_finished_od_and_edited_current_baseline_do_not_use_stale_source():
    e=item(hr0_basis='turned_outer',finished_outer_mm=119,hr0_diameter_mm=119)
    e['document']['geometry']['rotor_outer_mm']=119
    assert compare(e,model(e,hr0=1.2))['status']=='dimension_exact'
    e['document']['geometry']['rotor_outer_mm']=118
    assert compare(e,model(e,hr0=1.2))['status']=='dimension_unmatched'
    assert slots.effective_dimensions(e['document'],{'Di1':120,'g':1})['hr0']==1.2


def test_source_copy_requires_reference_od_and_integer_counts():
    from pydantic import ValidationError
    doc=item()['document'];doc['geometry']['rotor_outer_mm']=None
    with pytest.raises(ValidationError):slots.SlotDocument(**doc)
    doc=item()['document'];doc['drawing_source']['parameters']['Q2']=2.5
    with pytest.raises(ValidationError):slots.SlotDocument(**doc)
