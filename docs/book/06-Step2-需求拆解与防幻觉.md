# 第 6 章　Step2 · 需求拆解与防幻觉

## 概述

Step2 把 Step1 的文本事实拆成结构化需求条目，并经过一组防幻觉校验后写入 `requirements` 表。入口是 `step2_breakdown()`（`src/pipeline/step2.py:53`）。当前主路径是 `feature_driven_breakdown()`（`src/pipeline/steps/step2/analyzers/feature_driven.py:73`），其架构定位是：需求文档为骨架（定义"要测什么"），Figma 设计稿为预期行为依据，AI 只从 ≤600 字的切片提取三元组 `(operation, condition, expected_feedback)`。

模块 docstring（`step2.py:4`）给出管道：功能清单加载 → 三层匹配 → AI 提取三元组 → 去重 → 原文核对 → 逆向覆盖 → 置信度评分 → 门禁。`架构点.md:21` 把 Step2 标注为"5+1 步防幻觉"。

## 需求切分

切分由 `_smart_chunk_requirement()`（`feature_driven.py:13`）完成，策略是章节优先 + 超长二次切分，每片 ≤600 字（基础 ≤300，跨模式合并后最大 ≤600，见 `feature_driven.py:25`、`_fd_extraction.py` 的 `_CHUNK_MAX_CHARS`）。Markdown 表格在预处理阶段被 `preprocess_and_extract_tables` 识别为独立切片（`feature_driven.py:129`）。每条需求最终带 `slice_id`，指向 `requirement_slices` 表（`feature_driven.py:349`）。

切片覆盖率检查（`feature_driven.py:196`）统计 `covered_chunks/total_chunks`，未覆盖切片给 AI 两次机会重新提取（`_CHUNK_RETRY_MAX` 轮，`:207`）。最终 `coverage_rate = round(100.0 * covered / total, 1)`（`:238`）。

## "5+1 步防幻觉"的所指

`架构点.md:21` 仅以"5+1 步防幻觉"作为 Step2 的标签，代码内未显式枚举"5"。按代码实际存在的防幻觉防线，可对应到两处真实结构：

1. `breakdown_rules/base.py` 定义了 5 个规则阶段分组——预处理（preprocess）、提取（extract）、校验（check）、清洗（clean）、标签（label）（`:47`/`:61`/`:75`/`:89`/`:103`），每个分组内含具体规则（如 `title_filter.py`、`numbered_list.py`、`table_rules.py`）。
2. Step2 管道末端的"门禁"构成"+1"：三车道路由（`step2.py:637`）与入库前字段完整性门禁（`step2.py:690`）。

除规则阶段与门禁外，真正的防幻觉硬约束落在下面两节所述的"两阶段原文核对"与置信度评分上。

## 两阶段原文核对

防幻觉的核心是"每条需求都能在原文中找到依据"与"原文每段都被需求覆盖"两个方向的核查。

**阶段一（前向）：5a 子串匹配。** `_verify_source_quotes_in_chunks()`（`_fd_verification.py:38`，由 `feature_driven.py:304` 调用）校验每条需求的 `source_quote` 是否为其所属切片（`slice_id` 指向）的子串。匹配文本先做空白压缩与转小写，再判断 `quote_compact in chunk_compact`（`:117`）。v23 修复放宽范围：构建"扩展切片"=当前切片 + 前切片 + 后切片（`:68`），解决 AI 跨切片摘录被误判 `unverified` 的问题。三级信任模型：`is_implicit=0`（L1 显式）必须核对；`is_implicit=1/2`（L2/L3 隐性）跳过核对（`:55`）。

**阶段二（逆向）：逆向覆盖。** `_check_reverse_coverage_v2()`（`feature_driven.py:311`，定义于 `_fd_verification.py`）基于 RAG 功能索引判断每个切片是否被至少一条需求覆盖。该结果同时被 `confidence_scorer` 的 `section_covered` 维度消费（`confidence_scorer.py:14`），成为置信度的一项输入。

## 置信度怎么算

Step2 的置信度由结构指标评分（零 AI 调用）产出，替代旧的多视角交叉验证。`confidence_scorer.py` 的 `compute_confidence_scores` / `score_requirement` 用五个权重求和（`confidence_scorer.py:37-41`）：

| 维度 | 权重 |
|:---|:---:|
| `source_traceable`（source_quote 非空） | 0.30 |
| `source_verified`（引文真实存在于切片） | 0.25 |
| `section_covered`（所在切片被覆盖） | 0.20 |
| `uniqueness`（与其他需求低重叠） | 0.15 |
| `description_specific`（描述 >20 字） | 0.10 |

档位阈值 `HIGH=0.85`、`MEDIUM=0.60`（`confidence_scorer.py:44-45`）。此外 `evidence/confidence.py:257` 的 `compute_evidence_confidence` 把结构分与历史校准（`confidence_calibration` 表）、独立证据强度融合，输出 `_evidence_confidence`、`confidence_level`、`verdict`。Step2 把 `_evidence_confidence` 单写回 `requirements` 表（`step2.py:894`），并把分布写入 `ai_health_metrics`（`step2.py:667`）。

## 三车道路由与门禁

置信度评分后做三车道路由（P0-3 修复，原 `conf>=80→approved` 二值判定改为三档，`step2.py:611`）：`>=85` → `approved`（green）、`[60,85)` → `pending_review`（yellow）、`<60` → `blocked`（red）。`embed` 合并触发的切片强制 `pending_review`（`step2.py:629`）。最后一道防线是入库前字段完整性门禁（`step2.py:690`）：`module` 长度 <2 或 `description` 长度 <10 的需求被过滤，全部未通过则拒绝入库。

## 澄清会话（clarification）机制

`clarification_service.py` 的 `ClarificationService`（`clarification_service.py:442`）把 `ambiguity_report` 产出的高危歧义项升级为可交互问答闭环，落地于 `clarification_sessions` 与 `clarification_answers` 表：

- `create_sessions`（`:448`）：歧义报告 → 持久化澄清会话，状态 `open`。
- `get_session`/`list_sessions`（`:595`/`:611`）：查询会话与问题。
- `submit_answer`（`:644`）：逐条回答 → 回填 `requirements` 字段（按歧义类型映射：`missing_precondition→condition_expr`、`conflicting_field_semantics/non_unique_expected/unquantified_constraint→expected_feedback`、`undefined_entity→operation`，见 `_build_backfill` `:283`）。
- `release_gate`（`:742`）：解除生成闸门，状态 `released`。
- `is_gate_open`（`:786`）：闸门语义 = 任意 `blocking=1` 且 `status != 'released'` 则关闭。

状态机：`open → answered → released`；`open → skipped`（保持阻塞）。无 MySQL 时降级文件持久化 SQLite（`_get_db`，`:45`）。

## 结果落到哪些字段

需求经由 `src/pipeline/utils/requirement_store/crud_mixins/_write.py:145` 的 `INSERT INTO requirements` 写入。关键字段及来源：

| 字段 | 来源/含义 |
|:---|:---|
| `req_id` | `REQ-001` 顺序编号（`feature_driven.py:372`） |
| `slice_id` | 所属切片（防幻觉锚点） |
| `module` / `description` | 规则+AI 提取 |
| `operation` / `condition_expr` / `expected_feedback` | 三元组；澄清回填落此处 |
| `source_quote` | 5a 子串匹配事实源（`_fd_verification.py:38`） |
| `_verify_status` | passed / unverified / skipped_implicit 等 |
| `_evidence_confidence` | 结构置信度（`step2.py:894` 回写） |
| `is_implicit` | L1/L2/L3 三级信任 |
| `review_status` | approved / pending_review / blocked |
| `contains_sensitive_data` | 敏感数据检测（`step2.py:598`） |
| `payload_json` | 完整性建议等扩展字段 |

切片本身写入 `requirement_slices`（`feature_driven.py:353`）。

## 设计取舍

- AI 只处理 ≤600 字切片而非全文，把"理解"风险锁进小片段，代价是多一次切片与重试的编排复杂度。
- 5a 子串匹配从全文改为切片内（再放宽到扩展切片），精准度与召回率之间用扩展切片平衡；阈值 0.85/0.60 与置信度评分权重均为代码常量，可经配置覆盖。
- 澄清会话独立成领域服务并降级 SQLite，与后端路由解耦，代价是双后端（MySQL/SQLite）路径都要维护。

## 本章代码依据

- `src/pipeline/step2.py`：`step2_breakdown`（:53）、三车道路由（:637）、入库门禁（:690）、`_evidence_confidence` 回写（:894）、置信度分布落库（:667）。
- `src/pipeline/steps/step2/analyzers/feature_driven.py`：主入口（:73）、切片（:13）、覆盖率+两次机会（:196）、5a 调用（:304）、逆向覆盖调用（:311）、切片持久化（:353）、编号（:372）。
- `src/pipeline/steps/step2/analyzers/_fd_verification.py`：`_verify_source_quotes_in_chunks`（:38）、扩展切片（:68）、三级信任（:55）。
- `src/pipeline/steps/step2/analyzers/confidence_scorer.py`：权重（:37-41）、阈值（:44-45）。
- `src/pipeline/evidence/confidence.py`：`compute_evidence_confidence`（:257）。
- `src/pipeline/steps/step2/breakdown_rules/base.py`：5 规则阶段（:47/61/75/89/103）。
- `src/pipeline/steps/step2/clarification_service.py`：`ClarificationService`（:442）、`create_sessions`（:448）、`submit_answer`（:644）、`release_gate`（:742）、`is_gate_open`（:786）、`_build_backfill`（:283）。
- `src/pipeline/utils/requirement_store/crud_mixins/_write.py`：`requirements` INSERT（:145）。

## 待核实

- "5+1 步防幻觉"在 `架构点.md:21` 仅为标签，代码内无显式枚举。本章将"5"映射到 `breakdown_rules/base.py` 的 5 个规则阶段、"+1"映射到门禁，属合理推断，需作者确认是否为官方口径。
- ~~两处对 `source_verified` 的表述是否矛盾~~ → **已核实（2026-10-15）：不矛盾，是三档评分而非二选一。** `steps/step2/analyzers/confidence_scorer.py:191-203` 实测：`source_verified = 1.0`（5a 子串匹配通过）/ `0.7`（切片级修正后通过 / 5a 部分匹配）/ `0.0`（核对未通过，且注释明确"不再兜底 0.3"）。即**全文命中给满分、切片命中给 0.7**——两处注释各描述了其中一档，非冲突。
- ~~两处建表定义是否一致~~ → **已核实（2026-10-15）：列名与类型一致，差异只在索引与注释。** 逐列比对 `clarification_service.py:81-97`（sqlite）、`:118-137`（MySQL）与 `schema.sql:1942-1975`：sessions 表的列（session_id/project/version_id/run_id/req_id/ambiguity_item_id/ambiguity_type/severity/blocking/status/clarify_questions/backfill_json/created_at/updated_at）**列名与类型完全一致**，三处都有 `UNIQUE KEY uk_clr_session_id` 与 `idx_clr_session`、`idx_clr_status`；answers 表 6 列一致，但 **`clarification_answers` 的 `idx_clr_ans_session` 索引只在 `schema.sql:1974` 有**，服务的 MySQL 建表（`:142-150`）只有 `uk_clr_ans` —— 若库由服务自建（旧库升级场景）会少一个索引，属轻微不一致，不影响正确性。
- ~~"三层匹配"是否仍被执行~~ → **已核实（2026-10-15）：仍在执行并被消费。** `src/pipeline/step2.py:4` 流程 docstring 仍写"功能清单加载 → 三层匹配 → AI 提取三元组 → …"，`:61` 描述"关键词 → 嵌入 → 标记未文档化"；`:926` 把 `_breakdown_stats.get("match_stats", {})` 往下游传。即三层匹配是 `feature_driven_breakdown` **内部的**匹配阶段，不是与之并列的另一条路径。
