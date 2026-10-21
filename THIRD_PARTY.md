# 第三方组件

- FastAPI、Uvicorn、Pydantic：HTTP API 及输入校验，版本固定在 `requirements.txt`，遵循各上游许可证。
- Swagger UI Dist 5.34.0：来自官方 npm 包 `swagger-ui-dist`，Apache-2.0 许可证随附于 `frontend/vendor/LICENSE`；上游 https://github.com/swagger-api/swagger-ui 。
- REA / rea-agents：本地分析阶段使用的工具，上游 https://github.com/morluto/rea ，不是运行依赖，不随源码打包。

原软件、型号、材料、分析记录及恢复的私有计算资源不在本公开仓库中。本项目没有为这些原始资源声明新的开源许可证。

- ReportLab、fontTools：PDF 生成及本地静态字体实例处理，版本固定在 `requirements.txt`。
- jsdom：前端 DOM 交互测试，MIT 许可证；仅作为开发依赖，版本与传递依赖锁定在 `package-lock.json`，上游 https://github.com/jsdom/jsdom 。
- Noto Sans SC：PDF 中文字形来源，SIL Open Font License 1.1；上游为 https://github.com/notofonts/noto-cjk 。安装脚本固定上游提交和 SHA-256，在本地生成名为 DCSJ PDF Sans 的常规字重实例，并保存原许可证。字体文件不进入公开源码仓库；PDF 嵌入所需字形子集。[上游许可证](https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/LICENSE)
