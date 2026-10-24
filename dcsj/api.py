"""Local web workbench and versioned engineering API."""
import math
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__, catalog, projects, calculations, comparisons, modeling, resources, library, slots, auth
from .execution import BusyError, compute
from .storage import ConflictError, NotFoundError, now
from .parameters import ROOT, InputError, schema, validate, example_data, check_keys
from .engine import CalculationUnavailableError
from . import jobs, optimization
from . import pdf_fonts
from .pdf_report import PDFUnavailableError, PDFContentError
from .saved_catalog import series_name


@asynccontextmanager
async def lifespan(app):
    worker = jobs.Worker().start()
    try:
        yield
    finally:
        worker.close()

app = FastAPI(
    title="DCSJ 电磁设计 API", version=__version__,
    description="三相异步电动机设计工作台。相电压、极数和原材料表语义见参数 schema。"
                "历史型号记录与本次重新计算的结果分开提供。",
    docs_url=None, redoc_url=None,
    lifespan=lifespan,
)
from .tx_api import router as tx_router
from .tx_parameters import TXInputError
from .tx import TXUnavailableError
app.include_router(tx_router)
from .slots_api import router as slots_router
app.include_router(slots_router)
app.include_router(auth.router)


@app.exception_handler(TXInputError)
async def tx_input_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=422)


@app.exception_handler(TXUnavailableError)
async def tx_unavailable(request, error):
    return JSONResponse({'detail': str(error)}, status_code=503)


class DesignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, str | float | int | None] = Field(
        description="原字段名的参数字典；可从 /api/v1/example 或型号详情取得完整输入。"
    )

    @field_validator("parameters")
    @classmethod
    def finite_parameters(cls, values):
        if len(values) > 500:
            raise ValueError("参数不能超过 500 项")
        for key, value in values.items():
            if len(key) > 100 or isinstance(value, str) and len(value) > 1000:
                raise ValueError("参数文本过长")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{key} 必须为有限数值")
        return check_keys(values)


class ProjectInput(DesignInput):
    slot_bindings: slots.Bindings = Field(default_factory=slots.Bindings)
    name: str = Field(min_length=1, max_length=120)
    author: str | None = Field(default=None, max_length=120, description='由登录用户名自动填写；可省略，显式提供时必须与登录用户一致')
    series: str | None = Field(default=None, max_length=80, description='系列名；创建时省略为未分类，更新时省略保留原值，空字符串清除分类')
    validate_series = field_validator('series')(series_name)
    notes: str = Field(default="", max_length=4000)
    base_model_id: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("author")
    @classmethod
    def trim_author(cls, value):
        return value.strip() if value is not None else None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("设计名称不能为空")
        return value.strip()


class ProjectUpdate(ProjectInput):
    expected_revision: int = Field(ge=1)


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archived: bool
    expected_revision: int = Field(ge=1)


class CalculationInput(DesignInput):
    slot_bindings: slots.Bindings = Field(default_factory=slots.Bindings)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    project_revision: int | None = Field(default=None, ge=1)
    base_model_id: str | None = Field(default=None, min_length=1, max_length=160)
    persist: bool = Field(default=True, description='false 仅试算，不创建计算记录或报告 ID')

    @model_validator(mode="after")
    def project_link(self):
        if (self.project_id is None) != (self.project_revision is None):
            raise ValueError("关联项目时须同时提供 project_id 和 project_revision")
        return self


class ModelCalculationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    overrides: dict[str, str | float | int | None] = Field(default_factory=dict)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    persist: bool = True

    @field_validator('overrides')
    @classmethod
    def valid_overrides(cls, values):
        return DesignInput.finite_parameters(values)


class WireSpecification(BaseModel):
    JJ: int = Field(description='第一组并绕根数；0 表示不参与')
    Djj: float = Field(description='第一组线径，mm')
    MM: int = Field(description='第二组并绕根数；0 表示不参与')
    Dmm: float = Field(description='第二组线径，mm；根数为 0 时不参与计算')


class WireSelection(BaseModel):
    mode: Literal['fixed', 'target_fill']
    input: WireSpecification = Field(description='提交的线规，保持输入快照不变')
    used: WireSpecification = Field(description='计算后直接读取的实际线规，与 Sf、铜耗及铜重对应')
    ignored_input_keys: list[str]
    target_fill: float | None = Field(description='目标槽满率，0–1 比例；固定线规时为 null')
    actual_fill: float = Field(description='实际槽满率，与 values.Sf 同值')
    fill_delta: float | None = Field(description='实际减目标，比例；乘 100 得百分点')
    units: dict[str, str]
    source: Literal['kernel-final-state']


class CalculationResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    inputs: dict = Field(description='提交并规范化的输入快照；反算后的实际线规见 wire_selection.used；设计指标沿用 ATA、PF、IST、TST、TM')
    values: dict[str, float] = Field(description='规范数值输出；磁密原值 G，效率与功率因数均为 0–1 比例')
    metrics: list[dict] = Field(description='values 的同值元数据视图，含单位和 source；不是另一套计算')
    curves: list[dict] = Field(description='趋势采样；校核和寻优使用 values，不从曲线读判定值')
    provenance: dict = Field(description='数值核、材料、输入指纹和服务器解析的 base_model 来源')
    contract: dict = Field(default_factory=dict, description='values 与 metrics 的同值关系及曲线用途')
    values_units: dict[str, str] = Field(default_factory=dict)
    values_si: dict[str, dict] = Field(default_factory=dict, description='磁密等 SI 补充值，原 values 保持兼容单位')
    electrical: dict = Field(default_factory=dict, description='接法、相电压与线电压，Y 按 sqrt(3) 换算')
    requirements: list[dict] = Field(default_factory=list, description='原参数 ATA、PF、IST、TST、TM 与结果的比较，含判定方向和 passed')
    loss_semantics: dict = Field(default_factory=dict, description='损耗输入单位、核内比例、额定 W 及可选转速折算')
    material_semantics: dict = Field(default_factory=dict, description='材料声明频率与核内频率折算分开返回')
    wire_selection: WireSelection | None = Field(default=None, description='实际线规、原输入及槽满率偏差；旧记录未保存此项时为 null，须重新计算获取')
    curve_checks: list[dict] = Field(default_factory=list, description='曲线起动点和采样峰值与校核标量的差值')
    calculation_id: str | None = Field(description='persist=false 时为 null，无保存记录和报告')
    persisted: bool


PDF_RESPONSES = {
    200: {'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}},
    404: {'description': '计算或对比记录不存在'},
    422: {'description': '报告含当前字体不支持的字符'},
    429: {'description': 'PDF 渲染资源忙，请按 Retry-After 重试'},
    503: {'description': 'PDF 字体缺失或无法读取；返回 code、setup、html_report_url',
          'content': {'application/json': {'schema': {'type': 'object', 'properties': {
              'detail': {'type': 'string'}, 'code': {'type': 'string'},
              'setup': {'type': 'string'}, 'html_report_url': {'type': ['string', 'null']}}}}}},
}


class CalculationArchiveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    archived: bool = True


class CalculationCleanupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name_prefix: str | None = Field(default=None, min_length=1, max_length=120)
    before: datetime | None = None
    after: datetime | None = None
    action: Literal['archive', 'delete'] = 'archive'
    dry_run: bool = True
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode='after')
    def explicit_scope(self):
        if not (self.name_prefix or self.before or self.after):
            raise ValueError('请指定名称前缀或时间条件，默认只预览，不执行清理')
        for value in (self.before, self.after):
            if value is not None and value.tzinfo is None:
                raise ValueError('时间必须包含时区，例如 2026-01-01T00:00:00Z')
        if self.before and self.after and self.after >= self.before:
            raise ValueError('after 必须早于 before')
        return self


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calculation_ids: list[str] = Field(min_length=2, max_length=8)
    baseline_id: str | None = None

    @model_validator(mode="after")
    def distinct_schemes(self):
        if len(set(self.calculation_ids)) != len(self.calculation_ids):
            raise ValueError("对比方案不能重复")
        if self.baseline_id is not None and self.baseline_id not in self.calculation_ids:
            raise ValueError("基准方案必须在对比列表中")
        return self


class SavedComparisonInput(ComparisonInput):
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default='', max_length=4000)

    @field_validator('name')
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError('对比名称不能为空')
        return value.strip()


class QuickModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=100)
    changes: dict[str, str | float | int | None] = Field(default_factory=dict)
    rotor_outer_mm: float | None = Field(
        default=None, gt=0, le=1e9, allow_inf_nan=False, strict=True,
        description="可选转子外径 D2（mm）。按 g=(Di1-D2)/2 联动气隙；此时 changes 不得包含 g。")


class LibraryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    document: dict
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('名称不能为空')
        return value.strip()


class ResourceImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_package_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ResourceActivateInput(ResourceImportInput):
    package_id: str = Field(pattern=r'^[a-f0-9]{64}$')


@app.exception_handler(resources.ResourceError)
async def resource_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=422)


@app.exception_handler(NotFoundError)
async def not_found_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(BusyError)
async def busy_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=429, headers={"Retry-After": "2"})


@app.exception_handler(CalculationUnavailableError)
async def unavailable_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.exception_handler(PDFUnavailableError)
async def pdf_unavailable_error(request, error):
    identity = request.path_params
    fallback = ('/reports/' + identity['calculation_id']) if 'calculation_id' in identity else (
        '/comparison-reports/' + identity['comparison_id'] if 'comparison_id' in identity else None)
    return JSONResponse({'detail': str(error), 'code': 'PDF_FONT_UNAVAILABLE', 'retryable': False,
                         'setup': 'python scripts/install_pdf_font.py', 'html_report_url': fallback},
                        status_code=503, headers={'Content-Location': fallback} if fallback else {})


@app.exception_handler(PDFContentError)
async def pdf_content_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=422)


@app.exception_handler(ConflictError)
async def conflict_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.middleware("http")
async def bounded_body(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 512 * 1024:
                return JSONResponse({"detail": "请求正文不能超过 512 KiB"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path in {"/", "/app.js", "/styles.css", "/tx", "/tx.js", "/tx.css"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


app.middleware('http')(auth.protect)


@app.exception_handler(RequestValidationError)
async def request_validation_error(request, error):
    # Pydantic error contexts may contain exceptions or nonfinite input values.
    issues = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]}
              for e in error.errors()]
    return JSONResponse({"detail": issues}, status_code=422)


@app.exception_handler(InputError)
async def input_error(request, error):
    return JSONResponse({"detail": error.issues}, status_code=422)


@app.get("/api/v1/status", tags=["服务"])
def status():
    from .engine import engine_status
    return {"name": "DCSJ 电磁设计工作台", "version": __version__,
            "catalog": catalog.stats(), "engine": engine_status(), "reports": {"pdf": pdf_fonts.status()}}


@app.get("/api/v1/schema", tags=["设计"])
def get_schema():
    return schema()


@app.get("/api/v1/example", tags=["设计"])
def example():
    data = example_data()
    if data is None:
        raise HTTPException(404, "未安装私有示例，请导入自己的设计 JSON 或安装本地型号库。")
    return data


@app.get("/api/v1/series", tags=["型号库"])
def get_series():
    return {"items": catalog.series()}


@app.get("/api/v1/models", tags=["型号库"])
def get_models(
    q: Annotated[str, Query(max_length=120)] = "",
    series: Annotated[str, Query(max_length=80)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    history: bool = False,
    frequency_hz: Annotated[float | None, Query(gt=0, allow_inf_nan=False)] = None,
    poles: Annotated[int | None, Query(ge=2, le=64)] = None,
    voltage_v: Annotated[float | None, Query(gt=0, allow_inf_nan=False, description='绕组相电压 V')] = None,
    power_min: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    power_max: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    frame: Annotated[str | None, Query(pattern=r'^\d{2,4}$')] = None,
    source_table: Annotated[str | None, Query(max_length=120)] = None,
    source: Literal['original', 'saved', 'all'] = 'original',
):
    if poles is not None and poles % 2:
        raise HTTPException(422, '极数必须为偶数')
    if power_min is not None and power_max is not None and power_min > power_max:
        raise HTTPException(422, 'power_min 不能大于 power_max')
    return catalog.models(q, series, limit, offset, history, frequency_hz=frequency_hz,
                          poles=poles, voltage_v=voltage_v, power_min=power_min,
                          power_max=power_max, frame=frame, source_table=source_table, source=source)


@app.get("/api/v1/models/{model_id}", tags=["型号库"])
def get_model(model_id: str):
    result = catalog.model(model_id)
    if result is None:
        raise HTTPException(404, "型号不存在")
    return result


@app.post("/api/v1/calculate", response_model=CalculationResult, tags=["计算"],
          responses={409: {'description': '设计版本或来源不一致'}, 429: {'description': '计算资源忙'},
                     503: {'description': '计算资源未安装'}})
def calculate(body: CalculationInput):
    return _calculate(body)


def _calculate(body: CalculationInput, origin=None):
    from .engine import CalculationError
    bindings = body.slot_bindings.model_dump(exclude_none=True)
    if body.project_id is not None:
        version = projects.get_revision(body.project_id, body.project_revision)
        if validate(version['parameters']) != validate(body.parameters):
            raise ConflictError("输入与关联版本不同，请先保存新版本，或不关联版本进行计算。")
        saved_origin = version.get('lineage') or {}
        if body.base_model_id and saved_origin.get('id') != body.base_model_id:
            raise ConflictError('来源型号与关联设计版本不符，请先保存新版本')
        origin = saved_origin
        saved_bindings = version.get('slot_bindings') or {}
        if 'slot_bindings' in body.model_fields_set and bindings != saved_bindings:
            raise ConflictError('槽型引用与关联设计版本不同，请先保存新版本')
        bindings = saved_bindings
    elif body.base_model_id and origin is None:
        origin = catalog.lineage(body.base_model_id)
    resolved_slots = slots.resolve(bindings)
    try:
        result = compute(body.parameters)
        result['slot_bindings'] = bindings
        result['slots'] = slots.snapshot(result['inputs'], resolved=resolved_slots)
        result['provenance']['base_model'] = origin
        if not body.persist:
            return {**result, 'calculation_id': None, 'persisted': False,
                    'name': body.name or result['inputs']['SizeX'], 'created_at': now(),
                    'project_id': body.project_id, 'project_revision': body.project_revision}
        return calculations.save(result, body.name, body.project_id, body.project_revision)
    except BusyError as error:
        raise HTTPException(429, str(error), headers={"Retry-After": "2"}) from error
    except CalculationError as error:
        raise HTTPException(422, str(error)) from error


@app.post('/api/v1/models/{model_id}/calculate', response_model=CalculationResult, tags=['计算'])
def calculate_model(model_id: str, body: ModelCalculationInput):
    model = get_model(model_id)
    return _calculate(CalculationInput(parameters={**model['parameters'], **body.overrides},
                                      name=body.name, persist=body.persist, base_model_id=model_id,
                                      slot_bindings=model.get('project', {}).get('slot_bindings', {})),
                      origin=catalog.lineage_of(model))


@app.get("/api/v1/materials", tags=["材料库"])
def get_materials():
    return {"items": catalog.materials()}


@app.get("/api/v1/materials/{material_id}", tags=["材料库"])
def get_material(material_id: str):
    if material_id.startswith('user-material:'):
        return library.material(material_id)
    result = catalog.material(material_id)
    if result is None:
        raise HTTPException(404, "材料不存在")
    return result


@app.get('/api/v1/resources', tags=['资源与用户数据'])
def resource_status():
    return resources.status()


@app.post('/api/v1/resources/import', tags=['资源与用户数据'])
def import_private_resources(body: ResourceImportInput):
    return resources.import_remote(body.expected_package_id)


@app.post('/api/v1/resources/activate', tags=['资源与用户数据'])
def activate_resource_version(body: ResourceActivateInput):
    with resources.import_slot():
        return resources.activate(body.package_id, body.expected_package_id)


@app.get('/api/v1/library/{kind}', tags=['资源与用户数据'])
def list_user_entries(kind: str, include_archived: bool = False,
                      limit: Annotated[int, Query(ge=1, le=200)] = 50,
                      offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    return library.list_entries(kind, include_archived, limit, offset)


@app.get('/api/v1/library-schema', tags=['资源与用户数据'])
def user_library_schema():
    """用户 document 字段的 JSON Schema；材料单位及点数组校验见各字段说明。"""
    return {'models': library.ModelDocument.model_json_schema(),
            'materials': library.MaterialDocument.model_json_schema(), 'slots': slots.SlotDocument.model_json_schema()}


@app.post('/api/v1/library/{kind}', tags=['资源与用户数据'], status_code=201)
def save_user_entry(kind: str, body: LibraryInput):
    return library.save(kind, body.name, body.document, expected_revision=body.expected_revision)


@app.get('/api/v1/library/{kind}/{identity}', tags=['资源与用户数据'])
def get_user_entry(kind: str, identity: str, revision: Annotated[int | None, Query(ge=1)] = None):
    return library.get(kind, identity, revision)


@app.put('/api/v1/library/{kind}/{identity}', tags=['资源与用户数据'])
def update_user_entry(kind: str, identity: str, body: LibraryInput):
    if body.expected_revision is None:
        raise HTTPException(422, '更新用户数据必须指定 expected_revision')
    return library.save(kind, body.name, body.document, identity, body.expected_revision)


@app.get('/api/v1/library/{kind}/{identity}/revisions', tags=['资源与用户数据'])
def user_entry_revisions(kind: str, identity: str):
    return library.revisions(kind, identity)


@app.post('/api/v1/library/{kind}/{identity}/archive', tags=['资源与用户数据'])
def archive_user_entry(kind: str, identity: str, body: ArchiveInput):
    return library.archive(kind, identity, body.expected_revision, body.archived)


@app.get("/api/v1/projects", tags=["保存的设计"])
def get_projects(include_archived: bool = False):
    return {"items": projects.list_projects(include_archived)}


@app.post("/api/v1/projects", status_code=201, tags=["保存的设计"])
def save_project(body: ProjectInput, request: Request):
    author = auth.author(request, body.author)
    origin = catalog.lineage(body.base_model_id) if body.base_model_id else {}
    bindings = body.slot_bindings.model_dump(exclude_none=True)
    slots.resolve(bindings)
    return projects.save_project(body.name, body.parameters, body.notes, origin, author, bindings, body.series)


@app.get("/api/v1/projects/{project_id}", tags=["保存的设计"])
def get_project(project_id: str):
    result = projects.get_project(project_id)
    if result is None:
        raise HTTPException(404, "设计不存在")
    return result


@app.put("/api/v1/projects/{project_id}", tags=["保存的设计"])
def update_project(project_id: str, body: ProjectUpdate, request: Request):
    author = auth.author(request, body.author)
    origin = None
    if 'base_model_id' in body.model_fields_set:
        previous = get_project(project_id).get('lineage', {})
        origin = previous if body.base_model_id and previous.get('id') == body.base_model_id else (
            catalog.lineage(body.base_model_id) if body.base_model_id else {})
    bindings = body.slot_bindings.model_dump(exclude_none=True) if 'slot_bindings' in body.model_fields_set else None
    if bindings is not None:
        slots.resolve(bindings)
    return projects.update_project(project_id, body.expected_revision, body.name, body.parameters, body.notes, origin,
                                   author, bindings, body.series)


@app.post("/api/v1/projects/{project_id}/archive", tags=["保存的设计"])
def archive_project(project_id: str, body: ArchiveInput):
    return projects.set_archived(project_id, body.archived, body.expected_revision)


@app.get("/api/v1/projects/{project_id}/revisions", tags=["保存的设计"])
def project_revisions(project_id: str):
    return {"items": projects.revisions(project_id)}


@app.get("/api/v1/projects/{project_id}/revisions/{revision}", tags=["保存的设计"])
def project_revision(project_id: str, revision: int):
    return projects.get_revision(project_id, revision)


@app.get("/api/v1/calculations", tags=["计算记录与对比"])
def list_calculations(limit: Annotated[int, Query(ge=1, le=100)] = 30,
                      offset: Annotated[int, Query(ge=0)] = 0, project_id: str | None = None,
                      include_archived: bool = False):
    return calculations.list_calculations(limit, offset, project_id, include_archived)


@app.post('/api/v1/calculations/cleanup', tags=['计算记录与对比'])
def cleanup_calculations(body: CalculationCleanupInput):
    return calculations.cleanup(**body.model_dump(mode='json'))


@app.get("/api/v1/calculations/{calculation_id}", tags=["计算记录与对比"])
def get_calculation(calculation_id: str):
    return calculations.get(calculation_id)


@app.post('/api/v1/calculations/{calculation_id}/archive', tags=['计算记录与对比'])
def archive_calculation(calculation_id: str, body: CalculationArchiveInput):
    return calculations.archive(calculation_id, body.archived)


@app.delete('/api/v1/calculations/{calculation_id}', status_code=204,
            responses={404: {'description': '记录不存在'}, 409: {'description': '被对比或寻优任务引用，不能删除'}},
            tags=['计算记录与对比'])
def delete_calculation(calculation_id: str):
    calculations.delete(calculation_id)
    return Response(status_code=204)


@app.get("/api/v1/calculations/{calculation_id}/report.pdf", response_class=Response,
         responses=PDF_RESPONSES,
         tags=["打印报告"])
def download_report(calculation_id: str):
    from .pdf_report import render
    snapshot = calculations.get(calculation_id)
    return Response(render(snapshot), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="DCSJ-{snapshot["calculation_id"]}.pdf"',
        "Cache-Control": "no-store",
    })


@app.get("/reports/{calculation_id}", response_class=HTMLResponse, tags=["打印报告"])
def calculation_report(calculation_id: str):
    from .reports import render
    return HTMLResponse(render(calculations.get(calculation_id)), headers={"Cache-Control": "no-store"})


@app.post("/api/v1/comparisons", tags=["计算记录与对比"])
def compare_calculations(body: ComparisonInput):
    return calculations.compare(body.calculation_ids, body.baseline_id)


@app.post('/api/v1/comparison-sets', status_code=201, tags=['计算记录与对比'])
def save_comparison(body: SavedComparisonInput):
    return comparisons.save(body.name, body.calculation_ids, body.baseline_id, body.notes)


@app.get('/api/v1/comparison-sets', tags=['计算记录与对比'])
def list_comparisons(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     offset: Annotated[int, Query(ge=0)] = 0):
    return comparisons.list_saved(limit, offset)


@app.get('/api/v1/comparison-sets/{comparison_id}', tags=['计算记录与对比'])
def get_comparison(comparison_id: str):
    return comparisons.get(comparison_id)


@app.get('/api/v1/comparison-sets/{comparison_id}/report.pdf', response_class=Response,
         responses=PDF_RESPONSES,
         tags=['打印报告'])
def comparison_pdf(comparison_id: str):
    from .comparison_reports import render_pdf
    saved = comparisons.get(comparison_id)
    return Response(render_pdf(saved), media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="DCSJ-comparison-{saved["id"]}.pdf"',
        'Cache-Control': 'no-store'})


@app.get('/comparison-reports/{comparison_id}', response_class=HTMLResponse, tags=['打印报告'])
def comparison_report(comparison_id: str):
    from .comparison_reports import render_html
    return HTMLResponse(render_html(comparisons.get(comparison_id)), headers={'Cache-Control': 'no-store'})


@app.get("/api/v1/modeling/templates", tags=["快速建模"])
def model_templates(power_kw: Annotated[float, Query(gt=0, le=100000, allow_inf_nan=False)],
                    poles: Annotated[int, Query(ge=2, le=64)],
                    frequency_hz: Annotated[float, Query(gt=0, le=1000, allow_inf_nan=False)] = 50,
                    phase_voltage: Annotated[float, Query(gt=0, le=50000, allow_inf_nan=False)] = 380):
    if poles % 2:
        raise HTTPException(422, "极数必须为偶数")
    return modeling.templates(power_kw, poles, frequency_hz, phase_voltage)


@app.post("/api/v1/modeling/preview", tags=["快速建模"])
def quick_model(body: QuickModelInput):
    return modeling.build(body.model_id, body.changes, body.rotor_outer_mm)


@app.post("/api/v1/geometry/preview", tags=["快速建模"])
def geometry_preview(body: DesignInput):
    return modeling.geometry(body.parameters)


@app.get("/api/v1/optimization/config", tags=["自动寻优"])
def optimization_config():
    return optimization.config()


@app.get("/api/v1/optimization/jobs", tags=["自动寻优"])
def optimization_jobs():
    return {"items": jobs.list_jobs()}


@app.post("/api/v1/optimization/jobs", status_code=202, tags=["自动寻优"])
def create_optimization(body: optimization.SearchInput):
    return jobs.create(body)


@app.get("/api/v1/optimization/jobs/{identity}", tags=["自动寻优"])
def optimization_job(identity: str):
    return jobs.get(identity)


@app.post("/api/v1/optimization/jobs/{identity}/cancel", tags=["自动寻优"])
def cancel_optimization(identity: str):
    return jobs.cancel(identity)


@app.post("/api/v1/optimization/jobs/{identity}/retry", status_code=202, tags=["自动寻优"])
def retry_optimization(identity: str):
    return jobs.retry(identity)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend/index.html")


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(ROOT / "frontend/styles.css")


@app.get("/app.js", include_in_schema=False)
def javascript():
    return FileResponse(ROOT / "frontend/app.js")


@app.get('/series-picker.js', include_in_schema=False)
def series_picker():
    return FileResponse(ROOT/'frontend/series-picker.js', media_type='application/javascript')


@app.get("/docs", include_in_schema=False)
def api_docs():
    response = get_swagger_ui_html(
        openapi_url="/openapi.json", title="DCSJ API 文档",
        swagger_js_url="/api-assets/swagger-ui-bundle.js",
        swagger_css_url="/api-assets/swagger-ui.css",
        swagger_favicon_url="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
        swagger_ui_parameters={"persistAuthorization": False},
    )
    # All interactive API writes use the same CSRF header as the workbenches.
    return HTMLResponse(response.body.decode().replace('const ui = SwaggerUIBundle({',
        "const ui = SwaggerUIBundle({requestInterceptor: req => { req.headers['X-DCSJ-Request']='1'; return req; },"))


@app.get("/api-assets/{name}", include_in_schema=False)
def docs_asset(name: str):
    if name not in {"swagger-ui-bundle.js", "swagger-ui.css"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "frontend/vendor" / name)
