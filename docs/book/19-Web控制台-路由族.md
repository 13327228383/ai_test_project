# 第 19 章　Web 控制台 · 路由族

## 概述

控制台后端在 `src/backend/routes/` 下划分为 25 个功能子域，各自以 Blueprint 形式注册（装配见第 18 章 `app.py:357`）。端点路径由脚本实测提取；`clarification`、`diagnostics`、`kpi`、`quality` 四域经 Blueprint `url_prefix` 挂载，本章已拼出完整路径。本章每个子域给出端点清单、作用与关键实现文件。实测 25 子域共 **245 个注册点**。

关于计数口径需要说明一点：`src/backend/routes/data_datasources/__init__.py` 中的接口采用**直接调用形态**注册（形如 `data_bp.route("...")(func)`，见 `:110`），而不是装饰器形态。只统计 `@bp.route(...)` 装饰器会漏掉这一批共 22 个注册点。本节的子域计数已把两种形态一并计入。整个 `src/backend/` 的注册点合计 342 个，分布见附录 C。

## auth（auth_bp，无前缀，key 文件 `routes/auth/`）

鉴权、用户、角色、权限、凭证管理。共 38 个端点：

- `/api/login` POST、`/api/logout` POST、`/api/current-user`（登录态，`_login.py`）
- `/api/register` POST、`/api/users` GET/POST、`/api/users/<user_id>` PUT/DELETE、`/api/user-projects/<user_id>`、`/api/my-manageable-projects`、`/api/executions`（`_user.py`）
- `/api/key` POST/DELETE、`/api/ollama-models`、`/api/disk-embed-models`（`_key.py`）
- `/api/user-credentials` GET/POST/DELETE、`/api/user-credentials/copy` POST、`/api/user-credential-projects`、`/api/can-access`（`_credential.py`）
- `/api/roles` GET/POST、`/api/roles/<role_id>` PUT/DELETE、`/api/roles/<role_id>/permissions` GET/PUT（`_role.py`）
- `/api/users/<user_id>/roles` GET/PUT、`/api/users/<user_id>/projects` GET/PUT（`_role.py`）
- `/api/projects/<project>/members` GET/POST、`/api/projects/<project>/members/<user_id>` DELETE（`_role.py`）
- `/api/permissions`、`/api/permissions/role/<role>` GET/PUT、`/api/users/<user_id>/role` PUT（`_permission.py`）

## case_review（routes/case_review，挂 exec_bp）

用例审核与人审车道。共 11 个：

- `/api/cases` GET（列表）、`/api/cases/review` GET、`/api/cases/<case_id>` DELETE
- `/api/cases/review/approve` POST、`/api/cases/review/reject` POST、`/api/cases/review/edit` POST（单条审核）
- `/api/cases/review/batch-approve` POST（green 车道批量放行）、`/api/cases/review/confirm` POST、`/api/cases/review/state` GET
- `/api/review/lane` GET、`/api/review/lane/confirm` POST（逐条人审，`_routes.py`）

## case_rules（case_rules_bp）

用例生成规则只读查阅。共 5 个：

- `/api/case-generation-rules`、`/api/step1-rag-rules`、`/api/step2-breakdown-rules`、`/api/step4-verification-rules`、`/api/step5-report-rules`（`_routes.py`）

## case_upload（case_upload_bp）

Step3 用例上传绑定。共 5 个：

- `/api/step3/case/template` GET、`/api/step3/case/validate` POST、`/api/step3/case/upload` POST、`/api/step3/case/current-target` GET、`/api/step3/case/uploads` GET（`_template.py`/`_validate.py`/`_upload.py`/`_history.py`）

## clarification（url_prefix `/api/step2/clarify`）

交互式需求澄清问答。共 6 个：

- `/api/step2/clarify/sessions` GET、`/api/step2/clarify/sessions/<session_id>` GET、`/api/step2/clarify/questions` GET、`/api/step2/clarify/answers` POST、`/api/step2/clarify/release` POST、`/api/step2/clarify/gate` GET（`_routes.py`）

## data_datasources（data_bp，源自 `routes/data_datasources`）

需求数据源与背景图。共 22 个：

- `/api/requirements`、`/api/requirements/review` POST、`/api/requirements/review/confirm` POST
- `/api/upload-bg` POST、`/api/bg-image`、`/api/bg-image/<gid>`、`/api/bg-gallery`、`/api/bg-gallery/<gid>` DELETE、`/api/bg-gallery/activate` POST、`/api/bg-config` GET/POST、`/api/input-style` GET/POST
- `/api/figma-quota`、`/api/figma-refresh` POST、`/api/figma-retry-failed` POST、`/api/figma-images`、`/api/figma-image/<image_id>`、`/api/yapi-stats`、`/api/yapi-refresh` POST、`/api/confluence-stats`、`/api/confluence-refresh` POST、`/api/db-stats`、`/api/db-test` POST（`__init__.py`）

## data_models（data_models_bp）

AI 模型选择。共 11 个：

- `/api/ai-defaults`、`/api/task-models`、`/api/verification-models`、`/api/vision-models`、`/api/preview-models` POST、`/api/models/validate` POST
- `/api/data-models/<model_id>` GET/DELETE、`/api/task-model` POST、`/api/verification-model` POST、`/api/vision-model` POST（`_list.py`/`_detail.py`/`_create.py`/`_delete.py`）

## diagnostics（url_prefix `/api/diagnostics`）

诊断中心。共 6 个：

- `/api/diagnostics/self-test` POST、`/api/diagnostics/self-test/latest` GET、`/api/diagnostics/self-test/history` GET、`/api/diagnostics/health` GET、`/api/diagnostics/failures` GET、`/api/diagnostics/health/runs` GET（`diagnostics_api.py`）

## docs（docs_bp）

文档与可追溯性。共 32 个：

- `/api/docs/traceability`、`/api/docs/traceability/coverage`、`/api/docs/traceability/matrix`、`/api/docs/tool-execution-log`、`/api/docs/diff/report`、`/api/docs/diff/impact`、`/api/docs/rag/stats`、`/api/docs/coverage`、`/api/docs/execution-result`、`/api/docs/execution-stats`、`/api/docs/versions`、`/api/docs/version/<version_id>`、`/api/docs/requirement` GET/POST、`/api/docs/requirement/ai-supplement` POST、`/api/docs/breakdown`、`/api/docs/feature-list`、`/api/docs/xmind-design` POST、`/api/docs/rag/rebuild` POST（`docs_read_*`、`docs_write_*`）
- `/api/report-dir`、`/api/docs/figma-stats`、`/api/docs/intent-asset-files`、`/api/docs/intent-asset-file/<name>`、`/api/docs/executors`（`docs_read_downloads_*`）
- `/api/docs/smoke-intent-files`、`/api/docs/generate-smoke` POST、`/api/docs/generate-smoke/status`、`/api/docs/smoke-intents`、`/api/docs/smoke-intents/clear` POST（`docs_smoke.py`）
- `/api/trace/req/<req_id>`、`/api/trace/case/<case_id>`、`/api/trace/slice/<slice_id>`（`docs_trace_routes.py`）

## docs_analytics（docs_analytics_bp）

文档存储与分析。共 9 个：

- `/api/docs/save` POST、`/api/docs/file`、`/api/docs/preview`、`/api/docs/storage-stats`、`/api/docs/files`、`/api/docs/file-list`、`/api/docs/delete` POST、`/api/docs/batch-delete` POST、`/api/docs/search`（`routes/_save.py`/`_preview.py`/`_storage.py`/`_file_tree.py`/`_search.py`）

## execution（routes/execution，不含其下独立子域）

流水线执行控制。共 38 个：

- `/api/status`、`/api/version-check/confirm` POST、`/api/version-check/cancel` POST（`status.py`）
- `/api/config/flags` GET、`/api/config/toggle` POST（`config.py`）
- `/api/client-browser/startup-script` GET、`/api/client-browser/check` POST、`/api/client-browser/detect-ip` GET（`client_browser.py`）
- `/api/pipeline-progress` GET、`/api/report-content/<int:report_id>` GET、`/api/source-doc-content/<doc_type>` GET（`pipeline_progress.py`）
- `/api/deployment/docs` GET、`/api/deployment/versions` POST、`/api/last-run`、`/api/deployment/preview` POST（`deployment.py`）
- `/api/review/approve` POST、`/api/review/reject` POST（`review.py`）
- `/api/preflight` GET、`/api/preflight/verify` POST、`/api/target-system-status` GET（`preflight.py`）
- `/api/execution-tracks` GET、`/api/pipeline-trace/<int:step>/<id>` GET（`_pipeline_trace.py`）
- `/api/browser-parallel-recommendation` GET、`/api/step4-config` GET、`/api/step4/run-options` GET/POST（`__init__.py`）
- `/api/stop` POST、`/api/stop/<run_id>` POST（`run/_stop.py`）、`/api/pause` POST、`/api/resume` POST（`run/_status.py`）
- `/api/coverage-check` GET、`/api/coverage/auto-fill` POST、`/api/step/<int:n>` POST、`/api/step4-run` POST、`/api/step4-run-single` POST、`/api/run-all` POST、`/api/retry-failed` POST、`/api/retry-failed-with-regen` POST（`run/_start.py`）

## file_cleanup（routes/file_cleanup，挂 exec_bp）

文件清理。共 3 个：

- `/api/clearable-files` GET、`/api/clear-files` POST、`/api/clearable-files/children` GET（`_list.py`/`_clear.py`/`_children.py`）

## file_upload（file_upload_bp）

Step1 文件上传。共 4 个：

- `/api/step1/upload` POST、`/api/step1/uploads` GET、`/api/step1/uploads/<file_hash>` DELETE、`/api/step1/uploads/<file_hash>/weight` PUT（`_upload.py`/`_list.py`/`_delete.py`）

## health（health_bp）

健康看板。共 7 个：

- `/api/health/trends`、`/api/health/latest-run`（`routes_trends.py`）、`/api/health/summary`（`routes_summary.py`）、`/api/health/detailed`、`/api/health/metrics`、`/healthz`、`/readyz`（`routes_diagnostics.py`）

## kpi（url_prefix `/api/kpi`）

统一 KPI 聚合。共 2 个：

- `/api/kpi/overview` GET、`/api/kpi/trend` GET（`_routes.py`）

## metrics_page（metrics_page_bp）

指标中心页数据。共 2 个：

- `/api/metrics-summary`、`/api/test-methods` GET（`metrics_page.py`）

## nav_match（routes/nav_match，挂 exec_bp）

导航匹配可观测。共 3 个：

- `/api/nav-match/logs` GET、`/api/nav-match/stats` GET、`/api/nav-match/calibrate` POST（`_routes.py`）

## oracle（routes/oracle，挂 exec_bp）

视觉 Oracle。共 2 个：

- `/api/visual-review` POST、`/api/visual-review/status` GET（`_routes.py`）

## pipeline_config（pipeline_cfg_bp）

用户流水线配置。共 7 个：

- `/api/user-pipeline-config` GET/PUT/DELETE、`/api/pipeline-config/recommended` GET、`/api/pipeline-config/apply-recommended` POST、`/api/user-preference/<key>` GET/PUT（`_routes.py`）

## project_config（project_config_bp，经 project 门面注册）

项目配置读写。共 5 个：

- `/api/config` GET/POST、`/api/config/history`、`/api/config/rollback` POST、`/api/proxy/test-connection` POST（`project_config.py`）

## project_core（project_core_bp，经 project 门面注册）

项目 CRUD/切换/归档。共 7 个：

- `/api/projects`、`/api/new-project` POST、`/api/project` POST（切换，读 `name` 或别名 `project`）、`/api/project/meta` GET/POST、`/api/project/<name>` DELETE、`/api/project/<name>/restore` POST（`_project.py`/`_meta.py`/`_archive.py`）

## quality（url_prefix `/api/quality`）

W3 质量治理只读聚合。共 3 个：

- `/api/quality/w3-summary` GET、`/api/quality/methodology-validation` GET、`/api/quality/semantic-merge/latest` GET（`quality_routes.py`）

## action_library（action_library_bp）

动作库 CRUD/导入导出。共 7 个：

- `/api/action-library`、`/api/action-library/action` POST、`/api/action-library/action/<action_id>` DELETE、`/api/action-library/search`、`/api/action-library/reset` POST、`/api/action-library/export` GET/POST、`/api/action-library/import` POST（`routes/_crud.py`/`_export.py`）

## calibration（routes/calibration，挂 exec_bp）

校准环。共 1 个：

- `/api/calibration/summary` GET（`_routes.py`）

## routes（routes/routes，杂项蓝图）

指标与插件。共 9 个：

- `/api/stats`、`/api/figma`、`/api/intent-metrics`、`/api/run-summary-latest`、`/api/step1-status`（`_misc.py`）
- `/api/token-stats`、`/api/token-dashboard`、`/api/token-phase-breakdown`（`_token.py`）
- `/api/plugin-metrics`（`_plugin.py`）

## 设计取舍

- 多个审核/可观测子域（case_review、calibration、nav_match、oracle、file_cleanup）直接挂 `exec_bp` 而非独立蓝图，路径前缀与执行域混用；好处是共享执行上下文，代价是子域边界在 URL 上不直观。
- `project.py` 为门面 Blueprint，内部 `register_blueprint(project_core_bp)`、`project_config_bp`，故 `project_core`/`project_config` 仅经门面注册一次（见 `app.py:357` 仅注册 `project_bp`），无重复挂载。
- 除 25 子域外，`app.py` 还注册了 `version_bp`、`chart_bp`、`export_bp`、`ci_bp`、`synonym_bp`、`overrides_bp`、`pipeline_plugins_bp`、`p0_plugins_bp`、`system_cfg_bp` 共 9 个蓝图（约 43 个端点），未列入本契约 25 子域清单，需核实归属或补齐契约目录。

## 本章代码依据

- 端点清单由 Grep `*.route(` 在 `src/backend/routes/` 提取（含 `methods=`）
- Blueprint 定义与 `url_prefix`：`routes/diagnostics/diagnostics_api.py:15`、`routes/clarification/_routes.py:26`、`routes/kpi/_routes.py:21`、`routes/quality/quality_routes.py:48`
- 门面装配：`routes/project.py:34`（`project_bp.register_blueprint`）
- 注册顺序：`src/backend/app.py:357`

## 待核实

- ~~`version_bp` 归入哪个子域~~ → **已核实（2026-10-15）：它是独立 Blueprint，不属于 25 子域中的任何一个。** `src/backend/routes/version.py:31` 定义 `version_bp = Blueprint("version", __name__)`，`app.py:306` import、`:359` `_safe_register_blueprint(version_bp)`。端点含 `/api/project/versions`(GET/POST)、`/active`、`<id>/activate`、`/<id>`(PUT)、`/<id>/archive`、`/<id>/delete` 等。它是与 `project_core` **平级**注册的蓝图，不挂在其下，因此 25 子域统计不含它是正确口径，但目录表应补一行说明。
- ~~四个 Blueprint 的真实 `url_prefix`~~ → **已核实（2026-10-15）：源码内 `url_prefix` 均已显式声明，实测如下（无需运行期确认）。** `routes/clarification/_routes.py:26` → `/api/step2/clarify`；`routes/kpi/_routes.py:21` → `/api/kpi`；`routes/quality/quality_routes.py:48` → `/api/quality`；`routes/diagnostics/diagnostics_api.py:15` → `/api/diagnostics`。注意 **clarification 的前缀是 `/api/step2/clarify` 而不是 `/api/clarification`** —— 按 Blueprint 名猜前缀会猜错。四个声明都在 `Blueprint(...)` 构造参数里，是静态常量，运行期不再封装。
- `execution` 子域计数已剔除挂其下的 case_review/calibration/nav_match/oracle/file_cleanup；若统计口径改为「含挂载端点」则数值更高。
