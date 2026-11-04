# E2E 智能测试引擎 —— 全局修复增量设计（文件级改动点）

- 设计人：高见远（软件架构师）
- 日期：2026-08-29
- 上游输入：`deliverables/software-company/e2e-gap-audit-2026-08-29.md`（四目标差距审计）
- 项目根：`D:\AI\AI_Test_Preject`；引擎 `src/backend/app.py`（waitress，5009）；MySQL `ai_test_engine`
- 铁律：理解交给 AI、规则只兜底；禁止正则堆叠模拟理解；不表演、不造假；关键改动按 §14 需强模型复核 → **当前无 GLM/DeepSeek 强模型，全部关键改动标注 [未复核]**，交付前由具备强模型的环境复核。
- 只读声明：本设计仅读代码与审计报告，未修改任何文件。

---

## 0. 结论速览（工程师必读）

| 修复 | 一句话方案 | 核心文件 | 风险 |
|---|---|---|---|
| F1 失败分类接 AI | 在 `classify_failure` 规则判定后新增 `enrich_root_cause_ai()` 增补字段（**不覆盖** status/failure_category），AI 失败一律 fail-open 回规则结果 | `step4/failure_classifier.py`（新增）、`_agent_checks.py`、`_base.py` | 低（AI 仅 enrich） |
| F2 AI 稳定性统一 | 全仓 LLM 调用统一 `seed=42 + temperature=0.0`；**必须补 `call_local_model` 的 seed 透传**（当前本地 Ollama 路径 seed 被静默丢弃） | `local_ollama.py`、`_router.py`、`semantic_extractor.py`、`explorer_mixins/_llm.py`、`_semantic.py`、`_blue_red_report.py`、`case_generator/_model.py` | 中（探索路径确定性化，行为可逆） |
| F3 replay 断言适配 | 用 agent 路径**已有标记** `intent["op_cache_hits"]>0`（+结构化路径新增 `_replay_hit`）识别 replay，在 `_run_checks_for_agent` 层把"成功后断言"标记 NOT_APPLICABLE/SKIP，**删除 `_engine_coverage_gap` 后处理** | `_agent_checks.py`、`_base.py`、`_combo_enhancer.py` | 中（需防误伤真实 FAIL，见 3.5） |
| F4 pre_gate 骨架行 upsert | `_persist_gate_result/_persist_gate_blocked` 复用现有 `_upsert_execution`（SELECT→UPDATE/INSERT，键=run_id+project+case_id+check_type） | `scheduler.py`、`users/audit.py` | 低 |
| F5 409 容忍 / active_runs 清理 | 在 `_start.py` 所有 409 互斥判定前复用 status.py 的僵尸清理逻辑（死线程+超时看门狗），并跳过死线程条目 | `state.py`（新增 `_sweep_stale_runs`）、`_start.py` | 低-中（改动点多，均为防御式） |

**工程师实现顺序**：F4 → F5 → F1 → F2 → F3（先做低风险数据/状态修复，再做 AI 增量，最后做断言语义重构；F3 是行为语义变化最大的一项，放最后便于回归对照）。

**最高风险项**：**F3**（删除 `_engine_coverage_gap` 后 TC-REQ-004-I01 必须仍为 SKIP 而非 FAIL，否则破坏"0 FAIL"验收表现；且新增的 SKIP 语义必须避免掩盖真实产品缺陷）。

---

## F1. 失败分类 / 根因分析接入 AI（智能缺口最大）

### 1.1 现状（读码核实）

- `src/pipeline/steps/step4/failure_classifier.py` 全文件无任何 LLM 调用，纯规则：
  - `classify_failure()`（L124-195）：按 network_error / timeout / playwright_error / DOM 状态 / 策略数 返回 `(failure_category, status)`。
  - `FailureChain`（L79-121）：记录策略序列。
  - `build_failure_reason()`（L198-209）：拼人类可读失败链。
- 两个调用点（均已读码确认）：
  1. `src/pipeline/steps/step4/browser_core/single_mixins/_agent_checks.py` L364-397（AgentExplorer 路径）：`classify_failure(...)` → `_failure_cat, _status`，随后 L468-483 `record_execution(..., details=_details_json)`。
  2. `src/pipeline/steps/step4/browser_core/single_mixins/_base.py` L936-988（标准路径）：`classify_failure(...)` → `failure_category, status`，随后 `record_execution(...)`（L1010 之后）。
- `record_execution`（`src/users/audit.py` L101-190）支持 `details`（详情 JSON 字符串），**无需改表即可承载 AI 增补字段**。

### 1.2 改动点

| # | 文件 | 函数 | 改动内容 |
|---|---|---|---|
| F1-1 | `src/pipeline/steps/step4/failure_classifier.py` | **新增** `enrich_root_cause_ai(chain, expected="", actual="", failure_reason="", category="", status="") -> dict` | 真实 LLM 根因分析（双路由，参照 `semantic_extractor._ai_extract` 的 `router.route` 模式）。返回 `{ai_root_cause, ai_severity, ai_suggestion, ai_confidence, ai_model, ai_source}`；任何失败/超时/解析失败返回 `{}`（fail-open）。**绝不修改 category/status**。 |
| F1-2 | 同上 | 新增模块级 `_AI_ROOT_CAUSE_PROMPT_TMPL` + `_AI_ROOT_CAUSE_SYSTEM` | Prompt 骨架（见 1.3）。 |
| F1-3 | `_agent_checks.py` | `_try_agent_explorer_path` 内 L397 之后（`classify_failure` 返回处） | 调用 `enrich_root_cause_ai(...)`，把返回 dict 合并进 `_details_json`（JSON 追加 `ai_*` 键），再传给 `record_execution(details=...)`。 |
| F1-4 | `_base.py` | 标准路径 L988 之后（`classify_failure` 返回处） | 同上：合并进 details 后落库。 |

> 待读码确认：两个调用点 `record_execution` 的 details 参数在 L1010 之后如何构造（`_base.py`），工程师实现时按现有 details 结构追加 `ai_*` 键即可；若 details 为空则初始化为 `{}` 再追加。

### 1.3 Prompt 骨架（输入/输出契约）

```
system: 你是资深测试工程师，基于失败链路做根因分析。只输出 JSON，不要多余文本。

user: 请分析以下测试执行失败并输出 JSON。
## 失败分类（规则层，仅供参考，可推翻）
failure_category=PRODUCT_BUG|HARNESS_ERROR|ENVIRONMENT_ERROR|DATA_ERROR
status=FAIL|ERROR
## 期望
{expected}
## 实际
{actual}
## 失败原因
{failure_reason}
## 失败链路（策略序列）
{failure_chain_json}

输出 JSON 字段：
{
  "ai_root_cause": "PRODUCT_BUG|HARNESS_ERROR|ENVIRONMENT_ERROR|DATA_ERROR|OTHER",  // 根因类别（可不同于规则层）
  "ai_severity": "critical|high|medium|low",   // 严重度
  "ai_suggestion": "给测试/开发的修复建议，≤120字",
  "ai_confidence": 0.0~1.0,                    // AI 自身置信度
  "ai_evidence": "判定依据，≤80字"
}
```

### 1.4 调用参数（对齐 F2 稳定性）

```python
from src.ai.router_module import router
from src.core.pipeline_config import _read_config_value as _cfg

r = router.route(
    _AI_ROOT_CAUSE_PROMPT_TMPL.format(
        expected=expected[:500], actual=actual[:500],
        failure_reason=failure_reason[:500],
        failure_chain_json=chain.to_json()[:2000],
    ),
    system_prompt=_AI_ROOT_CAUSE_SYSTEM,
    expect_json=True,
    task_type="general",          # 复用既有 task 映射，避免新增 task_type 缺模型映射
    temperature=0.0,              # 稳定性铁律
    seed=int(_cfg("ai_stability_seed", 42) or 42),  # seed=42
    local_timeout=float(_cfg("ai_local_timeout", 60.0)),
)
```

### 1.5 降级 / 回退策略（fail-open 铁律）

1. `router.route` 返回 `success=False`（本地 Ollama 不可用/超时 → 云端兜底也失败）→ 返回 `{}`，调用点**原样走规则结果**。
2. `r.get("data")` 非 dict / 缺 `ai_root_cause` / JSON 解析失败 → 返回 `{}`。
3. 函数整体 `try/except BaseException` 包裹 → 任何异常返回 `{}`（绝不向执行路径抛异常）。
4. 回退开关：`pipeline_config` 增加 `failure_ai_enrich_enabled`（默认 True），False 时直接返回 `{}`（一键回退，不删代码）。

### 1.6 如何不影响现有 status/failure_category 判定

- `enrich_root_cause_ai` **不接收也不返回** status/category 变更；调用点只把 AI 输出合并进 `details`（增补而非覆盖）。
- `record_execution` 的 `status`/`failure_category`/`failure_reason` 参数保持规则层原值 → 现有 KPI 聚合、Step5 报告、ConfidenceGate 全部不受影响。
- `details` 是 JSON 追加键，`_evidence_extractor` 现有解析只读自己关心的键，不冲突。

### 1.7 自验证方法

1. 构造失败用例（如故意定位不存在元素）→ 执行 Step4 → `executions.details` 含 `ai_root_cause/ai_severity/ai_suggestion`。
2. 停掉本地 Ollama + 清空云端 key → 再次执行 → 执行不失败、status/failure_category 与纯规则基线一致、details 无 `ai_*` 键（fail-open）。
3. 同一失败用例连跑 2 次 → details 中 `ai_root_cause/ai_severity` 一致（稳定性）。
4. 回归：hospital_sut 全量 Step4 结果与修复前一致（PASS/SKIP/FAIL 分布不变，仅 details 增补）。

### 1.8 风险

- **低**。AI 仅增补字段，不改判定；最坏情况是 AI 不可用时无增补（等同现状）。AI 输出被展示层消费后若发现误导，可后续加"AI 结论仅参考"展示标注。

---

## F2. AI 稳定性统一（seed=42 + temperature=0.0 全局化）

### 2.1 关键读码发现（决定改动面）

1. **本地 Ollama 路径 seed 被静默丢弃**：`src/ai/router_module/_router.py` L421-431 调 `call_local_model(prompt, system_prompt, model, api_base, timeout, temperature, max_tokens)` **不传 seed**；`src/ai/router_module/local_ollama.py` L20-28 的 `call_local_model` **没有 seed 参数**，payload 只有 `options: {temperature, num_predict}`。→ **即使 Step2 已传 seed，本地 qwen2.5:7b 也不确定性**。必须补透传（Ollama API 支持 `options.seed`）。
2. **云端路径 seed 已透传**：`_router.py` L265-273 `chat(..., seed=seed)`；`openai_compat.py` L78/L108-110 `payload["seed"]=seed`。✓
3. `_ai_brain._call_api`（`ai_client_mixins/_base.py` L108-124）已支持 `seed` 参数并透传给 `router.route`（L177）。✓ 调用方传 `seed=42` 即可。
4. `semantic_extractor._ai_extract`（L139-170）调 `router.route(...)` **不传 seed/temperature** → 默认 `0.3/None`（`_router.py` L355/L361）。
5. `pipeline_config.py` L408 `ai_stability_seed=42`；`_STABILITY_SEED = pipeline_config.ai_stability_seed`（`step2/analyzers/constants.py` L42）。

### 2.2 全仓真实 LLM 调用点清单（grep 核实：router.route / _call_api / chat / chat_vision）

> 表内"改后参数"= 本轮要求；标注"保持"= 本轮不动（含理由）。

| # | 调用点（文件:行） | 当前参数 | 改后参数 | 决策 |
|---|---|---|---|---|
| 1 | `step2/analyzers/_fd_extraction.py:820` `_call_api` | temp=`_EXTRACT_TEMPERATURE`(=`ai_default_temperature`=0.0), seed=_STABILITY_SEED | 保持（已达标；仅受 2.1-1 本地 seed 透传修复影响） | 保持调用方；**修 `call_local_model` 透传** |
| 2 | `step2/analyzers/_fd_theme_gate.py:75` `_call_api` | temp=0.0, seed=_STABILITY_SEED | 保持（同上） | 保持 |
| 3 | `core/semantic_extractor.py:144` `router.route`（**主理解路径**） | 默认 temp=0.3, seed=None | **temp=0.0, seed=42** | **改**（审计核心缺口） |
| 4 | `step3/case_generator/_model.py:530` `_call_api` | temp=`mp.get("temperature",0.0)`（模型参数，cross-validation 可能双温度）, **无 seed** | **加 seed=42**；temp 保持 `mp.get(...)`（双模型交叉验证是有意的多样性特性，不破坏） | **改（仅加 seed）** |
| 5 | `step3/case_generator/_model.py:779` `_call_api` | 同上 | 同上 | **改（仅加 seed）** |
| 6 | `agent/explorer_mixins/_llm.py:374` `router.route` | temp=0.1, seed=None | **temp=0.0, seed=42** | **改**（见 2.3 权衡） |
| 7 | `agent/explorer_mixins/_semantic.py:57/128/199/268` `router.route`（4 处） | temp=0.1, seed=None | **temp=0.0, seed=42** | **改**（同上） |
| 8 | `step5/_blue_red_report.py:148` `_call_api`（报告生成） | temp=`PERSPECTIVE_PARAMS.get(...,0.1)`, 无 seed | **加 seed=42**；temp 保持视角参数 | **改（仅加 seed）** |
| 9 | `pipeline/helpers.py:70` `_call_api`（需求变更分析） | temp=0.1, seed=`ai_stability_seed` | 保持 seed；temp 对齐 0.0（可选，一致性优先） | 保持/可选对齐 |
| 10 | `ai/visual_verify.py:114` + `ai_client_mixins/_base.py:300` `call_vision` | temp=0.0（视觉） | **保持 0.0**（视觉输入多变，seed 无意义；已是最低温） | 保持 |
| 11 | `step4/verification/_verify_visual.py`（chat_vision 封装） | 降级设计（vision 不可用跳过） | 保持 | 保持 |
| 12 | **F1 新增** `failure_classifier.enrich_root_cause_ai` | — | **temp=0.0, seed=42** | 新增（见 F1） |
| 13 | `_router.py:421` `call_local_model`（本地 Ollama 通用入口） | 无 seed 参数 | **新增 `seed` 参数并写入 `options.seed`** | **改（本轮最关键的底层修复）** |
| 14 | 其他 `_call_api`（docs_read_coverage / docs_write / db_core/generate / deployment/matcher / case_validator/ai_check / rag_engine 系列 / traceability / system_data_resolver / _modeling_intents / smoke_generator / xmind_designer / text_mode / tool_mode / _adversarial / data_factory_core / db_verifier / _self_healing / risk_analyzer / _navigation_resolver 等） | 多数 temp 默认 0.3 / 无 seed | **本轮不逐点改**；仅当涉及 Step2/3/4/5 主链路的（13 之外）若已带 temp=0.0 可顺带加 seed=42 | 保持（登记在案，下轮统一） |

### 2.3 探索路径（Step4 `_llm`/`_semantic`）是否统一 —— 权衡结论

- **结论：统一为 seed=42 + temperature=0.0**（符合用户铁律"同输入→同输出"）。
- 理由：
  1. Step4 探索是**执行固定用例**，不是"生成多样用例"。同 case 每次探索路径不同正是测试抖动（flaky）的来源，确定性化提升回归可信度。
  2. "探索多样性"真正需要的是**跨用例**的多样性——不同 case 输入（intent/页面状态）天然不同，即使 seed 固定也不会退化为相同动作。
  3. 若未来确需同 case 内多样探索（如模糊/对抗用例），设计确定性派生方案：`seed = _STABILITY_SEED + zlib.crc32(case_id.encode())`，仍保证"同输入→同输出"。本轮不实现，仅记录。
- 视觉 `call_vision` 保持 0.0（不加 seed）：视觉输入为截图内容，逐帧变化，seed 不产生可复现价值。

### 2.4 底层修复细节（本地 seed 透传）

- `src/ai/router_module/local_ollama.py` `call_local_model(...)`：新增参数 `seed: int | None = None`；`payload["options"]` 中 `if seed is not None: payload["options"]["seed"] = seed`。
- `src/ai/router_module/_router.py` L421-431：调用 `call_local_model(..., seed=seed)`。
- 保持默认 `seed=None`（不破坏其他未传 seed 的调用方随机性；仅显式传 seed 的路径确定性化）。

### 2.5 降级 / 回退策略

- seed/temperature 都是 API 请求参数：云端不支持 seed 的 provider 会忽略（openai_compat L108 仅当非 None 才写），本地 Ollama 低版本若忽略 `options.seed` 也只是退化为随机，**不影响功能**。
- 回退：`pipeline_config.ai_stability_seed` 置 None 即全局恢复随机（`_STABILITY_SEED=None`），所有改动点读该配置，不硬编码。

### 2.6 自验证方法

1. 稳定性量化：取同一 case（如 TC-REQ-004）连跑 Step3 理解 + Step4 探索 2 次，对比 `ai_generation_logs`/`executions.details` 的 LLM 输出（或加临时日志 hash 输出首 200 字）——**必须一致**（本地+云端各验一次）。
2. 本地路径验证：确认 Ollama 请求日志 / 抓包出现 `"seed":42`。
3. 回归：Step2/3/4/5 各跑一遍，结果分布与修复前一致（温度 0.3→0.0 可能让个别生成结果变化，属预期；PASS/SKIP/FAIL 分布应无劣化）。

### 2.7 风险

- **中**：探索路径确定性化后，原"运气好才成功的探索"可能稳定失败（暴露真实问题而非掩盖）——这是期望行为，但会让部分用例从 PASS 变 FAIL。缓解：F3 只处理"replay 无业务动作"场景，真实执行失败仍如实 FAIL；回归时逐条核对变化用例。

---

## F3. C2 replay 断言适配重构（替代 `_engine_coverage_gap` 后处理）

### 3.1 现状与读码确认

- 审计：`_agent_checks.py` L299-319 `_engine_coverage_gap`（4 个字符串条件堆叠）+ L462-467 落库后处理 FAIL→SKIP，仅命中 TC-REQ-004-I01 单条，属补丁。
- **replay 在两条路径的落点（已读码确认）**：
  1. Agent 路径：`explorer.execute_intent` 内部查 op_cache_v2，命中返回 `strategy="replay"`；`_agent_executor.py` L358-361 **已有标记**：`intent["op_cache_hits"] += 1`。→ **可用 `intent.get("op_cache_hits",0)>0` 判定 replay**。
  2. 结构化路径：`_base.py` L495-507 `_rep_hit, _ = replay_attempt(...)`（命中替换 `step["actions"]`），`_rep_hit` 是局部变量，**未标记到 intent**。
- 断言执行点：`_agent_checks.py` `_run_checks_for_agent`（L26-…，UI 轨走 `_verify_ui_objective`，L180-205）；TC-REQ-004-I01 走 Agent 路径（日志 `[AgentRecord]` 前缀证实）。
- "成功后断言"生成点：`step3/case_generator/_combo_enhancer.py` L200-238 `_enhance_form_submit_scenario`（text_contains_check expected="成功,保存成功,操作成功"）。

### 3.2 改动点

| # | 文件 | 函数 | 改动内容 |
|---|---|---|---|
| F3-1 | `_agent_checks.py` | `_run_checks_for_agent` | 入参新增 `replay_hit: bool = False`（或从 `intent.get("op_cache_hits",0)>0` 读取）；执行 UI/API 轨前，过滤"成功后断言"（见 3.3）→ 记录 `[SKIP] {desc}: REPLAY_NO_ACTION（op_cache_v2 replay 未真实执行业务动作，成功后断言不适用）`，不计入 FAIL；返回结果增加 `replay_skipped: bool`。 |
| F3-2 | `_agent_checks.py` | `_try_agent_explorer_path`（L408-438 区域） | 调用 `_run_checks_for_agent` 时传 replay 标记；若 `replay_skipped` 且无其他 FAIL → `_status="SKIP"`、`_failure="op_cache_v2 replay 命中，成功后断言未执行(NOT_APPLICABLE): REPLAY_NO_ACTION"`、`_failure_cat` 可留空或 `"REPLAY_NO_ACTION"`；**删除 L299-319 `_engine_coverage_gap` 函数 + L462-467 调用**。 |
| F3-3 | `_base.py` | L504-507（replay 命中处） | `if _rep_hit: intent["_replay_hit"] = True`（结构化路径标记，供标准路径断言执行点读取）。 |
| F3-4 | `_combo_enhancer.py` | `_enhance_form_submit_scenario`（L208-221） | 新生成的成功后断言 check_item 增加标记 `"post_success": True`（生成层显式语义化，替代关键字匹配）。 |
| F3-5 | （可选，标准路径）`browser_intent_parser/_steps.py` / `verification/_db_ui.py` `_verify_ui_objective` | — | 若标准路径也存在"replay+成功后断言"场景（待读码确认：标准路径 intent checks 是否在 replay 命中后执行），在 check 构建/执行层同样按 `intent.get("_replay_hit")` 过滤。本轮**必做 Agent 路径**，标准路径按"待读码确认"处理；若确认无此场景则跳过。 |

### 3.3 判定逻辑（如何识别 replay 场景下应跳过的断言）

```python
def _is_replay_skippable_check(check_item: dict) -> bool:
    """成功后断言：replay 场景不适用（未真实执行业务动作）。"""
    c = check_item.get("check", {}) if isinstance(check_item, dict) else {}
    # 1) 显式标记（新生成用例，见 F3-4）
    if check_item.get("post_success"):
        return True
    # 2) 关键字兜底（历史用例，兼容已落库的 checks）
    if c.get("type") == "text_contains_check" and c.get("track") in ("ui", None):
        exp = str(c.get("expected") or check_item.get("expect") or "")
        return any(k in exp for k in (
            "成功", "保存成功", "新增成功", "创建成功", "提交成功", "删除成功", "更新成功", "操作成功"))
    return False
```

replay 判定：`intent.get("op_cache_hits", 0) > 0 or intent.get("_replay_hit")`（Agent + 结构化双路径）。

### 3.4 移除 `_engine_coverage_gap` 后的行为影响（审计要求核实项）

- **预期结果**：TC-REQ-004-I01 命中 replay → 成功后断言被标 NOT_APPLICABLE/SKIP → 其余断言通过 → `all_passed=True` 但 `replay_skipped=True` → `status=SKIP`（reason=REPLAY_NO_ACTION），**不是 FAIL，也不是 PASS**（PASS 会假称完整验证，SKIP 语义正确：业务动作未真实执行）。
- hospital_sut 计数：10 SKIP 不变（TC-REQ-004-I01 由"补丁式 SKIP"变为"语义式 SKIP"），0 FAIL 保持。
- **证据一致性**：修复前 failure_reason 残留 FAIL 文本（审计指出"掩盖痕迹"）；修复后 failure_reason 直接写 REPLAY_NO_ACTION，expected/actual/details 与 SKIP 语义一致，不再有 FAIL 文本残留。
- 回归风险：若其他 replay 命中用例存在**真实产品缺陷**（replay 了缓存动作但页面确实异常），新逻辑只跳过"成功后断言"，no_error/其他断言仍会 FAIL——不掩盖真实缺陷。fingerprint 校验（`_op_cache_replayer.py` L83-100）保证 replay 仅在页面指纹一致时触发，进一步降低误判。

### 3.5 降级 / 回退策略

- 回退开关：`pipeline_config.replay_assertion_skip_enabled`（默认 True）；False 时 `_run_checks_for_agent` 不跳过任何断言（回到旧行为——但 `_engine_coverage_gap` 已删除，此开关是"回退到保留补丁"的备用路径，需连同恢复 L299-319/L462-467 一起，仅应急用）。
- 更安全的中间态（可选）：先加"语义式 SKIP"（F3-1/2 新逻辑）并保留 `_engine_coverage_gap` 观察 1 轮，确认 TC-REQ-004-I01 走新逻辑后再删补丁。**推荐直接删**（审计要求"该删的删"），但工程师可按此中间态降低上线风险。

### 3.6 自验证方法

1. 清空 op_cache_v2 后跑 TC-REQ-004 → 真实执行，成功后断言生效（PASS/FAIL 取决于 SUT）。
2. 预热 op_cache_v2 后重跑 TC-REQ-004 → replay 命中 → 记录为 SKIP(REPLAY_NO_ACTION)，evidence 含 `[SKIP] ... REPLAY_NO_ACTION`，无 FAIL 文本。
3. grep 确认 `_engine_coverage_gap` 已无引用（`grep -rn "_engine_coverage_gap" src`）。
4. 构造真实失败（改 SUT 使保存按钮不存在）且 replay 命中 → 仍 FAIL（不误伤）。

### 3.7 风险

- **中**。主要风险是误伤：若某 replay 命中用例的"成功后断言"实际代表真实缺陷（页面无成功提示），跳过会掩盖。缓解：①只跳过"成功文本"类断言（`_is_replay_skippable_check` 白名单），其他断言照常；②fingerprint 校验兜底；③Step5 报告对 SKIP(REPLAY_NO_ACTION) 单独归因，方便审计。

---

## F4. pre_gate 骨架行防累积（upsert）

### 4.1 现状（读码确认）

- 写入点：`src/pipeline/scheduler.py`
  - `_persist_gate_result(step_num, gate_result, run_id, project)`（L969-1000）：`INSERT INTO executions (..., case_id=pre_gate_step{step_num}, check_type='pre_gate', status='PRE_GATE', ...)`。
  - `_persist_gate_blocked(step_num, reason, run_id, project)`（L1003-1046）：同 INSERT，status='PRE_GATE_BLOCKED'。
- 累积根因：纯 INSERT，无去重。且 `run_id` 是确定性值 `'{project}__{version_label}'`（`_start.py` L110-132 `_gen_run_id_and_clear_old_logs`）——同项目同版本每轮 Step4/5 都 INSERT，两轮=4 行。
- **已有可复用设施**：`src/users/audit.py` L65-98 `_upsert_execution(conn, cols, ph, params, run_id, project, case_id, check_type)`——SELECT `(run_id, project, case_id, check_type)` 存在则 UPDATE 覆盖、否则 INSERT（2026-08-24 为业务行去重新增，逻辑已成熟）。

### 4.2 改动点

| # | 文件 | 函数 | 改动内容 |
|---|---|---|---|
| F4-1 | `scheduler.py` | `_persist_gate_result` | 改为调用 `_upsert_execution`（键：run_id + project + case_id=`pre_gate_step{step_num}` + check_type=`pre_gate`），INSERT 语义变为 upsert。 |
| F4-2 | `scheduler.py` | `_persist_gate_blocked` | 同上。 |
| F4-3 | `users/audit.py` | `_upsert_execution` | 无需改动（已满足）。若担心非原子（并发同 run_id），可加注释说明受 Step 互斥保护（`_start.py` 409 互斥 + `_active_runs` 互斥，同版本并发概率极低）。 |

实现细节：`_persist_gate_result` 现有 INSERT 列集为 `run_id, project, case_id, check_type, user_id, status, failure_reason, expected, actual, details, executed_at`，将其组装为 `cols/ph/params` 传给 `_upsert_execution`；`check_type='pre_gate'` 参与键匹配，业务行（check_type='browser_exec' 等）不受影响。

### 4.3 历史脏数据清理（一次性数据修复，可选）

```sql
-- 仅清理 pre_gate 骨架行重复（保留每个 run+case 最新一条）
DELETE e1 FROM executions e1
JOIN executions e2 ON e1.run_id=e2.run_id AND e1.project=e2.project
  AND e1.case_id=e2.case_id AND e1.check_type=e2.check_type
  AND e1.id < e2.id
WHERE e1.check_type='pre_gate';
```
执行前备份；KPI 聚合器已排除 pre_gate，不影响分布（审计核实）。

### 4.4 降级 / 回退策略

- `_upsert_execution` 失败时外层已有 try/except（L998-1000 仅告警）→ 维持原行为（不落库），不阻断流水线。
- 回退：恢复为纯 INSERT 即可（逻辑简单，无状态依赖）。

### 4.5 自验证方法

1. 连续两轮触发 Step4+Step5（同项目同版本）→ `SELECT case_id, COUNT(*) FROM executions WHERE check_type='pre_gate' GROUP BY case_id, run_id` → 每 (run_id, case_id) 恰 1 行（共 2 行，不增长到 4）。
2. 修改门禁阻断一次 → pre_gate_stepN 行 status 被 UPDATE 为 PRE_GATE_BLOCKED（覆盖而非新增）。

### 4.6 风险

- **低**。只影响 pre_gate 骨架行；`_upsert_execution` 已用于业务行且有事务保证。

---

## F5. 步骤衔接 409 容忍 / active_runs 状态清理

### 5.1 现状（读码确认）

- `_active_runs` 全局字典：`src/backend/state.py` L635（`{run_id → {thread, force_stop, step, project, start_ts}}`）。
- 登记点：`_start.py` L572/L733/L827/L889/L926 等（`api_step_run`/`api_step4_run`/`api_step4_run_single`/retry/run-all）；`case_review/_routes.py` L720、`review.py` L209、`requirements.py` L257、`_figma_refresh.py` L46。
- 清理点：`_run_step.py` L404-413 + L708-712 finally 双 pop；`_make_session_runner`（`execution/__init__.py` L308-341）BaseException 兜底 pop。
- **409 判定**：`_start.py` L534-569（`api_step_run`）、L714-730、L809-829、L884、L921——**只查 `_active_runs` 条目是否存在，不校验 thread 是否存活**。
- **status.py 已有僵尸清理**：L63-91（死线程清理 + 30 分钟看门狗），但只在 `/api/status` 轮询时执行。
- **根因链**：任何"线程已死但条目未 pop"的泄漏（如 runner 启动前异常、线程被硬杀、`t.start()` 异常、pop 前崩溃）都会让同项目 Step2-5 永久 409；且 run_id 确定性（同项目同版本同 run_id）使单条泄漏即可阻断后续所有轮次。QA 实测现象与该机制吻合。

### 5.2 改动点（最小方案）

| # | 文件 | 函数 | 改动内容 |
|---|---|---|---|
| F5-1 | `src/backend/state.py` | **新增** `_sweep_stale_runs(project: str = None) -> int` | 复用 status.py L69-91 逻辑：①`thread is not None and not thread.is_alive()` → 移除；②`thread is not None and thread.is_alive()` 且 `now - start_ts > 30*60`（可配置 `run_max_seconds`）→ 移除（看门狗）。返回清理数。 |
| F5-2 | `src/backend/routes/execution/run/_start.py` | `api_step_run`（L530 区域）、`api_step4_run`（L714 区域）、`api_step4_run_single`（L809 区域）、retry/run-all（L884/L921 区域） | 在每个 `with _active_runs_lock:` 互斥判定**之前**调用 `_sweep_stale_runs(project)`；并把 `conflicting` 计算改为跳过死线程条目：`if t is not None and not t.is_alive(): continue`（双保险）。 |
| F5-3 | `src/backend/routes/execution/status.py` | `api_status` | 复用 `_sweep_stale_runs`（替换内联逻辑，行为不变）。 |

> 不做：不改 `_run_step` 的 finally pop（已存在且正确）；不新增 DB 持久化 active_runs（内存态 + 看门狗足够）；不改变 Step2-5 互斥的业务语义（真并行仍 409）。

### 5.3 降级 / 回退策略

- `_sweep_stale_runs` 内部全 try/except，异常时返回 0（不清理，等同现状），**绝不抛给请求线程**。
- 看门狗阈值从 `run_max_seconds` 读取，默认 1800（30 分钟，与 status.py 一致）；配置缺失回退 1800。
- 回退：删掉 `_start.py` 中新增的 sweep 调用即可恢复原行为（无状态依赖）。

### 5.4 自验证方法

1. **主验证**：Step4 完整执行完毕（MySQL 全量落库）→ 立即 POST `/api/step/5` → 200（不再 409）。
2. **泄漏注入验证**：手动向 `_active_runs` 插入 `{"thread": <已结束线程>, "step": 4, "project": <当前项目>, "start_ts": <过去>}` → POST `/api/step/5` → 200（sweep 清理后再判定）。
3. **真并行保护**：Step4 正在执行时 POST `/api/step/5` → 仍 409（不误放行）。
4. **看门狗**：注入 `start_ts` 为 40 分钟前的存活线程 → 下一次触发被清理。
5. 回归：status.py 轮询返回与修复前一致（仅清理时机提前）。

### 5.5 风险

- **低-中**。改动点多（5 处），但均为防御式（先清僵尸再判定），不改变互斥语义。唯一注意：sweep 必须在 `with _active_runs_lock:` 内执行，避免与 `_start.py` 登记竞争。

---

## 6. 明确不动范围（设计标注，工程师勿动）

- `_smart_locator` high 置信 0-token 直执行（规则兜底 + 性能；medium/low 已走 AI，不判缺口）。
- `_rule_extractor_nav.py`（规则兜底层）、op_cache_v2 机制本身（保留）、`adi_signal.py`、`_network_fault.py`。
- nav_graph 空表、authz 越权 [未复核]（待确认项，本轮不修）。
- 根目录垃圾脚本删除（审计 D1-D9）**不在本设计范围**（另立项，避免与修复混提）。

## 7. 工程师实现顺序建议

1. **F4**（10 min）：pre_gate upsert —— 独立、低风险、可立即验证。
2. **F5**（30 min）：active_runs sweep —— 独立、防御式，先解决"步骤衔接"痛点。
3. **F1**（40 min）：失败分类 AI 增补 —— 新增函数 + 两个调用点，fail-open 简单。
4. **F2**（40 min）：稳定性统一 —— 先修 `local_ollama` seed 透传（底层），再逐调用点加 seed/temp。
5. **F3**（60 min）：replay 断言适配 —— 行为语义变化最大，放最后；按"中间态观察 1 轮 → 删补丁"执行。

每项完成即跑对应"自验证方法"，最后统一回归 hospital_sut Step2→5 全流程，比对 PASS/SKIP/FAIL 分布（预期：10 SKIP / 21 PASS / 0 FAIL 保持，TC-REQ-004-I01 由补丁式 SKIP 变为 REPLAY_NO_ACTION SKIP）。

## 8. 风险总表

| 风险 | 等级 | 缓解 |
|---|---|---|
| F3 删除 `_engine_coverage_gap` 后 TC-REQ-004 变 FAIL | 高 | 新逻辑显式置 SKIP(REPLAY_NO_ACTION)；自验证 3.6-2；中间态观察 1 轮 |
| F2 探索确定性化致个别用例 PASS→FAIL | 中 | 逐条核对变化用例；`ai_stability_seed=None` 一键回退 |
| F1 AI 增补误导展示 | 低 | 仅 enrich 不改判定；展示层可标注"AI 结论参考" |
| F5 sweep 误清真运行 | 低 | 仅清"线程已死"或"超 30min"条目；sweep 异常 fail-open |
| F4 upsert 非原子 | 低 | Step 互斥保护；可选唯一索引（历史脏数据需先清理） |
| 关键改动未经强模型复核 | — | 全部标注 [未复核]，交付前用 GLM/DeepSeek 复核 §14 项 |
