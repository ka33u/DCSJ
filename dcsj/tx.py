"""Independent concentric-winding designs, immutable calculations and resources."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import threading
import uuid

from . import __version__, resources
from .numeric_vm import NumericKernelError
from .storage import connect, dumps, now, audit, ConflictError, NotFoundError
from .tx_parameters import validate, TXInputError
from .tx_vm import load_program, TXVM
from .execution import BusyError

_slots=threading.BoundedSemaphore(2)


class TXUnavailableError(ValueError):
    pass


@lru_cache(maxsize=4)
def _catalog(path, checksum):
    path=Path(path)
    if path.stat().st_size>32*1024*1024 or resources.digest(path)!=checksum:
        raise TXUnavailableError('TX 型号资源校验失败')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('format')!='tx-catalog' or data.get('format_version')!=1 or not 1<=len(data.get('models',[]))<=20000:
        raise TXUnavailableError('TX 型号资源格式无效')
    identities=set()
    for model in data['models']:
        identity=model.get('id')
        if not isinstance(identity,str) or not 1<=len(identity)<=100 or identity in identities:
            raise TXUnavailableError('TX 型号标识无效或重复')
        validate(model['parameters'])
        if not isinstance(model.get('name'),str) or len(model['name'])>120 or not isinstance(model.get('source'),dict):
            raise TXUnavailableError('TX 型号来源无效')
        identities.add(identity)
    if data.get('default_id') not in identities:raise TXUnavailableError('TX 默认型号缺失')
    return data


def validate_resources(root, files):
    try:
        catalog=_catalog(str(root/'tx_catalog.json'),files['tx_catalog.json']['sha256'])
        load_program(str(root/'tx_kernel.json.gz'),files['tx_kernel.json.gz']['sha256'])
        return len(catalog['models'])
    except (ValueError,KeyError,TypeError,OSError) as exc:
        raise resources.ResourceError('TX 资源包校验失败：'+str(exc)) from None


def context():
    package=resources.current()
    paths={k:resources.asset(k,package) for k in resources.TX_FILES}
    if not all(path.is_file() for path in paths.values()):
        raise TXUnavailableError('尚未安装 TX 资源，请在「资源与用户数据」导入包含 TX 的新版私有资源包')
    files=package['files'] if package else {k:{'sha256':resources.digest(path)} for k,path in paths.items()}
    return {'package_id':package['package_id'] if package else None,
            'resource_version':package['version'] if package else 'local',
            'paths':paths,'files':files}


def status():
    try:
        ctx=context();catalog=_catalog(str(ctx['paths']['tx_catalog.json']),ctx['files']['tx_catalog.json']['sha256'])
        return {'available':True,'resource_version':ctx['resource_version'],'package_id':ctx['package_id'],
                'model_versions':len(catalog['models']),'default_id':catalog['default_id'],'application_version':__version__}
    except (TXUnavailableError,resources.ResourceError,OSError) as exc:
        return {'available':False,'message':str(exc),'application_version':__version__}


def models(query='',limit=60,offset=0,source='original'):
    from . import saved_catalog
    saved=saved_catalog.search('tx',query,limit,offset) if source!='original' else {'items':[],'total':0}
    items=[]
    for row in saved['items']:
        p=json.loads(row.pop('parameters'))
        row.update(design_id=row['id'],id=saved_catalog.reference(row['id'],row['revision']),
                   kind='saved_design',power_kw=p['P2'],poles=p['P'],source={'kind':'saved_design'})
        items.append(row)
    original=_original_models(query,max(0,limit-len(items)),max(0,offset-saved['total'])) if source!='saved' else {'items':[],'total':0}
    return {**original,'items':items+original['items'],'total':saved['total']+original['total'],'offset':offset}


def _original_models(query='',limit=60,offset=0):
    ctx=context();catalog=_catalog(str(ctx['paths']['tx_catalog.json']),ctx['files']['tx_catalog.json']['sha256'])
    items=[m for m in catalog['models'] if query.casefold() in m['name'].casefold()]
    return {'items':[{k:m[k] for k in ('id','name','source','verified_execution')}|{'power_kw':m['parameters']['P2'],'poles':m['parameters']['P']} for m in items[offset:offset+limit]],
            'total':len(items),'resource_version':ctx['resource_version'],'offset':offset}


def model(identity, ctx=None):
    if identity.startswith('saved-design:'):
        from .saved_catalog import parse_reference
        d=design(*parse_reference(identity))
        return {'id':identity,'name':d['name'],'author':d['author'],'parameters':d['parameters'],
                'kind':'saved_design','design':d,'source':{'kind':'saved_design','design_id':d['id'],
                                                       'revision':d['revision'],'author':d['author']}}
    ctx=ctx or context();catalog=_catalog(str(ctx['paths']['tx_catalog.json']),ctx['files']['tx_catalog.json']['sha256'])
    found=next((m for m in catalog['models'] if m['id']==identity),None)
    if found is None:raise NotFoundError('TX 型号不存在于当前资源版本')
    return {**found,'resource_version':ctx['resource_version'],'package_id':ctx['package_id']}


def lineage(base_model_id,ctx):
    if not base_model_id:return {'kind':'manual'}
    item=model(base_model_id,ctx)
    return {'kind':'tx_catalog','model_id':item['id'],'name':item['name'],'source':item['source'],
            'package_id':ctx['package_id'],'resource_version':ctx['resource_version'],
            'base_input_sha256':hashlib.sha256(dumps(item['parameters']).encode()).hexdigest()}


def _report_text(rows):
    lines=[]
    for row in rows:
        line=''
        for token in row['tokens']:
            value=token['value'];kind=token['kind']&0x3f
            if kind==33:
                if value<=len(line):lines.append(line);line=''
                line+=' '*max(0,min(120,int(value)-1-len(line)))
            elif isinstance(value,(int,float)):line+=f'{value:g}'
            else:line+=str(value)
        lines.append(line.rstrip())
    return '\n'.join(lines)


def _result(vm,parameters,ctx,source):
    sites=vm.program['result_sites'];perf=[];harmonics=[];coils=[];details={}
    for row in vm.printed:
        parts=row['parts'];site=row['site']
        if site==sites['performance']:perf=[float(parts[i]) for i in (2,4,6,8,10,12,14)]
        elif site==sites['harmonics']:
            harmonics.append(dict(zip(('order','winding_factor','relative_mmf_percent','coil_index','turns','pitch'),[float(parts[i]) for i in (1,4,10,13,15,20)])))
        elif site==sites['coils']:
            coils.append(dict(zip(('index','span_cm','radius_cm','end_angle_rad','height_cm'),[float(parts[i]) for i in (1,3,8,13,18)])))
        for key,index in vm.program['output_rows'].get(str(site),{}).items():details[key]=float(parts[index])
    if len(perf)!=7 or len(harmonics)!=6 or not coils:raise NumericKernelError('TX 计算结果不完整')
    metrics=dict(zip(('efficiency','power_factor','maximum_torque_ratio','starting_torque_ratio','starting_current_ratio','aj','slip'),perf))
    if not 0<metrics['efficiency']<=1 or not 0<metrics['power_factor']<=1 or not 0<=metrics['slip']<1:
        raise NumericKernelError('TX 结果未得到有效的效率、功率因数或转差率，请检查参数')
    metrics['speed_rpm']=details.pop('speed_rpm')
    if not all(math.isfinite(v) for v in [*metrics.values(),*details.values()]):raise NumericKernelError('TX 结果包含非有限数值')
    return {'module':'tx','application_version':__version__,'engine':'tx-vb-ir-1','metrics':metrics,'details':details,
            'harmonics':harmonics,'coils':coils,'report_text':_report_text(vm.printed),'steps':vm.steps,
            'resource_version':ctx['resource_version'],'package_id':ctx['package_id'],
            'kernel_sha256':ctx['files']['tx_kernel.json.gz']['sha256'],'source':source,
            'input_sha256':hashlib.sha256(dumps(parameters).encode()).hexdigest()}


def calculate(parameters,persist=True,base_model_id=None,slot_bindings=None):
    from . import slots
    resolved = slots.resolve(slot_bindings, 'tx')
    parameters=validate(parameters);ctx=context();source=lineage(base_model_id,ctx)
    if not _slots.acquire(blocking=False):raise BusyError('TX 计算资源已占满，请稍后重试')
    try:
        program=load_program(str(ctx['paths']['tx_kernel.json.gz']),ctx['files']['tx_kernel.json.gz']['sha256'])
        vm=TXVM(program,parameters);vm.run();result=_result(vm,parameters,ctx,source)
        result['slot_bindings'] = slot_bindings or {}
        result['slots'] = slots.snapshot(parameters,module='tx',resolved=resolved,details=result['details'])
    except (ArithmeticError,ValueError,NotImplementedError,KeyError,IndexError,UnicodeError) as exc:
        raise TXInputError('TX 计算未完成，请检查槽型、绕组及计算系数。原因：'+str(exc)) from None
    finally:_slots.release()
    record={'id':str(uuid.uuid4()) if persist else None,'name':parameters['TYP'],'parameters':parameters,'result':result,'created_at':now()}
    if persist:
        with connect() as db:
            db.execute('INSERT INTO tx_calculations VALUES(?,?,?,?,?)',(record['id'],record['name'],dumps(parameters),dumps(result),record['created_at']))
            audit(db,'tx.calculated',record['id'],{'kernel_sha256':result['kernel_sha256']})
    return record


def calculation(identity):
    with connect() as db:row=db.execute('SELECT * FROM tx_calculations WHERE id=?',(identity,)).fetchone()
    if not row:raise NotFoundError('TX 计算记录不存在')
    item=dict(row);item['parameters']=json.loads(item['parameters']);item['result']=json.loads(item['result']);return item


def calculations(limit=60):
    with connect() as db:return [dict(r) for r in db.execute('SELECT id,name,created_at FROM tx_calculations ORDER BY created_at DESC LIMIT ?',(limit,))]


def designs():
    with connect() as db:return [dict(r) for r in db.execute('SELECT * FROM tx_designs ORDER BY updated_at DESC LIMIT 500')]


def design(identity,revision=None):
    with connect() as db:
        row=db.execute('SELECT * FROM tx_designs WHERE id=?',(identity,)).fetchone()
        if not row:raise NotFoundError('TX 设计不存在')
        version=db.execute('SELECT * FROM tx_revisions WHERE design_id=? AND revision=?',(identity,revision or row['revision'])).fetchone()
        if not version:raise NotFoundError('TX 设计版本不存在')
        versions=[r[0] for r in db.execute('SELECT revision FROM tx_revisions WHERE design_id=? ORDER BY revision DESC',(identity,))]
        return {**dict(row),'name':json.loads(version['parameters'])['TYP'],'author':version['author'],
                'revision':version['revision'],'latest_revision':row['revision'],'parameters':json.loads(version['parameters']),
                'source':json.loads(version['source']),'revisions':versions,
                'slot_bindings':json.loads(version['slot_bindings'])}


def save_design(parameters,identity=None,expected_revision=None,base_model_id=None,author=None,slot_bindings=None):
    from . import slots
    if slot_bindings is not None:
        slots.resolve(slot_bindings, 'tx')
    parameters=validate(parameters);source=lineage(base_model_id,context()) if base_model_id else {'kind':'manual'}
    stamp=now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if identity:
            row=db.execute('SELECT revision,author FROM tx_designs WHERE id=?',(identity,)).fetchone()
            if not row:raise NotFoundError('TX 设计不存在')
            if row['revision']!=expected_revision:raise ConflictError('TX 设计已被更新，请重新打开最新版本')
            if not base_model_id:
                previous=db.execute('SELECT source FROM tx_revisions WHERE design_id=? AND revision=?',
                                    (identity,expected_revision)).fetchone()
                source=json.loads(previous['source'])
            revision=expected_revision+1
            if slot_bindings is None:
                slot_bindings=json.loads(db.execute('SELECT slot_bindings FROM tx_revisions WHERE design_id=? AND revision=?',
                                                    (identity,expected_revision)).fetchone()[0])
            author=row['author'] if author is None else author
            db.execute('UPDATE tx_designs SET name=?,revision=?,updated_at=?,author=? WHERE id=?',(parameters['TYP'],revision,stamp,author,identity))
        else:
            identity=str(uuid.uuid4());revision=1
            author=author or ''
            db.execute('INSERT INTO tx_designs(id,name,revision,created_at,updated_at,author) VALUES(?,?,?,?,?,?)',
                       (identity,parameters['TYP'],revision,stamp,stamp,author))
        db.execute('INSERT INTO tx_revisions(design_id,revision,parameters,source,created_at,author) VALUES(?,?,?,?,?,?)',
                   (identity,revision,dumps(parameters),dumps(source),stamp,author))
        audit(db,'tx.design_saved',identity,{'revision':revision})
        db.execute('UPDATE tx_revisions SET slot_bindings=? WHERE design_id=? AND revision=?',
                   (dumps(slot_bindings or {}),identity,revision))
    return design(identity)
