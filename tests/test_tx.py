"""Synthetic contracts for the optional module; no recovered model fixtures."""
import copy
import gzip
import json
import math
from pathlib import Path
import pytest
from dcsj import tx, storage, resources
from dcsj.tx_parameters import KEYS, validate, TXInputError, schema
from dcsj.tx_vm import TXVM, load_program, format_number
from dcsj.numeric_vm import NumericKernelError, NumericKernelLimitError
from dcsj.tx_report import report


@pytest.fixture
def tx_parameters():
    p=dict.fromkeys(KEYS,1.0)
    p.update(TYP='Synthetic concentric sample',description='Invented data for API tests',P2=7.2,P=4,Q1=36,Q2=28,
             U1=400,HZ=60,D1=30,D3=18,D4=6,G=.05,L=16,bs1=.7,bs2=.9,hs12=1.8,
             A1=2,N6=2,N7=0,D6=.8,D7=0,E4=.9,E6=.8,DYXS=.96,Y=0,M1=1,M2=2)
    for i in range(1,7):p[f'NDXP({i})']=8 if i<4 else 0;p[f'YDXP({i})']=10-i if i<4 else 0
    return p


def synthetic_program():
    return {'image_base':0x400000,'image_size':0x30000,'stack_base':0x1000000,'stack_size':0x100000,
            'initial_ebp':0x10ff000,'initial_segments':[],'entry':0x402000,'exit':0x402004,
            'imports':{0x401000:'__vbaVarSub'},'io_skips':{},'arrays':[],'bindings':[],'strings':{},'code':{}}


def test_parameter_units_and_reject_invalid(tx_parameters):
    assert len(KEYS)==len(set(KEYS))==96
    assert validate(tx_parameters)['U1']==400
    units={f['key']:f['unit'] for f in schema()['fields']}
    assert units['D3']=='cm' and units['D6']=='mm' and units['P2']=='kW' and '线电压' in units['U1']
    for key,value in [('U1',True),('HZ',float('nan')),('D3',40),('P',3),('Y',8),('E4',90),('N6',0),('NDXP(1)',1.25)]:
        with pytest.raises(TXInputError):validate({**tx_parameters,key:value})
    half={**tx_parameters,'NDXP(1)':8.5}
    assert validate(half)['NDXP(1)']==8.5
    with pytest.raises(TXInputError):validate({**tx_parameters,'Ufai':200})
    with pytest.raises(TXInputError):validate({**tx_parameters,'NDXP(2)':0})


def test_variant_operand_order_rounding_and_isolation():
    p=synthetic_program();v=TXVM(p,{});other=TXVM(p,{})
    out,right,left=0x100c000,0x100c010,0x100c020
    v.put(left,9.5);v.put(right,4);v.push(left);v.push(right);v.push(out);v.push(0x402100);v.pc=0x401000
    v.callback();assert v.val(out)==5.5;assert other.val(out)==0
    for number,expected in [(2.5,2),(3.5,4),(-2.5,-2)]:
        v.program['imports'][0x401004]='__vbaI4Var';v.put(left,number);v.push(left);v.push(0x402100);v.pc=0x401004
        v.callback();assert v.reg('eax')==expected&0xffffffff
    with pytest.raises(NumericKernelError):v.put(out,float('inf'))
    with pytest.raises(NumericKernelError):v.memory.read(0,4)


def test_print_descriptor_preserves_zero_and_integer_variants():
    v=TXVM(synthetic_program(),{});v.program['imports'][0x401004]='__vbaPrintFile'
    descriptor=0x402000;number=0x100c000;v.put(number,0)
    v.memory.write(descriptor,bytes([3,0,0x48,0x61,0x8c]));label=v.bstr('count=')
    v.program['code'][0x402100]=(0x402101,'add',[['r','esp',4],['i',20,4]])
    for arg in [number,20,label,2,descriptor,0x402100]:v.push(arg)
    v.pc=0x401004;v.callback();assert v.printed[0]['parts']==['count=',20,0]
    assert tx._report_text(v.printed).endswith('0')


def test_vm_budget_and_forbidden_host_operations():
    p=synthetic_program();p['code']={p['entry']:(p['entry'],'jmp',[['i',p['entry'],4]])};v=TXVM(p,{})
    v.budget=8
    with pytest.raises(NumericKernelLimitError):v.run()
    p=synthetic_program();p['imports'][p['entry']]='__vbaFileOpen';v=TXVM(p,{})
    with pytest.raises(NotImplementedError):v.run()
    assert format_number(.123456,'.####')=='0.1235'


def test_user_design_revision_conflict_and_backup_compatibility(client,tx_parameters,tmp_path,monkeypatch):
    first=client.post('/api/v1/tx/designs',json={'parameters':tx_parameters})
    assert first.status_code==201;item=first.json();identity=item['id']
    updated={**tx_parameters,'description':'Changed by a user'}
    response=client.put('/api/v1/tx/designs/'+identity,json={'parameters':updated,'expected_revision':1})
    assert response.status_code==200 and response.json()['revision']==2
    assert client.put('/api/v1/tx/designs/'+identity,json={'parameters':updated,'expected_revision':1}).status_code==409
    assert client.get('/api/v1/tx/designs/'+identity+'?revision=1').json()['parameters']['description']==tx_parameters['description']
    assert client.get('/api/v1/tx/designs').json()['items'][0]['revision']==2
    assert client.post('/api/v1/tx/validate',json={'parameters':{**tx_parameters,'Ufai':10}}).status_code==422
    # A resource change cannot touch the TX user tables.
    monkeypatch.setattr(resources,'current',lambda:{'package_id':'a'*64,'version':'new','files':{}})
    assert tx.design(identity)['parameters']==validate(updated)
    assert client.get('/api/v1/tx/designs/missing').status_code==404
    with storage.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==storage.SCHEMA_VERSION
        assert db.execute('SELECT count(*) FROM tx_revisions').fetchone()[0]==2


def test_missing_resources_and_standalone_routes(client,tmp_path,monkeypatch):
    monkeypatch.setenv('DCSJ_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setattr(resources,'asset',lambda name,package=None:tmp_path/name)
    assert client.get('/api/v1/tx/status').json()['available'] is False
    assert client.get('/api/v1/tx/models').status_code==503
    for path in ['/tx','/tx.js','/tx.css']:
        response=client.get(path);assert response.status_code==200 and response.headers['cache-control']=='no-cache'
    assert '/api/v1/tx/calculate' in client.get('/openapi.json').json()['paths']
    assert client.get('/tx/reports/missing').status_code==404


def test_legacy_schema_migration_preserves_main_and_tx(client,tx_parameters):
    first=tx.save_design(tx_parameters)
    with storage.connect() as db:
        db.execute('PRAGMA user_version=5')
    assert tx.design(first['id'])['parameters']==validate(tx_parameters)
    with storage.connect() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==storage.SCHEMA_VERSION


def test_calculation_failures_are_not_saved(client,tx_parameters,monkeypatch):
    monkeypatch.setattr(tx,'context',lambda:{'paths':{'tx_kernel.json.gz':'synthetic'},'files':{'tx_kernel.json.gz':{'sha256':'synthetic'}}})
    monkeypatch.setattr(tx,'load_program',lambda *a:synthetic_program())
    monkeypatch.setattr(TXVM,'run',lambda self:(_ for _ in ()).throw(OverflowError('synthetic overflow')))
    response=client.post('/api/v1/tx/calculate',json={'parameters':tx_parameters})
    assert response.status_code==422 and 'synthetic overflow' in response.json()['detail']
    assert client.get('/api/v1/tx/calculations').json()['items']==[]


def test_snapshot_report_is_escaped_and_independent_of_resources(client,tx_parameters,monkeypatch):
    p={**tx_parameters,'TYP':'<script>alert(1)</script>'}
    fake={'metrics':dict(efficiency=.88,power_factor=.81,maximum_torque_ratio=2.7,starting_torque_ratio=2.1,
                         starting_current_ratio=6.8,speed_rpm=1700),
          'harmonics':[dict(order=i,winding_factor=.1,relative_mmf_percent=.5,turns=8,pitch=9,coil_index=n) for n,i in enumerate([1,5,7,11,13,17],1)],
          'coils':[dict(index=1,span_cm=12,radius_cm=8,end_angle_rad=.8,height_cm=6)],
          'details':dict(total_loss_w=400,current_a=15,copper_mass_kg=3,iron_mass_kg=20),
          'report_text':'<img src=x onerror=alert(1)>','application_version':'synthetic','resource_version':'synthetic',
          'input_sha256':'0'*64,'kernel_sha256':'1'*64}
    monkeypatch.setattr(tx,'context',lambda:{'paths':{'tx_kernel.json.gz':'synthetic'},'files':{'tx_kernel.json.gz':{'sha256':'synthetic'}}})
    monkeypatch.setattr(tx,'load_program',lambda *a:synthetic_program())
    monkeypatch.setattr(TXVM,'run',lambda self:None)
    monkeypatch.setattr(tx,'_result',lambda *a:copy.deepcopy(fake))
    trial=client.post('/api/v1/tx/calculate',json={'parameters':p,'persist':False})
    assert trial.status_code==200 and trial.json()['id'] is None
    assert not tx.calculations()
    saved=client.post('/api/v1/tx/calculate',json={'parameters':p}).json()
    fake['metrics']['efficiency']=.12
    assert tx.calculation(saved['id'])['result']['metrics']['efficiency']==.88
    monkeypatch.setattr(tx,'context',lambda:(_ for _ in ()).throw(tx.TXUnavailableError('offline')))
    output=client.get('/tx/reports/'+saved['id'])
    assert output.status_code==200 and '&lt;script&gt;' in output.text and '<script>alert' not in output.text
    assert '&lt;img' in output.text and '<img src=x' not in output.text
    assert '输入参数快照' in output.text and 'TY / cm' in output.text


def test_author_and_saved_catalog_without_private_resources(client, tx_parameters, monkeypatch):
    def unavailable():raise tx.TXUnavailableError('Synthetic resource absence')
    monkeypatch.setattr(tx,'context',unavailable)
    url='/api/v1/tx'
    created=client.post(url+'/designs',json={'parameters':tx_parameters,'author':'  张工  '})
    assert created.status_code==201
    d=created.json();assert d['author']=='张工'
    for q in ('张工',tx_parameters['TYP']):
        result=client.get(url+'/models',params={'source':'saved','q':q}).json()
        assert result['total']==1 and result['items'][0]['author']=='张工'
    reference=result['items'][0]['id']
    loaded=client.get(url+'/models/'+reference).json()['design']
    assert loaded['id']==d['id'] and loaded['parameters']==tx_parameters
    update={'parameters':{**tx_parameters,'TYP':'New name'},'expected_revision':1}
    assert client.put(url+'/designs/'+d['id'],json=update).json()['author']=='张工'
    update.update(expected_revision=2,author='李工')
    assert client.put(url+'/designs/'+d['id'],json=update).json()['author']=='李工'
    old=client.get(url+'/models/'+reference).json()['design']
    assert old['author']=='张工' and old['name']==tx_parameters['TYP'] and old['revision']==1
    assert client.get(url+'/models?source=saved&q=张工').json()['total']==0
    assert client.get(url+'/models?source=saved&q=李工').json()['total']==1
    update.update(expected_revision=3,author='')
    assert client.put(url+'/designs/'+d['id'],json=update).json()['author']==''
    assert client.put(url+'/designs/'+d['id'],json=update).status_code==409
    assert client.get(url+'/models?source=saved&q=%25').json()['total']==0
    assert client.get(url+'/models?source=saved&offset=1').json()['items']==[]
