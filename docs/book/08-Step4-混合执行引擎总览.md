# 第 8 章　Step4 · 混合执行引擎总览

## 概述

Step4 把 `TestCase {case_id, actions, checks}` 形式的用例物化为浏览器执行。执行编排入口是 `src/pipeline/step4_executor.py` 的 `step4_execute`（`step4_executor.py:202`），它从 `PipelineScheduler` 拆分而来，仅移动物理位置、不改业务逻辑。调度分为两层：运行级（一次 Web 触发）由「执行槽」控制并发；用例级（一批用例）由 `browser_core` 串行执行、失败即停。用例按 `actions`/`checks` 的数据完整度被路由到三条执行策略，三条策略共用同一条验证管道。

## 入口与多通道意图读取

`step4_execute` 的候选集来源按优先级分支（`step4_executor.py:348`-`415`）：

| 触发方式 | 读取函数 | 行号 |
|:---|:---|:---|
| 部署文档匹配 | `DeploymentMatcher.filter_intents_by_deployment` | `step4_executor.py:309` |
| 多版本 | `_read_intents_for_versions` | `step4_executor.py:349` |
| 多文件 | `_read_unexecuted_cases_by_files` | `step4_executor.py:365` |
| 失败重跑 | `_read_failed_intents` | `step4_executor.py:385` |
| 指定 case_id | `_read_intents_by_ids` | `step4_executor.py:400` |
| 全量/含已执行 | `_read_intents_with_meta` | `step4_executor.py:409`/`413` |

全量语义兜底：`include_executed`/`case_ids` 皆空且非 `retry_failed` 时，强制 `include_executed=True`（`step4_executor.py:254`），保证一键执行跑全量。候选集再按 `is_api_only_intent` 拆成 `api_only` 与 `browser_intents` 两路（`step4_executor.py:710`），api-only 走快速通道 `execute_api_only_intents`，浏览器用例进入 `BrowserCore.run_test_cases`。

## 两层调度模型

### 运行级：执行槽与 watchdog

运行级并发由信号量 `_RUN_SLOT = threading.Semaphore(4)` 控制（`_start.py:175`）。`_spawn_runner_thread` 以 `acquire(blocking=False)` 取槽，取不到直接返回 429 `EXECUTION_SLOT_FULL`，不阻塞 Web 服务（`_start.py:297`-`311`）。槽位在正常路径由 `_slot_runner` 的 `finally` 释放（`_start.py:237`）。

watchdog `_slot_watchdog`（`_start.py:354`）在 runner 线程挂起时兜底释放槽位，避免后续 run 级联 429。判据为「进展停滞」而非总时长：只要 `run_logs`/`executions` 持续有新行就不误杀，停滞预算按 step 区分：`_RUN_SLOT_MAX_SECONDS=180`、run-all（`step=1`）=`1800`、AI 重步骤（`step∈{2,3}`）=`900`（`_start.py:193`-`197`，`322`-`326`）。

### 用例级：串行与失败即停

`run_test_cases`（`base.py:288`）把 `max_parallel` 透传给 `_execute_all`（`serial.py:174`）。§10.1 红线强制 `max_parallel` 降为 1（`serial.py:209`-`219`）：即便配置 `browser_max_parallel>1`，也强制串行并执行「失败即停」，`break` 立即生效。并行路径仍保留在 `parallel.py`，以每批 `max_parallel` 个 task、整批完成再起下一批的方式分批（`parallel.py:423`-`427`）。

### 浏览器并行度自适应推荐

配置里的 `browser_max_parallel` 是人类设定的上限，而实际可跑的并行度由 `get_browser_parallel_recommendation(configured_max)`（`src/core/pipeline_config.py:891`）按**可用内存**推算：

- 入参 `configured_max` 先归一到 1–8（`:893`）。
- 结果带 30 秒缓存，缓存键含 `configured_max`，避免不同入参互相覆盖，返回副本防止外部 mutate（`:894-902`）。
- 读 `psutil.virtual_memory().available` 得 `available_mb`（`:920-921`），并估算单个浏览器实例内存 `browser_avg_mb`（默认取自 `browser_parallel_instance_mb=800`，`:905`）。
- 推荐值 = `clamp(usable_mb // browser_avg_mb, 1, 8)`，其中 `usable_mb = max(0, available_mb - reserved_mb)`，`reserved_mb` 来自 `browser_parallel_reserved_mb`（默认 2048，`:904`、`:947-949`）。
- 是否采纳推荐值由开关 `browser_parallel_auto_limit`（默认 True，`:911`）决定：开启时用 `min(configured_max, recommended)`，关闭时直接用 `configured_max`（`:952-954`）。返回 dict 含 `configured_max`/`recommended`/`effective`/`auto_limit`/`available_mb`/`browser_avg_mb`/`reserved_mb`/`reason`（`:907-916`）。

即：`browser_max_parallel` 是硬上限，自适应推荐在其之下按内存动态下压；未检测到系统内存时回退保守默认值（`reason="未检测到系统内存，使用保守默认值"`，`:915`）。

## 三策略路由

路由入口是 `steps_from_case`（`browser_intent_parser/_steps.py:383`），它把单个用例拆成执行步骤列表，策略选择委托给 `_select_strategy`（`strategy_router.py:30`）。选择依据仅看 `actions`/`checks` 是否非空列表：

| 条件 | 策略常量 | 含义 |
|:---|:---|:---|
| `actions` 且 `checks` 非空 | `STRATEGY_DETERMINISTIC = 1` | 确定性执行（零 AI） |
| 非 `actions`、`checks` 非空 | `STRATEGY_AI_EXPLORATION = 2` | AI 自主探索 |
| `actions` 非空、非 `checks` | `STRATEGY_PURE_ACTION = 3` | 纯动作执行 |
| 两者皆空 | `STRATEGY_SKIP = 0` | 跳过 |

人工超驰经 `case["forced_strategy"]` 传入（`_steps.py:396`），`_select_strategy` 在 `forced_strategy` 模式下校验前提：策略 1 需 `actions` 非空、策略 2 需 `checks` 非空、策略 3 需 `actions` 非空，前提不满足则回落 `STRATEGY_SKIP` 并带 `error`（`strategy_router.py:54`-`104`）。该函数为纯函数、无外部依赖，可独立单测（`strategy_router.py:16`）。

## 用例拆成执行步骤

`steps_from_case` 按策略产出步骤条目：

- 策略 1：构造 `execute` 步骤，挂 `_build_verify_step` 生成的三轨校验字段（`api_checks`/`db_checks`/`ui_checks`/`required_tracks`/`_checks`），`_strategy=1`（`_steps.py:422`-`454`）。`checks` 为空时标记 `skip_verification`，只执行 actions（S45-001，`_steps.py:435`）。
- 策略 2：构造 `exploration_step`，把成功标准拼成探索约束，并注入生成端 `test_steps` 作为「已知操作路径」指引（非强制），再追加 `verify_step`（`_steps.py:460`-`490`）。
- 策略 3：构造 `execute` 步骤并挂 `_build_basic_verify_step` 基础验证，`_strategy=3`（`_steps.py:495` 起）。

步骤列表经 `_execute_one_intent` 的 `_normalize_steps` 规整后进入步骤循环（`_base.py:96`），三条策略在此汇入同一验证管道。

## 失败重试 retry_failed

`retry_failed` 由 `opts.get("retry_failed")` 或 `mode=="retry_failed"` 判定（`step4_executor.py:246`-`248`）。该模式下候选集走 `_read_failed_intents`（`step4_executor.py:384`-`389`），多版本分支被显式跳过（`step4_executor.py:348`）。失败用例无论风险等级都重跑（`step4_executor.py:465`）。

## 结果落库与 run_id

每条用例结果写入 `executions` 表，由 `record_execution`（`audit.py:121`）经 `_upsert_execution`（`audit.py:85`）幂等落库：同一 `(run_id, project, case_id, check_type)` 已存在则 UPDATE 后者胜出，否则 INSERT（`audit.py:96`-`118`）；`run_id` 为空时退化为普通 INSERT，避免跨 run 覆盖（`audit.py:93`）。运行期日志写入 `run_logs` 表（`log_persistence.py:9`，`:150`），列含 `run_id, project, version_id, step_num, log_level, message`。

`run_id` 由 `make_run_id` 生成，规则为 `{project}__{version_label}`，非法字符经 `_RUN_ID_SAFE_RE`（`[^\w]`）替换为下划线（`versioning.py:21`，`:40`-`42`）。该值确定性，不含 step 与 user_id。字段为空时回退 `default`/`unversioned`（`versioning.py:34`-`38`）。权威解析收口于 `resolve_run_id`（`versioning.py:58`），优先级：显式传入 → 线程级 `_thread_run_id` → 当前 session 的 active version 归一；全源不可用时返回空串并要求调用方显式告警（`versioning.py:59`-`90`）。

## 设计取舍

- 运行级用固定 4 槽信号量而非动态伸缩：并发 run 数受浏览器资源限制，硬上限避免机器过载；代价是高峰触发 429，由 watchdog 自愈。
- 用例级强制串行：§10.1 红线把「正确性 > 速度」写死为默认，放弃批并发吞吐换「失败即停」可解释性。
- `run_id` 去随机化：牺牲唯一性换取重启后可从 `(project, version_label)` 反查历史，代价是同名重跑需调用方清理旧日志。

## 本章代码依据

- 入口：`step4_executor.py:202` `step4_execute`；候选分支 `:348`-`415`
- 执行槽：`_start.py:175` `_RUN_SLOT`；`:297` 非阻塞取槽；`:354` `_slot_watchdog`；`:193`-`197` 停滞预算常量
- 用例级：`base.py:288` `run_test_cases`；`serial.py:174` `_execute_all`；`:209`-`219` 强制 `max_parallel=1`
- 并行度自适应：`src/core/pipeline_config.py:891` `get_browser_parallel_recommendation`、`:893`、`:904-905`、`:911`、`:920-921`、`:947-954`；配置字段 `browser_parallel_auto_limit`/`browser_parallel_reserved_mb`/`browser_parallel_instance_mb`（`:358-359`）
- 路由：`browser_intent_parser/_steps.py:383` `steps_from_case`；`strategy_router.py:30` `_select_strategy`；常量 `:22`-`25`
- 落库：`audit.py:85` `_upsert_execution`、`:121` `record_execution`；`log_persistence.py:150`
- run_id：`versioning.py:24` `make_run_id`、`:58` `resolve_run_id`

## 待核实

- ~~`serial.py` 是否强制 max_parallel=1~~ → **已核实（2026-10-15）：属实。** `browser_core/serial.py:212-218` 实测：`max_parallel = pipeline_config.browser_max_parallel`（默认 2），`if max_parallel and max_parallel > 1:` 则打日志"§10.1 红线: 强制 max_parallel=1"并置 1。理由见 `:197-199` 注释：**并行模式下"失败即停"的 break 无法生效**。
- ~~`executions` 列名是否含 `failure_category`/`failure_chain`~~ → **已核实（2026-10-15）：三列均存在。** `src/core/schema.sql:230` `failure_chain TEXT`（失败链路 JSON）、`:231` `screenshot VARCHAR(512)`、`:235` `check_type VARCHAR(32)`（`pre_gate`/`step_exec`/…）。另注：本章"94 表"口径有误，实测行首 `CREATE TABLE` 为 **90** 条（另 4 处在 `:6/:1495/:1567/:1991` 注释行）。
- `retry_failed` 经 `_read_failed_intents` 取失败意图，其「失败」判定口径（status 取值）未在本次精读中确认。
