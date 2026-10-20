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

方案对比、自动寻优、A4 打印报告及快速建模均有网页入口，进度及验收门槛见 [ROADMAP.md](ROADMAP.md)。

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

## 方案、报告和寻优

工作台每次计算都会保存不可变快照。在「方案对比」中选择 2–8 个快照及基准，查看指标差值、变化的输入、计算来源和警告。不同额定工况会明确提示。保存已有设计会创建新版本；其他窗口修改过该设计时返回冲突，可刷新后继续或另存。

「快速建模」按功率、极数、频率和相电压检索本地模板。修改尺寸或绕组后先校验、预览变化，再载入工作台计算。几何预览展示铁心和槽位，不输出加工图。

「自动寻优」从工作台复制一份固定输入，配置 1–6 个几何、绕组或材料变量、目标和性能约束。每个候选均调用真实计算核；额定值、价格、经验修正系数保持固定。任务支持进度、取消、失败诊断、候选复核和基准对比。预算上限为 128 次（包含基准及失败候选），搜索使用可重复的随机种子；结果只代表此次已评估候选中的最佳可行方案，不保证全局最优。

取消在当前一次计算结束后生效。任务和候选存入设计数据库；异常退出后失去心跳的任务会在约 30 秒内标记为中断，保留已有结果，可按原配置重新运行。每个数据库同时执行一个寻优任务，交互计算与寻优共享两个计算席位，最多排队八个任务。

结果页、对比列表与寻优候选均有「打印报告」入口。报告从固定快照生成 A4 页面，包含额定条件、输入、输出、曲线、尺寸图、警告及版本指纹；使用浏览器打印或保存 PDF。

## 备份与恢复

在线备份使用 SQLite 一致性备份接口，包含已提交的 WAL 数据：

```bash
python scripts/backup.py create .runtime/backups/designs-20261021.sqlite
```

记录命令返回的 SHA-256。恢复到**新的文件路径**，校验后停止服务并通过 `DCSJ_PROJECTS_DB` 切换数据库，再启动服务：

```bash
python scripts/backup.py restore .runtime/restored.sqlite --source .runtime/backups/designs-20261021.sqlite --sha256 <备份摘要>
```

备份和恢复均拒绝覆盖已有文件。设计库包含用户输入及计算结果，应保留在私有存储中。

## 部署

Docker 用户可运行 `docker compose up --build -d`。配置默认只映射 `127.0.0.1:8767`，以非 root 用户运行，并将设计库保存在命名卷中。镜像仅复制源码和空表结构。容器部署仍需在本机将授权资源分别只读挂载到 `/app/data/catalog.sqlite`、`/app/data/material_kernel_tables.json` 及 `/app/dcsj/generated/numeric_kernel.json.gz` 后才能进行实际计算；可选挂载 `/app/data/example.json`。

当前服务是单工作站/可信本地使用模式，不包含多租户权限、计费或商用许可证管理。若部署给多用户，须先在反向代理配置身份认证、TLS 和访问控制；不要直接将无认证 API 暴露到公网。升级前备份设计库，运行测试并保留旧版源码及计算资源；启动会自动迁移旧版设计表，旧版本参数会保留。
