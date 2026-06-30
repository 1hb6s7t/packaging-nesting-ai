# 企业级包装印刷智能拼版优化系统

这是按技术方案落地的企业级包装印刷智能拼版优化系统工程骨架。当前实现聚焦第一阶段可运行闭环：

- FastAPI 后端、Vue3 前端、SQLAlchemy 2.x 模型、Alembic 迁移
- 订单、纸张、版图元数据、Polygon Asset、拼版任务、方案结果的数据库持久化
- SVG/DXF 优先的文件预检入口，CDR/AI/PDF 可归档后提交外部转换服务
- 文件转换作业记录：`file_conversion_job` 保存源格式、目标格式、状态、SLA 元数据、供应商回调 token hash/尾号/轮换记录、异常码映射和人工/外部转换日志；读取接口会遮蔽历史明文 token，只暴露 hash/尾号等审计摘要；成功结果会回写 normalized SVG/DXF、`artwork_version` 和 Polygon JSON，逾期作业会标记为 `overdue` 并写入站内通知
- SVG `rect/polygon/polyline/path/circle/ellipse` 基础解析，DXF `LWPOLYLINE/LINE` 闭环基础解析
- CSV/XLSX 订单文件上传导入
- Polygon JSON、Shapely 精确几何计算（多边形相交、包含、间距、差集空白面积、offset、自交修复）、规则引擎、规则集版本化、SolverAdapter、Validator、SVG 预览、报告；创建拼版任务会拒绝空候选和重复 item_id，Validator 同时校验候选项必须且只能处于 placed/unplaced 之一，阻断重复 job item、重复放置、重复未放置、漏报、未知 item 引用、order_id 不一致和空 unplaced 原因；方案报告会输出 validation 摘要、issue code、放置/未放置明细和 waste 成本估算
- solver_run / solver_run_log 运行生命周期记录
- operation_log 写操作审计查询
- 默认管理员登录、Bearer Token 鉴权、基础 RBAC 权限校验和企业角色模板
- `/api/rbac/*` 用户、角色、权限管理接口和前端权限管理页，内置 `admin`、`print_planner`、`production_operator`、`solution_approver`、`auditor`、`operations_manager`、`integration_manager`、`benchmark_engineer`
- 前端路由和侧边栏按登录状态与 RBAC 权限过滤，未登录访问受保护页面会回到登录页，登录成功后回到原目标页
- `/api/rules/*` 规则集版本、启用状态和执行日志接口，订单评分/候选筛选会写入 `rule_execution_log`
- 规则表达式使用受限白名单解释器，只允许订单字段、比较/布尔/三元表达式和 `normalize`、`due_date_score`、`geometry_fit_score`
- 方案审批流：提交审批、审批/驳回、审批记录持久化，生产导出必须先通过 Validator 并获得批准
- 敏感操作二次确认：审批决策、生产导出、任务取消/重试必须提交确认短语
- 通知中心：审批请求、审批结果、后台任务失败/超时、任务队列水位告警、生产异常告警和采购预警写入站内通知，普通登录用户可查看并标记自己的通知；拥有 `notifications:manage` 后才显示模板和收件组管理。支持 `message_template` 模板、`message_dispatch_log` 分发审计、用户组织字段、收件组按用户/权限/部门解析、未读超时升级，以及带 HMAC 签名、失败重试、`dedupe_key` 冷却去重和 Feishu/企业微信 payload 适配的外部 webhook 模板推送；模板也支持 SMTP 邮件通道并复用同一套权限/收件组治理
- 生产导出：审批通过后生成 PDF/DXF 文件，写入 `solution_export`，记录业务版本、生命周期、保留期限、对象后端、对象 key、对象 version_id/ETag/size 和备份清单，支持过期导出归档巡检，并提供受保护下载
- 异步任务：拼版求解、Benchmark 回归运行和生产导出可进入 `work_task` 队列，支持尝试次数、超时、取消传播、重试、进度、心跳、队列水位指标、阈值告警和外部告警推送；本地使用 FastAPI BackgroundTasks，Docker 部署使用 Celery/Redis，Celery soft/hard time limit 和 prefetch 可配置
- 存储适配：本地文件系统与 MinIO 通过统一 Storage Adapter 切换，上传、Polygon JSON 和导出文件不再直接散落写盘
- 系统集成：CRM/MES/ERP/商业 Solver 外部系统、Adapter 配置版本、启用状态、配置校验、客户字段验收、组织编码/收件组验收、上线数据字典签核、CRM 字段映射同步、MES/ERP 记录快照同步、排程/库存/交付确认领域归档、ERP 库存快照驱动物料放行、MES 排程和 ERP 交付闭环检查、HTTP 认证/分页拉取、增量游标、客户侧状态字典、失败重试队列、HTTP 回写确认、同步任务和回写日志持久化
- Solver 治理：`solver_registry` 自动种子，管理 Solver 启用状态、版本和许可证策略，运行求解前会检查 Solver 是否启用且不是未配置的外部 stub
- Benchmark：基准案例和运行结果写入 `benchmark_case` / `benchmark_run`，前端可保存案例、运行 Solver 并查看历史指标
- Rectpack 风格确定性内置 Solver；OR-Tools、PackingSolver、Sparrow、Phoenix Adapter 已注册为待配置，未替换 `external-adapter-stub-*` 版本前不能启用或运行
- AI Assistant 提供受控工具执行：`/api/ai/tools/execute` 可审计地调用订单查询、图形读取、纸张规格、Solver、Validator、方案对比和报告生成；生产导出、CRM 回写和 AI 直接创建拼版任务保持阻断，不生成或绕过生产坐标

## 本地运行

后端：

```powershell
$env:PYTHONPATH='backend'
uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

测试：

```powershell
$env:PYTHONPATH='backend'
pytest -q tests\backend
```

发布预检：

```powershell
python scripts\release_preflight.py
```

企业级批量慢门禁默认不跑 1500/20000 全量，发布验收时显式开启：

```powershell
python scripts\release_preflight.py --include-slow-batch-gates --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files --report-path artifacts\release-preflight.json --evidence-output-dir artifacts\release-preflight-evidence
```

企业级最终完善说明见 `docs/ENTERPRISE_FINALIZATION.md`，其中汇总了批量排产模式、787x1092 benchmark 门禁、公开数据集导入、外部 CLI Solver 契约和 AI 工具治理边界。

默认会运行后端发布门禁测试、仓库忽略规则审计、集成/审计脱敏回归、本地交付证据包生成与完整性验证、前端生产构建和临时 API 健康冒烟；API 冒烟默认自动选择可用本地端口，需要固定端口时使用 `--smoke-port 8030`；需要完整后端回归时使用 `python scripts\release_preflight.py --full-backend`，仅调试测试/构建时可用 `--skip-evidence-pack` 跳过证据包门禁。
交付留痕时可追加 `--report-path artifacts\release-preflight.json`，脚本会写出 JSON 报告，记录后端测试、证据包生成与验证、前端构建、API 冒烟实际端口和返回内容、依赖/许可证摘要、`backend`/`tests`/`scripts` 的 pycache 清理结果、耗时和失败原因；证据包 gate 的 payload 会嵌入 manifest/verification 摘要、artifact 相对路径、字节数和 SHA-256，便于只看 preflight 报告也能判断交付包完整性；如需完整依赖清单，可追加 `--inventory-path artifacts\dependency-inventory.json`，证据包默认写入 `tmp\release-preflight-evidence`，可用 `--evidence-output-dir` 改到指定目录。
生成 preflight 报告后可运行 `python scripts\verify_release_preflight.py --report artifacts\release-preflight.json --output artifacts\release-preflight-verification.json`，对报告 `passed` 状态、必需 gate、证据包 payload、API 冒烟、pycache 清理和依赖摘要执行离线复核；证据包门禁启用时会在 release evidence 目录同步生成 `dependency-review-verification.json`，preflight 离线复核会要求依赖审计通过时对应 verifier gate 也通过；如果要把生产 env 审计纳入证据包门禁，可在 preflight 上追加 `--env-file .env.production --require-production-env`。如果要把依赖人工复核项作为阻断，可在 preflight 或 verifier 上追加 `--fail-on-dependency-review`；如果当前 release image 中仍有需要人工确认的依赖，但上线流程允许通过签核放行，可在 preflight 上追加 `--dependency-review-file artifacts\dependency-review.json --require-dependency-review`，把复核签核纳入证据包门禁。真实客户沙箱、通知/转换供应商、存储切换和生产部署验收完成后，可追加 `--external-acceptance-file artifacts\external-acceptance.json --require-external-acceptance`，把外部验收签核纳入证据包门禁。

本地交付证据包：

```powershell
python scripts\release_evidence_pack.py --output-dir artifacts\release-evidence --env-file .env.production
```

该命令会生成 `release-evidence-pack.json` 汇总清单，并在同一目录写入生产 env 脱敏审计、仓库忽略规则审计、客户沙箱样本验收、通知通道验收、存储导出恢复演练、转换供应商验收、Solver 治理验收、外部验收签核、依赖/许可证清单和依赖复核审计；清单会记录每个已生成 artifact 的路径、字节数和 SHA-256，便于交付后核对文件完整性；manifest 顶层会输出 `policy_contract`，校验证据包 schema、必需 artifact 集合、required/skipped 边界、完整性字段、敏感信息扫描、底层 artifact 契约摘要和依赖复核签核状态；本地 Compose 演示凭据、release image 前的缺包装态或尚未提供依赖复核签核会保留为 warning，契约 failed 会阻断证据包；每个生成报告都会附带 `sensitive_scan`，输出前会遮蔽 password、token、secret、api key、webhook URL 和 URL 密码/敏感 query，发现未脱敏敏感值时证据包失败；没有生产 env 文件时可省略 `--env-file`，清单会把生产 env 审计标记为 skipped；没有真实外部验收文件时，外部验收签核会标记为 skipped；上线门禁要求生产 env 审计时追加 `--require-production-env`，要求依赖复核签核时追加 `--dependency-review-file artifacts\dependency-review.json --require-dependency-review`，要求真实外部验收签核时追加 `--external-acceptance-file artifacts\external-acceptance.json --require-external-acceptance`。
证据包还会默认生成 `deployment-compose-audit.json` 和 `repository-hygiene-audit.json`，分别离线检查 Docker Compose 必需服务、healthcheck、`depends_on` 顺序、外部镜像 tag、后端 MinIO/Celery 运行环境、后端镜像 `.[optimization]` 依赖安装面、前端 `package-lock.json` + `npm ci` 构建约束，以及 `.gitignore` 对本地密钥、运行数据库、日志、tmp 和 artifacts 的忽略规则；Compose 中的 `packaging/packaging`、`minioadmin/minioadmin` 仅作为本地演示 warning 保留，但任一服务声明 `APP_ENV=production` 时仍检测到这些默认值会阻断证据包。
交付、上传或归档后可运行 `python scripts\verify_release_evidence_pack.py --manifest artifacts\release-evidence\release-evidence-pack.json --output artifacts\release-evidence\release-evidence-verification.json`，按 manifest 中的相对路径、字节数和 SHA-256 重新核对 artifact，并复核 manifest 顶层 `policy_contract`、summary 计数和预期 artifact 集合；即使某些可选报告业务状态为 skipped，只要清单中记录了文件路径、字节数和 SHA-256，也会执行完整性校验。缺失、篡改、契约缺失/失败或未通过的证据包都会返回非零退出码。

顶层交付索引：
```powershell
python scripts\release_handoff_bundle.py --preflight-report artifacts\release-preflight.json --preflight-verification artifacts\release-preflight-verification.json --production-env-verification artifacts\release-evidence\production-env-verification.json --external-acceptance-verification artifacts\release-evidence\external-acceptance-verification.json --output artifacts\release-handoff-bundle.json
python scripts\verify_release_handoff_bundle.py --manifest artifacts\release-handoff-bundle.json --output artifacts\release-handoff-verification.json
python scripts\go_live_readiness_audit.py --handoff-manifest artifacts\release-handoff-bundle.json --handoff-verification artifacts\release-handoff-verification.json --output artifacts\go-live-readiness.json
python scripts\go_live_remediation_packet.py --go-live-report artifacts\go-live-readiness.json --output-dir artifacts\go-live-remediation
```

第一条命令会从 preflight 报告中派生证据包 manifest 和证据包校验报告路径，再把 preflight、preflight 离线复核、证据包 manifest、证据包完整性复核、证据包内各项 JSON artifact、依赖清单、依赖复核审计、依赖复核 verifier、生产 env verifier 和外部验收 verifier 汇总为一个带 SHA-256 的 handoff manifest；handoff 会保留 evidence manifest 中的 artifact summary，便于最终门禁复核底层契约和敏感扫描结果。第二条命令会校验 handoff manifest 的必备 artifact 集合、重复名称、summary/errors/warnings 一致性，并按相对路径、字节数和 SHA-256 重新核对交付文件；当依赖复核、生产 env 或外部验收审计 artifact 为 passed 时，对应 verifier artifact 也必须存在且为 passed。必需报告未通过、证据 artifact 缺失或损坏时会返回非零退出码；交付目录被复制到其他位置后，可在 verifier 上追加 `--base-dir <交付目录>`。第三条命令用于上线前最终判定，会要求 handoff verifier 已通过且 verifier 的 `manifest_path` 指向当前 handoff manifest，并要求仓库卫生、客户沙箱、通知通道、存储导出、转换供应商、Solver 治理、生产 env、外部验收签核、依赖清单、依赖复核、依赖复核 verifier、release image 依赖审计、release image 依赖 verifier、生产 env verifier 和外部验收 verifier 都通过；本地交付包即使 manifest 状态为 passed，只要没有传入匹配且通过的 handoff verification、go-live 证据仍为 skipped、底层 artifact 契约/敏感扫描失败、任一 verifier `report_status` 非 `passed` 或 `error_count` 非 0，或依赖清单仍有缺失安装项，`go_live_readiness_audit.py` 就会返回 failed。若 go-live 仍失败，第四条命令会生成 `production-env.template`、带应用密钥的 `production-env.draft`、脱敏 draft 报告、`external-acceptance.template.json`、外部证据目录说明和 `run-go-live-evidence.ps1`，用于补齐真实生产 env 与外部验收证据后重跑最终门禁。生成的 PowerShell 脚本会显式检查每个原生 `python` 命令的退出码，并在第一处失败时停止，避免后续产物掩盖首个失败原因。补齐 `.env.production` 和 `external-acceptance.draft.json` 后，可先运行 `python scripts\go_live_remediation_packet.py --audit-packet artifacts\go-live-remediation --output artifacts\go-live-remediation\go-live-remediation-readiness.json`；该审计会刷新外部证据大小/SHA-256，写出生产 env 与外部验收审计报告；如果真实输入缺失或证据刷新失败，会清理当前补证包目录内过期的生产 env/外部验收审计输出，避免旧产物被误当成当前证据；结果为 `ready` 后再进入较重的 release image 与 preflight 门禁。

依赖/许可证清单：

```powershell
python scripts\release_inventory.py --output artifacts\dependency-inventory.json
python scripts\release_image_dependency_audit.py --inventory-output artifacts\dependency-inventory-release-image.json --review-output artifacts\dependency-review-audit-release-image.json --output artifacts\release-image-dependency-audit.json
python scripts\verify_dependency_review_audit.py --report artifacts\dependency-review-audit-release-image.json --output artifacts\dependency-review-verification-release-image.json
python scripts\verify_release_image_dependency_audit.py --report artifacts\release-image-dependency-audit.json --output artifacts\release-image-dependency-verification.json
```

该清单会解析 `backend/pyproject.toml` 和 `frontend/package-lock.json`，输出 Python/NPM 依赖、版本、声明范围、许可证字段和需人工复核的 GPL/AGPL/LGPL/未知许可证项；Python 许可证优先读取 `License-Expression`，当前环境未安装的声明依赖会标为 `installed=false` 并计入 `missing_install_count`，上线前应在正式 release image 中重新生成清单。如果 preflight/verifier 的 warning 明确写着本机缺少 release-blocking package，应以 release image 依赖清单和复核审计覆盖本地清单，而不是把它当作真实许可证待审。后端 release image 通过 `pip install --no-cache-dir ".[optimization]" "psycopg[binary]"` 安装运行依赖和优化 extra，避免 `ortools`、`rectpack` 等声明依赖在 go-live 清单中继续缺失。`release_image_dependency_audit.py` 会构建后端镜像，在容器内生成 `dependency-inventory-release-image.json`、`dependency-review-audit-release-image.json` 和 `release-image-dependency-audit.json`；`verify_dependency_review_audit.py` 会离线复核镜像内生成的依赖复核审计，`verify_release_image_dependency_audit.py` 会离线复核该总报告的命令摘要、policy contract、summary 计数和 inventory/review 输出文件摘要是否一致。正式 handoff 时应把这五个文件分别传给 `release_handoff_bundle.py --dependency-inventory ... --dependency-review-audit ... --dependency-review-verification ... --release-image-dependency-audit ... --release-image-dependency-verification ...`，用 release image 证据覆盖本地开发环境清单并留存镜像依赖复核、镜像审计总报告及其离线复核结果。

依赖人工复核确认：
```powershell
python scripts\dependency_review_template.py --inventory artifacts\dependency-inventory.json --output artifacts\dependency-review.json
python scripts\dependency_review_audit.py --inventory artifacts\dependency-inventory.json --review-file artifacts\dependency-review.json --require-review-file --output artifacts\dependency-review-audit.json
python scripts\verify_dependency_review_audit.py --report artifacts\dependency-review-audit.json --output artifacts\dependency-review-verification.json
```

第一条命令会按当前 inventory 生成 `pending` 模板，避免手写条目漏填或版本/许可证不匹配；交付负责人或法务必须补齐 `reviewer`、带时区的 ISO `reviewed_at`，把每个 `decision` 改为 `approved` 并填写非空 `reason` 后，第二条审计才会通过。`dependency-review.json` 使用 `schema_version=1`，`entries` 中每个条目必须匹配当前 inventory 的 `ecosystem/name/scope/version/license`；可选 `expires_at` 必须是带时区的 ISO datetime，过期后会重新阻断。第三条命令会离线复核 `dependency-review-audit.json` 的明细计数、summary 和 `policy_contract` 是否一致，默认要求审计报告为 passed。证据包或 preflight 可追加 `--dependency-review-file artifacts\dependency-review.json --require-dependency-review`，生成 `dependency-review-audit.json`；preflight 的证据包门禁默认会额外生成 `dependency-review-verification.json`，并在依赖审计通过时要求该 verifier gate 通过。

生产环境变量审计：

```powershell
python scripts\production_env_audit.py --write-draft .env.production --output artifacts\production-env-draft-report.json
python scripts\production_env_audit.py --env-file .env.production --output artifacts\production-env-audit.json
python scripts\verify_production_env_audit.py --report artifacts\production-env-audit.json --env-file .env.production --output artifacts\production-env-verification.json
python scripts\storage_export_audit.py --output artifacts\storage-export-audit.json
python scripts\conversion_supplier_audit.py --output artifacts\conversion-supplier-audit.json
python scripts\solver_governance_audit.py --output artifacts\solver-governance-audit.json
```

`--write-draft` 会从 `.env.production.example` 生成 `.env.production`，自动填入新的 `AUTH_SECRET_KEY` 和 `DEFAULT_ADMIN_PASSWORD`，并在 draft 报告中只记录 key 名称，不输出密钥明文。运行正式审计前仍必须替换数据库、Redis、MinIO、管理员邮箱和前端域名等剩余 `<REPLACE_WITH_...>` 外部占位值。该审计只读取指定 env 文件和应用默认值，复用生产启动安全规则，输出脱敏后的 JSON 报告；若 `APP_ENV` 不是 `prod/production`、仍使用开发默认凭据、保留模板占位值或 example/template 域名、任务后端/Redis/存储配置不安全、env 语法错误或出现重复 key，脚本会返回非零退出码。
`scripts/storage_export_audit.py` 会在临时数据库中通过 Storage Adapter 写入临时本地 PDF/DXF 导出对象，验证 adapter object_key 规范化/危险 key 拒绝、read/write/inspect 元数据、manifest 中的对象 version_id/ETag/size、恢复演练 checksum、篡改检测、version drift 检测和过期归档 dry-run 的非破坏性结果。
`scripts/conversion_supplier_audit.py` 会使用 mocked 供应商 endpoint、临时数据库和临时对象目录验证外部转换提交认证与 multipart、callback token hash 落库/明文不落库、token 轮换、旧 token 拒绝、normalized SVG/Polygon 回写、供应商异常码映射和 SLA 逾期巡检。
`scripts/solver_governance_audit.py` 会在临时数据库中验证 Solver 注册表种子、外部 stub 启用阻断、禁用许可证阻断、运行时 stub 防线、Rectpack 确定性输出和 Benchmark 持久化。

真实外部验收签核：
```powershell
python scripts\external_acceptance_audit.py --write-template artifacts\external-acceptance.draft.json
python scripts\external_acceptance_audit.py --refresh-evidence-metadata artifacts\external-acceptance.draft.json --refreshed-output artifacts\external-acceptance.json --output artifacts\external-acceptance-refresh-report.json
python scripts\external_acceptance_audit.py --acceptance-file artifacts\external-acceptance.json --require-acceptance-file --output artifacts\external-acceptance-audit.json
python scripts\verify_external_acceptance_audit.py --report artifacts\external-acceptance-audit.json --base-dir artifacts --output artifacts\external-acceptance-verification.json
```

第一条命令生成待签核 draft 模板，要求覆盖 `customer_integration_sandbox`、`notification_channel_sandbox`、`conversion_supplier_sandbox`、`storage_backend_cutover` 和 `production_deployment`。交付负责人必须补齐真实环境名、`reviewer`、带时区的 ISO `reviewed_at`（例如 `2026-06-29T10:00:00Z`），把每个 area 改为 `status=passed`，填写验收摘要和 `ticket`，并为每个相对路径真实证据文件填写 `description`；第二条命令会按实际文件自动刷新 `size_bytes` 和 SHA-256；第三条审计会验证证据文件存在、未逃逸目录、大小和 SHA-256 匹配；第四条会离线复核审计报告的 summary、policy contract 和已验证证据文件完整性。通过后可把刷新后的 `artifacts\external-acceptance.json` 传给 preflight 或 evidence pack 的 `--external-acceptance-file ... --require-external-acceptance`。

迁移校验：

```powershell
cd backend
alembic -c alembic.ini upgrade head
```

`tests/backend/test_migrations.py` 会在临时数据库上执行 `upgrade head` 并校验迁移后的表和列与 SQLAlchemy 模型一致。
`APP_ENV=production` 启动时会拒绝缺少表、缺少列或 `alembic_version` 未到仓库 head 的数据库，避免生产环境回退到开发模式的隐式建表或跳过迁移。
生产模式还会拒绝 SQLite `DATABASE_URL`、缺少/过短/Docker 演示默认的 PostgreSQL 密码、开发默认本地 `STORAGE_ROOT`、非 Celery 任务后端、开发默认或非 Redis 的 `REDIS_URL`、默认或过短的 `AUTH_SECRET_KEY`、默认 `DEFAULT_ADMIN_EMAIL`、默认或过短的 `DEFAULT_ADMIN_PASSWORD`、MinIO 默认凭据和 `CORS_ORIGINS=*`；`AUTH_SECRET_KEY` 至少 32 字符，生产数据库密码、默认管理员密码和 MinIO secret 至少 12 字符。生产本地存储模式只允许配置为绝对路径的 NAS 或持久卷。

客户沙箱样本包校验：

```powershell
pytest -q tests\backend\test_customer_sandbox_pack.py
python scripts\customer_sandbox_audit.py --pack samples\integrations\customer-sandbox\adapter-sandbox-pack.json --output artifacts\customer-sandbox-audit.json
python scripts\notification_channel_audit.py --pack samples\notifications\webhook-channel-pack.json --output artifacts\notification-channel-audit.json
python scripts\storage_export_audit.py --output artifacts\storage-export-audit.json
python scripts\conversion_supplier_audit.py --output artifacts\conversion-supplier-audit.json
python scripts\solver_governance_audit.py --output artifacts\solver-governance-audit.json
```

`tests/backend/test_customer_sandbox_pack.py` 会在临时数据库加载 `samples/integrations/customer-sandbox/adapter-sandbox-pack.json`，校验 CRM/MES/ERP 样本配置的字段验收、字典签核、激活和 readiness 阻断项。样本包使用 `schema_version=1`，每个 Adapter 必须显式声明 `system_type`，ERP 库存/交付样本必须匹配固定 `domain_target`；`scripts/customer_sandbox_audit.py` 会先输出 `pack_contract` 结构契约检查，schema、必需样本、字段映射、状态字典、组织编码或收件组引用不合格时会在写入临时数据库前失败。字段必填缺口、签核失败或 readiness blocker 会返回非零退出码，mock/dry-run、通知模板缺失等 warning 会保留在报告中供上线评审接受或修复。
`scripts/notification_channel_audit.py` 使用 `samples/notifications/webhook-channel-pack.json` 离线验证通用 webhook、飞书/Lark、企业微信 payload、HMAC 签名、失败重试、`dedupe_key` 去重、SMTP 邮件收件人解析/主题/事件头/正文和关键事件覆盖率；报告失败时不要把对应通知模板切入真实客户通道。
`scripts/conversion_supplier_audit.py` 会离线生成转换供应商验收报告；报告失败时不要把 CDR/AI/PDF 供应商通道切入生产。
`scripts/solver_governance_audit.py` 会离线生成 Solver 治理验收报告；报告失败时不要启用商业/外部 Solver 或替换 `external-adapter-stub-*` 版本进入客户环境。

默认登录：

- 前端登录页：`/login`
- 默认管理员：`admin@example.com` / `Admin123!`
- 默认角色模板：`admin`、`print_planner`、`production_operator`、`solution_approver`、`auditor`、`operations_manager`、`integration_manager`、`benchmark_engineer`
- 登录接口：`POST /api/auth/login`
- 当前用户：`GET /api/auth/me`
- 登录保护：失败登录会写入 `operation_log` 的 `auth.login_failed`；同一邮箱和客户端在 `LOGIN_RATE_LIMIT_WINDOW_SEC` 内连续失败达到 `LOGIN_RATE_LIMIT_MAX_FAILURES` 后返回 `429`，并写入 `auth.login_throttled`
- API 默认写入 `X-Content-Type-Options=nosniff`、`X-Frame-Options=DENY`、`Referrer-Policy=no-referrer` 和受限 `Permissions-Policy`；生产环境不允许关闭 `SECURITY_HEADERS_ENABLED`，如 TLS 终止在 API 或同层网关，可开启 `SECURITY_HSTS_ENABLED=true`
- API 会接受安全格式的 `X-Request-ID` 或自动生成请求 ID，并在响应头和错误 JSON 的 `request_id` 中回传，同时在 `app.request` 访问日志中记录 request_id、方法、路径、状态码和耗时，便于把客户报错、反向代理日志和后端日志串联起来；未处理异常只返回安全的 `internal server error`
- RBAC 用户创建和改密会校验密码策略：12-128 字符，并且至少包含一个字母和一个数字
- `operation_log`、`sync_task`、`writeback_log`、MES/ERP 快照字段、转换作业读取模型、消息模板和通知收件组 metadata 写入/返回前会递归遮蔽 password、token、secret、api key、authorization、webhook_url、URL 密码和敏感 query 等 payload 字段，保留 `*_hash`、`*_tail`、`callback_token_rotated_at` 这类审计摘要
- 订单、纸张、版图、拼版任务、方案、Solver 运行、AI 工具 schema 和审计等业务数据读写接口需要 `Authorization: Bearer <token>`；`/api/ai/tools` 还需要 `ai:use` 权限；`/api/health`、`/api/health/ready`、`/api/auth/login` 和无状态版图预检可匿名访问；供应商转换回调不使用 Bearer，但必须携带最新 `X-Conversion-Callback-Token`
- 前端菜单会按当前用户权限显示可访问模块，方案审批/导出/归档、任务维护/取消/重试等敏感操作按钮也会按细粒度权限禁用；直接打开无权限模块会回到 Dashboard，未登录直接打开受保护模块会跳转登录页
- 前端检测到已保存 Bearer Token 被后端返回 `401` 时会清理本地会话并跳转登录页，登录成功后回到原目标页
- 健康检查：`GET /api/health` 是轻量 liveness；`GET /api/health/ready` 会检查数据库连接、SQLAlchemy 元数据表/列和 Storage Adapter 可用性，生产/显式迁移模式还会检查 `alembic_version` 是否等于仓库 head，依赖异常时返回 503
- 运维指标接口：`GET /api/metrics` 需要 `audit:read` 权限，按 route 模板、HTTP 方法和状态码类别聚合 API 请求数、5xx 错误数、总耗时、平均耗时和最大耗时，不记录请求体或凭据
- 权限管理页：`/permissions`
- RBAC 管理接口：`GET /api/rbac/users`、`POST /api/rbac/users`、`PATCH /api/rbac/users/{user_id}`、`GET/POST/PATCH /api/rbac/roles`、`GET /api/rbac/permissions`
- 方案权限拆分：`solutions:write` 用于校验和提交审批，`solutions:approve` 用于审批/驳回，`solutions:export` 用于生成和下载生产文件，`solutions:archive` 用于备份清单、恢复演练和归档巡检
- 方案审批接口：`POST /api/solutions/{solution_id}/approval/request`、`POST /api/solutions/{solution_id}/approval/decision`、`GET /api/solutions/{solution_id}/approval`
- 确认短语：审批通过 `APPROVE <solution_id>`，审批驳回 `REJECT <solution_id>`，导出 `EXPORT PDF|DXF <solution_id>`，任务控制 `CANCEL|RETRY <task_id>`
- 通知接口：`GET /api/notifications`、`POST /api/notifications/{notification_id}/read`、`POST /api/notifications/read-all`
- 消息模板和收件组接口：`GET/POST /api/notifications/templates`、`PATCH /api/notifications/templates/{template_id}`、`GET/POST /api/notifications/recipient-groups`、`PATCH /api/notifications/recipient-groups/{group_id}`、`POST /api/notifications/dispatch`、`GET /api/notifications/dispatch-logs`，需要 `notifications:manage`
- 规则接口：`GET /api/rules/sets`、`GET /api/rules/sets/active`、`POST /api/rules/sets`、`POST /api/rules/sets/{rule_set_id}/activate`、`GET /api/rules/execution-logs`
- 集成接口：`GET /api/adapters/status`、`GET /api/adapters/readiness`、`GET/POST/PATCH /api/adapters/systems`、`GET /api/adapters/configs`、`POST /api/adapters/systems/{system_id}/configs`、`POST /api/adapters/configs/{config_id}/activate|test|field-acceptance|dictionary-signoff`、`POST /api/adapters/crm/sync`、`POST /api/adapters/{mes|erp}/sync`、`GET /api/adapters/production-schedules`、`GET /api/adapters/inventory-snapshots`、`GET /api/adapters/delivery-confirmations`、`GET /api/adapters/sync-tasks`、`GET /api/adapters/sync-tasks/retry-queue`、`POST /api/adapters/sync-tasks/{task_id}/retry`、`POST /api/adapters/{crm|mes|erp}/writeback`、`GET /api/adapters/writeback-logs`
- 物料放行接口：`GET /api/nesting/jobs/{job_id}/material-readiness`，按拼版任务订单物料汇总需求，并基于 `inventory_snapshot.available_qty - reserved_qty` 返回 `ready`、`blocked` 或 `unknown`
- 采购预警接口：`POST /api/nesting/jobs/{job_id}/procurement-alerts/check`，把物料短缺转换为建议采购量，支持安全库存比例、最小采购量和通知去重
- 生产检查接口：`GET /api/nesting/jobs/{job_id}/production-readiness`，汇总物料放行、MES `production_schedule_entry` 和 ERP `delivery_confirmation`，返回生产整体状态、排程状态和交付闭环状态
- 生产告警接口：`POST /api/nesting/jobs/{job_id}/production-alerts/check`，将物料短缺、排程 blocked/missing/unknown、交付 blocked/incomplete 转为站内通知并按去重窗口抑制重复告警
- 异常回写接口：`POST /api/nesting/jobs/{job_id}/exception-writebacks/run`，默认 dry-run，把物料短缺回写为 ERP 采购请求，把排程异常回写给 MES，把交付异常回写给 ERP 并记录 `writeback_log`
- Solver 接口：`GET /api/solvers/registry`、`PATCH /api/solvers/registry/{solver_name}`
- AI 工具接口：`GET /api/ai/tools`、`POST /api/ai/chat`、`POST /api/ai/tools/execute`，兼容 `POST /api/ai/tools/search-orders|run-solver|validate-solution|compare-solutions|explain-unplaced-items|generate-report|export-pdf|export-dxf`；批量 AI 工具操作手册见 `docs/AI_BATCH_TOOLS.md`，批量工具统一通过 `/api/ai/tools/execute` 调用。
- Benchmark 接口：`GET/POST /api/benchmark/cases`、`GET /api/benchmark/cases/{case_id}`、`POST /api/benchmark/cases/{case_id}/runs`、`POST /api/benchmark/cases/{case_id}/runs/async`、`GET /api/benchmark/runs`、`POST /api/benchmark/run`
- 批量排版 Pattern 产物接口：`GET /api/batch-layout/patterns/{pattern_id}`、`GET /api/batch-layout/patterns/{pattern_id}/placement`、`GET /api/batch-layout/patterns/{pattern_id}/placement.svg`
- 文件转换接口：`POST /api/artworks/{artwork_id}/convert`、`GET /api/artworks/conversion-jobs`、`GET/PATCH /api/artworks/conversion-jobs/{job_id}`、`POST /api/artworks/conversion-jobs/{job_id}/submit`、`POST /api/artworks/conversion-jobs/{job_id}/result`、`POST /api/artworks/conversion-jobs/{job_id}/callback`、`POST /api/artworks/conversion-jobs/sla/check`、`GET /api/artworks/{artwork_id}/versions`
- 生产导出接口：`POST /api/solutions/{solution_id}/export/pdf`、`POST /api/solutions/{solution_id}/export/dxf`、`GET /api/solutions/{solution_id}/exports`、`GET /api/solutions/{solution_id}/exports/manifest`、`POST /api/solutions/{solution_id}/exports/recovery-drill`、`POST /api/solutions/exports/archive-expired`、`POST /api/solutions/exports/archive-expired/async`、`GET /api/solutions/exports/{export_id}/download`
- 异步接口：`POST /api/nesting/jobs/{job_id}/run-async`、`POST /api/benchmark/cases/{case_id}/runs/async`、`POST /api/solutions/{solution_id}/export/pdf/async`、`POST /api/solutions/{solution_id}/export/dxf/async`、`GET /api/tasks`、`GET /api/tasks/metrics`、`GET /api/tasks/maintenance/schedule`、`POST /api/tasks/maintenance/run`、`POST /api/tasks/alerts/check`、`POST /api/tasks/{task_id}/cancel`、`POST /api/tasks/{task_id}/retry`

存储配置：

- 本地默认：`STORAGE_BACKEND=local`，写入 `STORAGE_ROOT=storage`
- Docker/生产建议：`STORAGE_BACKEND=minio`，并设置 `MINIO_ENDPOINT`、`MINIO_BUCKET`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`；前端镜像使用 `package-lock.json` + `npm ci` 做可复现安装；`APP_ENV=production` 会拒绝 `minioadmin/minioadmin`，若使用 `STORAGE_BACKEND=local` 则 `STORAGE_ROOT` 必须是绝对 NAS/持久卷路径
- 导出保留期限：`EXPORT_RETENTION_DAYS`，默认 365 天
- local 模式的 `storage_key` 是本地路径；MinIO 模式的 `storage_key` 是 `minio://<bucket>/<object_key>`
- 后台任务：`TASK_EXECUTION_BACKEND=background|celery`，生产环境必须为 `celery`，并设置非开发默认的 `REDIS_URL`（`redis://` 或 `rediss://`）；`LOGIN_RATE_LIMIT_MAX_FAILURES` / `LOGIN_RATE_LIMIT_WINDOW_SEC` 控制登录失败限流，`SECURITY_HEADERS_ENABLED` 控制 API 基线安全响应头且生产不可关闭，`SECURITY_HSTS_ENABLED` / `SECURITY_HSTS_MAX_AGE_SEC` 控制是否由 API 返回 HSTS，`TASK_STALE_AFTER_SEC` 控制心跳超时判定，`TASK_SOFT_TIME_LIMIT_SEC` / `TASK_HARD_TIME_LIMIT_SEC` / `TASK_WORKER_PREFETCH_MULTIPLIER` 控制 Celery worker 行为，`TASK_ALERT_ACTIVE_THRESHOLD` / `TASK_ALERT_QUEUED_THRESHOLD` / `TASK_ALERT_STALE_RUNNING_THRESHOLD` / `TASK_ALERT_FAILURE_THRESHOLD` / `TASK_ALERT_DEDUPE_MINUTES` 控制队列告警，`EXTERNAL_ALERT_WEBHOOK_URL` 控制外部告警推送，`EXTERNAL_CONVERSION_SERVICE_URL` / `EXTERNAL_CONVERSION_SERVICE_API_KEY` 控制 CDR/AI/PDF 外部转换服务，`EXTERNAL_CONVERSION_SLA_MINUTES` 控制转换作业默认 SLA，`BENCHMARK_TASK_TIMEOUT_SEC` 控制 Benchmark 异步任务业务超时，`MAINTENANCE_SCHEDULER_ENABLED` / `MAINTENANCE_INTERVAL_MINUTES` / `MAINTENANCE_*` 控制 Celery beat 周期维护。
- Webhook 模板 `metadata.webhook_provider` 支持 `generic`、`feishu`/`lark`、`wecom`/`wechat_work`；通用模式发送完整事件 JSON，飞书发送 `msg_type=text`，企业微信默认发送 `msgtype=markdown`，可用 `metadata.webhook_message_type=text` 切换为文本。
- 邮件模板使用 `channel=email`，按模板接收权限和收件组解析活跃用户邮箱，复用 `metadata.dedupe_minutes` 和 payload `dedupe_key` 控制重复告警；SMTP 由 `SMTP_HOST`、`SMTP_PORT`、`SMTP_FROM_EMAIL`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_USE_TLS` 和 `SMTP_TIMEOUT_SEC` 配置。

样例：

- `samples/orders/sample-orders.csv`
- `samples/artworks/sample-path-box.svg`
- `samples/artworks/sample-box.dxf`
- `samples/integrations/customer-sandbox/adapter-sandbox-pack.json`
- `samples/notifications/webhook-channel-pack.json`

## 核心边界

- 不训练 AI 模型，不微调模型。
- 不让大模型直接生成生产坐标，不让大模型判断几何是否合法。
- 所有算法输入必须标准化为 Polygon JSON，单位为 mm。
- 所有 Solver 输出必须经过 Validator 和审批，未验证或未批准方案不能导出生产文件。
- 直接几何解析优先支持 SVG/DXF；CDR/AI/PDF 通过人工导出或独立转换服务进入系统，核心服务记录转换作业、提交外部服务、支持供应商回调 token hash 鉴权与轮换、按异常码把失败归类为 `failed` 或 `manual_required`、接收 normalized SVG/DXF 结果、巡检 SLA 逾期并重新进入 Polygon 解析流程。
- 规则表达式不执行任意 Python、内置函数、导入、方法调用或多级属性访问；非法表达式会写入规则执行错误。

## 目录

- `backend/app`: FastAPI 应用、领域契约、服务层、API、数据库模型
- `frontend/src`: Vue3 企业后台页面
- `tests/backend`: 后端核心测试
- `samples`: 订单和版图样例
- `docs`: 部署、运维、许可证、实施说明

## 当前持久化范围

- `/api/orders/*`: 导入后的订单写入 `production_order`
- `/api/sheets/*`: 纸张规格写入 `sheet_spec`
- `/api/artworks/*`: 原始文件写入 `storage/artworks/<artwork_id>/`，预检报告写入 `file_preflight_report`，Polygon 写入 `polygon_asset`，转换作业写入 `file_conversion_job.metadata_json`，记录 callback token hash/尾号、token 轮换历史、SLA 截止时间、提交次数、供应商响应、异常码映射和逾期标记；读取转换作业时会遮蔽历史明文 token 并保留向后兼容回调校验，normalized SVG/DXF 版本写入 `artwork_version`
- `/api/nesting/jobs/*`: Job JSON 写入 `nesting_job` 和 `nesting_job_item`，`/api/nesting/jobs/{job_id}/material-readiness` 会读取订单物料和 ERP 库存快照生成物料放行检查报告并写入审计日志，`/api/nesting/jobs/{job_id}/procurement-alerts/check` 会把物料短缺转换成采购建议并写入 `notification`，`/api/nesting/jobs/{job_id}/production-readiness` 会进一步汇总 MES 排程和 ERP 交付确认生成生产检查报告，`/api/nesting/jobs/{job_id}/production-alerts/check` 会把生产异常写入 `notification`，`/api/nesting/jobs/{job_id}/exception-writebacks/run` 会生成 ERP/MES 异常回写日志
- `/api/nesting/jobs/{job_id}/run-async`: 创建 `work_task`，异步执行求解并写入 `solver_run` / `solver_run_log`
- `/api/benchmark/cases/{case_id}/runs/async`: 创建 `benchmark.run` 类型 `work_task`，后台运行 Benchmark 并写入 `benchmark_run`
- `/api/solutions/*`: Solution JSON、Placement、Validator 报告写入数据库，可在内存清空后读取报告和 SVG 预览
- `/api/solutions/{solution_id}/approval/*`: 方案审批记录写入 `solution_approval`，报告中返回审批历史
- `/api/notifications`: 审批请求、审批结果、任务失败/超时、任务队列水位告警、生产异常告警和采购预警通知写入 `notification`，按当前用户查询和标记已读；消息模板写入 `message_template`，每次模板分发写入 `message_dispatch_log`
- `/api/rules/*`: 规则集版本写入 `rule_set` / `rule_item`，启用版本用于订单评分和候选筛选，每次规则执行写入 `rule_execution_log`
- `/api/adapters/*`: 外部系统、Adapter 配置版本、配置校验状态、字段验收报告、上线数据字典签核、上线就绪报告、CRM 字段映射导入、MES/ERP 外部记录快照、排程/库存/交付确认归档、客户侧状态字典映射、组织编码和通知收件组验收、HTTP 拉取摘要、增量游标状态、失败同步重试链路、HTTP 回写确认、同步任务和回写日志写入 `external_system`、`adapter_config`、`sync_task`、`writeback_log`、`production_schedule_entry`、`inventory_snapshot`、`delivery_confirmation`；`sync_task.payload`、`writeback_log.payload` 和 MES/ERP 快照 `fields` 会保存脱敏后的审计数据；`dictionary_signoff` 写入 `adapter_config.config`，真实 HTTP 且非 dry-run 的状态字典或 `organization_acceptance` 配置激活前必须通过签核，`/api/adapters/readiness` 会汇总必需系统、激活配置、字段验收、字典签核、生产模式、重试策略、通知模板和运行失败队列；`inventory_snapshot`、`production_schedule_entry` 和 `delivery_confirmation` 同时作为拼版任务生产检查的数据源
- `/api/solvers/registry`: Solver 注册表写入 `solver_registry`，求解运行前校验启用状态和外部 Adapter 配置状态
- `/api/ai/tools/execute`: AI Assistant 只进入白名单工具调度器，结果和安全边界写入 `operation_log`；`run_solver` 仅调用后端 Solver，`validate_solution` 仅调用后端 Validator，`export_pdf`、`export_dxf`、`write_back_crm` 和 `create_nesting_job` 会返回 `blocked`，生产操作仍必须走原审批、确认和 Adapter 流程
- `/api/benchmark/*`: Benchmark Case 和 Run 写入 `benchmark_case` / `benchmark_run`，用于回归比较 Solver 利用率、耗时和 Validator 结果
- `/api/batch-layout/patterns/{pattern_id}` 和 `/api/batch-layout/patterns/{pattern_id}/placement(.svg)`: 读取写入 `production_pattern` 的 deterministic placement JSON/SVG、checksum 和 solver 元数据
- `/api/solutions/{solution_id}/export/*`: 审批通过后通过 Storage Adapter 生成 PDF/DXF 文件，业务版本、生命周期、保留期限、storage_key、checksum、对象后端、对象 key、version_id、ETag 和 size 写入 `solution_export`
- `/api/solutions/{solution_id}/exports/manifest`: 生成某个方案的导出备份清单，包含版本、生命周期、storage_key、checksum、对象存在性、对象 version_id/ETag/size、当前对象 stat、active/archived/expired 统计
- `/api/solutions/{solution_id}/exports/recovery-drill`: 按 manifest 逐个读取导出对象并重算 SHA256，返回 missing/unreadable/checksum_mismatch/version_mismatch 明细和归档 dry-run 概览，用于 MinIO/NAS 备份恢复演练留痕
- `/api/solutions/exports/archive-expired`: 按 `retention_until` 巡检过期导出并标记为 `archived`，支持 dry-run 和异步 `work_task`
- `/api/nesting/jobs/{job_id}/runs`: 查询某个 Job 的 Solver 运行记录
- `/api/nesting/runs/{run_id}/logs`: 查询单次 Solver 运行日志
- `/api/operation-logs`: 查询写操作审计日志，支持按 action、target_type、target_id、actor_id 和时间区间过滤；前端操作日志页会展示脱敏后的结构化 payload
- `/api/rbac/users`、`/api/rbac/roles`、`/api/rbac/permissions`: 管理用户、组织字段、角色、企业角色模板和权限字典，写入 `user_account`、`role`、`user_role`、`role_permission`
- `/api/notifications/recipient-groups`: 维护通知收件组，按直接用户 ID、权限编码和 `user_account.org_unit_code` 部门编码解析真实接收人，写入 `notification_recipient_group`
- `/api/tasks`: 查询后台任务队列状态、尝试次数、超时、取消请求、进度、心跳、结果和失败原因；`/api/tasks/metrics` 查询队列水位和心跳超时数；`/api/tasks/maintenance/schedule` 返回周期维护开关和检查项；`/api/tasks/maintenance/run` 手动执行过期导出归档、转换 SLA 巡检和任务告警检查；`/api/tasks/alerts/check` 评估水位告警、生成站内通知并可推送外部 webhook；`/api/tasks/{task_id}/cancel|retry` 用于任务控制
