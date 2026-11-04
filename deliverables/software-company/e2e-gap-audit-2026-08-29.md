# E2E 智能测试引擎 —— 四目标差距审计 + 补丁式垃圾代码识别

- 审计人：高见远（软件架构师）
- 日期：2026-08-29
- 范围：只读审计（读代码 / git log / MySQL 只读；未修改任何源码与数据）
- 项目根：`D:\AI\AI_Test_Preject`；引擎 `src/backend/app.py`（waitress，端口 5009）；MySQL `ai_test_engine`
- 活跃靶子：`target_systems/hospital_sut`（Flask+Jinja2+SQLite，5080，admin/admin123）

> 说明：审计过程中执行了 `python -m compileall -q src`（仅生成 Python 标准 `.pyc` 缓存，不修改源码/数据）与若干 `import` 冒烟测试（模块级 import 会打印 INFO 日志，不写库）。特此声明，不影响审计结论。

---

## 0. 真实状态基线（2026-08-29 MySQL 只读核实）

| 项 | 值 | 证据 |
|---|---|---|
| executions 总数 | 52 | `SELECT status,COUNT(*) FROM executions GROUP BY status` |
| hospital_sut | PASS=21 / SKIP=10 / PRE_GATE=2（pre_gate_step4/5 哨兵） | 同上按 project 过滤 |
| api_demo_sut | PASS=16 / PRE_GATE=3 | 同上 |
| test_cases / case_meta / requirements | 51 / 53 / 19 | COUNT(*) |
| ai_generation_logs | 39 条，**全部 source='hallucination_logger'** | DESC + SELECT（记录的是"AI 幻觉/失败"，不是 AI 调用计数） |
| op_cache_v2 | 22 条 | COUNT(*) |
| nav_graph_edges / nav_graph_nodes | **0 条 / 0 条（全空）** | COUNT(*)（R1 修复注释声称"真实库 270 条链接派生边"，与现状矛盾，见 C5） |

hospital_sut 10 条 SKIP 明细：TC-REQ-006/007/008 的 I01-I03（9 条，合法不可达"需求功能在系统中不存在或不可达"）+ TC-REQ-004-I01（1 条，failure_reason 仍为 FAIL 文本 `UI: [FAIL] text_contains: 找到0个匹配文本节点...`，status 被改 SKIP，见 C2）。

---

## A. 四目标差距评估

### 目标 1：通用（控件组合层）——【部分达成】

达成证据：
- 场景模板唯一结构存在：`semantic_extractor.py` `_SCENES = {"view","create","edit","delete","search","login","other"}`（L45）；用例 `scenario_type` 枚举（exploratory/boundary/error_path/happy_path/security）在 ai_generation_logs 载荷中可见。
- 引擎主代码无业务硬编码：`_op_cache_replayer.py` 注释"intent_hash 基于 case_id+actions，不绑定业务术语"；`_agent_checks.py` 的 API/DB 轨 check 分类为通用集合。

违规/风险证据：
1. **`rerun_hospital_step4_5.py`（根目录）硬编码 hospital_sut / TC-REQ-004 / DELETE executions**——特定靶子补丁脚本 + 破坏性清库（见 C6）。
2. **`_engine_coverage_gap` 触发面仅 TC-REQ-004-I01 单条**（engine5009.log L298 唯一命中），尽管代码自称"通用性修复"（见 C2）。
3. `_smart_locator/_fusion.py` 采用"经验值阈值"（HIGH_SCORE_MIN=15 等，注释"v0.1 经验值"），规则参数与控件组合层原则存在张力（属规则兜底层，非理解层，不判定违规，但需关注）。

### 目标 2：智能（AI 判断 + AI 稳定性）——【部分达成】

#### 2a. AI 判断（真实 LLM 调用）

| 环节 | AI 调用 | 证据 |
|---|---|---|
| Step2 需求拆解 | ✅ 有 | `_fd_extraction.py:820`、`_fd_theme_gate.py:75` 经 `_ai_brain._call_api`（seed=_STABILITY_SEED） |
| Step3 需求理解 | ✅ 有 | `semantic_extractor._ai_extract` → `router.route`（qwen2.5:7b 本地 + 云端双路由）；engine5009.log L1018-1143 有真实 qwen 调用记录 |
| Step3 用例生成 | ✅ 有 | `case_generator/_model.py:530/779` `_ai_brain._call_api` |
| Step4 探索/自愈 | ✅ 有 | `explorer_mixins/_llm.py`、`_semantic.py`（temperature=0.1） |
| Step4 控件语义定位 | ⚠️ 部分 | `_smart_locator` **自身纯规则**（`__init__.py:13`"不依赖任何 AI / embedding / RAG"）；high 置信 0-token 直接执行（`_ai_loop.py:196-207` 跳过 AI），medium 才把 Top-K 给 AI 二选一，low 才走全 DOM AI 兜底 |
| Step4 语义验证 | ⚠️ 部分 | `visual_verify.py` 封装 `chat_vision`（VLM 有）；`verification/_verify_ui_objective` 的 text_contains/no_error 等为客观规则断言（符合"规则验证"，但"语义验证"中的语义判断未见 LLM） |
| Step4 失败分类/根因 | ❌ **AI 调用=0** | `failure_classifier.py` 全仓 grep `router/ai_brain/llm/chat` 无命中——纯规则分类 |
| Step5 报告生成 | ✅ 有 | engine5009.log task=report_generation 真实 qwen 调用（L1134-1143） |

**智能缺口量化（AI 调用=0 的环节）**：
- 失败分类/根因分析（failure_classifier.py）——0 AI，纯规则链。
- fuzzy_match_engine（模糊匹配）——0 AI，相似度算法。
- SmartLocator high 置信路径——0 AI（设计性省 token，但与"控件语义定位全程真实 LLM 调用"铁律字面冲突）。

#### 2b. AI 稳定性（同输入→同输出）——【未完全落地】

| 证据 | 结论 |
|---|---|
| `pipeline_config.py:408` `ai_stability_seed: int | None = 42` ✅ 存在 | 声明存在 |
| seed=42 实际使用点仅 3 处：`helpers.py:78`（需求变更分析）、`_fd_extraction.py:827`、`_fd_theme_gate.py:82` | **未覆盖主理解路径** |
| `semantic_extractor._ai_extract`（L144）调 `router.route(...)` **不传 seed/temperature** → 默认 `temperature=0.3, seed=None`（`_router.py:346-361`） | **主理解路径无确定性**，与"temperature=0.0 已落地"声称不符 |
| agent 探索 `_llm.py/_semantic.py` temperature=0.1、无 seed | 探索路径无确定性 |
| `temperature=0.0` 仅 `call_vision`（`ai_client_mixins/_base.py:300`） | 0.0 仅视觉调用落地 |
| 未发现"多次跑不抖动"的量化稳定性测试/报告 | 稳定性未量化 |

### 目标 3：易用（配置正常 + 响应功能正常）——【部分达成】

达成证据：
- `_auto_apply.py:132-141` `is_auto_apply_enabled` 默认 True（用户介入最少）✅
- `/api/executions`、dashboard 可用（rerun 脚本 ALIGN 检查通过）；Step1→5 逐步骤可触发（scripts_step_client.py 验证过）。

断点/违规证据：
1. **run-all 违禁入口前后端均存在**：前端 `pipeline.html:11` `<button id="btnRunAll">` + L3055 `doRunAll → POST /api/run-all`；后端 `_start.py:858` `@run_bp.route("/api/run-all")` 真实注册（`run/__init__.py:19` 挂到 exec_bp → `app.py:378` 注册 exec_bp），且 `_middleware.py:303` API Token 白名单含 `/api/run-all`。**与"用户明确禁用 run-all"矛盾**。
2. 根目录脚本（run_all.sh / main.py / scripts_step_client.py / rerun_hospital_step4_5.py）绕 Web 控制台直调后端/直跑流水线，违反"唯一入口=引擎前端"。
3. 多处能力默认关闭：ADI_SIGNAL_GATE_ENABLED=False、NETWORK_FAULT_ENABLED=False、TDL_RESET_ENABLED=False（配置项存在但默认不可用，见 C1/C7）。

### 目标 4：媲美高级测试工程师 ——【部分达成】

| 能力 | 达成 | 证据 |
|---|---|---|
| 正向+异常+边界+安全+性能+可访问性覆盖 | ✅ 有 | `testing_methods/`（boundary_value/equivalence_partitioning/error_guessing/state_transition…）、`empirical/`（a11y_checker/perf_checker/authz_matrix）、`security_scanner`、`_network_fault.py`（默认关） |
| 根因分析 | ⚠️ 部分 | failure_classifier 纯规则（无 AI 根因）；step5 有 `_triage.py`/`minimal_repro.py` |
| 详细 Bug 报告（复现步骤+严重程度） | ✅ 有 | `step5/_bug_report.py`、`_blue_red_report.py`、`bug_ticket.py` |
| 人机协同审核闸门 | ✅ 有 | case_review（batch-approve 真实存在，rerun 脚本使用）、PRE_GATE 置信度闸门（executions 中 pre_gate_step4/5 哨兵）、`_manual_retest.py` |
| 越权/安全维度 | ⚠️ 有但未复核 | git log 94ccc3c/8a8dd7f（authz_sut/AZ-12），标注 [未复核-需 GLM/DeepSeek 把关] |

---

## B. 全局为什么没达成（架构根因）

1. **补丁堆叠文化，缺少失败-验证闭环治理**：git log 近 15 条几乎全部带 `[未复核-需 GLM/DeepSeek 把关]`；代码内 P0-fix/v5.2/v12/v25 注释层层累加（如 `_agent_checks.py`、`_rule_extractor_nav.py`）。每次暴露一个缺陷就打一个"通用性修复"补丁，没有统一的失败分类/断言语义/稳定性配置治理层，导致"修复"与"根因"脱节。
2. **"通用性"声明与实现脱节**：`_engine_coverage_gap` 自称通用但仅命中 TC-REQ-004；`_smart_locator` 注释"Top-K AI 二选一"但模块自身纯规则；`ai_stability_seed=42 + temperature=0.0 已落地"声明与主理解路径（0.3/None）不符。**声明先行、实现滞后**是四目标差评的共性根因。
3. **影子能力模式**：ADI_SIGNAL_GATE / NETWORK_FAULT / TDL_RESET / authz 越权等大量"默认关闭/未复核"增量——代码在、开关关、复核缺，形成不可用能力的债务；且引用设计文档 `engine_hardening_incremental_design.md` 在仓库中不存在（docs/ 仅 encryption_key_recovery）。
4. **验证纪律被绕过**：根目录脚本（rerun 清空 executions、scripts_step_client 直调 API、main.py 直跑 PipelineScheduler）替代了"经 Web 控制台的回归验证"；验证依赖一次性临时脚本而非固化测试。
5. **规则兜底膨胀**：`_rule_extractor_*.py` 累计 v3/v5/v7/v25 修复（滑动窗口、通用后缀词降权、DOM 校验、阈值调参），与"理解交给 AI、规则只兜底"的铁律边界日益模糊（虽未直接新增"模拟理解"正则，但导航/定位规则已相当复杂）。

---

## C. 补丁式垃圾代码识别（逐项核实，不预设结论）

### C1. `src/pipeline/steps/step3/oracle/adi_signal.py`（ADI_SIGNAL_GATE_ENABLED 默认 False）
- 位置：`src/pipeline/steps/step3/oracle/adi_signal.py`；模块开关 L35；classify L145-177；已接入 `step4_executor.py:584-598`、`w3_collectors.py:329-345`、`_confidence_gate.py:245/286`、`_agent_checks.py:562`、`_base.py:1094`。
- 判定：**需保留（+待复核）**。默认关闭 ≠ 未启用代码：禁用时 `classify()` 返回 OK、`DeepAssertionMetrics` 度量也被 `if bool(_cfg(...))` gate（w3_collectors L329），是显式设计的"零行为变更增量"。
- 风险：删除将破坏 P0-3 增量链路；import 冒烟通过，不影响引擎启动。模块自身标注"§14 关键改动 [未复核-需 GLM/DeepSeek 把关]"（L19-21），且引用的 `engine_hardening_incremental_design.md` 不存在（待确认文档去向）。
- 建议：保留代码；补强模型复核；修复文档引用或删除注释中的悬空引用。

### C2. `_agent_checks.py` 的 `_engine_coverage_gap`（2026-08-29 新加 FAIL→SKIP）
- 位置：`src/pipeline/steps/step4/browser_core/single_mixins/_agent_checks.py` L299-319（判定函数）+ L462-467（调用）。
- 判定：**针对 TC-REQ-004-I01 的补丁（需重构）**，非真通用修复。证据：
  - engine5009.log 仅 1 条命中 `[AgentRecord] 引擎覆盖缺口→SKIP(非FAIL): TC-REQ-004-I01`（L298）；
  - `rerun_hospital_step4_5.py:132/164` 硬编码查询 `TC-REQ-004-I01`——整个 2026-08-29 验证围绕该用例；
  - 判定是 4 个字符串条件堆叠（replay 标记 + text_contains"找到0个匹配" + no_error + 业务成功文本黑名单）；
  - executions 中 TC-REQ-004-I01 `failure_reason` 仍为 FAIL 文本（status 改 SKIP 但 evidence 未改，属掩盖痕迹）。
- 根因：**op_cache_v2 replay 命中后替换了 actions，但"成功后断言"（text_contains 保存成功/新增成功）在 replay 场景无对应文本**——是"缓存执行优化"与"成功后断言"语义冲突，被用后处理 FAIL→SKIP 掩盖。
- 风险：删除后 TC-REQ-004-I01 恢复 FAIL（31 用例变 21P/9S/1F，破坏"0 FAIL"验收表现）；引擎可正常启动。
- 建议：重构为"replay 场景断言适配"——在断言生成/执行层对 replay 命中的用例将成功后断言标记 NOT_APPLICABLE/SKIP，而不是在结果落库后做字符串匹配后处理。

### C3. op_cache_v2 replay 缓存机制
- 位置：`_op_cache_replayer.py` / `_op_cache_recorder.py` / `op_cache/v2_store.py`；表 op_cache_v2 22 条。
- 判定：**需保留（合理执行优化），非"绕过真实执行的补丁式缓存"**。证据：`_op_cache_replayer.py:101-110` 命中后仅替换 `step["actions"]`，后续仍走 `_execute_structured_actions` + checks 断言（不假 PASS）；有 fingerprint 校验 + 失效缓存 repair（L83-100）；record 仅在动作执行成功后写（不伪造成功）。
- 风险：保留本身无风险；但与 C2 联动会触发 FAIL→SKIP 掩盖（replay + 成功后断言 0 匹配）。建议：保留；与 C2 合并修复断言兼容性。

### C4. `test_data_lifecycle.py` reset 双定义（L313 与 L427）
- 位置：`src/pipeline/steps/step4/test_data_lifecycle.py` L313（旧）与 L427（新）。
- 判定：**垃圾（双定义）**。Python 类体同名方法后者覆盖前者——L313 的 reset（含 TDL_RESET_ENABLED 开关逻辑）**永远不可达**（死代码）；`_teardown_ns`（L395-425）仅被 L344 旧 reset 引用。L427 新版被 `step4_executor.py:83` 实际调用。
- 风险：删除 L313-425（旧 reset + _teardown_ns）零影响（行为已由 L427 决定）；引擎可启动；hospital_sut 结果不受影响。
- 建议：删除 L313-425 死代码段。

### C5. `_rule_extractor_nav.py:134` 正则放宽（R1）
- 位置：`src/pipeline/step3/_rule_extractor_nav.py` L129-135（守卫由"仅认 via_sidebar"放宽为"sidebar 与 selector 皆空才跳过"）。
- 判定：**需保留（属规则兜底层，不违反"理解交给 AI"铁律）**。证据：R1 是导航上下文兜底（`_edge_nav_context` L29-64），属规则提取层而非 semantic_extractor 理解层；注释已复核（DeepSeek-V4-Flash 2026-08-05）；未新增"模拟理解"的正则。
- 风险/疑点：R1 注释声称依赖"真实库 270 条链接派生边"，但 **nav_graph_edges/nodes 当前全空（0 条）**——修复的数据前提不存在（待确认：数据被清 or 修复时即无数据）。删除该放宽在数据 0 条时无影响；若 nav_graph 有数据时会破坏扁平导航系统匹配。
- 建议：保留代码；核实 nav_graph 数据来源与空表原因（待确认）。

### C6. 根目录脚本与日志残留
| 文件 | 判定 | 理由 | 删除风险 |
|---|---|---|---|
| `run_all.sh` | **垃圾** | 一键执行脚本，用户明确禁用 run-all；且引用不存在的 `qa_driver.py`（断链，无法运行）；绕 Web 控制台 | 无（断链） |
| `main.py` | **需删除/标注 DEV-ONLY** | 直调 `PipelineScheduler.run(from_step=1..5)`，绕 Web 控制台，违反"唯一入口"；import 有效（scheduler.py 存在） | 低（引擎入口是 app.py） |
| `scripts_step_client.py` | 审计验证临时脚本 | 2026-08-29 验证产物（MySQL 事实源核验有价值），但直调 `/api/step/N` 绕 UI | 低（验证已完成） |
| `rerun_hospital_step4_5.py` | **垃圾（高风险）** | 硬编码 hospital_sut/TC-REQ-004；**DELETE FROM executions**（破坏事实源）；绕 UI | 中（曾用于验证，删除后需经 UI 重跑） |
| `client.log` / `engine5009.log`(338KB) / `sut5080.log` / `rerun_step4_5.log` | 运行时日志残留 | 应入 logs/（.gitignore 已忽略 logs/） | 无 |
| 根目录 `_*.py` | **不存在**（任务描述提及，实际无） | `ls _*.py` 无结果 | — |

### C7. src 内悬空 import / phase2 未实现代码
- 悬空 import：关键模块 import 冒烟**全部 OK**（semantic_extractor / adi_signal / _agent_checks / _op_cache_* / test_data_lifecycle / _rule_extractor_nav / _network_fault）——未发现关键路径悬空 import。
- 设计文档悬空引用：`adi_signal.py` L4 引用 `engine_hardening_incremental_design.md`，仓库 docs/ 不存在该文档（待确认）。
- phase2 未授权代码：authz 越权（authz_sut/AZ-12）**已实现**（git 94ccc3c/8a8dd7f），标注 [未复核-需 GLM/DeepSeek 把关]；`_network_fault.py` 是**真实实现**（12KB，基于 Playwright page.route，非占位），默认 NETWORK_FAULT_ENABLED=False 门控。任务背景称"越权 schema 未经授权不实现项"——实际已实现（授权状态待确认，但非占位垃圾）。

### C8. .pyc / __pycache__
- 数量：全仓 284 个 `__pycache__` 目录 / 1063 个 `.pyc`；src 内 173 目录 / 1054 个 .pyc；target_systems 9 个。
- 判定：**垃圾（本地运行产物）**，.gitignore 已忽略（不提交）。
- 风险：删除零风险（Python 自动重建）；不影响引擎启动与 hospital_sut 结果。
- 建议：`find . -type d -name __pycache__ -prune -exec rm -rf {} +` 或 `git clean -fdX` 清理。

---

## D. 删除清单（可直接执行，按风险分级）

| # | 路径 | 类型 | 理由 | 风险等级 | 备注 |
|---|---|---|---|---|---|
| D1 | `src/pipeline/steps/step4/test_data_lifecycle.py` L313-425（旧 reset + _teardown_ns） | 源码死代码 | 双定义被 L427 覆盖，永不可达 | **低**（零行为变更） | 保留 L427 新版 |
| D2 | `run_all.sh` | 根目录脚本 | 断链（qa_driver.py 不存在）+ 用户禁用 run-all | **低**（无法运行） | — |
| D3 | `rerun_hospital_step4_5.py` | 根目录脚本 | 特定靶子补丁 + DELETE executions 破坏事实源 + 绕 UI | **中**（验证已完成，删除后重跑需经 UI） | 删除前确认 2026-08-29 验证结论已存档 |
| D4 | `scripts_step_client.py` | 根目录脚本 | 审计验证临时脚本，绕 UI | **低**（验证已完成） | 或移入 tests/ 保留 |
| D5 | `main.py` | 根目录脚本 | 绕 Web 控制台直跑流水线，违反唯一入口 | **低**（引擎入口 app.py） | 或标注 DEV-ONLY |
| D6 | `client.log` / `engine5009.log` / `sut5080.log` / `rerun_step4_5.log` | 运行时日志 | 应入 logs/（已 gitignore） | **无** | — |
| D7 | 全仓 `__pycache__` / `*.pyc` | 缓存 | 运行产物，自动重建 | **无** | 建议 git clean -fdX |
| D8 | （建议）`_agent_checks.py` L299-319 + L462-467 | 源码重构（非删除） | FAIL→SKIP 后处理补丁，掩盖根因 | **中** | 需与 replay 断言适配合并重构（见 C2） |
| D9 | （待确认）`adi_signal.py` 注释中悬空文档引用 | 文档 | 引用不存在的 design md | **低** | 补文档或删引用 |

**明确不删除**：`_op_cache_replayer.py`/`_op_cache_recorder.py`（真实执行优化）、`_rule_extractor_nav.py`（规则兜底层）、`_network_fault.py`（真实实现默认关）、`adi_signal.py`（已接入默认关的 P0-3 增量）。

---

## E. 最高风险项

1. **C2 `_engine_coverage_gap`**（删除后 hospital_sut 恢复 1 FAIL，破坏"0 FAIL"验收表现；不删则持续掩盖引擎覆盖缺口根因）——建议重构而非删除。
2. **C6-D3 `rerun_hospital_step4_5.py`**（含 DELETE executions 破坏性操作，若继续使用会污染单一事实源）。
3. **run-all 违禁入口**（前端 btnRunAll + 后端 /api/run-all 已注册且进 API Token 白名单）——与用户明确禁用矛盾，需产品决策关闭。

---

## F. 审计声明

- 本报告全部结论基于上述文件/行/开关/日志/MySQL 证据；无法在只读范围内验证的（如设计文档去向、nav_graph 空表原因、authz 授权状态）已标注"待确认"。
- 审计过程仅执行只读查询、git 只读、import 冒烟与 compileall（生成标准 .pyc 缓存），未修改/删除任何源码、数据或配置。
