# 第 12 章　Step5 · 报告与补测建议

## 概述

Step5 的入口是 `generate_report(project_name, confidence_results, generate_html, run_id)`，位于 `src/pipeline/steps/step5/report_generator.py:55`。它把 Step4 落库的执行结果聚合为一份 Markdown 报告，可选地再渲染一份 HTML，并把正文持久化到 MySQL。

模块顶部 docstring（`report_generator.py:2-11`）写明输入是 `Validation_Intent-*.xlsx` 与 `requirements_breakdown.xlsx`。这是过时描述：实际读取路径 `_read_report_intents` / `_read_report_requirements`（`_db_utils.py:105`、`_db_utils.py:124`）分别调用 `case_store.list_intents_with_meta()` 与 `requirement_store.list_requirements()`，数据源为 MySQL。本章以当前代码为准。

报告按固定顺序拼接若干 Markdown 区块（`lines` 列表，最终 `"\n".join`，`report_generator.py:1165`），区块是否出现受 `pipeline_config` 开关控制。

## 报告输入：读取的表与字段

| 表 | 读取函数 | 关键字段 | 用途 |
|:---|:---|:---|:---|
| `test_cases` + `case_meta` | `_read_report_intents` → `list_intents_with_meta`（`_db_utils.py:105`） | `case_id`、`catalog_name`、`case_type`、`risk_level`、`verification_time`、`payload`（`last_intent_verdict`、`step_results[].{passed,description,error,ui_checks/api_checks/db_checks[].{check_id,check,expect,precision,actual}}`）、`expected_outcome`、`precondition`、`req_id` | 用例主数据、明细、证据 |
| `requirements` | `_read_report_requirements` → `list_requirements`（`_db_utils.py:124`） | `req_id`、`operation`、`expected_feedback`、`module`、`is_generated`、`payload_json.source_quote` | 需求覆盖与原文追溯 |
| `case_requirement_mapping` | `_load_source_quotes_for_cases`（`_db_utils.py:133`） | `case_id→req_id` | 失败用例反查需求原文 |
| `executions` | `get_execution_status_map`（`_db_utils.py:30`）、`_load_execution_confidence_from_db`（`_db_utils.py:42`）、`build_bug_report`（`_bug_report.py:183`）、`attribution`、`_find_flaky_cases`（`_manual_retest.py:210`） | `status`、`confidence`、`failure_reason`、`failure_category`、`failure_chain`、`check_type`、`expected`、`actual`、`details`、`screenshot`、`executed_at` | 状态、置信度、Bug、归因、Flaky |
| `capability_cache` / `capability_cache_events` / `capability_cache_policy` | `report_generator.py:380-469` | `is_valid`、`success_count`、`failure_count`、`hit_count`、`event_type` | 能力库治理概览 |

注意：`run_logs` 表（含 `step_num`）在本章覆盖的 `src/pipeline/steps/step5/` 全部模块中无任何读取调用（递归 Grep 无命中）。状态事实源是 `executions.status`，不是 `run_logs`。

## 输出结构与文件格式

主产物是 Markdown 字符串 `report_content`（`report_generator.py:1165`）。`persist_report`（`_report_persist.py:76`）把正文写入 MySQL `generated_reports`（`report_type='report'`，`_report_persist.py:121-136`），并回写 `run_summary` 表。

`generate_html=True` 时，`_generate_html_report`（`_html.py:15`）把同一份 Markdown 包成 HTML 文档，`save_html_report`（`_html.py:145`）写入 `generated_reports`（`report_type='html_report'`），失败时回退到临时文件。

函数返回 `dict`（`report_generator.py:1224`）：`{success, report_path, stats, report_markdown, [html_report_path]}`。`stats` 仅含汇总计数（`total/passed/failed/flaky/flaky_rate/skipped/pending`）。本模块不直接产出 Excel 或落盘 JSON 文件；`build_bug_report` 返回的 `dict` 可被序列化，但未以文件形式写出。

## 报告区块

区块按拼接顺序如下，受开关控制的默认关闭，仅 `SECURITY_REPORT_ENABLED`（默认 True，`report_generator.py:1104`）与 `CHANGE_IMPACT_KB_ENABLED`（默认 True，`report_generator.py:1142`）默认开启。

- **总览统计**：执行概览表（`report_generator.py:156-167`）、目录分布与 SVG 条形图（`:175-211`）、智能指标（`:817`）、能力库治理概览与复用情况（`:441`、`:473`）。
- **逐用例明细**：意图执行验证明细（`:214`）、意图与证据裁决（`:278`）、Check 粒度验证明细（`:343`，遍历 `step_results[].{ui,api,db}_checks`）、失败用例明细含 `source_quote` 原文追溯（`:496-531`）。
- **缺陷报告（Bug）**：`## Bug 清单 / 缺陷` 区段（`render_bug_report_section`，`report_generator.py:1152-1162` 无条件渲染）。
- **补测建议**：`## 人工补测建议清单`（`generate_manual_retest_section`，`report_generator.py:564-567`）。
- **横切质量门禁结果**：幂等（`IDEMPOTENCY_ENABLED`，`:1030`）、性能基线（`:1055`）、性能门禁（`:1064`）、性能趋势、可访问性 a11y（`:1095`）、安全 security（`:1104`）、视觉 visual（`:1113`）、假 PASS 审计（`:1120`）、引擎健康（`:1131`）、变更影响知识库（`:1142`）。各区块为配置门控的纯读聚合，闭合失败时 fail-open 不阻断主报告。
- 另含跨轨一致性报告（`:599`）、追溯校验防幻觉（`:664`）、版本对比（`:770`）、蓝红对抗分析（`:894`）、报告数据自校验（`:990`，对比 `executions` 按 `run_id` 的 `COUNT(status)`）。

## 通过率计算

代码位置：`report_generator.py:95-121`，由 `_get_status(c)`（`report_generator.py:83`）从 `verification_status_map` 取状态。

**粒度：按用例（case / intent），不按 check。** 所有计数都在 `intents`（每个元素是一条用例）上 `sum(1 for c in intents if ...)`：

```
total   = len(intents)                                    # :95
passed  = count(status == "PASS")                          # :96
failed  = count(status == "FAIL")                          # :97
flaky   = count(status == "FLAKY")   # 单列，不进分母      # :98
skipped = count(status == "SKIP")                          # :99
blocked = count(status in {BLOCKED, PRE_GATE_BLOCKED})     # :92-100
# _is_executed = {PASS, FAIL, SKIP}                        # :87
# _is_blocked  = {BLOCKED, PRE_GATE_BLOCKED}               # :92
pending = count(not executed and not blocked)              # :101，含 FLAKY/ERROR/空状态
_tpr_base = total - pending                               # :109
if ATTRIBUTION_STRICT_TPR: _tpr_base -= blocked           # :110-111
pass_rate = passed / _tpr_base * 100                      # :114-118
```

结论（代码事实）：

- **SKIP 计入分母**：SKIP 属于 `_is_executed`（`report_generator.py:87`），因此 `pending` 排除 SKIP，`_tpr_base` 含 SKIP。
- **FLAKY 与 ERROR 不计入分母**：两者都不在 `_is_executed` 也不在 `_is_blocked`，落入 `pending`（待验证）。主概览表没有 ERROR 单独的桶，ERROR 状态被当作 pending 处理。
- **默认口径分母 = PASS+FAIL+SKIP+BLOCKED**（FLAKY/ERROR 除外）。设 `ATTRIBUTION_STRICT_TPR=True` 时再剔除 blocked（`report_generator.py:110-111`）。
- 失败归因区块有独立通过率：默认 `denom = total`，严格模式 `denom = total - blocked - pre_gate_blocked`（`attribution.py:192-196`），由 `render_attribution_section` 渲染（`:24-25`）。两套分母定义不同，见「设计取舍」。

## 人工补测建议清单

生成函数 `generate_manual_retest_section`（`_manual_retest.py:17`），调用点 `report_generator.py:564-567`，入参为 `intents`、`reqs`、`failed_intents`。

建议项由 `_collect_retest_suggestions`（`_manual_retest.py:66`）三类来源汇总，按 `_RISK_ORDER = {P0,P1,P2,P3}` 排序（`:86`）：

1. **未覆盖需求**（`_find_uncovered_reqs`，`:92`）：`req_id` 无对应用例且 `is_generated` 为假。风险由 `_classify_req_risk`（`:127`）按 `operation` 关键词判定——P0：新增/编辑/删除/create/edit/delete/update/submit 等；P1：查询/搜索/导出/审批/search/export 等；P2：展示/查看/view/display 等；否则 P3。建议表述为「补正向用例（参照需求三元组操作+预期）」。
2. **失败用例**（`_find_failed_cases`，`:172`）：遍历 `failed_intents`，`failure_category == "HARNESS_ERROR"` 跳过（执行系统问题，不需人工补测）。`PRODUCT_BUG` 且风险高/P0 → P0，否则 P1；其余 → P2。建议表述：`PRODUCT_BUG` 为「人工复现并验证是否为系统bug」，否则「检查环境/数据问题」。原因含 `failure_category` 与截断的 `_failure_reason`。
3. **Flaky 用例**（`_find_flaky_cases`，`:210`）：直接查 `executions WHERE status='FLAKY'`，统一 P1，建议「人工复跑确认稳定性」。

渲染 `_format_retest_section`（`:248`）：无建议时输出「所有需求和用例均已覆盖，无需人工补测。」；有则按 P0–P3 分节列出 `| ID | 类型 | 模块 | 原因 | 建议操作 |`，末尾附等级汇总。

## Bug 报告字段结构

`build_bug_report`（`_bug_report.py:158`）取 `executions WHERE status IN ('FAIL','ERROR')`，列见 `_bug_report.py:183-192`。每条 Bug 的字段：

- 身份：`execution_id`、`project`、`case_id`、`check_type`、`status`
- 严重级：`severity`、`severity_rule`（`_bug_report.py:242`，说明命中哪类规则）
- 复现：`repro_steps`、`expected`、`actual`、`minimal_repro`（最小复现，`converge_repro_strings`，`minimal_repro.py`）
- 根因：`root_cause_category`（= `failure_category` 或 `"unclassified"`，`:247`）、`failure_reason`、`failure_chain`、`failure_triage`
- 其他：`screenshot`、`executed_at`、`title`

**严重级规则**（`classify_severity`，`_bug_report.py:63`，关键字常量 `_HIGH_KEYS:55`、`_MEDIUM_KEYS:59`、`_LOW_KEYS:60`）：命中 `insert/update/delete/写库/数据破坏/注入/越权/权限/sql/安全/登录/支付/创建/提交/核心流程` → `high`；命中 `文案/样式/拼写/显示/错别字/颜色` → `low`；命中 `性能/校验/边界/状态/并发/超时` 或未命中明确关键词 → `medium`（默认 `medium`）。

**根因三分类**：`failure_triage` 由 `build_failure_triage`（`_triage.py`，`_bug_report.py:50` 导入）产出，`category ∈ {ENGINE, SUT, ENV, UNKNOWN}`，含 `root_cause_chain`（≥3 层，每层带 `evidence_ref`）。证据不足时 fail-open 归 `UNKNOWN`，不硬造。

**最小复现步骤**：`minimal_repro` 收敛为 `preconditions / key_steps(≤3) / trigger / assertion / convergence_ratio`，渲染为「最小复现（N 步，收敛率 X%）」（`_report_sections.py:331-339`）；无可回放步骤时如实标注「无可回放步骤」。

SKIP 单列于 `skipped` 列表（非缺陷，`_bug_report.py:256-269`）。Bug 区段由 `render_bug_report_section`（`_report_sections.py:235`）渲染，含严重级分布表、逐条 `失败归属 + 根因 + 最小复现 + 可提单` 信息。

## 用例来源区分

报告本身**不消费用例来源**。`attribution.build_attribution_report`（`attribution.py:64`）仅按 `status` 与 `failure_category` 聚合，不携带作者维度。`report_generator.py:65` 的 `_case_source` 是 `_read_report_intents` 的返回值，其值为字面量 `"mysql"`（`_db_utils.py:121`），表示数据来自 MySQL，而非用例由谁编写。在 `src/pipeline/steps/step5/` 目录内检索 `AI生成 / 人工编写 / 内置兜底 / builtin / generated_by / source_type` 无业务命中。

但需要澄清一点，避免把"报告未展示"误读成"数据不存在"：**`test_cases` 表有 `case_source` 列，且被真实写入。** 该列在用例落库时赋值，默认值为 `"generated"`（`src/pipeline/utils/case_store/crud/write.py:399`），另存在以下取值：

| 取值 | 含义 | 写入点 |
|:---|:---|:---|
| `generated` | 引擎生成（默认值） | `src/pipeline/utils/case_store/crud/write.py:399` |
| `inherited` | 跨版本继承 | `src/pipeline/step3/_requirement_selector.py:153-169` |
| `inherited_virtual` | 继承得到的虚拟用例 | `src/pipeline/utils/case_store/crud/base.py:122` |
| `manual_override` | 人工覆盖 | `src/core/excel_cases/_update.py:40-41` |

也就是说，"用例来源"在数据层是**已记录**的事实，只是没有进入 Step5 报告的输出。若要实现"报告标注用例来源"，不需要新增采集逻辑，只需要让报告层读取这个已有列。

`idempotency.compare_case_runs` 返回的 `source`（`param_error` / `insufficient_runs` / `recent_two`）指运行来源，与用例作者无关。

## 设计取舍

- **Markdown 为先、HTML 为派生**：正文始终先拼成 Markdown，HTML 只是包装层（`_html.py:15`），保证两种格式同源、不漂移。
- **主概览通过率与归因通过率分母不一致**：主通过率分母 `= PASS+FAIL+SKIP+BLOCKED`（排除 FLAKY/ERROR），归因区块分母默认 `= total`（严格模式 `= total - blocked - pre_gate_blocked`，`attribution.py:192-196`）。两者都标「通过率」，口径不同，阅读时需区分上下文。
- **ERROR 在主概览被归为 pending**：`executions.status='ERROR'` 不在 `_is_executed` 集合，因此不计入通过率分母，也不出现在概览表；而 Bug 报告与归因区块把 ERROR 视为失败/缺陷。同一状态在三处语义不同。
- **补测建议不调 AI**：`_manual_retest.py:7` 注明「规则优先」，风险分级为纯关键词规则，避免建议生成引入不确定性。
- **门控区块 fail-open**：a11y/perf/security 等横切区块默认多数为 OFF，异常时返回空或提示，不影响主报告交付。

## 本章代码依据

- 入口与统计：`report_generator.py:55`、`report_generator.py:83-121`、`report_generator.py:156-167`
- 输入读取：`_db_utils.py:30`、`_db_utils.py:42`、`_db_utils.py:105`、`_db_utils.py:124`、`_db_utils.py:133`
- 持久化与 HTML：`_report_persist.py:76`、`_report_persist.py:121`、`_html.py:15`、`_html.py:145`
- 补测建议：`_manual_retest.py:17`、`_manual_retest.py:66`、`_manual_retest.py:92`、`_manual_retest.py:127`、`_manual_retest.py:172`、`_manual_retest.py:210`、`_manual_retest.py:248`
- Bug 报告：`_bug_report.py:63`、`_bug_report.py:55/59/60`、`_bug_report.py:158`、`_bug_report.py:183`、`_report_sections.py:235`、`_report_sections.py:331`
- 归因与根因三分类：`attribution.py:64`、`attribution.py:192`、`_report_sections.py:14`、`_triage.py`（import `_bug_report.py:50`）
- 用例来源：结论见「用例来源区分」节，检索覆盖 `src/pipeline/steps/step5/`

## 待核实

1. ~~`report_generator.py` 是否仍保留 Excel 读取分支~~ → **已核实（2026-10-15）：无 Excel 分支，docstring 是过时注释。** 在 `src/pipeline/steps/step5/` 全目录检索 `openpyxl` / `read_excel` / `.xlsx` **零命中**；该模块实际从 MySQL 读取。建议把 `:5` 的"从 Validation_Intent-*.xlsx 和 requirements_breakdown.xlsx 读取数据"改为实际数据源，避免读者照 docstring 去找输入文件。
2. ~~三个报告层开关的真实默认值~~ → **已核实（2026-10-15）：三者默认均为 `False`，即默认全部关闭。** `ATTRIBUTION_STRICT_TPR`：`attribution.py:87` 与 `report_generator.py:106` 均为 `_cfg(..., False)`；`ATTRIBUTION_ENABLED`：`attribution.py:222` 与 `report_generator.py:1019` 为 `bool(_cfg(..., False))`；`IDEMPOTENCY_ENABLED`：`report_generator.py:1030` 为 `bool(_cfg(..., False))`。含义：默认口径分母 = PASS+FAIL+SKIP+BLOCKED（不剔除 blocked）；归因模块与主报告幂等默认不启用。`PERF_*` / `A11Y_*` 两组仍待核实。
3. `run_logs`（`step_num`）在 Step5 是否应被消费、消费点是否在 step5 之外，需跨目录确认其归属。
4. ~~`executions` 是否确含 `failure_chain`/`check_type`/`screenshot`~~ → **已核实（2026-10-15）：三列拼写均正确。** `src/core/schema.sql:230` failure_chain、`:231` screenshot、`:235` check_type。其中 `check_type` 由 `:1607-1608` 的 ALTER 补入（注释标注 2026-07-12 修复"scheduler 门禁写入失败"）。
