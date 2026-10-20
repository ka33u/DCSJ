# DCSJ 电磁设计工作台

面向三相异步电动机设计的中文网页工作台，提供版本化 HTTP API 和离线接口文档。支持 macOS、Windows 和 Linux，使用 Python 3.12。

本仓库只发布应用源码、通用测试和部署说明。**不包含型号库、材料数据、原软件、分析资料及私有计算资源。** 全新克隆可启动界面、API、方案管理；实际电磁计算需要在本机安装授权的私有资源，见 [安装资源说明](data/README.md)。

## 启动

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 使用 .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

工作台：http://127.0.0.1:8767 ，API 文档：http://127.0.0.1:8767/docs 。也可使用 `start.command` 或 `start.bat`。

设计、计算记录保存在 `.runtime/projects.sqlite`，与只读型号库分开。通过 `DCSJ_DATA_DIR` 改变运行数据目录，或通过 `DCSJ_PROJECTS_DB` 单独指定设计数据库路径。服务默认只监听本机，适合个人工作站。

## 功能

- 参数编辑、JSON 设计交换、可选的本地型号与材料库。
- 输入校验、电磁计算、性能曲线和明确的计算状态。
- 方案版本历史、并发修改冲突保护、归档与恢复。
- 不可变计算记录；API 支持多方案指标、变化参数及基准差值对比。
- 快速建模 API：按额定需求寻找本地模板、显式参数修改、尺寸预览。

自动寻优、打印报告和更完整的网页流程持续开发，进度及验收门槛见 [ROADMAP.md](ROADMAP.md)。

## API

`GET /api/v1/status` 查看资源与服务状态；`GET /api/v1/schema` 获取字段及单位。
`POST /api/v1/calculate` 接收 `{"parameters": {...}}`，成功后生成独立计算记录。
`POST /api/v1/comparisons` 接收 `calculation_ids` 列表及可选 `baseline_id`。
完整请求、响应和校验规则以 `/docs` 为准。

保存项目允许未完成的草稿；计算时会严格校验。输入 `Ufai` 是绕组相电压。尺寸预览是设计辅助图，不能直接用于加工。

## 测试与发布

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/check_public_scope.py
python scripts/package_release.py
```

公共测试使用人工构造的测试输入，不携带真实型号或材料数据。私有数值验证仅在安装相应本地资源后运行。CI 覆盖 Windows、macOS 和 Linux；打包脚本只收录通过路径检查的 Git 源码文件。

已有数值核仍需实际电机及第三方计算校核，材料损耗单位和最大转矩估计差异有待工程确认。当前版本不宣称完成商用认证或全部设计空间验证。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。
