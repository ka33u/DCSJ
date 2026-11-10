"""Compare supplied drawing dimensions with every immutable catalog version."""
from collections import defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from . import catalog, slots
from .parameters import input_parameters

METHOD = 'drawing-dimensions-v1'
LABELS = {'dimension_exact':'槽尺寸及外形一致', 'dimension_partial':'已知尺寸相符，资料待核',
          'dimension_conflict':'有对应规格，尺寸有差异', 'dimension_unmatched':'未找到同外形规格'}
NOTE = ('逐项比较槽数、槽型、槽宽、槽高、角度和外形，不按型号名称认定相同。'
        '毫米项差值不超过 0.01、角度不超过 0.1°，槽数与槽号须相同；这是录入精度比对，不是加工公差。'
        '原冲片外圆用于筛选配套定子内径，hr0 按当前计算外径换算；历史版本与默认版本分别保留。')


def number(value):
    from .slot_matching import number as finite
    return finite(value)


def equal(key, left, right):
    left, right = number(left), number(right)
    tolerance = 0 if key in ('Q1','Q2','zc') else .1 if key in ('zs1','zs2') else .01
    return left is not None and right is not None and abs(left-right) <= tolerance + (0 if tolerance==0 else 1e-9)


def required_keys(side, parameters):
    if side=='stator':return slots.MAIN_KEYS[side]
    common = 'Q2 zc bc02 hr0 zs2 br1 br2 hr12 hr3 hr4'.split()
    return common + (['br3','br4'] if parameters.get('zc') not in (1,2,3) else [])


@lru_cache(maxsize=3)
def index(stamp):
    result={'stator':defaultdict(list), 'rotor':defaultdict(list)}
    with catalog.connect(Path(stamp[0])) as db:
        for row in db.execute('SELECT record_id,model,series,is_primary,raw_json,source_file,source_table,row_number,source_sha256 FROM model_catalog ORDER BY record_id'):
            raw=input_parameters(json.loads(row['raw_json']))
            keys=set(slots.MAIN_KEYS['stator']+slots.MAIN_KEYS['rotor'][:12]+['D1','Di1','g','Di2'])
            p={k:number(raw.get(k)) for k in keys}
            model={'record_id':row['record_id'], 'model':row['model'], 'series':row['series'],
                   'primary':bool(row['is_primary']), 'parameters':p,
                   'source':{'path':row['source_file'],'table':row['source_table'],'row':row['row_number'],'sha256':row['source_sha256']}}
            result['stator'][p.get('Q1')].append(model)
            result['rotor'][(p.get('Q2'),p.get('zc'))].append(model)
    return result


def _candidates(doc, models, reference):
    side=doc['side']; g=doc['geometry']; source=doc['drawing_source']
    if side=='stator':
        rows=models['stator'].get(reference.get('Q1'),[])
        return [m for m in rows if equal('D1',g.get('stator_outer_mm'),m['parameters'].get('D1'))
                and equal('Di1',g.get('bore_mm'),m['parameters'].get('Di1'))]
    rows=models['rotor'].get((reference.get('Q2'),reference.get('zc')),[])
    selected=[]
    for model in rows:
        p=model['parameters']; bore,gap=p.get('Di1'),p.get('g')
        if bore is None or gap is None:continue
        if source.get('hr0_basis') == 'turned_outer':
            accept=equal('D2',g.get('rotor_outer_mm'),bore-2*gap)
        elif source.get('finished_outer_mm') is not None:
            accept=equal('D2',source['finished_outer_mm'],bore-2*gap)
        else:
            accept=equal('Di1',g.get('rotor_outer_mm'),bore)
        if accept:selected.append(model)
    return selected


def compare_item(item, models, token):
    doc=item['document']; source=doc['drawing_source']; side=doc['side']
    reference=dict(doc['parameters'] or source['parameters'])
    required=required_keys(side,reference)
    missing=[k for k in required if reference.get(k) is None]
    groups={}
    for model in _candidates(doc,models,reference):
        p=model['parameters']; expected=dict(reference)
        dimensions={k:p[k] for k in slots.MAIN_KEYS[side] if p.get(k) is not None}
        adjustment=None
        if side=='stator':
            expected.update(D1=doc['geometry'].get('stator_outer_mm'),Di1=doc['geometry'].get('bore_mm'))
        else:
            d2=p['Di1']-2*p['g']
            basis=doc['geometry'].get('rotor_outer_mm')
            if expected.get('hr0') is not None and source.get('hr0_diameter_mm') is not None and basis is not None:
                original=expected['hr0'];expected['hr0']+=(d2-basis)/2
                adjustment={'original_hr0':original,'reference_diameter_mm':basis,
                            'model_diameter_mm':d2,'adjusted_hr0':expected['hr0'], 'basis':source['hr0_basis']}
            expected['Di2']=doc['geometry'].get('rotor_inner_mm')
        comparisons=[]
        for key in dict.fromkeys([*required,*expected]):
            a,b=expected.get(key),p.get(key)
            comparisons.append({'key':key,'label':key,'reference':a,'model':b,
                                'unit':'°' if key in ('zs1','zs2') else '' if key in ('Q1','Q2','zc') else 'mm',
                                'equal':equal(key,a,b)})
        disagreements=sum(not c['equal'] for c in comparisons if c['reference'] is not None and c['model'] is not None)
        absent=[c['key'] for c in comparisons if c['reference'] is None or c['model'] is None]
        known_equal=disagreements==0
        complete=known_equal and not absent and not source['review_required']
        evidence={key:val for key,val in model.items() if key!='parameters'}
        evidence.update(comparisons=comparisons,geometry_matches=complete,known_dimensions_match=known_equal,
                        difference_count=disagreements,missing_fields=absent,hr0_adjustment=adjustment,
                        application_source=f"{source['sheet']}!第 {source['row']} 行",application_index=0)
        signature=hashlib.sha256(json.dumps(dimensions,sort_keys=True).encode()).hexdigest()
        group=groups.setdefault(signature,{'id':signature,'parameters':dimensions,'sources':[],
                                           'missing_fields':[k for k in required if dimensions.get(k) is None]})
        group['sources'].append(evidence)
    candidates=list(groups.values())
    for group in candidates:
        group['sources'].sort(key=lambda s:(s['difference_count'],len(s['missing_fields']),not s['primary'],s['model'],s['record_id']))
        group['geometry_matches']=any(s['geometry_matches'] for s in group['sources'])
        group['known_dimensions_match']=any(s['known_dimensions_match'] for s in group['sources'])
        group['minimum_differences']=min(s['difference_count'] for s in group['sources'])
        group['area']=slots.areas(group['parameters'])[side]
    candidates.sort(key=lambda g:(not g['geometry_matches'],g['minimum_differences'],bool(g['missing_fields']),g['id']))
    complete=sum(g['geometry_matches'] for g in candidates)
    partial=sum(g['known_dimensions_match'] for g in candidates)
    status='dimension_exact' if complete else 'dimension_partial' if partial else 'dimension_conflict' if candidates else 'dimension_unmatched'
    return {'reference':item['reference'],'name':item['name'],'drawing':doc['drawing'],'side':side,
            'notes':doc.get('notes',''),'status':status,'label':LABELS[status], 'method':METHOD,'note':NOTE,
            'candidate_count':len(candidates),'geometry_matched_count':complete,'application_count':len(doc['applications']),
            'catalog_token':token,'candidates':candidates,'source_review_required':source['review_required'],
            'source_missing_fields':missing,'source_row':source['row'],'source_sheet':source['sheet'],
            'matching_models':sorted({s['model'] for g in candidates for s in g['sources'] if s['geometry_matches']}),
            'candidate_models':sorted({s['model'] for g in candidates for s in g['sources']}),
            'matching_primary_versions':sum(s['geometry_matches'] and s['primary'] for g in candidates for s in g['sources']),
            'matching_history_versions':sum(s['geometry_matches'] and not s['primary'] for g in candidates for s in g['sources'])}


@lru_cache(maxsize=2048)
def _match(stamp, item_json, token):
    return compare_item(json.loads(item_json),index(stamp),token)


def matches(item, stamp, token):
    return _match(stamp,json.dumps(item,sort_keys=True,ensure_ascii=False),token)
