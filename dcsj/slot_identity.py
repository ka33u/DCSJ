"""Complete slot profiles and recognition independent of design bindings.

Rotor identity uses the neck-bottom diameter D2 - 2*hr0. Turning the same
lamination changes airgap and hr0 together, but does not change that diameter.
"""
from functools import lru_cache
import hashlib
import json
import math

from . import slots
from .parameters import InputError

ANGLE_KEYS = {'zs1','zs2','DXJJ','ZXJJ'}
INTEGER_KEYS = {'Q1','Q2','zc','M2'}


def number(value):
    try:
        value = float(value) if not isinstance(value,bool) else math.nan
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def required(doc):
    side, module, p = doc['side'],doc['module'],doc['parameters']
    if module=='main':
        if side=='stator':return slots.MAIN_KEYS[side]
        keys='Q2 zc bc02 hr0 zs2 br1 br2 hr12 hr3 hr4'.split()
        return keys+(['br3','br4'] if p.get('zc') not in (1,2,3) else [])
    return slots.TX_KEYS[side] if module=='tx' else []


def issues(doc):
    p,g=doc.get('parameters',{}),doc.get('geometry',{})
    side,module=doc['side'],doc['module']
    errors=[]
    if module not in ('main','tx'):return ['未提供计算槽尺寸']
    if (doc.get('drawing_source') or {}).get('review_required'):errors.append('原表尺寸待核')
    keys=required(doc)
    errors += ['缺少 '+k for k in keys if number(p.get(k)) is None]
    geokeys=['stator_outer_mm','bore_mm'] if side=='stator' else ['rotor_outer_mm','rotor_inner_mm']
    errors += ['缺少 '+k for k in geokeys if number(g.get(k)) is None]
    if errors:return errors
    if any(number(g[k])<=0 for k in geokeys):errors.append('外形尺寸须为正数')
    if side=='stator' and g['stator_outer_mm']<=g['bore_mm']:errors.append('定子外径须大于内径')
    if side=='rotor' and g['rotor_outer_mm']<=g['rotor_inner_mm']:errors.append('转子外径须大于轴孔')
    if any(p[k]!=int(p[k]) or p[k]<=0 for k in keys if k in INTEGER_KEYS):errors.append('槽数和槽型代码须为正整数')
    if any(abs(p[k])>=90 for k in keys if k in ANGLE_KEYS):errors.append('槽角度须在 -90° 与 90° 之间')
    if side=='stator':
        if p.get('bs1',0)<=0 or abs(p.get('bs2',0))<=0 or p.get('hs12',0)<=0:errors.append('槽宽和槽深须为正数')
        if p.get('b01',0)<0 or p.get('hs0',p.get('Hs0',0))<0:errors.append('槽口尺寸不能为负')
    else:
        if p.get('hr0',0)<0 or p.get('br1',0)<=0 or p.get('br2',0)<0 or p.get('hr12',0)<0:errors.append('转子槽宽、高度无效')
        if module=='main' and (p['br2']==0 or p['bc02']<0):errors.append('转子槽宽或槽口尺寸无效')
        if module=='main' and p.get('zc') not in range(1,9):errors.append('未建立该槽型完整性规则')
        if module=='main' and p.get('zc') in (4,6,7,8) and min(p['br3'],p['br4'])<=0:errors.append('多段槽宽缺失或为零')
        if g['rotor_outer_mm']-2*p['hr0']*(10 if module=='tx' else 1)<=g['rotor_inner_mm']:errors.append('槽口基准落入轴孔')
    return errors


def compact(doc):
    """Keep current dimensions and paired names only; legacy evidence stays in old revisions."""
    result={'schema_version':2,'side':doc['side'],'module':doc['module'],'drawing':doc.get('drawing',''),
            'parameters':{k:float(doc['parameters'][k]) for k in required(doc) if k in doc['parameters']},
            'geometry':{k:doc.get('geometry',{}).get(k) for k in
                        (['stator_outer_mm','bore_mm'] if doc['side']=='stator' else ['rotor_outer_mm','rotor_inner_mm'])},
            'aliases':[dict(pair) for pair in doc.get('aliases',[])]}
    return result


def vector(doc):
    if issues(doc):return None
    p,g=doc['parameters'],doc['geometry'];side,module=doc['side'],doc['module']
    values={k:number(p[k]) for k in required(doc)}
    values.update({k:number(g[k]) for k in (['stator_outer_mm','bore_mm'] if side=='stator' else ['rotor_inner_mm'])})
    if side=='rotor':
        values['neck_diameter_mm']=number(g['rotor_outer_mm'])-2*values.pop('hr0')*(10 if module=='tx' else 1)
    return values


def signature(doc):
    values=vector(doc)
    if values is None:return None
    payload=[doc['module'],doc['side'],{k:round(v,6) for k,v in values.items()}]
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def identities(item):
    values=[{'name':item['name'],'drawing':item['document'].get('drawing','')},*item['document'].get('aliases',[])]
    return [dict(pair) for i,pair in enumerate(values) if pair not in values[:i]]


def from_parameters(parameters,side,module='main'):
    geometry=slots.geometry(parameters,module)
    shaft=number(parameters.get('D4' if module=='tx' else 'Di2'))
    if shaft is not None:geometry['rotor_inner_mm']=shaft*(10 if module=='tx' else 1)
    return {'side':side,'module':module,'parameters':{k:number(parameters[k]) for k in slots.KEYS[module][side] if k in parameters},'geometry':geometry}


@lru_cache(maxsize=8)
def _prepared(serialized):
    items=json.loads(serialized)
    return [(item,vector(item['document'])) for item in items if not issues(item['document'])]


def recognize(parameters,module='main'):
    # Re-read current user revisions each time; changing a name or library entry
    # must also change the next result even when the electromagnetic cache hits.
    data=slots.search(module=module,limit=20000)['items']
    prepared=_prepared(json.dumps(data,ensure_ascii=False,sort_keys=True))
    result=[]
    for side in slots.SIDES:
        actual=vector(from_parameters(parameters,side,module));matched=[]
        for item,expected in prepared:
            if item['document']['side']!=side or item['document']['module']!=module or actual is None:continue
            if actual.keys()!=expected.keys():continue
            def equal(key):
                tolerance=0 if key in INTEGER_KEYS else .1 if key in ANGLE_KEYS else .001 if module=='tx' and key in slots.TX_KEYS[side] else .01
                return abs(actual[key]-expected[key])<=tolerance+1e-9
            if all(equal(key) for key in expected):matched.append(item)
        pairs=[]
        for item in matched:
            for pair in identities(item):
                if pair not in pairs:pairs.append(pair)
        result.append({'side':side,'status':'recognized' if len(pairs)==1 else 'ambiguous' if pairs else 'unrecognized',
                       'name':' / '.join(dict.fromkeys(p['name'] for p in pairs)) or '未识别',
                       'drawing':' / '.join(dict.fromkeys(p['drawing'] for p in pairs if p['drawing'])),
                       'identities':pairs,'references':[i['reference'] for i in matched],'reference':None,'revision':None,'document':None})
    return {'method':'dimension-neck-diameter-v1','entries':result,
            'note':'按实际尺寸识别；转子按 D2−2×hr0 核对同一槽颈底位置。多图号同时相符时全部显示，不自动指定。'}


def snapshot(parameters,module='main',details=None):
    found=recognize(parameters,module)
    # Calculation areas remain numerical results, not library fields.
    base=slots.snapshot(parameters,module=module,resolved={},details=details)
    for entry,manual in zip(found['entries'],base['entries']):entry['area']=manual['area']
    found['geometry_check']=base['geometry_check']
    return found


def validate_complete(doc):
    problems=issues(doc)
    if problems:raise InputError([{'field':'slot','message':'；'.join(problems)}])


def permissions(item,actor=None):
    own=bool(actor and item.get('owner_id')==actor['id'])
    latest=item.get('latest_revision',item.get('revision'))
    return {**item,'author':item.get('author') or '',
            'can_edit':own and not item.get('archived') and item.get('revision')==latest,
            'can_archive':own and item.get('revision')==latest}


def profiles(q='',side=None,module=None,limit=40,offset=0,*,actor=None):
    groups={}
    for item in slots.search(side=side,module=module,limit=20000)['items']:
        geometry_key=signature(item['document'])
        if geometry_key is None:continue
        # Personal copies are independently owned/versioned, even when the
        # dimensions match a resource or another account's design exactly.
        key=item['reference'] if item['origin']=='user' else geometry_key
        if key not in groups:
            groups[key]={**item,'document':compact(item['document'])}
        else:
            group=groups[key];known=identities(group)
            for pair in identities(item):
                if pair not in known:group['document']['aliases'].append(pair);known.append(pair)
    terms=q.casefold().split()
    items=[item for item in groups.values() if all(term in (item['name']+' '+json.dumps(item['document'],ensure_ascii=False)).casefold() for term in terms)]
    return {'items':[permissions(i,actor) for i in items[offset:offset+limit]],'total':len(items),'limit':limit,'offset':offset}


def save_profile(name,doc,identity=None,expected_revision=None,*,actor=None,operation=None,copied_from=None):
    from . import library
    # Validate the entered dimensions before stripping old source annotations.
    doc=slots.SlotDocument.model_validate(doc).model_dump(mode='json')
    validate_complete(doc)
    doc=compact(doc);slots.SlotDocument.model_validate(doc)
    operation=operation or ('update' if identity else 'create')
    if operation=='update':
        if not identity or expected_revision is None or copied_from:
            raise InputError([{'field':'operation','message':'保存新版本须提供记录 ID 和当前版本号'}])
    elif operation in ('create','copy'):
        if identity is not None or expected_revision is not None or (operation=='create' and copied_from):
            raise InputError([{'field':'operation','message':'新增和另存为不能更新现有记录'}])
    else:raise InputError([{'field':'operation','message':'无效的保存操作'}])
    if copied_from:slots.get(copied_from)  # Validate a frozen source reference; never write it.
    saved=library.save('slots',name,doc,identity,expected_revision,actor=actor,copied_from=copied_from or '')
    return permissions(saved|{'origin':'user','operation':operation},actor)
