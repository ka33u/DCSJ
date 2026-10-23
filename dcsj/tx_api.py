"""Versioned API and standalone frontend for TX concentric windings."""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
from . import tx, slots
from .tx_parameters import schema, validate
from .tx_report import report
from .parameters import ROOT

router=APIRouter(tags=['TX 双层同心式绕组'])


class TXInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parameters: dict = Field(description='TX 独立参数；从 /api/v1/tx/models/{id} 取得，单位见 /schema。')
    base_model_id: str | None = Field(default=None,max_length=100)
    slot_bindings: slots.Bindings = Field(default_factory=slots.Bindings)


class TXCalculationInput(TXInput):
    persist: bool=True


class TXDesignInput(TXInput):
    author: str = Field(default='', max_length=120, description='作者署名；不参与计算')

    @field_validator('author')
    @classmethod
    def trim_author(cls, value):
        return value.strip()


class TXDesignUpdate(TXDesignInput):
    expected_revision: int=Field(ge=1)


@router.get('/tx',include_in_schema=False)
def page():return FileResponse(ROOT/'frontend/tx.html')


@router.get('/tx.js',include_in_schema=False)
def script():return FileResponse(ROOT/'frontend/tx.js',media_type='application/javascript')


@router.get('/tx.css',include_in_schema=False)
def styles():return FileResponse(ROOT/'frontend/tx.css',media_type='text/css')


@router.get('/api/v1/tx/status')
def status():return tx.status()


@router.get('/api/v1/tx/schema')
def parameters():return schema()


@router.get('/api/v1/tx/models')
def models(q:str=Query(default='',max_length=120),limit:int=Query(default=60,ge=1,le=100),offset:int=Query(default=0,ge=0,le=20000),
           source:Literal['original','saved','all']='original'):
    return tx.models(q,limit,offset,source)


@router.get('/api/v1/tx/models/{identity}')
def model(identity:str):return tx.model(identity)


@router.post('/api/v1/tx/validate')
def check(body:TXInput):return {'valid':True,'parameters':validate(body.parameters)}


@router.post('/api/v1/tx/calculate')
def calculate(body:TXCalculationInput):return tx.calculate(body.parameters,body.persist,body.base_model_id,body.slot_bindings.model_dump(exclude_none=True))


@router.get('/api/v1/tx/calculations')
def calculations():return {'items':tx.calculations()}


@router.get('/api/v1/tx/calculations/{identity}')
def calculation(identity:str):return tx.calculation(identity)


@router.get('/tx/reports/{identity}',response_class=HTMLResponse,include_in_schema=False)
def calculation_report(identity:str):return report(tx.calculation(identity))


@router.get('/api/v1/tx/designs')
def designs():return {'items':tx.designs()}


@router.post('/api/v1/tx/designs',status_code=201)
def create(body:TXDesignInput):return tx.save_design(body.parameters,base_model_id=body.base_model_id,author=body.author,slot_bindings=body.slot_bindings.model_dump(exclude_none=True))


@router.get('/api/v1/tx/designs/{identity}')
def design(identity:str,revision:int|None=Query(default=None,ge=1)):return tx.design(identity,revision)


@router.put('/api/v1/tx/designs/{identity}')
def update(identity:str,body:TXDesignUpdate):
    return tx.save_design(body.parameters,identity,body.expected_revision,body.base_model_id,
                          body.author if 'author' in body.model_fields_set else None,
                          body.slot_bindings.model_dump(exclude_none=True) if 'slot_bindings' in body.model_fields_set else None)
