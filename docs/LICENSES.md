# 开源许可证说明

当前代码优先使用商业友好的组件：

- FastAPI / Starlette / Pydantic
- HTTPX
- SQLAlchemy / Alembic
- Shapely / GEOS 用于核心几何计算，交付前需确认 wheel 分发和底层 GEOS 许可证义务
- Vue / Vite / Element Plus / Pinia

交付前可生成机器可读依赖/许可证清单：

```powershell
python scripts\release_inventory.py --output artifacts\dependency-inventory.json
```

发布预检也可通过 `python scripts\release_preflight.py --report-path artifacts\release-preflight.json --inventory-path artifacts\dependency-inventory.json` 在验收报告中写入依赖/许可证摘要。清单会解析 `backend/pyproject.toml` 和 `frontend/package-lock.json`，优先读取 Python 包的 `License-Expression`，再回退 `License` 和许可证 classifier，并标记 GPL/AGPL/LGPL/SSPL/BUSL/未知许可证等需要人工复核的依赖。

如果某个 Python 依赖在当前环境没有安装，清单会把它标为 `installed=false` 并计入 `missing_install_count`，表示需要在正式 release image 或完整依赖环境中重新生成清单；这类项不是许可证风险结论，只是当前机器无法读取包元数据。

需要重点审查：

- PackingSolver、Sparrow、Clipper2、pstoedit、UniConvertor、Ghostscript、Inkscape、LibreOffice 等组件的许可证和分发方式。
- GPL/AGPL 组件优先作为独立服务或命令行隔离，并在客户交付前由法务确认。
- 商业 Phoenix/Esko 类集成只通过 Adapter，不写死到核心数据结构。
