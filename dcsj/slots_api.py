"""Slot catalog management and workbench preview endpoints."""
from typing import Literal
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
import math
from . import slots
from .parameters import ROOT

router = APIRouter(tags=['定转子槽型库'])


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parameters: dict = Field(max_length=500)
    module: Literal['main','tx'] = 'main'
    slot_bindings: slots.Bindings = Field(default_factory=slots.Bindings)

    @field_validator('parameters')
    @classmethod
    def finite(cls, values):
        if any(len(k)>100 or not isinstance(v,(int,float,str,type(None))) or
               isinstance(v,(int,float)) and not math.isfinite(v) or
               isinstance(v,str) and len(v)>2000 for k,v in values.items()):
            raise ValueError('输入参数格式无效')
        return values


class CaptureInput(PreviewInput):
    side: Literal['stator','rotor']


class ApplyInput(PreviewInput):
    reference: str = Field(min_length=1,max_length=200)
    application: int | None = Field(default=None,ge=0,le=999)
    include_geometry: bool = False


@router.get('/slots', include_in_schema=False)
def page():
    return FileResponse(ROOT/'frontend/slots.html')


@router.get('/slots.js', include_in_schema=False)
def script():
    return FileResponse(ROOT/'frontend/slots.js', media_type='application/javascript')


@router.get('/slot-picker.js', include_in_schema=False)
def picker():
    return FileResponse(ROOT/'frontend/slot-picker.js', media_type='application/javascript')


@router.get('/slots.css', include_in_schema=False)
def styles():
    return FileResponse(ROOT/'frontend/slots.css', media_type='text/css')


@router.get('/api/v1/slots/schema')
def schema():
    return slots.schema()


@router.get('/api/v1/slots')
def search(q: str=Query(default='',max_length=120), side:Literal['stator','rotor']|None=None,
           module:Literal['main','tx']|None=None, source:Literal['all','user','resource']='all',
           include_archived:bool=False, limit:int=Query(default=40,ge=1,le=200),
           offset:int=Query(default=0,ge=0,le=100000)):
    return slots.search(q,side,module,source,include_archived,limit,offset)


@router.get('/api/v1/slots/entry')
def entry(reference:str=Query(min_length=1,max_length=200)):
    return slots.get(reference)


@router.post('/api/v1/slots/preview')
def preview(body:PreviewInput):
    result = slots.snapshot(body.parameters,body.slot_bindings.model_dump(exclude_none=True),body.module)
    if body.module == 'main':
        from .slot_diagrams import slot_drawings, svg
        result['drawings'] = [{'side':panel['id'], 'svg':svg(panel)} for panel in slot_drawings(body.parameters)]
    return result


@router.post('/api/v1/slots/capture')
def capture(body:CaptureInput):
    return slots.capture(body.parameters,body.side,body.module)


@router.post('/api/v1/slots/apply')
def apply(body:ApplyInput):
    return slots.apply(body.reference,body.parameters,body.module,body.application,body.include_geometry)
