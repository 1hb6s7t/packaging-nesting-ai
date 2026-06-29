# 部署说明

## MVP 私有化部署

```powershell
docker compose -p packaging_nesting up --build
```

生产数据库初始化或升级必须先在目标配置下执行：

```powershell
cd backend
alembic -c alembic.ini upgrade head
```

发布前可在工程根目录执行本地预检：

```powershell
python scripts\release_preflight.py
```

该脚本会运行迁移、健康、生产配置、客户沙箱、集成/审计脱敏、Compose、仓库忽略规则、鉴权面发布门禁测试、本地交付证据包生成与完整性验证、前端生产构建和临时 API 健康冒烟；API 冒烟默认自动选择可用本地端口，需要固定端口时加 `--smoke-port 8030`；需要完整后端回归时加 `--full-backend`，仅调试测试/构建时可用 `--skip-evidence-pack` 跳过证据包门禁。
上线交接或客户验收时建议追加 `--report-path artifacts\release-preflight.json`，保留 JSON 审计报告；报告包含每个 gate 的命令、工作目录、超时、退出码、耗时、证据包生成与验证结果、API 冒烟实际端口和返回内容、依赖/许可证摘要、`backend`/`tests`/`scripts` 的 pycache 清理数量和整体通过状态。证据包 gate 的 payload 会嵌入 manifest/verification 摘要、artifact 相对路径、字节数和 SHA-256。需要完整依赖清单时再追加 `--inventory-path artifacts\dependency-inventory.json`；preflight 证据包默认写入 `tmp\release-preflight-evidence`，可用 `--evidence-output-dir` 指定目录；需要把生产 env 审计纳入证据包门禁时，追加 `--env-file .env.production --require-production-env`；需要把依赖人工复核签核纳入证据包门禁时，追加 `--dependency-review-file artifacts\dependency-review.json --require-dependency-review`；真实客户沙箱、通知/转换供应商、存储切换和生产部署验收完成后，追加 `--external-acceptance-file artifacts\external-acceptance.json --require-external-acceptance`。
报告生成后应运行 `python scripts\verify_release_preflight.py --report artifacts\release-preflight.json --output artifacts\release-preflight-verification.json`，离线复核 preflight 报告本身的必需 gate、证据包 payload、API 冒烟、清理结果和依赖摘要；如上线策略要求依赖复核项清零，可在 preflight 或 verifier 上追加 `--fail-on-dependency-review`；如策略允许签核放行，应先用 `python scripts\dependency_review_template.py --inventory artifacts\dependency-inventory.json --output artifacts\dependency-review.json` 生成待签核模板，交付负责人或法务补齐带时区的 ISO `reviewed_at`、`approved` 决策和原因后，再用 `python scripts\dependency_review_audit.py --inventory artifacts\dependency-inventory.json --review-file artifacts\dependency-review.json --require-review-file --output artifacts\dependency-review-audit.json` 校验复核文件。

上线交付证据包可用一个命令生成：

```powershell
python scripts\release_evidence_pack.py --output-dir artifacts\release-evidence --env-file .env.production
```

该命令会在输出目录写入 `release-evidence-pack.json` 汇总清单，并生成生产 env 脱敏审计、仓库忽略规则审计、客户沙箱样本验收、通知通道验收、存储导出恢复演练、转换供应商验收、Solver 治理验收、外部验收签核、依赖/许可证清单和依赖复核审计。清单会记录每个已生成 artifact 的路径、字节数和 SHA-256，便于交付、上传或归档后核对文件完整性；每个生成报告都会附带 `sensitive_scan`，输出前会遮蔽 password、token、secret、api key、webhook URL 和 URL 密码/敏感 query；发现未脱敏敏感值时对应 artifact 和整个证据包会失败。若当前阶段没有生产 env 文件，可省略 `--env-file`，生产 env 审计会在清单中标记为 skipped；若还没有真实外部验收文件，外部验收签核会标记为 skipped；若上线门禁要求必须提供 env 文件，可追加 `--require-production-env`；若上线门禁要求依赖复核签核，可追加 `--dependency-review-file artifacts\dependency-review.json --require-dependency-review`；若上线门禁要求真实外部验收签核，可追加 `--external-acceptance-file artifacts\external-acceptance.json --require-external-acceptance`。
证据包会同时写入 `deployment-compose-audit.json` 和 `repository-hygiene-audit.json`，离线校验 Docker Compose 必需服务、healthcheck、服务依赖、外部镜像 tag、后端 MinIO/Celery 环境、后端镜像 `.[optimization]` 依赖安装面、前端 lockfile 安装，以及 `.gitignore` 对本地密钥、运行数据库、日志、tmp 和 artifacts 的忽略规则；本地演示凭据只产生 warning，但如果 Compose 服务声明 `APP_ENV=production` 且仍使用 `packaging/packaging` 或 `minioadmin/minioadmin`，审计会失败并阻断证据包。
证据包复制到交付目录、对象存储或客户环境后，应运行 `python scripts\verify_release_evidence_pack.py --manifest artifacts\release-evidence\release-evidence-pack.json --output artifacts\release-evidence\release-evidence-verification.json`；脚本会优先使用 manifest 中的 `relative_path` 在清单所在目录下定位 artifact，并核对字节数和 SHA-256，避免绝对路径随机器变化导致误验收。可选报告如果业务状态为 skipped 但已经生成 JSON 文件并记录完整性字段，仍会被校验；只有没有文件证据的可选项才保留为 skipped。
完成 preflight 离线复核后，应运行 `python scripts\release_handoff_bundle.py --preflight-report artifacts\release-preflight.json --preflight-verification artifacts\release-preflight-verification.json --output artifacts\release-handoff-bundle.json`，生成顶层交付索引；脚本会从 preflight payload 派生证据包 manifest/verification 路径，并把证据包内各项 JSON artifact、依赖清单和依赖复核审计纳入带 SHA-256 的交付总清单。复制、上传或归档后再运行 `python scripts\verify_release_handoff_bundle.py --manifest artifacts\release-handoff-bundle.json --output artifacts\release-handoff-verification.json` 复核顶层索引中的文件完整性；若交付目录已迁移，可追加 `--base-dir <交付目录>`。真正切生产前还应运行 `python scripts\go_live_readiness_audit.py --handoff-manifest artifacts\release-handoff-bundle.json --handoff-verification artifacts\release-handoff-verification.json --output artifacts\go-live-readiness.json`，该脚本会把缺失、失败或与当前 manifest 不匹配的 handoff verification、仓库卫生审计、生产 env、外部验收、依赖复核和 release image 依赖缺失提升为 go-live blocker。若 go-live 报告仍有 blocker，可运行 `python scripts\go_live_remediation_packet.py --go-live-report artifacts\go-live-readiness.json --output-dir artifacts\go-live-remediation` 生成补证包；其中包含生产 env 模板、带应用密钥的 production env draft、脱敏 draft 报告、外部验收模板、外部证据目录说明和重跑最终证据链的 PowerShell 脚本。该脚本会逐步检查原生 `python` 命令退出码，并在第一处失败时停止。补齐 `.env.production` 和 `external-acceptance.draft.json` 后，可先运行 `python scripts\go_live_remediation_packet.py --audit-packet artifacts\go-live-remediation --output artifacts\go-live-remediation\go-live-remediation-readiness.json`，快速确认补证包已经 ready；若真实输入缺失或外部证据刷新失败，readiness 会清理补证包目录内过期的生产 env/外部验收审计输出，避免旧文件混入当前验收。确认 ready 后再执行较重的 release image 与 preflight 门禁。

上线前应从正式后端镜像重新生成依赖证据：`python scripts\release_image_dependency_audit.py --inventory-output artifacts\dependency-inventory-release-image.json --review-output artifacts\dependency-review-audit-release-image.json --output artifacts\release-image-dependency-audit.json`。该脚本会构建后端 Dockerfile，在容器内运行依赖清单和依赖复核审计；若 `missing_install_count` 不为 0 或复核审计不是 passed，会返回非零退出码。如果本地 preflight/verifier 提示 review-required 项来自当前机器缺少 release-blocking package，应使用这份 release image 证据覆盖本地开发环境清单。生成 handoff 时追加 `--dependency-inventory artifacts\dependency-inventory-release-image.json --dependency-review-audit artifacts\dependency-review-audit-release-image.json --release-image-dependency-audit artifacts\release-image-dependency-audit.json`，让 go-live readiness 使用 release image 证据并校验镜像依赖审计总报告已留存。

生产 env 文件上线前应单独审计并留存脱敏报告：

```powershell
python scripts\production_env_audit.py --write-draft .env.production --output artifacts\production-env-draft-report.json
python scripts\production_env_audit.py --env-file .env.production --output artifacts\production-env-audit.json
python scripts\storage_export_audit.py --output artifacts\storage-export-audit.json
python scripts\conversion_supplier_audit.py --output artifacts\conversion-supplier-audit.json
python scripts\solver_governance_audit.py --output artifacts\solver-governance-audit.json
```

`--write-draft` 会从 `.env.production.example` 生成 `.env.production`，自动填入新的 `AUTH_SECRET_KEY` 和 `DEFAULT_ADMIN_PASSWORD`，并在 draft 报告中只记录 key 名称，不输出密钥明文。正式审计前必须替换数据库、Redis、MinIO、管理员邮箱和前端域名等剩余 `<REPLACE_WITH_...>` 外部占位值；`production_env_audit.py` 会阻断仍保留占位值或 example/template 域名的文件。该脚本只读取指定 env 文件和应用默认值，不依赖当前终端环境变量；语法错误、重复 key、非生产 `APP_ENV` 或不满足生产安全规则时返回非零退出码。
`scripts/storage_export_audit.py` 会在临时数据库中通过 Storage Adapter 写入临时本地导出对象，验证 adapter object_key 规范化/危险 key 拒绝、read/write/inspect 元数据、导出 manifest、对象 version_id/ETag/size、恢复演练 checksum、篡改检测、version drift 检测和过期归档 dry-run；报告还会输出 `policy_contract`，校验 manifest/recovery solution scope、对象元数据完整性、对象 key 作用域、当前对象 version/ETag/size 匹配、恢复演练覆盖率、单 active 版本链、保留期限和 archive dry-run 过期覆盖，可作为真实 MinIO/NAS 验收前的本地证据。
`scripts/conversion_supplier_audit.py` 会在临时数据库和临时对象目录中用 mocked 供应商 endpoint 验证外部转换提交认证、multipart 原文件、callback token hash 落库/明文不落库、token 轮换、旧 token 拒绝、normalized SVG/Polygon 回写、供应商异常码映射和 SLA 逾期巡检；报告还会输出 `policy_contract`，校验供应商 endpoint/callback URL HTTPS、提交认证、multipart 作业与源文件、202 accepted、callback token 轮换历史/hash-only 存储/旧 token 拒绝、normalized SVG/DXF 产物、Polygon 回写、异常码映射覆盖、SLA 逾期检测和沙箱通知禁用，可作为真实供应商沙箱验收前的本地证据。
`scripts/solver_governance_audit.py` 会在临时数据库中验证 Solver 注册表种子、外部 stub 启用阻断、禁用许可证阻断、运行时 stub 防线、Rectpack 确定性输出和 Benchmark 持久化，可作为商业 Solver 接入前的治理证据。

真实外部验收文件可用 `python scripts\external_acceptance_audit.py --write-template artifacts\external-acceptance.draft.json` 生成 draft 模板，补齐真实环境名、签核人、带时区的 ISO 签核时间、五个必需 area 的 `status=passed`、验收摘要、`ticket` 和相对路径证据文件 `description` 后，再运行 `python scripts\external_acceptance_audit.py --refresh-evidence-metadata artifacts\external-acceptance.draft.json --refreshed-output artifacts\external-acceptance.json --output artifacts\external-acceptance-refresh-report.json` 自动写入文件大小和 SHA-256，最后运行 `python scripts\external_acceptance_audit.py --acceptance-file artifacts\external-acceptance.json --require-acceptance-file --output artifacts\external-acceptance-audit.json`。该审计会验证证据文件没有越过清单目录、大小和 SHA-256 匹配；通过后才能把刷新后的文件作为 `--external-acceptance-file` 纳入 preflight 或 evidence pack 门禁。

发布前保留 `tests/backend/test_migrations.py` 的绿灯结果，确保 Alembic `head` 创建的表和列与当前 SQLAlchemy 模型一致。
`APP_ENV=production` 启动时会拒绝 SQLite `DATABASE_URL`，并检查 SQLAlchemy 元数据要求的表和列以及数据库 `alembic_version` 是否等于仓库 head；如迁移未执行、数据库结构不完整或版本戳未到 head，API 会直接报错退出，不再隐式 `create_all`。
生产启动同样会校验安全配置和任务后端：`DATABASE_URL` 不能缺少 PostgreSQL 密码、不能使用 Docker 演示默认 `packaging/packaging` 且数据库密码至少 12 字符，`AUTH_SECRET_KEY` 不能使用默认值且至少 32 字符，`DEFAULT_ADMIN_EMAIL` 不能使用默认管理员邮箱，`DEFAULT_ADMIN_PASSWORD` 不能使用默认值且至少 12 字符，MinIO 模式不能使用 `minioadmin/minioadmin`，`CORS_ORIGINS` 不能包含 `*`，`SECURITY_HEADERS_ENABLED` 不能关闭，`TASK_EXECUTION_BACKEND` 必须为 `celery`，`REDIS_URL` 不能使用开发默认值且必须为 `redis://` 或 `rediss://`。

组件：

- FastAPI API: `http://127.0.0.1:8000`
- Frontend: `http://127.0.0.1:5173`
- PostgreSQL: `127.0.0.1:5432`
- Redis: `127.0.0.1:6379`
- MinIO: `http://127.0.0.1:9001`
- Celery beat `scheduler` 服务：按 `MAINTENANCE_INTERVAL_MINUTES` 触发维护任务，结果写入 `work_task`

Docker Compose 已为 PostgreSQL、Redis 和 API 配置 healthcheck：API 会在 PostgreSQL/Redis healthy 后启动，并通过 `/api/health/ready` 验证数据库、模型 schema、生产迁移 head 和 Storage Adapter；worker、scheduler 和 frontend 会等待 API healthy 后再启动。
发布前可单独运行 `python scripts\deployment_compose_audit.py --output artifacts\deployment-compose-audit.json` 留存 Compose 交付审计；默认本地演示账号只作为 warning，生产声明下的演示账号会作为 error。

## 生产化注意点

- PostgreSQL、MinIO 建议独立持久化和备份。
- 生产反向代理不要绕过 API 鉴权：除 `/api/health`、`/api/health/ready`、`/api/auth/login`、静态 AI 工具 schema 和无状态版图预检外，订单、纸张、版图、拼版任务、方案、报告、预览、Solver 运行和审计数据都必须携带 Bearer Token；供应商转换回调入口必须保留 `X-Conversion-Callback-Token` 头并由后端校验。
- API 默认返回 `X-Content-Type-Options=nosniff`、`X-Frame-Options=DENY`、`Referrer-Policy=no-referrer` 和受限 `Permissions-Policy`；如 TLS 终止在反向代理，应由代理配置 HSTS，如 TLS 终止在 API 同层可设置 `SECURITY_HSTS_ENABLED=true` 和 `SECURITY_HSTS_MAX_AGE_SEC`。
- 反向代理应透传或生成安全格式的 `X-Request-ID`；API 会在响应头和错误 JSON 的 `request_id` 中回传请求 ID，并在 `app.request` 访问日志中记录 request_id、方法、路径、状态码和耗时，便于生产故障排查和客户工单追踪。未处理异常只返回安全的 `internal server error`，具体堆栈只进入后端日志。
- 上线监控可用 `GET /api/metrics` 拉取 API 请求数、5xx 错误数和响应耗时聚合数据；该接口需要 Bearer Token 且用户具备 `audit:read` 权限，不应作为负载均衡匿名探针。
- API 会按邮箱和客户端地址对失败登录做进程内限流，默认 `LOGIN_RATE_LIMIT_MAX_FAILURES=5`、`LOGIN_RATE_LIMIT_WINDOW_SEC=300`；生产反向代理或网关仍应配置全局限流，并把 `auth.login_failed`、`auth.login_throttled` 纳入安全告警。
- RBAC 用户创建和改密接口会拒绝不满足 12-128 字符、至少一个字母和一个数字的密码；生产导入或批量开户脚本应复用同一策略。
- 审计日志、`sync_task`、`writeback_log`、MES/ERP 快照字段、转换作业读取模型、消息模板和通知收件组 metadata 会默认遮蔽 password、token、secret、api key、authorization、webhook_url、URL 密码和敏感 query 等 payload 字段；上线检查仍应避免把完整外部凭据放入业务错误消息。
- `operation_log` 的时间、动作、对象和操作者过滤索引由 Alembic 迁移维护；生产升级后应确认 `/api/operation-logs` 的安全事件和上线变更查询不退化为全表扫描。
- 前端路由和侧边栏会按当前用户 RBAC 权限过滤模块，但生产安全边界仍以后端 Bearer Token 和 permission 检查为准；前端过滤用于减少误操作和无权限 401/403 流程。
- 前端生产包按路由懒加载页面，并只注册实际使用的 Element Plus 组件，避免把全部后台页面和完整组件库打进首屏 JS；发布时应保留 `npm.cmd run build` 的 chunk size 绿灯结果。
- 负载均衡 readiness 应使用 `GET /api/health/ready`；该接口会检查数据库连接、模型表/列和 Storage Adapter 可用性，生产/显式迁移模式还会检查 `alembic_version` 是否等于当前仓库 head，任一依赖异常会返回 503，并在 JSON `checks` 中说明失败组件。
- PackingSolver/Sparrow/商业 Phoenix 通过独立 Adapter 服务配置，不与核心业务强绑定；启用商业 Solver 前应先运行 `python scripts\solver_governance_audit.py --output artifacts\solver-governance-audit.json`，确认 `external-adapter-stub-*` 版本不会被启用或运行，且 Rectpack/Benchmark 回归仍通过；`solver_registry`、`external_system`、`adapter_config.version`、`is_active`、`validation_status`、`config.dictionary_signoff`、`sync_task`、`writeback_log`、`production_schedule_entry`、`inventory_snapshot`、`delivery_confirmation`、`message_template`、`notification_recipient_group`、`message_dispatch_log` 需要随业务数据库一起迁移和备份。
- AI Assistant 不连接生产 LLM 坐标生成链路，只通过 `/api/ai/tools/execute` 调用白名单后端工具；上线时应确认 `ai:use` 只分配给允许查看订单、方案和报告的角色，并把 `operation_log.action=ai.tool.execute` 纳入审计抽查。AI 边界内的 `export_pdf`、`export_dxf`、`write_back_crm` 和 `create_nesting_job` 必须保持 `blocked`，生产导出和客户回写仍走原审批、确认和 Adapter 工作流。
- ERP 库存快照 `inventory_snapshot` 会用于 `/api/nesting/jobs/{job_id}/material-readiness` 的物料放行检查；生产上线前必须确认 `material_code`/`material_name`、`available_qty`、`reserved_qty`、`unit` 和库存状态字典映射准确。
- 客户真实 CRM/MES/ERP API 接入前，必须为每个 Adapter 配置提供客户样本 `pages`、`records` 或 `sample_records`，可参考 `samples/integrations/customer-sandbox/adapter-sandbox-pack.json`，并执行 `/api/adapters/configs/{config_id}/field-acceptance`；报告为 `failed` 时不要启用正式同步或正式回写。上线前可先运行 `python scripts\customer_sandbox_audit.py --pack samples\integrations\customer-sandbox\adapter-sandbox-pack.json --output artifacts\customer-sandbox-audit.json`，脚本会先校验样本包 `pack_contract`、`sync_strategy_contract` 和 `business_flow_contract`：`schema_version=1`、CRM/MES/ERP 必需样本、显式 `system_type`、ERP 库存/交付固定 `domain_target`、本地样本记录、必需字段映射、状态字典、组织/收件组引用、CRM/MES 分页终止、增量游标来源和跨系统订单/物料/交付关系都必须满足，结构或策略不合格会在临时数据库导入前失败；通过后才完成样本导入、字段验收、字典签核、激活和 readiness 汇总，并把 JSON 报告纳入交付证据。若配置 `organization_acceptance.required_org_unit_codes` 或 `required_recipient_group_names`，上线前必须先在用户组织字段和通知收件组中落地客户通讯录编码。真实 HTTP 且非 dry-run 的状态字典或 `organization_acceptance` 配置还必须执行 `/api/adapters/configs/{config_id}/dictionary-signoff`，提交 `SIGNOFF <config_id>` 确认短语；签核记录会写入 `adapter_config.config.dictionary_signoff` 并进入 `operation_log`。上线评审应保存 `/api/adapters/readiness?required_system_types=crm,mes,erp` 报告，所有 `blocked` 项必须清零，`warning` 项需要明确接受或修复。
- `/api/nesting/jobs/{job_id}/procurement-alerts/check` 会把物料短缺转换为采购建议并写入站内通知；上线前应确认采购/计划相关用户拥有 `nesting:write` 或 `integrations:write` 权限，并按物料采购策略配置安全库存比例和最小采购量。
- `/api/nesting/jobs/{job_id}/production-readiness` 会同时读取 MES 排程和 ERP 交付确认；生产上线前必须确认排程字段能映射到 `order_id` 或 `job_id`，交付字段能映射到 `order_id`、`quantity`、`delivered_at` 和可判定的签收/异常状态。
- `/api/nesting/jobs/{job_id}/production-alerts/check` 会把生产异常写入站内通知；上线时应结合岗位权限确认 `nesting:write` 用户范围，并按通知噪声调整 `TASK_ALERT_DEDUPE_MINUTES`。
- 真实通知渠道上线前，可先运行 `python scripts\notification_channel_audit.py --pack samples\notifications\webhook-channel-pack.json --output artifacts\notification-channel-audit.json`；脚本会先校验通知样本包 `policy_contract`，要求 `schema_version=1`、关键事件覆盖、模板渲染、目标、`dedupe_key`、`dedupe_minutes`、webhook HTTPS URL、`webhook_provider`、`retry_count`、generic HMAC 签名、飞书/Lark 平台关键词、企业微信 `key`/消息类型和邮件收件路由完整，策略不合格会在临时数据库 dispatch 前失败；通过后再用 mocked endpoint 和注入式邮件发送器验证通用 webhook、飞书/Lark、企业微信 payload、HMAC 签名、失败重试、去重、SMTP 邮件收件人解析/主题/事件头/正文和关键事件覆盖率，并把 JSON 报告纳入上线证据。
- 消息模板和收件组通过 `/api/notifications/templates`、`/api/notifications/recipient-groups` 和 `/notifications` 页面维护，需要 `notifications:manage`；上线前应为关键事件确认站内接收权限、收件组、未读升级权限、升级组、webhook URL、`webhook_provider`、HMAC 签名密钥、失败重试次数、SMTP 发件配置、`dedupe_key` 冷却窗口、模板字段路径和 `message_dispatch_log` 审计保留策略，并确认用户 `org_unit_code` 与客户组织/产线/岗位编码一致。飞书/Lark、企业微信 webhook 和 SMTP 邮件需要在客户沙箱中分别验证平台响应码、关键词/签名规则、邮件投递策略、收件人解析和消息展示效果。
- `/api/nesting/jobs/{job_id}/exception-writebacks/run` 默认 dry-run；切换正式外部回写前必须为 ERP/MES Adapter 配置 HTTP writeback endpoint、认证、状态字典、确认字段和失败重试策略，并先用 dry-run 的脱敏 `writeback_log.request_body` 完成客户字段验收。
- CDR/AI/PDF 转换服务独立部署，避免专有格式和 GPL/AGPL 组件污染核心闭源代码；核心 API 通过 `EXTERNAL_CONVERSION_SERVICE_URL` 提交转换作业，随请求传递 callback token 和 SLA 分钟数，但本地只保存 callback token hash/尾号；重提时可用 `rotate_callback_token=true` 轮换回调 token，供应商回调 `/api/artworks/conversion-jobs/{job_id}/callback` 必须携带最新 `X-Conversion-Callback-Token`；若早期数据仍保留明文 token，读取接口会遮蔽该字段且回调校验保持兼容，迁移窗口内应尽快重提轮换；失败回调可在 `metadata.vendor_error_code`/`error_code`、`metadata.vendor_error_message` 和可选 `metadata.vendor_error_map` 中提交异常码，核心服务会把已知人工类问题归入 `manual_required`；上线时应把 `/api/artworks/conversion-jobs/sla/check` 接入定时巡检，并先运行 `python scripts\conversion_supplier_audit.py --output artifacts\conversion-supplier-audit.json` 留存离线验收报告；核心服务通过 `file_conversion_job.metadata_json` 和 `artwork_version` 记录转换状态、日志、SLA、回调状态、token hash/尾号/轮换历史、异常码映射和 normalized SVG/DXF 产物，不直接内置专有格式转换器。
- 所有生产导出必须检查 `validation_report.is_valid = true`。
- 本地默认 `STORAGE_BACKEND=local`，上传原始文件和 `polygon.json` 写入 `storage/artworks/<artwork_id>/`，审批后的 PDF/DXF 导出写入 `storage/exports/<solution_id>/`；生产若使用 local 模式，`STORAGE_ROOT` 必须是绝对 NAS/持久卷路径。
- Docker Compose 的 PostgreSQL `packaging/packaging` 和 MinIO `minioadmin/minioadmin` 仅用于 MVP/本地演示；`APP_ENV=production` 前必须替换数据库、MinIO、默认管理员和应用签名密钥等凭据。Docker Compose 已设置 `STORAGE_BACKEND=minio`，API/worker 会把上传、Polygon JSON 和导出文件写入 MinIO bucket。
- 前端镜像构建使用 `package-lock.json` 和 `npm ci` 做可复现安装；更新前端依赖时必须同时提交 `package.json` 与 `package-lock.json`，并重新运行 `npm.cmd run build` 或发布预检。
- MinIO/NAS 生产部署必须启用对象版本或外部备份，并把 `solution_export.storage_key`、`storage_backend`、`storage_object_key`、`storage_version_id`、`storage_etag`、`storage_size_bytes`、version、lifecycle_status、retention_until、checksum、manifest、`/api/solutions/{solution_id}/exports/recovery-drill` 恢复演练报告、过期导出归档巡检结果、`scripts/storage_export_audit.py` 本地审计报告、`file_conversion_job` 状态/日志/SLA 元数据以及 `artwork_version.normalized_storage_key` 纳入备份恢复演练。
- 本地开发默认使用 `TASK_EXECUTION_BACKEND=background`；Docker Compose 已为 API 和 worker 设置 `TASK_EXECUTION_BACKEND=celery` 和 `REDIS_URL=redis://redis:6379/0`，依赖 Redis 执行后台任务，并通过 `scheduler` 服务运行 Celery beat。`APP_ENV=production` 会拒绝 background 模式、开发默认 Redis URL 和非 Redis URL。生产环境需按部署规模配置 Redis、Celery worker 并发、`TASK_SOFT_TIME_LIMIT_SEC`、`TASK_HARD_TIME_LIMIT_SEC`、`TASK_WORKER_PREFETCH_MULTIPLIER` 和 `TASK_STALE_AFTER_SEC`；队列告警由 `TASK_ALERT_ACTIVE_THRESHOLD`、`TASK_ALERT_QUEUED_THRESHOLD`、`TASK_ALERT_STALE_RUNNING_THRESHOLD`、`TASK_ALERT_FAILURE_THRESHOLD`、`TASK_ALERT_DEDUPE_MINUTES` 控制，周期维护由 `MAINTENANCE_SCHEDULER_ENABLED`、`MAINTENANCE_INTERVAL_MINUTES`、`MAINTENANCE_ARCHIVE_EXPIRED_EXPORTS`、`MAINTENANCE_CONVERSION_SLA_CHECK`、`MAINTENANCE_TASK_ALERT_CHECK` 控制，配置 `EXTERNAL_ALERT_WEBHOOK_URL` 后会同步推送外部告警，也可为具体事件配置 webhook 或 SMTP 邮件消息模板；邮件通道使用 `SMTP_HOST`、`SMTP_PORT`、`SMTP_FROM_EMAIL`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_USE_TLS` 和 `SMTP_TIMEOUT_SEC`；Benchmark 后台运行默认超时由 `BENCHMARK_TASK_TIMEOUT_SEC` 控制。
