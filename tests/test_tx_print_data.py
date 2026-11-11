"""Snapshot display contracts using synthetic values, without private resources."""
import copy
from html.parser import HTMLParser

import pytest

from dcsj.tx_parameters import KEYS
from dcsj.tx_print_data import dimensions, material, slot_fills, winding
from dcsj.tx_report import electromagnetic, parameter_columns, slot_dimensions


@pytest.mark.parametrize('labels', [(41,42,43),(51,52,53),(61,62,63,64)])
def test_all_native_slot_fill_variants_are_preserved(labels):
    line='  '.join(f'SF({label})= {value}' for label,value in zip(labels,['.52','6.1e-1','.7','0']))
    text='Free note SF(99)= .99\n低谐波绕组数据\n'+line+'\n计算数据\nSF(98)= .98'
    values=slot_fills({'report_text':text})
    assert [v['label'] for v in values]==[f'SF({label})' for label in labels]
    assert [v['value'] for v in values]==[.52,.61,.7,0][:len(labels)]


def test_slot_fill_parser_ignores_notes_invalid_values_and_absent_history():
    text='低谐波绕组数据\nSF(11)=.95\nUser text\n低谐波绕组数据\nSF(51)=.51 trailing\nSF(52)=1e999\nSF(53)=.53\n计算数据'
    assert slot_fills({'report_text':text})==[{'label':'SF(53)','value':.53}]
    for text in [None,123,'SF(51)=.8','低谐波绕组数据\nSF(51)=nan']:
        assert slot_fills({'report_text':text})==[]
    assert slot_fills({})==[]


def test_material_and_dimensions_use_frozen_inputs_with_distinct_gap_and_neck():
    p={'GGP':600,'D3':12.3,'G':.04,'hr0':.065,'b01':.3,'CI':.025,'DXJJ':30,'M2':3,'D6':.8}
    before=copy.deepcopy(p);mm=dimensions(p)
    assert material(p)=='DW600'
    assert mm['D3']==123 and mm['D2']==pytest.approx(122.2)
    assert mm['G']==pytest.approx(.4) and mm['hr0']==pytest.approx(.65)
    assert mm['b01']==3 and mm['CI']==.25
    assert mm['DXJJ']==30 and mm['M2']==3 and mm['D6']==.8
    assert p==before
    assert dimensions({})['D2'] is None
    assert dimensions({'D3':12,'G':None})['D2'] is None
    for value in [None,True,'600',float('inf')]:assert material({'GGP':value})=='—'


def test_winding_summary_keeps_wire_millimetres_and_unused_second_gauge():
    p={'GGP':600,'N6':3,'D6':1.12,'N7':0,'D7':.85,'A1':2}
    r={'report_text':'低谐波绕组数据\nSF(61)=.6 SF(62)=.7 SF(63)=.75 SF(64)=.8\n计算数据'}
    before=copy.deepcopy((p,r));value=winding(p,r)
    assert value['wires']==[{'group':1,'parallel_wires':3,'diameter_mm':1.12,'used':True},
                            {'group':2,'parallel_wires':0,'diameter_mm':.85,'used':False}]
    assert value['parallel_paths']==2 and value['material']=='DW600'
    assert len(value['slot_fills'])==4 and value['slot_fills'][-1]['label']=='SF(64)'
    assert (p,r)==before
    assert winding({}, {})['wires'][0]['used'] is None


def test_api_enriches_historical_snapshot_without_mutating_saved_json(client,monkeypatch):
    from dcsj import tx,storage
    p={'GGP':600,'N6':2,'D6':.76,'N7':1,'D7':.65,'A1':1}
    r={'report_text':'低谐波绕组数据\nSF(51)=.6123\n计算数据','details':{'stator_tooth_flux_g':14567}}
    with storage.connect() as db:
        db.execute('INSERT INTO tx_calculations VALUES(?,?,?,?,?)',('old-wires','Old snapshot',storage.dumps(p),storage.dumps(r),storage.now()))
    before=tx.calculation('old-wires')
    monkeypatch.setattr(tx,'context',lambda:(_ for _ in ()).throw(AssertionError('Display must not load resources')))
    actual=client.get('/api/v1/tx/calculations/old-wires').json()
    assert actual['result']['winding']==winding(p,r)
    assert actual['result']['details']==r['details']
    assert tx.calculation('old-wires')==before


def test_engineering_summary_preserves_precision_and_missing_values():
    result={'details':{'current_density_a_mm2':4.1234,'resistance_ohm':.2345,'stator_tooth_flux_g':13567,
                       'stator_slot_area_mm2':123.456},
            'metrics':{'aj':987.6,'slip':.0234},
            'report_text':'低谐波绕组数据\nSF(51)=.5123 SF(52)=.6123 SF(53)=.7123\n计算数据'}
    before=copy.deepcopy(result);html=electromagnetic(result)
    assert all(s in html for s in ['4.1234 A/mm²','0.2345 Ω','0.0234','13567','987.6','51.23%','61.23%','71.23%'])
    assert '转子齿 Brt2 / G' in html and '—' in html
    assert 'SF —' in electromagnetic({'details':{},'metrics':{}})
    dims=slot_dimensions({'D3':12.3,'G':.04,'hr0':.065},result)
    assert all(s in dims for s in ['122.2 mm','0.4 mm','0.65 mm','123.456 mm²'])
    assert result==before


class TableCells(HTMLParser):
    def __init__(self):super().__init__();self.cells=[];self.rows=[];self.row=[];self.inside=False;self.text=''
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag=='td':self.inside=True;self.text=''
    def handle_data(self,data):
        if self.inside:self.text+=data
    def handle_endtag(self,tag):
        if tag=='td':self.inside=False;self.cells.append(self.text);self.row.append(self.text)
        if tag=='tr' and self.row:self.rows.append(self.row)


def test_rebalanced_columns_keep_every_scalar_input_exactly_once():
    p=dict.fromkeys(KEYS,1.0)
    p.update(TYP='Synthetic <name>',description='<script>note</script>',FE=1.2345678901234567)
    html=parameter_columns(p);parser=TableCells();parser.feed(html)
    symbols=[row[1] for row in parser.rows if len(row)==4]
    for key in KEYS:
        if key not in {'TYP','description'} and not key.startswith(('NDXP(','YDXP(')):
            assert symbols.count(key)==1,key
    assert parser.cells.count('型号 / TYP Synthetic <name>')==1
    assert str(p['FE']) in parser.cells
    assert '&lt;script&gt;note&lt;/script&gt;' in html and '<script>' not in html
