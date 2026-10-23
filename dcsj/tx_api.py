"""Versioned API and standalone frontend for TX concentric windings."""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from . import tx
from .tx_parameters import schema, validate
from .tx_report import report
from .parameters import ROOT

router=APIRouter(tags=['TX 双层同心式绕组'])


class TXInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parameters: dict = Field(description='TX 独立参数；从 /api/v1/tx/models/{id} 取得，单位见 /schema。')
    base_model_id: str | None = Field(default=None,max_length=100)


class TXCalculationInput(TXInput):
    persist: bool=True


class TXDesignUpdate(TXInput):
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
def models(q:str=Query(default='',max_length=120),limit:int=Query(default=60,ge=1,le=100),offset:int=Query(default=0,ge=0,le=20000)):
    return tx.models(q,limit,offset)


@router.get('/api/v1/tx/models/{identity}')
def model(identity:str):return tx.model(identity)


@router.post('/api/v1/tx/validate')
def check(body:TXInput):return {'valid':True,'parameters':validate(body.parameters)}


@router.post('/api/v1/tx/calculate')
def calculate(body:TXCalculationInput):return tx.calculate(body.parameters,body.persist,body.base_model_id)


@router.get('/api/v1/tx/calculations')
def calculations():return {'items':tx.calculations()}


@router.get('/api/v1/tx/calculations/{identity}')
def calculation(identity:str):return tx.calculation(identity)


@router.get('/tx/reports/{identity}',response_class=HTMLResponse,include_in_schema=False)
def calculation_report(identity:str):return report(tx.calculation(identity))


@router.get('/api/v1/tx/designs')
def designs():return {'items':tx.designs()}


@router.post('/api/v1/tx/designs',status_code=201)
def create(body:TXInput):return tx.save_design(body.parameters,base_model_id=body.base_model_id)


@router.get('/api/v1/tx/designs/{identity}')
def design(identity:str,revision:int|None=Query(default=None,ge=1)):return tx.design(identity,revision)


@router.put('/api/v1/tx/designs/{identity}')
def update(identity:str,body:TXDesignUpdate):
    return tx.save_design(body.parameters,identity,body.expected_revision,body.base_model_id)
