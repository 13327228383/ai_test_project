# 附录 B · 数据表速查

本附录由脚本从 `src/core/schema.sql` 的建表语句逐条提取，共 **90 张表**、888 个列定义。该文件中 `CREATE TABLE` 字面出现 94 次，其中 4 次位于注释行，不计入表数。列说明取自 DDL 内的 `COMMENT`，类型与空值约束按原样保留。

## 表清单

| 表 | 定义行 | 列数 | 主键 | 说明 |
|:---|---:|---:|:---|:---|
| `projects` | 14 | 4 | `name` | 项目表 — 每个测试项目对应一条记录（元数据见 project_meta 表） |
| `project_versions` | 23 | 9 | `id` | 项目版本表 — 每次Step1拉取或版本切换产生一个版本记录 |
| `project_configs` | 39 | 4 | `project` | 项目配置表 — 存储项目级配置，替代旧的 projects/{name}/config.py 文件 |
| `users` | 52 | 10 | `id` | 用户账号表 — 存储登录用户的基本信息 |
| `roles` | 65 | 5 | `id` | 角色定义表 — 定义系统中可用的角色 |
| `user_roles` | 74 | 3 | `user_id,role_id` | 用户-角色关联表 — 一个用户可有多个角色 |
| `role_permissions` | 84 | 4 | `role_id,permission` | 角色-权限关联表 — 定义每个角色拥有的权限集合 |
| `role_projects` | 93 | 4 | `role_id,project` | 角色-项目关联表 — 定义角色在项目中的身份级别(owner/admin/member/viewer) |
| `project_members` | 104 | 5 | `user_id,project` | 项目成员表 — 直接绑定用户与项目的身份关系(owner/member两级) |
| `user_credentials_v2` | 116 | 6 | `user_id,project,key` | 用户凭证表 — 存储用户在各项目中的 API Key / 模型偏好等敏感配置 |
| `login_tracking` | 128 | 7 | `id` | 登录追踪表 — 记录用户的登录/登出/失败历史 |
| `flask_sessions` | 141 | 4 | `id` | Flask Session 存储表 — 服务端持久化用户登录态 |
| `api_tokens` | 150 | 11 | `id` | API Token 鉴权表 — CI/CD 外部调用凭据，SHA256 哈希存储 |
| `case_meta` | 177 | 30 | `project,version_id,case_id` | 测试用例元数据表 — 测试用例的审核/置信度/风险/覆盖率指标等元信息（schema.sql 是唯一事实源） |
| `executions` | 218 | 23 | `id` | 执行记录表 — 每次测试用例执行验证的结果记录 |
| `flaky_guard_history` | 256 | 9 | `id` | FlakyGuard 执行历史 — 每次测试用例执行后持久化，用于失败率统计 |
| `flaky_guard_detected` | 273 | 12 | `id` | FlakyGuard 检测结果 — 标记不稳定测试用例及根因 |
| `action_logs` | 293 | 8 | `id` | 操作日志表 — 记录用户关键操作，用于审计追踪；默认保留90天且全表最多100000行 |
| `config_history` | 308 | 8 | `id` | 配置变更历史表 — 记录配置项变更的前后值 |
| `document_versions` | 326 | 9 | `id` | 文档版本表 — 记录导出文档（Excel/XMind/报告）的历史版本 |
| `ai_health_metrics` | 346 | 12 | `id` | AI 健康度指标表 — 流水线埋点收集的覆盖率/交叉校验/守卫拦截/三轨校验/Flaky/CoVe/缓存等运营指标 |
| `figma_mcp_cache` | 373 | 9 | `cache_key` | Figma MCP 项目/版本级缓存表 — 缓存当前项目版本的 Figma API/MCP 原始响应 |
| `figma_api_quota` | 389 | 9 | `provider,credential_scope,credential_hash,current_month` | Figma API 配额表 — 按凭证追踪Figma API调用配额，防止触发429限流 |
| `figma_change_sessions` | 403 | 5 | `project,version_label,session_id` | Figma 变更会话表 — 记录每次Figma设计稿变更检测会话 |
| `figma_node_hashes` | 414 | 5 | `project,version_label,node_id` | Figma 节点哈希表 — 节点级MD5哈希字典，用于变更检测锚点 |
| `figma_images` | 426 | 14 | `id` | Figma 图片存储表 — Figma MCP download_figma_images 下载的图片全量转存MySQL，不落磁盘 |
| `pipeline_checkpoints` | 451 | 9 | `run_id,step` | 流水线检查点表 |
| `source_documents` | 471 | 13 | `project,doc_type,version_label` | 统一文档内容表（含上传文件元数据） |
| `generated_reports` | 499 | 11 | `` | 流水线生成报告表（与 source_documents 分离，避免源文档与生成物混用） |
| `system_feature_list` | 534 | 12 | `` | 目标系统功能清单表（多源融合：DOM/Figma/需求/YApi） |
| `execution_screenshots` | 558 | 10 | `` | 截图存储表 — 存储 Step4 执行失败的截图（取代磁盘PNG，自动保留6个月后清理） |
| `visual_baselines` | 579 | 8 | `project,baseline_key` | 视觉基线表 — Step4 视觉回归对比基线截图（取代磁盘 visual_baselines/*.png） |
| `vision_heal_cache` | 596 | 7 | `cache_key` | 视觉自愈缓存 — 成功的元素修复记录（取代磁盘JSON，跨session复用） |
| `self_heal_patches` | 612 | 13 | `id` | 自愈补丁持久化表 — 记录成功修复的定位器补丁，验证通过后跨运行复用 |
| `step4_op_cache` | 639 | 12 | `cache_key` | Step4 操作指令语义缓存表 — 基于页面DOM指纹+步骤目标的语义级缓存 |
| `rag_chunks` | 662 | 21 | `scope_type,scope_id,chunk_id` | RAG 项目级文档分块表 — 项目级累积，按稳定 chunk_id upsert，is_active 软删除 |
| `run_logs` | 696 | 8 | `id` | 流水线运行日志表 — 存储每次执行的详细日志（取代磁盘 *.log 文件） |
| `qa_feedback` | 726 | 7 | `id` | QA 反馈表 — 记录项目反馈和全局反馈模式（取代磁盘 JSONL） |
| `test_rules` | 742 | 4 | `scope_type,scope_id` | 测试规则表 — 存储全局默认规则和项目覆盖规则；自动发现规则每项目最多5000条 |
| `requirements` | 754 | 28 | `project,version_id,req_id` | 需求列表表 — 功能清单驱动拆解，存储原子需求三元组 |
| `test_cases` | 795 | 23 | `project,version_id,case_id` | 测试用例表 — v3.0 用例驱动，核心数据见payload_json（actions + checks） |
| `case_requirement_mapping` | 827 | 9 | `project,version_id,case_id` | 测试用例-需求映射表 |
| `review_log` | 847 | 9 | `id` | 审核操作日志表 — 记录谁在什么时间对什么做了什么审核动作 |
| `source_hashes` | 867 | 6 | `project,doc_type,version_label` | 源数据统一哈希表 — Step1/Step2 各数据源的总体内容哈希（断点续跑锚点） |
| `source_confluence_hashes` | 880 | 5 | `project,version_label,hash_key` | Confluence 源数据哈希表 — Step1断点续跑用 |
| `source_yapi_hashes` | 890 | 4 | `project,version_label` | YApi 源数据哈希表 — Step1断点续跑用 |
| `source_breakdown_hashes` | 899 | 6 | `project,version_label,hash_key` | Step2 需求拆解哈希表 — 断点续跑用 |
| `coverage_results` | 914 | 19 | `project,run_id,req_id` | 覆盖率结果表 — 每个需求在各维度的覆盖状态 |
| `coverage_summaries` | 940 | 11 | `project,run_id` | 覆盖率汇总表 — 每次运行的覆盖率总体统计 |
| `rag_usage_log` | 960 | 10 | `id` | RAG 使用日志 — 记录 RAG 检索结果到用例字段的映射 |
| `diff_snapshots` | 982 | 6 | `project,version_label,snapshot_type` | 版本差异快照表 — 存储各版本的需求/API/DB/测试用例快照 |
| `trace_links` | 997 | 11 | `req_id,project,version_id` | 追溯链路表 — 需求级元数据（资源详情走 trace_link_chunks + rag_chunks） |
| `requirement_slices` | 1017 | 16 | `project,version_id,slice_id` | 需求切片表 — 持久化智能切片结果，支持关系链路追溯到切片级别 |
| `trace_link_chunks` | 1067 | 8 | `req_id,project,version_id,chunk_type,chunk_id` | 追溯链路关联表 — 需求与RAG chunk的单一对多关联（消除资源数据分叉） |
| `action_categories` | 1094 | 5 | `category_id` | 通用操作指令分类表 — 全项目共享的浏览器操作分类目录 |
| `action_actions` | 1103 | 15 | `category_id,action_id` | 通用操作指令详情表 — 全项目共享的浏览器操作定义 |
| `project_synonym_overrides` | 1136 | 5 | `project,core_word` | 项目同义词覆盖表 — 全局同义词库的项目级覆盖 |
| `project_meta` | 1155 | 7 | `project` | 项目元数据表 — 项目的显示名/描述/状态等辅助信息 |
| `user_bg_gallery` | 1171 | 7 | `` | 用户背景图库表 — 每用户最多20张背景图，LONGBLOB存储 |
| `ai_generation_logs` | 1187 | 12 | `project,log_id` | AI生成日志表 — 记录幻觉/环境等AI生成异常 |
| `prompt_templates` | 1208 | 7 | `name` | 系统级 Prompt 模板表 — DB 唯一事实源，前端可编辑，is_seed=1 的种子模板可恢复默认 |
| `synonym_library` | 1219 | 4 | `` | 系统级同义词库 — 全局默认同义词；项目差异通过 project_synonym_overrides 覆盖 |
| `capability_cache` | 1231 | 22 | `` | Step4 项目级业务能力缓存表 |
| `capability_cache_events` | 1259 | 11 | `` | Step4 项目级能力缓存事件表 |
| `capability_cache_policy` | 1276 | 5 | `` | Step4 项目级能力库治理策略表 |
| `run_summary` | 1288 | 26 | `` | Step5 项目运行级摘要表 — 记录每次流水线报告结果，用于趋势对比和历史质量分析 |
| `tool_execution_log` | 1333 | 15 | `id` | 融合架构工具执行日志表 — 记录每个tool_call的执行详情，用于链路追踪和性能分析 |
| `system_config` | 1457 | 4 | `key` | 系统配置表 — 加密密钥、全局参数等 |
| `confidence_calibration` | 1477 | 8 | `id` | 置信度校准表 — 证据引擎：记录AI声称置信度 vs 实际执行通过率，驱动贝叶斯校准 |
| `pipeline_user_config` | 1496 | 6 | `` | 流水线用户级配置覆盖表 — 优先级高于环境变量和系统默认配置 |
| `figma_pages` | 1510 | 11 | `id` | Figma 页面事实表 — 顶层 FRAME/CANVAS，RAG 重建时读取 |
| `figma_nodes` | 1529 | 11 | `id` | Figma 节点事实表 — 页面内的交互元素，RAG 重建时读取 |
| `figma_components` | 1548 | 8 | `id` | Figma 组件事实表 — 可复用组件定义，RAG 重建时读取 |
| `pipeline_self_test_results` | 1623 | 10 | `id` | 流水线自检结果表 — 诊断中心存储每次自检的Step级结果 |
| `metric_thresholds` | 1714 | 12 | `id` | 指标阈值配置 — 通用告警体系 |
| `nav_graph_nodes` | 1745 | 11 | `id` | 真实爬取导航图节点（替 paper nav_map） |
| `nav_graph_edges` | 1764 | 9 | `id` | 页面间转移边（状态转移，喂 op_cache） |
| `op_cache_v2` | 1787 | 9 | `id` | 真实原子指令序列缓存（替 0 行 op_cache，SOTA 三拍第①拍） |
| `calibration_samples` | 1803 | 8 | `id` | 主动学习样本（替休眠 calibration，解 85 常量根因） |
| `golden_set` | 1817 | 8 | `id` | 引擎自评估黄金集 |
| `oracle_results` | 1836 | 11 | `id` | 每次执行的三种 oracle 结论 |
| `visual_review` | 1852 | 9 | `id` | VLM 视觉 oracle 复核会话 |
| `multimodal_chunks` | 1867 | 8 | `id` | 多模态 RAG 块（需求+DOM+figma） |
| `review_lane` | 1880 | 8 | `id` | 三车道路由（绿/黄/红），替自动 approved |
| `case_upload_history` | 1918 | 10 | `id` | 用例上传历史表 |
| `clarification_sessions` | 1942 | 15 | `id` | 需求澄清会话表 — 每条高危歧义项一个会话，支持多轮问答与状态机 |
| `clarification_answers` | 1964 | 7 | `id` | 需求澄清回答表 — 一次回答一行，多轮可追溯 |
| `navigation_match_logs` | 1995 | 17 | `id` | |
| `plugin_interventions` | 2018 | 8 | `id` | 插件干预记录 — 各钩子返回的实质性干预结果持久化 |
| `user_preferences` | 2032 | 4 | `user_id,pref_key` | |

## 列定义

### `projects`　—　项目表 — 每个测试项目对应一条记录（元数据见 project_meta 表）

定义行 14，主键 `name`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `name` | VARCHAR(128) | NO | 项目名（唯一标识，如 某SPA系统） |
| `status` | VARCHAR(32) | NO | 状态: active / archived / deleted |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `project_versions`　—　项目版本表 — 每次Step1拉取或版本切换产生一个版本记录

定义行 23，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 版本记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签（如 20260610_152230 / v1.2.3） |
| `description` | VARCHAR(512) | NO | 版本描述 |
| `status` | VARCHAR(32) | NO | 状态: draft / active / archived / deleted |
| `created_by` | VARCHAR(64) | NO | 创建者用户ID → users.id |
| `created_at` | DATETIME | NO | 创建时间 |
| `activated_at` | DATETIME | YES | 激活时间 |
| `deleted_at` | DATETIME | YES | D-1-1: 软删除时间（status=deleted 时填写） |

### `project_configs`　—　项目配置表 — 存储项目级配置，替代旧的 projects/{name}/config.py 文件

定义行 39，主键 `project`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `config_json` | JSON | NO | 项目配置（JSON，合并到系统配置中） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `users`　—　用户账号表 — 存储登录用户的基本信息

定义行 52，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | VARCHAR(64) | NO | 用户ID（唯一标识） |
| `name` | VARCHAR(128) | NO | 用户昵称/显示名 |
| `password` | VARCHAR(256) | NO | 密码哈希（SHA-256，不加盐） |
| `failed_attempts` | INT | NO | 连续登录失败次数（成功后归零） |
| `locked_until` | DATETIME | YES | 锁定截止时间（NULL=未锁定） |
| `last_login` | DATETIME | YES | 最后登录时间 |
| `status` | VARCHAR(32) | NO | 用户状态（active/disabled） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `last_project` | VARCHAR(128) | NO | 用户上次选择的项目（登录后自动恢复，空=未选过） |

### `roles`　—　角色定义表 — 定义系统中可用的角色

定义行 65，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | VARCHAR(64) | NO | 角色ID（如 admin / user / viewer） |
| `name` | VARCHAR(128) | NO | 角色显示名（如 管理员 / 普通用户 / 只读用户） |
| `description` | VARCHAR(512) | NO | 角色描述 |
| `is_system` | TINYINT(1) | NO | 是否系统内置角色（1=是，不可删除） |
| `created_at` | DATETIME | NO | 创建时间 |

### `user_roles`　—　用户-角色关联表 — 一个用户可有多个角色

定义行 74，主键 `user_id,role_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `user_id` | VARCHAR(64) | NO | 用户ID → users.id |
| `role_id` | VARCHAR(64) | NO | 角色ID → roles.id |
| `created_at` | DATETIME | NO | 分配时间 |

### `role_permissions`　—　角色-权限关联表 — 定义每个角色拥有的权限集合

定义行 84，主键 `role_id,permission`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `role_id` | VARCHAR(64) | NO | 角色ID → roles.id |
| `permission` | VARCHAR(128) | NO | 权限标识（如 execution.run / project.manage / report.view） |
| `granted` | TINYINT(1) | NO | 是否授予（1=授予；0=显式撤销，用于角色权限覆盖） |
| `created_at` | DATETIME | NO | 分配时间 |

### `role_projects`　—　角色-项目关联表 — 定义角色在项目中的身份级别(owner/admin/member/viewer)

定义行 93，主键 `role_id,project`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `role_id` | VARCHAR(64) | NO | 角色ID → roles.id |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `level` | VARCHAR(32) | NO | 项目级身份: owner/admin/member/viewer |
| `created_at` | DATETIME | NO | 分配时间 |

### `project_members`　—　项目成员表 — 直接绑定用户与项目的身份关系(owner/member两级)

定义行 104，主键 `user_id,project`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `user_id` | VARCHAR(64) | NO | 用户ID → users.id |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `role` | VARCHAR(32) | NO | 项目身份: owner/member |
| `added_by` | VARCHAR(64) | NO | 添加者用户ID |
| `created_at` | DATETIME | NO | 分配时间 |

### `user_credentials_v2`　—　用户凭证表 — 存储用户在各项目中的 API Key / 模型偏好等敏感配置

定义行 116，主键 `user_id,project,key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `user_id` | VARCHAR(64) | NO | 用户ID → users.id |
| `project` | VARCHAR(128) | NO | 项目名（* 表示全局凭证） |
| `key` | VARCHAR(128) | NO | 凭证键名（如 API_KEY / API_MODEL / API_BASE_URL） |
| `value` | TEXT | NO | 凭证值（加密存储） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `login_tracking`　—　登录追踪表 — 记录用户的登录/登出/失败历史

定义行 128，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `user_id` | VARCHAR(64) | NO | 用户ID → users.id |
| `action` | VARCHAR(32) | NO | 动作: login / logout / login_failed |
| `ip_address` | VARCHAR(64) | NO | 登录IP |
| `user_agent` | TEXT | NO | 浏览器UA |
| `detail` | TEXT | NO | 详细信息（失败原因等） |
| `created_at` | DATETIME | NO | 发生时间 |

### `flask_sessions`　—　Flask Session 存储表 — 服务端持久化用户登录态

定义行 141，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | INT AUTO_INCREMENT | NO | 自增主键 |
| `session_id` | VARCHAR(255) | YES | Flask session ID（唯一） |
| `data` | BLOB | YES | 序列化后的 session 数据 |
| `expiry` | DATETIME | YES | session 过期时间 |

### `api_tokens`　—　API Token 鉴权表 — CI/CD 外部调用凭据，SHA256 哈希存储

定义行 150，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `token_hash` | VARCHAR(128) | NO | token 的 SHA256 哈希（不存明文） |
| `project` | VARCHAR(128) | NO | 授权项目（* 表示所有项目，故不设置外键） |
| `label` | VARCHAR(255) | NO | Token 用途标签（如 Jenkins CI / GitHub Actions） |
| `scopes` | TEXT | YES | 授权范围（JSON 数组，如 ["run","read"]） |
| `created_by` | VARCHAR(128) | NO | 创建者 user_id → users.id |
| `last_used_at` | DATETIME | YES | 最后使用时间 |
| `use_count` | BIGINT | NO | 累计使用次数 |
| `is_active` | TINYINT | NO | 是否启用（0=停用 1=启用） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | YES | 更新时间 |

### `case_meta`　—　测试用例元数据表 — 测试用例的审核/置信度/风险/覆盖率指标等元信息（schema.sql 是唯一事实源）

定义行 177，主键 `project,version_id,case_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `case_asset_file` | VARCHAR(256) | NO | 来源文件名 |
| `confidence` | DECIMAL(5,2) | NO | 置信度评分（0-100） |
| `risk_level` | VARCHAR(32) | NO | 风险等级: low / medium / high / critical |
| `risk_embedding` | JSON | YES | 风险分析用embedding向量(384维，用于k-NN分类) |
| `review_status` | VARCHAR(32) | NO | 审核状态: pending / approved / rejected / modified |
| `reviewed_by` | VARCHAR(64) | NO | 审核人用户ID → users.id |
| `reviewed_at` | DATETIME | YES | 审核时间 |
| `case_coverage` | FLOAT | YES | 用例覆盖度（Step3 指标，与 case_completeness 双轨保留） |
| `case_completeness` | FLOAT | YES | 用例完整度（Step3 指标: 五字段填充率） |
| `evidence_total` | FLOAT | YES | 证据总量（Step3 指标: UI+API+DB 三轨条目数，FLOAT 兼容浮点统计） |
| `avg_confidence` | FLOAT | YES | 平均置信度（Step3 指标） |
| `case_driven_rate` | FLOAT | YES | 用例驱动率（Step3 指标） |
| `strictness_score` | FLOAT | YES | 严格度评分（Step4 指标: must/should/may 加权） |
| `execution_factors` | JSON | YES | System B 执行置信度因子明细（Step4 指标: assertion_rate等6因子JSON） |
| `evidence_confidence` | FLOAT | YES | System C 证据校准置信度（Step4 指标: 贝叶斯融合AI声称+历史校准+独立证据） |
| `evidence_ai_raw` | FLOAT | YES | System C AI原始声称置信度（0-1） |
| `evidence_cal_accuracy` | FLOAT | YES | System C 历史校准准确率（0-1，无数据时NULL） |
| `evidence_strength` | FLOAT | YES | System C 独立证据强度（0-1） |
| `evidence_level` | VARCHAR(16) | YES | System C 置信度等级: HIGH/MEDIUM/LOW |
| `evidence_verdict` | VARCHAR(32) | YES | System C 判决类型: evidence_calibrated/calibrated_only/cold_start/ai_only |
| `evidence_cold_start` | TINYINT | YES | System C 是否冷启动: 1=冷启动 0=有足够校准样本 |
| `action_coverage` | VARCHAR(32) | YES | 动作覆盖率（Step3 指标: 动作被用例覆盖的比例） |
| `assertion_coverage` | VARCHAR(32) | YES | 断言覆盖率（Step3 指标: 断言被用例覆盖的比例） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `method_valid` | TINYINT | NO | T2: 方法论标签校验是否通过(1=通过/0=非法已处置) |
| `method_fix_note` | VARCHAR(255) | NO | T2: 方法论标签修正/标记说明（如 白名单外test_method=XX 已就近映射为EP） |

### `executions`　—　执行记录表 — 每次测试用例执行验证的结果记录

定义行 218，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 执行记录ID |
| `run_id` | VARCHAR(64) | NO | 流水线运行ID（关联 pipeline_checkpoints.run_id） |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `user_id` | VARCHAR(64) | NO | 执行用户ID → users.id |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `case_asset_file` | VARCHAR(256) | NO | 用例资产文件名 |
| `case_row` | INT | NO | 在文件中的行号（兼容字段） |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `status` | VARCHAR(32) | NO | 结果: PASS / FAIL / SKIP / ERROR / FLAKY |
| `failure_reason` | TEXT | NO | 失败原因（FAIL/ERROR时填写） |
| `failure_category` | VARCHAR(32) | NO | 失败归属: PRODUCT_BUG=被测系统bug / HARNESS_ERROR=执行系统bug / ENVIRONMENT_ERROR=环境问题 / DA… |
| `failure_chain` | TEXT | YES | 失败链路JSON：记录策略执行序列和每步结果，用于精准判定failure_category |
| `screenshot` | VARCHAR(512) | NO | 失败截图路径 |
| `executed_at` | DATETIME | NO | 执行时间 |
| `verification_track` | VARCHAR(32) | NO | D6M 功能轨道: api_track/db_track/ui_track |
| `cross_cut_dims` | VARCHAR(200) | NO | D6M 横切维度声明: security,accessibility,performance,visual |
| `check_type` | VARCHAR(32) | NO | 检查类型: pre_gate/step_exec/... |
| `expected` | TEXT | YES | 预期结果 |
| `actual` | TEXT | YES | 实际结果 |
| `details` | TEXT | YES | 详细信息 |
| `confidence` | DOUBLE | NO | 置信度评分（0-1，ConfidenceGate 计算后写入，_run_step 直接读取） |
| `confidence_factors` | TEXT | YES | 置信度因子JSON（assertion_rate/case_completeness/hard_verify_rate/first_try_rate/sema… |
| `diagnosis` | VARCHAR(64) | YES | 失败根因分类: SUT_DB_NOT_MIGRATED/SUT_UNREACHABLE/CREDENTIAL_INVALID/UNKNOWN |

### `flaky_guard_history`　—　FlakyGuard 执行历史 — 每次测试用例执行后持久化，用于失败率统计

定义行 256，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `run_id` | VARCHAR(64) | NO | 流水线运行ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `passed` | TINYINT(1) | NO | 是否通过: 1=PASS / 0=FAIL |
| `failure_detail` | TEXT | NO | 失败原因 |
| `step_count` | INT | NO | 步骤数 |
| `recorded_at` | DATETIME | NO | 记录时间 |

### `flaky_guard_detected`　—　FlakyGuard 检测结果 — 标记不稳定测试用例及根因

定义行 273，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `failure_rate` | FLOAT | NO | 失败率 |
| `total_runs` | INT | NO | 总执行次数 |
| `failures` | INT | NO | 失败次数 |
| `root_cause` | VARCHAR(64) | NO | 根因分类 |
| `fix_suggestion` | TEXT | NO | 修复建议 |
| `first_seen` | DATETIME | NO | 首次发现时间 |
| `last_seen` | DATETIME | NO | 最近发现时间 |
| `detected_at` | DATETIME | NO | 检测时间 |

### `action_logs`　—　操作日志表 — 记录用户关键操作，用于审计追踪；默认保留90天且全表最多100000行

定义行 293，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 日志ID |
| `user_id` | VARCHAR(64) | NO | 操作用户ID → users.id |
| `scope_type` | VARCHAR(32) | NO | 日志范围类型（system/project/user/global） |
| `scope_id` | VARCHAR(128) | NO | 范围ID（项目名/用户ID/空） |
| `action` | VARCHAR(128) | NO | 操作标识（如 step1.run / step3.model / intent.approve） |
| `detail` | JSON | YES | 操作详情（JSON） |
| `ip_address` | VARCHAR(64) | NO | 操作来源IP地址（PV-007 审计追踪） |
| `created_at` | DATETIME | NO | 操作时间 |

### `config_history`　—　配置变更历史表 — 记录配置项变更的前后值

定义行 308，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `user_id` | VARCHAR(64) | NO | 操作用户ID → users.id |
| `scope_type` | VARCHAR(32) | NO | 配置范围类型（system/project/user/global） |
| `scope_id` | VARCHAR(128) | NO | 范围ID（项目名/用户ID/空） |
| `section` | VARCHAR(128) | NO | 配置节（如 ai / playwright / storage） |
| `old_value` | JSON | YES | 旧值 |
| `new_value` | JSON | YES | 新值 |
| `created_at` | DATETIME | NO | 变更时间 |

### `document_versions`　—　文档版本表 — 记录导出文档（Excel/XMind/报告）的历史版本

定义行 326，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `doc_type` | VARCHAR(64) | NO | 文档类型: requirement / validation_intent / report / xmind |
| `version` | VARCHAR(128) | NO | 版本标识 |
| `file_path` | VARCHAR(512) | NO | 文件路径 |
| `file_hash` | VARCHAR(64) | NO | 文件MD5哈希 |
| `file_size` | BIGINT | NO | 文件大小（字节） |
| `created_by` | VARCHAR(64) | NO | 创建者用户ID → users.id |
| `created_at` | DATETIME | NO | 创建时间 |

### `ai_health_metrics`　—　AI 健康度指标表 — 流水线埋点收集的覆盖率/交叉校验/守卫拦截/三轨校验/Flaky/CoVe/缓存等运营指标

定义行 346，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 版本ID → project_versions.id，0=未指定/legacy |
| `run_id` | VARCHAR(64) | NO | 流水线运行ID（关联 pipeline_checkpoints.run_id） |
| `step` | INT | NO | 流水线阶段编号（1=需求拆解, 2=覆盖率, 3=用例建模, 4=执行验证, 5=报告） |
| `metric_type` | VARCHAR(64) | NO | 指标大类: coverage / cross_check / deep_check / rule_check / guard_reject / action_… |
| `metric_name` | VARCHAR(128) | NO | 具体指标名（如 api_mismatch / orphan_modules / guard_rejection / cache_hit 等） |
| `category` | VARCHAR(64) | NO | 子分类（如 api/ui/enum/ai/action/reverse/forward/3rail 等） |
| `value` | DOUBLE | NO | 指标数值（计数、比率或布尔值 0/1） |
| `total` | DOUBLE | NO | 分母/总量（用于在查询时计算比率，纯计数时为 0） |
| `details` | JSON | YES | 扩展明细（JSON 对象，如 step_desc / 三轨结果等） |
| `created_at` | DATETIME | NO | 记录创建时间 |

### `figma_mcp_cache`　—　Figma MCP 项目/版本级缓存表 — 缓存当前项目版本的 Figma API/MCP 原始响应

定义行 373，主键 `cache_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `cache_key` | VARCHAR(256) | NO | 缓存键（project:version:file_key:node_id:depth 格式） |
| `project` | VARCHAR(128) | NO | 所属项目名称；Figma 缓存按项目隔离 |
| `version_label` | VARCHAR(128) | NO | 所属项目版本；为空表示未绑定具体版本 |
| `file_key` | VARCHAR(128) | NO | Figma 文件 KEY |
| `node_id` | VARCHAR(128) | NO | 节点 ID |
| `depth` | INT | NO | 递归深度 |
| `response_json` | MEDIUMTEXT | NO | Figma API/MCP 响应 JSON（可达数MB） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `figma_api_quota`　—　Figma API 配额表 — 按凭证追踪Figma API调用配额，防止触发429限流

定义行 389，主键 `provider,credential_scope,credential_hash,current_month`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `provider` | VARCHAR(32) | NO | 服务提供方（默认 figma） |
| `credential_scope` | VARCHAR(64) | NO | 凭证范围类型（token/user/global/legacy_project） |
| `credential_hash` | VARCHAR(64) | NO | 凭证哈希，不保存明文 Token |
| `current_month` | VARCHAR(7) | NO | 当前月份（YYYY-MM） |
| `total_used` | INT | NO | 当月已用请求数 |
| `rate_limit_until` | DOUBLE | NO | 限流结束时间戳（Unix秒） |
| `last_call_at` | DOUBLE | NO | 最后调用时间戳（Unix秒） |
| `last_error` | LONGTEXT | YES | 最后错误信息 |
| `updated_at` | DOUBLE | NO | 更新时间戳（Unix秒） |

### `figma_change_sessions`　—　Figma 变更会话表 — 记录每次Figma设计稿变更检测会话

定义行 403，主键 `project,version_label,session_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `session_id` | VARCHAR(255) | NO | 会话ID |
| `session_json` | LONGTEXT | NO | 会话完整JSON |
| `created_at` | DOUBLE | NO | 创建时间戳（Unix秒） |

### `figma_node_hashes`　—　Figma 节点哈希表 — 节点级MD5哈希字典，用于变更检测锚点

定义行 414，主键 `project,version_label,node_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `node_id` | VARCHAR(255) | NO | Figma 节点ID |
| `hash_value` | VARCHAR(255) | NO | 节点内容MD5哈希 |
| `updated_at` | DOUBLE | NO | 更新时间戳（Unix秒） |

### `figma_images`　—　Figma 图片存储表 — Figma MCP download_figma_images 下载的图片全量转存MySQL，不落磁盘

定义行 426，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 图片自增ID |
| `project` | VARCHAR(128) | NO | 所属项目 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `file_key` | VARCHAR(128) | NO | Figma 文件 KEY |
| `page_id` | VARCHAR(255) | NO | 所属 Figma 页面/FRAME 节点ID |
| `page_name` | VARCHAR(255) | NO | 所属 Figma 页面/FRAME 名称（与 trace_links.figma_pages_json 对齐） |
| `node_id` | VARCHAR(255) | NO | Figma 节点ID（I格式） |
| `file_name` | VARCHAR(255) | NO | 原始文件名（含扩展名） |
| `image_data` | LONGBLOB | NO | 图片二进制数据（PNG/SVG/GIF） |
| `image_type` | VARCHAR(10) | NO | 图片类型扩展名（png/svg/gif） |
| `width` | INT | YES | 图片宽度（像素，SVG可能为空） |
| `height` | INT | YES | 图片高度（像素，SVG可能为空） |
| `image_size` | INT | NO | 文件大小（字节） |
| `created_at` | TIMESTAMP | NO | 创建时间 |

### `pipeline_checkpoints`　—　流水线检查点表

定义行 451，主键 `run_id,step`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `run_id` | VARCHAR(64) | NO | 运行ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `step` | INT | NO | 阶段编号(1-5) |
| `status` | VARCHAR(32) | NO | 状态: running/success/failed |
| `started_at` | DATETIME | NO | 开始时间 |
| `completed_at` | DATETIME | YES | 完成时间 |
| `error_msg` | TEXT | YES | 失败原因 |
| `integrity` | VARCHAR(32) | YES | 完整性标记（兼容旧 checkpoint_store 表） |

### `source_documents`　—　统一文档内容表（含上传文件元数据）

定义行 471，主键 `project,doc_type,version_label`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `doc_type` | VARCHAR(64) | NO | 文档类型: confluence / yapi / yapi_structured / xmind / deployment / requirement / … |
| `version_label` | VARCHAR(128) | NO | 版本标签 → project_versions.version_label |
| `content` | LONGTEXT | NO | 文档原始内容（可能很大） |
| `content_hash` | VARCHAR(64) | NO | 内容MD5哈希 |
| `char_count` | INT | NO | 字符数 |
| `filename` | VARCHAR(255) | NO | 原始文件名（仅上传文档使用） |
| `file_format` | VARCHAR(50) | NO | 文件格式: xlsx/csv/docx/md/sql/json/xmind/txt |
| `weight` | DECIMAL(3,2) | NO | 权重 0-1（仅上传文档使用） |
| `category` | VARCHAR(50) | NO | 分类: requirement/data_dict/api_supplement/design_supplement/test_data/ddl_schema |
| `source_url` | VARCHAR(512) | NO | 原始来源 URL（Confluence/YApi/Figma 等远程数据源） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 最后更新时间 |

### `generated_reports`　—　流水线生成报告表（与 source_documents 分离，避免源文档与生成物混用）

定义行 499，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | YES | 报告记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `version_label` | VARCHAR(128) | NO | 版本标签 → project_versions.version_label |
| `run_id` | VARCHAR(64) | NO | 运行批次ID（coverage_report/html_report/report 均关联） |
| `report_type` | VARCHAR(64) | NO | 报告类型: report / html_report / coverage_report |
| `content` | LONGTEXT | NO | 报告内容（Markdown/HTML/覆盖率文本） |
| `content_hash` | VARCHAR(64) | NO | 内容MD5哈希 |
| `char_count` | INT | NO | 字符数 |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 最后更新时间 |

### `system_feature_list`　—　目标系统功能清单表（多源融合：DOM/Figma/需求/YApi）

定义行 534，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | YES | 功能清单ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 → project_versions.version_label |
| `page_url` | VARCHAR(512) | NO | 页面完整URL |
| `page_path` | VARCHAR(256) | NO | 页面路径 |
| `page_title` | VARCHAR(256) | NO | 页面标题 |
| `features_json` | LONGTEXT | NO | 功能列表JSON（20类元素：button/input/chart/dropdown/tab/modal/upload等） |
| `statistics_json` | TEXT | YES | 统计信息JSON |
| `source` | VARCHAR(32) | NO | 功能来源：dom(目标系统运行时) / figma(设计稿) / requirement(需求文档) / yapi(接口契约) |
| `source_status` | VARCHAR(32) | NO | 功能状态：implemented(dom有) / designed(figma有dom无) / planned(需求有figma无) / api_only(y… |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 最后更新时间 |

### `execution_screenshots`　—　截图存储表 — 存储 Step4 执行失败的截图（取代磁盘PNG，自动保留6个月后清理）

定义行 558，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | YES | 截图ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `run_id` | VARCHAR(64) | NO | 运行ID |
| `step_num` | INT | NO | 截图序号/动作序号 |
| `image_data` | MEDIUMBLOB | NO | 截图二进制数据（PNG） |
| `file_size` | INT | NO | 文件大小（字节） |
| `status` | VARCHAR(16) | YES | 用例终态 PASS/FAIL/BLOCKED |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `created_at` | DATETIME | NO | 创建时间 |

### `visual_baselines`　—　视觉基线表 — Step4 视觉回归对比基线截图（取代磁盘 visual_baselines/*.png）

定义行 579，主键 `project,baseline_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `baseline_key` | VARCHAR(256) | NO | 基线键 {project}_{case_id}_step{n} |
| `image_data` | MEDIUMBLOB | NO | 基线截图二进制（PNG） |
| `image_hash` | VARCHAR(64) | NO | 基线截图 MD5（快速比对路径） |
| `diff_data` | MEDIUMBLOB | YES | 差异高亮图二进制（mismatch 时生成） |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `created_at` | DATETIME | NO | 基线建立时间 |
| `updated_at` | DATETIME | NO | 最近对比时间 |

### `vision_heal_cache`　—　视觉自愈缓存 — 成功的元素修复记录（取代磁盘JSON，跨session复用）

定义行 596，主键 `cache_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `cache_key` | VARCHAR(64) | NO | 缓存键 MD5(ai_id:target_hint) |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=跨版本复用 |
| `ai_id` | VARCHAR(256) | NO | 动作AI标识 |
| `original_hint` | TEXT | NO | 原定位提示 |
| `new_hint` | TEXT | NO | 自愈后新定位提示 |
| `created_at` | DATETIME | NO | 记录时间 |

### `self_heal_patches`　—　自愈补丁持久化表 — 记录成功修复的定位器补丁，验证通过后跨运行复用

定义行 612，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id |
| `case_id` | VARCHAR(256) | NO | 测试用例ID → case_meta.case_id |
| `original_hint` | TEXT | YES | 原始定位器（修复前） |
| `healed_hint` | TEXT | YES | 修复后定位器 |
| `strategy` | VARCHAR(64) | NO | 自愈策略名称 |
| `verified` | TINYINT | NO | 是否已验证（0=待验证 1=验证通过 2=验证失败） |
| `verify_at` | DATETIME | YES | 验证时间 |
| `verify_detail` | TEXT | YES | 验证详情（错误信息等） |
| `page_url` | VARCHAR(1024) | YES | 当前页面URL |
| `created_at` | DATETIME | NO | 补丁创建时间 |
| `updated_at` | DATETIME | YES | 补丁更新时间 |

### `step4_op_cache`　—　Step4 操作指令语义缓存表 — 基于页面DOM指纹+步骤目标的语义级缓存

定义行 639，主键 `cache_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `cache_key` | VARCHAR(64) | NO | 缓存键（MD5哈希） |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=跨版本复用 |
| `goal_normalized` | TEXT | NO | 归一化的操作目标 |
| `page_path` | TEXT | NO | 页面路径（netloc + path） |
| `dom_fingerprint` | TEXT | NO | DOM结构指纹 |
| `config_hash` | VARCHAR(16) | NO | 配置哈希 |
| `actions_json` | LONGTEXT | NO | AI返回的操作指令JSON |
| `hit_count` | INT | NO | 命中次数 |
| `success_count` | INT | NO | 成功执行次数 |
| `created_at` | DOUBLE | NO | 创建时间戳（Unix秒） |
| `last_hit_at` | DOUBLE | YES | 最后命中时间戳（Unix秒） |

### `rag_chunks`　—　RAG 项目级文档分块表 — 项目级累积，按稳定 chunk_id upsert，is_active 软删除

定义行 662，主键 `scope_type,scope_id,chunk_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `scope_type` | VARCHAR(32) | NO | 知识范围类型（global/project） |
| `scope_id` | VARCHAR(128) | NO | 范围ID（global为空，project为项目名） |
| `version_label` | VARCHAR(128) | NO | 项目版本标签（审计：最后构建版本） |
| `chunk_id` | VARCHAR(256) | NO | 分块唯一ID（稳定键：api:POST:/path / db:table / figma:page / req:stable_key） |
| `type` | VARCHAR(32) | NO | 分块类型：api / db_table / figma_page / requirement / upload_* |
| `text` | MEDIUMTEXT | NO | 分块文本内容 |
| `meta` | JSON | YES | 元数据（JSON：endpoint/table/page信息） |
| `keywords` | JSON | YES | 关键词数组（用于 BM25 关键词匹配） |
| `vector_blob` | MEDIUMBLOB | YES | 向量嵌入二进制（float32 紧凑存储） |
| `vector_json` | MEDIUMTEXT | YES | 向量嵌入 JSON（便于调试/人工读取） |
| `vector_norm` | DOUBLE | YES | 向量 L2 范数（=1 表示已归一化，可用点积） |
| `embed_dim` | INT | NO | 向量维度（0=未构建向量） |
| `embed_model` | VARCHAR(128) | NO | 生成向量的嵌入模型名称（如 nomic-embed-text） |
| `chapter_hash` | VARCHAR(32) | NO | 段落内容哈希（MD5前16位，增量重建时比对：相同则保留旧向量） |
| `is_active` | TINYINT | NO | 是否有效（0=已被新版本取代，1=当前有效） |
| `source_version_label` | VARCHAR(128) | NO | 最后构建版本标签（审计） |
| `deactivated_version_label` | VARCHAR(128) | NO | 失效版本标签（审计） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `user_id` | VARCHAR(64) | NO | 用户ID（空=历史数据/共享） |
| `slice_id` | VARCHAR(128) | NO | 来源切片ID → requirement_slices.slice_id |

### `run_logs`　—　流水线运行日志表 — 存储每次执行的详细日志（取代磁盘 *.log 文件）

定义行 696，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 日志ID |
| `run_id` | VARCHAR(64) | NO | 运行ID（关联 pipeline_checkpoints.run_id） |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | INT | NO | 版本ID → project_versions.id（ 2026-08-14 补版本隔离） |
| `step_num` | INT | NO | 流水线阶段 (0=全流程) |
| `log_level` | VARCHAR(16) | NO | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `message` | MEDIUMTEXT | NO | 日志内容 |
| `created_at` | DATETIME | NO | 创建时间 |

### `qa_feedback`　—　QA 反馈表 — 记录项目反馈和全局反馈模式（取代磁盘 JSONL）

定义行 726，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `scope_type` | VARCHAR(32) | NO | 反馈范围类型（project/global） |
| `scope_id` | VARCHAR(128) | NO | 范围ID（project为项目名，global为空） |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `qa_decision` | VARCHAR(32) | NO | QA 判定: correct / false_positive / false_negative / needs_review |
| `qa_notes` | TEXT | NO | QA 备注 |
| `created_at` | DATETIME | NO | 创建时间 |

### `test_rules`　—　测试规则表 — 存储全局默认规则和项目覆盖规则；自动发现规则每项目最多5000条

定义行 742，主键 `scope_type,scope_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `scope_type` | VARCHAR(32) | NO | 规则范围类型（global/project） |
| `scope_id` | VARCHAR(128) | NO | 范围ID（global为空，project为项目名） |
| `rules_json` | JSON | NO | 规则定义 JSON（patterns + global_constraints） |
| `updated_at` | DATETIME | NO | 更新时间 |

### `requirements`　—　需求列表表 — 功能清单驱动拆解，存储原子需求三元组

定义行 754，主键 `project,version_id,req_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `req_id` | VARCHAR(128) | NO | 需求编号 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `stable_req_key` | VARCHAR(255) | NO | 跨版本稳定需求键 |
| `source_anchor` | VARCHAR(512) | NO | 源文档锚点（如 Confluence 页面标题层级路径） |
| `content_hash` | VARCHAR(64) | NO | 规范化文本哈希 |
| `semantic_hash` | VARCHAR(64) | NO | 语义哈希 |
| `change_type` | VARCHAR(32) | NO | added/modified/unchanged/removed/impacted/unknown |
| `origin_req_id` | VARCHAR(128) | NO | 来源版本需求编号 |
| `origin_version_id` | BIGINT | NO | 来源项目版本ID，0=无 |
| `feature_id` | BIGINT | NO | 关联功能清单ID → system_feature_list.id |
| `module` | VARCHAR(128) | NO | 功能模块 |
| `description` | TEXT | NO | 需求描述 |
| `operation` | TEXT | NO | 操作描述（三元组：用户执行了什么操作） |
| `condition_expr` | TEXT | NO | 条件描述（三元组：在什么条件下触发） |
| `expected_feedback` | TEXT | NO | 可观察反馈（三元组：系统如何响应） |
| `is_implicit` | TINYINT(1) | NO | 是否隐含需求（需求文档未明确描述，标记待确认） |
| `is_generated` | TINYINT(1) | NO | 是否已建模测试用例 |
| `dependency_req_id` | VARCHAR(255) | NO | 依赖需求编号 |
| `review_status` | VARCHAR(32) | NO | 审核状态 |
| `payload_json` | JSON | NO | 完整需求原始JSON |
| `contains_sensitive_data` | TINYINT(1) | NO | S2-018 是否包含敏感信息（身份证/手机号/邮箱/IP） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `slice_id` | VARCHAR(128) | NO | 来源切片ID → requirement_slices.slice_id |
| `source_quote` | TEXT | NO | 需求原文引用（防幻觉5a子串匹配事实源，必须来自所属切片真实文本） |
| `_verify_status` | VARCHAR(32) | NO | source_verify 校验状态: passed/passed_corrected/partial_match/unverified/blocked/no… |
| `_evidence_confidence` | DOUBLE | NO | 证据置信度评分 0.0~1.0 |

### `test_cases`　—　测试用例表 — v3.0 用例驱动，核心数据见payload_json（actions + checks）

定义行 795，主键 `project,version_id,case_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `case_id` | VARCHAR(128) | NO | 测试用例编号 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `origin_case_id` | VARCHAR(128) | NO | 来源版本测试用例编号 |
| `origin_version_id` | BIGINT | NO | 来源项目版本ID，0=无 |
| `case_source` | VARCHAR(32) | NO | generated/inherited/regenerated/manually_added |
| `valid_status` | VARCHAR(32) | NO | valid/stale/retired |
| `req_id` | VARCHAR(128) | NO | 关联需求编号 |
| `case_asset_file` | VARCHAR(256) | NO | 来源文件名 |
| `title` | TEXT | NO | 测试用例标题 |
| `case_type` | VARCHAR(32) | NO | 测试用例类型 |
| `risk_level` | VARCHAR(32) | NO | 风险等级 |
| `case_actions_json` | TEXT | NO | 用例动作JSON（v3.0: 从 actions[] 序列化）；原始用例见payload_json（actions + checks） |
| `expected_outcome` | TEXT | NO | 预期结果（从 checks[].expect 提取） |
| `test_method` | VARCHAR(32) | NO | 测试方法论编码(07-05h): EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI，与scenario_type正交 |
| `control_type` | VARCHAR(32) | NO | 控件类型(07-05h): input/select/button/upload/display/nav/interactive/composite |
| `priority` | VARCHAR(32) | NO | 风险驱动优先级(08-16h): critical/high/medium/low，由 derive_priority 推导 |
| `payload_json` | JSON | NO | 完整测试用例原始JSON |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `precondition` | TEXT | YES | 前置条件(2026-08-09 IEEE 829): 编号列表，如"1.系统已登录 2.测试数据已准备" |
| `test_steps` | TEXT | YES | 测试步骤(2026-08-09 IEEE 829): JSON数组，描述操作步骤序列 |
| `expected_result` | TEXT | YES | 预期结果(2026-08-09 IEEE 829): 精确描述操作后的预期状态 |

### `case_requirement_mapping`　—　测试用例-需求映射表

定义行 827，主键 `project,version_id,case_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `case_id` | VARCHAR(128) | NO | 测试用例ID |
| `req_id` | VARCHAR(128) | NO | 需求ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID |
| `req_version_id` | BIGINT | NO | 需求版本ID |
| `case_version_id` | BIGINT | NO | 测试用例版本ID |
| `mapping_source` | VARCHAR(32) | NO | 映射来源 |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `review_log`　—　审核操作日志表 — 记录谁在什么时间对什么做了什么审核动作

定义行 847，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 日志ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `item_type` | VARCHAR(255) | NO | 审核对象类型（case / requirement） |
| `item_id` | VARCHAR(255) | NO | 审核对象ID |
| `action` | VARCHAR(255) | NO | 审核动作（approve / reject / modify） |
| `reviewer` | VARCHAR(255) | NO | 审核人用户ID |
| `comment` | TEXT | NO | 审核备注 |
| `created_at` | DATETIME | NO | 创建时间 |

### `source_hashes`　—　源数据统一哈希表 — Step1/Step2 各数据源的总体内容哈希（断点续跑锚点）

定义行 867，主键 `project,doc_type,version_label`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `doc_type` | VARCHAR(32) | NO | 文档类型: confluence / yapi / breakdown |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `content_hash` | VARCHAR(64) | NO | 内容MD5哈希 |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `source_confluence_hashes`　—　Confluence 源数据哈希表 — Step1断点续跑用

定义行 880，主键 `project,version_label,hash_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `hash_key` | VARCHAR(255) | NO | 哈希键（__overall__ 或 page:<page_id>） |
| `hash_value` | VARCHAR(255) | NO | 哈希值 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `source_yapi_hashes`　—　YApi 源数据哈希表 — Step1断点续跑用

定义行 890，主键 `project,version_label`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `overall_hash` | TEXT | NO | 整体哈希值 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `source_breakdown_hashes`　—　Step2 需求拆解哈希表 — 断点续跑用

定义行 899，主键 `project,version_label,hash_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `hash_key` | VARCHAR(255) | NO | 哈希键（__overall__ 或 page:<page_id>） |
| `hash_value` | VARCHAR(255) | NO | 哈希值 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `meta_json` | TEXT | YES | 元数据JSON |

### `coverage_results`　—　覆盖率结果表 — 每个需求在各维度的覆盖状态

定义行 914，主键 `project,run_id,req_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `run_id` | VARCHAR(64) | NO | 运行批次ID |
| `req_id` | VARCHAR(128) | NO | 需求编号 |
| `dim_positive` | VARCHAR(32) | NO | 正向覆盖状态 |
| `dim_negative` | VARCHAR(32) | NO | 异常覆盖状态 |
| `dim_boundary` | VARCHAR(32) | NO | 边界值覆盖状态 |
| `dim_security` | VARCHAR(32) | NO | 安全覆盖状态 |
| `dim_api_check` | VARCHAR(32) | NO | API轨校验覆盖状态 |
| `dim_api_coverage` | VARCHAR(32) | NO | API接口覆盖状态 |
| `dim_db_coverage` | VARCHAR(32) | NO | DB表覆盖状态 |
| `dim_page_coverage` | VARCHAR(32) | NO | 页面覆盖状态 |
| `dim_trace_complete` | VARCHAR(32) | NO | 追溯链路完整性 |
| `total_cases` | INT | NO | 该需求关联测试用例数 |
| `has_gaps` | INT | NO | 是否存在缺口(0/1) |
| `gap_dims_json` | JSON | NO | 缺口维度列表JSON |
| `payload_json` | JSON | NO | 完整覆盖结果原始JSON |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `coverage_summaries`　—　覆盖率汇总表 — 每次运行的覆盖率总体统计

定义行 940，主键 `project,run_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `run_id` | VARCHAR(255) | NO | 运行批次ID |
| `total_requirements` | INT | NO | 总需求数 |
| `total_cases` | INT | NO | 总测试用例数 |
| `requirements_with_gaps` | INT | NO | 有缺口的需求数 |
| `coverage_rate` | DOUBLE | NO | 覆盖率(0.0-1.0) |
| `total_gaps` | INT | NO | 总缺口数 |
| `missing_by_dim_json` | JSON | NO | 按维度统计缺口JSON |
| `payload_json` | JSON | NO | 完整汇总原始JSON |
| `created_at` | DATETIME | NO | 创建时间 |

### `rag_usage_log`　—　RAG 使用日志 — 记录 RAG 检索结果到用例字段的映射

定义行 960，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | INT AUTO_INCREMENT | NO | 主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `case_id` | VARCHAR(64) | NO | 用例 ID → test_cases.case_id |
| `check_id` | VARCHAR(64) | NO | 检查点 ID |
| `req_id` | VARCHAR(128) | NO | 需求编号（用于 RAG 检索时建立映射） |
| `rag_source_type` | ENUM('DB_SCHEMA','API_DOC','FIGMA_PAGE') | NO | RAG 来源类型 |
| `rag_source_id` | VARCHAR(256) | NO | RAG 来源标识（表名/API路径/页面ID） |
| `field_used` | VARCHAR(256) | NO | 实际使用的字段（表名/API路径等） |
| `created_at` | DATETIME | NO | 创建时间 |

### `diff_snapshots`　—　版本差异快照表 — 存储各版本的需求/API/DB/测试用例快照

定义行 982，主键 `project,version_label,snapshot_type`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `snapshot_type` | VARCHAR(255) | NO | 快照类型: requirements/api_schema/db_schema/test_cases/cross_dependencies |
| `payload_json` | LONGTEXT | NO | 快照内容JSON |
| `item_count` | INT | NO | 元素个数 |
| `created_at` | DATETIME | NO | 创建时间 |

### `trace_links`　—　追溯链路表 — 需求级元数据（资源详情走 trace_link_chunks + rag_chunks）

定义行 997，主键 `req_id,project,version_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `req_id` | VARCHAR(128) | NO | 需求编号 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `feature_id` | BIGINT | NO | 关联功能清单ID → system_feature_list.id |
| `module` | VARCHAR(255) | NO | 功能模块 |
| `description` | LONGTEXT | NO | 需求描述 |
| `related_reqs_json` | JSON | NO | 关联需求JSON |
| `confidence` | VARCHAR(32) | NO | 置信度: high/medium/low |
| `payload_json` | JSON | NO | 链路元数据快照（不含API/DB/Figma资源详情） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `requirement_slices`　—　需求切片表 — 持久化智能切片结果，支持关系链路追溯到切片级别

定义行 1017，主键 `project,version_id,slice_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `slice_id` | VARCHAR(128) | NO | 切片唯一ID（如 req_raw:0 / req_raw:pre:1 / req_raw:1:2） |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id |
| `version_label` | VARCHAR(128) | NO | 版本标签 |
| `doc_type` | VARCHAR(64) | NO | 来源文档类型（requirement/upload_*） |
| `doc_anchor` | VARCHAR(512) | NO | 来源文档锚点（章节路径） |
| `section` | VARCHAR(255) | NO | 章节标题（H2/H3路径，空表示无章节结构） |
| `slice_index` | INT | NO | 切片序号（0-based，按原文顺序） |
| `text` | MEDIUMTEXT | NO | 切片原文（≤600字，智能切片保证：基础≤300字，跨模式合并后最大600字） |
| `char_count` | INT | NO | 字符数 |
| `needs_review` | TINYINT(1) | NO | 是否需人工审核（相似度合并触发） |
| `merge_reasons_json` | JSON | YES | 合并原因JSON（如["Jaccard相似度=0.18≥0.15"]） |
| `feature_label` | VARCHAR(255) | NO | 所属功能标签（Phase4聚类结果） |
| `prev_context` | TEXT | YES | 改进3(L3): 前文上下文（前一切片最后100字，供审核参考） |
| `next_context` | TEXT | YES | 改进3(L3): 后文上下文（后一切片前100字，供审核参考） |
| `created_at` | DATETIME | NO | 创建时间 |

### `trace_link_chunks`　—　追溯链路关联表 — 需求与RAG chunk的单一对多关联（消除资源数据分叉）

定义行 1067，主键 `req_id,project,version_id,chunk_type,chunk_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `req_id` | VARCHAR(128) | NO | 需求编号 → requirements.req_id |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id |
| `chunk_type` | VARCHAR(32) | NO | 资源类型: api / db_table / figma_page / dom / requirement / upload_* |
| `chunk_id` | VARCHAR(256) | NO | RAG chunk_id → rag_chunks.chunk_id |
| `rel_type` | VARCHAR(32) | NO | 关联方式: ai / heuristic / matched / implicit |
| `confidence` | VARCHAR(16) | NO | 置信度: high / medium / low |
| `created_at` | DATETIME | NO | 创建时间 |

### `action_categories`　—　通用操作指令分类表 — 全项目共享的浏览器操作分类目录

定义行 1094，主键 `category_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `category_id` | VARCHAR(128) | NO | 通用操作分类ID（如 mouse / form / keyboard） |
| `name` | VARCHAR(256) | NO | 分类名称 |
| `icon` | VARCHAR(64) | NO | 分类图标 |
| `sort_order` | INT | NO | 排序序号 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `action_actions`　—　通用操作指令详情表 — 全项目共享的浏览器操作定义

定义行 1103，主键 `category_id,action_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `category_id` | VARCHAR(128) | NO | 所属通用分类ID → action_categories.category_id |
| `action_id` | VARCHAR(128) | NO | 通用操作ID（如 click / fill / verify） |
| `label` | VARCHAR(256) | NO | 操作标签 |
| `description` | VARCHAR(1024) | NO | 操作描述 |
| `keywords_json` | LONGTEXT | NO | 关键词列表JSON |
| `params_json` | LONGTEXT | NO | 参数列表JSON |
| `needs_ai_id` | INT | NO | 是否需要AI定位元素 |
| `is_workflow` | INT | NO | 是否工作流 |
| `example` | LONGTEXT | NO | 示例 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `applicable_tags` | JSON | YES | 适用控件标签/role列表（如 ["input","textarea"]），Step3按控件过滤、Step4校验使用 |
| `total_count` | INT | NO | 调用总次数 |
| `success_count` | INT | NO | 调用成功次数 |
| `fail_count` | INT | NO | 调用失败次数 |
| `tool_name` | VARCHAR(80) | NO | 对应 browser_tools 工具名，action_dispatcher 直接查表执行 |

### `project_synonym_overrides`　—　项目同义词覆盖表 — 全局同义词库的项目级覆盖

定义行 1136，主键 `project,core_word`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `core_word` | VARCHAR(128) | NO | 核心词 → synonym_library.core_word |
| `synonym_group` | TEXT | NO | 项目覆盖同义词组JSON |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `project_meta`　—　项目元数据表 — 项目的显示名/描述/状态等辅助信息

定义行 1155，主键 `project`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `display_name` | VARCHAR(256) | NO | 显示名称 |
| `description` | VARCHAR(512) | NO | 项目描述 |
| `status` | VARCHAR(32) | NO | 状态: active/archived/deleted |
| `created_by` | VARCHAR(64) | NO | 创建者用户ID |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `user_bg_gallery`　—　用户背景图库表 — 每用户最多20张背景图，LONGBLOB存储

定义行 1171，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 图片ID |
| `user_id` | VARCHAR(100) | NO | 用户ID → users.id |
| `image_data` | LONGBLOB | NO | 图片二进制数据 |
| `mime_type` | VARCHAR(50) | NO | MIME类型（image/jpeg等） |
| `image_size` | INT | NO | 文件大小（字节） |
| `is_current` | TINYINT(1) | NO | 是否当前激活（每用户仅1张） |
| `created_at` | TIMESTAMP | YES | 创建时间 |

### `ai_generation_logs`　—　AI生成日志表 — 记录幻觉/环境等AI生成异常

定义行 1187，主键 `project,log_id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `log_id` | VARCHAR(255) | NO | 日志唯一ID |
| `case_id` | VARCHAR(255) | NO | 测试用例ID |
| `req_id` | VARCHAR(128) | NO | 需求ID |
| `module` | VARCHAR(255) | NO | 模块名 |
| `title` | TEXT | NO | 标题 |
| `failure_reason` | LONGTEXT | NO | 失败原因 |
| `classification` | VARCHAR(32) | NO | 分类: hallucination / env / unknown |
| `raw_steps_json` | LONGTEXT | NO | 原始用例行为JSON |
| `source` | VARCHAR(64) | NO | 来源 |
| `created_at` | DATETIME | NO | 创建时间 |

### `prompt_templates`　—　系统级 Prompt 模板表 — DB 唯一事实源，前端可编辑，is_seed=1 的种子模板可恢复默认

定义行 1208，主键 `name`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `name` | VARCHAR(128) | NO | 模板名称（主键） |
| `template_text` | MEDIUMTEXT | NO | Prompt 模板正文（动态模板含 {{占位符}}，render 时注入运行时上下文） |
| `category` | VARCHAR(64) | NO | 分类: Step2 需求拆解 / Step2 需求去重 / Step3 用例建模 / Step4 浏览器操作 / 质量校验 / 自定义 |
| `description` | VARCHAR(255) | NO | 模板用途描述 |
| `is_seed` | TINYINT | NO | 是否种子模板(1=系统内置不可删除，可恢复默认，0=用户自定义) |
| `enabled` | TINYINT | YES | 是否启用 |
| `updated_at` | TIMESTAMP | YES | 更新时间 |

### `synonym_library`　—　系统级同义词库 — 全局默认同义词；项目差异通过 project_synonym_overrides 覆盖

定义行 1219，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | INT AUTO_INCREMENT | YES | 自增主键 |
| `core_word` | VARCHAR(128) | NO | 核心词 |
| `synonym_group` | TEXT | NO | 同义词组（JSON 或分隔文本） |
| `created_at` | TIMESTAMP | YES | 创建时间 |

### `capability_cache`　—　Step4 项目级业务能力缓存表

定义行 1231，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `capability_key` | VARCHAR(64) | NO | 能力缓存主键，按项目、能力类型和业务意图生成 |
| `project` | VARCHAR(128) | NO | 所属项目名称；能力缓存是项目级资产 |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=跨版本复用 |
| `capability_name` | VARCHAR(128) | NO | 能力名称 |
| `case_normalized` | TEXT | NO | 归一化后的业务意图文本 |
| `preconditions_json` | LONGTEXT | YES | 能力适用前置条件 JSON |
| `inputs_schema_json` | LONGTEXT | YES | 测试数据约束或输入结构 JSON |
| `implementation_json` | LONGTEXT | YES | 最近一次成功运行的实现经验 JSON |
| `success_evidence_json` | LONGTEXT | YES | 最近一次成功证据 JSON |
| `success_count` | INT | NO | 成功沉淀次数 |
| `failure_count` | INT | NO | 失败次数 |
| `hit_count` | INT | NO | 复用命中次数 |
| `last_status` | VARCHAR(32) | YES | 最近状态：PASS/FAIL/INVALID |
| `confidence` | DOUBLE | YES | 最近一次能力置信度 |
| `is_valid` | TINYINT | NO | 是否仍可复用 |
| `page_signature_hash` | VARCHAR(64) | NO | 能力适用页面签名哈希 |
| `invalidation_reason` | TEXT | YES | 最近一次失效原因 |
| `invalidated_at` | DATETIME | YES | 最近一次失效时间 |
| `last_verified_at` | DATETIME | YES | 最近一次复用前校验时间 |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |
| `last_hit_at` | DATETIME | YES | 最近一次命中时间 |

### `capability_cache_events`　—　Step4 项目级能力缓存事件表

定义行 1259，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 事件自增主键 |
| `project` | VARCHAR(128) | NO | 所属项目名称 |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=跨版本复用 |
| `capability_key` | VARCHAR(64) | NO | 关联 capability_cache.capability_key |
| `capability_name` | VARCHAR(128) | NO | 能力名称 |
| `event_type` | VARCHAR(32) | NO | 事件类型：HIT/INVALIDATE/FAIL/PASS |
| `reason` | TEXT | YES | 事件原因或说明 |
| `old_signature_hash` | VARCHAR(64) | NO | 事件发生前的页面签名哈希 |
| `new_signature_hash` | VARCHAR(64) | NO | 事件发生时的页面签名哈希 |
| `context_json` | LONGTEXT | YES | 事件上下文 JSON |
| `created_at` | DATETIME | NO | 事件创建时间 |

### `capability_cache_policy`　—　Step4 项目级能力库治理策略表

定义行 1276，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `project` | VARCHAR(128) | NO | 所属项目名称；能力治理策略按项目隔离 |
| `max_capabilities` | INT | NO | 单项目能力缓存最大条目数 |
| `event_retention_days` | INT | NO | 能力事件保留天数 |
| `created_at` | DATETIME | NO | 策略创建时间 |
| `updated_at` | DATETIME | NO | 策略更新时间 |

### `run_summary`　—　Step5 项目运行级摘要表 — 记录每次流水线报告结果，用于趋势对比和历史质量分析

定义行 1288，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 所属项目名称；运行摘要按项目隔离 |
| `version_id` | VARCHAR(128) | NO | 项目版本标识 |
| `run_id` | VARCHAR(64) | NO | 流水线运行 ID |
| `user_id` | VARCHAR(64) | NO | 方案B: 执行用户ID，支持用户级隔离 |
| `report_version` | VARCHAR(64) | NO | 报告版本号 |
| `total_cases` | INT | NO | 本次报告测试用例总数 |
| `executed_cases` | INT | NO | 本次已测试用例数 |
| `total_passed` | INT | NO | 本次通过测试用例数 |
| `total_failed` | INT | NO | 本次失败测试用例数 |
| `total_skipped` | INT | NO | 本次跳过测试用例数 |
| `total_pending` | INT | NO | 本次待测试用例数 |
| `total_flaky` | INT | NO | 本次 Flaky 测试用例数 |
| `pass_rate` | DOUBLE | NO | 本次达成率，范围 0-1 |
| `avg_confidence` | DOUBLE | NO | 本次平均置信度，范围 0-1 |
| `low_confidence_count` | INT | NO | 低置信度测试用例数量 |
| `op_cache_hit_rate` | DOUBLE | NO | Step4 操作缓存命中率 |
| `capability_hit_rate` | DOUBLE | NO | Step4 能力库命中率 |
| `total_duration_sec` | DOUBLE | NO | 本次执行验证总耗时秒数 |
| `evidence_calibrated_confidence` | DOUBLE | NO | 证据引擎校准后的平均置信度（贝叶斯后验+独立验证） |
| `evidence_coverage` | DOUBLE | NO | 有独立证据验证的主张比例 |
| `evidence_cold_start` | TINYINT | NO | 是否冷启动（校准样本<阈值） |
| `summary_json` | LONGTEXT | YES | 运行摘要扩展 JSON |
| `created_at` | DATETIME | NO | 摘要创建时间 |
| `updated_at` | DATETIME | NO | 摘要更新时间 |
| `ai_loop_pass_rate` | DECIMAL(5,4) | YES | |

### `tool_execution_log`　—　融合架构工具执行日志表 — 记录每个tool_call的执行详情，用于链路追踪和性能分析

定义行 1333，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `case_id` | VARCHAR(128) | NO | 测试用例ID |
| `step_desc` | VARCHAR(512) | NO | 动作目标描述 |
| `trace_id` | VARCHAR(64) | NO | 链路追踪ID（同一批tool_call共享） |
| `tool_name` | VARCHAR(64) | NO | 工具名（如browser_click） |
| `tool_args_json` | JSON | YES | 工具参数JSON（摘要） |
| `result_ok` | TINYINT(1) | NO | 执行结果（1=成功，0=失败） |
| `result_content` | TEXT | YES | 执行结果内容或错误信息 |
| `elapsed_ms` | INT | NO | 执行耗时（毫秒） |
| `atomic_rollback` | TINYINT(1) | NO | 是否因原子性被跳过（1=是） |
| `tool_mode` | TINYINT(1) | NO | 是否来自原生工具调用模式（1=是，0=文本模式） |
| `model` | VARCHAR(64) | NO | 使用的AI模型名 |
| `created_at` | DATETIME | NO | 创建时间 |
| `version_id` | BIGINT | NO | 版本数字ID（版本隔离） |

### `system_config`　—　系统配置表 — 加密密钥、全局参数等

定义行 1457，主键 `key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `key` | VARCHAR(128) | NO | 配置键（如 encryption_key_v2） |
| `value` | TEXT | NO | 配置值 |
| `description` | VARCHAR(512) | NO | 配置说明 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `confidence_calibration`　—　置信度校准表 — 证据引擎：记录AI声称置信度 vs 实际执行通过率，驱动贝叶斯校准

定义行 1477，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `calibration_key` | VARCHAR(128) | NO | 校准维度: consensus_level / perspective_count / source_quote / requirement_type / r… |
| `calibration_value` | VARCHAR(128) | NO | 校准值（如 HIGH / 2 / yes / functional / P0 / 功能） |
| `total_predictions` | INT | NO | AI 声称高置信度的总次数 |
| `total_correct` | INT | NO | Step4 实际通过次数 |
| `accuracy` | DOUBLE | NO | 实际准确率 = total_correct / total_predictions |
| `last_updated` | DATETIME | NO | 最后更新时间 |

### `pipeline_user_config`　—　流水线用户级配置覆盖表 — 优先级高于环境变量和系统默认配置

定义行 1496，主键 ``。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | YES | 自增主键 |
| `user_id` | VARCHAR(128) | NO | 用户ID → users.id |
| `config_key` | VARCHAR(128) | NO | 配置键名（如 ai_model / op_cache_max_capacity） |
| `config_value` | TEXT | NO | 配置值（字符串存储，读取时按类型转换） |
| `source` | VARCHAR(16) | NO | 来源: manual=用户手动保存 / auto=切项目自动套用推荐值 |
| `updated_at` | DATETIME | YES | 更新时间 |

### `figma_pages`　—　Figma 页面事实表 — 顶层 FRAME/CANVAS，RAG 重建时读取

定义行 1510，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签（空字符串 = 全局基线） |
| `page_id` | VARCHAR(100) | NO | Figma 节点 ID |
| `name` | VARCHAR(500) | NO | 页面名称 |
| `canvas` | VARCHAR(500) | NO | 所属画布名 |
| `page_type` | VARCHAR(50) | NO | 页面类型: FRAME / COMPONENT_SET |
| `ui_structure` | MEDIUMTEXT | YES | parse_figma_document() 输出的 UI 结构描述 |
| `element_count` | INT | NO | 页面内交互元素数量 |
| `created_at` | DATETIME | NO | 首次入库时间 |
| `updated_at` | DATETIME | NO | 最后更新时间 |

### `figma_nodes`　—　Figma 节点事实表 — 页面内的交互元素，RAG 重建时读取

定义行 1529，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签（空字符串 = 全局基线） |
| `node_id` | VARCHAR(100) | NO | Figma 节点 ID |
| `page_id` | VARCHAR(100) | NO | 所属页面 ID → figma_pages.page_id |
| `parent_node_id` | VARCHAR(100) | NO | 父节点 ID |
| `name` | VARCHAR(500) | NO | 节点名称 |
| `node_type` | VARCHAR(50) | NO | 节点类型: TEXT / INSTANCE / FRAME / COMPONENT / ... |
| `text_content` | TEXT | YES | 节点的文本内容（TEXT 节点的 characters） |
| `meta_json` | JSON | YES | variants / componentProperties 等元数据 |
| `created_at` | DATETIME | NO | 首次入库时间 |

### `figma_components`　—　Figma 组件事实表 — 可复用组件定义，RAG 重建时读取

定义行 1548，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 自增主键 |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签（空字符串 = 全局基线） |
| `component_id` | VARCHAR(100) | NO | Figma 组件 ID |
| `name` | VARCHAR(500) | NO | 组件名称 |
| `description` | TEXT | YES | 组件描述（name + variant 选项摘要） |
| `variant_options` | JSON | YES | componentPropertyDefinitions 的 variant 选项 |
| `created_at` | DATETIME | NO | 首次入库时间 |

### `pipeline_self_test_results`　—　流水线自检结果表 — 诊断中心存储每次自检的Step级结果

定义行 1623，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `run_id` | VARCHAR(64) | NO | 自检运行ID |
| `user_id` | VARCHAR(64) | NO | 执行用户ID |
| `steps_json` | JSON | NO | 各Step自检结果（status/detail/checks/duration_ms） |
| `overall` | VARCHAR(16) | NO | 总体状态: HEALTHY/DEGRADED/BROKEN |
| `passed` | INT | NO | 通过Step数 |
| `failed` | INT | NO | 失败Step数 |
| `skipped` | INT | NO | 跳过Step数 |
| `created_at` | DATETIME | NO | 创建时间 |

### `metric_thresholds`　—　指标阈值配置 — 通用告警体系

定义行 1714，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | |
| `metric_type` | VARCHAR(64) | NO | 指标类型（如 cdp_browser / ai_overall / fusion） |
| `metric_name` | VARCHAR(128) | NO | 指标名称（如 attach_count / pass_rate） |
| `field` | VARCHAR(64) | NO | fetcher 返回的字段名（如 attach_success_rate / pass_rate） |
| `warn_threshold` | DOUBLE | YES | 警告阈值 |
| `error_threshold` | DOUBLE | YES | 错误阈值 |
| `critical_threshold` | DOUBLE | YES | 严重阈值 |
| `compare_op` | VARCHAR(4) | NO | 比较运算符: >= / <= / == / > / < |
| `enabled` | TINYINT | NO | 是否启用 |
| `description` | VARCHAR(255) | NO | 阈值描述 |
| `created_at` | DATETIME | NO | |
| `updated_at` | DATETIME | NO | |

### `nav_graph_nodes`　—　真实爬取导航图节点（替 paper nav_map）

定义行 1745，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 节点自增ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 → project_versions.version_label |
| `page_url` | VARCHAR(1024) | NO | 完整页面URL（含 hash/query，Fix6 去重键） |
| `page_path` | VARCHAR(512) | NO | 页面路径（path 部分，仅兜底去重） |
| `page_title` | VARCHAR(512) | NO | 页面标题 |
| `element_count` | INT | NO | 该页可交互元素数 |
| `page_elements` | JSON | YES | P0-3b: 结构化页面元素（json: {buttons,inputs,links,selects,forms}），替 system_nav_maps.na… |
| `snapshot_id` | BIGINT | YES | P0-1 已废弃(2026-08-05): 原关联 page_dom_snapshots.id，该表已 DROP；写侧固定 NULL，勿再使用 |
| `source` | VARCHAR(32) | NO | 来源: dom / manual |
| `created_at` | DATETIME | NO | 创建时间 |

### `nav_graph_edges`　—　页面间转移边（状态转移，喂 op_cache）

定义行 1764，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 边自增ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_label` | VARCHAR(128) | NO | 版本标签 → project_versions.version_label |
| `from_node` | BIGINT | NO | 源节点 → nav_graph_nodes.id |
| `to_node` | BIGINT | NO | 目标节点 → nav_graph_nodes.id |
| `trigger_action` | JSON | YES | 触发动作（json: {type, selector, value}） |
| `via_tab` | VARCHAR(128) | NO | 经 Tab（如 叶组配方/掺配物配方） |
| `via_sidebar` | VARCHAR(128) | NO | 经侧边栏菜单路径 |
| `created_at` | DATETIME | NO | 创建时间 |

### `op_cache_v2`　—　真实原子指令序列缓存（替 0 行 op_cache，SOTA 三拍第①拍）

定义行 1787，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 缓存记录ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `intent_hash` | CHAR(64) | NO | 意图哈希（同意图复现键） |
| `page_fingerprint` | VARCHAR(64) | NO | 页面指纹（定位命中） |
| `action_sequence` | JSON | NO | 真实原子指令序列（json 数组） |
| `hit_count` | INT | NO | 命中次数（SOTA replay 统计） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | YES | 更新时间 |

### `calibration_samples`　—　主动学习样本（替休眠 calibration，解 85 常量根因）

定义行 1803，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 样本ID |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id |
| `case_id` | VARCHAR(128) | NO | 关联用例ID → test_cases.case_id |
| `predicted_verdict` | VARCHAR(32) | NO | 模型预测结论: PASS/FAIL |
| `human_verdict` | VARCHAR(32) | NO | 人工结论: PASS/FAIL |
| `features` | JSON | YES | 特征向量（json，供校准模型） |
| `created_at` | DATETIME | NO | 回流时间 |

### `golden_set`　—　引擎自评估黄金集

定义行 1817，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 条目ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `case_id` | VARCHAR(128) | NO | P2-12接入: 关联用例ID → test_cases.case_id（空=按scenario匹配） |
| `scenario` | VARCHAR(512) | NO | 场景描述 |
| `expected_verdict` | VARCHAR(32) | NO | 期望结论: PASS/FAIL |
| `expected_confidence` | DECIMAL(5,4) | NO | 期望置信度 0~1 |
| `notes` | TEXT | YES | 备注 |
| `created_at` | DATETIME | NO | 创建时间 |

### `oracle_results`　—　每次执行的三种 oracle 结论

定义行 1836，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 结果ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `version_id` | BIGINT | NO | 版本ID → project_versions.id |
| `execution_id` | BIGINT | NO | 关联 executions.id |
| `oracle_type` | VARCHAR(32) | NO | oracle 类型: existence/correctness/completeness |
| `verdict` | VARCHAR(32) | NO | 结论: pass/fail/warn |
| `score` | DECIMAL(5,4) | NO | 得分 0~1 |
| `detail` | JSON | YES | 明细（json） |
| `created_at` | DATETIME | NO | 创建时间 |
| `case_id` | VARCHAR(64) | NO | 关联 executions.case_id — D6M 归属键 |
| `run_id` | VARCHAR(128) | NO | 关联 executions.run_id — 同 run 聚合键 |

### `visual_review`　—　VLM 视觉 oracle 复核会话

定义行 1852，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 复核会话ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `version_id` | BIGINT | NO | 版本ID |
| `execution_id` | BIGINT | NO | 关联 executions.id |
| `baseline_id` | BIGINT | YES | 关联 visual_baselines.id |
| `screenshot_path` | VARCHAR(1024) | NO | 截图路径 |
| `vlm_diff` | JSON | YES | VLM 差异区域（json: [{x,y,w,h,desc}]） |
| `human_confirm` | VARCHAR(32) | YES | 人工确认: match/mismatch/pending |
| `created_at` | DATETIME | NO | 创建时间 |

### `multimodal_chunks`　—　多模态 RAG 块（需求+DOM+figma）

定义行 1867，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 块ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `source_type` | VARCHAR(32) | NO | 来源类型: requirement/dom/figma |
| `ref_id` | VARCHAR(128) | NO | 来源对象ID |
| `content` | MEDIUMTEXT | NO | 块内容 |
| `embedding` | MEDIUMTEXT | YES | 向量（json 数组或外部向量库 ID） |
| `created_at` | DATETIME | NO | 创建时间 |

### `review_lane`　—　三车道路由（绿/黄/红），替自动 approved

定义行 1880，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 分流记录ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `version_id` | BIGINT | NO | 版本ID |
| `case_id` | VARCHAR(128) | NO | 关联用例ID → test_cases.case_id |
| `lane` | VARCHAR(16) | NO | 车道: green/yellow/red |
| `routed_to_human` | TINYINT(1) | NO | 是否送人审: 0/1 |
| `reason` | VARCHAR(512) | NO | 分流原因（锚点合取分说明） |
| `created_at` | DATETIME | NO | 创建时间 |

### `case_upload_history`　—　用例上传历史表

定义行 1918，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 上传记录ID |
| `filename` | VARCHAR(255) | NO | 上传文件名 |
| `target_key` | VARCHAR(128) | NO | 被测系统 key → target_cfgs 注册表 |
| `version` | VARCHAR(64) | NO | 被测系统版本 |
| `uploader` | VARCHAR(128) | NO | 上传人（用户ID） |
| `uploaded_at` | DATETIME | YES | 上传时间 |
| `matched` | TINYINT | NO | target+version 匹配是否通过 1=通过/0=拒绝 |
| `policy` | VARCHAR(16) | NO | 不匹配处置策略 reject/warn/force |
| `message` | TEXT | YES | 匹配校验消息 |
| `exec_summary` | TEXT | YES | 执行结果摘要（all_pass/cases/probes） |

### `clarification_sessions`　—　需求澄清会话表 — 每条高危歧义项一个会话，支持多轮问答与状态机

定义行 1942，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 会话自增ID |
| `session_id` | VARCHAR(64) | NO | 会话ID（如 CLR-<run_id>-<seq>） |
| `project` | VARCHAR(128) | NO | 项目名 → projects.name |
| `version_id` | VARCHAR(128) | NO | 项目版本ID（兼容字符串形态，对齐流水线上下文） |
| `run_id` | VARCHAR(64) | NO | 流水线运行ID（pipeline_checkpoints.run_id） |
| `req_id` | VARCHAR(128) | NO | 关联需求编号 → requirements.req_id |
| `ambiguity_item_id` | VARCHAR(64) | NO | 歧义项ID（ambiguity_report items[].ambiguity_id） |
| `ambiguity_type` | VARCHAR(64) | NO | 歧义类型（六类规则枚举） |
| `severity` | VARCHAR(16) | NO | 严重度 high/medium/low |
| `blocking` | TINYINT | NO | 是否阻断生成（high=1） |
| `status` | VARCHAR(16) | NO | 状态: open/answered/released/skipped |
| `clarify_questions` | JSON | NO | 澄清问题列表（json数组） |
| `backfill_json` | JSON | YES | 回填结果（json: 更新后的 operation/condition/expected_feedback） |
| `created_at` | DATETIME | NO | 创建时间 |
| `updated_at` | DATETIME | NO | 更新时间 |

### `clarification_answers`　—　需求澄清回答表 — 一次回答一行，多轮可追溯

定义行 1964，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 回答自增ID |
| `session_id` | VARCHAR(64) | NO | → clarification_sessions.session_id |
| `question_index` | INT | NO | 问题序号（从0开始） |
| `question` | VARCHAR(512) | NO | 问题原文快照 |
| `answer` | VARCHAR(1024) | NO | 用户回答 |
| `answered_by` | VARCHAR(128) | NO | 回答人（当前登录用户ID） |
| `answered_at` | DATETIME | NO | 回答时间 |

### `navigation_match_logs`

定义行 1995，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | |
| `project` | VARCHAR(64) | NO | |
| `version_id` | BIGINT | NO | 项目版本ID → project_versions.id，0=legacy |
| `case_id` | VARCHAR(128) | NO | |
| `req_text` | VARCHAR(512) | NO | |
| `match_level` | VARCHAR(16) | NO | |
| `llm_used` | TINYINT | NO | |
| `llm_elapsed_ms` | INT | NO | |
| `llm_success` | TINYINT | NO | |
| `llm_raw_response` | VARCHAR(512) | NO | |
| `llm_menu` | VARCHAR(128) | NO | |
| `menu_hit` | TINYINT | NO | |
| `calibration_status` | VARCHAR(16) | NO | |
| `corrected_menu` | VARCHAR(128) | NO | |
| `calibrated_by` | VARCHAR(64) | NO | |
| `calibrated_at` | DATETIME | YES | |
| `created_at` | DATETIME | NO | |

### `plugin_interventions`　—　插件干预记录 — 各钩子返回的实质性干预结果持久化

定义行 2018，主键 `id`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `id` | BIGINT AUTO_INCREMENT | NO | 记录ID |
| `project` | VARCHAR(128) | NO | 项目名 |
| `case_id` | VARCHAR(64) | NO | 测试用例编号 |
| `intent_id` | VARCHAR(64) | NO | 意图ID |
| `plugin_name` | VARCHAR(64) | NO | 插件名 |
| `hook` | VARCHAR(32) | NO | 钩子类型 |
| `intervention_data` | TEXT | NO | 干预结果JSON |
| `created_at` | DATETIME | NO | 记录时间 |

### `user_preferences`

定义行 2032，主键 `user_id,pref_key`。

| 列 | 类型 | 允许空 | 说明 |
|:---|:---|:---:|:---|
| `user_id` | VARCHAR(128) | NO | |
| `pref_key` | VARCHAR(128) | NO | |
| `pref_value` | TEXT | NO | |
| `updated_at` | DATETIME | YES | |
