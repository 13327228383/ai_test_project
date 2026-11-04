# 附录 C · HTTP 接口清单

本附录由脚本扫描 `src/backend/` 生成，覆盖 **两种注册形态**：装饰器形态（`@bp.route(...)` 置于函数之上）与直接调用形态（`bp.route(...)(func)`）。后者容易被只数装饰器的统计漏掉，本附录一并计入，共 **342 个接口注册点**。路径参数占位符保留原始写法。

## 按模块分布

| 模块 | 注册点 |
|:---|---:|
| `（app.py 内置）` | 56 |
| `auth` | 38 |
| `execution` | 38 |
| `docs` | 32 |
| `data_datasources` | 22 |
| `version.py` | 11 |
| `case_review` | 11 |
| `data_models` | 11 |
| `docs_analytics` | 9 |
| `routes` | 9 |
| `overrides_routes.py` | 8 |
| `export.py` | 7 |
| `action_library` | 7 |
| `health` | 7 |
| `pipeline_config` | 7 |
| `project_core` | 7 |
| `ci_integration.py` | 6 |
| `clarification` | 6 |
| `diagnostics` | 6 |
| `case_rules` | 5 |
| `case_upload` | 5 |
| `project_config` | 5 |
| `synonym_routes.py` | 4 |
| `file_upload` | 4 |
| `file_cleanup` | 3 |
| `nav_match` | 3 |
| `quality` | 3 |
| `system_config_api.py` | 2 |
| `kpi` | 2 |
| `metrics_page` | 2 |
| `oracle` | 2 |
| `chart_data.py` | 1 |
| `p0_plugins.py` | 1 |
| `pipeline_plugins.py` | 1 |
| `calibration` | 1 |
| **合计** | **342** |

## 接口明细

「声明前缀」取自同名 Blueprint 的 `url_prefix`；为空表示该蓝图未声明前缀，其实际前缀由注册处决定，或路径本身已是完整路径。

| 蓝图变量 | 声明前缀 | 路径 | 方法 | 定义文件 |
|:---|:---|:---|:---|:---|
| `app` | `—` | `/` | GET | `src/backend/app.py` |
| `app` | `—` | `/action-lib` | GET | `src/backend/app.py` |
| `app` | `—` | `/ai-health` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/audit-logs` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/log-history` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/log-history/<run_id>` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/log-runs` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/log-stream` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/run-id` | GET | `src/backend/app.py` |
| `app` | `—` | `/api/version` | GET | `src/backend/app.py` |
| `app` | `—` | `/audit-logs` | GET | `src/backend/app.py` |
| `app` | `—` | `/change-password` | GET | `src/backend/app.py` |
| `app` | `—` | `/charts` | GET | `src/backend/app.py` |
| `app` | `—` | `/ci-integration` | GET | `src/backend/app.py` |
| `app` | `—` | `/clarify` | GET | `src/backend/app.py` |
| `app` | `—` | `/clear-files` | GET | `src/backend/app.py` |
| `app` | `—` | `/confidence-block` | GET | `src/backend/app.py` |
| `app` | `—` | `/config` | GET | `src/backend/app.py` |
| `app` | `—` | `/config-history` | GET | `src/backend/app.py` |
| `app` | `—` | `/dashboard` | GET | `src/backend/app.py` |
| `app` | `—` | `/datasource` | GET | `src/backend/app.py` |
| `app` | `—` | `/diagnostics` | GET | `src/backend/app.py` |
| `app` | `—` | `/docs` | GET | `src/backend/app.py` |
| `app` | `—` | `/ds-api` | GET | `src/backend/app.py` |
| `app` | `—` | `/ds-database` | GET | `src/backend/app.py` |
| `app` | `—` | `/ds-design` | GET | `src/backend/app.py` |
| `app` | `—` | `/ds-requirements` | GET | `src/backend/app.py` |
| `app` | `—` | `/ds-target` | GET | `src/backend/app.py` |
| `app` | `—` | `/export-page` | GET | `src/backend/app.py` |
| `app` | `—` | `/file-upload` | GET | `src/backend/app.py` |
| `app` | `—` | `/intent-edit` | GET | `src/backend/app.py` |
| `app` | `—` | `/intent-review` | GET | `src/backend/app.py` |
| `app` | `—` | `/log-panel` | GET | `src/backend/app.py` |
| `app` | `—` | `/login` | GET | `src/backend/app.py` |
| `app` | `—` | `/logs` | GET | `src/backend/app.py` |
| `app` | `—` | `/metrics` | GET | `src/backend/app.py` |
| `app` | `—` | `/metrics-page` | GET | `src/backend/app.py` |
| `app` | `—` | `/nav-tree` | GET | `src/backend/app.py` |
| `app` | `—` | `/new-project` | GET | `src/backend/app.py` |
| `app` | `—` | `/personal-config` | GET | `src/backend/app.py` |
| `app` | `—` | `/pipeline` | GET | `src/backend/app.py` |
| `app` | `—` | `/pipeline-config` | GET | `src/backend/app.py` |
| `app` | `—` | `/project-mgmt` | GET | `src/backend/app.py` |
| `app` | `—` | `/prompt-templates` | GET | `src/backend/app.py` |
| `app` | `—` | `/quality-panel` | GET | `src/backend/app.py` |
| `app` | `—` | `/realtime-logs` | GET | `src/backend/app.py` |
| `app` | `—` | `/register` | GET | `src/backend/app.py` |
| `app` | `—` | `/review` | GET | `src/backend/app.py` |
| `app` | `—` | `/role-mgmt` | GET | `src/backend/app.py` |
| `app` | `—` | `/skill-rules` | GET | `src/backend/app.py` |
| `app` | `—` | `/step3-case-upload` | GET | `src/backend/app.py` |
| `app` | `—` | `/step4-config` | GET | `src/backend/app.py` |
| `app` | `—` | `/system-config` | GET | `src/backend/app.py` |
| `app` | `—` | `/test-methods` | GET | `src/backend/app.py` |
| `app` | `—` | `/user-mgmt` | GET | `src/backend/app.py` |
| `app` | `—` | `/version-mgmt` | GET | `src/backend/app.py` |
| `action_library_bp` | `—` | `/api/action-library` | GET | `src/backend/routes/action_library/routes/_crud.py` |
| `action_library_bp` | `—` | `/api/action-library/action` | POST | `src/backend/routes/action_library/routes/_crud.py` |
| `action_library_bp` | `—` | `/api/action-library/action/<action_id>` | DELETE | `src/backend/routes/action_library/routes/_crud.py` |
| `action_library_bp` | `—` | `/api/action-library/reset` | POST | `src/backend/routes/action_library/routes/_crud.py` |
| `action_library_bp` | `—` | `/api/action-library/search` | GET | `src/backend/routes/action_library/routes/_crud.py` |
| `action_library_bp` | `—` | `/api/action-library/export` | GET,POST | `src/backend/routes/action_library/routes/_export.py` |
| `action_library_bp` | `—` | `/api/action-library/import` | POST | `src/backend/routes/action_library/routes/_export.py` |
| `auth_bp` | `—` | `/api/can-access` | GET | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/user-credential-projects` | GET | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/user-credentials` | GET | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/user-credentials` | POST | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/user-credentials` | DELETE | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/user-credentials/copy` | POST | `src/backend/routes/auth/_credential.py` |
| `auth_bp` | `—` | `/api/disk-embed-models` | GET | `src/backend/routes/auth/_key.py` |
| `auth_bp` | `—` | `/api/key` | POST | `src/backend/routes/auth/_key.py` |
| `auth_bp` | `—` | `/api/key` | DELETE | `src/backend/routes/auth/_key.py` |
| `auth_bp` | `—` | `/api/ollama-models` | GET | `src/backend/routes/auth/_key.py` |
| `auth_bp` | `—` | `/api/current-user` | GET | `src/backend/routes/auth/_login.py` |
| `auth_bp` | `—` | `/api/login` | POST | `src/backend/routes/auth/_login.py` |
| `auth_bp` | `—` | `/api/logout` | POST | `src/backend/routes/auth/_login.py` |
| `auth_bp` | `—` | `/api/permissions` | GET | `src/backend/routes/auth/_permission.py` |
| `auth_bp` | `—` | `/api/permissions/role/<role>` | GET | `src/backend/routes/auth/_permission.py` |
| `auth_bp` | `—` | `/api/permissions/role/<role>` | PUT | `src/backend/routes/auth/_permission.py` |
| `auth_bp` | `—` | `/api/users/<user_id>/role` | PUT | `src/backend/routes/auth/_permission.py` |
| `auth_bp` | `—` | `/api/projects/<project>/members` | GET | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/projects/<project>/members` | POST | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/projects/<project>/members/<user_id>` | DELETE | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles` | GET | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles` | POST | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles/<role_id>` | PUT | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles/<role_id>` | DELETE | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles/<role_id>/permissions` | GET | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/roles/<role_id>/permissions` | PUT | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/users/<user_id>/projects` | GET | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/users/<user_id>/projects` | PUT | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/users/<user_id>/roles` | GET | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/users/<user_id>/roles` | PUT | `src/backend/routes/auth/_role.py` |
| `auth_bp` | `—` | `/api/executions` | GET | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/my-manageable-projects` | GET | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/register` | POST | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/user-projects/<user_id>` | GET | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/users` | GET | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/users` | POST | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/users/<user_id>` | PUT | `src/backend/routes/auth/_user.py` |
| `auth_bp` | `—` | `/api/users/<user_id>` | DELETE | `src/backend/routes/auth/_user.py` |
| `exec_bp` | `—` | `/api/calibration/summary` | GET | `src/backend/routes/calibration/_routes.py` |
| `exec_bp` | `—` | `/api/cases` | GET | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/<case_id>` | DELETE | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review` | GET | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/approve` | POST | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/batch-approve` | POST | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/confirm` | POST | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/edit` | POST | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/reject` | POST | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/cases/review/state` | GET | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/review/lane` | GET | `src/backend/routes/case_review/_routes.py` |
| `exec_bp` | `—` | `/api/review/lane/confirm` | POST | `src/backend/routes/case_review/_routes.py` |
| `case_rules_bp` | `—` | `/api/case-generation-rules` | GET | `src/backend/routes/case_rules/_routes.py` |
| `case_rules_bp` | `—` | `/api/step1-rag-rules` | GET | `src/backend/routes/case_rules/_routes.py` |
| `case_rules_bp` | `—` | `/api/step2-breakdown-rules` | GET | `src/backend/routes/case_rules/_routes.py` |
| `case_rules_bp` | `—` | `/api/step4-verification-rules` | GET | `src/backend/routes/case_rules/_routes.py` |
| `case_rules_bp` | `—` | `/api/step5-report-rules` | GET | `src/backend/routes/case_rules/_routes.py` |
| `case_upload_bp` | `—` | `/api/step3/case/uploads` | GET | `src/backend/routes/case_upload/_history.py` |
| `case_upload_bp` | `—` | `/api/step3/case/current-target` | GET | `src/backend/routes/case_upload/_template.py` |
| `case_upload_bp` | `—` | `/api/step3/case/template` | GET | `src/backend/routes/case_upload/_template.py` |
| `case_upload_bp` | `—` | `/api/step3/case/upload` | POST | `src/backend/routes/case_upload/_upload.py` |
| `case_upload_bp` | `—` | `/api/step3/case/validate` | POST | `src/backend/routes/case_upload/_validate.py` |
| `chart_bp` | `—` | `/api/report/chart-data` | GET | `src/backend/routes/chart_data.py` |
| `ci_bp` | `—` | `/api/ci/template/github` | GET | `src/backend/routes/ci_integration.py` |
| `ci_bp` | `—` | `/api/ci/template/jenkins` | GET | `src/backend/routes/ci_integration.py` |
| `ci_bp` | `—` | `/api/ci/token` | POST | `src/backend/routes/ci_integration.py` |
| `ci_bp` | `—` | `/api/ci/token/<int:token_id>` | DELETE | `src/backend/routes/ci_integration.py` |
| `ci_bp` | `—` | `/api/ci/tokens` | GET | `src/backend/routes/ci_integration.py` |
| `ci_bp` | `—` | `/api/ci/webhook-config` | GET | `src/backend/routes/ci_integration.py` |
| `clarification_bp` | `/api/step2/clarify` | `/answers` | POST | `src/backend/routes/clarification/_routes.py` |
| `clarification_bp` | `/api/step2/clarify` | `/gate` | GET | `src/backend/routes/clarification/_routes.py` |
| `clarification_bp` | `/api/step2/clarify` | `/questions` | GET | `src/backend/routes/clarification/_routes.py` |
| `clarification_bp` | `/api/step2/clarify` | `/release` | POST | `src/backend/routes/clarification/_routes.py` |
| `clarification_bp` | `/api/step2/clarify` | `/sessions` | GET | `src/backend/routes/clarification/_routes.py` |
| `clarification_bp` | `/api/step2/clarify` | `/sessions/<session_id>` | GET | `src/backend/routes/clarification/_routes.py` |
| `data_bp` | `—` | `/api/bg-config` | GET,POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/bg-gallery` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/bg-gallery/<gid>` | DELETE | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/bg-gallery/activate` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/bg-image` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/bg-image/<gid>` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/confluence-refresh` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/confluence-stats` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/db-stats` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/db-test` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/figma-image/<image_id>` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/figma-images` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/figma-quota` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/figma-refresh` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/figma-retry-failed` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/input-style` | GET,POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/requirements` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/requirements/review` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/requirements/review/confirm` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/upload-bg` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/yapi-refresh` | POST | `src/backend/routes/data_datasources/__init__.py` |
| `data_bp` | `—` | `/api/yapi-stats` | GET | `src/backend/routes/data_datasources/__init__.py` |
| `data_models_bp` | `—` | `/api/task-model` | POST | `src/backend/routes/data_models/_create.py` |
| `data_models_bp` | `—` | `/api/verification-model` | POST | `src/backend/routes/data_models/_create.py` |
| `data_models_bp` | `—` | `/api/vision-model` | POST | `src/backend/routes/data_models/_create.py` |
| `data_models_bp` | `—` | `/api/data-models/<model_id>` | DELETE | `src/backend/routes/data_models/_delete.py` |
| `data_models_bp` | `—` | `/api/data-models/<model_id>` | GET | `src/backend/routes/data_models/_detail.py` |
| `data_models_bp` | `—` | `/api/ai-defaults` | GET | `src/backend/routes/data_models/_list.py` |
| `data_models_bp` | `—` | `/api/models/validate` | POST | `src/backend/routes/data_models/_list.py` |
| `data_models_bp` | `—` | `/api/preview-models` | POST | `src/backend/routes/data_models/_list.py` |
| `data_models_bp` | `—` | `/api/task-models` | GET | `src/backend/routes/data_models/_list.py` |
| `data_models_bp` | `—` | `/api/verification-models` | GET | `src/backend/routes/data_models/_list.py` |
| `data_models_bp` | `—` | `/api/vision-models` | GET | `src/backend/routes/data_models/_list.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/failures` | GET | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/health` | GET | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/health/runs` | GET | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/self-test` | POST | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/self-test/history` | GET | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `diagnostics_bp` | `/api/diagnostics` | `/self-test/latest` | GET | `src/backend/routes/diagnostics/diagnostics_api.py` |
| `docs_bp` | `—` | `/api/docs/coverage` | GET | `src/backend/routes/docs/docs_read_coverage.py` |
| `docs_bp` | `—` | `/api/docs/breakdown` | GET | `src/backend/routes/docs/docs_read_downloads/_downloads.py` |
| `docs_bp` | `—` | `/api/docs/feature-list` | GET | `src/backend/routes/docs/docs_read_downloads/_downloads.py` |
| `docs_bp` | `—` | `/api/docs/intent-asset-file/<name>` | GET | `src/backend/routes/docs/docs_read_downloads/_downloads.py` |
| `docs_bp` | `—` | `/api/docs/requirement` | GET | `src/backend/routes/docs/docs_read_downloads/_downloads.py` |
| `docs_bp` | `—` | `/api/docs/executors` | GET | `src/backend/routes/docs/docs_read_downloads/_queries.py` |
| `docs_bp` | `—` | `/api/docs/figma-stats` | GET | `src/backend/routes/docs/docs_read_downloads/_queries.py` |
| `docs_bp` | `—` | `/api/docs/intent-asset-files` | GET | `src/backend/routes/docs/docs_read_downloads/_queries.py` |
| `docs_bp` | `—` | `/api/report-dir` | GET | `src/backend/routes/docs/docs_read_downloads/_queries.py` |
| `docs_bp` | `—` | `/api/docs/execution-result` | GET | `src/backend/routes/docs/docs_read_execution.py` |
| `docs_bp` | `—` | `/api/docs/execution-stats` | GET | `src/backend/routes/docs/docs_read_stats/_stats.py` |
| `docs_bp` | `—` | `/api/docs/diff/impact` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/diff/report` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/rag/stats` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/tool-execution-log` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/traceability` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/traceability/coverage` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/traceability/matrix` | GET | `src/backend/routes/docs/docs_read_traceability.py` |
| `docs_bp` | `—` | `/api/docs/version/<version_id>` | GET | `src/backend/routes/docs/docs_read_versions.py` |
| `docs_bp` | `—` | `/api/docs/versions` | GET | `src/backend/routes/docs/docs_read_versions.py` |
| `docs_bp` | `—` | `/api/docs/generate-smoke` | POST | `src/backend/routes/docs/docs_smoke.py` |
| `docs_bp` | `—` | `/api/docs/generate-smoke/status` | GET | `src/backend/routes/docs/docs_smoke.py` |
| `docs_bp` | `—` | `/api/docs/smoke-intent-files` | GET | `src/backend/routes/docs/docs_smoke.py` |
| `docs_bp` | `—` | `/api/docs/smoke-intents` | GET | `src/backend/routes/docs/docs_smoke.py` |
| `docs_bp` | `—` | `/api/docs/smoke-intents/clear` | POST | `src/backend/routes/docs/docs_smoke.py` |
| `bp` | `—` | `/api/trace/case/<case_id>` | GET | `src/backend/routes/docs/docs_trace_routes.py` |
| `bp` | `—` | `/api/trace/req/<req_id>` | GET | `src/backend/routes/docs/docs_trace_routes.py` |
| `bp` | `—` | `/api/trace/slice/<slice_id>` | GET | `src/backend/routes/docs/docs_trace_routes.py` |
| `docs_bp` | `—` | `/api/docs/rag/rebuild` | POST | `src/backend/routes/docs/docs_write/_rag.py` |
| `docs_bp` | `—` | `/api/docs/requirement` | POST | `src/backend/routes/docs/docs_write/_requirement.py` |
| `docs_bp` | `—` | `/api/docs/requirement/ai-supplement` | POST | `src/backend/routes/docs/docs_write/_requirement.py` |
| `docs_bp` | `—` | `/api/docs/xmind-design` | POST | `src/backend/routes/docs/docs_write/_xmind.py` |
| `bp` | `—` | `/api/docs/batch-delete` | POST | `src/backend/routes/docs_analytics/routes/_file_tree.py` |
| `bp` | `—` | `/api/docs/delete` | POST | `src/backend/routes/docs_analytics/routes/_file_tree.py` |
| `bp` | `—` | `/api/docs/file-list` | GET | `src/backend/routes/docs_analytics/routes/_file_tree.py` |
| `bp` | `—` | `/api/docs/files` | GET | `src/backend/routes/docs_analytics/routes/_file_tree.py` |
| `bp` | `—` | `/api/docs/preview` | GET | `src/backend/routes/docs_analytics/routes/_preview.py` |
| `bp` | `—` | `/api/docs/file` | GET | `src/backend/routes/docs_analytics/routes/_save.py` |
| `bp` | `—` | `/api/docs/save` | POST | `src/backend/routes/docs_analytics/routes/_save.py` |
| `bp` | `—` | `/api/docs/search` | GET | `src/backend/routes/docs_analytics/routes/_search.py` |
| `bp` | `—` | `/api/docs/storage-stats` | GET | `src/backend/routes/docs_analytics/routes/_storage.py` |
| `exec_bp` | `—` | `/api/browser-parallel-recommendation` | GET | `src/backend/routes/execution/__init__.py` |
| `exec_bp` | `—` | `/api/step4-config` | GET | `src/backend/routes/execution/__init__.py` |
| `exec_bp` | `—` | `/api/step4/run-options` | GET | `src/backend/routes/execution/__init__.py` |
| `exec_bp` | `—` | `/api/step4/run-options` | POST | `src/backend/routes/execution/__init__.py` |
| `exec_bp` | `—` | `/api/execution-tracks` | GET | `src/backend/routes/execution/_pipeline_trace.py` |
| `exec_bp` | `—` | `/api/pipeline-trace/<int:step>/<id>` | GET | `src/backend/routes/execution/_pipeline_trace.py` |
| `exec_bp` | `—` | `/api/client-browser/check` | POST | `src/backend/routes/execution/client_browser.py` |
| `exec_bp` | `—` | `/api/client-browser/detect-ip` | GET | `src/backend/routes/execution/client_browser.py` |
| `exec_bp` | `—` | `/api/client-browser/startup-script` | GET | `src/backend/routes/execution/client_browser.py` |
| `exec_bp` | `—` | `/api/config/flags` | GET | `src/backend/routes/execution/config.py` |
| `exec_bp` | `—` | `/api/config/toggle` | POST | `src/backend/routes/execution/config.py` |
| `exec_bp` | `—` | `/api/deployment/docs` | GET | `src/backend/routes/execution/deployment.py` |
| `exec_bp` | `—` | `/api/deployment/preview` | POST | `src/backend/routes/execution/deployment.py` |
| `exec_bp` | `—` | `/api/deployment/versions` | POST | `src/backend/routes/execution/deployment.py` |
| `exec_bp` | `—` | `/api/last-run` | GET | `src/backend/routes/execution/deployment.py` |
| `exec_bp` | `—` | `/api/pipeline-progress` | GET | `src/backend/routes/execution/pipeline_progress.py` |
| `exec_bp` | `—` | `/api/report-content/<int:report_id>` | GET | `src/backend/routes/execution/pipeline_progress.py` |
| `exec_bp` | `—` | `/api/source-doc-content/<doc_type>` | GET | `src/backend/routes/execution/pipeline_progress.py` |
| `exec_bp` | `—` | `/api/preflight` | GET | `src/backend/routes/execution/preflight.py` |
| `exec_bp` | `—` | `/api/preflight/verify` | POST | `src/backend/routes/execution/preflight.py` |
| `exec_bp` | `—` | `/api/target-system-status` | GET | `src/backend/routes/execution/preflight.py` |
| `exec_bp` | `—` | `/api/review/approve` | POST | `src/backend/routes/execution/review.py` |
| `exec_bp` | `—` | `/api/review/reject` | POST | `src/backend/routes/execution/review.py` |
| `run_bp` | `—` | `/api/coverage-check` | GET | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/coverage/auto-fill` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/retry-failed` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/retry-failed-with-regen` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/run-all` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/step/<int:n>` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/step4-run` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/step4-run-single` | POST | `src/backend/routes/execution/run/_start.py` |
| `run_bp` | `—` | `/api/pause` | POST | `src/backend/routes/execution/run/_status.py` |
| `run_bp` | `—` | `/api/resume` | POST | `src/backend/routes/execution/run/_status.py` |
| `run_bp` | `—` | `/api/stop` | POST | `src/backend/routes/execution/run/_stop.py` |
| `run_bp` | `—` | `/api/stop/<run_id>` | POST | `src/backend/routes/execution/run/_stop.py` |
| `exec_bp` | `—` | `/api/status` | GET | `src/backend/routes/execution/status.py` |
| `exec_bp` | `—` | `/api/version-check/cancel` | POST | `src/backend/routes/execution/status.py` |
| `exec_bp` | `—` | `/api/version-check/confirm` | POST | `src/backend/routes/execution/status.py` |
| `export_bp` | `—` | `/api/export/<path:type>/preview` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/export/coverage` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/export/navigation-tree` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/export/report` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/export/requirements` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/export/validation-intents` | GET | `src/backend/routes/export.py` |
| `export_bp` | `—` | `/api/navigation-tree/preview` | GET | `src/backend/routes/export.py` |
| `exec_bp` | `—` | `/api/clearable-files/children` | GET | `src/backend/routes/file_cleanup/_children.py` |
| `exec_bp` | `—` | `/api/clear-files` | POST | `src/backend/routes/file_cleanup/_clear.py` |
| `exec_bp` | `—` | `/api/clearable-files` | GET | `src/backend/routes/file_cleanup/_list.py` |
| `file_upload_bp` | `—` | `/api/step1/uploads/<file_hash>` | DELETE | `src/backend/routes/file_upload/_delete.py` |
| `file_upload_bp` | `—` | `/api/step1/uploads/<file_hash>/weight` | PUT | `src/backend/routes/file_upload/_delete.py` |
| `file_upload_bp` | `—` | `/api/step1/uploads` | GET | `src/backend/routes/file_upload/_list.py` |
| `file_upload_bp` | `—` | `/api/step1/upload` | POST | `src/backend/routes/file_upload/_upload.py` |
| `health_bp` | `—` | `/api/health/detailed` | GET | `src/backend/routes/health/routes_diagnostics.py` |
| `health_bp` | `—` | `/api/health/metrics` | GET | `src/backend/routes/health/routes_diagnostics.py` |
| `health_bp` | `—` | `/healthz` | GET | `src/backend/routes/health/routes_diagnostics.py` |
| `health_bp` | `—` | `/readyz` | GET | `src/backend/routes/health/routes_diagnostics.py` |
| `health_bp` | `—` | `/api/health/summary` | GET | `src/backend/routes/health/routes_summary.py` |
| `health_bp` | `—` | `/api/health/latest-run` | GET | `src/backend/routes/health/routes_trends.py` |
| `health_bp` | `—` | `/api/health/trends` | GET | `src/backend/routes/health/routes_trends.py` |
| `kpi_bp` | `/api/kpi` | `/overview` | GET | `src/backend/routes/kpi/_routes.py` |
| `kpi_bp` | `/api/kpi` | `/trend` | GET | `src/backend/routes/kpi/_routes.py` |
| `metrics_page_bp` | `—` | `/api/metrics-summary` | GET | `src/backend/routes/metrics_page/metrics_page.py` |
| `metrics_page_bp` | `—` | `/api/test-methods` | GET | `src/backend/routes/metrics_page/metrics_page.py` |
| `exec_bp` | `—` | `/api/nav-match/calibrate` | POST | `src/backend/routes/nav_match/_routes.py` |
| `exec_bp` | `—` | `/api/nav-match/logs` | GET | `src/backend/routes/nav_match/_routes.py` |
| `exec_bp` | `—` | `/api/nav-match/stats` | GET | `src/backend/routes/nav_match/_routes.py` |
| `exec_bp` | `—` | `/api/visual-review` | POST | `src/backend/routes/oracle/_routes.py` |
| `exec_bp` | `—` | `/api/visual-review/status` | GET | `src/backend/routes/oracle/_routes.py` |
| `overrides_bp` | `—` | `/api/admin/prompts` | GET | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/admin/prompts` | POST | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/admin/prompts/<name>` | DELETE | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/admin/prompts/<name>/restore` | POST | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/admin/prompts/re-seed` | POST | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/project-overrides/synonyms` | GET | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/project-overrides/synonyms` | POST | `src/backend/routes/overrides_routes.py` |
| `overrides_bp` | `—` | `/api/project-overrides/synonyms/<core_word>` | DELETE | `src/backend/routes/overrides_routes.py` |
| `p0_plugins_bp` | `—` | `/api/p0-plugin-metrics` | GET | `src/backend/routes/p0_plugins.py` |
| `pipeline_cfg_bp` | `—` | `/api/pipeline-config/apply-recommended` | POST | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/pipeline-config/recommended` | GET | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/user-pipeline-config` | GET | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/user-pipeline-config` | PUT | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/user-pipeline-config` | DELETE | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/user-preference/<key>` | GET | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_cfg_bp` | `—` | `/api/user-preference/<key>` | PUT | `src/backend/routes/pipeline_config/_routes.py` |
| `pipeline_plugins_bp` | `—` | `/api/pipeline-plugin-metrics` | GET | `src/backend/routes/pipeline_plugins.py` |
| `project_config_bp` | `—` | `/api/config` | GET | `src/backend/routes/project_config/project_config.py` |
| `project_config_bp` | `—` | `/api/config` | POST | `src/backend/routes/project_config/project_config.py` |
| `project_config_bp` | `—` | `/api/config/history` | GET | `src/backend/routes/project_config/project_config.py` |
| `project_config_bp` | `—` | `/api/config/rollback` | POST | `src/backend/routes/project_config/project_config.py` |
| `project_config_bp` | `—` | `/api/proxy/test-connection` | POST | `src/backend/routes/project_config/project_config.py` |
| `project_core_bp` | `—` | `/api/project/<name>` | DELETE | `src/backend/routes/project_core/_archive.py` |
| `project_core_bp` | `—` | `/api/project/<name>/restore` | POST | `src/backend/routes/project_core/_archive.py` |
| `project_core_bp` | `—` | `/api/project/meta` | GET | `src/backend/routes/project_core/_meta.py` |
| `project_core_bp` | `—` | `/api/project/meta` | POST | `src/backend/routes/project_core/_meta.py` |
| `project_core_bp` | `—` | `/api/new-project` | POST | `src/backend/routes/project_core/_project.py` |
| `project_core_bp` | `—` | `/api/project` | POST | `src/backend/routes/project_core/_project.py` |
| `project_core_bp` | `—` | `/api/projects` | GET | `src/backend/routes/project_core/_project.py` |
| `quality_bp` | `/api/quality` | `/methodology-validation` | GET | `src/backend/routes/quality/quality_routes.py` |
| `quality_bp` | `/api/quality` | `/semantic-merge/latest` | GET | `src/backend/routes/quality/quality_routes.py` |
| `quality_bp` | `/api/quality` | `/w3-summary` | GET | `src/backend/routes/quality/quality_routes.py` |
| `bp` | `—` | `/api/figma` | GET | `src/backend/routes/routes/_misc.py` |
| `bp` | `—` | `/api/intent-metrics` | GET | `src/backend/routes/routes/_misc.py` |
| `bp` | `—` | `/api/run-summary-latest` | GET | `src/backend/routes/routes/_misc.py` |
| `bp` | `—` | `/api/stats` | GET | `src/backend/routes/routes/_misc.py` |
| `bp` | `—` | `/api/step1-status` | GET | `src/backend/routes/routes/_misc.py` |
| `bp` | `—` | `/api/plugin-metrics` | GET | `src/backend/routes/routes/_plugin.py` |
| `bp` | `—` | `/api/token-dashboard` | GET | `src/backend/routes/routes/_token.py` |
| `bp` | `—` | `/api/token-phase-breakdown` | GET | `src/backend/routes/routes/_token.py` |
| `bp` | `—` | `/api/token-stats` | GET | `src/backend/routes/routes/_token.py` |
| `synonym_bp` | `—` | `/api/synonyms` | GET | `src/backend/routes/synonym_routes.py` |
| `synonym_bp` | `—` | `/api/synonyms` | POST | `src/backend/routes/synonym_routes.py` |
| `synonym_bp` | `—` | `/api/synonyms/<int:synonym_id>` | DELETE | `src/backend/routes/synonym_routes.py` |
| `synonym_bp` | `—` | `/api/synonyms/batch` | POST | `src/backend/routes/synonym_routes.py` |
| `system_cfg_bp` | `—` | `/api/system-config` | GET | `src/backend/routes/system_config_api.py` |
| `system_cfg_bp` | `—` | `/api/system-config` | POST | `src/backend/routes/system_config_api.py` |
| `version_bp` | `—` | `/api/project/versions` | GET | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions` | POST | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>` | PUT | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/activate` | POST | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/archive` | POST | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/delete` | POST | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/purge` | DELETE | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/requirements` | GET | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/<int:version_id>/restore` | POST | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/active` | GET | `src/backend/routes/version.py` |
| `version_bp` | `—` | `/api/project/versions/compare` | GET | `src/backend/routes/version.py` |

## 蓝图清单

| 蓝图变量 | 蓝图名 | 声明前缀 |
|:---|:---|:---|
| `action_library_bp` | action_library | `—` |
| `auth_bp` | auth | `—` |
| `case_review_bp` | case_review | `—` |
| `case_rules_bp` | case_rules | `—` |
| `case_upload_bp` | case_upload | `—` |
| `chart_bp` | chart_data | `—` |
| `ci_bp` | ci_integration | `—` |
| `clarification_bp` | clarification | `/api/step2/clarify` |
| `data_bp` | data | `—` |
| `data_models_bp` | data_models | `—` |
| `diagnostics_bp` | diagnostics | `/api/diagnostics` |
| `docs_analytics_bp` | docs_analytics | `—` |
| `docs_bp` | docs | `—` |
| `exec_bp` | execution | `—` |
| `export_bp` | export | `—` |
| `file_upload_bp` | file_upload | `—` |
| `health_bp` | health | `—` |
| `kpi_bp` | kpi | `/api/kpi` |
| `metrics_page_bp` | metrics_page | `—` |
| `overrides_bp` | overrides | `—` |
| `p0_plugins_bp` | p0_plugins | `—` |
| `pipeline_cfg_bp` | pipeline_cfg | `—` |
| `pipeline_plugins_bp` | pipeline_plugins | `—` |
| `project_bp` | project | `—` |
| `project_config_bp` | project_config | `—` |
| `project_core_bp` | project_core | `—` |
| `quality_bp` | quality | `/api/quality` |
| `synonym_bp` | synonym | `—` |
| `system_cfg_bp` | system_config | `—` |
| `version_bp` | version | `—` |
