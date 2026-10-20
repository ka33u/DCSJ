# 本地私有资源

公开仓库仅包含空表结构 `schema.sql`，不包含型号、材料、示例、分析记录或计算指令资源。

持有授权私有资源的用户可在本机放置下列文件，然后重启服务：

- `data/catalog.sqlite`：只读型号及材料目录；也支持 `DCSJ_CATALOG_PATH` 指定绝对路径。
- `data/material_kernel_tables.json`：计算所需材料表。
- `dcsj/generated/numeric_kernel.json.gz`：与当前数值解释器兼容的计算资源。
- `data/example.json`：可选的本地设计示例。

型号库也可通过本地 `catalog.sqlite.gz` + `catalog_bundle.json` 校验解压；这些文件同样不公开。
缺少型号库时提供空目录；缺少计算资源时计算接口返回 503，不生成模拟结果。
资源目录已被 `.gitignore` 排除；发布前运行 `scripts/check_public_scope.py`。
