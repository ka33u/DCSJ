"""Synthetic model/drawing identities; never embed private source catalog rows."""
import copy
import json
import sqlite3

import pytest
from dcsj import library, slot_matching as matching, slots


def application(**changes):
    return {'series': 'SYN', 'frame': '120M', 'poles': 4, 'power': '12', 'material': 'TEST',
            'stack_mm': 150, 'source': '人工测试!A2:H2',
            'geometry': {'stator_outer_mm': 200, 'bore_mm': 120, 'gap_mm': .5, 'rotor_outer_mm': 119}, **changes}


def model(identity='one', **changes):
    p = {'Q1': 36, 'b01': 2, 'hs0': 1, 'zs1': 30, 'bs1': 4, 'bs2': 6, 'hs12': 14}
    return {'record_id': identity, 'model': 'SYN-120M1-4', 'series': 'SYN', 'frame': '120M1',
            'primary': True, 'geometry': {'stator_outer_mm': 200, 'bore_mm': 120, 'gap_mm': .5, 'rotor_outer_mm': 119, 'hr0_mm': .8},
            'stack_mm': 150, 'material': 'TEST', 'input_sha256': 'b'*64,
            'source': {'path': 'synthetic/test.mdb', 'table': 'SYN', 'row': 2, 'sha256': 'c'*64},
            'parameters': {'stator': p, 'rotor': {'Q2': 28, 'zc': 2, 'bc02': 1, 'hr0': .8, 'zs2': 20,
                'br1': 5, 'br2': 2, 'br3': 0, 'br4': 0, 'hr12': 15, 'hr3': 0, 'hr4': 0}}, **changes}


def item(**changes):
    return {'reference': 'synthetic-ref', 'name': 'Synthetic slot', 'document': {
        'side': 'stator', 'module': 'reference', 'drawing': 'TEST-DRAWING',
        'parameters': {}, 'geometry': {}, 'applications': [application()], **changes}}


def index(*models):
    return {('SYN', '120M', 4, 12): list(models)}


def test_same_dimensions_deduplicate_versions_but_preserve_evidence_and_conflicts():
    a=model(); b=model('history', primary=False)
    result=matching.match_item(item(), index(a, b), 'token')
    assert result['status']=='unique' and len(result['candidates'])==1
    assert len(result['candidates'][0]['sources'])==2
    assert result['candidates'][0]['area']['value_mm2']>0
    changed=copy.deepcopy(a); changed['record_id']='variant'; changed['parameters']['stator']['bs1']=4.5
    conflict=matching.match_item(item(), index(a, b, changed), 'token')
    assert conflict['status']=='ambiguous' and conflict['geometry_matched_count']==2
    assert '尚未核对冲片原图' in conflict['note']


def test_specific_frame_power_series_and_poles_never_fall_back_to_name_similarity():
    for app in [application(frame='120M2'), application(power='13'), application(series='ANOTHER'), application(poles=6)]:
        assert matching.match_item(item(applications=[app]), index(model()), 'token')['status']=='unmatched'
    assert matching.match_item(item(applications=[application(frame='120M1')]), index(model()), 'token')['status']=='unique'


def test_geometry_disagreements_and_missing_values_are_explicit_not_auto_corrected():
    a=model(stack_mm=151); a['geometry']['gap_mm']=.55; a['geometry']['rotor_outer_mm']=118.9
    result=matching.match_item(item(), index(a), 'token')
    assert result['status']=='mismatch'
    source=result['candidates'][0]['sources'][0]
    assert [c['key'] for c in source['comparisons'] if not c['equal']]==['gap_mm','rotor_outer_mm','stack_mm']
    missing=matching.match_item(item(applications=[application(stack_mm=None)]), index(model()), 'token')
    assert missing['status']=='mismatch'
    assert matching.match_item(item(module='tx'), index(model()), 'token')['status']=='unsupported'


def test_index_reads_explicit_catalog_and_invalidates_when_file_changes(tmp_path, monkeypatch):
    path=tmp_path/'models.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE model_catalog (record_id,model,series,rated_power_kw,poles,is_primary,raw_json,source_file,source_table,row_number,source_sha256)')
        p={'D1':200, 'Di1':120, 'g':.5, 'L':150, **model()['parameters']['stator']}
        db.execute('INSERT INTO model_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   ('one','SYN-120M1-4','HSYN',12,4,1,json.dumps(p),'test.mdb','SYN',1,'a'*64))
    monkeypatch.setenv('DCSJ_CATALOG_PATH',str(path))
    stamp=matching._stamp(); idx=matching._index(stamp)
    assert idx[('SYN','120M',4,12)][0]['parameters']['stator']['bs1']==4
    with sqlite3.connect(path) as db:
        p['bs1']=5;db.execute('UPDATE model_catalog SET raw_json=?',(json.dumps(p),))
    assert matching._stamp()!=stamp
    assert matching._index(matching._stamp())[('SYN','120M',4,12)][0]['parameters']['stator']['bs1']==5


def test_candidate_draft_save_apply_and_snapshot_keep_model_and_excel_provenance(client, monkeypatch, parameters):
    original=library.save('slots','Drawing reference',item(side='rotor')['document'])
    a=model();stamp=('synthetic',1,2,3)
    monkeypatch.setattr(matching,'_stamp',lambda:stamp)
    monkeypatch.setattr(matching,'_index',lambda _:index(a))
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    response=client.get('/api/v1/slots/matches',params={'reference':original['reference']})
    assert response.status_code==200
    result=response.json()
    body={'reference':original['reference'],'record_id':'one','application':0,'catalog_token':result['catalog_token']}
    response=client.post('/api/v1/slots/matches/draft',json=body)
    assert response.status_code==200,response.text
    draft=response.json();doc=draft['document']
    assert doc['applications']==[]  # Other application diameters cannot replace this model's hr0 basis.
    assert doc['reference_area_mm2'] is None
    assert doc['parameters']['hr0']==.8 and doc['geometry']['hr0_mm']==.8
    assert doc['dimension_source']['application']['source']=='人工测试!A2:H2'
    saved=client.post('/api/v1/library/slots',json={'name':draft['name'],'document':doc}).json()
    assert slots.search('TEST-DRAWING',source='user')['total']==2
    applied=slots.apply(saved['reference'],parameters,include_geometry=True)
    snapshot=slots.snapshot(applied['parameters'],applied['binding'])
    assert snapshot['entries'][1]['document']['dimension_source']==doc['dimension_source']
    assert snapshot['entries'][1]['area']['value_mm2']>0
    assert slots.get(original['reference'])['document']['parameters']=={}
    assert client.post('/api/v1/slots/matches/draft',json={**body,'catalog_token':'0'*64}).status_code==409
    assert client.post('/api/v1/slots/matches/draft',json={**body,'record_id':'unrelated'}).status_code==404
    assert client.post('/api/v1/slots/matches/draft',json={**body,'application':1}).status_code==404


def test_summary_covers_all_reference_drawings_and_incomplete_sources_cannot_be_adopted(client,monkeypatch):
    a=model();del a['parameters']['stator']['bs2']
    monkeypatch.setattr(matching,'_stamp',lambda:('test',1,2,3))
    monkeypatch.setattr(matching,'_index',lambda _:index(a))
    monkeypatch.setattr(slots,'resource_entries',lambda:[item(),item(applications=[])])
    summary=client.get('/api/v1/slots/matching-summary').json()
    assert summary['total']==2 and summary['counts']['unmatched']==1
    monkeypatch.setattr(slots,'get',lambda _:item())
    result=matching.matches('test')
    assert result['candidates'][0]['missing_fields']==['bs2']
    response=client.post('/api/v1/slots/matches/draft',json={'reference':'test','record_id':'one','application':0,'catalog_token':result['catalog_token']})
    assert response.status_code==422


def test_negative_legacy_neck_marker_is_preserved_without_inventing_a_positive_baseline(monkeypatch):
    a=model();a['geometry']['hr0_mm']=-1;a['parameters']['rotor']['hr0']=-1
    monkeypatch.setattr(matching,'_stamp',lambda:('test',1,2,3))
    monkeypatch.setattr(matching,'_index',lambda _:index(a))
    monkeypatch.setattr(slots,'get',lambda _:item(side='rotor'))
    token=matching.matches('test')['catalog_token']
    draft=matching.draft('test','one',0,token)['document']
    assert draft['parameters']['hr0']==-1
    assert draft['geometry']['hr0_mm'] is None
    assert '保留参数原值' in draft['notes']
    monkeypatch.setattr(slots,'get',lambda _:item())
    assert matching.draft('test','one',0,token)['document']['geometry']['hr0_mm'] is None
