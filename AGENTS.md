# Agents Memory — 核心目标与约束

## 1. 核心目标（最高优先级，不可偏离）

构建一个**通用、智能、媲美高级软件测试工程师**的自动化测试引擎。

- **通用**：跨系统/行业/页面，规则驱动，不依赖特定业务知识。任何修复必须考虑通用性，不能只为当前靶子系统打补丁。
- **智能**：AI理解需求、自动生成用例、语义验证、自主探索执行。不是脚本回放，是真正的智能测试。
- **媲美高级软件测试工程师**：覆盖全面（正向+异常+边界+安全+性能+可访问性），根因分析，详细Bug报告（含复现步骤、严重程度分类）。
- **人机协同端到端**：需求分析→用例生成→执行→报告，AI 主导全链路；Step2/Step3 后设人工审核闸门，Step5 生成人工补测建议清单。与 `src/架构点.md`（测试用例驱动混合执行引擎）保持一致。
- **不打补丁，根因修复**：遇到问题追溯根因，做通用性修复。不能针对特定用例打补丁——补丁是打不完的。

## 2. 靶子系统

- 主靶子实际为 **`examples/smartshop/`（Flask 商品管理，端口 8020，自动拉起）**
  —— 引擎 `main.py target --target smartshop` 实测 4/4 PASS + 全横切门禁（详见 §4/d9s）。
  历史文档"Django Admin :8000"为早期残留，仓库内无 Django 版 SmartShop，以此为准。
- 服务必须可见启动，不能后台静默运行

## 3. 交付标准

- 每次交付前自查：目标是否真正达成？还有哪些差距？
- 不能说"完成了"但实际还有ERROR/FAIL没处理
- 报告必须包含根因分析（引擎缺陷 vs 靶子系统问题）
- 最终交付物：HTML报告 + 可工作的引擎代码

## 4. 当前状态 (2026-08-09，2026-08-10 证据链补齐)

- 通过率：112/113 (99.1%) — **有可复现佐证**：
  - `e2e-test-report-2026-08-10/verification_report.json` + `.html`（由
    `scripts/maintenance/generate_verification_report.py` 从 MySQL `executions`
    表直接查询生成，逐 case 结果，数据源可复算）
  - MySQL `ai_test_engine.executions` run_id=`SmartShop__v1_0`：113 个真实用例
    = 112 PASS + 1 SKIP + 0 FAIL，通过率 99.1%，平均置信度 0.8942
- 唯一非 PASS 用例：TC-REQ-001-I01（DB 状态为 **SKIP**，非 ERROR —— 模块
  '【主源 Confluence】' 无括号模型名，靶子系统导航失败导致跳过，非引擎缺陷）
- 已实施4项通用性修复（operation-type-aware验证、API/DB轨分类、SSR感知SKIP、配置类型转换）
- **通用性实测（2026-08-10 追加）：API 型 + SPA 型靶子系统已构建并跑通**
  - `api_demo/`（Flask REST, 端口 8010）：token 鉴权/CRUD/参数校验(400)/业务边界(404/403)/统一错误码/启动种子。
    引擎实测 **18/18 PASS（100%）** + 服务状态交叉验证 5/5，报告
    `e2e-test-report-2026-08-10/api_demo_report.{json,html}`
  - `spa_demo/`（纯静态 SPA, 端口 8020）：hash 客户端路由/前端渲染/表单校验/localStorage 状态/动态 DOM。
    引擎实测 **11/11 PASS（100%）** + localStorage 交叉验证 3/3，报告
    `e2e-test-report-2026-08-10/spa_demo_report.{json,html}`
  - **跨系统零改代码复用证据**：两个新靶子的流水线（`api_demo/e2e_pipeline.py`、
    `spa_demo/e2e_pipeline.py`）与 smartshop/projecthub 共用同一批引擎核心模块
    （`testing_methods` 12 方法 + `MethodRouter` + `TaxonomyFilter` + `extensions`
    三件套），引擎核心代码零改动，仅新增靶子配置与用例。引擎实测还发现并驱动修复了
    SPA 靶子的 2 个真实缺陷（#/books/new 路由被 #/books/:id 提前匹配、price/stock
    校验错误文案未渲染）。
  - **通用命令入口（root `main.py`，2026-09-04 真实实现）**：两条通用路径
      - `python main.py project --project <engine_project> [--from N]` → 完整 Step1→5
        AI 流水线（经由 src/pipeline/full_pipeline_runner.py 非交互驱动，自动放行 green lane、
        绕过外部 Figma MCP 阻塞，无需手动逐步点继续）。
      - `python main.py target --target <key> [--mode ...] [--report-dir ...] [--out ...]`
        → 跨系统实证入口，复用 `execute(VerificationPlan)`，任何注册到 target_cfgs 的靶子
        （api_demo / spa_demo / hospital_sut / smartshop）均可直接跑。（旧文档
        `main.py --project api_demo/spa_demo` 一词不带子命令的用法也兼容）
      - 早期受限记录：完整流水线经 Figma MCP 拉数据源会卡在外部服务启动，属外部环境条件，
        非引擎缺陷；现驱动脚本默认 `skip_sources=['figma']` 绕过。常规流水线仍可用各靶子自带
        e2e_pipeline.py。
- 已知差距（2026-08-10 追加；**2026-09-05 全部归零实证**，详见 `audits/full_repo_audit_2026-09-04.md §5`、证据 `logs/g_close/`）：
  - 视觉审核 visual_review：已实测判真——VLM 走 `.env` 注入，hospital_sut/spa_demo/smartshop
    视觉门禁均输出真实 valid 判定（hospital PASS conf0.9、smartshop PASS conf0.9），不再是 NO_DATA。
    并根因修复横切采样前未重载列表页导致的过渡态伪 FAIL（`target_empirical._settle_page_for_sampling`）
  - AI 生成日志 ai_generation_logs：T2 已根因修复——Step3 每次 AI 生成尝试现落一行
    （source=step3_ai_generation），修复前历史仅 2 行（hallucination_logger 路径）
  - 可访问性 a11y：已接入流水线横切门禁（`cross_cut_gates.run_a11y_gate`），并实证发现真实缺陷
    （hospital_sut 2 high / smartshop 1 medium）
  - 安全 security（2026-09-05 补全为一等公民横切维度，与 a11y/perf/visual 对齐）：接入横切门禁
    （`cross_cut_gates.run_security_gate`）+ step4 落库 `oracle_results(security)` + step5 安全区块 + 聚合报告 4D→5D。
    **判真/判伪实证**：spa_demo 同一门禁产 security=0（干净页真负无伪报）；`logs/g_close/_g4_security.py` 对含漏洞页命中
    7 项（critical×5/high×2，覆盖 CMDI/CSRF/DATA/PATH/XSS 五类规则）判真 PASS、干净页 0 命中判伪 PASS，
    证据 `logs/g_close/security_evidence.json`
  - Bug 报告规范：已用缺陷靶子 `examples/api_demo_broken` 产真实 FAIL 验证——severity 规则生效、
    根因三分类归因 SUT、minimal_repro 诚实不硬造；顺带根因修复 executions dict 行按元组解包导致的假报告
    （`_bug_report._row_to_dict`）
  - 性能测试：采集→门禁→相对基线回归三层成体系并实证——能真判回归（spa_demo 负载下 FAIL）且能区分
    环境噪声（隔离重跑 PASS），报告 `logs/g_close/reports/*_gate.json`
  - 性能基线降频 O4（2026-09-05）：基线回写由"每次 run 无条件 update"改为条件更新——首轮自动建立；
    已建立默认只判定不更新（防 v5→v7 漂移）；仅 `perf.update_baseline=true`（校准模式）回写。`main.py target
    --perf-recalibrate` 经 `cfg_overrides` 部分覆盖注入（`plan.py` 修复 cfg_overrides 整体替换丢 target_key 的
    通用缺陷：缺 key 时改从注册表合并基础配置）。校准状态现透传 `perf.calibration`（status=updated /
    skipped_no_samples / skip_regular + before/after），校准脚本可据实报告而非猜测 NO-VERSION。
  - **留存物决策（2026-09-05 用户裁决）**：`examples/api_demo_broken/`（app.py+seed.py，端口 8015）**保留为永久负向夹具**
    ——常驻以复现真实 FAIL，验证引擎 Bug 报告 severity/级复现（4D 能力）的可复现证据链，不删除。驱动脚本 `logs/g_close/_g3_broken.py`
    在 `logs/`（全局已 gitignore）内，无需处置。

### §4.1 step1_verify 全量实证（2026-10-25 21:32→22:28，3405s）

- **分布**：本次切片 519 行 → PASS 356 / FAIL 55 / SKIP 107 / PRE_GATE 1；ERROR 0。
  run 内共 520 行 = 518 用例 + 2 条 PRE_GATE（`pre_gate_step3` 建于更早）。证据完整度
  `no_exp=0 no_act=0 no_det=0`（`expected` 运行期污染已归零）。报告 `e2e-test-report-2026-10-25/verification_report.{json,html}`（pass_rate 86.6%，avg_conf 0.7901）。
- **FAIL 55 归因（全部 SUT 侧，0 引擎缺陷）**：44 条同一根因 = `inline onsubmit=confirm` 被
  CSP `script-src 'self'` 拦截 → 确认框从未弹出（同一缺陷经 PF/UX/V 横切变体重复出现）；
  5 条 REST 端点 404（需求所述 `/api/products|categories` SUT 未实现）；2 条 `field_constraint`
  （REQ-029 名称无 maxlength；REQ-044 页面无"入库数量"字段）；其余 4 条（element_exists 0 命中 /
  text_contains 0 命中 / click_target_size 过小）。
- **SKIP 107**：98 条 `semantic_check:⊘(运行期 AI 已禁用)`（生成端未映射为机械断言），9 条其它非判定态。
  均诚实 SKIP（不记 PASS 防假绿 / 不记 FAIL 防假 Bug）。
- **本轮引擎根因修复（均已真实流水线实证）**
  1. `field_error_visible_check` 适用性判据：由「页面有 form」改为「存在**可见可编辑字段**」
     —— 列表页每行删除按钮也是 form（隐藏 token + submit），旧判据把字段级断言落到无字段校验面的
     页面 → 恒假 FAIL。修复后 7 条假 FAIL 归零；不适用时返回结构化
     `reason=ASSERTION_NOT_APPLICABLE` + `has_not_applicable_assertion` 关掉 PASS 门槛（记 SKIP）。
  2. `any_of_check`（析取式 OR 语义）执行器 + 生成端护栏：期望"显示错误提示**或**确认拦截"落
     `any_of_check`，任一分支成立即 PASS，全不成立才 FAIL。
  3. `any_of_check` **非判定态分支不参与聚合**：N/A 跳过的分支其 `passed=True` 只是诚实返回，
     若按裸 `passed` 聚合会把整条析取断言撑成假 PASS（本轮三态实证实际抓到并修复）。
  4. 生成端"须弹出确认框"→ `any_of_check[dialog_check, element_exists(CONFIRM_SELECTOR_CSV)]`
     （`expects_confirm_dialog` 为唯一判据），把确认类语义期望（存量 3 条）转为机械可判。
  5. `_handle_dialog` 引擎不可达分支补 `reason=EXECUTOR_EXCEPTION`（结构化非判定态，防被聚合为产品缺陷）。
- **三态实证**（临时夹具，跑完即删）：P01 任一分支成立→PASS（`alt1:field_error_visible_check=✓ '价格必须大于 0'`）；
  N01 全部分支不成立→FAIL/PRODUCT_BUG；S01 唯一分支 N/A→SKIP/ENGINE_COVERAGE_GAP。
- **待 Step3 重生成才生效**（代码已落地、门禁 27 passed，但存量 518 用例 payload 仍是旧 check）：
  上面第 2/4 项的生成侧映射。重生成后需再跑一次全量 Step4 实证。
- **本轮量化出的两个深水区（下一轮按数据驱动）**
  1. PASS 356 中 **144 条仅弱断言**（其中 122 条只断言 `element_exists + no_error`）—— 期望强度高于
     断言强度（例：REQ-025-I04 声明"SQL注入被正确防御"，实际只证明"页面未崩溃/未泄露"）。
     根因在生成端断言合成（按需求类型选断言族），修它必须 Step3 重生成 + 全量复跑。
  2. `SKIP 107` 中 `semantic_check` 片段可机械化统计：确认弹窗类 3 条、其余 95 条无机械映射
     （"删除商品并刷新列表"类需行数差分/"跳转404"类需 URL 断言，尚未建立生成端映射）。
     数字来自 `expects_confirm_dialog` / `expects_error_feedback` 直接投影，可复算。

### §4.2 生成端两处根因修复（2026-10-25 22:40→23:10，实测口径）

- **R1 非法值生成缺少「约束维度」**（`src/pipeline/step3/value_generator.py`）：
  `generate_invalid_value_from_type` 原本只能给「类型/格式不合规」值，调用方无法表达
  「要违反哪条约束」→ B01「超长」模板传 `("string","超长输入")` 得到 3 字符 `!@#`，
  断言"超长校验提示"却从未越界（边界用例空转）。现签名加 `constraint`
  (`type` 默认 / `max_length` / `required`) + `max_length`，超长值长度 = `max_length+1`
  （未给定取 `MAX_LENGTH_OVERFLOW+1`=501）。实测：修复后 `constraint=max_length` → len 501，
  `type` → `!@#`（原行为不变）。**本靶子 518 用例中 0 条 B01**（模板族 I0 181 / PF 128 /
  V0 79 / UX 65 / E0 64 / CP 1），故该修复对当前基线是**潜伏缺陷**修复，不改变既有判定。
- **R2 只读模板继承破坏性动作**（`src/pipeline/step3/_fallback_cases.py::build_cross_cut_cases`）：
  V01（布局/内容可见）此前只要需求有入口按钮就无条件追加 `click(入口)`，删除类需求因此
  在只读布局用例里点「删除」——同一 SUT 缺陷（内联 `onsubmit=confirm` 被 CSP 拦截）
  被重复计为多条 FAIL，且用例间数据互相污染。现入口动作只在 `form_scope`（断言需要进入
  表单）时发射，即"动作由该模板的**观察面**派生"。
  存量扫描：64 条 V01 中 8 条带 `click 删除,移除,清除`（REQ-007/008/037/038/039/041/051/061），
  这 8 条在本基线**全部 FAIL/PRODUCT_BUG**（占 55 条 FAIL 的 14.5%）——同一 SUT 缺陷被
  重复计数，非独立缺陷。
  **真实流水线实证**：临时夹具 `TC-TMPV-V01`（= REQ-061-V01 去掉 click，navigate+screenshot）
  经 `/api/step/4` 定向执行 → **PASS，conf 0.857**，
  `viewport_overflow_check:✓(无水平溢出) + element_exists:✓(8 个)`，无 JS 报错；
  而同源的存量 `TC-REQ-061-V01`（含 click 删除）在同一 run 内为 FAIL（CSP JS 报错）。
  夹具已删（test_cases 518 / executions 520 复位，分布仍 PASS 356 / FAIL 55 / SKIP 107 /
  PRE_GATE 2，证据完整度 `no_exp=0 no_act=0 no_det=0`）。
- **定向执行的两个操作要点**（本轮踩过，写脚本前必看）：
  ① `case_store._row_to_intent` 以 **`payload_json.case_id`** 为准，DB 列 `test_cases.case_id`
  不会被采用 → 手工插入夹具时必须同步改 payload 内 case_id，否则 `指定测试用例读取: 0 条`
  且日志报"以下测试用例未找到"（不是引擎缺陷）。
  ② 单用例定向重跑会丢失前序用例造出的数据（列表为空 → 删除按钮不存在），判定可能与全量
  run 不同；比对基线时以全量 run 快照为准。
  ③ 手工改 payload 前必须先 `json.loads(json.dumps(x))` 深拷贝备份：`backup[cid]=payload` 存的是
  引用，随后 `payload["actions"]=...` 会把备份一起改掉，"还原"写回的就是被污染值（本轮实际踩到，
  已按同族未改动用例的 action 结构逐字段还原）。
- **存量动作链与现行生成器不一致（32 条改判已实测，仍待 Step3 重生成收敛）**：全量扫描 518 条
  payload，带破坏性 `click(删除/移除/清除/导出/导入/禁用/注销)` 的条数 = I0 19 / PF 16 / V0 8 /
  UX 8 / E0 8（共 59）。用**真实需求数据**调用现行生成入口 `_build_rule_fallback_cases(REQ-061)`
  得到 `V01 / PF01 / UX02` 动作链均为 `navigate + screenshot`（PF02 为 `navigate +
  count_list_rows + screenshot`），**均无 click**。该结论曾一度被解读为"存量 payload 是旧版产物、
  无注入层"，**已被实测推翻**：Step3 `force_regen` 后这 32 条仍带 `click 删除`，故真根因在下游
  `_check_guard.complete_submit_flow → _trigger_actions.align_delete_flow_shape` —— 它对删除型
  需求的**所有**用例无条件补删除触发，覆盖了只读模板的观察面（生成端并未产出该 click）。
  修法遵循 §6 纪律：观察面在生成端一次性落结构化字段 `interaction_scope=observe_only`
  （V01/PF01/PF02/UX02/CP01 五模板），下游 `complete_submit_flow` 只读该字段即早退；
  修复后重生成实测 32/32 变为 `navigate + screenshot`（`interaction_scope=observe_only`、零破坏性 click）。
  于是对 CSP-FAIL 中族为 PF/V0/UX 的 32 条（REQ-007/008/037/038/039/041/051/061 ×
  PF01/PF02/UX02/V01）做了**双向实测**（`/api/step/4` 定向执行，两次各 ~220s）：
  - 换成现行生成器投影（只读链）→ **32/32 PASS**；
  - 换回存量链（`navigate + click 删除,移除,清除 + screenshot`）→ **32/32 FAIL / PRODUCT_BUG**，
    失败原因统一为点击触发的 CSP `console.error`。
  结论：这 32 条是「只读模板被塞破坏性动作」的**重复计数**（同一 SUT 缺陷，CSP 拦截内联
  `onsubmit=confirm`），不是 32 个独立缺陷；R2 修复方向被双向实证确认。payload 已还原为存量原值，
  判定已复位（PASS 356 / FAIL 55 / SKIP 107 / PRE_GATE 2，`no_exp=0 no_act=0 no_det=0`）。
  真正的收敛动作仍是 **Step3 重生成 + 全量 Step4 复跑**（R2 只在生成侧生效，存量 payload 不会自变）。
  剩余 12 条 CSP FAIL（I0 10 / E0 2）同样带 click，但 I0（主流程交互）/E0（异常触发）
  的 click 是该用例的合法观察动作，属真实 SUT 缺陷的**首次计数**，不属于重复。
  复核口径：44 条 CSP FAIL 逐条查 payload，含 click 的比例为 44/44（PF 16、UX 8、V 8、I 10、E 2）。

### §4.3 step1_verify 全量实证（2026-10-26 11:00:50→11:53:54，3184s）

- **分布**：499 行（497 用例 + 2 PRE_GATE）→ PASS 373 / FAIL 24 / SKIP 100 / ERROR 0；
  决策口径通过率 **94.0%**（373/397，SKIP 不计入），avg_conf 0.7999，证据完整度
  `no_exp=0 no_act=0 no_det=0`。报告 `e2e-test-report-2026-10-26/verification_report.{json,html}`。
  对照上一基线：356/55/107（86.6%）。
- **用例集变化**：518 → 497（Step3 `force_regen`；I 族 181→160，其余族不变）。
  32 条"只读模板被塞破坏性 click"目标用例实测变为 `navigate + screenshot`（R2 收敛）。
- **生成端门控**（`case_rules/base.py::_build_error_boundary_case`）：异常模板的期望文案是
  引擎自撰，若它要求错误极性而**需求原文未声明**该极性，则该用例断言的是不存在的需求
  → 不生成。实测拦下 **21** 条（全部 I02 族；其中 16 条上一轮为 PASS，属断言非需求期望的
  弱/假 PASS 面，5 条为 FAIL）。
- **极性 SSOT 修复**（`_rule_extractor_keywords`）：① `_NEGATED_DEFENSE_RE` 不再跨「的」匹配、
  不再把「不存在」单列为否定防御（否定作用在"存在"上，主谓仍是"返回错误提示"）；
  ② `_REJECT_KEYWORDS` 补否定动词「禁止」。修复前门控误删 23 条，修复后 21 条
  （REQ-039-I02「删除不存在的商品返回错误提示」、REQ-041-I02「禁止删除并提示…」找回）。
  双向守卫：5 条肯定要求判拒绝型、8 条防御措辞（无数据库错误泄露/不泄露数据库错误信息/
  未导致崩溃/无致命错误/不存在错误页）判接受型；`tests/test_core` 244 passed（+2 契约）。
- **FAIL 24 归因（0 条引擎判定缺陷）**
  - CSP 拦截内联 `onsubmit=confirm`（确认框从未弹出，同一 SUT 缺陷的首次计数）9 条：
    007-I03 / 037-E01 / 038-I03 / 039-I03 / 041-I03 / 061-I03（console.error 直接判）+
    037-I01 / 068-I01 / 068-E01（any_of 的 dialog 分支未捕获到弹窗）
  - REST 端点未实现（404）5 条：REQ-054~058-I01
  - 字段约束缺失 2 条：REQ-029-I01（名称控件无 maxlength）、REQ-044-I01（页面无"入库数量"字段）
  - 列表数据依赖 2 条：REQ-061-I01/E01 `element_exists:✗(0 个)` —— 执行期列表已被前序用例清空
    （引擎侧缺"用例级造数/隔离"，属引擎缺口，非 SUT）
  - any_of 全分支不成立 2 条：REQ-039-I02 / 041-I02（同 CSP 阻断链路，错误反馈面不可达）
  - 其余 3 条：REQ-063-I03 / 064-I03（any_of）、REQ-069-E01（手机号校验提示未渲染）、
    TC-PAGE-ROOT-UX01（`click_target_size_check:✗` 43×23 < 32px 阈值）
- **数据卫生**：`executions` 同 run_id 为 UPSERT，被删用例会留下孤儿行 → 报告分母虚高
  （曾 total=520）。已按「case_id 不在当前 version 的 test_cases」清理 21 行，报告分母 = 499。
- **静态断言强度（497 条）**：仅弱断言 155（I 75 / PF 59 / V 15 / E 6）、无断言 4、
  `semantic_check` 98（运行期 AI 禁用 → 诚实 SKIP）。

### §4.4 step1_verify 全量实证（2026-10-26 12:17:29→13:11:00，3204s）+ 期望强度门控移位

- **分布**：489 行（488 用例 + 1 PRE_GATE）→ PASS 367 / FAIL 24 / SKIP 97 / ERROR 0；
  决策口径通过率 **93.9%**（367/391），avg_conf 0.8004，证据完整度
  `no_exp=0 no_act=0 no_det=0`。报告 `e2e-test-report-2026-10-26/verification_report.{json,html}`
  （`total=490` 含 2 条 PRE_GATE 行，PRE_GATE 不进通过率分母）。对照上一基线 94.0%（373/397）。
- **用例集 497→488 的来由（A 项根因修复）**：把"期望强度 ≤ 需求声明"的判据从
  单个生成器迁到**落库前唯一咽喉** `_check_guard.overstated_expectation_reason`，
  并删除生成端那份重复判据（`case_rules/base.py` 内 N01 分支）——同一语义不再有
  第二个判定入口，AI 生成/其它模板同受约束。引擎日志实证
  `期望强度超需求未落库 30 条`。
  判据（全复用 SSOT，不另立词表）：作用面限定结构化 `scenario_type==error_path`
  （边界模板允许"正确处理"分支成立，不构成超强主张）；用例期望经
  `expects_error_feedback` 判错极性；需求侧 `expects_error_feedback` ∨
  `expects_noop_invariant` ∨ `_REQUIRED_KEYS`/字段规格（需求声明字段约束即保留）∨
  需求语料不可得时 **fail-open**（无判据不删用例）。
- **作用面干跑（存量 497 条、不写库）**：仅删 10 条 V02「空提交后字段级错误提示可见」
  ——上一基线中 8 PASS / 2 SKIP，属"断言非需求派生"的弱通过；需求声明必填/约束的
  REQ-029/032/044/047 等 V02 全部保留（`_REQUIRED_KEYS` 命中）。
- **FAIL 24 全为真实判定（0 假 Bug、0 引擎判定缺陷）**
  - CSP 拦截内联 `onsubmit=confirm` **13 条**：console.error 直接判 6 条
    （007-I03/037-E01/038-I03/039-I03/041-I03/061-I03）+ any_of 的 dialog 分支全不成立
    7 条（017-I03/037-I01/039-I02/041-I02/064-I03/068-I01/068-E01）
  - REST 端点未实现 404 **5 条**：REQ-054~058-I01
  - 字段约束缺失 **2 条**：029-I01（名称控件无 maxlength）/ 044-I01（页面无"入库数量"字段）
  - 点击目标过小 **1 条**：TC-PAGE-ROOT-UX01（删除按钮 43×23 < 32px 阈值；
    同用例 semantic_check 为 ⊘ 非判定态，但 click_target_size ✗ 是真实负向判定 → FAIL 正确）
  - 手机号校验提示未渲染 **1 条**：069-E01
  - **引擎缺口 2 条**：061-I01/E01 `element_exists:✗(0 个)` —— 执行期列表已被前序破坏性
    用例清空，引擎缺"用例级造数/隔离"（归引擎，非 SUT；见 §4.5-B）
- **SKIP 97** = 92 条 `semantic_check`（运行期 AI 禁用 → 机械不可判，诚实 SKIP）
  + 5 条断言全为非判定态（004/039/041 的 I01/E01）。
- **假 PASS 面**：仅弱断言 155 条仍在（I 75 / PF 59 / V 15 / E 6，与上轮持平）——
  C 项未做（见 §4.5-C），不宣称已收敛。

### §4.5 待办（B/C，按 §6 纪律：上游一次性判定落结构化字段，下游只读不推）

- **B 用例级前置数据保障**（消 061-I01/E01 的 2 条引擎缺口）：结构化字段
  `data_precondition="existing_row"` 只在**注入删除触发的唯一位置**
  `_check_guard.complete_submit_flow`（`_del_fixed` 分支）落一次；执行端只读该字段，
  数据不足时先用 SUT 自身"新建"入口造行（`form_completion` 填字段 + 提交），
  造不出才诚实记 SKIP（ENGINE_COVERAGE_GAP），**不得记 FAIL**（防假 Bug），
  也不得记 PASS（防假绿）。
- **C 弱断言升级**（155 条）：根因在生成端"按需求类型选断言族"缺失——模板通用期望
  （"系统不崩溃"/"列表有数据行渲染"）只能落 `element_exists` + `no_error`。正解是
  期望由需求声明的可观察事实派生（`resolve_expected_feedback` 单一事实源 →
  变更为行数差分、拒绝为错误反馈/不变量、访问控制为 URL/403 断言），
  改完必须 Step3 重生成 + 全量 Step4 复跑才算收敛。

### §4.6 B/C 落地 + 全量实证（2026-10-26 15:13→16:10，3444s）

- **分布**：489 行（488 用例 + 1 PRE_GATE）→ PASS 367 / FAIL 24 / SKIP 97 / ERROR 0；
  决策口径通过率 **93.9%**（367/391），avg_conf 0.8004，证据完整度
  `no_exp=0 no_act=0 no_det=0`。报告 `e2e-test-report-2026-10-26/verification_report.{json,html}`。
  用例集未变（488 条，Step3 payload 未动；本轮只改执行端与生成端 C 代码）。
- **B 项（用例级前置数据保障）已闭环**
  - 结构化字段 `data_precondition="existing_row"` 唯一产生点 =
    `_check_guard.complete_submit_flow`（`trigger_confirm` 分支），存量落 16 条；
    执行端 `action_hooks._ensure_case_data_precondition` 只读该字段，
    在首个元素交互动作**之前**调 `steps/step4/data_precondition.ensure_existing_row` 造行。
  - **造数能力三态实证**（零业务夹具 `temp/_b2_fixture.py`，证据 `temp/_b2_fixture.json`）：
    P01 空列表 → 经"新建"入口 + 表单补全 + 提交，行数 0→1，`gap=""`；
    P02 已有 1 行 → 不造数直接返回 `gap=""`；
    N01 无可用入口 → 返回缺口串（不抛异常）。
    缺口经 `_base` 记 **SKIP(ENGINE_COVERAGE_GAP)**，不记 FAIL（防假 Bug）/不记 PASS（防假绿）。
  - 主靶子 smartshop 上该分支**结构性不可达**（`app.py` COUNT==0 自种子 5~6 行，列表永不为空），
    故用夹具取证，非"未实现"。
  - 16 条声明 `existing_row` 的用例本轮结果为 8 SKIP / 8 FAIL，全部落在同一 SUT 缺陷链
    （内联 `onsubmit=confirm` 被 CSP 拦截 / REST 404），前置数据本身已由声明保障。
- **B-1 归因口径已纠正**：REQ-061-I01/E01 由上基线"列表被前序用例清空 → 引擎缺口"改判为
  **真 SUT 缺陷**——本轮证据为 `element_exists:✗(0 个)` +
  `list_row_count_check:✗(列表行数 5→5, Δ=0)`，即删除动作未生效（CSP 阻断确认框），
  不是断言对象不存在。
- **C 项（生成端断言族）已落地并实证生效**：落库前咽喉新增
  `_check_guard.align_requirement_observable_assertions`，仅在现有断言**全为弱断言**
  （`element_exists`/`no_error`/`no_fatal_error`）时按需求可观察事实派生四族：
  ① 访问限制 → `access_control_check`（受限动词候选为空则**不发射**，空判据是假 PASS 温床）；
  ② 拒绝型变更 → `field_error_visible_check`；③ 变更型接受后果 → `list_row_count_check(Δ)`；
  ④ 指向列表行的裸存在性 → `list_row_count_check(gte 1)`。
  判据全部复用既有 SSOT（极性 / 访问限制 / 变更语义 / 列表行选择器），无新词表。
  - **执行实证**：`list_row_count_check` 66 条中 64 条判 ✓（PASS）、2 条判 ✗（= 061-I01/E01 真缺陷）；
    `field_error_visible_check` 判 ✓ 21 条；无新增该类假 FAIL。
  - **静态弱断言 155 → 94**（I 74 / V 15 / E 5），与派生族预测一致（-61 条升级为行数规模断言）。
- **C 项引入的回归已根因修复**：升级把 PF02 的"列表规模"从裸 `element_exists` 换成
  `list_row_count_check(expected_count)` 后，`case_self_consistency` 仍只按 check **类型**
  判"需先有操作"，把静态规模断言当成操作后果 → 64 条 PF02 被记 SKIP（PASS 367→304）。
  修复 = 该契约读取上游已声明的 `interaction_scope=observe_only`（生成端五模板一次性落字段），
  观察型用例不需要前置操作；`_base` 传参。契约测试
  `tests/test_core/test_assertion_family_derivation.py` 5 passed（含"无声明时仍判不可达"双向守卫）。
  修复后本轮 SKIP 97 全为 `ENGINE_COVERAGE_GAP`，**零 PF02**，PASS 回到 367。
- **FAIL 24 归因（0 引擎判定缺陷，全部 PRODUCT_BUG）**
  - CSP 拦截内联 `onsubmit=confirm` **13 条**：`console.error` 直判 6 条
    （007-I03 / 037-E01 / 038-I03 / 039-I03 / 041-I03 / 061-I03）+ any_of 分支全不成立 7 条
    （037-I01 / 039-I02 / 040-I03 / 041-I02 / 064-I03 / 068-I01 / 068-E01）
  - REST 端点未实现 404 **5 条**：REQ-054~058-I01
  - 字段约束缺失 **2 条**：029-I01（名称控件无 maxlength）/ 044-I01（无"入库数量"字段）
  - 删除未生效 **2 条**：061-I01/E01（`element_exists:✗` + 行数 Δ=0，同一 CSP 链）
  - 手机号校验提示未渲染 **1 条**：069-E01
  - 点击目标过小 **1 条**：TC-PAGE-ROOT-UX01（43×23 < 32px）
- **未收敛面（诚实记录，不宣称已清零）**：弱断言 94 条 =
  安全探针（SQLi/XSS/CSRF，I04/I05/I06）43 + 析取式容错期望（I02/I03）26
  + V01 布局观察面 15 + E01 探针 5 + 无断言探索用例 4。
  其中安全探针的期望（"系统不崩溃，注入被正确防御"）当前只由 `element_exists + no_error`
  证明，需专门的安全断言族（XSS 反射形态 / 错误泄露 / 状态不变量）才能收敛。
  另：访问控制族对 REQ-005（"不可新增、编辑、删除"）不发射——
  `_ACCESS_RESTRICTION_KEYWORDS` 无「不可」形态；**未补词表**，因为该需求下的存量用例是
  安全探针（SQLi/XSS/CSRF），套用访问控制断言会让用例改判它所不观察的对象。

### §4.7 Step5 报告实证（2026-10-26 16:20→16:24）

- **首跑暴露报告统计串味（引擎缺陷，已根因修复）**：`report_generator` 内追溯校验（防幻觉）
  段落复用了 `passed` / `failed` 两个变量名，把执行统计**就地覆盖**成"追溯校验通过数"：
  本次 488 条全部 valid → `run_summary.total_passed=488 / total_failed=0`、控制台
  "通过: 488 | 失败: 0"，而 `pass_rate` 仍是 367/488（两值互斥）。
  报告自校验 `[S45-014] verified=False`（`passed: 报告=488 vs DB=367`、`failed: 0 vs 24`）抓到了该不一致。
  根因 = 同一函数内两种语义共用变量名（数据源正确，映射正确），修复 = 改名为 `_trace_ok` / `_trace_bad`。
- **复跑实证**：`[S45-014] 报告自校验: verified=True, db_counts={PASS:367, FAIL:24, SKIP:97, PRE_GATE:3}`；
  `run_summary` = 367 / 24 / 97，控制台"通过: 367 | 失败: 24"；报告写入
  `generated_reports(report_type=report, size=293020)`。
- **交付物导出**：`e2e-test-report-2026-10-26/step5_report.md` + `step5_report.html`
  （HTML 由引擎原生 `_generate_html_report` 渲染，未另写模板）。
- **报告章节实测在位**：执行概览 / 目录分布 / 488 条明细 / 失败 24 条逐条根因（归属 SUT）/
  人工补测建议 121 条（P0 62 · P1 14 · P2 45）/ 需求覆盖矩阵（70 条需求）/
  追溯校验（488 全 valid）/ 5D 横切（a11y 0 违规、security 1 中危、visual 28 条 **NO_DATA**——
  本工程未配置 `visual.expectation`，门禁诚实跳过，非判真失败）/ 假 PASS 审计 GREEN（0 条无证据假 PASS）/
  引擎自评估 accuracy 93.86%（false_pass 0 · false_fail 24）/ 蓝红对抗 / Bug 清单 24 条。
- **通过率双口径（勿混用）**：`run_summary.pass_rate=75.2%` = PASS/全部用例（**SKIP 计入分母**）；
  决策口径 **93.9%** = 367/391（SKIP 不计入），后者记在 `verification_report` 与引擎自评估。

### §4.8 src/ 去靶子化 + 看门狗误杀根因（2026-10-26 18:00→21:49，真实流水线实证完成）

**A. 删除的「靶子适配」补丁（全部落在 src/ 引擎树，净减 ≈1300 行）**
1. `src/pipeline/step1/requirement_sut_matcher.py` 整模块删除（196 行）：`_REQ_TO_SUT_HINTS`
   是「需求词 → 靶子路由/实体」硬编码词表（含 Django 遗留与 Flask SmartShop 实体），
   属给当前靶子量身定做的匹配表；调用点 `step1.py` Phase 3.8（63 行）与
   `pipeline_progress.py` 进度条目（7 行）一并删除。
2. `authz_matrix.py` 删除 `CODE_OK/40101/40301/40401` 与白名单里的业务码：判定输入是
   `resp.status_code`（只可能是 3 位），业务码**永远不可能命中**，是「与 api_demo 对齐」的死配置。
   现只剩 HTTP 语义 `_HTTP_REJECT_CODES={401,403,404}` / `_HTTP_UNAUTH_CODES={401}`；
   需要精确业务码的靶子用探针 `expected_code` 声明（既有机制，不新增配置键）。
3. `sut_harness.py` 删除引擎内置默认凭据与默认登录端点（`admin/admin123`、`user/user123`、
   `/api/v1/auth/login`），改为**只读靶子 `auth` 声明**；未声明 → ERROR 日志 + 返回空 cookie/None
   （诚实 blocked），不替靶子猜账号。browser 登录同时改为读 `auth.login_url` 与
   `login_fields.username_field/password_field`（原固定 POST `/login` + 固定键名）。
4. `nav_seed`：hospital 专用种子移出引擎树（`target_systems/hospital_sut/nav_seed.py`），
   `nav_seed/__init__.py` 改为按靶子声明 `{file,fn}` 动态加载；并修 `get_target_cfg` 白名单漏
   `nav_seed` 的死链（声明了却从不进 merged cfg）。
5. 判定为**合法声明式配置、不动**：`target_cfgs.py`（url/port/selectors/authz/probes/nav_seed 属靶子自身数据）。
   靶子名在引擎里只出现在注释/示例（`grep '== "(smartshop|...)"'` 无匹配）。
6. **Django 发现引擎与适配器层整块删除（≈1016 行，净减）**：`step3/project_adapters/`
   （`base/django/generic/__init__` 4 文件 565 行）是「早期 Django 版 SmartShop」的产物，
   全仓 `manage.py` 为 0（`examples/`、`target_systems/` 均无），`DjangoAdapter.is_supported()`
   恒 False；`_rule_extractor.py` 内还重复实现了同一套发现逻辑（`_SETTINGS_RE`/`_SYSPATH_PARENT_RE`/
   `_discover_django_projects`/`_scan_apps_in_dir`/`_parse_verbose_name_from_models_py` 等 456 行，
   与适配器层同语义两处定义）。两者唯一调用链
   `extract_cases_by_rules → _load_entity_tables_for_context → _enrich_entity_tables_with_verbose_names
   → get_adapter(Generic).enrich_entity_tables` 对非 Django 项目是**纯恒等返回**；
   今日 Step3 日志实证 `entity_tables 最终: 0 张表`（Flask SmartShop）。现只保留
   `_load_entity_tables_for_context` 读 `SystemDataResolver` 的原始结果。
7. `api_only_executor.py` 删除 `_cfg.target_password or "admin123"` 默认凭据兜底
   （与 §A3 同语义，配置层 `target_password` 缺失即跳过登录，不替靶子猜账号）。
   遗留同类赘余（未改，仅记录）：`browser_ai_login.py` / `browser_intent_parser/_steps.py`(×2) /
   `single_mixins/_base.py` 各有一处 `config.target_username or "admin"`，与
   `config_loader/target.py::target_username` 的 `TARGET_DEFAULT_USERNAME` 默认值重复；
   因其中一处承担「per-case 身份切换置空后不回落」语义，改动需连带上游契约，未在本轮动。
   第二轮已按同一语义（配置层唯一默认源）把 4 处 `or "admin"` 全部删除，并删掉
   `config_loader/target.py` 里已零引用的 `target_default_username/password` 属性
   （`TARGET_DEFAULT_USERNAME` 在模块级 `target_username` 内仍是唯一默认值）。
8. **case_meta 孤儿行根因修复**（`step3/_modeling_intents.py`）：`all_metrics` 在生成阶段按
   **候选全量**收集，而落库前的 `_check_guard.sanitize_intents(flat_intents)` 会把不合规候选
   （如期望强度超出需求声明）**原地剔除**；原实现照单写 meta，于是留下「test_cases 里不存在
   的 case_id」孤儿行（实测 518 vs 488 = 30 条 I02/V02 孤儿，覆盖率/审核口径分母虚高）。
   修复 = 写 meta 前用 `sanitize_intents` 之后的保留集合过滤 `all_metrics`（同一咽喉点、
   第二次推导取消）。第二轮清空态实证：Step3 后 `test_cases 488 / case_meta 488`。

**B. 看门狗误杀的真正根因（不是「30 分钟总时长」）**
- `_run_progress_marker` 用 `row[0]` 读 `SELECT MAX(t) ...`，而 `db_manager` 返回 **dict 行**
  → 每次 `KeyError` 被 `except` 吞成 `None`。两个看门狗（`_slot_watchdog` 执行槽、
  `state._sweep_stale_runs`）都把 `None` 当「无进展」并退回 `start_ts` 判停滞
  → Step4 在 ~190s 被强杀（Step1 则是 ~1810s 同因）。
- 修复：① dict/tuple 行兼容取值；② `None` 语义改为「判据不可读 = 不判挂起（fail-open）」，
  只有「能读到时间戳但不再前进」才判停滞。
- `_gen_run_id_and_clear_old_logs` 原按 `run_id` 清**全部**日志：整条流水线共享同一 run_id，
  于是触发 Step5 会抹掉 Step1~4 的 `RUN_START/RUN_END`（本轮实测证据链被抹掉）。
  改为 `WHERE run_id=%s AND step_num=%s`，只清将被重跑的那一步。
- `scheduler_executor` 停止文案不再把看门狗判挂起谎报为「用户手动停止」。

**C. 实证（Step1→5 全链路，清空派生数据后从干净态跑）**
- `tests/test_core` **258 passed**（A1~A8 后复跑通过）。
- **C-b 清空态全链路（最终基线，2026-10-26 20:40→21:49）**：本轮唯一权威数字。
  先 `purge_project('step1_verify')` 清空 **49018 行 / 44 张表、0 失败**（含 `run_logs` 28674、
  `screenshots` 1072；输入态与注册表保留），然后重启引擎加载全部新代码，连续驱动 Step1→5
  （无人值守、无人工介入）：
  Step1 20:40:46→20:40:47 / Step2 20:40:56→20:40:58（requirements 70）
  / Step3 20:41:06→20:41:36（**test_cases 488 + case_meta 488**，A8 孤儿修复生效）
  / Step4 20:42:41→21:49:01（3982s）/ Step5 21:49:02→21:49:17，全部 `RUN_END status=completed`。
  `run_logs` 五步齐全（step 分布 `{0:30, 1:62, 2:87, 3:2805, 4:25846, 5:47}`，共 28877 行）——
  「触发下一步抹掉前序 RUN_START/RUN_END」缺陷已修。
- **Step4 全量**：total=490（488 用例 + 2 PRE_GATE）→ **PASS 369 / FAIL 22 / SKIP 97 /
  PRE_GATE 2**，决策口径通过率 **94.4%（369/391）**，证据完整度
  `no_exp=0 no_act=0 no_det=0 no_conf=0`。用例集 488 条与删除 `project_adapters` 前完全一致，
  Step3 日志 `entity_tables: 0 张表`、无 Traceback → **A6 删除在重生成下端到端等价**。
  对照上一轮基线 A（367/24/97）：040-I03、064-I03 转 PASS。
  FAIL 22 逐条归因（0 引擎判定缺陷，全 PRODUCT_BUG）：CSP 拦截内联 `onsubmit=confirm`
  console.error 6（007-I03/037-E01/038-I03/039-I03/041-I03/061-I03）+ REST 404 5（054~058-I01）
  + any_of dialog 分支全不成立 3（037-I01/068-E01/068-I01）+ any_of 全分支非判定态 2
  （039-I02/041-I02：alt1 无可编辑字段面 ⊘、alt2 弹窗未弹出）
  + 字段约束 2（029-I01 名称无 maxlength / 044-I01 无「入库数量」字段）
  + 删除未生效 2（061-I01/E01，`list_row_count_check:✗ Δ=0`，同一 CSP 链）
  + 069-E01 text_contains 0 命中 + TC-PAGE-ROOT-UX01 click_target_size 43×23<32px。
  静态断言强度：弱断言 94 / 无断言 4（与基线 A 持平，C 项未动）。
- **Step5**：`[S45-014] verified=True, db_counts={PASS:369, FAIL:22, SKIP:97, PRE_GATE:3}`；
  `[S45-015]` 原文追溯 **22/22 命中**；`run_summary` 369/22/97，报告 size=153419，
  duration 4013s，写入 `generated_reports`。
- **perf 门禁**：4 样本 **3 PASS / 1 FAIL，`blocking=true`**；失败 key `web::/products/{id}/edit`
  （ttfb 31.9ms vs 基线 14.57ms / 相对 +118.94%，dcl +87.2%、load +84.39%）。绝对值增量仅
  ~18ms，属首访噪声候选；**门禁缺「相对阈值之外的绝对噪声地板」**是本轮发现的设计缺口
  （见 D 段，未打补丁）。
- **A7 复核状态**：4 处 `or "admin"` 与 `target_default_*` 属性已删除，但 SmartShop
  `auth.type=none` → 登录分支不可达，本轮实证**不覆盖**该改动；需登录型靶子
  （api_demo/hospital_sut）下一轮核验，届时才算闭环。

**D. 未清项（诚实保留，不宣称已清零）**
- `src/extensions/traceability/heuristic.py` 中英领域词表（通用领域词，非靶子适配）。
- 事件叙事注释存量（改写不减行数，未 apply）。
- 已清项见 §4.9（perf 绝对噪声地板、`_identity_assign` 需求级执行身份判据、
  step5 链路无条件回写基线导致的参照系漂移）。

### §4.9 清空态全链路复跑（2026-10-26 23:22→2026-10-27 00:26，3687s）+ 三项根因修复

**A. 权威数字（清空派生数据后无人值守跑 Step1→5）**
- 先 `purge_project('step1_verify')` 清 **48992 行 / 43 张表、0 错误**（输入态与注册表保留），
  再重启引擎加载新代码，连续驱动 Step1→5（无人工介入）：
  Step1 23:22:17→23:22:18 / Step2 23:22:27→23:22:29（requirements 70，review_lane 70）
  / Step3 23:22:37→23:23:09（**test_cases 488 + case_meta 488**，孤儿修复仍生效）
  / Step4 23:24:01→00:25:22（3687s）/ Step5 00:25:28→00:25:42；五步 `RUN_END status=completed`。
- **Step4 分布**：491 行（488 用例 + 3 条 PRE_GATE 系统行）→ **PASS 369 / SKIP 97 / FAIL 22 /
  PRE_GATE 3**；决策口径通过率 **94.4%（369/391，SKIP 不计入）**；证据完整度
  `no_exp=0 no_act=0 no_det=0 no_conf=0`。
- **断言强度（静态扫描 488 条）**：仅弱断言 **106**（较 §4.8 C 的 94 **+12**）、无断言 4。
  增加来源明确：12 条 boundary 用例的极性反转被抑制后落回 `element_exists + no_error`
  （见 B 段），`any_of_check` 由 19 → **7**（-12）。此为**诚实记录**，不宣称断言强度改善。
- **FAIL 22 逐条归因（0 条引擎判定缺陷，全 PRODUCT_BUG）**：CSP 拦截内联
  `onsubmit=confirm` console.error 6（007-I03/037-E01/038-I03/039-I03/041-I03/061-I03）
  + REST 404 5（054~058-I01）+ any_of dialog 分支全不成立 3（037-I01/068-E01/068-I01）
  + any_of 全分支非判定态 2（039-I02/041-I02）+ 字段约束 2（029-I01 名称无 maxlength /
  044-I01 无「入库数量」字段）+ 删除未生效 2（061-I01/E01，`list_row_count_check:✗ Δ=0`，
  同一 CSP 链）+ 069-E01 text_contains 0 命中 + TC-PAGE-ROOT-UX01 click_target_size 43×23<32px。
- **Step5**：`[S45-014] 报告自校验 verified=True, db_counts={PASS:369, FAIL:22, SKIP:97, PRE_GATE:3}`；
  `[S45-015] 失败用例原文追溯 22/22 命中`；报告写入 `generated_reports(report_type=report, size=152935)`。

**B. 假 Bug 消除：极性反转的唯一前提（`_check_guard.requirement_declares_negative_obligation`）**
- 现象：TC-REQ-017-I03 在上一轮被记 FAIL——边界模板自撰期望「显示校验提示或正确处理」，
  下游把它当需求义务 → 极性反转 → 语义对齐展开为 OR[错误反馈, 弹窗] → 丢掉「正确处理」
  分支 → SUT 正常受理反被判 FAIL（假 Bug）。
- 根因：极性反转缺一个**上游事实前提**（需求原文是否声明拒绝/字段约束义务），于是模板文案
  自己给自己发义务。修复 = 在落库前咽喉新增唯一判据
  `requirement_declares_negative_obligation(case)`（三态：True 声明 / False 明确未声明 /
  None 语料不可得 → fail-open），反转条件加 `_req_negative is not False`，抑制时打显式日志。
- 实证：清空态重生成后翻转用例 **27 → 15**（12 条 boundary 抑制生效）；017-I03 新 payload 只剩
  `element_exists + no_error(mode=defense)`，执行转 **PASS**；`overstated_expectation_reason`
  复用同一判据（不再有第二处推导）。契约 `test_polarity_judgment_source_contract.py` +3 条双向守卫。

**C. perf 门禁绝对噪声地板（判据合并为单一入口，非叠加常量）**
- 地板与相对阈值合并为**一条判据**（`gate.evaluate_gate`），值落结构化配置
  `perf_rules.yaml → relative.noise_floor`（`config.noise_floor_of` 读取），代码不含常量。
- **双向实证**（`temp/_perf_floor_probe.py`，smartshop 规则）：基线 14.57ms → 31.9ms
  （+118.94%，绝对 +23.79ms < 50ms 地板）→ **PASS**；同规则 100ms → 400ms
  （绝对 +70ms ≥ 地板）→ **FAIL `relative`**。干净页与真回归两侧都判对。
- 真实 Step5 链路复核：本基线 `web::/products/{id}/edit` 31.9ms vs 基线 15.82ms → `gate_summary`
  `total=4 pass=4 fail=0 blocking=False`（地板在流水线内同样生效）。

**D. 基线回写策略收敛为唯一入口 `baseline.write_after_gate`（消第二判定入口）**
- 病灶：`perf_checker.py`（靶子链路）已按 O4 降频，而 step5 适配层
  `_perf_gate_report.py` 仍**无条件** `manager.update(...)` → 同一条策略两处判定，参照系每次
  run 漂移（实测 `[perf_gate] 基线已更新 target=step1_verify version=8`，与 §4 已声明的 O4 自相矛盾）。
- 修复：策略（首轮自动建立 / 常规 `skip_regular` 只判定 / `update_baseline=true` 校准才回写 /
  无有效样本 `skipped_no_samples`）在 `baseline.write_after_gate` **单点**实现；两条链路只调用。
  step5 报告新增 `calibration` 字段据实报告，不再让调用方猜。
- **真实流水线实证**：把 4 条样本按真实 schema 落入 perf 采集通道（`data/perf_runtime/<run_id>.jsonl`，
  模拟 step4 采集产物），经 Web API 跑真实 Step5（`drain → 4 条样本`）：
  `perf_report_step1_verify__v1_0.json` 里 `calibration={'mode': False, 'status': 'skip_regular'}`、
  `baseline_version_before=8`，基线文件 `LastWriteTime`/字节数**不变**、version 仍 8、日志无
  「基线已更新」；同时 `gate_summary total=4 pass=4 fail=0`、`[S45-014] verified=True`、
  `[S45-015] 22/22` 保持。策略契约 `tests/test_perf/test_perf_baseline_write_policy.py` 5 passed；
  `tests/test_perf` + `tests/test_core` 全绿 **322 passed**。

**E. A7 闭环（删除引擎内置默认凭据后，登录型靶子可达）**
- `api_demo`（登录型，`auth.type=token` 声明）经 `main.py target` 实测：**4/4 PASS**、
  authz 探针 **5/5**、perf **5/5 PASS**、`calibration.status=skip_regular`（证据 `temp/_a7_api_demo.json`）。
  证明 §4.8 A3/A7 的「不替靶子猜账号、只读 `auth` 声明」对需登录链路可达且工作正常。

**F. 执行身份判据复核（部分闭环，口径如实）**
- 需求级：`_identity_assign.pick_identity` 现读上游结构化 `requirements.payload_json.access_control`
  （单一事实源 `core/requirement_semantics.is_access_restriction_req`，仅字段缺失的历史行才回退到
  同一函数），不再自持第二套限制词表 → §6「仍未清」条目据此收敛。
- 断言级：`case_expects_denial` 先看结构化 check 类型（`access_control_check`），仅当结构化 check
  缺失时才回退 `_DENY_ASSERTION_RE` 文本判定（判「该用例断言是否要求被拒绝 / 点名了哪个角色」，
  与需求级语义不同）——保留为**结构化优先的文本兜底**，不宣称已消除。

**G. HTML 交付物在流水线内不可达（已根因修复）**
- 病灶：`generate_report(..., generate_html=False)` 是默认值，而 `scheduler._step5_report` 与
  `streaming_scheduler._make_step5_func` 两个**唯一生产入口**都不传该参数 → `html_report`
  在真实流水线里恒不存在（能力齐全、调用方缺席）。平台侧 `_file_tree` / `mysql_doc` 早已按
  `html_report` 类型取用，即「产物类型被上层承认但下游从不生产」。
- 修复：在两处生产入口传 `generate_html=True`（不新增分支、不改渲染逻辑），HTML 由引擎原生
  `_generate_html_report` 渲染并落 `generated_reports(report_type='html_report')`。
- **实证**（真实 Step5，00:40）：日志 `HTML 报告已写入 MySQL: project=step1_verify
  version=v_20261027_004046 size=244955`，DB `generated_reports id=41 / html_report / char_count=244955`；
  同轮 `[S45-014] verified=True`、`[S45-015] 22/22`、perf `calibration=skip_regular` 均不变。
- 交付物导出（引擎原生内容，未另写模板）：`e2e-test-report-2026-10-27/step5_report.md`（149972 字符）
  + `step5_report.html`（244955 字符）+ `coverage_report.md`。

### §4.10 表单补全极性上游化（清空态全链路实证 2026-10-27 02:10→03:16，3854s）

**A. 权威数字（purge 49092 行 / 0 错误 → 重启引擎 → 无人值守 Step1→5）**
- Step1 02:10:32 / Step2 02:10:42（requirements 70，review_lane 70）/ Step3 02:10:52→02:11:17
  （**test_cases 488 + case_meta 488**）/ Step4 02:12:16→03:16:23（3854s）/ Step5 03:16:30→03:16:39，
  五步 `RUN_END status=completed`；`run_logs` 分布 `{0:30, 1:62, 2:87, 3:3363, 4:25359, 5:47}`。
- Step4：491 行 → **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**；决策口径 **94.4%（369/391）**；
  与上一基线逐用例比对**仅 1 条状态变化**（`TC-REQ-029-V02` SKIP→PASS），零回归。
- 静态断言强度：仅弱断言 **63**、无断言 4；`security_probe_check 43` / `list_row_count_check 66` /
  `field_error_visible_check 15` 照常生效。
- Step5：`[S45-014] verified=True, db_counts={PASS:369, FAIL:22, SKIP:97, PRE_GATE:3}`；
  `[S45-015] 22/22 命中`；`html_report 248008` / `report 152994` / `coverage_report 4819`。
- perf：`gate_summary {total:4, pass:4, fail:0, blocking:false}`、`calibration=skip_regular`、
  `baseline_version_before=8`（参照系未漂移）。
- 交付物：`e2e-test-report-2026-10-27/{step5_report.md,step5_report.html,coverage_report.md}`。

**B. 根因：提交前表单补全的极性判据只存在于执行层，且输入各自拼装**
- 病灶：`single_mixins/_base.py` 从用例的 `_explore_task` 取
  `operation/expected_feedback/description` 调极性判据；**结构化执行用例没有该字段**
  → 三参全空 → 判据退化为「补全」→ 「空提交/非法值触发字段校验」类用例的价格/库存被自动
  填成合法样例 → 提交被受理并跳回列表 → **被测的错误反馈面结构性不存在**。
- 量化作用面：无 `_explore_task` 且含提交点击的用例 86 条，其中期望为错误反馈 **11** 条；
  上一基线这些 PASS 的判定证据**全是靶子「商品名称已存在（需唯一）」**（即在**无关规则**上通过），
  1 条（`TC-REQ-029-V02`）落 `field_error_visible_check:⊘(页面无可编辑字段)` 无结论。
- 修复（§6 纪律：上游一次性判定落结构化字段，下游只读不推）：
  1. 新建 `src/pipeline/utils/form_fill_policy.py` —— 极性判据的**单一事实源**
     （`should_autofill` + 两个通用常量 `_SERVER_REJECT_RE` / `_CLIENT_BLOCK_WORDS` 从
     `form_completion.py` 迁入）；`FORM_FILL_REQUIRED="fill_required"` / `FORM_FILL_LEAVE_EMPTY="leave_empty"`。
  2. 唯一生产点 `declare_form_fill_policy(case)` 挂到落库前咽喉
     `_check_guard.sanitize_intents`（`ensure_post_action_triggers` 之后、纯验证免探索判定之前）
     → 落结构化字段 `form_fill_policy`；存量 488 条实测 `fill_required 391 / leave_empty 97 / 缺字段 0`。
  3. 执行端 `_base.py` 改 `autofill_allowed({**intent, **(intent_payload or {})})` **只读**该字段；
     字段缺失的历史行回退到同一判据函数（不另立第二套词表）。
  4. Agent/引导路径（`agent/explorer_mixins/_action.py`）判据与常量改从同一模块 import。
- **实证（本轮唯一状态变化）**：`TC-REQ-029-V02` → **PASS**，证据由「仅在名称唯一性上成立」
  变为需求自身的字段规则：`field_error_visible_check:✓('商品名称已存在（需唯一）' + **'价格必须是数字'**)`；
  另 10 条同类用例（010-E01/011-E01/011-I02/032-V02/044-V02/047-V02/063-I02/063-I03/063-V02/065-E01）
  同样带上 `'价格必须是数字'` 字段级证据。
- 契约测试 `tests/test_core/test_form_fill_policy_contract.py` **9 passed**（含：错误期望→`leave_empty`、
  正向→`fill_required`、声明字段优先于文本重推、缺字段回退同一判据、「服务端校验」型仍补全 /
  客户端拦截型不补全的双向守卫、`sanitize_intents` 唯一调用点、`form_fill_policy` **全局唯一写入点
  与唯一判据定义**的静态门禁）。`tests/test_core+test_perf+test_step4+test_step5` **431 passed**。

**C. 诚实保留的口径（不宣称已清零）**
- `leave_empty` 中含提交点击者 **14 条**（= 上述 11 + 010-I01 / 020-I01 / 065-I01）。
  后 3 条属需求文本含「错误结构 / 失败行号 / 错误率」而命中极性判据的**宽匹配**；本轮判定未变
  （仍 PASS），但其判定证据仍是表单校验面而非需求所述语义，属**弱判定面**，记录不宣称已收敛。
- FAIL 22 与上一基线同集合、同归因（全 PRODUCT_BUG）：CSP 拦截内联 `onsubmit=confirm` 6
  （007-I03/037-E01/038-I03/039-I03/041-I03/061-I03）+ REST 404 5（054~058-I01）
  + any_of dialog 分支全不成立 3（037-I01/068-E01/068-I01）+ any_of 全分支非判定态 2
  （039-I02/041-I02）+ 字段约束 2（029-I01 / 044-I01）+ 删除未生效 2（061-I01/E01，Δ=0）
  + 069-E01 text_contains 0 命中 + TC-PAGE-ROOT-UX01 click_target_size 43×23<32px。

### §4.11 代码规模治理（2026-10-27，零新增引擎代码、净减 728 行）

**A. 规模实测（磁盘实际文件，排除 `.git/data/logs/temp`；`temp/_size.py` 可复算）**
- 全仓 `.py` **1,514 文件 / 338,012 行**；其中 `src/`（引擎本体）**1,172 文件 / 294,963 行**。
  非引擎：`tests` 10,467 / `scripts` 10,360 / `examples` 4,738 / `target_systems` 2,688 /
  `ModSystem` 4,034 / `.ui_sweep` 3,512 / `.workbuddy` 2,332 / `marketing` 1,476。
- 其它产物：js 200,011 / json 96,948 / html 94,142 / ts 53,583 / md 34,495 / css 10,705。
  → 「近 40 万行」口径 = 全仓各语言合计；引擎本体 .py 为 29.5 万行。
- 引擎内部最大块：`pipeline/steps/step4` **75,278 行 / 269 文件（25.5%）**；
  step3 10,657 / step2 9,656 / step1 8,854 / step5 8,693。

**B. 判据与结果：模块级死代码按「入口可达性」判定（不再只看 import 有无）**
- 旧「零引用」判据有假阳性：`case_rules/` 与 `breakdown_rules/` 由 `pkgutil.iter_modules`
  自动发现（21 个模块属活代码），`step4/verification/_verify_*.py` 由 `_rule_meta` 动态加载。
- 现行判据（`temp/_dead_reach.py`）：节点=全部 tracked `.py`；边=AST import（绝对/相对，
  `__init__.py` 归一为包名）+ 字符串 `"src.*"` 引用 + 动态发现边（pkgutil 包、`_verify_*.py`）；
  根=`main` / `src.backend.app` / 文档化独立脚本入口。
- 结果：生产入口不可达 **6 模块 / 1,555 行**，处置如下：
  - **删除 2 个失效模块 / 728 行**：`src/core/config_ui.py`（627 行；独立 Flask 配置页，
    与 `backend/routes/system_config_api` 语义重复，全仓零导入、零注册、零测试）
    + `src/pipeline/steps/step4/verification/verdict.py`（101 行；文档自身已声明
    「未接入执行器终判」，全仓零引用）。
  - 保留 2 个文档化独立入口：`src/frontend/verify_lock.py`（281 行，`.github/workflows/ci.yml`
    直接执行的前端完整性门禁）+ `src/verification/cli.py`（214 行，自带用法示例的台账 CLI）。
  - 保留 1 个仅脚本引用模块：`src/extensions/evidence_manager.py`（331 行，
    `scripts/case_runner/_tracks_exec.py` 使用）。
- **净减 2 文件 / 728 行，本轮零新增引擎代码**。删除后
  `pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5` = **431 passed**
  （与删除前基线一致）。文档中指向这两个已删模块的描述已成历史（`docs/引擎技术设计与实现.md`
  `:4949`、`:5713` 等），不再作为代码事实引用。

**C. 「无来源增添代码」审计（如实记录，未删除）**
- `git ls-files --others` 实测未入库 `.py` **33 文件 / 4,264 行**，其中 `src/` **11 文件**：
  `core/project_purge.py`、`core/requirement_semantics.py`、`core/upload_documents.py`、
  `step1/file_parser/_encoding.py`、`step1/rag_coverage_audit.py`、
  `step2/breakdown_rules/declarative_line.py`、`step2/breakdown_rules/predicate.py`、
  `step4/data_precondition.py`、`step4/form_completion.py`、`utils/form_fill_policy.py`、
  `utils/step_assertions.py`。
- 这些均有修复来源与实证记录（§4.6 B、§4.8 A8、§4.9 C/D、§4.10），属「有来源、未入库」，
  缺 git 历史。处置：本轮不删除，待提交入库；后续新增引擎模块必须同时给出
  「来源 = 已实证缺陷 / 单一事实源」否则不落。

**D. 诚实口径（未做的部分）**
- 引擎本体已无模块级死代码（可达性扫描归零）；但**函数级死代码/重复实现未做**：
  Step4 用 mixin 链 + 动态 dispatch（`_UI_CHECK_DISPATCH`、`_rule_meta`），静态判据假阳性高，
  需另立专项并以真实流水线复跑核验，不得凭静态结论删代码。
- 跨文件完全相同的实现（AST 归一化 ≥40 行）实测 **0 组**（`temp/_dup_impl.py`），
  即当前体量不是「复制粘贴」堆出来的。

**E. 清空态全链路等价性实证（删除后无人值守 Step1→5，2026-10-27 05:19→06:24，3797s）**
- 先 `purge_project('step1_verify')` 清 **49,117 行 / 0 错误**（输入态与注册表保留），
  重启引擎加载删除后的代码，连续驱动：
  Step1 05:19:04→05:19:05 / Step2 05:19:14→05:19:16（requirements 70，review_lane 70）
  / Step3 05:19:24→05:19:56（**test_cases 488 + case_meta 488**，`form_fill_policy`
  `fill_required 391 / leave_empty 97 / 缺字段 0`，与删除前逐值一致）
  / Step4 05:20:48→06:24:03（3797s）/ Step5 06:24:05→06:24:15；五步 `RUN_END status=completed`。
- Step4：491 行 → **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**；决策口径 **94.4%（369/391）**；
  静态弱断言 **63** / 无断言 4；check top（`no_error 242 / element_exists 138 /
  semantic_check 98 / performance_lcp_check 68 / list_row_count_check 66 /
  viewport_overflow_check 50 / loading_complete 49 / security_probe_check 43 /
  text_contains_check 24 / field_error_visible_check 15`）与删除前完全一致。
  FAIL 22 集合与归因不变（全 PRODUCT_BUG：CSP 拦截内联 `onsubmit=confirm` 6 + REST 404 5
  + any_of dialog 全不成立 3 + any_of 全分支非判定态 2 + 字段约束 2 + 删除未生效 2
  + 069-E01 text_contains 0 命中 + TC-PAGE-ROOT-UX01 click_target_size 43×23<32px）。
- Step5：`[S45-014] verified=True, db_counts={PASS:369, FAIL:22, SKIP:97, PRE_GATE:3}`；
  `[S45-015] 22/22 命中`；`html_report 247,787` / `report 152,849` / `coverage_report 4,819`。
- perf：`gate_summary {total:4, pass:4, fail:0, blocking:false}`、`calibration=skip_regular`、
  `baseline_version` 8（未回写、未漂移）。
- 交付物：`e2e-test-report-2026-10-27/{step5_report.md, step5_report.html, coverage_report.md}`。
- 结论：删除 2 个失效模块后**判定与产物零差异**（等价性成立），本轮不做任何判定逻辑改动。

**F. 函数级 / 级联死代码治理（2026-10-27，零新增引擎代码）**
- 判据（三重，任一不满足即不删）：① AST 零引用（`temp/_dead_funcs.py`，排除装饰器注册型
  `route/fixture/task`、dunder/property、`on_*` 插件 hook 动态分发）；② 全仓文本复核
  （`temp/_dead_funcs_verify.py` 扫 2,346 个 .py/.json/.md/.html，命中仅限定义行）；
  ③ 命名动态分发黑名单（`getattr(plugin, f"on_{hook}")`、`getattr(self, f"_scenario_{x}")`、
  `globals()[...]`、`pkgutil` 自动发现包、`_verify_*.py` 动态边）。
- 删除清单（`temp/_dead_delete_report.json` 可复算）：
  - **33 个函数 / 2,095 行 / 24 文件**（显式清单，`temp/_delete_defs.py` 按 AST 行区间删，逐文件
    `ast.parse` 自校验）。代表项：`db_access._query_table_columns_meta(77)` /
    `_query_table_indexes(33)`、`capability_cache.record_failure(54)`、
    `_modeling_intents._generate_cross_requirement_intents(194)`（自注「当前禁用最安全」）、
    `op_cache.invalidate_on_schema_change(143)`、`db_agent/schema.load_schema(144)`、
    `contract_builder.diff_contract_schemas(131)`/`validate_response_schema(71)`、
    `data_factory_core.execute_import_workflow(111)`、`step5/minimal_repro.extract_minimal_repro(84)`、
    `auth/encryption.rotate_encryption_key(81)`、`interactions.{select_option,check,hover,press_key,
    double_click,right_click}_element` 等。
  - **级联 16 个函数 / 512 行**（`temp/_cascade_dead.py`，白名单 13 文件 × 三重判据，多轮至
    dry-run=0）：`db_agent/cleanup._build_cleanup_order(55)`、`schema.{_resolve_column,_build_table_patterns,
    _find_name_column,_find_code_column}`、`data_factory_core` 模板/上传/下载链路 10 个私有方法
    （`_verify_import_result(77)`/`_try_api_import(72)`/`find_and_click_download_template(63)` 等）。
  - **4 个整文件 / 639 行**：`playwright_engine/actions/{click,fill,type_text}.py`（381 行，
    ActionsMixin 的 legacy 原子动作，真实执行只经 `action_actions.tool_name → ALL_TOOLS`，
    全仓 `engine.hover/click/...` 零调用）+ `step4/data_gen.py`（288 行，唯一调用方为已删的
    `_try_api_import`）。同步清理 `actions/__init__.py` 的 import/基类/文档（−94 行）。
- **净减 4 文件 / 3,341 行**（`temp/_size.py` 复算）：全仓 `.py` 338,012→**334,671**，
  `src/` 294,963→**291,622**（文件 1,172→1,168）。零新增引擎代码。
- 静态验证：`compileall` 全通过；16 个关键模块导入通过；`temp/_cascade_dead.py` dry-run=0；
  `pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5` = **431 passed**（前后一致）。
- **清空态全链路等价性实证**：`purge_project('step1_verify')` 清 49,116 行/0 错误 → 重启引擎 →
  无人值守 06:43:47→07:49:44（Step4 3,835s）：
  - Step3 `test_cases 488 / case_meta 488`；Step4 `RUN_END completed`；
    executions **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**、决策口径 **94.4%（369/391）**、
    静态弱断言 **63** / 无断言 **4**、check top 与基线逐项相同。
    FAIL 22 集合与归因不变（PRODUCT_BUG：CSP 内联 `onsubmit` 6 / REST 404 5 / any_of 全分支
    不成立 5 / 字段约束 2 / 删除未生效 3 / 069-E01 text_contains 0 命中 / ROOT-UX01 43×23<32px）。
  - Step5 `html_report 247,788 / report 152,850 / coverage_report 4,819`；后两者与基线差 +1 字符
    （运行时间戳数字位差，非语义差异，已比对内容结构一致）。
  - 交付物已重导出覆盖：`e2e-test-report-2026-10-27/{step5_report.md, step5_report.html,
    coverage_report.md}`（07:49:44 版本）。
- 诚实缺口（未收敛，勿包装为已修）：弱断言 63 / 无断言 4 不变（根因在 Step3 生成端断言族）；
  98 条 `semantic_check` 因运行期 AI 禁用仍为诚实 SKIP；`leave_empty` 14 条宽匹配弱判定面
  （010-I01/020-I01/065-I01）与 `_NEGATED_DEFENSE_RE` 缺「非」前缀未动。

**G. monkey-patch 清理 + 重复实现收敛（2026-10-27，零新增引擎代码）**

- 判据（沿用 §6「三禁」）：① 运行时注入/改写他人类的胶水一律删，钩子必须只有一份真实实现；
  ② 同一语义在 ≥2 处各写一遍实现即「重复造轮子」，收敛到唯一事实源后调用方只 import。
- 删除（全仓文本复核零调用方后执行）：
  - `step4/browser_ai_patch.py`（45 行，文件头自述「通过 monkey-patch 方式在运行时注入插件钩子」）。
  - `step4/ai/dispatcher.py`（177 行，`dispatch_action` / DOM 指纹 / 差异记录；全仓仅
    `browser_ai.py` docstring 与 patch 文件提及，src/tests 零调用）。
  - `browser_core/base.py::_init_plugins` 末尾的 `patch_dispatch_action(BrowserAi)` 注入块；
    `browser_ai.py` 的 `_BrowserAiDispatcherMixin` 基类与文档行。
  - 钩子唯一化后：所有执行路径（AI 循环 / 结构化 / 工具 / 数据工厂）统一走
    `action_hooks.run_action_with_hooks`，不再经 monkey-patch 注入。
- 重复实现收敛（`temp/_dup_funcs.py` 归一化 AST 扫描取证，24 组 → 22 组）：
  - `record_metric` **5 份手抄 → 1 份**（唯一实现移入 `extensions/metrics_collector.record_metric`），
    调用方 `browser_ai` / `ai/tool_mode` / `ai/text_mode_mixins._helpers` /
    `browser_ai_validator._metrics` / `action_dispatchers` 改为 import 别名；
    其中 `browser_ai._record_metric` 与 `browser_ai._fill_placeholders` 本就零调用，直接删除。
  - `_load_allowed_actions_from_library` **2 → 1**（`browser_ai` 权威，`validator/_validate` import）。
  - `browser_ai.py` 死副本删除后同步修文档（不再列 `_dispatcher.py`）。
- 规模（`temp/_size.py` 复算）：全仓 `.py` 334,671→**334,363**，`src/` 291,622→**291,314**，
  文件 1,168→**1,166**。净减 2 文件 / 308 行，零新增引擎代码。
- 静态验证：`compileall` 全通过；关键模块导入冒烟通过（`_record_metric is record_metric` 为真）；
  `pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5` = **431 passed**。
- **清空态全链路实证（两轮）**：
  - 第一轮 09:20 启动后 Step4 于 09:32 被判挂起中止。**根因非本次改动**：靶子 smartshop 未运行
    （8020 被外部进程占用且 HTTP 超时），首个用例 `[PRE-CHECK] 页面不可达` 导航挂到 ~190s 触发
    看门狗；`dispatch_action` 删除后全仓零调用方已静态复核。
  - 对照轮（09:44:53→10:54:13）：清 49,102 行 → executions **PASS 369 / FAIL 22 / SKIP 97 /
    PRE_GATE 3**、**94.4%（369/391）**、weak 63 / no_assert 4、test_cases 488、check top 逐项相同。
  - 最终轮（10:57:34→12:05:49，含上述全部收敛）：清 49,101 行 → 分布与对照轮**逐项相同**
    （369/22/97/3、94.4%、weak 63、no_assert 4、check top 一致）；FAIL 22 集合与归因不变；
    Step5 `html_report 247,725 / report 152,757 / coverage_report 4,819`（与基线仅字符数差异，
    来自运行时长/时间戳数字位，结构一致）；交付物已覆盖导出到
    `e2e-test-report-2026-10-27/{step5_report.md, step5_report.html, coverage_report.md}`。
- 已取证未动（下一轮按需、须先给判据）：`_dup_funcs` 剩余 22 组中真正重复的逻辑
  （`_require_perm`/`_login_required` 双份、`_to_json`/`_from_json`/`_get_db` ×4 存储模块、
  `_type_name` ×2、`_extract_url` ×2）；
  `deep_assertion_runner.py` 的 `case_id in ("P01"/"N01"/"B01"/"D01")` 属 `main.py` CLI 文档化夹具场景，
  保留并已注明来源，非补丁。

**H. 持续监控通用化（2026-10-27，零新增模块 / 零阈值调整）**

- 事故取证（`temp/_win_log.py` 还原 PowerShell 折行日志，09:28:50–09:32:30 全 46 条）：
  `09:29:15 [PRE-CHECK] 页面不可达 (url=about:blank)，尝试导航...` → 两次
  `[ConfigTarget] TARGET_URL=http://127.0.0.1:8020 不可达，回退靶子注册表` → 至 `09:32:18` **零日志**
  → watchdog 判「进展停滞 180s」并 dump 全线程栈（worker 线程停在 `asyncio.run` IOCP `_poll`，
  即事件循环空转等 I/O）→ `[sweep]` 置 force_stop → `RUN_END status=failed`。
- 结构性根因（三条，均为「判据错位」而非「阈值太小」）：
  ① run 级停滞预算(180s) < 用例级超时(300s)，恢复粒度落在 run 而非用例；
  ② 看门狗只知道「run 无新日志」，不知道「用例内有硬上界的长等待正在飞」；
  ③ `_check_preconditions` **丢弃 `engine.goto` 返回值**、吞异常后仍 `return True`，
     把「导航失败」判成「前置条件成立」；`page_ready.wait_page_ready` 声明「有界 fail-open」
     但只在 `evaluate` **返回后**才查上限，坏页面上 `locator.count()`/`page.evaluate` 可永不返回。
- 实证（`temp/_probe_goto.py`，本地起「TCP 可连但永不应答」服务）：`page.goto(timeout=5000)`
  5.6s 抛 `Page.goto: Timeout 5000ms exceeded`；随后 `locator.count()` **>60s 未返回**。
  即「导航有界、页面查询无界」，静默 181s 的真凶是后者。
- 改动（全部为既有单点的通用化，不新增旁路、不动任何阈值）：
  - `ai/ai_inflight.py`：新增 `bounded_wait_inflight(timeout)` 登记表，`inflight_deadline()` 取
    AI 请求与有界等待两表的最大值；看门狗只读这一个判据（原「AI 专用豁免」消除）。
  - `browser_core/{serial,parallel}.py`：单用例 `asyncio.wait_for` 外层登记有界等待
    → 恢复粒度回到**该用例**（到期落 TIMEOUT + `record_execution`），不再由 run 级误杀。
  - `config_loader/target.py`：新增 `is_target_reachable(timeout, fresh)`（复用既有
    `_url_reachable`，唯一事实源，不另造探测）。
  - `browser_core/base.py`：`run_test_cases` 增加**批次级环境健康门禁**（不可达则整批
    `BLOCKED` + `failure_category=ENVIRONMENT_ERROR`，不启动浏览器、不逐条空转）；
    探测预算**不复用**执行器 `timeout_ms`（实测 600s，黑洞端口会让门禁自身卡死 600s，
    仍会被 180s 的 run 级看门狗判挂起），改为由探测单一事实源 `_url_reachable` 的默认 2s 决定
    （只传 `fresh=True` 要「此刻的结论」，不新增第二个超时旋钮）；
    `_block_all_candidates()` 成为「批次级前置不成立」的唯一落库实现（登录失败 / 目标不可达共用）；
    `_check_preconditions` 按 `goto` 真实返回值判定，密码表单探测加同口径 `wait_for` 上界。
  - `core/page_ready.py`：每次 `evaluate` 用 `asyncio.wait_for(remain)` 包住，声明上界真实生效
    （超时仍 fail-open，不升级为用例失败）。
  - `routes/execution/run/_start.py`：仅日志措辞与 docstring 对齐（「在飞豁免」），判据未改。
- 静态验证：`compileall` 全通过；导入冒烟 `temp/_smoke_monitor.py` 通过，其中
  「挂起页 + cap=1500ms」实测 **1.51s 返回 False**（改造前无限挂起），
  `bounded_wait_inflight(5s)` 对 `inflight_deadline()` 可见且释放后归零；
  `pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5` = **431 passed**。
- **清空态全链路等价性实证**：停引擎 → `purge_project('step1_verify')` 清 49,093 行/0 错误 →
  重启引擎 → 无人值守 14:09:19→15:19:53（Step1 10s / Step2 10s / Step3 80s /
  **Step4 14:11:02→15:19:31，4,115s** / Step5 15:19:38→15:19:53，五步 `RUN_END completed`）：
  - executions **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**、决策口径 **94.4%（369/391）**、
    静态弱断言 **63** / 无断言 **4**、`test_cases 488 / case_meta 488`、check top 十项
    （`no_error 242 / element_exists 138 / semantic_check 98 / performance_lcp_check 68 /
    list_row_count_check 66 / viewport_overflow_check 50 / loading_complete 49 /
    security_probe_check 43 / text_contains_check 24 / field_error_visible_check 15`）
    与基线**逐项相同**；FAIL 22 集合与归因不变（CSP 内联 `onsubmit` 6 / REST 404 5 /
    any_of 全分支不成立 5 / 字段约束 2 / 删除未生效 3 / 069-E01 / ROOT-UX01 43×23<32px）。
  - Step5 `[RUN_END] status=completed`；`html_report 247,656 / report 152,688 /
    coverage_report 4,819`（与前轮仅数字位差，结构一致）。
  - 交付物已覆盖导出：`e2e-test-report-2026-10-27/{step5_report.md, step5_report.html,
    coverage_report.md}`（15:19:53 版本）。
- 规模（`temp/_size.py` 复算）：全仓 `.py` 334,363→**334,474**，`src/` 291,314→**291,425**，
  文件数 1,166 **不变**（净 +111 行，全部为上述 6 处判据收敛，无新模块）。
- 判据双向实证（`temp/_probe_reachable.py`，只读不写库）：活靶子 `127.0.0.1:8020` →
  `reachable=True 0.05s`；死端口 `127.0.0.1:8098` → `reachable=False 2.01s`（探测有界）。
- **端到端负向演练（`temp/_drill_gate_block.py`，隔离项目 `gate_drill`，完事即删）**：
  用黑洞端口（TCP 可连但永不应答）替代真实靶子 → `is_target_reachable` **2.02s 判不可达** →
  `BrowserCore.run_test_cases` 全批返回 `blocked=2 / passed=0 / failed=0 /
  failure_category=ENVIRONMENT_ERROR`（总耗时 **2.05s**，未启动浏览器），
  `executions` 实落 2 行 `BLOCKED + ENVIRONMENT_ERROR`（`failure_reason=目标系统不可达: …`）；
  清理后 `projects=0 / executions=0`（级联删除已验证），真实项目分布零改动。
  该演练同时暴露并修掉一个真实缺陷：门禁原把探测超时取成执行器 `timeout_ms`（600s），
  黑洞端口会让门禁自己卡到 600s —— 已改为探测自身 2s 预算（见上）。
- 诚实缺口（未收敛，勿包装为已修）：①探测预算 2s 与「SUT 冷启动 >2s」存在张力：冷启动窗口内
  会判不可达 → 整批 BLOCKED（结论正确但代价是整批不执行），当前**未**做失败后重探/预热等待，
  是否值得引入需先给判据；②`locator.count()` 等页面查询遍布工具层（约 254 处），**结构上**
  均位于 `_execute_one_intent` 的用例级 `wait_for` + `bounded_wait_inflight` 之内（故有上界），
  但登录/页面侦察等「用例前阶段」依赖各自逐动作 timeout，未做「每个 await 必有上界」全量审计；
  ③弱断言 63 / 无断言 4 不变（根因在 Step3 断言生成端）。

**I. 错误页判定的数据误判治理（2026-10-27）**

- 现象（下一轮清空态复跑时暴露）：Step4 首个 UI 用例起，导航到 `http://127.0.0.1:8020/`
  连续被判「错误页面」→ 重登/菜单回退均失败 → 结构化导航失败 → 用例 SKIP；随后
  `Guided` 的 Pre 校验同样命中 404 → 4 条基线 PASS 用例（TC-REQ-010/011/020/065-I01）
  降为 ERROR。
- 取证（三层，均为只读）：
  - 日志：`[Navigate] 检测到错误页面: keyword=404`、`[ContentVerify] ❌ SPA 404页面检测:
    命中关键词=['404']`（`temp/_win_runlogs.py` 窗口 17:21:20–17:24:10）。
  - 截图：`temp/_shot_dump.py TC-REQ-001-I01` → `temp/_shot.png` 实为**正常商品列表**
    （共 137 件商品、第 28/28 页），并非 404 页。
  - 数据：`temp/_scan404.py` 扫描靶子列表 → 第 27 页商品描述 `TEST_hnlg404pg7`
    含 "404" 子串。根因即：**错误页判定用整页文本子串匹配数字状态码**，测试数据本身
    可含 "404"/"403" 字样 → 正常页误判为错误页。
- 修复（唯一谓词 + 三处副本收敛，零新模块）：
  - `browser_tools/execution/_single_nav_state.py`：新增 `_keyword_hit()`（纯数字状态码
    按**独立 token** 匹配，`TEST_hnlg404pg7` 不命中）；新增 `page_is_notfound_response()` /
    `notfound_keyword()`；`page_is_denial_response`、`_denial_keyword`、`_check_error_page`
    全部改为读同一命中器；`_NOTFOUND_KEYWORDS` 扩为跨三方的并集（唯一词表）。
  - `browser_core/single_mixins/_exploratory.py`：删除模块级 `_NOT_FOUND_KW` 与
    `_verify_page_content_loaded` 内的本地词表，三处（内容加载 Pre 校验、模块验证两分支）
    统一 import 上述谓词。
- 双向实证（`temp/_check_pred.py`）：`TEST_hnlg404pg7` / `A403X-2026-0001` → 不误判；
  `404 页面不存在`、`Page Not Found`、`403 Forbidden` → 仍命中（6/6 通过）。
- 顺带修复（同源判据形态，非新增逻辑）：`_ai_loop` 在运行期 AI 闸门关闭时
  **跳过空响应重试**——策略性拒绝重试同请求同结论，实测单轮 187 次空响应 ≈ 9 分钟
  纯等待（`runtime_ai_gate` 仍是唯一判据，仅去掉无意义退避）。
- 静态验证：`compileall` 通过；`pytest tests/test_core tests/test_perf tests/test_step4
  tests/test_step5` = **431 passed**。
- **清空态全链路等价性实证**（17:47:48→18:58:20，五步 `RUN_END completed`；Step1 10s /
  Step2 10s / Step3 80s / **Step4 17:49:32→18:57:58，4,113s** / Step5 80s）：
  - executions **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**、决策口径 **94.4%（369/391）**、
    weak 63 / no_assert 4、`test_cases 488 / case_meta 488`、check top 十项、**FAIL 22 集合**
    与基线逐项相同（CSP 内联 6 / REST 404 5 / any_of 5 / 字段约束 2 / 删除未生效 3 /
    069-E01 / ROOT-UX01）。
  - 误判归零：`[检测到错误页面]` **0**、`[SPA 404]` **0**；上一轮由误判降级的
    **TC-REQ-010/011/020/065-I01 全部回到 PASS**（不在 FAIL 集合中）。
  - Step4 日志 25,347 行；`运行期 AI 已禁用` 209 次均为**确定性判定**（不再伴随空响应退避）。
  - Step5 产物：`html_report 247,898 / report 152,960 / coverage_report 4,819`，
    已覆盖导出 `e2e-test-report-2026-10-27/{step5_report.md, step5_report.html,
    coverage_report.md}`（18:58:20 版本）。

**J. 登录阶段页面查询有界化（2026-10-27，缺口②首片，零新增旋钮）**

- 结构缺陷：Playwright 的 `locator.count()` / `page.content()` / `page.evaluate()`
  **不接受 timeout 参数**（`page.set_default_timeout` 也管不到），执行上下文不可用时
  它们会永不返回（H 段实测 60s+）。登录发生在**任何用例之前**，不在
  `_execute_one_intent` 的用例级 `wait_for` + `bounded_wait_inflight` 之内 →
  一处挂死 = 整轮 run 静默，只能被 run 级看门狗杀掉（与 H 段事故同一失效面）。
- 修复（唯一实现，四处登录路径统一接入）：
  - `browser_core/session_login.py`：新增 `count_bounded()`（返回 `-1`＝探测超时、
    `0`＝确实不存在，调用方 `> 0` 判定语义不变，但归因可区分「环境坏」与「元素缺」）与
    `content_bounded()`；上界 `LOGIN_PROBE_TIMEOUT_SEC=10s` 与该模块既有逐动作上界
    （`wait_for_selector` 5s/10s）一致，**未新增配置旋钮**；`has_login_form`、
    `session_login._fill_submit_verify` 内部同样改读该谓词。
    （**2026-10-27 晚已迁出**：唯一实现收归 `core/page_ready`，见 N 段。）
  - 接入：`ai/login.py::_try_deterministic_login`（5 处 count + 1 处 content）、
    `browser_ai_login.py::try_deterministic_login / detect_login_page`（9 处 count）、
    `capabilities/login.py::_verify_login_success`（2 处 count）。
- 实证：挂起页冒烟（`temp/_smoke_monitor.py`）`count_bounded` → `-1` **1.50s**、
  `content_bounded` → `""` **1.51s**（改造前无限挂起）；`compileall` 通过；
  三模块导入冒烟通过；`pytest` 四组 431 passed。
- 诚实缺口：本轮靶子 `auth.type=none`（打印 `[SessionAware] 靶子无需登录 → 跳过自动登录`），
  登录路径**未被真实走到**，故等价性来自「同一判据 + 纯包装（仅加 `asyncio.wait_for`）」
  与挂起页实证，而非全链路用例覆盖；其余「用例前阶段」（前置检查、预检、侧边栏展开）
  结构上已在用例级 `wait_for` 之内。

**K. 「冷启动重探」不引入（2026-10-27，判据先行，不新增猜测性宽限）**

- H 段诚实缺口①原样保留，理由是判据推导后不成立：
  - 真实冷启动在端口 bind 之前，`TCP connect` 是**连接被拒**——与「靶子宕机」在协议层
    **不可区分**；唯一可区分的是「TCP 可连但 HTTP 不应答」（进程在、尚未就绪），
    而后者恰恰就是 `temp/_drill_gate_block.py` 的黑洞端口形态。
  - 给「连接被拒」加宽限＝给任何真宕机加 N 秒假等待；只给「TCP 可连」加宽限则对真实冷启动
    **无效**。两者都不比现状更接近真相。
  - 现状代价有界且可观测：2s 内给出确定结论 → 全批 `BLOCKED` + `failure_category=ENVIRONMENT_ERROR`、
    不启动浏览器、不浪费浏览器预算；门禁**按批次**执行，SUT 就绪后的后续批次自动恢复。
- 结论：保持「确定不可达 → 立即阻断」。若未来出现「TCP 可连且短时内转为可应答」的实测证据，
  再按 `_tcp_listening`（TCP 层唯一判据）+ 有界重探单点引入。

**L. 通过证据必须是可观测事实（2026-10-27，反假 PASS）**

- 现象：`executions.actual` 中 4 条 PASS（TC-REQ-010/011/020/065-I01）写着
  `验证通过[错误条检测]: 在监控模块中，当接口错误率超过 5%时，告警`——这是**需求期望句子**，
  不是页面事实。`temp/_shot.png`（该用例终态截图）显示页面停在「新增商品」表单
  （含 `价格必须是数字`/`库存必须是整数` 提示），并无该句。
- 根因（单行）：`agent/explorer_mixins/_action.py` 通过分支用
  `"验证通过[%s]: %s" % (_vmethod, _expect)` 把**期望**当证据写进 actual；
  `_verify_records.actual` 里有真实命中文本却从未出现在执行记录。
- 修复：改为 `_actual`（页面命中文本/反馈条内容），为空则 `(无文本证据)`；期望仍完整保存在
  `_verify_records.expect` 供报告对照。判定逻辑零改动（`_ok` 不变）→ 分布不变。
- 实证：`compile_ok`；`pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5`
  = **431 passed**；`grep 验证通过[` 确认下游只按方法名前缀判定
  （`_agent_checks` 的 `关键词匹配/语义断言` 白名单），无消费者解析后缀。
- 实证（2026-10-27 20:17→21:30 清空态全链路）：`executions.actual` 已为**页面事实**——
  TC-REQ-010/020/065-I01 记 `验证通过[错误条检测]: 页面反馈拒绝/校验错误:
  .field-error: 价格必须是数字；.field-error: 库存必须是整数`，需求期望句子已不再冒充证据；
  四条均 PASS。

**M. 单次页面探测的上界口径统一（2026-10-27，缺口②续）**

- 缺陷：`base.py::_check_preconditions` 的探测上界写成
  `max(1.0, engine._timeout_ms/1000)`——执行器全局超时可达 **600s**，而用例级硬超时是
  300s。于是「探测」永远先被外层用例超时杀掉：证据丢失、用例记 ERROR，
  **声明上的上界等于没有上界**（H 段事故的同一形态，只是换了位置）。
- 修复（单点，不新增旋钮）：上界取 `page_ready.READY_CAP_MS`（同一「单次页面交互预算」
  的既有唯一来源，3s）与执行器超时的较小者：
  `_probe_to = min(READY_CAP_MS/1000, max(1.0, engine._timeout_ms/1000))`。
  语义：探测拿不到答案（执行上下文不可用）时，在前置检查阶段就按「前置条件不成立」收口，
  把证据留在本用例内，而不是交给外层硬超时。
- 实证：`compile_ok`；`pytest` 四组 **431 passed**；全仓仅此一处使用
  `_timeout_ms/1000` 作为等待上界（`grep` 已确认无第二处副本）。
- 等价性：健康页 `count()` 为毫秒级返回，此改动只在「执行上下文不可用」分支生效，
  分布预期不变（与 L 段合并由下一轮清空态跑一次性认证）。

**N. 有界 DOM 查询原语收归一处 + 侦察/菜单路径全量接入（2026-10-27，缺口②收口）**

- 结构缺陷：J 段把 `count_bounded/content_bounded` 放在 `session_login.py`，命中「能力按业务
  模块摆放」的坏形状——侦察/菜单路径要用同一能力时只能**再写一份**或跨业务模块 import。
  真实后果已实测：`element_utils.get_page_snapshot / page_reconnaissance`、
  `explorer_mixins/_element.py::_expand_and_capture_full_menu`（阶段2 hover/2b/2c/2d/2e）、
  `_sidebar.py` 子菜单扫描、`_exploratory.py::_verify_module_in_page / _verify_page_content_loaded`
  共 **60+ 处** `count()/evaluate()/text_content()/title()` 无界；其中预检（preflight）与
  侧边栏/菜单捕获发生在**用例之前**，与 H/J 段同一失效面（挂死＝整轮静默）。
- 修复（单一实现，零新增旋钮）：
  - `src/core/page_ready.py` 新增四个原语：`await_bounded(coro, timeout, default, what)` 是
    **唯一的 `asyncio.wait_for` 内核**；`count_bounded`（target 可为 Page+selector 或 Locator，
    超时 `-1`/不存在 `0`）、`evaluate_bounded`（超时 None）、`content_bounded`。
    两档预算复用既有量级：`QUERY_TIMEOUT_SEC=READY_CAP_MS/1000=3s`（单次交互）、
    `PROBE_TIMEOUT_SEC=10s`（串行诊断/登录探测，与登录逐动作上界一致）。
  - `session_login.py` 改为 `from src.core.page_ready import count_bounded, content_bounded`，
    **删除本模块内的两份实现**；`ai/login.py`、`browser_ai_login.py`（2 处）、
    `capabilities/login.py` 同步改从 `core/page_ready` 导入（不再经业务模块转出）。
  - 接入点位（全部经上述原语，无例外）：`element_utils.py`（快照×2、侦察滚动/展开、
    tabs/页内导航/文本/弹窗/提示、`click_button` 与 `try_pw_locate_button` 的 count/visible/
    text/attribute/evaluate、`repair_click`）；`_element.py`（全菜单捕获 5 阶段 + JS 提取 +
    fallback + 侧边栏项）；`_sidebar.py`；`_exploratory.py`（模块验证、内容加载、
    删除行数比对、错误条检测、预检触发器扫描）。
  - 侦察早停：`page_reconnaissance` 首个求值即不可用时**放弃滚动/展开直接取快照**，
    避免十余次求值逐个等满上界。
- 实证：`compileall` 全通过；`pytest tests/test_core tests/test_perf tests/test_step4
  tests/test_step5` = **431 passed**（改动前后各一次）；`grep` 确认 `session_login` 内已无
  第二份 `wait_for` 实现。
- 等价性：健康页这些调用均为毫秒级；改动只在「执行上下文不可用」分支改变行为
  （原先无限等待 → 现在有界返回 fail-open）。分布预期不变，由本轮清空态全链路一次性认证。
- **清空态全链路实证（2026-10-27 20:17:58→21:30:29，Step4 20:19:42→21:29:58 / 4,216s）**：
  purge 后重启引擎加载新代码，五步 `RUN_END status=completed`。executions
  **PASS 369 / FAIL 22 / SKIP 97 / PRE_GATE 3**、决策口径 **94.4%（369/391）**、
  weak 63 / no_assert 4、`test_cases 488 / case_meta 488`、check top 十项
  （`no_error 242 / element_exists 138 / semantic_check 98 / performance_lcp_check 68 /
  list_row_count_check 66 / viewport_overflow_check 50 / loading_complete 49 /
  security_probe_check 43 / text_contains_check 24 / field_error_visible_check 15`）
  与基线**逐项相同**；FAIL 22 集合与归因不变。
  健康信号：引擎日志 `PageProbe` 告警 **0**、Traceback **0**、20:21 后 ERROR **0**；
  `execution_screenshots 1070`、`oracle_results 960`。
  Step5 产物 `html_report 247,775 / report 152,761 / coverage_report 4,819`，
  已导出 `e2e-test-report-2026-10-27/{step5_report.md, step5_report.html, coverage_report.md}`
  （21:30:29 版本）。
- 诚实缺口：`explorer_mixins/_llm.py`、`_action.py` 内仍有数十处 Playwright 直调未接入——
  它们都发生在**用例级 `wait_for` 之内**（超时记账为单条用例 ERROR，不会静默整轮），
  故不属于本次「用例前挂死」失效面；若未来出现用例内挂死证据，按同一原语接入，不另起实现。

### §4.12 弱断言收敛 + 反馈面适用性（2026-10-27/28，清空态全链路两轮实证）

**A. 断言族⑤：容错/析取期望 → `any_of_check`（生成端，唯一构造器）**
- 缺陷：异常/边界模板的期望天然是析取式（"显示错误提示**或**确认拦截"、"系统不崩溃，
  显示无结果或提示"）。族② 因主动收窄单一极性而跳过、族③ 因无接受语义跳过、族④ 的判定
  对象不是列表行 ⇒ 用例只剩 `element_exists(页面主体) + no_fatal_error`（只证明"页面能打开"），
  而检索/筛选/导入/删除点击等真实激励已执行、后果无人观察。
- 修复：族⑤ 用 `any_of_check` 承载"交互后至少出现一个合法结果面"（错误反馈条 ∨
  成功反馈条 ∨ 结果承载面），三支全不成立才 FAIL（可证伪）。分支集由期望自身极性派生，
  **唯一构造器** `_check_guard.outcome_surface_check(expected)`，两条生成通道共用：
  族⑤ 与 `_align_feedback_assertions` 的 semantic→any_of 改写；不得再各写一份分支表。
- 极性判据仍只有 `_rule_extractor_keywords` 一个事实源，新增 `has_tolerant_outcome_marker`
  （是否出现"非错误极"的结果语汇：正确处理/合理结果/正常响应…），与 `is_tolerant_outcome`
  分工明确：前者回答"能否挂成功/结果承载面分支"，后者回答"是否为容错型期望（含析取）"。
  纯错误/确认的析取**不得**挂结果承载面，否则"页面只要还开着"就满足"应被拒绝"（退化假 PASS）。
- 边界（二次根因）：族⑤ 判据必须是 `is_tolerant_outcome`，不能用"既非拒绝也非接受"的补集——
  否则布局前置条件（"表单容器已渲染/必填标记可见"）与防御探针（"系统无报错"）也会被改写，
  而它们在本表单/详情页上三支必然全不成立 → 结构性假 FAIL。

**B. `_expect_error_bar` 判据改为极性 SSOT（`case_rules/base.py`）**
- 缺陷：原判据是"能否组装出录入动作"。检索/筛选/导入补出交互后，**容错型**期望
  （"系统正常响应/显示合理结果"）也被要求"必须出现错误提示" → 正常页面上恒无错误条 →
  结构性假 SKIP/FAIL（实测边界值 TC-REQ-001/013/023/026/027/028/059/060-I03 由 PASS 退化）。
- 修复：`_expects_error(expected) and not _is_disj(expected) and bool(_data_actions)`
  —— 期望极性只有 `_rule_extractor_keywords` 一个事实源，本处不得自造判据。

**C. 反馈面适用性：`feedback_scope`（本轮核心，动作形态 SSOT 派生）**
- 缺陷：`field_error_visible_check` 的 N/A 判据是"页面是否存在可编辑字段"。该判据回答的是
  **页面能力**，而删除/导入/分类拦截等用例的期望反馈是**操作后果**——两者被同一判据回答 ⇒
  这些流程的终态页就是列表页（恒无字段）→ 断言一律 N/A → 用例**假 SKIP**，需求从未被判定
  （实测 TC-REQ-039-E01/I01、041-E01/I01、063-I02）。
- 修复（单点、幂等）：`_trigger_actions.field_rejection_scope(actions)` 按**最终动作链**
  判定 `field`（含字段录入动作 → 字段级校验反馈面）/ `operation`（仅操作类交互 →
  页面级反馈面），`_check_guard.declare_feedback_scope(case)` 在落库边界写入
  `check.feedback_scope`；执行器 `_db_ui._handle_field_error_visible` 只读：
  `operation` + 无命中 ⇒ **FAIL**（需求要求的反馈未出现，可证伪），`field` + 无命中 +
  页面无可编辑字段 ⇒ 原 N/A（诚实跳过）。
  判据全部来自动作类型（跨系统/行业/页面通用），零业务硬编码；`feedback_scope` 缺省为
  `field`（= 原语义），不影响任何未标注的用例。
- 常量去重：`OBSERVE_ONLY_ACTION_TYPES` 归位 `_trigger_actions` 唯一（此前 `_check_guard`
  另有一份 `_EXPLORE_ONLY_ACTION_TYPES`，改为 import）。

**D. 回归与实证**
- `compileall` 通过；`pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5`
  = **431 passed**（27.65s）。
- 第十轮清空态全链路（purge → 重启引擎 → Step1→5，2026-10-28 01:50:39→03:05:09；
  Step3 01:50:48→01:51:13，Step4 01:52:12→03:05:01 ≈4,370s，Step5 03:05:09）：
  五步 `RUN_END status=completed`；`test_cases 488 / case_meta 488`。
- 分布：**PASS 364 / FAIL 31 / SKIP 93 / PRE_GATE 3**，决策口径 **92.2%（364/395）**；
  静态弱断言 **36**（较 §4.11 的 63 降 27）/ 无断言 4（`010/011/020/065-I01` 既存）；
  check top 十项与基线逐项一致（`no_error 242 / element_exists 127 / semantic_check 98 /
  performance_lcp_check 68 / list_row_count_check 66 / viewport_overflow_check 50 /
  loading_complete 49 / security_probe_check 43 / text_contains_check 24 / any_of_check 18`）。
- 逐用例 diff（对 §4.12 前一轮）：**仅 5 条变化，全部 SKIP→FAIL**——
  `TC-REQ-039-E01/I01`、`TC-REQ-041-E01/I01`（`feedback_scope=operation`，证据：
  "未发现可见错误/拒绝反馈（用例已施加可被拒绝的操作…扫描34个选择器）"）、
  `TC-REQ-063-I02`（同一证据 + 同用例 `element_exists(错误条)` ✗）。
  **PASS 集合零变化（364 条逐条相同），无 PASS→FAIL、无 PASS→SKIP 退化**。
- Step5 产物：`html_report 254,675 / report 158,598 / coverage_report 4,819`，
  已导出 `e2e-test-report-2026-10-27/{step5_report.md, step5_report.html, coverage_report.md}`
  （03:05:09 版本）。

**E. 诚实缺口（如实记录，未强行消除）**
- `039/041/063` 的 FAIL 是**断言级诚实**（需求要求的反馈未出现），但底层还有一层
  **场景构造缺口**：`删除不存在的商品` 无法经由 UI 造出"不存在的 id"（用例只能点列表行的删除）；
  `分类管理` 模块在靶系统中不存在；`盘点单批量导入` 无导入能力。三者均为产品/需求实现缺口，
  非断言误判；若要 PASS，必须先在生成端构造可实达的前置（API 直调或数据预置），属后续专项。
- `weak_only 36` / `no_assert 4` 未继续收敛：剩下的都是"只读观察面 + 防御探针"型用例，
  其需求本身只声明"页面正常渲染/不报错"，强行升级会臆造需求义务（比弱断言更糟）。
- `feedback_scope` 目前只作用于 `field_error_visible_check`；`semantic_check` 的拒绝极性
  通道仍由执行器自身的 DOM 反馈条判定承载（本轮未改，避免同语义两处判定）。

### §4.13 两处判定根因修复（2026-10-28，清空态实证）

**A. 无断言不得记 PASS（Agent 路径与结构化路径同源）**
- 缺陷：4 条 `checks` 为空的用例被引导层通用探针（页面已加载/任意错误条）判 PASS
  （conf 0.88~0.93），探针不承载需求义务 → 结构性假绿。根因在 `_agent_checks`
  `if _passed and not _skipped and intent.get("checks"):`——checks 为空时探索后的
  断言验证被整体跳过，`_passed=True` 直接沿用为 PASS。
- 修复：判据单点收口 `_assertion_verdict.declares_executable_assertion(case)`
  （结构化路径 `_base._execute_one_intent` 与 Agent 路径同源调用，禁止两处各自拼装）。
  空 `checks` ⇒ **SKIP / ENGINE_COVERAGE_GAP**：不记 PASS（防假绿），也不记 FAIL（防假 Bug）。
- 实证：`TC-REQ-010-I01 / 011-I01 / 020-I01 / 065-I01` 定向重跑全部 PASS→SKIP，
  `failure_reason` 精确为"用例未声明任何可执行断言（checks 为空）…"。

**B. 页面装载期 JS 报错不归因到用例（浏览器实现差异导致的放大）**
- 现象：清空态全量跑 30 分钟内 115 条里 109 条 FAIL，reason 统一为
  "页面存在 5 条 JS 报错(console.error/pageerror): [console.error] Executing inline
  event handler violates … Content Security Policy"。
- 取证：最小 Playwright 探针（同靶子、同代码）——内置 chromium 加载 `/` 报 **0** 条；
  系统 Chrome 156.0.8078.48 / Edge 156.0.4314.42 各报 **6** 条（5 CSP + 1 资源 404）。
  时序探针：这 6 条在 `framenavigated` 之后、`domcontentloaded` 之前产生；真实点击删除后
  再 +5 条（交互期增量）。即同一份页面缺陷，不同浏览器实现**上报时机不同**：有的只在
  真正执行内联处理器时报 1 条，有的在解析完成时就把页面上每个内联处理器各报一条。
- 根因：GAP-01 把"页面装载期的存量事实"与"用例动作引发的增量事实"混为一谈 ⇒ 一份页面
  缺陷被放大成 N 份用例假 Bug（用例自身需求如 LCP 32ms 实际已通过却判 FAIL）。
- 修复（单点、零业务词表、无环境逃生口）：
  - 新模块 `playwright_engine/js_load_phase.py` 是唯一判据源：装载期 = 主框架
    `framenavigated` → `load` + `LOAD_QUIET_SEC`(0.1s)；软路由无 `load` 时以
    `NAV_PHASE_CAP_SEC`(2s) 为硬上界。
  - 生产者打标：`lifecycle._on_console/_on_pageerror` 与
    `_worker_errors.make_worker_handlers`（含并行 worker 自己的 tracker）给每条报错写
    `page_load` 标记。
  - 消费者只读：`_base` GAP-01 仅统计 `is_case_attributable(entry)` 为真的条目，
    证据文案改为"用例执行期间新增 N 条 JS 报错"。
- 实证：`TC-REQ-012-I01` FAIL→PASS（10 条装载期报错被排除）；`TC-REQ-007-I03` /
  `TC-REQ-037-E01` 复跑 PASS（15 条全为页面装载期，点击后无新增增量）。
- 与基线的关系：上一轮 6 条 CSP FAIL（`007-I03 / 037-E01 / 038-I03 / 039-I03 / 041-I03 /
  061-I03`）是**同一份页面缺陷按用例重复背书**；本轮其中 5 条转 PASS，`039-I03` 因自有
  断言失败仍 FAIL。这不是"放宽判定"，而是把页面级事实从用例判决里摘出去。
- 诚实缺口：装载期报错不再让用例 FAIL ⇒ 页面在装载期抛错时不再产生用例级 FAIL
  （"防放大"与"防假绿"不可兼得）。该类事实仍完整保留在引擎
  `[GAP-01] … 页面装载期存在 N 条 JS 报错(页面级事实，不计入用例)` 日志中，并在逐用例
  `details.evidence.page_level_console`（`total` + 去重签名 `signatures`，见 E 段体积契约）
  留可诊断摘要；若要单列页面级缺陷清单，需在 Step5 报告增设"页面级问题"通道（本轮未做，如实记录）。

**C. 回归**：`compileall` 通过；`pytest tests/test_core tests/test_perf tests/test_step4
tests/test_step5` = **431 passed**（30.95s）。

**D. 本轮清空态全链路结果（2026-10-28 10:03:25 → 11:23:18，确定性复现）**
- 前置：`executions`/`run_logs` 物理清空（-489 / -588），引擎 PID 5140（09:52:20）、
  SUT smartshop PID 21236（10:01:37，:8020 健康检查 200）。
- **分布**：`PASS 366 / FAIL 25 / SKIP 97 / PRE_GATE 2` = 490 行；`test_cases` 488 条，
  未落库 **0**；多出 2 行是 run 级门禁 `pre_gate_step4 / pre_gate_step5`。
  与上一轮（09:36:59）终值完全一致 = 确定性复现。
- **决策口径通过率 = 366/391 = 93.6%**；报告层达成率 366/488 = 75.0%（SKIP 计入分母）。
- **Step4 耗时**：引擎 `run_logs` 记（`Step 4 执行完成 | 耗时: 4871.4 秒`）；验收脚本墙钟口径 4898s。
- **证据体积契约实证**：`details` max **17904** / avg 5394 字符（修复前单例 30629），
  490 行中 **479 行**含 `page_level_console` 摘要（归属切分真正生效）。
- **断言强度**：`weak_only=36`、`no_assert=4`（= SKIP 4 条来源）；check 分布
  `no_error 242 / element_exists 127 / semantic_check 98 / performance_lcp_check 68 /
  list_row_count_check 66 / viewport_overflow_check 50 / loading_complete 49 /
  security_probe_check 43 / text_contains_check 24 / any_of_check 18 …`。
- **FAIL 25 全部 `PRODUCT_BUG`（0 引擎缺陷）**，按根因归并：
  - REST 端点未实现 → 404 **5**：`REQ-054/055`（`/api/products`）、`REQ-056/057`
    （`/api/products/{id}`）、`REQ-058`（`/api/categories`）；
  - CSP 拦截内联 `onsubmit=confirm` → 确认/删除类无弹窗 **5**：
    `REQ-037-I01 / 068-E01 / 068-I01`（`any_of_check` 全分支不成立）＋`REQ-061-E01/I01`
    的 `list_row_count_check Δ=0`（删除未生效，行数差分坐实）；
  - 导入/导出/图片上传未实现 **4**：`REQ-019-I03 / 021-I02 / 062-I02 / 067-I03`；
  - 字段约束缺失 **2**：`REQ-029-I01`（名称无 `maxlength=50`）、`REQ-044-I01`
    （页面无"入库数量"字段，`field_constraint_check` 定位不到控件）；
  - 拒绝反馈缺失 **5**：`REQ-039-E01/I01`、`REQ-041-E01/I01`（`field_error_visible_check`
    扫描 34 个选择器无可见错误反馈）＋`REQ-063-I02`；
  - 文本未出现 **1**：`REQ-069-E01`（`text_contains` 0 命中）；
  - 可达性/文案 **3**：`TC-PAGE-ROOT-UX01`（`semantic_check` 机械文本未命中）、
    `REQ-039-I02 / 041-I02`（`element_exists` 命中但 `any_of_check` 另一分支 0 命中）。
- **Step5 产物（纠正一处认知错误）**：`/api/step/4` 未带 `end_step` 时 `end_step=4`，
  调度器按 `step_num > end_step → skipped`（`scheduler.py:259-266`）**不会自动编排 Step5**；
  09:36:59 的产物来自旧脚本显式 `/api/step/5`，并非自动。本轮按契约显式触发：
  `report 156189 字符` / `html_report 252666 字符`，`updated_at=11:29:56`（`created_at`
  仍为 03:05:09 是 upsert 保留首写时间，非未更新）；`coverage_report`（4819）由 **Step3**
  产出，本轮未重生成 Step3（用例集未变），`updated_at=01:51:13` 属正常。
- **交付物**：`e2e-test-report-2026-10-28/step5_report.{md,html}` + `coverage_report.md`
  （由 `temp/_export_report.py` 从 MySQL `generated_reports` 原样导出，不重写模板）。
- **三源对账（可复算）**：
  - `run_summary`（id=35，`updated_at=11:29:56`）：`total 488 / passed 366 / failed 25 /
    skipped 97`、`pass_rate 0.75`、`avg_confidence 0.8592`、`evidence_calibration 0.6381`、
    `evidence_coverage 1.0`、`total_duration_sec 4898`；`self_eval.accuracy=0.9361`
    （= 366/391）与脚本口径一致。
  - `generated_reports.report` 正文含 `| 通过 | 366 |`×2 / `| 失败 | 25 |` / `| 跳过 | 97 |`，
    与 `executions` 分布逐项一致。
  - 证据完整度 `no_exp=0 no_act=0 no_det=0`；本轮 `run_logs` **26188 行全 INFO，0 ERROR/WARNING**
    → 无 guard 吞异常、无「假完成」路径。
  - FAIL 复现证据：**20/25** 条有本轮截图（`execution_screenshots`，本轮 4829 条留存）；
    缺的 5 条正是 API-only 404 用例（`REQ-054~058`，无 UI 交互，截图不适用），
    `details` 内含 API 请求/响应与逐 check 明细。
- **诚实缺口（下一轮 Step3 根因项）**：`REQ-039-E01/I01`、`REQ-041-E01/I01`、
  `REQ-069-E01` 共 5 条属**生成端建模缺陷**而非 SUT 缺陷——动作从未构造
  "删除不存在的商品"/"分类下有商品"的前置，断言声明"手机号出现在列表"却无短信验证码流程；
  需在 Step3 的动作/断言合成端修复后重生成并全量复跑，本轮时间窗不足，如实记录。

**E. 结果落库整行丢失（1406 击穿 + guard 吞异常，2026-10-28）**
- 现象：清空态全量跑 `RUN_END status=completed`、引擎日志打印 `Step 483/483`，
  但 `executions` 只有 **371 行 / 488 用例**（未落库 118 条，恰好是 E01/I01/UX02/PF02
  Agent 探索族）；分布随之失真（SKIP 6 而非 97），Step5 无法按 run 复算。
- 直接原因：引擎日志（`guard` 吞异常位）**122 条**
  `_execute_one_intent: from src.core.config_loader import g 失败(可容忍):
  (1406, "Data too long for column 'details' at row 1")` —— `details` 是
  TEXT(65535 字节)，结果写出超限。
- 根因（4 层）：① 证据写入无体积契约，C5 接线把用例结束时缓冲里的 console/network
  条目**原样**拼进 `details`；② 页面装载期报错（页面级事实）被逐用例各存一份，体积
  随用例数线性放大（单例可达 160+ 条 CSP 报错）；③ 从契约上看，`details` 的列容量
  （64KB）与被测系统日志量无关，属"容量假设"而非契约；④ **最致命**——
  `record_execution` 及其后置处理整段写在 `with guard(...)`（吞掉一切 Exception）内，
  写入异常被当成"可容忍"吞掉，主循环照常打印"结果"→ **静默丢行 = 假完成**。
- 修复（三处，均无业务词表 / 无环境逃生口）：
  1. 新模块 `single_mixins/_evidence_budget.py` 是逐用例证据落库的唯一入口：
     归属切分复用 `js_load_phase.is_case_attributable`（与 GAP-01 同源）——页面级
     事实压成 `page_level_console{total,signatures}`；可归因条目按类型限额
     （console 40 / network 60 / 单条 300 字符）保留，超限记 `*_dropped` 条数。
  2. `schema.sql`：`executions.expected/actual/details` TEXT → **MEDIUMTEXT**，
     并附 `ALTER … MODIFY` 迁移；`db_schema._alter_already_applied` 新增 MODIFY
     幂等判据（比对 `information_schema.DATA_TYPE`），避免每次启动重建表。
  3. 结构修复：把 `execution_id = record_execution(...)` 起至该 guard 块末尾整体
     移出 best-effort 段落（回归"分类可容忍、落库不可容忍"的职责边界）。此后落库
     失败会**冒泡为显式失败**，不再有静默丢行。
- 回归：新增 `tests/test_step4/test_evidence_budget.py`（5 例：归属切分/限额/截断/
  混合缓冲）；`pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5`
  = **436 passed**。
- 实证（本轮）：落库行数与用例步数 1:1（`t=150s` 用例步 9 / 已落库 14，其中含
  1 PRE_GATE + 5 run 级横切行）；全量终值见 D 段。

**F. 证据源与判定源同标（双缓冲归属标记缺失，2026-10-28 终局审计）**
- 现象：E 段修复后落库不再丢行、`details` 体积达标，但 490 行中
  `page_level_console` 命中 **0**（本轮首轮审计实测），而 GAP-01 日志却报"页面装载期
  存在 N 条 JS 报错(页面级事实)" —— 同一批报错，**判定侧认页面级、证据侧认用例级**。
- 根因：`lifecycle._on_console` 有两条缓冲——`_console_errors`（GAP-01 判定源）与
  `_console_logs`（C5 证据落库的输入源）。此前只给前者写 `PAGE_LOAD_KEY`，
  后者无标记 ⇒ `pack_case_evidence` 无法按 `js_load_phase.is_case_attributable` 切分，
  页面缺陷只能整体塞进 `console_logs`（体积击穿 + 证据口径与判定口径分叉）。
- 修复（单点判据、零业务词表）：
  1. `_on_console` 在**两个缓冲同时**写 `PAGE_LOAD_KEY`（同一次 `_in_load_phase()` 取值），
     生产者契约由测试锁定 `test_console_marker_present_in_both_buffers`。
  2. `_evidence_budget._entry_type_text` 增加 `level` 回退：全量缓冲的条目键是 `level`，
     error 缓冲是 `type`，网络缓冲是 `event` —— 类型键按**生产者契约**回退，
     否则签名摘要显示 `"?"`（`test_page_level_signature_uses_level_key_of_full_log_buffer`）。
- 实证（本轮全量 run）：`details` max 从 **30629 → 17904**、avg 5394；
  490 行中 **479 行**命中 `page_level_console`（首轮 0）；`console_logs` 不再承载装载期报错。
- 已知展示残留（无需改代码）：本轮 DB 的 `signatures[].type` 仍为 `"?"`（如 `REQ-037-I01`），
  因为 `_evidence_budget.py` 于 **10:14:36** 修改而引擎进程 **09:52:20** 启动、run 10:03 起跑，
  Python 已把旧模块载入内存 → 属**未热载**，非缺陷；当前源码 + 单测（
  `test_page_level_signature_uses_level_key_of_full_log_buffer`）返回 `"error"`，下次重启引擎即可见。
- 回归：`tests/test_step4/test_evidence_budget.py` 扩到 **9 例全绿**；
  `pytest tests/test_core tests/test_perf tests/test_step4 tests/test_step5` = **440 passed**（66.89s）。

**G. 本轮结论边界（不得当作"引擎无缺陷"）**
- 研发侧：Step4 判定/落库/证据三处根因已修且有可复算实证；25 条 FAIL 全为 SUT 缺陷。
- 未闭环：生成端 5 条假 FAIL（D 段末条）＋ `SKIP 97` 中 93 条"非判定态"仍属
  生成端未建立机械映射（`semantic_check` 无法在运行期 AI 关闭时验证）。
  二者都需 **Step3 重生成 + 全量复跑** 才能收敛，不在本轮时间窗内，如实挂账。

### §4.14 用例审核页置信度分析展示修复（2026-10-28，实测定位）

- **现象**：`/intent-review` 右侧「置信度分析」5 个维度（生成/执行/证据/覆盖率/完整度）
  有数值但无进度条；勾选单条用例后详情与置信度不联动（"勾选无反应"）。
- **根因 1（CSS 契约缺失）**：`renderConfidence` 生产者用 `<span class="md-dim-bar-fill">`，
  CSS 只定义 `height:100%` 未定义 `display` → span 默认 inline，width/height 全部失效。
  实测 `getBoundingClientRect()` = **0×0**（内联 `width:86%` 存在但不参与布局）；
  对照 `#confidenceBar` 用 `div` → 206×8 正常。
  修复：`.md-dim-bar / .md-dim-bar-fill` 显式 `display:block`（不依赖父级是否 flex，
  生产者用 span 或 div 都成立）。
- **根因 2（激活行为被打断）**：勾选框 `click` 冒泡到行 → `selectCase` → `renderList`
  在浏览器执行勾选框激活行为（翻转 `checked` + 派发 `change`）**之前**把 `input` 从 DOM
  移除；未连接的 input 不做激活 → `change` 不触发、`checked` 回滚、已选计数停在 0。
  实测：点第 2 条勾选框后详情切到 `TC-PAGE-ROOT-UX01`（行点击副作用）但「已选」仍 0。
  修复：勾选框及其 `.ir-cb-wrap` 阻断 `click` 冒泡，`change` 内更新选中集后再
  `selectCase(caseId)` 联动右栏。
- **实证**：修复后 5 条填充 rect 宽 **351/233/262/135/339 px × 6px**、`display:block`；
  勾选后 `detail=TC-PAGE-ROOT-UX01`、`checked=true`、`已选 1 条`、分数 97% 且 5 维同步刷新。
- **回归锁**：`tests/test_frontend/test_intent_review_confidence.py`（Playwright 真实渲染，
  2 例）—— 维度条 rect 宽高 > 0；单条勾选 → 详情切换 + 勾选保持 + 计数=1 + 分数渲染。

### §4.15 指令治理面板分类筛选条压缩修复（2026-10-28，实测定位）

- **现象**：`/action-lib`（指标区 → 指令治理面板）「全部分类」下筛选按钮被压缩，
  计数/文案越长的越靠右、越明显。
- **根因（布局契约）**：`.al-cat-tabs` 为**单行 flex**（`flex-wrap:nowrap`）+
  `overflow-x:auto` + 右缘 mask 渐隐。实测 1440px：容器 **1144** / 内容 **1663** →
  后半排按钮（`断言校验 43`、`高级交互 11`、`导入导出工作流 1` 等）被裁在可视区外；
  按钮自身并未 flex 收缩（`min-width:auto` 生效，rect == scrollWidth），
  用户观感即「被压缩」。1024px 下只能看到 4 个。
- **修复（与二级筛选条同一契约，零业务词表）**：
  1. `.al-cat-tabs` 改 `flex-wrap: wrap`，移除 `overflow-x`/scrollbar/mask 渐隐；
  2. `.al-cat-tab` / `.al-sub-tab` 增加 `flex: 0 0 auto`（按内容取宽，永不被压扁）；
  3. 删除已成死路径的 `updateCatTabsFade()` 及其 resize 绑定、`.al-overflow` 规则。
- **实证**：1440px → **2 行 / 12 按钮 / 裁切 0**（条高 87）；1024px → **3 行 / 裁切 0**；
  点击「断言」后二级筛选条 7 项正常、列表筛出 43 条。
- **回归锁**：`tests/test_frontend/test_action_lib_filter_bar.py`（Playwright，2 例）——
  1024px 下必须多行、无裁切；最长按钮 `rect.width >= scrollWidth`。
  同批回归 `test_design_tokens.py + test_ui_regression.py` = **93 passed**。

### §4.16 算法规则清单卡片自适应铺满（2026-10-28，实测定位）

- **现象**：`/skill-rules`（指标区 → 算法规则清单）仅 5 张卡，1440px 下显得逼仄、
  右下留大片空白。
- **根因（布局契约）**：`.pc-rules-grid` 用 CSS 多列 `columns: 300px`。实测 1440px：
  容器 1144 → **3 列**（每列 375px），5 张卡按 **2/2/1** 分布，右下空出一整块
  （填充率 **0.809**）；1280px 同样 3 列（321px），填充率 0.805。1920px 才是 5 列。
  多列布局不能按行拉伸，卡片数不整除列数时空格无法避免（上一版 09-21 修复只换了
  实现方式，未解决"末行不足"）。
- **修复**：改 flex 行内等分 —— `display:flex; flex-wrap:wrap; align-items:stretch;
  gap:space-3`；卡片 `flex: 1 1 300px; min-width: 0`。行内剩余宽度由卡片拉伸补齐，
  末行不再留空格；列数随宽度自适应（1920→5 列、1440/1280→3+2、1024→2+2+1、900→单列）。
- **实证**：1440px → 第一行 3×375，第二行 2×**567**（铺满），填充率 **0.974**；
  1280px → 321 / 487，填充率 0.971；1920px → 5×317 单行 0.975；无横向溢出。
- **回归锁**：`tests/test_frontend/test_skill_rules_layout.py`（Playwright，2 例）——
  每行"卡片宽之和 + 间距 == 网格宽"（±2px）、卡片最小宽 ≥ 300px。
  同批回归 `test_design_tokens.py + test_ui_regression.py` = **93 passed**。

#### 纵向铺满（同日第二轮，用户反馈"仍逼仄、下方大片空白"）

- **现象**：横向已铺满，但每张卡内 `.pc-rule-list` 固定 `max-height: 360px`，
  5 张卡总高仅约 400px，1440×1000 下内容区（953px）底部空出约 **480px**；
  Step4 有 55 条规则（内容高 **4575px**）却只露出约 8 条。
- **根因（CSS 契约）**：flex 换行容器的行高由 **item 的 max-content 贡献**决定，
  `align-content: stretch` 只会"补足空余、不会压缩超高内容"。因此
  ① 只把列表改 `flex: 1 1 auto` → 行高被 Step4 的 4575px 撑到 **4624px**（整页失控）；
  ② 改 `flex: 1 1 0` 也不行 —— 卡片自身高度是 auto，其 max-content 贡献仍按内容算。
- **修复（两条同时生效，缺一不可）**：
  1. 卡片加 `contain: size; overflow: hidden` —— 卡片的**固有尺寸视为 0**，内容不再参与
     行高计算 → 行高由 `.pc-rules-grid` 的 `align-content: stretch` 按行等分剩余空间
     （1440 两行各 437px、1920 单行 884px）。
  2. `.pc-rules-grid { flex: 1 0 auto }` —— grow 保证大屏纵向填满；shrink:0 保证窄屏
     多行时网格不被压扁（否则 1024×800 的 3 行会被挤成小窗）。
     列表 `.pc-rule-list { flex: 1 1 0; min-height: 0; max-height: none; overflow-y: auto }`。
- **实证**（`.content-body` 953/853/753 三个视口）：1920×1000 → 5×884 单行、列表可见 834；
  1440×1000 → 437×5、列表 387；1280×900 → 387×5、列表 337；1024×800 → 3 行×213、列表 164；
  四者 **填充率 0.96~0.98、底部空白 0、无整页纵向溢出**。Step4 列表内容 4575 → 8369px 全部卡内滚动。
- **回归锁**：同文件新增 `test_cards_fill_vertical_space`（底部空白 ≤2px、卡片高 ≤ 网格高、
  列表可见高 > 360px、内容区不出现纵向滚动）= **3 passed**。

### §4.17 用例审核首步「导航到 /」根因修复（2026-10-28，实测 + 端到端实证）

用户现象：`/intent-review` 用例审核页「操作步骤（含逐步预期）」第一步**全部**显示
「导航到 /」。诊断结论是**三处独立缺陷叠加**，按方案 A + B 一并修复（禁止补丁式改动）。

- **实证（修复前）**：555 条用例首步 `url=<none> | hint=/` 550 条、`/api/products` 5 条，
  无一条带真实 url；`nav_graph_nodes` 全库 **0 行**；执行日志
  `[Structured] 导航URL拼接: http://127.0.0.1:8020/` 约 **488 次**（停在首页后靠 AI 猜按钮）。
- **根因链**：
  1. **生成端兜底**：多处 `target_hint: nav_target or "/"` —— 无导航事实时把站点根
     **伪装**成目标页（`_fallback_cases.py` 9 处、`case_rules/base.py`、`_rule_extractor_actions.py`）。
  2. **解析端静默跳过**：`resolve_intents_batch` 在 nav_graph 为空时整体 `return`，
     占位目标原样落库；且它只在 Step3 建模出口调用，导入/自愈回填/重试脚本可绕过。
  3. **导航图恒空（最上游）**：预检只遍历「侧边栏菜单项」，见 `not _all_menu_texts`
     直接 `return 0`；smartshop 是平铺路由（5 个路由、无侧边栏）→ 菜单 0 项 → 永不落库。
- **方案 A：导航发现通用化**（`browser_core/single_mixins/_exploratory.py`）
  - 删除菜单为 0 的早退，菜单遍历后追加**同源链接 BFS**（`_discover_pages_by_links`）；
    两种来源共用 `_page_elements_from_snapshot` / `_write_nav_nodes`（唯一写库实现）/
    `_visited` / 120s 时间预算，产出同形三元组。
  - 去重按**路由模板**（`_path_identity`：实体 id 段归一 `{id}`）—— 否则列表页上的
    `/products/940..944/edit` 会把 10 个页面上限占满（实测 8 页里 5 页是同一模板）。
  - 判据 SSOT：认证页 `_is_auth_page_url`（链接发现不遍历 + 落库不写入共用）；
    页面身份 `_page_identity`（语义链接文案 > document.title > 路径；翻页/查看类泛化
    文案不作身份 —— 实测修前 `/products` 被记成「下一页」）。
  - **实证**：smartshop 实跑 `discovered=8 / 4.6s`（含 5 个实体页）→ 归一去重后
    **4 个页面类型 / 2.3s**；预检实跑 `preflight_written=4`，`nav_graph_nodes`
    由 0 → 4 行（`/`、`/products`、`/products/new`、`/products/{id}/edit`，6.6s）。
    注意 FK：`nav_graph_nodes(project,version_label) → project_versions`，version 必须传
    激活版本（空串会 1452 外键失败且被 guard 吞掉 → 静默 0 行）。
- **方案 B：不再伪造 `/`，解析不到即如实**
  - 生成端 `_navigation_resolver`：新增 `is_placeholder_nav`（**唯一判据**：navigate +
    无 url + target ∈ {"", "/", "/login"}）、`mark_nav_unresolved`（目标改写为
    module 语义页面名、去 url、打 `nav_unresolved` + reason，用例级与步骤级同步）、
    `clear_nav_unresolved`（真实事实解析成功时清除，避免"谎报未解析"）。
    `resolve_intents_batch` 无导航图时**逐条标注**（reason=`nav_graph_empty`）而非静默跳过。
  - 落库边界强制：`case_store.upsert_intents` 在唯一持久化咽喉点调用
    `resolve_intents_batch`（与 `sanitize_case_checks` 同一原则，任何生产者自动受约束）。
  - 执行端 `browser_structured_executor._resolve_navigate_url`：占位目标直接
    `_nav_ok=False` 返回 None → 判结构化失败，**不再**把 "/" 当相对路径拼成站点根。
  - 评审 UI：`intent-review.html` 优先渲染 `actions`（可执行指令链）、过滤 screenshot
    取证步骤；`nav_unresolved` 时显示「目标页未解析」徽章（`.md-step-unresolved`），
    不再渲染「导航到 /」。存量数据由 `/api/cases/review` **读时投影**
    （reason=`legacy_placeholder`，不回写 DB），历史用例同样如实展示。
- **实证（修复后）**：`/api/cases/review` 首条 `nav_unresolved=true`、
  `target_hint="搜索"`；浏览器刷新后第一步文案「导航到 搜索」+「目标页未解析」徽章，
  全页无「导航到 /」。用真实导航图重跑解析：首步 `url=http://127.0.0.1:8020/`。
- **回归锁**：`tests/test_step3/test_nav_unresolved_marking.py`（6 例）、
  `tests/test_step4/test_link_discovery.py`（8 例：同源/协议/破坏性/实体去重/身份）、
  `tests/test_step4/test_nav_placeholder_exec.py`（3 例）、
  `tests/test_core/test_instruction_contract_gate.py` 增加「落库边界必须调用
  resolve_intents_batch」静态不变量。同批回归 `test_step3 + test_step4 + test_core`
  = **387 passed**。

## 5. 引擎 Web API 速查（2026-09-22 增补，agent 写脚本前必读，防重复踩坑）

- **POST /api/login**：JSON 键为 **`user_id`**（规范键，语义=用户名）+ `password`。
  服务端已归一化接受别名 **`username`**（2026-09-22 起，入口归一化，历史脚本曾反复写错）。
  成功后靠 Flask session cookie 鉴权（脚本需带 CookieJar/Session）。
- **POST /api/step/<n>**：JSON `{project, step, end_step?, force_regen?, resume?}`；Step4 默认即全量（无 case_ids 时 include_executed 自动置 True）。
  **步骤不自动链式**：未带 `end_step` 时 `end_step=step`，调度器 `step_num > end_step → skipped`
  （`src/pipeline/scheduler.py:259-266`）。跑完 Step4 需**显式** `POST /api/step/5` 才会出报告
  （2026-10-28 实测：RUN_END 后 3 分钟无产物，显式触发后 ~75s 落库）。
- **完成判据**：executions 同 run_id 是 UPSERT（行数恒定）→ 必须用 run_logs 的 `RUN_END` 消息 + `executed_at >= start_ts` 切片判定，别信 `/api/status.running`。
- **错误列名**是 `failure_reason` 非 `error`；engine 控制台账号 admin/admin123（users 表，sha256）。
- **Playwright 登录探测**已子进程隔离 + with 块内 os._exit（teardown 在本机必死锁，勿改回线程/直调模式）。
- 验收脚本模板：`scripts/_accept_step4.py`（登录→触发→RUN_END 轮询→增量核对）。

### §5.1 API 契约审计结论（2026-09-22 02:07 全面比对后增补）

- **已归一化（服务端两键都收，写哪个都对）**：
  - /api/login、/api/users(POST)、/api/register：user_id（规范）+ username（别名）
  - /api/project(POST 切换)：name（规范）+ project（别名）——兄弟端点 /api/step/<n> 用 project，历史脚本必撞
  - /api/project/versions(POST)：version_name + version_label 本就双键兼容
- **审核端点族辨析（易混，语义不同）**：
  - POST /api/review/approve = 批量批准全部「需求」（Step2 闸门，无 body 必填）
  - POST /api/cases/review/approve = 单条「用例」批准（body: case_id）
  - POST /api/cases/review/batch-approve = 用例 green-lane 批量放行（review_lane 无数据→400，属闸门保护非 bug）
  - POST /api/cases/review/confirm = 用例确认收尾（无 body 键）
- **DB/响应字段坑**：executions 表无 created_at（用 executed_at）；错误列是 failure_reason 非 error；ai_health_metrics.created_at 已统一本地时间（2026-09-22）。
- **Step4 触发端点有三个**（/api/step/4、/api/step4-run、/api/step4-run-single）：脚本一律用 /api/step/4，其余为前端专用，勿混用。
- 遗留未改（有意）：step4 三端点冗余属历史表面，牵动前端，未经授权不重构。

## 6. 反补丁三禁（2026-09-27 用户裁定；可执行判定口径）

**三禁**：禁止打补丁、禁止重复造轮子、禁止保留感染性信息与注释。

**"是不是补丁"的判定口径（不用口号，按此判定）**

- **补丁** = 在"症状出现的那一层"加分支/加词表/加兜底，而不消除"同一语义被多处重新推导"。
  - 反例（本轮实际发生，勿重犯）：`期望极性(positive/negative)` 这一条语义被 ≥5 处
    各自拼输入重推 —— `_rule_extractor_keywords.expects_error_feedback`（权威）/
    `step3/case_rules/base.py` / `agent/explorer_mixins/_action.py:324,1073` /
    `step3/_check_guard.py:343,403` / `step4/verification/_db_ui.py:2040`。
    **词表统一 ≠ 根因修复；判据入口重复才是根因。**
  - 正确做法：语义在**上游一次性判定并落结构化字段**（如 `requirements.polarity`、
    `requirements.access_control`），下游**只读不推**；该语义的生产函数全局唯一。
- **重复造轮子** = 同一常量/判据/正则在 ≥2 处定义或 ≥2 处推导。
  - 已清（2026-09-27，勿回退）：`_DROP_EXPECT_KW` 归位模块级唯一；
    `_DENY_RE` 归位 `_rule_extractor_keywords` 唯一；`_ANON_RE`/`_AUTH_ASSERTION_RE`
    合并为 `_UNAUTH_STATE_RE` 唯一；析取式护栏统一为 `is_disjunctive_text` 唯一实现。
  - ✅ 已清（2026-10-02）：跨模块重复词表 21 组 + 同文件重复 4 项全部归并为
    「权威源定义 + 下游 import 引用」（含 101 元 DOM 噪声表→`_rule_extractor_dom`、
    数据源→`datasource_registry.BUILTIN_SOURCE_KEYS`、严重级序→`core/severity.py`、
    引擎逻辑表→`core/known_tables.py`），ratchet 基线已收缩为空。门禁照常拦截新增。
    ✅ 已清（2026-10-26）：`step3/_identity_assign.py` 需求级执行身份现读上游结构化
    `access_control`（单一事实源 `core/requirement_semantics`，仅字段缺失时回退同一函数）；
    残留正则只判「该用例断言是否要求被拒绝 / 点名了哪个角色」，属断言级与实体识别，
    非需求级语义重推（口径见 §4.9 F）。
- **感染性信息/注释** = 注释里保留「Fix X(日期) / 根因(实测) / 第N轮修复」这类**事件叙事**：
  它诱导后来者顺着旧分支继续加分支。规则注释**只留一行 why**（目的 + 它修的是哪个既有缺陷）。

**门禁**

- 已有：`tests/test_core/test_instruction_contract_gate.py`、`tests/test_core/test_static_scope_gate.py`。
- 已补（2026-09-27）：`tests/test_core/test_duplicate_definition_gate.py` —— 拦「同一常量/词表
  被重复定义」，已接入 `.pre-commit-config.yaml` 防线 5。判据：A=同文件同名 `_` 常量
  （tuple/list/set ≥2 元素）定义 ≥2 次；B=同一多元素字符串常量（≥3 元素）出现在 ≥2 个模块。
  B 有存量，走 ratchet 基线**只减不增**。
- ✅ 当前重复词表实测 **0 组**（2026-10-02 全量归并完成，dup gate A=0/B=0）。
  已知语义分叉处置（2026-10-04 裁定）：
  ① API 执行双轨 —— ✅ 已解：探针随靶子走（examples/api_demo/empirical_probes.py），
    target_cfg["probes"] 声明派发（提交 08f0ee5），未声明靶子 fail-closed BLOCKED。
  ② 定位器 —— ✅ 裁定为**两层刻意设计**，非重复：agent 侧（element_utils）是
    框架无关 W3C/ARIA + force 兜底的点击执行器；tool 侧（browser_tools）是
    框架感知模糊模板的定位器（返回 Locator）。输入语义/返回语义/选择器哲学均不同，
    选择器清单顺序=匹配优先级（各自带 5-why 缺陷修复记录），合并属设计倒退。
    字面重复已随词表归并清零。
  ③ Step3 两套规则生成体系 —— 保留双入口（按控件类型兜底 vs 按方法论码叠加）。
    ✅ 方法码"未决定标记"集合已收敛到 catalog.UNDECIDED_METHOD_MARKERS 单点
    （曾漂移：制造层 2 项 vs 校验层 6 项，提交 2026-10-04）。
    去重判定：模板内步骤去重（dedup_verifications，按 action/target/value/expect）
    与跨用例语义去重（case_quality/dedup.py，embedding 相似度）是**两层不同语义**，
    非重复。唯一遗留深水区：两套 verify 模板 dict 结构内核共享（~400-600 行），
    前置条件 = Step3 产物快照基线（需 AI 全流水线跑基线），列为下一专项。
- **交付验证口径（2026-09-27 用户裁定）**：一切修复的完成判据 = **真实流水线实证**——
  必须跑 Step3→approve→pilot→Step4，用 `_probe_rerun.py` 按 run_logs `RUN_END` +
  `executed_at>=start_ts` 切片核验：ERROR/FAIL 逐条归因（引擎缺陷必修、SUT 缺陷归 SUT）、
  假 PASS=0、耗时对照基线。不允许用单测/静态分析替代流水线实证。
- **仍缺**：拦「下游越权重推上游已判定语义」（需先用极性/身份契约化把字段落到上游，
  否则门禁无判据）。同类问题已于 2026-08-29 审计过：
  `deliverables/software-company/e2e-gap-audit-2026-08-29.md §C`
  （C2 已判定 `_agent_checks._engine_coverage_gap` 为"针对 TC-REQ-004-I01 的补丁，需重构"——
  即**只有文字规则、没有门禁，所以又犯了一次**）。
