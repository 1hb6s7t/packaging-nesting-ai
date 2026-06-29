# 实施计划

1. 项目骨架：FastAPI、Vue、PostgreSQL、Redis、MinIO、Docker Compose。
2. 数据库模型和 Alembic 迁移。
3. 文件上传和 SVG/DXF 解析。
4. Polygon 标准化和 Geometry Engine。
5. 纸张规格、订单导入、规则引擎。
6. RectpackSolverAdapter 和基础 Validator。
7. SVG 预览、Top-K 方案、报告生成。
8. 接入 PackingSolver/Sparrow。
9. AI Assistant Function Calling。
10. 权限、审计、监控、备份和私有化部署。
11. 客户 API 到位后接 CRM/MES/ERP Adapter。

当前进度：

- 第 1-2 步：工程骨架、数据库模型、Alembic 迁移已完成。
- 第 3 步：文件上传、预检、SVG 基础解析、DXF 基础解析、转换作业持久化、外部转换服务提交、供应商回调 token hash 鉴权与轮换、历史明文 token 返回遮蔽与兼容校验、供应商异常码映射、SLA 逾期巡检、normalized SVG/DXF 结果回写、ArtworkVersion 归档、转换日志页和 `scripts/conversion_supplier_audit.py` 离线供应商验收报告已完成；该报告已通过 `policy_contract` 校验 endpoint/callback HTTPS、提交认证、multipart、202 accepted、token 轮换/hash-only、normalized 产物、Polygon、异常码映射覆盖、SLA 和沙箱通知禁用；CDR/AI/PDF 不内置解析器，仍通过独立转换服务或人工导出处理。
- 第 4 步：Polygon 标准化、Geometry Engine、多边形相交/包含/最小距离校验、Shapely 精确差集空白面积、真实 offset 和自交修复已完成；Clipper2/更高级轮廓处理可作为后续性能和精度优化。
- 第 5-7 步：纸张规格、CSV/XLSX 订单导入、规则引擎、规则集版本化、执行日志、Rectpack MVP Adapter、基于真实放置多边形的 Validator、SVG 预览、JSON 报告已完成基础闭环。
- 企业持久化：订单、纸张、版图元数据、预检报告、FileConversionJob、Polygon Asset、NestingJob、NestingSolution、SolutionPlacement、ValidationReport 已接入 SQLAlchemy repository。
- 运行与审计：Solver 运行现在写入 `solver_run` / `solver_run_log`，写操作写入带查询索引的 `operation_log`，前端运行监控和操作日志页已接真实 API；操作日志支持按动作、对象、操作者和时间区间过滤，并可查看任务、运行日志和审计记录的结构化 payload。
- 运维健康检查：`/api/health` 保持轻量 liveness，`/api/health/ready` 已检查数据库连接、SQLAlchemy 模型表/列和 Storage Adapter 可用性；生产/显式迁移模式还会校验数据库 `alembic_version` 是否等于仓库 head，依赖异常或迁移未到 head 时返回 503；API 已回传/生成 `X-Request-ID`，错误 JSON 包含 `request_id`，未处理异常只返回安全 detail，并在 `app.request` 访问日志记录 request_id、方法、路径、状态码和耗时；`/api/metrics` 已按 route 模板、方法和状态码类别汇总请求数、5xx 错误数和响应耗时，并通过 `audit:read` 保护；`scripts/release_preflight.py` 已固化迁移、健康、生产配置、客户沙箱、集成/审计脱敏、转换供应商、Solver 治理、Compose、仓库忽略规则、鉴权面、本地交付证据包生成与完整性验证、前端构建和 API 冒烟等发布门禁，并可通过 `--report-path` 输出机器可读 JSON 验收报告，证据包 gate payload 会嵌入 manifest/verification 摘要和 artifact 完整性字段，且可通过 `--env-file ... --require-production-env` 要求生产 env 审计进入证据包，可用 `--fail-on-dependency-review` 将依赖人工复核项提升为阻断，也可通过 `--dependency-review-file ... --require-dependency-review` 要求依赖复核签核进入证据包，可通过 `--external-acceptance-file ... --require-external-acceptance` 要求真实外部验收签核进入证据包；`scripts/verify_release_preflight.py` 已支持交付后离线复核 preflight 报告的必需 gate、证据包 payload、API 冒烟、清理结果、依赖摘要、强制生产 env artifact、强制依赖复核 artifact 和强制外部验收 artifact；`scripts/release_evidence_pack.py` 已支持一键生成生产 env、客户沙箱、通知通道、存储导出、转换供应商、Solver 治理、外部验收、依赖/许可证和依赖复核审计的本地交付证据包，并对生成报告执行 `sensitive_scan`、输出脱敏和未脱敏敏感值阻断，同时记录 artifact 相对路径、字节数和 SHA-256；`scripts/verify_release_evidence_pack.py` 已支持交付后按 manifest 复核证据包完整性，且会校验已生成文件证据的 skipped 可选报告；`scripts/release_handoff_bundle.py` 已支持把 preflight、preflight verifier、证据包 manifest、证据包 verifier、证据包内各项 JSON artifact、依赖清单和依赖复核审计汇总为顶层交付索引并记录 SHA-256；`scripts/verify_release_handoff_bundle.py` 已支持交付后按 handoff manifest 复核顶层交付文件完整性，并可用 `--base-dir` 适配复制后的交付目录；`scripts/go_live_readiness_audit.py` 已支持把缺失、失败或 manifest_path 不匹配的 handoff verification、生产 env、外部验收、依赖复核或 release image 依赖安装缺口作为最终上线 blocker；`scripts/go_live_remediation_packet.py` 已支持从 go-live 报告生成生产 env 模板、带应用密钥的 production env draft、脱敏 draft 报告、外部验收模板、证据目录说明、补证包 readiness 审计和最终证据链重跑脚本，且重跑脚本会检查每个原生 `python` 命令退出码并在第一处失败时停止，避免剩余上线 blocker 只能靠手工整理或被后续产物掩盖；`scripts/repository_hygiene_audit.py` 已支持审计 `.gitignore` 是否忽略本地密钥、运行数据库、日志、tmp 和 artifacts；`scripts/release_inventory.py` 已支持生成 Python/NPM 依赖和许可证清单，优先读取 Python `License-Expression` 并在 preflight 报告中写入缺失安装/人工复核摘要；`scripts/release_image_dependency_audit.py` 已支持构建后端 release image 并在容器内生成依赖清单和依赖复核审计，供 handoff 覆盖本地开发环境依赖结果；`scripts/dependency_review_template.py` 已支持按当前 inventory 生成待签核模板，降低手写签核条目漂移风险；`scripts/dependency_review_audit.py` 已支持按当前 inventory 校验依赖复核签核文件，阻断缺失、未批准、版本/许可证不匹配、字段不完整或过期的确认；`scripts/external_acceptance_audit.py` 已支持生成真实外部验收模板、自动刷新证据文件大小/SHA-256 并校验必需 area；`.env.production.example` 已提供生产 env 占位模板，`scripts/production_env_audit.py` 已支持生成带 `AUTH_SECRET_KEY`/`DEFAULT_ADMIN_PASSWORD` 的生产 env 草稿、对指定生产 env 文件执行离线安全审计、模板占位值和重复 key 阻断并输出脱敏 JSON 报告。
- 外部验收审计补强：`scripts/external_acceptance_audit.py` 会要求五个必需 area 均为 `status=passed`，且顶层签核时间必须是带时区的 ISO datetime；每项验收必须填写摘要、`ticket`、真实相对路径证据文件和证据 `description`，证据文件仍需通过大小和 SHA-256 复核；报告已通过 `policy_contract` 区分可选 skipped、必需验收缺失、area 覆盖/证据完整性失败和非必需 area warning。
- 生产 env 审计补强：`.env.production.example` 已把数据库、Redis、MinIO、管理员邮箱和前端域名都保留为显式 `<REPLACE_WITH_...>` 占位符；`scripts/production_env_audit.py` 会阻断模板占位值、重复 key、开发默认值和 example/template 域名，并输出 `policy_contract` 校验生产模式、推荐部署 key、应用凭据、PostgreSQL、MinIO/NAS 存储、Celery/Redis、安全响应头/HSTS、占位符/模板域名清零和脱敏报告有效性，避免只替换密码但继续使用样例域名或未显式启用上线安全项的配置进入 go-live。
- 依赖复核审计补强：`scripts/dependency_review_audit.py` 会要求依赖复核签核的 `reviewed_at` 和可选 `expires_at` 使用带时区的 ISO datetime，避免许可证/合规签核只留下日期或本地无时区时间；报告会输出 `policy_contract`，区分可选缺失 skipped、强制缺失 failed、覆盖率/approved 决策/版本许可证漂移/过期/字段完整性 failed 和当前清单不再需要的多余签核 warning。
- 补证包 readiness 补强：`scripts/go_live_remediation_packet.py --audit-packet` 在生产 env 或外部验收输入缺失、外部证据刷新失败时，会清理补证包目录内对应的过期审计输出，避免上一轮成功生成的 `production-env-audit.json`、`external-acceptance.json` 或 `external-acceptance-audit.json` 被误当作当前上线证据。
- Docker/Compose 交付审计：`scripts/deployment_compose_audit.py` 已接入，离线检查 Compose 必需服务、healthcheck、`depends_on` 顺序、外部镜像 tag、后端 MinIO/Celery 环境、后端镜像 `.[optimization]` 依赖安装面、前端 lockfile 安装和本地演示凭据边界；报告会输出 `policy_contract`，把上述检查聚合为可复核的交付契约。默认本地 Compose 凭据只作为 warning，若服务声明 `APP_ENV=production` 仍使用演示默认值则阻断，并已纳入 release evidence pack。
- 仓库卫生交付审计：`scripts/repository_hygiene_audit.py` 已接入 release evidence pack，默认生成 `repository-hygiene-audit.json`，用于留存 `.gitignore` 对本地密钥、运行数据库、日志、tmp 和 artifacts 的忽略规则审计证据；报告已输出 `policy_contract`，会把 `.env`/`.env.*` 忽略、`.env.production.example` 例外放行、运行状态、release artifact、Python cache 和前端构建目录忽略策略固化为交付契约，并已纳入 `go_live_readiness_audit.py` 的最终必过 artifact。
- 正式镜像依赖审计留痕：`release_handoff_bundle.py` 已支持 `--release-image-dependency-audit`，可把 `release-image-dependency-audit.json` 和 release image 内生成的依赖清单/复核审计一起纳入 handoff；`go_live_readiness_audit.py` 会要求该总审计 artifact 通过。
- 前端生产构建：路由页面已按需懒加载，Element Plus 改为注册实际使用组件，首屏 JS 不再包含全部后台页面和完整组件库；前端 Docker 镜像使用 `package-lock.json` + `npm ci` 做可复现依赖安装。
- 认证与 RBAC：默认管理员、企业角色模板、密码哈希、Bearer Token、`/api/auth/login`、`/api/auth/me`、失败登录审计、登录失败限流、API 基线安全响应头、可选 HSTS、审计 payload 敏感字段脱敏、托管账号创建/改密密码策略、细粒度 permission 检查、用户/角色管理 API、前端权限管理页已接入，写入/审计接口已按权限保护，方案校验、审批、导出、下载、备份清单、恢复演练和归档巡检已拆分到独立权限；`APP_ENV=production` 会拒绝 SQLite、缺少/过短/演示默认 PostgreSQL 密码、开发默认本地 storage、开发默认 `AUTH_SECRET_KEY`、默认管理员邮箱、默认管理员密码、MinIO 默认凭据、wildcard CORS、关闭安全响应头和不安全任务后端配置。
- 业务读取鉴权：订单、纸张、版图元数据/预览、拼版任务、方案、报告、Solver 运行和日志等存储型业务读取已要求 Bearer Token，匿名入口仅保留健康检查、登录、静态 AI 工具 schema 和无状态版图预检；供应商转换回调使用独立 callback token 鉴权；前端路由、侧边栏、方案审批/导出/归档和任务维护/取消/重试等敏感操作控件已按登录状态与 RBAC 权限过滤或禁用，并会在已登录请求返回 `401` 时清理本地会话回到登录页。
- 审批与放行：方案审批请求、审批/驳回、`solution_approval` 持久化、审批历史报告、生产导出前审批校验已接入，前端方案管理页已支持审批流。
- 敏感操作确认：审批决策、生产导出、任务取消/重试已接入确认短语校验，前端使用输入确认弹窗。
- 通知中心：审批请求、审批结果、后台任务失败/超时、任务队列水位告警、生产异常告警和采购预警会写入 `notification`，前端通知中心和已读接口已接入；`message_template`、`notification_recipient_group`、`message_dispatch_log`、`notifications:manage`、模板化站内/外部 webhook/SMTP 邮件分发、客户组织字段、按用户/权限/部门解析的收件组、未读超时升级、通用/Feishu/企业微信 webhook payload、webhook HMAC 签名、失败重试和 `dedupe_key` 冷却去重策略已接入；模板和收件组读取接口会遮蔽 webhook_url、signature_secret、api_key 等敏感 metadata，投递路径仍使用数据库内原始 metadata 签名和发送；`samples/notifications/webhook-channel-pack.json` 和 `scripts/notification_channel_audit.py` 已支持离线生成通知通道 JSON 验收报告，先用 `policy_contract` 阻断 schema、必需事件、模板渲染、目标、`dedupe_key`、`dedupe_minutes`、webhook HTTPS URL、provider、retry、generic 签名、Feishu/Lark 关键词、企业微信 key/message_type 和邮件收件路由不合格的样本，再用 mocked endpoint/注入式邮件发送器覆盖 webhook payload、签名/重试/去重和 SMTP 邮件收件人解析、主题、事件头、正文校验。
- 生产导出：审批后 PDF/DXF 文件生成、`solution_export` 版本治理、生命周期状态、保留期限、SHA256 校验和、对象 version_id/ETag/size 联动、备份清单、导出对象恢复演练、过期导出归档巡检和受保护下载接口已接入；`scripts/storage_export_audit.py` 已支持离线生成 Storage Adapter contract、manifest、恢复演练、篡改检测、version drift 检测和归档 dry-run JSON 验收报告，并通过 `policy_contract` 校验 manifest/recovery solution scope、对象元数据完整性、对象 key 作用域、当前对象 version/ETag/size 匹配、恢复演练覆盖率、单 active 版本链、保留期限和 archive dry-run 过期覆盖。
- 异步任务：`work_task` 队列、求解/Benchmark/导出后台执行、尝试次数、超时、取消传播、重试、进度、心跳、队列水位指标、阈值告警、外部 webhook 推送、Celery worker 入口、Celery beat 周期维护、可配置 soft/hard time limit、任务监控 API 和前端运行监控页已接入；`APP_ENV=production` 会拒绝 `TASK_EXECUTION_BACKEND=background`、开发默认 `REDIS_URL` 和非 Redis URL。
- 存储适配：原始版图、Polygon JSON、生产导出文件已通过 Storage Adapter 支持本地文件系统和 MinIO 两种后端。
- 规则治理：默认规则集自动种子，`/api/rules/*` 支持创建版本、启用版本和查询执行日志，订单评分/候选筛选使用当前启用规则集并写入 `rule_execution_log`。
- 规则表达式：硬约束和软评分已接入受限白名单解释器，支持订单字段、比较/布尔/三元表达式和受控评分函数，拒绝导入、内置函数、方法调用和未知字段。
- 系统集成治理：`/api/adapters/*` 已支持外部系统、Adapter 配置版本、启用状态、配置校验、客户字段验收、客户组织编码/通知收件组验收、上线数据字典签核、上线就绪报告、CRM 字段映射、MES/ERP 外部记录快照、排程/库存/交付确认领域归档、ERP 库存快照驱动物料放行和采购预警、MES 排程和 ERP 交付闭环生产检查、生产异常站内通知告警、物料/排程/交付异常回写策略、客户侧状态字典、HTTP 认证/分页拉取、增量游标持久化、失败同步重试队列、CRM/MES/ERP HTTP 回写确认、同步任务和回写日志，前端系统集成页和拼版生产检查入口已接真实 API；`sync_task.payload`、`writeback_log.payload` 和 MES/ERP 快照 `fields` 已接入敏感字段、URL 密码和敏感 query 脱敏；真实 HTTP 且非 dry-run 的状态字典或 `organization_acceptance` 配置激活前必须签核，`/api/adapters/readiness` 会汇总上线阻断和警告项；`samples/integrations/customer-sandbox/adapter-sandbox-pack.json` 已提供带 `schema_version=1`、显式 `system_type`、CRM/MES 分页与增量游标样本和固定 ERP `domain_target` 的 CRM/MES/ERP 及组织通讯录沙箱样本包，并由 `tests/backend/test_customer_sandbox_pack.py` 回归校验字段验收、签核、激活和 readiness 阻断项；`scripts/customer_sandbox_audit.py` 已支持 `pack_contract`、`sync_strategy_contract` 和 `business_flow_contract` 离线契约校验，能在临时数据库导入前阻断 schema、必需样本、字段映射、状态字典、组织/收件组引用、分页终止、增量游标和 CRM/MES/ERP 业务串联不合格的客户样本包，并生成离线 JSON 验收报告。
- Solver 治理：`solver_registry` 默认种子、`/api/solvers/registry` 查询/更新、`solvers:manage` 权限、前端 Solver 配置页、求解运行前启用校验、外部 stub 启用/运行阻断和 `scripts/solver_governance_audit.py` 离线治理验收报告已接入；该报告已通过 `policy_contract` 校验注册表模板、Rectpack 开源默认 solver、外部 placeholder 禁用/版本前缀/许可证策略、启用防线、Adapter 边界、Rectpack 有效性/确定性和 Benchmark 持久化。
- Benchmark 治理：`/api/benchmark/*` 已支持案例持久化、历史运行记录和前端基准测试页，运行结果写入 `benchmark_case` / `benchmark_run`。
- AI Assistant Function Calling：`/api/ai/tools/execute` 已接入受控工具调度器，可审计执行订单搜索、订单详情、图形读取、纸张规格、后端 Solver、Validator、方案对比、未放置解释和报告生成；生产导出、CRM 回写和 AI 直接创建拼版任务在 AI 边界内保持 `blocked`，每次调用写入 `operation_log`。

下一步建议：

1. 接入客户真实 CRM/MES/ERP 沙箱环境，基于现有样本包继续补齐客户专属字段差异、分页/增量策略和组织编码清单覆盖率。
2. 评估 Clipper2 或商业几何内核，用于更高性能的批量 offset、布尔差集和刀线清理。
3. 对接客户企业微信/飞书/邮件等真实通道沙箱，在现有离线通知通道审计报告基础上验收平台侧关键词/签名规则、失败重试阈值、告警冷却窗口和收件组治理。
4. 在真实 MinIO/NAS 对象存储环境验收对象 version_id/ETag 联动、恢复演练报告和周期维护任务。
5. 对接真实转换服务供应商沙箱，在现有离线转换供应商审计报告基础上补充实测 SLA、失败重试间隔、回调签名、token 轮换和供应商异常码清单覆盖率。
