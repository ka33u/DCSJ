# 私有资源与用户库

公开仓库只包含空表结构 `schema.sql`。型号、材料、示例、分析记录及计算指令资源不进入公开仓库或源码镜像。

## 部署后一键导入

1. 为私有资源仓库创建仅授予 **Contents: read** 权限的 GitHub fine-grained token，仓库默认 `ka33u/DCSJ-SYSJ`。在服务端配置 `DCSJ_RESOURCE_TOKEN`，或把令牌放在受保护文件中并设置 `DCSJ_RESOURCE_TOKEN_FILE`。凭据不通过网页输入，也不保存在用户数据库、导出文件或日志里。
2. Docker Compose 用户复制 `.env.example` 为被 Git 忽略的 `.env`，填写只读令牌，运行 `docker compose up --build -d`。原生 Python 启动需在进程环境中设置这些变量；程序不会自动读取 `.env`。
3. 打开工作台「资源与用户数据」，点击「从私有仓库一键导入」。导入成功后立即可用，无需重启。第一次配置服务端环境变量后需重启进程以读取配置。

应用验证仓库仍为私有，将 `DCSJ_RESOURCE_REF`（默认 `main`）解析到固定提交，在同一提交读取 `manifest.json` 和 `resource-bundle.zip`，校验整个包及每个文件的 SHA-256、大小、数据库结构、材料表、计算接口 ABI 和最低应用版本。资源先在独立目录校验，完成后原子切换当前版本；失败保留原版本。同一压缩包重复导入不产生副本。在线资源按 3 MiB 分段，通过 GitHub 文件对象的 JSON/Base64 编码传输，逐段核验后重组原始压缩包，同时校验 Git 对象标识，避免文本网关破坏压缩包。下载只访问 `api.github.com`，不跟随重定向；服务端需能访问该域名。

导入及切换 API：

- `GET /api/v1/resources`：当前包、已安装版本、仓库配置状态；不返回令牌。
- `POST /api/v1/resources/import`，请求 `{"expected_package_id":null}`（首次安装）或当前包的 64 位标识。
- `POST /api/v1/resources/activate`，请求 `{"package_id":"<已安装包>","expected_package_id":"<当前包>"}`。

版本已被其他窗口修改时返回 409。已有排队或运行中的寻优任务时拒绝切换，避免一次搜索混用资源。每次计算固定使用开始时的资源包，结果记录包版本及哈希。已安装旧版本不会自动删除，可在页面选择并切回。浏览器导入超时不代表服务端取消，应先刷新状态再决定是否重试。

离线安装支持从私有仓库取得资源包后，在服务器运行：

```bash
python scripts/import_resources.py --file /private/path/resource-bundle.zip
# 或使用相同的服务端凭据直接下载：
python scripts/import_resources.py --github
```

容器可把压缩包复制到持久卷后执行 `docker compose exec dcsj python scripts/import_resources.py --file /app/.runtime/resource-bundle.zip`，导入后可删除该压缩包。资源解压到 `DCSJ_DATA_DIR/resources/<包 SHA-256>/`，与 `projects.sqlite` 同一持久卷，首次安装需为压缩包、解压副本和校验阶段预留足够磁盘空间；单包限制压缩 64 MiB、解压 512 MiB。

## 手工型号和材料

材料逐行表采用 B1/B2(G) 和 H1(A/cm)，B1、B2 分别维护，支持增删、批量替换和预览；保存时 B 转回 T、H 转为所选单位。复制原材料时读取原始采样点，不把绘图原点或插值结果写入用户库；未改动的导入点保留原始精度。批量输入可选原版 G/A/cm 或 T/所选 H 单位，现有 JSON 格式保持兼容。

「资源与用户数据」可新增、修改、导出和导入用户 JSON，归档或恢复记录。型号可复制当前工作台完整参数，也可粘贴自己的参数对象；保存时校验必要输入，额外原始字段仍保留。草稿可先保存在「我的设计」。材料支持录入 B–H 与 B–损耗曲线、频率、单位、来源和备注，保存后可绘图。

用户记录保存在独立用户库，不写回基础型号库。身份由 ID 和修订号确定；同名基础数据和多个同名用户记录可并存。修改使用 `expected_revision`，并发冲突返回 409；每次修改创建不可变版本。归档不删除历史版本，既有型号、计算和寻优仍可引用固定版本。

API 路径 `/api/v1/library/{kind}` 中 `kind` 为 `models` 或 `materials`：

- GET 分页列表，支持 `limit`、`offset`、`include_archived`。
- POST 新增，提交 `name`、`document`，不指定 `expected_revision`。
- GET `/{id}?revision=N` 读取指定版本，省略时读取最新版本。
- PUT `/{id}` 提交 `name`、`document`、`expected_revision`。
- GET `/{id}/revisions` 读取历史版本清单。
- POST `/{id}/archive` 提交 `archived` 和 `expected_revision`。

`GET /api/v1/library-schema` 返回两种 document 的 JSON Schema。型号 document 为 `{"parameters":{...},"source":"来源","notes":"备注"}`。材料格式为：

```json
{
  "source": "实测或供应商资料编号",
  "notes": "测量条件与适用范围",
  "frequency_hz": 50,
  "h_unit": "A/m",
  "loss_unit": "W/kg",
  "bh": [[0.5, 100], [1.88, 10000]],
  "loss": [[0.5, 1], [1.88, 10]]
}
```

以上点值仅演示接口格式，不是可用于设计的材料数据。每条曲线 2–2000 点，B 单位 T，必须严格递增，纵轴非负且单调不减；不接受重复、无穷或无效点。H 支持 A/m、A/cm，损耗支持 W/kg、W/m³（API 值 `W/m3`）和显式声明的 `kernel_native`。

**W/kg、W/m³ 可管理和绘图，但暂不自动映射到原计算核损耗单位。** 只有已确认数值采用 `kernel_native`、50 Hz、两条曲线均覆盖 0.5–1.88 T 的材料可用于当前计算核。H 转为 A/cm，在原采样轴内线性插值，不外推；其他计算常量沿用私有资源的自定义材料槽位。此适配仍需工程校核，不能把更改单位标签当作数值换算。

材料的固定引用形如 `user-material:ul_<ID>@<版本>`，用于设计参数 `Paihao`；型号引用形如 `user-model:ul_<ID>@<版本>`，可由型号 API 和快速建模 API 使用。页面会把当前兼容材料的固定引用写入设计，并保留旧版本。导出用户记录使用 `dcsj-user-entry` v1；导入先预览，保存为新 ID，避免覆盖。跨安装迁移单个型号时，如果它引用用户材料，应先导入对应材料，再在工作台选择新的材料引用；完整迁移可使用数据库备份恢复。

## 备份、更新与旧安装兼容

用户库与设计、计算记录一起由 `scripts/backup.py` 备份；资源包目录需另外备份，或保留对应私有提交和压缩包以便重装。数据库升级至结构版本 4，保留已有设计及历史记录。资源版本回退不回退用户数据；应用版本回退需使用升级前的数据库备份。

旧版手工放置的文件继续兼容：`data/catalog.sqlite`、`data/material_kernel_tables.json`、`dcsj/generated/numeric_kernel.json.gz`、可选 `data/example.json`。未激活版本化包时使用这些资源；手工替换旧路径需重启服务。型号库也支持 `catalog.sqlite.gz` + `catalog_bundle.json`。`DCSJ_CATALOG_PATH` 显式设置时优先于资源包内的型号目录，界面会显示提示；要完全采用版本化资源，请取消该覆盖。

缺少型号库时提供空目录，缺少计算资源时计算返回 503。构建新版私有包可运行 `python scripts/build_resources.py --source-root <资源根目录> --output <私有输出目录> --version <版本>`，将生成的 `manifest.json`、`resource-bundle.zip` 及清单列出的 `.partNNN` 文件一起提交到私有资源仓库。不要把暂存 `files/`、设计库或令牌提交到公开仓库。发布源码前运行 `scripts/check_public_scope.py`。
