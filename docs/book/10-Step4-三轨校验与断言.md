# 第 10 章　Step4 · 三轨校验与断言

## 概述

Step4 的混合执行引擎在任何一步意图动作完成后，都调用同一个验证管道 `verify_step`（`src/pipeline/steps/step4/verification/__init__.py:610`）汇总 UI / API / DB 三条轨道的结果。三条执行策略（浏览器全链路、纯接口、Agent 探索）共用这条管道，差异只在于哪些轨道被声明（`required_tracks`）。

一条 check 从结构化描述到判定结果的完整路径如下：

```text
checks: { required_tracks, ui_checks[], api_checks[], db_checks[],
          consistency_check?, security_check?, ... }
   │
   ├─ verify_step 按 required_tracks 裁剪三轨（__init__.py:638-655, 816-819）
   │     ├─ ui 轨 → _verify_ui_objective(page, checks)        (__init__.py:685 → _db_ui.py:3018)
   │     ├─ api 轨 → _verify_api(...)                          (__init__.py:746 → _api.py:49)
   │     └─ db 轨 → _verify_db_async(db_checks)               (__init__.py:793 → _db_ui.py:151)
   │
   ├─ 每轨结果经 normalize_track 归一化（__init__.py:806 → tracks.py:73）
   ├─ 子集 AND：passed_tracks == len(required_tracks)         (__init__.py:817-818)
   ├─ _compute_confidence 打分（__init__.py:842）
   └─ decide_verdict 终判 PASS/FAIL/UNKNOWN（verdict.py:36）
```

`checks` 中每条 `check` 形如 `{track, precision, check, expect}`，`track∈{ui,api,db}`、`precision∈{exact,fuzzy}`。三条轨道均按同一协议 `TrackVerifier`（`_track_protocol.py:102`）执行：check 归一化 → 字典分派 → 逐条 verdict → 聚合。

## exact 与 fuzzy 的分流点

分流点在**每一条 check 的 `precision` 字段**，由 `_verify_ui_objective` 在 `_db_ui.py:3082` 读取（`precision = check.get("precision", "fuzzy")`，缺省 fuzzy）。该字段透传给每个 handler，由各 handler 自行决定规则判定还是 AI 判定。

- exact：走规则判定。以文本断言 `_handle_text_contains`（`_db_ui.py:219`）为例，exact 分支（`_db_ui.py:270-271`）只做逗号拆分、逐元素精确匹配，不做 2 字子词扩展——避免"科目名称"误匹配"科目"（`_db_ui.py:265-268` 根因记录）。
- fuzzy：走 AI 辅助判定。同函数 fuzzy 分支（`_db_ui.py:372`）先做 2 字子词扩展再比对 `body.innerText`，并排除输入框值（`_db_ui.py:387`）。语义型 check（`semantic_check`，`_db_ui.py:2162`）在机械命中不可判别时交由本地语义引擎与云端 LLM 兜底（`src/agent/explorer_mixins/_semantic.py`）。

`precision` 未被所有 handler 消费：状态类、样式类、SQL 类判定与 precision 无关，只做确定性比较。

## UI 轨断言：选择器、定位器与伪元素

UI 轨入口 `_verify_ui_objective`（`_db_ui.py:3018`）遍历 `ui_checks`，对每条 check 取 `inner.type` 作为 check 类型，按双查策略查 `_UI_CHECK_DISPATCH`（`_db_ui.py:2870-2967`）：先查原始键，未命中再查去掉 `_check` 后缀的键（`_db_ui.py:3085-3089`）。该表是 UI 断言执行器的**唯一注册表**，含 80+ 个类型到 handler 的映射（如 `text_contains`→`_handle_text_contains`、`element_exists`→`_handle_element_exists`、性能族 `performance_lcp_check`→`_handle_performance_metric_check`）。

未命中分派表的处理是 fail-closed 永久终态（`_db_ui.py:3098-3111`）：返回 `status="ERROR"`、`reason="UNSUPPORTED_CHECK"`、`passed=False`，不再像旧实现那样兜底 `passed=True` 制造假 PASS。

定位器解析分两层：生成端写入 `selector` 或 `target_hint`（带 `text=/placeholder=/label=/id=/name=` 前缀），执行端在 `browser_tools/_basic_locator.py` 与 `_fallback_locator.py` 解析为 Playwright 定位器。伪元素由 `_strip_pseudo_elements`（`_db_ui.py:44`）从选择器中剥离 `::before` / `::after` 等伪元素（`_db_ui.py:37` 的伪元素白名单），避免断言打到无法查询的伪节点。

渲染存在性兜底 `_assert_field_rendered`（`_db_ui.py:2970`）针对取值型断言（如 `text_contains`、`select_value_check`），校验目标元素存在、可见且读值非空；若模板未渲染该字段（render_blank），即便内存态匹配也翻转为失败（`_db_ui.py:3118-3142`），捕获"接口有数据但前端未渲染"的盲区（GAP-02）。

## DB 轨断言：SQL 生成与执行

DB 轨薄适配层 `_verify_db_async`（`_db_ui.py:151`）将 check 展开为值级断言（`_expand_db_value_assertions`，`_db_ui.py:68`）后交给 `DbTrackVerifier`（`_track_protocol.py:522`）。SQL 在 `verification/_verify_db_ext.py` 中按 check 类型生成：

| check 类型 | 生成 SQL | 位置 |
|:---|:---|:---|
| db_record_count_check | `SELECT COUNT(*) AS cnt FROM <table> [WHERE <where>]` | `_verify_db_ext.py:117-119` |
| db_record_not_exists_check | `SELECT COUNT(*) AS cnt FROM <table> WHERE <where>` | `_verify_db_ext.py:162` |
| db_record_exists_check | `SELECT COUNT(*) AS cnt FROM <table> [WHERE <where>]` | `_verify_db_ext.py:202` |
| db_field_value_check | `SELECT <field> AS fval FROM <table> WHERE <where> LIMIT 1` | `_verify_db_ext.py:258` |

所有 SQL 经安全校验：`validate_table_name` / `validate_column_name` / `validate_readonly_sql`（`_verify_db_ext.py:105,152,192,242,376`），只读校验（`_verify_db_ext.py:386`）禁止 INSERT/UPDATE/DELETE/DROP/ALTER。执行经 `DatabaseAgent.query`（`_track_protocol.py:636-637`），实际连接与驱动在 `src/extensions/db/`（见第 11 章）。

### DB 校验实现（`db_verifier` 包）

上文 `_verify_db_ext.py` 生成的是 SQL 文本，真正把 check 描述变成"查库—比对—结论"的实现在 `src/pipeline/steps/step4/db_verifier/`。统一入口 `verify_db(check_desc, use_ai=True)`（`__init__.py:56`），被 DB 轨的多个校验点调用（`verification/_track_protocol.py:663`、`_verify_db_ext.py:105/151/191/241/375`、`_verify_db_ui.py:30`、`_verify_time.py:86`、`api_only_executor.py:662/917`）；`verification/__init__.py:13` 把"DB 轨 → DbVerifier"登记为三轨之一，与本章 §DB 轨断言的薄适配层是**实现与调用**的关系（`DbTrackVerifier` 是协议壳，`verify_db` 是执行体）。

`verify_db` 支持 4 种校验格式，分派在 `verifiers.py`：

| 格式 | 入口 | 说明 |
|:---|:---|:---|
| 结构化 dict | `_verify_structured`（`verifiers.py:154`） | `{table, where, expect, field_check}`，支持字段级值校验（`field_check`）与精度/容差比较 |
| 原生 SQL | `_verify_sql`（`:271`） | 直接执行 `SELECT COUNT(*) ...` |
| 自然语言（正则） | `_verify_natural`（`:304`） | 解析表名 + WHERE |
| 自然语言（AI） | `_verify_db_ai`（`:26`） | AI 依意图生成 SQL → 执行 → 返回结果 |

只读安全阀在 `safety.py`：`_validate_sql_safe(sql)`（`:7`）与 `_validate_where_safe(where)`（`:46`）分别校验 SQL 与 WHERE 片段，拦截写操作。`test_data_lifecycle.py:13` 与 `_lifecycle_db.py:4` 均显式声明：数据生命周期的写操作**不得**复用 `db_verifier.safety._validate_sql_safe`——那是只读安全阀，不可放宽。包另含 `helpers.py`（精度比较/标识符引用/LIMIT/schema 摘要）与 `schema.py`（表结构自动加载，不依赖 `session_ctx`）。

## 汇总层校验规则族

三轨各自得到 `result` 之后、`decide_verdict` 终判之前，汇总层还会追加一族跨轨增强校验。它们不是「第四条轨道」，而是对同一份 result 的附加标注。这族规则的清单由 `_rule_meta.get_rule_meta_list()`（`_rule_meta.py:326`）给出，分三种 stage（`_STAGE_TAXONOMY`，`:33`）：

- `aggregation`：固定聚合层规则，来自代码中真实存在的 `_flag_*` / `infer_required_tracks` 等，逐个手工登记（`_AGGREGATION_RULES`，`:38`）；
- `checker`：自动发现的 `_verify_*.py` 断言校验器，扫目录 + 读模块 docstring 生成（`_discover_verify_checkers`，`:287`），零手工维护、与源码同步；
- `plugin`：浏览器执行插件类规则（定位器自愈/视觉基线/模糊匹配），随 plugins 系统运行。

登记的意义是把「引擎到底有哪些算法规则」变成可枚举、可前端展示的事实，而不是散落在各文件里靠人脑记忆；前端 `loadRuleList` 直接消费该结构。聚合层与插件层共 24 条（`_rule_meta.py:38`-`281`）：

| rule_id | 名称 | stage | 触发声明 | 语义 |
|:---|:---|:---|:---|:---|
| `track_inference` | 三轨推断与路由 | aggregation | 自动 | `required_tracks` 缺失时从各轨断言非空性兜底；UI 恒在，全无断言默认全三轨 |
| `track_protocol` | 单轨校验器协议 | aggregation | 自动 | 三轨统一产出 `status/passed/evidence/evidence_ref`，非 UNKNOWN 轨必须带证据引用 |
| `ui_objective_assert` | UI 客观断言 | aggregation | `ui_checks` | DOM 查询校验存在性/文本/属性/计数/网络/加载完成 |
| `consistency` | 跨轨一致性 | aggregation | `consistency_check` | UI/API/DB 三值两两精度容差比对；有效来源 < 2 → inconclusive |
| `api_contract` | 接口契约检测 | aggregation | `api_contract` | 响应结构校验（必填/类型/嵌套/数组长度），fail-open |
| `idempotency` | 幂等一致性 | aggregation | `idempotency.expect_repeat_same` | 同一操作调用 ≥2 次结果不一致 → 标 `[idempotency_violation]` |
| `concurrency` | 并发竞态 | aggregation | `concurrency.expect_consistent` | 并发窗口内 ≥`concurrency_hint` 次结果不一致 → 标 `[concurrency_race]` |
| `perf_slow` | 响应性能慢 | aggregation | `performance.max_ms` | 命中端点耗时 > 阈值 → 标 `[perf_slow]`（含实测 ms） |
| `network_anomaly` | 网络异常/弱网 | aggregation | `network` | 状态码不在期望 → `[network_anomaly]`；耗时超阈值 → `[network_slow]` |
| `mutation_scan` | 变异测试 | aggregation | `mutation_scan.keys` | baseline 全关 + 逐 key 激活双跑比对 → 有差异标 `[mutation_detected]` |
| `security_xss` | XSS 反射 | aggregation | `xss.check_reflected` | payload 被响应体原样反射 → `[xss_reflected]` |
| `security_leak` | 敏感数据泄露 | aggregation | `sensitive_leak` | 响应体/头出现声明模式 → `[sensitive_leak]` |
| `security_authz` | 越权绕过 | aggregation | `security_check.authz_gated` | 未授权身份调受保护端点却成功 → `[security_authz_bypass]` |
| `config_hidden` | 配置隐藏疑似 | aggregation | 自动 | UI 因 `[render_blank]` 失败且源轨有数据 → 加 `[config_hidden_suspect]` |
| `a11y` | 页面可访问性 | aggregation | `a11y.enabled` 或页面级默认 | **22 条规则（R1–R22）**（`extensions/a11y/checker.py:5-26`；前 8 条为 img-alt/label 关联/button 文本/heading 跳级/a-href/aria-label/重复 id/nested-interactive，2026-08-29 三次增强补至 R22）。注：`empirical/a11y_checker.py` docstring 仍写"8 条"，是规则扩容后未同步的旧文本 |
| `confidence` | 三轨置信度 | aggregation | 自动 | 按声明轨子集算多因子数值置信度，skipped 轨不计分母 |
| `dom_pruning` | DOM 智能剪枝（8 层） | aggregation | 自动 | 剪枝后仅保留交互元素并注入 `data-ai-id`/bbox（机制见第 9 章） |
| `url_match` | URL 归一化匹配 | aggregation | URL 类 check | 判断导航是否到达目标页（两处实现见下） |
| `verdict_fusion` | 三轨裁决融合 | aggregation | 自动 | 三轨子结果融为单条终判，未声明轨按 UNKNOWN 跳过 |
| `db_assert` | DB 轨数据断言 | aggregation | `db_checks` | 值/行数/存在性真实查询校验 |
| `api_schema_diff` | 接口契约演进对比 | aggregation | 历史/当前 schema | 识别响应结构增减/类型漂移等破坏性变更 |
| `self_healing` | 定位器自愈 | plugin | 定位失败 | 记录错配证据，多候选策略重定位 |
| `visual_baseline` | 视觉回归基线 | plugin | 截图比对 | 建基线并在后续执行对比差异 |
| `fuzzy_match` | 模糊语义相似度 | plugin | 跨命名定位 | token 化 Jaccard + 语义相似度 |

其中 `consistency`/`api_contract`/`idempotency`/`concurrency`/`perf_slow`/`network_anomaly`/`mutation_scan`/`security_xss`/`security_leak`/`security_authz`/`a11y` 属同一模式：**fail-open**——默认只向 `evidence`/`summary` 追加标记，不改 `passed`；只有用例显式 opt-in（`fail_on_violation`/`fail_on_race`/`fail_on_slow`/`fail_on_network`/`fail_on_mutation`/`fail_on_xss`/`fail_on_leak`/`fail_on_bypass`/`fail_on_a11y`）才硬失败。这是「增强维度默认不误杀、要它当门禁就显式声明」的取舍，与三轨 AND 语义（声明轨必须过）刻意分开。

### 几条规则的判定语义

**幂等（`_flag_idempotency`，`_op_quality.py:109`）。** 只在声明 `expect_repeat_same` 时生效；「同一操作」的标识是 `URL(去 query) + method 大写`（`_op_quality.py:84`）。要求同一操作被观测到 ≥2 次调用才判定，仅一次或端点未观测到 → 不标、不记证据（`:196`）。

**并发竞态（`_flag_concurrency`，`:516`）。** 声明 `expect_consistent` 后，要求同一操作在「并发窗口」内被调用次数 ≥ `concurrency_hint`（默认 2，`:530`、`:572`）；窗口内调用数不足 → 不判竞态，只在证据里说明（`:648`）。它与幂等共用「同操作标识」，但多了时间窗口这一维。

**性能慢与弱网的分工（`_flag_performance`，`:302`；`_flag_network`，`:689`）。** 两者都读命中断点的调用耗时，差别在触发位：`performance.max_ms` 只回答「慢不慢」（`[perf_slow]`），`network` 同时管状态码与耗时（`[network_anomaly]` + `[network_slow]`）。性能慢缺 `elapsed_ms` 时返回「无法判定」而非默认通过（`:381`）。

**变异测试（`mutation_scan`）。** 它回答的是「注入缺陷激活后，引擎的既有观测是否感知到差异」。判定依赖驱动侧对同一用例做 baseline 全关 + 逐 key 激活的双跑，比对同一端点调用差异（`_rule_meta.py:130`-`139`）。也就是说这项能力一半在汇总层、一半在驱动侧，单靠一次执行观测不到。

**接口契约的三层边界。** `_api_contract.py` 文件头把边界写得很清楚（`:10`-`23`）：`api_response_body_check` 是「值级」比较（`_verify_api_ext._check_response_body`）；`api_contract_check` 是 fail-closed 的「check 级」扁平契约（`_verify_contract.verify_api_contract`，声明即必须满足，无匹配请求或响应体为空 → FAIL，不允许假通过）；`_flag_api_contract` 是 fail-open 的「汇总层」结构契约，支持嵌套路径（`data.items[0].id`）与数组结构（`array_ok`/`min_items`/`max_items`），并区分「路径不存在」与「存在但值为 null」（`_resolve_path`，`:40`）。三层不重复造轮子：各有明确分工，默认行为不同。

**接口契约演进（`diff_contract_schemas`，`contract_builder.py:556`）。** 对历史与当前 schema 做差异对比，产出 `breaking_changes`/`non_breaking_changes`/`is_breaking`（`:571`-`573`）。判为 breaking 的包括字段移除、类型漂移、新增 required 字段等；新增普通字段与 required 移除算向后兼容（`:658`-`672`）。schema 加载入口 `load_openapi_schemas`（`:257`）兼容 OpenAPI 形态。

**URL 匹配有两处实现，不可互相替代。** `_url_match._match_url_pattern`（`_url_match.py:13`）只做三种模式——包含、通配符 `*`、正则（`^` 开头），供 API 端点匹配与契约匹配使用，不做 query/hash/尾斜杠归一。UI 轨「导航是否到达目标页」走另一处（`_db_ui.py:550`-`614`）：按 `precision` 分治，`exact`/`prefix` 用 `urlparse` 取 path、与当前页同源时把全 URL pattern 归一为 path、`rstrip("/")` 后比较，`prefix` 还把「目标路径本身或其子路径」视为到达（路径段级前缀，避免 `/assets` 误配 `/assetsfoo`）；`fuzzy` 保持全 URL 正则搜索。

## 深断言执行器与普通断言的区别

普通断言（`verify_step` 管道）是**快照式逐条判定**：在动作完成后的当前 DOM / 最近一次 API 流量 / 当前 DB 状态上，对每条结构化 check 求值。`_verify_ui_objective`（`_db_ui.py:3018`）即此类。

深断言是**实证式整用例驱动**，由 `DeepAssertionRunner`（`empirical/deep_assertion_runner.py:125`）按 `target_cfg["mode"]` 分派到 `BrowserAssertionRunner`（`deep_assertion_runner.py:194`）。它真正拉起浏览器、填表、提交、删除，并克服分页失真——列表总数必须 `_count_total` 逐页累加（`deep_assertion_runner.py:704`），不能用当前页行数静默降级（`PAGER_RE`，`deep_assertion_runner.py:49`）。它如实区分原生校验拦截与后端拒绝：`blocked_by="browser_native_validation"` / `"client_validation"`（`deep_assertion_runner.py:24-29`）。编排由 `TargetEmpiricalVerifier`（`empirical/target_empirical.py:6`）串起 `BrowserBackendResolver → SutHarness → DeepAssertionRunner → EmpiricalReportWriter`。

二者边界：普通断言在混合执行管道内对既有状态做规则/AI 判定；深断言针对 P01/N01/B01 这类需要交互推进的场景，在活靶子上跑完整流程。深断言文件头标注 `[未复核-需 GLM/DeepSeek 把关]`（`deep_assertion_runner.py:5`），其结论是否回写同一 `decide_verdict` 管道见待核实。

## 判定结果归一化：PASS / FAIL / SKIP / ERROR

三轨状态枚举 `TrackStatus`（`tracks.py:26-31`）只有 PASS / FAIL / UNKNOWN 三值，`normalize_track`（`tracks.py:73`）保证每轨带 `status` 并执行构造校验（非 UNKNOWN 轨必须带 `evidence_ref`，`tracks.py:54-70`）。

| 终态 | 判定条件 | 代码位置 |
|:---|:---|:---|
| PASS | 所有声明轨 `passed=True`，且无轨 UNKNOWN；`decide_verdict` 中 `confidence ≥ 0.70` | `verdict.py:99-100`；阈值 `verdict.py:28` |
| FAIL | 任一声明轨失败；或 `decide_verdict` 中 `confidence < 0.70`（假 PASS 拦截）；或 DB check `evidence="unverified"` | `verdict.py:91-97`；`_db_ui.py:174-178` |
| SKIP | 轨道 `skipped=True`（声明未执行/全 check 非判定态）；或逐条 check 无有效结论（`no_verdict_only`） | `_track_protocol.py:287`（`all_skipped`）；`_db_ui.py:3196-3198`；`_verify_db_async:179-183` |
| ERROR | check 类型未注册（`UNSUPPORTED_CHECK`）或 handler 异常（`EXECUTOR_EXCEPTION`） | `_db_ui.py:3105-3111`；`_track_protocol.py:216-270` |

`decide_verdict`（`verdict.py:36`）是**终判唯一入口**，规则顺序固定：非 PASS 状态不二次改判（`verdict.py:62-69`）→ 任一轨 UNKNOWN 降级 UNKNOWN（`verdict.py:71-81`）→ confidence 缺失降级 UNKNOWN（`verdict.py:84-88`）→ confidence 不足判 FAIL（`verdict.py:91-97`）→ 否则 PASS。阈值 0.70 为代码常量加契约下限，配置只可上调不可下调（`verdict.py:54-59`）。

逐条 check 的三值聚合由 `aggregate_check_results`（`_track_protocol.py:846`）给出 `has_positive` / `has_negative` / `no_verdict_only`，供上游区分"产品缺陷 FAIL"与"无结论 SKIP"，避免 `all(r["passed"])` 把跳过态算作通过（`_track_protocol.py:733-746` 根因说明）。

## 结果级红队对抗验证（adversarial）

三轨校验给出的是**单条用例级** PASS/FAIL，但"判定本身是否可信"需要第二道独立审查。`src/pipeline/steps/step4/browser_ai_validator/_adversarial.py` 提供结果级红队对抗验证 `adversarial_review_results(execution_results, candidates)`（`:341`），由 Step4 执行编排在结果汇总后调用（`src/pipeline/step4_executor.py:1069`/`:1077`），**不修改原执行引擎**（`:4-5`）。

三个验证器（文件头 `:7-10`，模型统一取 `config.verification_model`）：

| 验证器 | 职责 | 触发信号 |
|:---|:---|:---|
| A 证据一致性 | 证据是否支撑 pass/fail | passed 却无任何证据条目→疑似空执行漏报；failed 却步骤全 passed→硬校验假阳性；步骤耗时 <0.1s→疑似跳过；步骤描述与 intent_title 不匹配（`:55-58`） |
| B 误判审查 | passed 是否可能漏报 | 边界/异常/超时场景 |
| C 可靠性评估 | 执行过程异常信号 | 重试/超时/元素未找到；单步 > `_SLOW_STEP_SECONDS`（8.0s）记为慢（`:45`） |

- **免费通道优先**：Verifier C 结构化、不调 AI，最先执行（`:398`）。仅当样本数 ≥ `_MIN_REVIEW_SAMPLES`（3）且（平均可靠性 < `_FAST_RELIABILITY_THRESHOLD` **或**存在失败用例）时才触发 AI 审查（`:27-28`、`:401-404`），最多送 `_MAX_REVIEW_SAMPLES`（20）条。
- **输出**：`{success, reliability_score, evidence_consistency, false_positive_review, reliability_assessment, anomalies, verdicts{reliable/flaky/unreliable}}`（`:346-358`）。
- **零硬编码 + 异常安全**：模型名从 `config.verification_model` 读取，缺省回退 `config.ai_model`（`:360-363`）；任一验证器失败降级跳过，不阻断主流程（`:12-13`）。快速通道阈值走 `pipeline_config.adversarial_fast_reliability`（`:32-41`，缺省 0.85）。

与本章 §「判定结果归一化」的关系：红队验证**不参与** `decide_verdict` 的终判，而是对终判结果做独立复核，产出可靠性评分与 `reliable/flaky/unreliable` 归类，用于报告层的置信度标注。

## 设计取舍

- 三轨共享 `TrackVerifier` 协议而非各自实现，使 UI 轨的 `_UI_CHECK_DISPATCH` 与 DB 轨的 `DbTrackVerifier` 走同一套"归一化—分派—聚合"骨架，新增 check 类型只需注册一次。
- 未注册 check 类型从 fail-open（`passed=True`）改为 fail-closed（`ERROR`），牺牲少量"生成端误发新类型即全绿"的宽容度，换得假 PASS 的根除。
- 终判收拢到 `decide_verdict` 单一函数，放弃历史上 plan 期硬编码、门禁、scorecard 多处各判 PASS 的并行路径，消除同一用例结论不一致。
- exact 不做子词扩展、fuzzy 做扩展，是按"精确字段值 vs 语义包含"语义分治，而非统一模糊，避免精确匹配被 2 字碎片击穿。

## 本章代码依据

- 主编排：`verification/__init__.py:610`（`verify_step`）、`:638-655`（轨道裁剪）、`:816-819`（子集 AND）、`:842`（置信度）
- UI 分派：`verification/_db_ui.py:2870`（`_UI_CHECK_DISPATCH`）、`:3018`（`_verify_ui_objective`）、`:3082`（precision 读取）、`:3098-3111`（UNSUPPORTED_CHECK）、`:2970`（`_assert_field_rendered`）、`:44`（`_strip_pseudo_elements`）
- DB 生成：`verification/_verify_db_ext.py:117-258`、`verification/_track_protocol.py:522`（`DbTrackVerifier`）、`:636`（query）
- 协议与状态：`verification/_track_protocol.py:102`（`TrackVerifier`）、`:846`（`aggregate_check_results`）、`verification/tracks.py:26`（`TrackStatus`）、`:73`（`normalize_track`）
- 终判：`verification/verdict.py:28`（阈值）、`:36`（`decide_verdict`）
- 深断言：`empirical/deep_assertion_runner.py:125`、`:194`、`:704`；`empirical/target_empirical.py:6`
- 规则族：`verification/_rule_meta.py:326` `get_rule_meta_list`、`:33` `_STAGE_TAXONOMY`、`:38` `_AGGREGATION_RULES`、`:287` `_discover_verify_checkers`
- 汇总层判定：`verification/_op_quality.py:109` `_flag_idempotency`、`:516` `_flag_concurrency`、`:302` `_flag_performance`、`:689` `_flag_network`、`:84` 同操作标识
- 契约：`verification/_api_contract.py:10`-`23` 三层边界、`:40` `_resolve_path`；`verification/contract_builder.py:257` `load_openapi_schemas`、`:556` `diff_contract_schemas`
- URL：`verification/_url_match.py:13` `_match_url_pattern`；`verification/_db_ui.py:550`-`614` UI 轨导航 URL 归一
- 一致性：`verification/_consistency.py:52` `verify_consistency_async`
- 红队对抗：`src/pipeline/steps/step4/browser_ai_validator/_adversarial.py:341` `adversarial_review_results`、阈值 `:27-28,45`、免费通道 `:398`、AI 审查触发 `:401-404`、输出结构 `:346-358`；接入 `src/pipeline/step4_executor.py:1069,1077`；配置 `pipeline_config.adversarial_fast_reliability`
- DB 校验实现：`src/pipeline/steps/step4/db_verifier/__init__.py:56` `verify_db`；`verifiers.py:26,154,271,304`（AI/结构化/SQL/自然语言）；`safety.py:7,46`（`_validate_sql_safe`/`_validate_where_safe`）；调用点 `verification/_track_protocol.py:663`、`_verify_db_ext.py:105,151,191,241,375`、`_verify_db_ui.py:30`、`_verify_time.py:86`、`api_only_executor.py:662,917`

## 待核实

- `deep_assertion_runner.py` 与 `target_empirical.py` 文件头标注 `[未复核-需 GLM/DeepSeek 把关]`，其结论是否汇入 `decide_verdict` 同一终判管道未确认。
- `aggregate_check_results` 的 `no_verdict_only` 在 `_verify_ui_objective` 中映射到 `no_valid_assertion`（`_db_ui.py:3196-3198`），该标志最终如何呈现为章节所述的 SKIP（而非 FAIL）依赖上游报告层消费，未逐行核对报告层。
- `precision` 字段是否在所有 UI handler 内都被消费未穷举（状态/样式/SQL 类 handler 源码未逐一确认其 precision 分支）。
- `consistency_check`、`security_check` 等跨轨增强为 fail-open 标记，opt-in 才硬失败（`__init__.py:837-961`），其默认不计入子集 AND 的边界条件需结合 Step5 报告层确认。
- `_rule_meta` 中 `step4.url_match` 的描述（`_rule_meta.py:212`-`219`）把 `_match_url_pattern` 与 UI 轨 URL 归一合述为「忽略通用 query 顺序/hash/尾斜杠差异」，与 `_match_url_pattern` 的实际实现不符——该能力只存在于 UI 轨（`_db_ui.py:550`-`614`），`_match_url_pattern` 仅做包含/通配/正则。此条为登记描述的过度宣称，未修改源码，仅在此标注。
- 汇总层各 `_flag_*` 规则在真实批量执行中的触发率未统计（哪些规则实际被用例声明并命中），本次仅做静态语义核对。
- 红队验证结果的**下游消费**未逐行确认：本次核到 `step4_executor.py:1069`/`:1077` 调用并把 `adv_report` 挂回汇总，但该报告如何进入 Step5 报告/置信度标注（是否落 `oracle_results` 或仅内存传递）未追到消费端，待核。
- 红队验证的**实测状态**：本次仅静态读码，未在真实轮次中观测三个验证器的触发与 `verdicts` 分布（尤其 `flaky`/`unreliable` 是否实际产出）。
- `db_verifier.verify_db` 的**四格式分派优先级**未逐行精读：`check_desc` 为 dict / 原生 SQL 字符串 / 自然语言时如何判定走哪条（`verifiers.py` 内的选择条件），以及 `use_ai=False` 时自然语言路径的降级行为，待核。
