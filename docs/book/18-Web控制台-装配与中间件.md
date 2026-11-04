# 第 18 章　Web 控制台 · 装配与中间件

## 概述

Web 控制台是 E2E 智能测试引擎的唯一操作入口，其后端为 Flask 3 应用 `src/backend/app.py`（26742 字节）。应用采用 facade 拆分：装配逻辑集中在 `app.py`，鉴权与限流在 `_middleware.py`，SSE 与页面路由在 `_sse_routes.py`，全局状态在 `state.py`，日志链路由 `log_capture.py`、`log_persistence.py`、`observers.py` 协作，请求校验由 `request_schema.py` 与 `pydantic_schemas.py` 提供。本章只陈述读到的代码事实，行号以源文件实测为准。

## 应用装配顺序

`app.py` 的执行顺序决定了中间件与蓝图的行为边界，关键节点如下：

1. 创建 Flask 应用并设定模板/静态目录：`app.py:130` 以 `src/frontend/templates` 与 `src/frontend/static` 为模板与静态根。
2. 会话存储：`app.py:162` 默认 `SESSION_STORAGE=mysql`，经 `flask_session` 落表 `flask_sessions`；连接池 `pool_size=2, max_overflow=2, pool_recycle=300`（`app.py:191`）。`SESSION_COOKIE_HTTPONLY=True`、`SAMESITE=Lax`（`app.py:153`）。
3. 中间件注册顺序固定（`app.py:218`）：`after_request(_no_cache)` → `teardown_request(_release_db_connections)` → `before_request(_metrics_before_request)`、`after_request(_metrics_after_request)` → `before_request(try_api_cache)` → `before_request(invalidate_cache_on_write)` → `before_request(_require_login)` → `before_request(_maybe_extend_session)` → `after_request(save_api_cache)`。缓存命中在最前、`_require_login` 在其后，保证命中缓存的请求跳过登录守卫。
4. 启动期副作用（`app.py:236` 起）：`install_log_capture()` 接管 `sys.stdout`；`_register_ai_observers()` 与 `_hijack_router_logs()` 注入观察者；`init_schema_on_startup()` 预建表；`prompt_templates.seed_if_empty()` 写种子；守护线程预热 embedding 模型；`init_pipeline_plugins()` 实例化插件；并向 `browser_core` 注入 `update_monitor_dom` 等回调（`app.py:284`）。
5. 蓝图注册：`app.py:357` 起经 `_safe_register_blueprint()` 注册 25 个蓝图（带 `_got_registered_once` 重置防重复）；页面路由随后以 `app.route(...)` 直接注册（`app.py:435` 起）。
6. 启动入口 `main()`（`app.py:509`）：bind host 取 `WEB_BIND_HOST`（默认 `127.0.0.1`），端口默认 `8700`，生产用 `waitress` 起 48 线程（`app.py:545`）；启动前 `run_all()` bootstrap，启动后清理僵尸检查点（`app.py:602`）。

## 中间件机制

`_middleware.py` 提供六类钩子，全部从 `app.py` 注册，互不依赖 Flask 扩展：

- 缓存控制：`try_api_cache`（`_middleware.py:83`）与 `save_api_cache`（`_middleware.py:112`）实现 GET `/api/*` 30 秒 TTL 内存缓存，键为 `(path, query, uid, proj)`（`_middleware.py:39`）。黑名单 `/api/log-stream`、`/api/sse`、`/api/events` 不缓存（`:32`）；`/api/status` 走 1 秒短缓存（`:87`）。写请求经 `invalidate_cache_on_write` 清空全部缓存（`:196`）。
- 响应头：`_no_cache`（`_middleware.py:217`）统一给 `/api/`、`/static/`、动态页面加 `no-store` 头，并对 `/api/bg-image` 放行 1 小时浏览器缓存；另加 `X-Content-Type-Options`、`X-Frame-Options`、`CSP` 等安全头（`:240`）。
- 连接兜底：`_release_db_connections`（`_middleware.py:256`）在 `teardown_request` 释放线程借出未还的连接。
- 耗时指标：`_metrics_before/after_request` 记录 API 耗时并上报 `metrics_collector`（`_middleware.py:267`、`:273`）。
- 登录守卫：`_require_login`（`_middleware.py:319`）先抽 `X-API-Key`/`Bearer` 走 API Token 白名单（`:307`），再走 session。白名单 `_PUBLIC_PATHS`（`:292`）含 `/api/login`、`/api/logout`、`/api/status` 等；未登录访问 `/api/*` 返回 401（`:475`）。守卫每次把 `session.user_id/project_name/version_label` 同步到 thread-local（`config_loader.set_current_user` 与 `state._set_current_project`），消除线程池复用造成的用户/项目串扰（`:379`、`:392`、`:416`）。
- 滑动续期：`_maybe_extend_session`（`_middleware.py:481`）在活跃任务时每次请求续期，无任务时每 5 分钟续一次；`SESSION_IDLE_MINUTES=180`（`_middleware.py:316`）。

## SSE 实时日志通道

`_sse_routes.py` 的 `api_log_stream`（`_sse_routes.py:363`）每个订阅者分配一个 `RingBuffer(maxsize=1000)`（`_ring_buffer.py:14`）。`RingBuffer` 基于 `deque(maxlen=1000)`，满时丢弃最旧消息、永不阻塞（`_ring_buffer.py:42`）。订阅者按 `run_id` 有无分别挂入 `_sse_queues` 或 `_run_sse_queues`（`_sse_routes.py:374`）。生成器每 5 秒发一次 `{"heartbeat": true}` 心跳（`:389`），客户端断开时从对应队列移除（`:396`）。

广播起点在 `log_capture.py` 的 `_broadcast_log`（`log_capture.py:182`）：同一条 payload 既发全局队列、也发 `run_id` 专属队列，字段含 `log/run_id/priority/step/level/ts`，保证前端按 Tab 分发。优先级由 `_classify_sse_priority` 分级（`:82`），`high/normal` 在队列满时挤掉旧消息（`:102`）。

## 全局状态 state.py

`state.py` 用 thread-local 与进程级字典管理全局状态，关键实体：

- 显式状态机 `ExecutionState`（`state.py:24`）：`pending/running/paused/completed/failed/forced_stopped`，定义合法转换路径。
- 当前项目与版本：`_current_project`/`_set_current_project` 基于 `threading.local`（`:129`），写时回查 `projects` 表做大小写归一并同步 Flask session（`:134`）。`_resolve_project` 提供「显式参数 → thread-local」回退链（`:161`）。
- 运行状态：A1 改造后 `_force_stop_by_project`、`_paused_step_by_project`、`_version_warning_by_project`、`_thinking_by_project` 均为按项目隔离的字典（`:217` 起）；`_is_running(project)` 支持按项目过滤（`:293`），并在读前 `_sweep_stale_runs` 清僵尸（`:663`）——只清线程已退出的条目，存活但超时的置 `force_stop` 令其自行收敛，避免同项目并发（`:705`）。
- 锁顺序：P0-05 引入 `_register_lock` 与 `ordered_lock`（`:58`、`:66`），按编号升序取锁防死锁。
- Token 统计：`_total_*/_cur_*` 经 `_update_token_stats` 一次加锁更新（`:504`），并从 `ai_health_metrics` 加载跨重启基线 `_db_token_baseline`（`:462`）。

## 日志捕获与持久化链路

链路为：业务 `print`/logging → `LogCapture`（替换 `sys.stdout`，`log_capture.py:248`）与 `_install_logging_bridge` 的 `_SSELogHandler`（`log_capture.py:380`）→ `_broadcast_log` 入 SSE 队列 + 入内存 `_all_log_lines`/`_run_logs`（`state.py:627`、`:639`）→ `log_persistence.append_run_log` 写 MySQL `run_logs` 表（`log_persistence.py:176`）。`run_logs` 以 `run_id, project, version_id, step_num, log_level, message` 为列（`:9`）。`_normalize_run_id`（`log_persistence.py:78`）强制 `run_id` 前缀等于当前 `project`，杜绝跨项目日志残留。`finish_run_log` 调用 `cleanup_manager.cleanup_all(keep_runs=20)`（`log_persistence.py:312`）触发清理。

`observers.py` 注册 AiBrain 观察者：`on_finish` 调 `_st._update_token_stats` 并经 `_persist_token_usage` 后台落 `ai_health_metrics`（`observers.py:39`、`:74`）；`_hijack_router_logs` 劫持 Router 日志更新模型切换原因（`observers.py:211`）。`cleanup_manager.py` 的 `CleanupManager` 提供 `cleanup_logs`、`cleanup_temp_files`、`cleanup_old_screenshots`、`cleanup_old_run_logs`（183 天）、`cleanup_old_versions` 等（`cleanup_manager.py:24`）。`helpers.py` 提供 `_user_can_access_project`、`_resolve_request_project`、`_validate_key` 等鉴权与校验辅助（`:25`、`:47`、`:354`）。

## Pydantic 请求校验

`request_schema.py` 的 `validate_json_schema`（`request_schema.py:17`）做轻量校验（类型/必填/枚举/长度/正则），失败时返回标准 400 JSON。

`pydantic_schemas.py` 的 `validate_body`（`pydantic_schemas.py:38`）优先用 Pydantic 校验；未安装 pydantic 时降级为 `validate_json_schema`。已定义模型：`ConfigUpdateSchema`、`NewProjectSchema`、`ExecutionStartSchema`、`VersionCreateSchema`、`IntentReviewSchema`、`LoginSchema`（`:245` 起）。`LoginSchema` 规范键 `user_id`，注释明确接受别名 `username`（`:303`）。`ExecutionStartSchema` 覆盖 `/api/step/<n>`（`:265`，`step` 约束 `ge=1, le=5`）。

## 设计取舍

- 中间件用普通函数钩子而非 Flask 扩展，便于在 `app.py` 精确排定「缓存命中在登录守卫之前」的顺序；代价是逻辑分散在 `_middleware.py`，与 `app.py` 强耦合。
- 全局状态用 thread-local 隔离多用户，并以按项目字典隔离执行槽；代价是 A1 改造遗留大量 `_by_project` 字典，读取需传 `project` 参数。
- SSE 用 `RingBuffer` 替代 `queue.Queue`，牺牲「严格有序保留」换取「生产快于消费时永不阻塞执行线程」。
- 日志双写内存 + MySQL：内存供实时推送与最近历史，MySQL 供跨重启追溯；代价是 `run_logs` 写入链路需跨线程纠正 `project`/`run_id`。

## 本章代码依据

- `src/backend/app.py:130`、`app.py:218`、`app.py:357`、`app.py:509`
- `src/backend/_middleware.py:83`、`_middleware.py:217`、`_middleware.py:319`、`_middleware.py:481`
- `src/backend/_sse_routes.py:363`、`src/backend/_ring_buffer.py:14`、`src/backend/log_capture.py:182`
- `src/backend/state.py:24`、`state.py:129`、`state.py:293`、`state.py:663`
- `src/backend/log_persistence.py:78`、`src/backend/observers.py:74`
- `src/backend/request_schema.py:17`、`src/backend/pydantic_schemas.py:38`

## 待核实

- ~~`executions.status` / `failure_reason` 字段名~~ → **已核实（2026-10-15）：拼写正确。** `src/core/schema.sql:218` 起为 `executions` 建表语句，含 `status` 与 `failure_reason`；同表另有 `failure_chain`(`:230`)、`screenshot`(`:231`)、`check_type`(`:235`)。
- ~~`SESSION_REDIS=None` 运行期是否会被注入~~ → **已核实（2026-10-15）：不会，redis 模式实际不可用。** `src/backend/app.py:162-179` 三分支实测：redis 分支（`:164-168`）只设 `SESSION_TYPE`/`SESSION_REDIS=None`/`SESSION_KEY_PREFIX`，`SESSION_REDIS` 之后**再无赋值点**；filesystem 分支（`:169-175`）；else 分支（默认，`:176-200`）走 SQLAlchemy。即设 `SESSION_STORAGE=redis` 会得到 None 连接，Flask-Session 初始化即失败。默认 `SESSION_STORAGE` 为 `mysql`（`:162`），故线上不受影响。
- `waitress` 48 线程为实测默认（`app.py:545`），实际并发上限依赖部署配置，未在本次读取中验证压测数据。
