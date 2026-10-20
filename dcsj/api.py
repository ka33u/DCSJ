"""Local web workbench and versioned engineering API."""
import math
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__, catalog, projects, calculations, modeling
from .execution import BusyError, compute
from .storage import ConflictError, NotFoundError
from .parameters import EXAMPLE, ROOT, InputError, schema
from .engine import CalculationUnavailableError

app = FastAPI(
    title="DCSJ 电磁设计 API", version=__version__,
    description="三相异步电动机设计工作台。相电压、极数和原材料表语义见参数 schema。"
                "历史型号记录与本次重新计算的结果分开提供。",
    docs_url=None, redoc_url=None,
)


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
        return values


class ProjectInput(DesignInput):
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=4000)

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
    name: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    project_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def project_link(self):
        if (self.project_id is None) != (self.project_revision is None):
            raise ValueError("关联项目时须同时提供 project_id 和 project_revision")
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


class QuickModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=100)
    changes: dict[str, str | float | int | None] = Field(default_factory=dict)


@app.exception_handler(NotFoundError)
async def not_found_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(CalculationUnavailableError)
async def unavailable_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=503)


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
    if request.url.path in {"/", "/app.js", "/styles.css"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


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
            "catalog": catalog.stats(), "engine": engine_status()}


@app.get("/api/v1/schema", tags=["设计"])
def get_schema():
    return schema()


@app.get("/api/v1/example", tags=["设计"])
def example():
    if EXAMPLE is None:
        raise HTTPException(404, "未安装私有示例，请导入自己的设计 JSON 或安装本地型号库。")
    return EXAMPLE


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
):
    return catalog.models(q, series, limit, offset, history)


@app.get("/api/v1/models/{model_id}", tags=["型号库"])
def get_model(model_id: str):
    result = catalog.model(model_id)
    if result is None:
        raise HTTPException(404, "型号不存在")
    return result


@app.post("/api/v1/calculate", tags=["计算"])
def calculate(body: CalculationInput):
    from .engine import CalculationError
    if body.project_id is not None:
        projects.get_revision(body.project_id, body.project_revision)
    try:
        result = compute(body.parameters)
        return calculations.save(result, body.name, body.project_id, body.project_revision)
    except BusyError as error:
        raise HTTPException(429, str(error), headers={"Retry-After": "2"}) from error
    except CalculationError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/v1/materials", tags=["材料库"])
def get_materials():
    return {"items": catalog.materials()}


@app.get("/api/v1/materials/{material_id}", tags=["材料库"])
def get_material(material_id: str):
    result = catalog.material(material_id)
    if result is None:
        raise HTTPException(404, "材料不存在")
    return result


@app.get("/api/v1/projects", tags=["保存的设计"])
def get_projects(include_archived: bool = False):
    return {"items": projects.list_projects(include_archived)}


@app.post("/api/v1/projects", status_code=201, tags=["保存的设计"])
def save_project(body: ProjectInput):
    return projects.save_project(body.name, body.parameters, body.notes)


@app.get("/api/v1/projects/{project_id}", tags=["保存的设计"])
def get_project(project_id: str):
    result = projects.get_project(project_id)
    if result is None:
        raise HTTPException(404, "设计不存在")
    return result


@app.put("/api/v1/projects/{project_id}", tags=["保存的设计"])
def update_project(project_id: str, body: ProjectUpdate):
    return projects.update_project(project_id, body.expected_revision, body.name, body.parameters, body.notes)


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
                      offset: Annotated[int, Query(ge=0)] = 0, project_id: str | None = None):
    return calculations.list_calculations(limit, offset, project_id)


@app.get("/api/v1/calculations/{calculation_id}", tags=["计算记录与对比"])
def get_calculation(calculation_id: str):
    return calculations.get(calculation_id)


@app.post("/api/v1/comparisons", tags=["计算记录与对比"])
def compare_calculations(body: ComparisonInput):
    return calculations.compare(body.calculation_ids, body.baseline_id)


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
    return modeling.build(body.model_id, body.changes)


@app.post("/api/v1/geometry/preview", tags=["快速建模"])
def geometry_preview(body: DesignInput):
    return modeling.geometry(body.parameters)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend/index.html")


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(ROOT / "frontend/styles.css")


@app.get("/app.js", include_in_schema=False)
def javascript():
    return FileResponse(ROOT / "frontend/app.js")


@app.get("/docs", include_in_schema=False)
def api_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title="DCSJ API 文档",
        swagger_js_url="/api-assets/swagger-ui-bundle.js",
        swagger_css_url="/api-assets/swagger-ui.css",
        swagger_favicon_url="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
        swagger_ui_parameters={"persistAuthorization": False},
    )


@app.get("/api-assets/{name}", include_in_schema=False)
def docs_asset(name: str):
    if name not in {"swagger-ui-bundle.js", "swagger-ui.css"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "frontend/vendor" / name)
