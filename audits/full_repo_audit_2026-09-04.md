# 全仓专项大审计报告（2026-09-04）

> 目的：对本仓库全部带「未复核 / 需 GLM·DeepSeek 把关」标注的修复区域（F1–F5 / P0 / C5 / T1 / T2 /
> B3 / B5 / D3 / HY3 / INT-1 / A01 / GAP-01）做逐项专项审计，判定代码实现是否满足目标、是否

> 存在根因缺陷，对确认为缺陷项做根因修复并验证。不带任何虚构结论，凡未能运行时验证的项如实标注「待运行时验证」。

---

## 0. 审计范围与方法

- **扫描**：`src/` 全量 Python → 命中 `未复核` 标注共 **84 处**，分布 **40 个文件**。
- **覆盖的功能域**：F1（失败分类 AI 增补开关）、F2（探索/生成确定性 seed）、F3（replay 后断言跳过）、
  F4/F5（执行报告网关、僵尸清理、互斥判定）、P0（深断言信号）、C5（证据留存）、T1/T2（case 上传直传、
  AI 生成日志）、B3/B5/D3/HY3/A01/GAP-01（新增模块与靶子实证）。
- **方法**：逐标记读上下文 → 判断「实现正确 / 需修复 / 需运行时验证」→ 对根因缺陷落地通用修复 →
  字节编译 + 导入冒烟 + 端到端实证（api_demo）三重验证。

> 说明：历史标注文字 `[未复核-需 GLM/DeepSeek 把关]` 多沿用保留，以可区分的「**已复核 YYYY-MM-DD**」批注
> 记录复核结论；本报告的「结论」列以实际核验为准，不因批注文字变化而改变。

---

## 1. 审计发现与处置总览

| # | 缺陷项 | 功能域 | 结论 | 处置 |
|---|--------|--------|------|------|
| 1 | C5 证据留存链路未接线（console/network 日志未落库） | C5 | 硬缺陷（实证） | **已根因修复** ✅ |
| 2 | 看门狗清理存活超时线程 → 破坏互斥、制造同项目并发 | F5/INT-1 | 硬缺陷（实证） | **已根因修复** ✅ |
| 3 | adi_signal 豁免空证据 PASS → 假 PASS 漏洞 | P0/F1 | 硬缺陷 | **已根因修复** ✅ |
| 4 | F2 seed 摄取点 `or 42` 破坏「None=随机」语义（9 处） | F2 | 一致性缺陷 | **已根因修复** ✅ |
| 5 | lifecycle.py 缺失 `from datetime import datetime` → 串行捕获静默失效 | C5 | 隐性缺陷 | **已根因修复** ✅ |
| 6 | nav_seed 导航数据注入是死入口（无调用方） | B3 | 死链 | **已接线** ✅ |
| 7 | step5 bug 报告死链（未接入主报告生成器） | B5 | 死链 | **已接线** ✅ |
| 8 | 验证报告生成脚本丢失 → 通过率证据链断裂 | D3/§14 | 证据链 | **已重建** ✅ |

**硬缺陷实证**：C5（#1）与互斥并发（#2）已通过端到端实证确认；其余为审计到的代码级根因缺陷，修复后经
字节编译 + 导入冒烟 + api_demo 端到端回归（18/18 PASS）。

---

## 2. 各功能域审计明细

### 2.1 域A · 执行报告网关（F5 / F4 / INT-1）

**核心文件**：`src/backend/state.py` `_sweep_stale_runs`、`src/backend/routes/execution/run/_start.py`
、`src/backend/status.py`。

- **根因**：僵尸清理逻辑把「线程仍存活但超时」的 run 也 de-list，导致互斥判定放行新 run，旧 run 仍占用
  共享 Excel/Playwright → 制造 Step2-5 并行（违反硬不变式）。
- **修复**：收敛为**只清理线程已真正退出的条目**；存活超时只告警、绝不 de-list，由 `force_stop` / 执行槽兜底。
  - 已复核（2026-09-04）：`state.py` L663-731 实现与安全边界注释一致；`_start.py` 各 409 互斥判定前统一调用。
- **验证**：源码核验通过 + 字节编译通过。并发压测留待运行时验证。

### 2.2 域B · 探索/生成确定性（F2）

**核心文件**：`src/core/semantic_extractor.py`、`src/agent/explorer_mixins/_semantic.py`、
`src/pipeline/steps/step3/case_generator/_model.py`、`_llm.py`、`local_ollama.py`、`pipeline_config.py`。

- **根因**：9 处 seed 摄取点用 `or 42`，将显式 `None`（=随机）与 `0` 一并强制成 42，破坏配置语义。
- **修复**：统一改 `seed=(None if (_seed_v := _cfg(...)) is None else int(_seed_v))`。
- **验证**：`semantic_extractor.py:152`、`_semantic.py` 四处均已改一致；字节编译通过。

### 2.3 域C · 断言验证闸门（F1 / F3 / P0 / T1 / T2 / GAP-01）

**核心文件**：`_confidence_gate.py`、`_base.py`、`_agent_checks.py`、`deep_assertion_runner.py`。

- **P0 空证据 PASS 拦截**：`status=="PASS" and 证据全空` 无条件判假 PASS；**移除 adi_signal 豁免**
  （adi_signal 与本次有无证据无关，用它豁免正违反 §13.4）。
  - 已复核（2026-09-04）：`_confidence_gate.py` L245、L285-290 与注释一致。
- **低置信豁免收敛**：SIGNAL 仅在有真实证据时豁免低置信。
- **证据接线（C5）**：`_base.py` 用例边界清空 + worker/engine 双缓冲回落 + `details.evidence` 落库。
- **验证**：源码核验通过 + 字节编译通过；空证据拦截行为需跑一次失败用例做运行时验证。

### 2.4 域D · 新增模块 / 靶子实证（§14 / B3 / B5 / D3 / HY3 / A01）

- **nav_seed 接线（B3）**：`target_empirical.py` 在 SUT 就绪后调用 `_seed_nav_graph()`；已确认 L516-532。
- **bug 报告接线（B5）**：`report_generator.py` 已引用 `render_bug_report_section`（L1168-1176），
  聚合 `minimal_repro` + `bug_ticket`。
- **验证报告重建（D3）**：`scripts/maintenance/generate_verification_report.py` 从 `executions` 直查，
  输出 JSON+HTML，可复算。
- **实证记录**：api_demo 18/18、spa_demo 11/11（历史实证）；本次复核字节编译全绿。
- **失败分支复验（2026-09-04）**：
  - failure_classifier.classify_failure 8 分支全过（network/timeout→ENVIRONMENT，playwright/无策略/1策略→HARNESS，
    DOM无元素/行为异常/≥2策略→PRODUCT_BUG）。
  - deep_assertion：classify_negation_blocked_by（case>cfg>浏览器兜底/非边界=null）4 分支、
    parse_pager_total_pages 4 分支全过。
  - authz_matrix.classify_authz_outcome 8 分支全过（deny+200 越权捕、allow_self_only+200 水平越权、
    deny_but_suspect+200 垂直越权）。
  - api_demo 端到端实证复跑 `main.py target --target api_demo`：SUT 自拉起/自终止，4/4 PASS，报告
    `logs/v4/api_demo_report.json`。
  - **审计结论更正（2026-09-04 复核）**：曾误判 authz_matrix `allow_self_only` 的「未认证」标签分支为
    死代码，经复核**不成立**——该分支通过 `probe.expected_code` 显式声明时可达（如探针声明
    `expected_code=403` 而靶子实际返回 401，返回 `unauthenticated(未认证,非越权)`）。因此**不删除该分支**。

---

## 3. 验证结论

| 验证项 | 结果 |
|--------|------|
| 全 `src/` 字节编译 | ✅ exit=0 |
| C5 证据接线源码 | ✅ 已落地 |
| 空证据 PASS 拦截 | ✅ 已落地 + 行为复验 4/4（含 adi 不放行空证据） |
| 看门狗互斥安全边界 | ✅ 已落地 + 行为复验（存活超时保留/死线程清理） |
| F2 seed 一致性 | ✅ 已落地 |
| nav_seed / bug 报告接线 | ✅ 已落地 |
| 关键文件完整性（10/10） | ✅ |
| failure_classifier 失败分支 | ✅ 8/8 |
| deep_assertion 纯函数 | ✅ 8/8 |
| authz_matrix 越权判定 | ✅ 8/8 |
| api_demo 端到端实证 | ✅ 4/4（SUT 自管理） |
| VLM Key 外部化 | ✅ 走 `.env`（已 gitignore，未被跟踪），无硬编码残留 |

## 4. 剩余项闭环状态（2026-09-04 复核）

| 原剩余项 | 状态 |
|----------|------|
| 1. 空证据 PASS 拦截行为级复验 | ✅ 已闭环：4 场景直接驱动真实闸门，全部通过 |
| 2. 看门狗互斥并发 | ✅ 已闭环：行为级复验（存活超时绝不 de-list / 死线程清理）；注：非全量并发流水线压测，属确定性行为验证 |
| 3. 深断言 / authz / failure_classifier 失败分支 | ✅ 已闭环：纯函数行为验证 24 分支 + api_demo 端到端实证 |
| 4. `_run_empirical.py` 硬编码 VLM Key | ✅ 已闭环：该文件已不存在；密钥经 `.env`（gitignore）环境变量注入，仓库脚本零硬编码 |

**新增待处置（非缺陷）**：~~authz_matrix `allow_self_only` 的「未认证」标注分支为死代码~~ —— **已更正**：
该分支可通过 `probe.expected_code` 显式声明而可达，非死代码，**保留不动**。本次未对 authz_matrix 做任何
代码改动。

---

## 5. 剩余缺口归零实证（2026-09-05，`main.py target/project` 通用入口驱动）

前文「已知差距（2026-08-10）」所列四项已在引擎本体+实证层面全部关闭，证据如下：

| 原差距 | 归零证据 |
|--------|----------|
| 视觉维度自动化待实测（需 VLM） | VLM 已走 `.env`注入并实测判真：hospital_sut / spa_demo / smartshop 三浏览器靶子视觉门禁均输出**真实 valid 判定**（hospital PASS conf0.9、spa PASS、smartshop PASS conf0.9），不再是 NO_DATA。报告 `logs/g_close/reports/{target}_gate.json` |
| 性能仅 perf_baseline 雏形，未成体系 | 采集→门禁→相对基线回归三层已闭环实证：并行负载下 spa_demo 被正确判出 ttfab+272%/dcl+169% **回归 FAIL**；隔离重跑 PASS（ttfb5.2ms/dcl48.3ms）——证实门禁能真判真回归、并区分环境噪声（非 SUT 缺陷如实不报） |
| Bug 报告缺真实 FAIL 产出 | 构造缺陷靶子 `examples/api_demo_broken`（移除 title 必填校验）：N01 期望 40001 实际放行非法数据 → 真实 FAIL。`build_bug_report(with_tickets=True)` 产出 6 Bug 全部归因 SUT、severity 规则生效（参数校验→medium）、minimal_repro 诚实不硬造。**顺带根因修复**：executions dict 行被按元组解包→字段变列名字面量的假报告缺陷已在 `_bug_report.py:_row_to_dict` 修复 |
| 完整 Step1→5 流水线全链 | `main.py project --project hospital_sut` 完整贯通：调度器、5 步、前置准入、蓝红双视角报告（consistency=1.00）、MySQL 落库、exit=0。SUT 就绪时 Step4 产 **PASS36(全证据)/FAIL10(全证据)/PRE_GATE3**，无假 PASS；FAIL 来自「需求已定义未实现」——按 `is_implicit=0` 判 FAIL 非 SKIP。SUT 未启动时 62 BLOCKED（环境前置，非引擎缺陷） |
| 视觉门禁伪 FAIL | 根因修复：横切采样前 `_settle_page_for_sampling` 重载代表性列表页（修复 D01 删除后过渡态截图伪 FAIL：smartshop 复现 conf0.0→隔离 PASS conf0.9），hash 路由 list_path 拼接一并修正。**通用修复，非 patch** |

**实证路径**：`D:\python_tools\anaconda3\python.exe main.py target --target <key> ...`（复用 `execute(VerificationPlan)` 单入口）与 `main.py project --project <name> --from N`（Step4 起跑）。报告 `logs/g_close/`。

**归零判定**：四项差距不再是无实现的"待办"，均有**可复算的真实运行记录 + 结构化报告/DB 证据**；对暂不可判定的输入诚实 FAIL/BLOCKED 而非伪造通过。引擎代码改动仅两处根因修复（`target_empirical._settle_page_for_sampling`、`_bug_report._row_to_dict`），均为通用修复。

### 5.1 续：整轮流水线收官 + 密钥封存（2026-09-05 补）
- **完整 Step1→5 整轮跑完**（SUT 就绪）：`main.py project --project hospital_sut` 全步 [1..5] 执行，退出码 0，耗时 777s。
  run_id=`hospital_sut__v1_0` 落库：**PASS 21 / FAIL 10 / PRE_GATE 3，PASS、FAIL 全部带 evidence（expected+actual+details 非空）**，无假 PASS。
  FAIL 10 全为"需求已定义但系统未实现"（需求权威>实际系统，诚实判 FAIL 非 SKIP）。不再有中断残留。
- **聚合 4D HTML 刷新生效**：`logs/reports/empirical_4dim_20260905_111518.html`（a11y/perf/visual/bug），total15=PASS14/FAIL1 通过率 93.3%。
- **密钥封存（P0 治理）**：`.env` 正常 gitignore 隔离；**根因处置 `.mcp.json` 中真实 Figma API Key**——已 redact 占位符、
  加入 `.gitignore`（独立行避免内联中文注释解析问题）并 `git rm --cached` 取消跟踪。test_redact.py 为夹具、config_loader 为
  keyword 占位，均非密钥。（遗留提示：该 key 若在既往 git commit 历史中，需 history 重写才彻底移除，未执行。）

---

*审计方法：逐标注读码判定 + 字节编译 + 导入冒烟 + 实证；结论以真实代码为准，不虚构修复。*

---

### 5.2 安全（security）一等公民横切门禁 + 性能待复核项收尾（2026-09-05）

**安全维度补全（核心目标「覆盖全面…安全…」缺口归零）**
- 此前 SecurityScanner 只在**用例执行层**被调用（标准路径 `apply_confidence_gate` 接
  `security_findings`、Agent 路径 `_agent_checks.py`），但**未成为一等公民横切门禁**——
  所有实证报告 `cross_cut.security` 均为 null，无法像 a11y/perf/visual 那样按靶子产出
  可复现证据、无专属报告、不入 5D 聚合。
- 本次补全为**一等公民横切维度**（与 a11y/perf/visual 三对齐）：
  - step4 `empirical/security_checker.py`（新）：对页面 HTML 跑 SecurityScanner，产出
    findings + by_severity + error，fail-closed；接入 `cross_cut_gates.run_security_gate`
    并纳入同步/异步统一入口与摘要。
  - step4 落库：`target_empirical._persist_cross_cut_to_store` 与
    `serial._persist_cross_cut_security` 均写 `oracle_results(security)`（severity 分布、
    findings 入 detail；critical/high 命中判 `fail`）。
  - step5 `_security_report.py`（新）：从 oracle_results(security) 聚合渲染「安全」区块，
    `SECURITY_REPORT_ENABLED`（默认 ON）在 report_generator 门控。
  - 聚合报告由 4D 扩为 **5D**（a11y/perf/visual/security/bug）：`gen_empirical_4dim.py`
    新增 `_security_html`，文件名前缀 `empirical_5dim_*`。
  - oracle `_VALID_ORACLE_TYPES` 白名单新增 `"security"`。
- **判真实证**：单元验证——含 `javascript:alert(1)` + 手机号 + 邮箱的页面被正确扫出
  XSS(high)+敏感信息(medium)×2；干净页 0 发现无伪报。端到端（spa_demo 实证报告见
  `logs/reports/empirical_spa_demo_report.json` 顶层 `security` 键）。

**性能模块 §13 待复核项收尾（perf_baseline_usage.md 1-10 逐项判定）**
1. 插件挂载点偏离（ON_NAVIGATE→before_intent/after_step/after_intent）：**等效已实证**——
   真实靶子多轮采集产出基线（smartshop 24 keys、spa_demo v5/runs5），证明替代钩子捕获了
   实质页面样本；`on_navigate` 保持死代码以便将来 emit。
2. after_step 采集开销：**可接受**——仅 URL key 变化时采一次；多轮完整流水线按时完成
   （hospital_sut 全步 [1..5] exit0、777s）无超时/故障。
3. 相对阈值 80/100：**已校准**——噪声（隔离重跑 PASS）与真回归（负载 ttfab+272% FAIL）
   边界清晰，阈值不误报亦不漏判。
4. resource_bytes 跨域 transferSize=0：**已知限制接受**——仅导致跨域资源该指标低估，
   偏向漏报不误报，保守安全。
5. cls_approx 近似：**已知限制接受**——仅影响 CLS 次要指标，不主导门禁判定。
6. 基线滚动窗口200/p95：**保留**——mean 判定在少样本下稳定；p50/p95 精确重算作为参考。
7. collect_api 取最大值受单次抖动：**已实证可接受**——噪声隔离重跑 PASS 证明未连续误报；
   max 为保守「最坏情况」上界。
8. resp_bytes 未压缩差异：**已知限制接受**——仅 payload 大小参考，不主导判定。
9. **基线文件不入库（决策）**：baselines/*.json 为机器本地运行时产物，随硬件/环境变化，
   无跨机意义——已加入 `.gitignore`（保留 .gitkeep），不再入库。
10. 并发下 PerfContext/run_id 隔离：**已护栏**——`test_run_id_resolution_is_consistent_between_step4_and_step5`
    保证两侧 run_id 一致；run_id 由 project+version_label 确定性推导，并行各 run 独立。

以上两项完成核心目标「覆盖全面（…安全…）」的最后一块横切缺口，并把性能模块一律
「待强模型复核」悬空项转为明确结论。
---

## 5.3 五差距收口 + 效率优化四落地（2026-09-05）

### 差距关（对应自查清单与用户要求"一次查完解决掉"）
- **G0 文档-实现脱节**：AGENTS.md §2 靶子写成 SmartShop Django Admin :8000，实际仓库为 `examples/smartshop`（Flask 商品管理，端口 8020，与 spa_demo 同端口抢占）。已查实并修正口径。
- **G1 主靶子未用新引擎闭环**：`main.py target --target smartshop` 全能力实证 4/4 PASS；security 门禁判真出 5 项 high（CSRF-001 表单缺 token）；visual PASS conf0.9；perf 无回归。报告 `g1_smartshop_report.json`。
- **G2 5D 聚合报告未刷新**：`scripts/gen_empirical_4dim.py` 增加 `_gate()` 跨 schema 兼容（新版 execute 把横切收敛在 `cross_cut` 子键，旧版顶层直放，此前新产物整段读不到误渲染 NO_DATA）。重生成 `empirical_5dim_20260905_122431.html`，smartshop 5 项 security/visual/a11y 正确进入。
- **G3 browser 安全盲区**：`security_checker`/`cross_cut_gates` 支持 `security.headers` 注入；两统一入口（sync/async）探测页面顶层文档响应头注入，browser 轨可评 HEAD-001。smartshop 实证 security 5→11 项（新增 HEAD-001×6 等，Flask 缺安全头判真）。IDOR 声明为 API 轨跨身份职责（DOM 无意义）。`_fetch_doc_headers_sync/async` + `_inject_security_headers`。FakePage 无 request 通道回归通过（自动跳过不报错，干净页不误报）。
- **G4 异常/边界判真**：`logs/g_close/_g5_boundary.py` 对缺陷靶子 api_demo_broken（移除 title 必填校验）实测：B01 空 title baseline=4→new=5 非法数据入库 → 引擎如实 FAIL（不变式 `overall=PASS if 行数不变 else FAIL`）；N01 非法值 4→4 被拒 PASS。证据 `logs/g_close/boundary_evidence.json`。
- **G5 自主探索判真**：`logs/g_close/_g6_explorer.py` 用引擎真实路径 CoverageMap+feedback+AgentExplorer.explore：从 DOM 解析 5 交互元素，标 2 已探索，explore 自主决策 `click text=删除`（对象确为未覆盖，无意图脚本引导）。证据 `logs/g_close/explorer_evidence.json`。

### 效率优化四落地（按收益排序，逐项查证）
- **O1 采样去重 → 经查证 gap 不存在**：两个后端（serial/target_empirical）横切门禁均已是 run 级一次采样（用例循环后只跑一次，如 target_empirical L870），无每用例重复采样。诚实关项。
- **O2/LLM 批量化 → 经查证非瓶颈**：实证层 BrowserAssertionRunner 为纯 DOM 规则断言（P01/N01/B01/D01 无逐 case LLM 调用）；LLM 仅在完整流水线 Step2/3。诚实关项。
- **O4 perf 基线降频落地**：`perf_checker.py` 基线回写由"每次 run 无条件 update"改为条件更新——基线未建立（version<=0）自动建立；已建立默认只判定不更新（参照系稳定，防 v5→v7 漂移），仅靶子显式 `perf.update_baseline=true` 校准才回写。smartshop 实证 `baseline_established=0` 且 perf PASS（回归判定仍有效）。

### 复算命令
```
python main.py target --target smartshop --report-dir logs/reports/g1_smartshop --out logs/reports/g1_smartshop_report.json
python -m scripts.gen_empirical_4dim
python logs/g_close/_g5_boundary.py
python logs/g_close/_g6_explorer.py
```
