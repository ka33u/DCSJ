# DCSJ 电磁设计工作台

面向三相异步电动机设计的中文网页工作台，提供版本化 HTTP API 和离线接口文档。支持 macOS、Windows 和 Linux，要求 Python 3.12 或更新版本；CI 使用 Python 3.12 验证。

本仓库只发布应用源码、通用测试和部署说明。**不包含型号库、材料数据、原软件、分析资料及私有计算资源。** 全新克隆可启动界面、API、方案管理；实际电磁计算需要在本机安装授权的私有资源，见 [安装资源说明](data/README.md)。

## 启动

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 使用 .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/install_pdf_font.py
python run.py
```

工作台：http://127.0.0.1:8767 ，API 文档：http://127.0.0.1:8767/docs 。也可使用 `start.command` 或 `start.bat`。

启动脚本会核对 `requirements.txt` 中每个依赖的固定版本，缺失或不一致时安装对应版本；再次启动且版本一致时跳过安装。首次安装及升级依赖需要网络。已有 `.venv` 若使用低于 3.12 的 Python，会明确报错并停止；请先安装受支持的 Python，再重命名旧 `.venv` 并重新创建虚拟环境，设计数据无需搬动。

设计、计算记录保存在 `.runtime/projects.sqlite`，与只读型号库分开。通过 `DCSJ_DATA_DIR` 改变运行数据目录，或通过 `DCSJ_PROJECTS_DB` 单独指定设计数据库路径。服务默认只监听本机，适合个人工作站。

## 功能

- 参数编辑、JSON 设计交换、可选的本地型号与材料库。
- 私有资源一键导入、校验、版本切换；独立用户型号和材料曲线库，历史版本、同名并存及单位兼容检查。
- 输入校验、电磁计算、性能曲线和明确的计算状态。
- 方案版本历史、并发修改冲突保护、归档与恢复。
- 不可变计算记录；API 支持多方案指标、变化参数及基准差值对比。
- 保存对比与评审说明，重新打开固定结果，输出含全部指标及计算来源的 A4 横向对比报告。
- 快速建模 API：按额定需求寻找本地模板、显式参数修改、转子外径与气隙联动、尺寸预览。

方案对比、自动寻优、A4 打印报告及快速建模均有网页入口，进度及验收门槛见 [ROADMAP.md](ROADMAP.md)。

## API

`GET /api/v1/status` 查看资源与服务状态；`GET /api/v1/schema` 获取字段及单位。
`POST /api/v1/calculate` 接收 `{"parameters": {...}}`，成功后生成独立计算记录。
`POST /api/v1/comparisons` 接收 `calculation_ids` 列表及可选 `baseline_id`。
`POST /api/v1/comparison-sets` 在上述参数中增加 `name` 和可选 `notes`，保存对比；`GET /api/v1/comparison-sets` 分页列出已存记录，`GET /api/v1/comparison-sets/{id}` 返回固定结果。
`GET /api/v1/resources` 查看已安装资源，`POST /api/v1/resources/import` 从私有仓库导入。用户库 API 为 `/api/v1/library/{models|materials}`，文档结构见 `/api/v1/library-schema`；详情见 [资源与用户库说明](data/README.md)。
完整请求、响应和校验规则以 `/docs` 为准。

保存项目允许未完成的草稿；计算时会严格校验。输入 `Ufai` 是绕组相电压。尺寸预览是设计辅助图，不能直接用于加工。

## 0.7.0 接口与计算语义

本版处理 issue #1 的九类问题，默认数值路径保持原版行为。升级会将用户库结构迁移至 v5；历史计算快照不重算、不改写。升级前备份 `.runtime/projects.sqlite`，回退程序时使用升级前备份。

- **要求与初值分开**：`ATA`、`PF`、`IST` 是原版迭代初值，会影响收敛。新增 `ATA_req`、`PF_req`、`IST_req`、`TST_req`、`TM_req` 只参与结果判定和报告目标，不写入数值核。效率、功率因数为 0–1；起动电流为上限，其余为下限。未设置 `*_req` 时，目标比较兼容旧字段。`requirements` 返回判定依据，`core_input_sha256` 与 `requirements_sha256` 分别追踪两类输入。
- **损耗单位显式化**：`pfw_unit`、`ps_unit` 可填 `W`、`pu`、`legacy`，默认 `legacy`。原规则在单精度转换后以 `>1` 判断 W，否则为额定输出功率比例；显式 `W` 支持 0.8 W、1 W。`loss_semantics` 返回解析单位、核内比例和额定损耗 W。机械损耗在负荷曲线上保持固定 W，杂散损耗按输出功率变化。仅设置正数 `pfw_ref_rpm` 时，启用 `(当前同步转速/参考同步转速)^pfw_speed_exponent` 工程估算，指数默认 2；不自动假设每台机器都适用转速平方律。
- **材料频率**：`material_semantics.loss_curve_frequency_hz=null` 表示原表未声明实测频率；`loss_frequency_model` 单独列出核内 `(f/50)^1.33` 折算及实际因子。这不是实测 60 Hz 材料数据。用户材料继续按现有单位和频率兼容性校验，不能将 60 Hz 实测表直接当作 50 Hz 基表输入。
- **校核与曲线**：`values` 为规范数值输出，`metrics` 是同值元数据视图。`IST1`、`TST1`、`TM1` 用于程序内设计校核与寻优；`curves[].authoritative=false` 表示趋势采样，`curve_checks` 单独返回起动点、采样峰值与校核输出的差值。未拉伸曲线去强制匹配标量输出；该标记不构成实机验证结论。
- **单位契约**：`values_units` 对应原始输出单位；原磁密 G 保留，`values_si` 和 `metrics[].si` 同时给 T。`electrical` 返回接法、相电压和线电压，Y 线电压按 √3 换算，△ 相线电压相同。效率原始数值为比例，图上显示百分比。
- **严格输入**：所有新 HTTP 参数和 overrides 按规范字段名检查。`Kw`、未知字段及历史输出列返回 422，不再静默忽略；请从型号详情的 `inputs` 或 `/example` 取得纯输入。受信任的旧库读入仍兼容 Access 大小写，旧项目未识别原列保留在 `raw_parameters`。备注应放在顶层 `notes`，不要放进 `parameters`。旧导出 JSON 如夹带输出列，需按 schema 的 input 字段清理后再提交。

`GET /api/v1/models` 新增 `frequency_hz`、`poles`、`voltage_v`（相电压）、`power_min`、`power_max`、`frame`（机座数字，如 `180`）、`source_table` 过滤，可与分页及 history 组合。详情 `inputs`/`parameters` 只含输入，`raw_parameters` 和 `historical_results` 分别保留源行和历史结果。

直接从型号试算：

```http
POST /api/v1/models/<model_id>/calculate
Content-Type: application/json

{"overrides":{"f":60,"Ufai":460,"ATA_req":0.92,"pfw_unit":"W","pfw":200},"persist":false}
```

通用 `/calculate` 同样支持 `persist:false`，默认 true。试算返回 `calculation_id:null`、`persisted:false`，不创建计算记录、审计事件或报告。`base_model_id` 可用于计算和项目保存，来源由服务器查库解析；项目版本和后续对比保留原型号、来源文件摘要和资源包版本。更新项目省略该字段时保留原来源，显式 null 清除来源。来源标识表示派生基础，不表示输入与基础型号完全相同。

`POST /api/v1/calculations/{id}/archive` 接收 `{"archived":true}`，改为 false 恢复；默认列表隐藏归档项，`include_archived=true` 可查看。`DELETE /api/v1/calculations/{id}` 删除未被引用的记录，保存的对比及寻优引用会返回 409。寻优候选继续保留以便核对，可以归档隐藏。

批量清理 `POST /api/v1/calculations/cleanup` 接收 `name_prefix`、带时区的 `after`（含）/`before`（不含），条件组合为 AND，必须至少指定一个。`action` 为 `archive`（默认）或 `delete`；默认 `dry_run:true` 仅预览。返回匹配数量、可处理 ID、跳过原因、是否截断；`limit` 默认 100、最多 1000。确认范围后提交 `dry_run:false` 执行，受引用记录始终不能删除。

PDF 客户端先探测 `/status` 的 `reports.pdf.available`。缺失、损坏或无法读取字体时保持兼容的 503 状态，但返回 `code=PDF_FONT_UNAVAILABLE`、`retryable:false`、`setup` 和 `html_report_url`，并设置 `Content-Location`。安装字体后再请求，不应盲目重试；OpenAPI 已声明相关响应。`embedded` 指生成 PDF 时嵌入字体，**不表示 HTML 内嵌字体**。网页版仍可打印。

## 测试与发布

```bash
python -m pip install -r requirements-dev.txt
python scripts/install_pdf_font.py
python -m pytest -q
npm ci --ignore-scripts --registry=https://registry.npmjs.org
npm test
python tests/smoke_startup.py
python scripts/check_public_scope.py
python scripts/package_release.py
```

公共测试使用人工构造的测试输入，不携带真实型号或材料数据。前端测试需要 Node.js 24.15 以上的 24.x 版本或 26 及以上版本，通过 jsdom 执行实际 HTML 和脚本，检查型号载入、项目版本与归档、快照切换和异步请求顺序；不连接真实服务，也不替代浏览器布局和打印验收。运行应用本身无需 Node.js。私有数值验证仅在安装相应本地资源后运行。CI 覆盖 Windows、macOS 和 Linux；打包脚本只收录通过路径检查的 Git 源码文件。

`smoke_startup.py` 在包含中文和空格的临时源码目录运行对应平台的真实启动脚本：创建新虚拟环境、安装依赖、通过 HTTP 保存及修改人工方案，然后重启并核对版本历史。测试使用独立设计库和已安装的字体，需要安装依赖的网络权限，不读取工作站的设计数据库。

已有数值核仍需实际电机及第三方计算校核，材料损耗单位和最大转矩估计差异有待工程确认。当前版本不宣称完成商用认证或全部设计空间验证。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 方案、报告和寻优

0.6.4 将工作台、网页打印及 PDF 负荷图的效率纵轴统一显示为 0–100%，功率因数保留 0–1，便于区分两项指标。仅在渲染时同步缩放效率坐标和刻度，曲线形状及拟合方法不变；已保存的合并负荷图也适用，无需重新计算。API 与快照中的原始值及 `plot` 仍保留原来的比值单位，工作台原始数据表明确标注单位。

0.6.3 补齐转子圆端槽（zc=3）、分段槽（zc=4）、双笼槽（zc=5–8）及常见自定义元素槽（zc>20，元素号1/2/4/5）的报告尺寸示意。分段槽按肩角正负区分对称与单侧轮廓；双笼槽保留独立的槽口、连接颈宽与笼间高度。自定义槽沿用原绘图的圆段和首肩高度，若与输入总深不同则同时列出两值，不修改计算输入。负槽口表示的闭口槽、负槽口高度及未确认的特殊分支仍显示对应尺寸/元素表。

0.6.2 将单方案报告首页改为紧凑设计摘要：集中展示额定条件、带尺寸标注的定子与转子槽型、铁心尺寸、绕组导线、设计目标与计算结果、运行与电磁指标。目标取自保存的输入，效率以百分比显示，差值为百分点；最大转矩原公式值与曲线采样峰值分别列出。网页打印和 PDF 均取消“适用边界与计算提示”板块。完整输入、输出和来源仍在后续页。

0.6.1 修正了旧版曲线显示兼容性。M–s 恢复从 1 到 0 的横轴及电流、转矩独立刻度；负荷图恢复 P1、Sn、I1、cosφ、η 五条曲线，以原版文本精度对 9 点作二次最小二乘拟合，显示 20%–150%。30%–120% 外的趋势线是外推。M–s 沿用前 49 点及显示原点，采用有界自然三次样条；原版的边界索引异常未照搬，平滑细节可能有微小差异。

曲线 API 的 `series[].points` 保留原始数值，新增 `plot` 保存显示坐标、独立纵轴、方向、拟合方法及说明。`load_characteristic` 提供合并负荷图；原有三个负荷分图仍返回，标记 `display:false`。网页和报告读取相同显示数据。历史计算快照保持原版本，需重新计算才会生成新曲线。

材料图按原版使用 H1(A/cm) 或 Pfe 作横轴、B1/B2(G) 作纵轴，原始 API 数据仍以 B(T) 及声明的 H 单位返回。材料编辑器提供两张独立采样表、增删行、两种单位格式批量输入及保存前预览；B1 与 B2 不要求同轴或同行数。私有基础材料显示旧版原点，手工材料只连接实际录入点。旧版损耗轴虽标作 W/cm³，物理单位仍待核验，不自动转换为 W/kg。

工作台每次计算都会保存不可变快照。在「方案对比」中选择 2–8 个快照及基准，查看指标差值、变化的输入、计算来源和警告。不同额定工况会明确提示。保存已有设计会创建新版本；其他窗口修改过该设计时返回冲突，可刷新后继续或另存。

运行对比后填写名称与评审说明，点击「保存对比并生成报告」。保存时由服务端读取计算记录，固定方案顺序、基准、指标、单位、差值及来源；后续修改设计或升级不会重算这份对比。缺失指标保留为空，基准为零时不计算变化率。页面底部可重新打开已保存对比，也可通过 `/comparison-reports/{id}` 网页打印或 `/api/v1/comparison-sets/{id}/report.pdf` 直接下载中文 PDF。报告采用 A4 横向，每组重复基准并列出最多三个候选，包含所有对比指标、变化输入、警告和计算来源。完整单方案参数、曲线及尺寸图仍见对应计算报告。

「快速建模」按功率、极数、频率和相电压检索本地模板。修改尺寸或绕组后先校验、预览变化，再载入工作台计算。几何预览展示铁心和槽位，不输出加工图。

选用模板后，可将「气隙输入方式」切换为「由转子外径 D2 联动气隙」。输入转子外径后，按 `g = (Di1 - D2) / 2` 计算径向气隙；修改定子内径时保持 D2 并重新计算气隙。预览列出公式、输入尺寸和修改前后的参数，载入前必须重新校验。切回直接输入后可以独立编辑 g。几何约束为 `0 ≤ Di2 < D2 < Di1 < D1`；联动不推算槽形、绕组、材料或性能。

对应 API 为 `POST /api/v1/modeling/preview`：在 `model_id`、`changes` 外增加可选的数值 `rotor_outer_mm`（mm），例如 `{"model_id":"<模板 ID>","changes":{"Di1":182},"rotor_outer_mm":178.4}` 会得到约 1.8 mm 的气隙。使用联动时 `changes` 不能同时指定 `g`，冲突或无效尺寸返回 422。响应 `linkages` 给出公式、输入值和派生值；省略 `rotor_outer_mm` 时保留原来的直接修改方式。

「自动寻优」从工作台复制一份固定输入，配置 1–6 个几何、绕组或材料变量、目标和性能约束。每个候选均调用真实计算核；额定值、价格、经验修正系数保持固定。任务支持进度、取消、失败诊断、候选复核和基准对比。预算上限为 128 次（包含基准及失败候选），搜索使用可重复的随机种子；结果只代表此次已评估候选中的最佳可行方案，不保证全局最优。

取消在当前一次计算结束后生效。任务和候选存入设计数据库；异常退出后失去心跳的任务会在约 30 秒内标记为中断，保留已有结果，可按原配置重新运行。每个数据库同时执行一个寻优任务，交互计算与寻优共享两个计算席位，最多排队八个任务。

结果页、对比列表与寻优候选提供「网页打印」和「下载 PDF」。单方案的网页及 PDF 报告均从固定快照生成，包含紧凑设计摘要、目标值对照、槽型与尺寸、曲线、完整输入输出及版本指纹。PDF 直接由本机服务生成，嵌入中文字体和矢量图，并处理 A4 分页、重复表头及页码，不依赖浏览器打印设置。

首次运行 `python scripts/install_pdf_font.py` 会从固定版本的 Noto 官方仓库下载字体和许可证、核对 SHA-256，再生成本地常规字重实例。字体缓存默认在 `.runtime/fonts/`，不进入公开仓库。已有字体通过校验后可完全离线重用；PDF 生成过程没有网络请求。`start.command`、`start.bat` 会尝试安装，Docker 构建会预装。也可通过 `DCSJ_PDF_FONT` 指定具有所需字形及嵌入许可的 TrueType 字体。缺少字体时 PDF 接口返回 503，网页打印仍可使用；字体不支持输入字符时返回明确错误，避免静默输出方框。

单方案 PDF API：`GET /api/v1/calculations/{id}/report.pdf`。两类报告共享一个 PDF 生成席位，繁忙时返回 429；下载响应不缓存设计资料。

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

Docker 用户可运行 `docker compose up --build -d`。配置默认只映射 `127.0.0.1:8767`，以非 root 用户运行，并将设计库保存在命名卷中。镜像仅复制源码和空表结构。服务端配置私有资源仓库的只读令牌后，可在「资源与用户数据」中一键导入，无需重建镜像或重启；资源与独立用户库都存入持久卷。离线导入、版本切换、手工材料曲线格式及旧路径兼容见 [资源与用户库说明](data/README.md)。

当前服务是单工作站/可信本地使用模式，不包含多租户权限、计费或商用许可证管理。若部署给多用户，须先在反向代理配置身份认证、TLS 和访问控制；不要直接将无认证 API 暴露到公网。升级前备份设计库，运行测试并保留旧版源码及计算资源；启动会自动迁移旧版设计表，旧版本参数会保留。

程序拒绝打开结构版本高于自身支持版本的设计库，不会将其版本号降级。需要回退应用时，应将升级前备份恢复到新路径再切换；新版本创建的数据仍保留在原数据库中。备份及恢复的 SHA-256 校验采用分块读取，避免大设计库占用等量内存。
