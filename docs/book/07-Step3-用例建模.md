# 第 7 章　Step3 · 用例建模

## 概述

Step3 把 Step2 落库的 `requirements_breakdown` 表（结构化需求）转换为可执行的 `TestCase {case_id, actions, checks}` 资产，并写入 `test_cases` 与 `case_meta` 两张表。本章覆盖 `src/pipeline/step3/`（编排层）与 `src/pipeline/steps/step3/`（能力层）。

编排层与能力层是两个不同目录，职责不重叠：`src/pipeline/step3/` 决定"这一步做哪些子动作、按什么顺序"，`src/pipeline/steps/step3/` 实现具体能力（用例生成、规则、xmind 等）。依赖方向为编排层 → 能力层。

整体采用"规则优先 + AI 补漏"架构：先用确定性规则产出用例，规则覆盖不到的需求才调用 LLM（`_modeling_engine.py:461` 注释）。

```text
requirements_breakdown
   │  _read_step3_requirements (__init__.py:84)
   ▼
_select_requirements_for_case_modeling (__init__.py:184, 每条需求≥3用例)
   ▼
_model_full_intents → _parallel_model_all_reqs (_modeling_engine.py:421)
   ├─ extract_cases_by_rules (规则层, _rule_extractor.py:693)
   ├─ _build_rule_fallback_cases (全量保底层, _fallback_cases.py:1473)
   └─ model_test_cases (AI 补漏, _model.py:273 → _call_api:544)
   ▼
_write_intents_to_store → test_cases + case_meta (_modeling_intents.py:77)
```

## 1. 整体流程：需求读出 → 用例生成 → 落库

入口是 `step3_model_cases`（`src/pipeline/step3/__init__.py:53`）。它先恢复 Step1 上下文（`_restore_step1_contexts`），再读需求：

- `_read_step3_requirements(excel_handler)`（`__init__.py:84`）从 Excel/MySQL 的 `requirements_breakdown` 读取结构化需求。
- 数据完整性校验 `validate_step_input`（必填 `description`/`module`）（`__init__.py:88`）。
- `_select_requirements_for_case_modeling`（`__init__.py:184`）按 `MIN_INTENTS_PER_REQ`（默认 3）筛出"已覆盖"与"待建模"需求；`force` 模式清空 `test_cases` 与追踪器后全量重跑（`__init__.py:145`）。

用例生成由 `_parallel_model_all_reqs`（`_modeling_engine.py:421`）并行执行。`ThreadPoolExecutor` 的 `max_workers` 取 `config.step3_parallel_workers`（默认 10）（`_modeling_engine.py:591`）。每条需求一个 worker（`_worker`，`_modeling_engine.py:634`）。

落库在 `_write_intents_to_store`（`_modeling_intents.py:77`）：`case_store.upsert_intents` 批量写入 `test_cases`（`_modeling_intents.py:462`），`excel_handler._write_case_meta` 写 `case_meta`（`_modeling_intents.py:509`）。写库前经过三道确定性闸门：`resolve_intents_batch` 导航解析（`:412`）、`sanitize_intents` 检查项守卫（`:423`）、`assign_intent_identities` 身份指派（`:445`）。

## 2. AI 生成与规则生成两条路径的分工

**规则路径（确定性，不调用 LLM）：**

1. `extract_cases_by_rules`（`_rule_extractor.py:693`）：需求先过规则提取前置层。成功返回用例列表，失败返回 `None`（`_modeling_engine.py:522` 调用，`:528` 判断）。grep 验证该文件无 `_call_api`/`ai_brain` 调用。
2. `_build_rule_fallback_cases`（`_fallback_cases.py:1473`）：在 AI worker 启动前对**全部**需求跑一遍，产出 6 类（功能/UI/安全/性能/易用/兼容）× 多轨的确定性用例（`_modeling_engine.py:539`）。

**AI 路径（真实 LLM 调用）：**

- 规则未覆盖的需求进入 `_ai_pending_reqs`（`_modeling_engine.py:520`、`:584`）。
- worker 内：`should_run_cross_validation(risk_level)` 为真则 `generate_with_cross_validation`（`_modeling_engine.py:684`、`:692`），否则 `model_test_cases`（`_modeling_engine.py:740`）。
- `model_test_cases`（`steps/step3/case_generator/_model.py:273`）在 `_model.py:544` 调用 `_ai_brain._call_api`，校验未过则 `_model.py:795` 重试再次 `_call_api`。
- quick 模式（`__init__.py:379`）直接 `ai_brain.model_test_cases`，失败退回 `_build_rule_fallback_cases`（`__init__.py:394`）。

**诚实说明：** `_build_rule_fallback_cases` 的文档串写"AI 不可用时使用"，但实测它在 `_modeling_engine.py:541` 对**所有**需求无条件运行，是"规则优先保底层"反转（2026-09-16）后的常态行为，并非仅在 AI 不可用时。其后还有去重逻辑（`_modeling_engine.py:990`）：同一 `(req_id, type)` 若已被规则兜底覆盖，则删除重叠的 AI 用例，保留确定性版本。

`case_rules/base.py`（122KB）是另一条规则路径——注册式检查发射器（button_rule/input_rule 等）。grep 确认其仅一行注释含 "LLM" 字样，无任何 `_call_api`/`ai_brain` 调用，属于确定性规则。

RAG 检索（`_enrich_requirements`，`_modeling_engine.py:255`）只做向量召回与过滤，不参与用例生成，规则层与 AI 层共用其返回的上下文。

## 3. 规则提取器的分工与权威源

`_rule_extractor.py` 是聚合层（re-export 各子模块），权威源按信息类型分散：

| 信息域 | 权威源文件 | 关键符号 |
|:---|:---|:---|
| 极性 / 拒绝 / 登录 / 接口语义 | `_rule_extractor_keywords.py` | `_DENY_RE`、`is_login_operation`、`is_api_operation`、`_is_system_integration_requirement`（`:27`） |
| DOM 控件 / 页面元素 | `_rule_extractor_dom.py` | `_SEARCH_UI_BLACKLIST`（词表唯一源，`_rule_extractor.py:46` 注明）、`_load_dom_elements_for_page` |
| 导航信息 | `_rule_extractor_nav.py` | `_find_nav_path_for_req`、`_score_nav_match` |
| API 端点 | `_rule_extractor_api.py` | `_match_apis_from_rag_chunks`、`_parse_rag_context` |
| actions 构建 | `_rule_extractor_actions.py` | `_build_actions`、`_infer_click_target` |
| checks 构建 | `_rule_extractor_checks.py` | `_build_checks`、`_build_ui_check_by_operation` |
| 控件类型推断 | `_rule_extractor_downgrade.py` | `_infer_control_type_from_operation` |

近期"词表唯一源"治理后，`_rule_extractor_keywords.py` 是极性、拒绝、登录、接口类语义的权威源：`_identity_assign.py:25` 复用 `_DENY_RE` 而非自持第二份词表；`_fallback_cases.py:1495` 复用 `is_api_operation`。DOM 词表唯一源在 `_rule_extractor_dom.py`（`_rule_extractor.py:46` 显式标注"词表唯一源在 _rule_extractor_dom"）。

`extract_cases_by_rules` 的数据流（`_rule_extractor.py:711` 起）：加载 Step2 的 `trace_links` 精确关联 → 从 `rag_chunks` 补充 API 详情（`_enrich_apis_with_rag_details`）→ 无关联时按语义匹配（`_match_apis_from_rag_chunks`，注释称覆盖率从 58.3% 升到 95%+）→ 推断目标页面（`_match_page_path`）→ 组合可执行用例。

## 4. checks 的 track/precision 判定与 case_meta 字段

`checks` 每项形如 `{track, precision, check, expect}`。

- **规则路径**：`track` 与 `precision` 在 `_rule_extractor_checks.py` 的各 builder 中**硬编码**，无运行时判定。实测取值规律：UI 类（`element_exists` 等）多为 `ui/fuzzy`（`:140`、`:185`、`:646`）；API 类成功路径 `api/exact`（`track:api, precision:exact`, `:328`、`:371`、`:495`），异常/边界路径 `api/fuzzy`（`track:api, precision:fuzzy`, `:341`、`:385`、`:513`）；DB 类 `db/exact`（`track:db, precision:exact`, `:576`、`:613`）。
- **AI 路径**：`track`/`precision` 由 LLM 在生成 JSON 时直接输出（`_model.py` 透传），无规则介入。
- **落库前定稿**：`sanitize_intents`（`_check_guard.py:599`）是唯一咽喉点——剔除执行注册表未注册的 check 类型、极性过滤、按剩余 checks 重算 `required_tracks`（`_check_guard.py:586`）。

`case_meta` 表由 `_write_case_meta`（`excel_handler.py:424`）写入，Step3 传入的字段（`_modeling_intents.py:509`）：`score`（置信度，来自 `score_test_case`）、`risk`、`case_metrics`、`risk_embedding`、`method_valid`、`method_fix_note`。指标另由 `review_store.write_case_metrics_batch` 批量写入（`_modeling_intents.py:497`）。`test_cases` 表承载 `case_id/req_id/actions/checks` 主资产，以及透传的 `source_quote`、`feature_id`、`module`、`is_implicit`、`trust_level`、`identity`、`test_method`（`_modeling_intents.py:182`）。

## 5. 三车道审核 review_lane 的产生与消费

`review_lane` 表（`green/yellow/red`）的产生**不在 Step3**，而在 Step2：`step2.py:761` 调用 `write_review_lanes`（`review_lane_store.py:15`），写入 `lane`、`routed_to_human`（`green→0`，其余→1）、`reason`（`review_lane_store.py:61`）。

消费点：
- Step4 执行前闸门 `_execute_one_intent` → `check_lane_gate`（`steps/step4/browser_core/single_mixins/_base.py:103`），`red` 车道阻断执行。
- Web 审核路由 `case_review/_routes.py` 读 `read_case_lanes`（`_routes.py:469`），`review_lane` 无数据时拒绝批量审核（`:617`、`:634`）。

**诚实说明：** Step3 自身的"测试用例审核闸门"（`__init__.py:611`）读的是 `review_store.list_case_meta()` 的 `review_status`（`approved`/`modified`/`pending`），**不是** `review_lane`。即 Step3 核心不消费三车道路由，三车道由 Step2 产出、Step4 与 Web 审核消费。详见「待核实」。

## 6. 方法码 / catalog / taxonomy 关系

单一权威来源是 `src/testing_methods/catalog.py`：

- `METHOD_CODES`（18 码）、`METHOD_CATALOG`、`CATEGORY_LABELS`（`catalog.py:54-66`）——该文件注释明确"收敛为单一权威来源，_test_method_rules.py 与 _test_methods.py 均从此处读取"（`:4`）。
- 18 码分类：黑盒经典 8（EP/BV/CE/DT/ST/OT/EG/FD）、黑盒规格 1（SD）、白盒 1（PC）、安全 4（SX/SI/CS/AU）、Web 专属 4（SS/CT/PF/ET）（`catalog.py:13`）。
- `UNDECIDED_METHOD_MARKERS` 是"未决定"标记码唯一事实源（`catalog.py:71`），制造层与校验层共用。

下游关系：

- `_test_method_rules.py` 从 `catalog` 导入 `METHOD_CODES`/`UNDECIDED_METHOD_MARKERS`（`:26`），`TEST_METHOD_RULES`（`:41`）把每个码映射到 `verify_templates`/`checks_templates`；`canonical_method_code` 把 `FD`/未决定就近归一到权威码；`apply_method_rules` 把模板叠加进 `verifications`。
- `explore_task_builder._build_default_test_method` 在 AI 未给 `test_method` 时生成默认 `"FD"`（`_explore_task_builder.py:332`）。
- `_model.py` 在 `_inject_method_and_action_templates`（`:99`）调用 `canonical_method_code` 统一解码写回（`_model.py:134`），并在写库前 `_modeling_engine.py:1087` 再次校准。
- `_methodology_validator.py` 对 `scenario_type`/`test_method` 做白名单 + 配额校验，低危自动修正打 `method_fix_note`，高危置 `method_valid=0` + `blocked`（`_modeling_engine.py:1154`）。

**诚实说明：** `taxonomy` 一词在源码中无独立模块/常量，关系上 `catalog.py` 即方法码的 taxonomy 唯一权威，命名差异不影响结论。

## 7. _fallback_cases 兜底路径的触发条件

`_build_rule_fallback_cases`（`_fallback_cases.py:1473`）在三种条件下被调用，且内部无 LLM 调用（grep 验证无 `_call_api`/`ai_brain`）：

1. **全量保底层**（常态）：`_parallel_model_all_reqs` 在 AI worker 启动前对**全部**需求调用（`:541`，`reason="规则优先保底层"`），作为确定性底座。
2. **AI 失败兜底**：worker 返回 `intents` 为空的需求（`_ai_pending_reqs` 中执行失败者）在 `:968` 循环里调用（`:976`，`reason="AI生成失败，使用规则兜底"`）。
3. **quick 模式**：`__init__.py:394` 在 `ai_brain.model_test_cases` 失败分支调用。

**内部跳过条件：** 接口操作型需求（经 `is_api_operation` 判定）直接 `return []`（`_fallback_cases.py:1497`），不产浏览器轨兜底——其功能验证由 API 轨意图用例承担，避免把 HTTP 文案当页面断言造成假 ERROR。

## 8. 用例质量三层保障（case_quality，S3-014）

`src/pipeline/steps/step3/case_quality/` 是 S3-014 规格设计的"三层质量保障"落点，统一编排入口为 `orchestrator.py:22`。三层按执行时机分布：

| 层 | 时机 | 实现 | 接入点 |
|:---|:---|:---|:---|
| 1 Few-Shot | 生成前 | `_load_few_shot_examples`/`_format_few_shot`（`few_shot.py:104`/`:186`） | `case_generator/_contexts.py:345` 注入 prompt |
| 2 交叉验证 | 生成中 | `should_run_cross_validation`（`orchestrator.py:80`）+ `generate_with_cross_validation`（`cross_validation.py:10`） | `_modeling_engine.py:684`/`:692` |
| 3 语义去重 | 写入前 | `apply_batch_dedup`（`orchestrator.py:22`）→ `deduplicate_intents`（`dedup.py:52`） | `_modeling_intents.py:278` |
| （附）指标 | 落库前 | `compute_case_metrics`（`metrics.py:33`） | `excel_cases/_write.py:142` |

- **层 2 阈值可配**：`should_run_cross_validation(risk_level)`（`orchestrator.py:80`）读 `pipeline_config.cross_validation_risk_threshold`（`:97`），取值 `high`（默认，仅高风险触发）/`medium`（中+高）/`low`（全触发）。命中则走双模型取交集 `generate_with_cross_validation`，结果挂 `_cross_validation` 字段（`_modeling_engine.py:705`）；它与 `model_test_cases` 是同一 `if` 的两条互斥分支（`:684`）。
- **层 3 追踪信息**：`apply_batch_dedup` 返回 `{kept, duplicates, duplicate_count, total_input, dedup_applied}`（`orchestrator.py:34-41`）；去重异常被捕获、原样返回不阻塞写入（`:69-77`）。除批量写入路径外，`dedup_remodel`（`dedup.py:185`）供 `case_review/_remodel.py:108` 的"重建模"路径复用；`_case_quality_score`（`dedup.py:261`）与 `_cosine_sim`（`:308`）是去重评分底座。
- **层 1 配置**：Few-Shot 上下文长度由 `step3_ctx_few_shot`（默认 900，范围 100–3000，`pipeline_config.py:630`）控制，组合开关 `combo_few_shot_enabled`（`pipeline_config.py:824`）。

注：本节的三层质量保障与 §2 的"规则兜底 `(req_id, type)` 重叠去重"（`_modeling_engine.py:990`）是两套不同机制——前者是生成侧的语义去重/交叉验证/Few-Shot，后者是规则层与 AI 层同 `(req_id, type)` 的重复消除。

## 9. 反向覆盖生成（step3_inverse，v0.1）

`src/pipeline/steps/step3_inverse/` 在正向建模完成后，对正向意图做"反向全面覆盖"——生成异常/攻击/顺序异常路径的**结构化意图**，回灌 Step3 落库后由 Step4 主流程执行（包文档串 `__init__.py:8-15`）。它与 Step4 `browser_core/single.py` 的"AI 兜底循环"不同：后者是动作无法结构化时的运行时救场，本模块是**生成阶段**的意图扩展（`__init__.py:12-15`）。

- **开关与接入**（`src/pipeline/step3/__init__.py:405-448`）：由前端开关 `SECURITY_INVERSE_ENABLED` 控制（经 `_read_config_value`，`:416`），未开时回退 `step3.inverse_enabled`（默认 `off`，`:418-422`）。开启后 `generate_inverse_intents(case_store.list_intents())`（`:433`）产出反向意图，经 `excel_handler.parse_and_write_intents` 落库（`:439`）。该回退键读自 system yaml（`src/core/config.yaml`）。
- **三个反向维度**（`step3_inverse/__init__.py:80-85`）：
  - `replay` — 重放/幂等/防重提交（`replay.py:20`）。v0.1 仅产出 1 条"重放×3"，且用粗匹配判定（`replay.py:29`）。
  - `security` — 越权/注入/CSRF/XSS/敏感泄漏五类探测（`security.py:124` `_expand_impl`），是三维度中实现最完整的一支（`:130-146`）。
  - `ordering` — 执行顺序异常（`ordering.py:21`）。v0.1 仅实现 `step_skip`（跳过首个 `fill` 步骤，要求正向意图 steps ≥ 3，`:29-31`）；`step_reverse`/`mid_back`/`concurrent` 均标注 `TODO（v0.2）`（`:36-38`）。
- **诚实说明**：包文档串（`__init__.py:30-36`）自述"★ v0.1 骨架 ★……当前每个生成器只产出最小可运行占位……反向规则待正向流程跑通后再补全"。据此，当前真实状态应表述为：安全维度已成体系（5 类探测），重放与顺序维度仅为最小占位；反向意图的"通过"判据（防御生效=通过 vs 攻击成功=失败）尚未落地（同文档串 `:36`）。默认开关 `off`，未开启时该能力不参与流水线。

## 10. 冒烟用例生成（smoke_generator）

除逐需求的完整用例建模外，Step3 还提供一条**并行产出**：为每个功能模块生成 1 条端到端 Happy Path 冒烟用例，打包成带层级目录的 Excel（`src/pipeline/steps/step3/smoke_generator/`，包文档串 `__init__.py:4-15`）。

- **主入口**：`generate_smoke_excel(...)`（`__init__.py:43`）按功能模块分组，经 `ThreadPoolExecutor` 分发到 `_gen_one_module`（`:122`）逐模块生成；AI 调用使用 `_sg_cfg.ai_model`（`:154`、`:162`）。缓存清理由 `clear_smoke_cache`（`:323`）负责。
- **缓存优先**：有缓存直接返回，无缓存才 AI 生成（`__init__.py:4-7`）。产物为 base64 编码的 Excel，写入 MySQL `source_documents(doc_type='smoke_excel')`；`cache.py` 提供 5 个 MySQL 读写函数（write/get/get_latest/delete/list）。
- **Excel 构建**：`excel_builder.py` 负责 `EXCEL_HEADERS`（`:14`）、`_sanitize_sheet_name`（`:33`）与 `_build_smoke_excel`（`:41`）；`prompts.py` 提供 `_SMOKE_EXCEL_SYSTEM_PROMPT`。
- **消费**：Web 路由 `/api/docs/generate-smoke`（`src/backend/routes/docs/docs_smoke.py:113,117`）与文档中心"冒烟"Tab（第 19/20 章）。清理路径在 `backend/executor/_clear.py:168,182`（`doc_type IN ('smoke','smoke_excel')`）。

它与主用例集的定位差异：冒烟集是"每模块一条主干路径"的快检基线，主用例集（`test_cases`）是逐需求的完整覆盖——两者落库的 `doc_type` 不同，互不覆盖。

## 设计取舍

- **规则优先而非 AI 优先**：省 token、降幻觉。规则兜底产出的用例带 `_generation_source="local_rule_fallback"`，与 AI 用例按 `(req_id, type)` 去重，确定性版本优先（`_modeling_engine.py:990`）。
- **规则层拆分权威源**：DOM 词表、极性词表、导航信息各归其文件，避免多处分叉漂移；`extract_cases_by_rules` 只做语义匹配与组合，不写业务硬编码（`_rule_extractor.py:20` 设计原则）。
- **track/precision 双轨**：规则层硬编码、AI 层 LLM 输出，落库前 `sanitize_intents` 统一清洗。牺牲了 AI 层的判别自由度，换取可执行性保证。
- **三车道外置**：review_lane 在 Step2 产出、Step4 消费，使 Step3 不被审核状态阻塞；代价是 Step3 暂停闸门需另读 `case_meta.review_status`。

## 本章代码依据

- 编排入口：`src/pipeline/step3/__init__.py:53`（`step3_model_cases`）、`:84`（读需求）、`:184`（覆盖筛选）、`:379`（quick AI）、`:611`（审核闸门）。
- 并行引擎：`src/pipeline/step3/_modeling_engine.py:421`（`_parallel_model_all_reqs`）、`:522`（规则提取）、`:539`（兜底保底）、`:591`（workers）、`:740`（AI 调用点）、`:990`（去重）、`:1154`（方法论校验）。
- 规则层：`_rule_extractor.py:693`、`_rule_extractor_keywords.py:27`、`_rule_extractor_dom.py`、`_rule_extractor_nav.py`、`_rule_extractor_checks.py:140` 等。
- 兜底：`src/pipeline/step3/_fallback_cases.py:1473`、`:1497`。
- AI 层：`src/pipeline/steps/step3/case_generator/_model.py:273`、`:544`、`:795`、`_inject_method_and_action_templates:99`、`canonical_method_code:134`。
- 检查守卫：`src/pipeline/step3/_check_guard.py:600`（`sanitize_intents`）。
- 身份指派：`src/pipeline/step3/_identity_assign.py:245`（`assign_intent_identities`）。
- 落库：`src/pipeline/step3/_modeling_intents.py:77`、`:462`、`:509`；`src/core/excel_handler.py:424`。
- 方法码：`src/testing_methods/catalog.py:54`；`src/pipeline/steps/step3/case_generator/_test_method_rules.py:26`、`:41`。
- 三车道：`src/pipeline/step2.py:761`；`src/pipeline/utils/review_lane_store.py:15`；`src/pipeline/steps/step4/browser_core/single_mixins/_base.py:103`。
- 用例质量三层保障：`src/pipeline/steps/step3/case_quality/orchestrator.py:22,80,97`；`few_shot.py:104,186`；`cross_validation.py:10`；`dedup.py:52,185,261,308`；`metrics.py:33`；接入 `_modeling_engine.py:684,692,705`、`_modeling_intents.py:278`、`case_generator/_contexts.py:345`、`excel_cases/_write.py:142`；配置 `pipeline_config.py:630,824`。
- 反向覆盖：`src/pipeline/steps/step3_inverse/__init__.py:48,80-85`；`ordering.py:21,29-38`；`replay.py:20,29`；`security.py:115,124`；接入 `src/pipeline/step3/__init__.py:405-448`。
- 冒烟用例生成：`src/pipeline/steps/step3/smoke_generator/__init__.py:43,122,323`（`generate_smoke_excel`/`_gen_one_module`/`clear_smoke_cache`）、`:154,162`（AI 调用）；`excel_builder.py:14,33,41`；`prompts.py`（`_SMOKE_EXCEL_SYSTEM_PROMPT`）；消费 `src/backend/routes/docs/docs_smoke.py:113,117`、`backend/executor/_clear.py:168,182`。

## 待核实

1. **review_lane 的消费归属**：据读到的代码，Step3 暂停闸门读的是 `case_meta.review_status`（`__init__.py:622`），不读 `review_lane` 表；`review_lane` 的实际消费在 Step4 车道闸门与 Web 审核路由。需确认 Step3 是否存在其他分支直接消费 `review_lane`（当前未见）。
2. **规则两层重叠比例**：`extract_cases_by_rules`（返回非空时需求不进 AI）与 `_build_rule_fallback_cases`（全量保底）同为规则路径，二者对单需求的覆盖边界（是否重复产出）需实测统计确认。
3. ~~step3 文件计数~~ → **已核实（2026-10-15）：step3 有两个目录，合计 86 个 .py。** `src/pipeline/step3/` = 52 个（根 31 + `case_rules/` 17 + `project_adapters/` 4）；`src/pipeline/steps/step3/` = 34 个（根 5 + `case_generator/` 13 + `case_quality/` 6 + `oracle/` 4 + `smoke_generator/` 4 + `nav_seed/` 2）。"30 个文件"的旧口径不成立。
4. ~~极性反转的判定条件~~ → **已核实（2026-10-15）。** 位置为 `src/pipeline/step3/_check_guard.py:525-548`（本章原引 `steps/step3/_check_guard.py:619` 路径有误——该文件在 `src/pipeline/step3/` 下，无 `steps/` 层）。判定条件是**不用正则**：当断言类型为 `no_error`（即"不应报错"）时，从 `case.expected_feedback` / `_explore_task.expected_feedback` / `case.expected_result` 三个候选里取第一个满足 `expects_error_feedback()` 的文本作为 `_req_expect`（取不到时兜底为"系统应拒绝该操作并给出错误提示"），把 `check.type` 改写为 `semantic_check`、`check.expected` 改写为 `_req_expect`，并打标记 `_polarity_flipped="no_error"`。目的见 `:543-544` 自述：保住需求"应被拒绝"的判定能力，防探针-only 假 PASS。
5. **反向覆盖的实测状态**：`step3_inverse` 三维度中仅 `security` 成体系，`replay`/`ordering` 为 v0.1 占位（见 §9）。本次仅静态读码，未在开启 `SECURITY_INVERSE_ENABLED` 的真实轮次中统计反向意图产出条数与 Step4 执行结果；该开关默认 `off`。
6. ~~Few-Shot 的数据源与降级~~ → **已核实（2026-10-15）。** 实现在 `src/pipeline/steps/step3/case_quality/few_shot.py:104`（本章原引 `_contexts.py:345` 系旧路径）。**数据源**：MySQL，SQL 见 `:133-141` —— `test_cases` INNER JOIN `executions`(e.status='PASS') LEFT JOIN `requirements`(取 module)，筛 `risk_level IN ('P0','P1')`，module 命中用 `r.module=%s OR r.module LIKE %s%`，`GROUP BY case_id ORDER BY risk_level, MAX(executed_at) DESC LIMIT 5`。即"高质量"= **曾真实执行 PASS 且风险等级 P0/P1**。**降级**：`config.project_name` 为空直接返回 `[]`（`:121-122`）；`module` 为空则去掉模块过滤只按项目取（`:143-152`）；标题为空的行跳过（`:159-160`）；整体包在 `try + guard` 内，DB 异常不冒泡。
