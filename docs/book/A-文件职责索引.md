# 附录 A · 文件职责索引

本附录由 AST 脚本扫描生成（`docs/book/_build/gen_file_index.py`），覆盖仓库内全部 1327 个 Python 源文件。每个条目给出文件路径、行数与职责——职责取自模块文档串首句；无模块文档串的文件改列其顶层类名。产物目录（`build/`、`dist/`、`node_modules/` 等）不在扫描范围。

## （仓库根目录）　（2 个文件 / 295 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `main.py` | 157 | 智能测试引擎通用命令行入口。 |
| `run.py` | 138 | 统一配置驱动入口 — 调度各靶子 E2E 流水线。 |

## src/　（2 个文件 / 50 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/__init__.py` | 1 | （无模块文档串） |
| `src/version.py` | 49 | 引擎版本号 — 单一事实源 ====================== |

## ModSystem/　（13 个文件 / 4047 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `ModSystem/_diag.py` | 139 | 快速诊断：确认 hidden 覆盖层是否拦截指针事件，并验证修复效果。 |
| `ModSystem/app/app.py` | 766 | ModSystem — 引擎能力覆盖靶子系统。 |
| `ModSystem/app/bugs.py` | 84 | ModSystem — 缺陷注入开关。 |
| `ModSystem/app/db.py` | 250 | ModSystem — SQLite 数据层与种子数据。 |
| `ModSystem/demo_injection.py` | 118 | 现场演示：同一用例，注入缺陷前后结果对比（最直观的可视证据）。 |
| `ModSystem/e2e_pipeline.py` | 1583 | ModSystem × 引擎 覆盖实测流水线。 |
| `ModSystem/export_cases.py` | 145 | 导出靶子用例集与需求文本，供引擎侧消费。 |
| `ModSystem/gen_coverage_doc.py` | 303 | 从引擎权威词表 + e2e_pipeline 用例表，自动生成《引擎能力覆盖矩阵》文档。 |
| `ModSystem/run_server.py` | 69 | ModSystem 启动脚本（供测试引擎 SutHarness / 手动启动复用）。 |
| `ModSystem/selfcheck.py` | 152 | ModSystem 自检脚本 —— 启动服务并验证全部页面/接口/缺陷注入。 |
| `ModSystem/serve_daemon.py` | 79 | 以脱离父进程的方式启动 ModSystem，保证会话结束后服务仍存活。 |
| `ModSystem/shots.py` | 49 | 用真实浏览器逐页截图，产出可视证据。 |
| `ModSystem/verify_injection.py` | 310 | ModSystem — 缺陷注入双向可信度验证（判真 / 判伪）。 |

## autoresearch/　（3 个文件 / 1114 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `autoresearch/__init__.py` | 7 | AutoResearch — Karpathy Loop 移植到测试引擎。 |
| `autoresearch/engine_eval.py` | 616 | AutoResearch 评估基准 — 锁死，不可修改。 |
| `autoresearch/ratchet.py` | 491 | AutoResearch Ratchet Loop — 自动实验循环脚本。 |

## data/　（2 个文件 / 27 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `data/_inject_req.py` | 18 | （无模块文档串） |
| `data/_probe_src.py` | 9 | （无模块文档串） |

## docs/　（6 个文件 / 1027 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `docs/book/_build/build_all.py` | 47 | 一键重建全部产物。 |
| `docs/book/_build/build_html.py` | 329 | 把 docs/book/*.md 合成为单文件 HTML 电子书。 |
| `docs/book/_build/build_md.py` | 109 | 把 docs/book/*.md 按顺序合并为单文件 Markdown 全书。 |
| `docs/book/_build/gen_appendices.py` | 297 | 生成附录 A（文件职责索引）、附录 B（数据表速查）、附录 C（HTTP 接口清单）。 |
| `docs/book/_build/gen_file_index.py` | 156 | 全量源码索引生成器。 |
| `docs/book/_build/normalize.py` | 89 | 章节标题归一化 + 已知事实勘误。 |

## examples/　（14 个文件 / 4747 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `examples/api_demo/__init__.py` | 3 | api_demo — Python 包标记。 |
| `examples/api_demo/app.py` | 265 | api_demo — API 型靶子系统（Flask REST 服务，端口 8010） ============================================================ [未复核-需 GLM/Dee… |
| `examples/api_demo/e2e_pipeline.py` | 788 | E2E 引擎 — api_demo（API 型靶子系统）完整流水线 ==================================================== [未复核-需 GLM/DeepSeek 把关] 新靶子流水线（2… |
| `examples/api_demo/empirical_probes.py` | 470 | 顶层类：ApiAssertionRunner |
| `examples/api_demo/seed.py` | 21 | api_demo — 种子数据（启动自动建库种子） ============================================ [未复核-需 GLM/DeepSeek 把关] 新靶子系统种子（2026-08-10 新增）。 |
| `examples/api_demo_broken/app.py` | 265 | api_demo — API 型靶子系统（Flask REST 服务，端口 8010） ============================================================ [未复核-需 GLM/Dee… |
| `examples/api_demo_broken/seed.py` | 21 | api_demo — 种子数据（启动自动建库种子） ============================================ [未复核-需 GLM/DeepSeek 把关] 新靶子系统种子（2026-08-10 新增）。 |
| `examples/promo_demo/app.py` | 184 | promo_demo 靶子系统 — 电商订单域（用于引擎能力演示，含 3 个真实缺陷）。 |
| `examples/promo_demo/e2e_pipeline.py` | 545 | E2E 引擎 — promo_demo（电商订单域演示靶子）完整流水线 ======================================================== 推广素材靶子：故意注入 3 个真实缺陷，验证引擎能正… |
| `examples/promo_demo/screenshot_engine.py` | 130 | 引擎前端能力推广截图 — 叙事聚焦"引擎本身暴露的能力"。 |
| `examples/promo_demo/screenshot_promo.py` | 127 | promo_demo 推广截屏工具 — 产出视频推广素材到 deliverables/promo/。 |
| `examples/smartshop/app.py` | 465 | [未复核-需 GLM/DeepSeek 把关] smartshop — Flask 商品管理靶子（端口 8020，带分页的浏览器 CRUD）。 |
| `examples/smartshop/e2e_pipeline.py` | 803 | E2E 引擎 v3.0 — SmartShop 完整流水线演示 |
| `examples/spa_demo/e2e_pipeline.py` | 660 | E2E 引擎 — spa_demo（SPA 型靶子系统）完整流水线 ==================================================== [未复核-需 GLM/DeepSeek 把关] 新靶子流水线（2… |

## scripts/　（56 个文件 / 7407 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `scripts/_accept_step4.py` | 114 | Step4 子进程隔离修复端到端验收（v2）： - 完成判据改用 run_logs 的 RUN_END（executions 同 run_id 是 UPSERT，行数不敏感） - 用 start_ts 切片统计本次 run 的 execu… |
| `scripts/_audit_44fam_matrix.py` | 133 | 44 族能力点 × 接线证据 × 单轮点火矩阵（单次 grep 全量解析，避免 88 次子进程）。 |
| `scripts/_audit_44fam_round_scope.py` | 82 | 按「单轮」口径重算 44 族点亮：这是唯一诚实的口径。 |
| `scripts/_collect_l1_evidence.py` | 81 | 采集 L1 目标层验收证据（通用/智能/易用/工程师水位 四维）—— 全部来自 MySQL 实测。 |
| `scripts/_extract_metric_matrix.py` | 73 | 全量指标发射矩阵抽取器（以代码为准）。 |
| `scripts/_peek_now.py` | 19 | （无模块文档串） |
| `scripts/_rerun_subset_d18.py` | 149 | D18/D20/D21 定向子集端到端验证：重启后只跑受影响用例，核对判定迁移是否生效。 |
| `scripts/_verify_d18_20_21.py` | 130 | D18 / D20 / D21 回归自检（合成断言，不依赖 DB，秒级可复跑）。 |
| `scripts/_verify_d18_replay_round5.py` | 149 | D18/D20 回放验证 v2：用 round5 真实数据重放判定，量化"假 Bug 消除数"。 |
| `scripts/_verify_step2_gap_v3.py` | 144 | v3：「步骤增量跳过」4 族点亮观察器（正确终态判据）。 |
| `scripts/ai_stand_in_server.py` | 177 | AI Stand-in 接力服务（Ollama + OpenAI 双协议兼容）。 |
| `scripts/audit/ui_audit/_analyze.py` | 39 | 分析 ui_audit_report.json，汇总各页问题清单 |
| `scripts/audit/ui_audit/crawler.py` | 453 | UI 自动化核查爬虫（动态层 B + 跳转层 C + 数据层 E）—— 彻底覆盖所有 Tab 与跳转页 ===================================================================… |
| `scripts/audit/ui_audit/static_scan.py` | 89 | 静态层核查 A：模板内 els 引用 vs els 注册 一致性 + $('id') 是否存在。 |
| `scripts/bundle/_smoke_install/_internal/src/frontend/__init__.py` | 1 | （无模块文档串） |
| `scripts/bundle/setup_installer.py` | 225 | SmartShop 测试引擎 — 安装向导 (setup.exe 主体) ============================================== 体验: 双击 setup.exe -> 选安装目录 -> [安装] -… |
| `scripts/case_runner/_argspec.py` | 63 | 从引擎源码自动提取每个断言/动作工具的参数契约（args.get / check.get 的键）。 |
| `scripts/case_runner/_env.py` | 134 | 用例执行环境（真实浏览器 + 真实靶子，非 mock）。 |
| `scripts/case_runner/_run.py` | 792 | 通用用例执行器：把 xlsx 用例逐条真实下发到引擎执行器。 |
| `scripts/case_runner/_spike.py` | 97 | 技术可行性验证：用引擎自带的 PlaywrightEngine 真实驱动浏览器打靶子。 |
| `scripts/case_runner/_tracks_exec.py` | 467 | 非浏览器工具层指令的真实执行路由（轨路由）。 |
| `scripts/case_runner/analyze.py` | 81 | 对执行报告做根因聚类（区分引擎缺陷 / 驱动器缺陷 / 环境 / 用例语义）。 |
| `scripts/case_runner/check_resolver.py` | 155 | 断言指令 → 校验器处理器的通用解析器。 |
| `scripts/case_runner/diag_metrics.py` | 79 | 指标页"0/空"诊断：直接调用全部 fetcher，逐板块输出真实返回。 |
| `scripts/case_runner/engine_tools.py` | 63 | 引擎真实工具全集（权威事实源 = 引擎自己执行分派表，非本文件硬编码）。 |
| `scripts/case_runner/fix_db_cred.py` | 86 | 诊断并修复 DB 轨凭据配置（环境配置问题，非引擎代码缺陷）。 |
| `scripts/case_runner/report.py` | 232 | 生成用例执行总览报告（HTML），数据源 = logs/case_runner/*.json 逐条证据。 |
| `scripts/case_runner/run_batch.py` | 141 | 批量真实执行用例集（xlsx → 引擎执行器 → 证据报告）。 |
| `scripts/case_runner/scan_lower_mismatch.py` | 69 | 通用根因排查：查找「归一化后比较」与「含大写常量」冲突的同类缺陷。 |
| `scripts/diag_case_checks.py` | 55 | 诊断 7：检查被合并用例的 checks 构成（矛盾断言）。 |
| `scripts/diag_check_guard.py` | 68 | 诊断 8：复现 check_guard 极性过滤为何未剔除正向用例的 field_error_visible_check。 |
| `scripts/diag_fail_by_type.py` | 50 | 按 case_type 剖析失败用例，判断跨切面检查是否被注入到非本类型用例。 |
| `scripts/diag_fail_reasons.py` | 34 | 剖析 accept_sut_0923 失败原因分布（定位引擎缺陷 vs 靶子系统问题）。 |
| `scripts/diag_false_fail.py` | 41 | 诊断 6：TC-REQ-002-I01 的完整失败原因（检查项全 PASS 却判 FAIL）。 |
| `scripts/diag_kpi_state.py` | 44 | 诊断 Dashboard KPI（通过/失败、已执行）取数口径与当前 DB 真实状态。 |
| `scripts/diag_page_dedup.py` | 50 | 检查 兼容/易用 用例的页面归属多样性，确定去重键。 |
| `scripts/diag_req_compat.py` | 31 | 列出 accept_sut_0923 需求，检查是否声明了 兼容/响应式/移动端/易用 类诉求。 |
| `scripts/diag_run_summary.py` | 44 | 诊断：run_summary（Dashboard 通过/失败 的权威来源）与 executions 实时口径对照。 |
| `scripts/diag_run_timing.py` | 30 | 查看历史 Step3/Step4 耗时，估算重跑成本。 |
| `scripts/diag_step4_history.py` | 20 | 历史 Step4 全量 run 的起止与耗时（判断重跑可行性）。 |
| `scripts/diag_stuck_kpi.py` | 135 | 诊断：Dashboard KPI 卡在 91/267 / 358 已执行的取数根因。 |
| `scripts/diag_stuck_kpi2.py` | 94 | 诊断 2：为何数据"卡住" —— 检查 run_logs 是否有新 run、active 版本是否漂移。 |
| `scripts/diag_stuck_kpi3.py` | 93 | 诊断 3：run_logs 最近活动 + 兼容/易用用例定义 + 失败是否同源。 |
| `scripts/diag_stuck_kpi4.py` | 60 | 诊断 4：Step4 停滞期（09-25 凌晨~中午）run_logs 尾部 + PRE_GATE 行内容。 |
| `scripts/diag_stuck_kpi5.py` | 103 | 诊断 5：重跑前置条件（AI 端点 / 用例审核状态 / 可重跑子集）。 |
| `scripts/diag_sut_probe.py` | 35 | 探活 fullcov_sut：真实登录 + 列表页 + 关键 data-testid 存在性。 |
| `scripts/gen_empirical_4dim.py` | 235 | 五维实证汇总报告生成器 — 消费 logs/reports/empirical_<key>.json，渲染含 a11y / perf / visual / security / bug 五维证据的统一 HTML 报告。 |
| `scripts/generate_engine_selftest_cases.py` | 676 | 引擎自检全链路测试用例集 生成器 v2 ======================================== 对象：把「智能测试引擎」本身当作被测系统，对引擎的 ① 操作指令轨 ② 断言指令轨 ③ 前端控件轨 ④ 数据链路(指… |
| `scripts/keep_alive_backend.py` | 92 | 后端保活守护（Windows）。 监控本地 8700 端口；若后端进程意外退出/端口不可达，则自动重新拉起， 避免出现「无法访问，请启动服务器」的停摆状态。 |
| `scripts/maintenance/generate_verification_report.py` | 187 | 从 MySQL ``executions`` 表生成可复算的验证报告（逐用例 view）。 |
| `scripts/maintenance/install_git_hooks.py` | 124 | 安装/卸载 git pre-commit 钩子：提交涉及 src/frontend/ 的改动时自动跑布局锁校验。 |
| `scripts/maintenance/migrate_rc_ae_selectors.py` | 169 | RC-AE 存量数据迁移：把 payload_json.checks 里的**旧反馈条词表**替换为单一事实源。 |
| `scripts/maintenance/migrate_rc_s_verify_steps.py` | 102 | RC-S 存量脏数据清理：删除用例 test_method.verifications 中残留的「字段约束」验证点。 |
| `scripts/maintenance/perf_calibrate.py` | 185 | O4 性能相对基线周期校准驱动（定时自动化，联动 target 命令 --perf-recalibrate）。 |
| `scripts/step5_watcher.py` | 81 | Step4→Step5 自动衔接守望器（纯轮询，零 AI 介入）。 |
| `scripts/verify_selftest_xlsx.py` | 52 | 交付自查：校验生成的 xlsx 是否字段完整、无空壳、方法覆盖全集。 |

## src/agent　（10 个文件 / 8170 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/agent/__init__.py` | 3 | src/agent 包 — 覆盖驱动探索代理（P2 前沿模块）。 |
| `src/agent/coverage_crawl.py` | 510 | P2-9 CoverageCrawl — 覆盖驱动探索接口（仅接口，先小样本证伪）。 |
| `src/agent/element_utils.py` | 778 | P2-10 元素工具 — 模块级通用函数（页面快照、元素匹配、点击、观察）。 |
| `src/agent/explorer.py` | 551 | P2-10 AgentExplorer — Agent 驱动页面探索器（对标 BrowserGym/WebVoyager）。 |
| `src/agent/explorer_mixins/__init__.py` | 22 | P2-10 AgentExplorer mixins — 按功能拆分，每个文件 ≤ 800 行。 |
| `src/agent/explorer_mixins/_action.py` | 4285 | P2-10 ActionMixin — 动作执行（触发点击、删除、通用点击、搜索）（~400行）。 |
| `src/agent/explorer_mixins/_element.py` | 552 | P2-10 ElementMixin — 元素发现、快照、匹配（~350行）。 |
| `src/agent/explorer_mixins/_llm.py` | 489 | P2-10 LLMMixin — LLM驱动的多步探索闭环（~350行）。 |
| `src/agent/explorer_mixins/_semantic.py` | 474 | P2-10 SemanticMixin — LLM语义匹配与断言（~250行）。 |
| `src/agent/explorer_mixins/_sidebar.py` | 506 | P2-10 SidebarMixin — 侧边栏/NavMap/Tab探索（~300行）。 |

## src/ai　（28 个文件 / 4740 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/ai/__init__.py` | 1 | （无模块文档串） |
| `src/ai/ai_client.py` | 43 | AI 核心客户端 (AiBrain) ======================== 本模块提供 AI API 调用的底层封装，包括： - 通用 API 调用（按任务类型路由） - JSON 响应解析 - Token 计费统计 - 观察… |
| `src/ai/ai_client_mixins/__init__.py` | 3 | ai_client_mixins — AI 客户端混入模块 |
| `src/ai/ai_client_mixins/_base.py` | 544 | 顶层类：_BaseMixin |
| `src/ai/ai_client_mixins/_business.py` | 39 | AiBrain 业务方法挂载 Mixin ========================== 通过 __getattr__ 懒加载 step3 的业务函数，避免循环导入。 同时提供模块级辅助函数用于单例的惰性挂载。 |
| `src/ai/ai_client_mixins/_parse.py` | 27 | AiBrain 响应解析 Mixin ===================== 提供 JSON 响应解析等辅助静态方法。 |
| `src/ai/ai_client_mixins/_stats.py` | 29 | AiBrain 统计 Mixin ================== 提供统计信息查询与重置能力。 |
| `src/ai/ai_providers/__init__.py` | 82 | AI Provider 抽象适配层 ======================= 统一封装 OpenAI / DeepSeek / Anthropic 的 API 差异， 对外暴露一致的 chat() 接口。 |
| `src/ai/ai_providers/anthropic.py` | 161 | 顶层类：AnthropicProvider |
| `src/ai/ai_providers/base.py` | 73 | 顶层类：BaseProvider |
| `src/ai/ai_providers/deepseek_openai.py` | 61 | DeepSeek + OpenAI Provider（OpenAI 兼容格式 + 各自计费）— 从 ai_providers.py 拆出，零业务逻辑变更。 |
| `src/ai/ai_providers/ollama.py` | 160 | 顶层类：OllamaProvider |
| `src/ai/ai_providers/openai_compat.py` | 366 | 顶层类：_OpenAICompatProvider |
| `src/ai/browser_detector.py` | 142 | 顶层函数：_query_registry_path、_check_fallback_paths、detect_browsers、get_installed_browsers |
| `src/ai/data_sandbox.py` | 364 | 数据沙箱 (DataSandbox) — v5 ============================ 测试用例间数据隔离：快照、变更检测、回滚、重建。 |
| `src/ai/json_parser.py` | 412 | JSON 响应解析与修复模块 ======================== 提供对 LLM 返回文本中 JSON 内容的健壮解析，处理常见格式问题： - markdown 代码块包裹 - 尾逗号、注释、控制字符、非法转义 - 截断 J… |
| `src/ai/llm_circuit.py` | 51 | LLM 不可用性熔断（进程级单例，供语义辅助层快速失败）。 |
| `src/ai/local_semantics.py` | 247 | 本地语义引擎 — 云端 LLM 不可用时的确定性语义能力（stdlib-only）。 |
| `src/ai/model_registry.py` | 173 | 模型注册与发现模块 (ModelRegistry) =================================== 负责模型列表拉取、缓存、视觉能力检测等，从 config_loader.py 拆分而来。 |
| `src/ai/observers.py` | 50 | 观察者通知模块 ============== 封装 AiBrain 的观察者回调通知逻辑，供 GUI 注册以实现 thinking... 实时反馈。 |
| `src/ai/pricing_fetcher.py` | 439 | 顶层类：PricingFetcher |
| `src/ai/router_module/__init__.py` | 12 | 路由模块 (RouterModule) — facade ================================= 外部导入路径不变：from src.ai.router_module import RouterModule, … |
| `src/ai/router_module/_router.py` | 626 | 顶层类：RouterModule |
| `src/ai/router_module/local_ollama.py` | 137 | 本地 Ollama 调用 + JSON 校验工具 — 从 router_module.py 拆出，零业务逻辑变更。 |
| `src/ai/router_module/task_types.py` | 86 | TaskType 枚举 + 模型定价表 + 任务-模型映射 — 从 router_module.py 拆出，零业务逻辑变更。 |
| `src/ai/stats.py` | 90 | 统计与计费模块 ============== 封装 AiBrain 的 Token 用量统计与费用计算逻辑。 |
| `src/ai/token_estimator.py` | 139 | Token 估算器 (TokenEstimator) =============================== 轻量级 token 计数（不依赖 tiktoken，零外部依赖）。 用于动态调整 max_tokens，避免输出截断或浪… |
| `src/ai/visual_verify.py` | 183 | 视觉验证模块 ============ 封装 AiBrain 的视觉验证能力：截图 + 多模态模型判断。 |

## src/backend　（188 个文件 / 42385 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/backend/__init__.py` | 1 | （无模块文档串） |
| `src/backend/_api_auth.py` | 101 | API Token 鉴权模块 ================== 通过 X-API-Key 请求头进行 CI/CD 外部调用鉴权。 |
| `src/backend/_error_handlers.py` | 73 | API JSON 错误处理器（从 app.py 拆分）。 |
| `src/backend/_middleware.py` | 535 | 中间件函数（从 app.py 拆分）。 |
| `src/backend/_ring_buffer.py` | 66 | RingBuffer — 线程安全的环形缓冲区队列（从 app.py 拆分）。 |
| `src/backend/_sse_routes.py` | 600 | SSE 实时日志流 + Prometheus 指标 + 前端入口路由（从 app.py 拆分）。 |
| `src/backend/app.py` | 645 | E2E 智能测试引擎 — Web 控制面板 (Flask + SSE) =============================================== 入口文件（facade）：创建 Flask 应用，注册所有 Bluep… |
| `src/backend/case_upload_history.py` | 324 | 用例上传历史记录（T08 — 上传追溯）。 |
| `src/backend/cleanup_manager.py` | 468 | KEEP-27: CleanupManager — 统一的资源清理管理器。 ==================================================== 将分散在 log_persistence、db_agen… |
| `src/backend/executor/__init__.py` | 16 | 执行引擎模块（package 模式） ============================ 从原 executor.py 拆分而来：_run_step, _retry_failed, _clear_files, _figma_refr… |
| `src/backend/executor/_clear.py` | 469 | _clear_files —— MySQL 化清除中间产物。 从原 executor.py 提取，逻辑保持不变。 |
| `src/backend/executor/_figma_refresh.py` | 76 | _figma_refresh_task —— 在后台线程中刷新 Figma 缓存。 从原 executor.py 提取，逻辑保持不变。 |
| `src/backend/executor/_retry.py` | 191 | _retry_failed —— 失败测试用例重跑。 从原 executor.py 提取，逻辑保持不变。 |
| `src/backend/executor/_run_step.py` | 704 | _run_step —— 在后台线程中执行指定步骤。 从原 executor.py 提取，逻辑保持不变。 |
| `src/backend/helpers.py` | 435 | 业务辅助函数模块 ================ 从 app.py 提取：_real_stats, _load_case_asset_files, _load_browsers, _get_available_priorities, _… |
| `src/backend/log_capture.py` | 518 | 日志捕获与 SSE 广播模块 |
| `src/backend/log_persistence.py` | 313 | 运行日志持久化模块（MySQL 版） ================================= 将每次 Step 执行的日志实时写入 MySQL `run_logs` 表，替代原先的磁盘 .log 文件。 保留函数签名（begi… |
| `src/backend/observers.py` | 262 | 观察者注册与日志劫持模块 ======================== 从 app.py 提取：_register_ai_observers, _hijack_router_logs, log_step_ok, log_step_fa… |
| `src/backend/pydantic_schemas.py` | 392 | Pydantic 请求校验模块 ====================== 为 Flask 路由提供类型安全的请求体校验。 |
| `src/backend/request_schema.py` | 76 | KEEP-30: API 请求 schema 校验基础层。 |
| `src/backend/routes/__init__.py` | 1 | （无模块文档串） |
| `src/backend/routes/_data_helpers.py` | 46 | data 路由辅助函数 ================= 从 data.py 提取的公共辅助函数，供 data.py（facade）和 routes/ 子模块共同使用。 |
| `src/backend/routes/action_library/__init__.py` | 25 | 操作指令库管理 API (Action Library Blueprint) ================================================ 提供操作指令库的 CRUD / 搜索 / 持久化。 |
| `src/backend/routes/action_library/_validators.py` | 143 | 操作指令库 — 字段校验辅助函数 |
| `src/backend/routes/action_library/routes/__init__.py` | 9 | 操作指令库 — 路由子包 |
| `src/backend/routes/action_library/routes/_crud.py` | 288 | 操作指令库 — CRUD / 搜索路由 |
| `src/backend/routes/action_library/routes/_export.py` | 80 | 操作指令库 — 导入 / 导出路由 |
| `src/backend/routes/auth/__init__.py` | 18 | 认证与用户管理路由 Blueprint — 子模块聚合入口 原 auth.py 拆分后的 re-export，保持外部导入不变。 |
| `src/backend/routes/auth/_core.py` | 14 | 认证 Blueprint 核心定义与公共依赖 |
| `src/backend/routes/auth/_credential.py` | 271 | 用户级凭证管理 |
| `src/backend/routes/auth/_key.py` | 382 | API Key 设置 / 清除 / Ollama 模型列表 |
| `src/backend/routes/auth/_login.py` | 217 | 登录 / 登出 / 当前用户 |
| `src/backend/routes/auth/_permission.py` | 163 | 功能权限 API（按钮级访问控制） |
| `src/backend/routes/auth/_role.py` | 490 | 角色管理 API（RBAC 核心 — 角色 CRUD + 权限/项目勾选 + 用户-角色分配） |
| `src/backend/routes/auth/_user.py` | 374 | 用户 CRUD / 项目授权 / 执行记录 |
| `src/backend/routes/calibration/__init__.py` | 16 | 校准环路由 (facade) =================== P1-7: 暴露 calibration_samples 统计给前端人审界面。 |
| `src/backend/routes/calibration/_routes.py` | 124 | 校准环路由 — 暴露 calibration_samples 统计给前端人审界面。 |
| `src/backend/routes/case_review/__init__.py` | 33 | 执行控制 — 测试用例审核端点 (facade) ======================== Step 3 完成后触发测试用例审核闸门（人工确认/编辑/删除/重新建模）。 |
| `src/backend/routes/case_review/_remodel.py` | 408 | 测试用例重新建模（AI 调用 + 替换） — 从 case_review.py 拆出。 |
| `src/backend/routes/case_review/_routes.py` | 889 | 审核路由 — 测试用例审核端点（注册到 exec_bp） |
| `src/backend/routes/case_review/_state.py` | 110 | 审核状态管理 — 测试用例审核闸门的状态查询 v2: 审核状态已迁移到 case_meta.review_status DB 实体字段， 不再使用内存 _case_review_state。 |
| `src/backend/routes/case_rules/__init__.py` | 16 | 用例生成规则 API Blueprint ========================== 提供 Step3 用例生成规则的查询接口（只读，规则由代码文件定义，不存数据库）。 |
| `src/backend/routes/case_rules/_routes.py` | 115 | 用例生成规则 — 查询路由 |
| `src/backend/routes/case_upload/__init__.py` | 20 | Step3 用例上传路由 — Blueprint「case_upload」 ============================================ 用例绑定专项(2026-08)：把"用例上传执行"升级为绑定被测系统+版… |
| `src/backend/routes/case_upload/_decorators.py` | 56 | case_upload 路由共享装饰器与工具。 |
| `src/backend/routes/case_upload/_history.py` | 37 | GET /api/step3/case/uploads — 查询上传历史（T08）。 |
| `src/backend/routes/case_upload/_template.py` | 81 | GET /api/step3/case/template — 下载预填版本用例模板（权限 case.template）。 |
| `src/backend/routes/case_upload/_upload.py` | 203 | POST /api/step3/case/upload — 上传用例文件并执行（权限 case.upload）。 校验通过 → run_uploaded_cases → 写上传历史。 |
| `src/backend/routes/case_upload/_validate.py` | 107 | POST /api/step3/case/validate — 仅校验用例文件（不上传、不执行）。 返回 VersionMatchResult（matched/expected_version/message/policy）。 |
| `src/backend/routes/chart_data.py` | 397 | 可视化报告 — 图表数据 API ========================= 提供 /api/report/chart-data 端点，返回多维度结构化统计数据， 供前端 ECharts 图表渲染使用。 |
| `src/backend/routes/ci_integration.py` | 493 | CI/CD 集成路由 ============== 提供 API Token 管理、Webhook 回调触发、CI 配置模板生成。 |
| `src/backend/routes/clarification/__init__.py` | 21 | clarification/__init__.py — 交互式需求澄清 Blueprint 包入口 ========================================================== 对齐 quality… |
| `src/backend/routes/clarification/_routes.py` | 170 | 交互式需求澄清 API（clarification_bp）—— 流水线强化专项 T3。 |
| `src/backend/routes/data.py` | 36 | data.py — 数据路由统一入口（facade） ==================================== 【2026-06-18 拆分】路由按业务域物理拆分： - _data_helpers.py — _check_… |
| `src/backend/routes/data_datasources/__init__.py` | 143 | 数据源 & 需求审核 & 背景配置路由 — Facade ============================================ 作为 data_datasources 包的入口，定义常量， 并从 fetchers 子模… |
| `src/backend/routes/data_datasources/fetchers/__init__.py` | 1 | （无模块文档串） |
| `src/backend/routes/data_datasources/fetchers/bg.py` | 582 | 背景图片/配置路由处理函数（v3 — MySQL 图库存储）。 ────────────────────────── public API: api_upload_bg POST /api/upload-bg api_bg_image G… |
| `src/backend/routes/data_datasources/fetchers/confluence.py` | 168 | Confluence 统计/刷新路由处理函数。 ────────────────────────── api_confluence_stats, api_confluence_refresh |
| `src/backend/routes/data_datasources/fetchers/db/__init__.py` | 12 | 数据库统计/测试路由处理函数（包入口）。 ───────────────────── re-export api_db_stats, api_db_test |
| `src/backend/routes/data_datasources/fetchers/db/_stats.py` | 292 | 数据库统计路由处理函数。 ───────────────────── api_db_stats |
| `src/backend/routes/data_datasources/fetchers/db/_test.py` | 151 | 数据库测试路由处理函数。 ───────────────────── api_db_test |
| `src/backend/routes/data_datasources/fetchers/figma.py` | 263 | Figma 配额/刷新路由处理函数。 ───────────────────── api_figma_quota, api_figma_refresh |
| `src/backend/routes/data_datasources/fetchers/input_style.py` | 126 | 主题配色方案路由：GET/POST /api/input-style ────────────────────────── 仿 bg_config 模式，存储在 user_credentials_v2 表（project='*'，用户级偏… |
| `src/backend/routes/data_datasources/fetchers/requirements.py` | 303 | 需求审核路由处理函数。 ──────────────────────────────── api_requirements, api_review_requirement, api_confirm_all_requirements |
| `src/backend/routes/data_datasources/fetchers/yapi.py` | 138 | YApi 统计/刷新路由处理函数。 ─────────────────── api_yapi_stats, api_yapi_refresh |
| `src/backend/routes/data_models/__init__.py` | 22 | AI 模型选择路由 (package facade) ================ 外部导入保持不变: from src.backend.routes.data_models import data_models_bp |
| `src/backend/routes/data_models/_create.py` | 176 | 创建/更新路由：/api/task-model, /api/verification-model, /api/vision-model (POST) |
| `src/backend/routes/data_models/_delete.py` | 21 | 删除路由（占位）：/api/data-models/<model_id> (DELETE) |
| `src/backend/routes/data_models/_detail.py` | 21 | 详情路由（占位）：/api/data-models/<model_id> |
| `src/backend/routes/data_models/_list.py` | 500 | 列表路由：/api/ai-defaults, /api/task-models, /api/verification-models, /api/vision-models |
| `src/backend/routes/diagnostics/__init__.py` | 3 | 诊断中心 API 路由 |
| `src/backend/routes/diagnostics/diagnostics_api.py` | 515 | 诊断中心 — 三大功能 API： 1. POST /api/diagnostics/self-test — 流水线自检 2. GET /api/diagnostics/health — Step 健康快照 3. GET /api/diag… |
| `src/backend/routes/docs/__init__.py` | 23 | 文档中心 路由包 ============== docs_read: GET 读取路由 docs_write: POST 写入路由 docs_smoke: 核心验证测试用例 + 后台生成 |
| `src/backend/routes/docs/docs_read.py` | 26 | 文档中心 — 读取路由（Facade） ============================ 原 docs_read.py 已按功能域拆分为子模块，本文件保留为 facade， 通过导入子模块触发路由注册，保持对既有消费者的兼容性。 |
| `src/backend/routes/docs/docs_read_coverage.py` | 137 | 文档中心 — 覆盖率路由 ==================== |
| `src/backend/routes/docs/docs_read_downloads/__init__.py` | 13 | 文档中心 — 文档下载路由 (package facade) ======================================== 需求稿、功能清单、Figma 统计、测试用例文件、需求拆解表、执行人列表。 |
| `src/backend/routes/docs/docs_read_downloads/_downloads.py` | 372 | 文档中心 — 文件下载类路由 ======================== 需求稿、功能清单、测试用例文件下载、需求拆解表。 |
| `src/backend/routes/docs/docs_read_downloads/_queries.py` | 154 | 文档中心 — 查询类路由 ======================== 报告目录、Figma 统计、测试用例文件列表、执行人列表。 |
| `src/backend/routes/docs/docs_read_execution.py` | 353 | 文档中心 — 执行结果导出路由 ========================== 执行结果 ZIP 下载。 |
| `src/backend/routes/docs/docs_read_stats/__init__.py` | 22 | 文档中心 — 统计报告导出路由 (package facade) 多维度统计报告 + 多格式导出（md/xlsx/docx/pdf）。 |
| `src/backend/routes/docs/docs_read_stats/_export.py` | 381 | 统计报告多格式导出函数 |
| `src/backend/routes/docs/docs_read_stats/_stats.py` | 151 | 统计路由 — api_docs_execution_stats |
| `src/backend/routes/docs/docs_read_traceability.py` | 368 | 文档中心 — 追溯链路 + 变更对比 + RAG 面板路由 ============================================= |
| `src/backend/routes/docs/docs_read_versions.py` | 83 | 文档中心 — 文档版本管理路由 ========================== |
| `src/backend/routes/docs/docs_smoke.py` | 352 | 文档中心 — 冒烟用例导出路由 ============================ 冒烟用例 Excel 生成 + 后台线程状态 + 缓存检查 + 下载 + 清除。 从 docs_core.py 拆分出来。 |
| `src/backend/routes/docs/docs_trace_routes.py` | 436 | 关系链路统一 API（07-05h 新增） ================================ |
| `src/backend/routes/docs/docs_write/__init__.py` | 13 | 文档中心 — 写入路由 ==================== 需求稿上传、AI 补全、XMind 设计、RAG 索引重建。 从 docs_core.py 拆分出来。 |
| `src/backend/routes/docs/docs_write/_rag.py` | 194 | 文档中心 — RAG 索引重建路由 |
| `src/backend/routes/docs/docs_write/_requirement.py` | 181 | 文档中心 — 需求稿上传路由 |
| `src/backend/routes/docs/docs_write/_xmind.py` | 118 | 文档中心 — XMind 测试设计下载路由 |
| `src/backend/routes/docs_analytics/__init__.py` | 27 | docs_analytics/__init__.py — 文档分析路由包入口 ================================================= 原 docs_analytics.py 按 facade +… |
| `src/backend/routes/docs_analytics/docs_analytics.py` | 72 | docs_analytics.py — 文档分析路由（facade） ========================================== |
| `src/backend/routes/docs_analytics/fetchers/__init__.py` | 9 | docs_analytics.fetchers — 文档中心辅助函数子模块 |
| `src/backend/routes/docs_analytics/fetchers/mysql_doc.py` | 543 | 顶层函数：doc_type_from_mysql_uri、_ext_for_doc_type、_conn、_active_version_id、resolve_doc、_resolve_mysql_uri |
| `src/backend/routes/docs_analytics/fetchers/search.py` | 163 | docs_analytics.fetchers.search — 文件搜索/扫描/路径/版本辅助函数 ================================================================ |
| `src/backend/routes/docs_analytics/fetchers/stats.py` | 165 | docs_analytics.fetchers._stats — 统计数据辅助函数 |
| `src/backend/routes/docs_analytics/routes/__init__.py` | 2 | （无模块文档串） |
| `src/backend/routes/docs_analytics/routes/_file_tree.py` | 531 | routes/_file_tree.py — 文件浏览类路由 ====================================== |
| `src/backend/routes/docs_analytics/routes/_preview.py` | 188 | routes/_preview.py — 文件预览路由 ================================= - api_docs_preview — 文件预览 API（含 XMind 解析，最大端点） |
| `src/backend/routes/docs_analytics/routes/_save.py` | 190 | routes/_save.py — 文件读写类路由 ================================= - api_docs_save — 文件保存 API - api_docs_file — 文件下载 API |
| `src/backend/routes/docs_analytics/routes/_search.py` | 149 | routes/_search.py — 文件搜索路由 ================================= - api_docs_search — 文件搜索 API |
| `src/backend/routes/docs_analytics/routes/_storage.py` | 135 | routes/_storage.py — 存储用量统计路由 ====================================== - api_docs_storage_stats — 存储用量统计 API |
| `src/backend/routes/execution/__init__.py` | 454 | 执行控制路由 Blueprint ======================= URL 路径不变，从 app.py 提取。 |
| `src/backend/routes/execution/_pipeline_trace.py` | 508 | 流水线追溯链路 API（从 pipeline_progress.py 拆分，§4.3 单文件≤800行）。 |
| `src/backend/routes/execution/_progress_db.py` | 113 | 流水线进度 DB 查询：从数据库产物推断已完成的最大步骤号。 |
| `src/backend/routes/execution/client_browser.py` | 257 | 客户端浏览器 API ================ 支持用户使用自己的浏览器执行测试（CDP 远程连接模式）。 |
| `src/backend/routes/execution/config.py` | 536 | 执行控制 — 配置开关端点 ======================== P1/P2: 防幻觉功能开关 API (flags / toggle)。 从 execution.py 拆分出来。 |
| `src/backend/routes/execution/deployment.py` | 218 | Deployment 路由 ============== 从 execution.py 拆分，注册到 exec_bp Blueprint。 包含：deployment-docs, deployment-versions, last-run… |
| `src/backend/routes/execution/pipeline_progress.py` | 1181 | 流水线执行进度聚合 API ====================== GET /api/pipeline-progress 聚合 Step1-5 状态 + 需求条目状态 + 用例关联，供"流水线执行"页面消费。 |
| `src/backend/routes/execution/preflight.py` | 722 | Preflight 预检路由 ================== 从 execution.py 拆分，注册到 exec_bp Blueprint。 |
| `src/backend/routes/execution/review.py` | 303 | 执行控制 — 需求审核端点 ======================== P1-6: 需求审核门控 (approve / reject)。 v2: 审核状态已迁移到 requirements.review_status DB 实体… |
| `src/backend/routes/execution/run/__init__.py` | 22 | 执行控制路由 (facade) ===================== 从 run.py 拆分为 package 模式。 包含：coverage-check, run-step, step4-run, run-all, stop, s… |
| `src/backend/routes/execution/run/_start.py` | 1034 | 启动路由 ======== coverage-check, run-step, step4-run, run-all, retry-failed |
| `src/backend/routes/execution/run/_status.py` | 71 | 状态路由 ======== pause, resume |
| `src/backend/routes/execution/run/_stop.py` | 146 | 停止路由 ======== stop, stop-run |
| `src/backend/routes/execution/status.py` | 231 | Status 路由 ============ 从 execution.py 拆分，注册到 exec_bp Blueprint。 包含：api_status, api_version_check_confirm, api_version_c… |
| `src/backend/routes/export.py` | 441 | 导出路由 Blueprint ================== 从 MySQL 读取数据，按需生成 Excel / Markdown 文件供下载。 运行时只写 MySQL，导出时从库生成格式文件。 |
| `src/backend/routes/file_cleanup/__init__.py` | 24 | 文件清理路由 (package) ===================== /api/clear-files + /api/clearable-files + /api/clearable-files/children |
| `src/backend/routes/file_cleanup/_children.py` | 298 | 子列表懒加载路由：/api/clearable-files/children |
| `src/backend/routes/file_cleanup/_clear.py` | 48 | 清除文件路由：/api/clear-files |
| `src/backend/routes/file_cleanup/_list.py` | 359 | 可清除文件列表路由：/api/clearable-files |
| `src/backend/routes/file_upload/__init__.py` | 21 | Step1 文件上传路由 — P2 优化版 ================================= 支持 CSV / Excel / Markdown / Word / XMind / JSON / SQL / TXT 格… |
| `src/backend/routes/file_upload/_decorators.py` | 75 | file_upload package — 装饰器与常量 |
| `src/backend/routes/file_upload/_delete.py` | 112 | file_upload package — 删除与更新权重路由 |
| `src/backend/routes/file_upload/_list.py` | 88 | file_upload package — 列表路由 |
| `src/backend/routes/file_upload/_upload.py` | 260 | file_upload package — 上传路由 |
| `src/backend/routes/health/__init__.py` | 31 | health/__init__.py — 健康度仪表盘 API 包入口 ============================================= 原 health.py 按功能域拆分为： - health.py — Bl… |
| `src/backend/routes/health/fetchers/__init__.py` | 9 | health.fetchers — 健康度仪表盘数据获取与格式化模块 |
| `src/backend/routes/health/fetchers/format.py` | 154 | health.fetchers._format — 健康度数据格式化层 ============================================== 封装置信度标签、标签映射和摘要响应构建逻辑。 |
| `src/backend/routes/health/fetchers/queries.py` | 174 | 顶层函数：_get_conn、_latest_run_id、_query_metrics、_query_metrics_agg、_integrate_metrics |
| `src/backend/routes/health/health.py` | 56 | AI 健康度仪表盘 API — facade ============================== 提供幻觉率、守卫拦截、校验不一致等指标查询。 数据来源: ai_health_metrics 表（由 metrics_collec… |
| `src/backend/routes/health/routes_diagnostics.py` | 161 | 诊断/探针路由 — detailed / metrics / healthz / readyz |
| `src/backend/routes/health/routes_summary.py` | 481 | GET /api/health/summary — 最新运行聚合摘要 |
| `src/backend/routes/health/routes_trends.py` | 249 | 健康度趋势 + 最新运行明细 路由 |
| `src/backend/routes/kpi/__init__.py` | 16 | kpi/__init__.py — 统一 KPI 视图 Blueprint 包入口 ================================================ 对齐 quality/__init__.py 的包模式：… |
| `src/backend/routes/kpi/_aggregator.py` | 705 | KPI 聚合器（KpiAggregator）—— 流水线强化专项 T5。 |
| `src/backend/routes/kpi/_routes.py` | 87 | 统一 KPI 聚合 API（kpi_bp）—— 流水线强化专项 T5。 |
| `src/backend/routes/metrics_page/__init__.py` | 13 | metrics_page/__init__.py — 指标中心 API 包入口 ============================================= 原 metrics_page.py 按功能域拆分为子模块后，本文件… |
| `src/backend/routes/metrics_page/alerts.py` | 156 | 通用指标告警计算模块（2026-07-15） ================================ 从 metric_thresholds 表读取阈值配置，对比 fetcher 返回的指标数据， 生成告警等级（normal/w… |
| `src/backend/routes/metrics_page/fetchers/__init__.py` | 16 | metrics_page.fetchers — 指标中心各领域数据获取器 |
| `src/backend/routes/metrics_page/fetchers/_ai_overall.py` | 264 | AI 综合健康获取器（Flaky / 幻觉分类） |
| `src/backend/routes/metrics_page/fetchers/_blue_red.py` | 284 | 蓝红对抗指标获取器 (Blue-Red Adversarial Metrics) ================================================== 从 requirements / test_cases… |
| `src/backend/routes/metrics_page/fetchers/_cdp_browser.py` | 187 | CDP 客户端浏览器指标获取器（2026-07-15） ================================================ 读取 ai_health_metrics 表中 cdp_browser 类型的指标，… |
| `src/backend/routes/metrics_page/fetchers/_fusion.py` | 232 | 融合架构获取器（Tool-mode / Recipe 缓存 / 原子回滚） |
| `src/backend/routes/metrics_page/fetchers/_h4_new_metrics.py` | 190 | H4 新增指标获取器（2026-07-10） ================================ 读取 ai_health_metrics 表中 6 个新增 metric_type 的最新 run_id 数据： - slic… |
| `src/backend/routes/metrics_page/fetchers/_health.py` | 61 | 健康度汇总获取器 |
| `src/backend/routes/metrics_page/fetchers/_p0_governance.py` | 156 | P0 治理获取器（Harness / Scorecard / TokenBudget / FlakyGuard） |
| `src/backend/routes/metrics_page/fetchers/_pipeline_ext.py` | 101 | 流水线扩展获取器（Step1/2/3/5 插件注册数） |
| `src/backend/routes/metrics_page/fetchers/_smart.py` | 410 | 智能能力引擎获取器（业务能力 / 动态 DOM / 区域树 / 模糊匹配） |
| `src/backend/routes/metrics_page/fetchers/_step2_breakdown.py` | 228 | Step2 功能清单驱动拆解指标获取器 ================================ 读取 ai_health_metrics 表中 metric_type='step2_breakdown' 的最新 run_id 数… |
| `src/backend/routes/metrics_page/fetchers/_step4_plugins.py` | 331 | Step4 基础插件获取器（复用 _plugin.py 的 collect_plugin_metrics 顶层函数） |
| `src/backend/routes/metrics_page/fetchers/_system.py` | 40 | 系统运行时获取器（psutil 进程/内存/CPU） |
| `src/backend/routes/metrics_page/fetchers/_test_methods.py` | 147 | _test_methods.py — 测试方法论清单 API 数据获取器 |
| `src/backend/routes/metrics_page/fetchers/_utils.py` | 209 | metrics_page 共享工具函数 |
| `src/backend/routes/metrics_page/metrics_page.py` | 802 | metrics_page.py — 指标中心 API ============================== |
| `src/backend/routes/nav_match/__init__.py` | 16 | 导航匹配可观测路由 (facade) ============================ v5.15: 暴露 navigation_match_logs 查询/统计/校准给前端人审界面。 |
| `src/backend/routes/nav_match/_routes.py` | 76 | 导航匹配可观测路由（v5.15, 2026-08-04）——L2 LLM 语义匹配失败率统计 + 人工校准。 |
| `src/backend/routes/oracle/__init__.py` | 16 | 视觉 Oracle 路由 (facade) ========================= P1-7: 暴露 VLMOracle.check() 给前端人审界面。 |
| `src/backend/routes/oracle/_routes.py` | 186 | 视觉 Oracle 路由 — 暴露 VLMOracle.check() 给前端人审界面。 |
| `src/backend/routes/overrides_routes.py` | 369 | Prompt 模板管理 API（2026-07-14 重构：DB 唯一事实源） ===================================================== - prompt_templates 表是唯一事实… |
| `src/backend/routes/p0_plugins.py` | 191 | p0_plugins.py — P0 治理插件指标 API ==================================== |
| `src/backend/routes/pipeline_config/__init__.py` | 5 | 流水线配置 Blueprint — 用户级 pipeline_config 持久化。 |
| `src/backend/routes/pipeline_config/_auto_apply.py` | 245 | 切项目自动套用算法推荐值（用户偏好驱动的自动调参）。 |
| `src/backend/routes/pipeline_config/_routes.py` | 1257 | 流水线配置 API — 用户级 CRUD。 |
| `src/backend/routes/pipeline_plugins.py` | 82 | pipeline_plugins.py — 全流水线插件 API ======================================= 提供各阶段插件的注册状态查询接口。 |
| `src/backend/routes/project.py` | 73 | 项目管理路由 Blueprint（Facade 模式） ====================================== 原 monolithic 文件已按功能域拆分为子模块： - project_core.py — 项目 C… |
| `src/backend/routes/project_config/__init__.py` | 32 | project_config/__init__.py — 项目配置路由包入口 ============================================== 原 project_config.py 按功能拆分为 fetche… |
| `src/backend/routes/project_config/fetchers/__init__.py` | 11 | project_config.fetchers — 项目配置路由各功能模块 |
| `src/backend/routes/project_config/fetchers/constants.py` | 276 | 顶层函数：_rebuild_valid_keys_cache、_coerce_config_value |
| `src/backend/routes/project_config/fetchers/history.py` | 66 | project_config 历史/回滚路由: api_config_history, api_config_rollback |
| `src/backend/routes/project_config/fetchers/read.py` | 133 | project_config 只读路由: api_get_config |
| `src/backend/routes/project_config/fetchers/write.py` | 458 | project_config 写入路由: api_save_config |
| `src/backend/routes/project_config/project_config.py` | 148 | 项目配置路由 —— 配置读写、历史、回滚 |
| `src/backend/routes/project_core/__init__.py` | 18 | 项目核心路由 —— 项目管理、切换、元数据、归档/恢复 Package facade：保持外部导入路径不变 |
| `src/backend/routes/project_core/_archive.py` | 118 | 归档/恢复路由 —— 项目归档、物理删除、恢复 |
| `src/backend/routes/project_core/_meta.py` | 168 | 项目元数据 DB 操作路由 |
| `src/backend/routes/project_core/_project.py` | 340 | 项目管理路由 —— 创建、列表、切换 |
| `src/backend/routes/quality/__init__.py` | 14 | quality/__init__.py — 质量治理只读聚合 API 包入口 ================================================ 对齐 `metrics_page/__init__.py` 的… |
| `src/backend/routes/quality/quality_routes.py` | 292 | 质量治理只读聚合 API（W3 九类能力）。 |
| `src/backend/routes/quality/w3_collectors.py` | 501 | W3 九类质量治理能力 —— 只读采集器集合。 |
| `src/backend/routes/routes/__init__.py` | 8 | data/routes/ — data 路由子模块包 ================================ 从 data.py 按业务域拆分的路由端点。 每个子模块导出 register(bp) 函数，由 data.py（fa… |
| `src/backend/routes/routes/_misc.py` | 401 | 杂项路由 ======== - GET /api/stats — 实时统计数据 - GET /api/figma — Figma 数据状态（接通真实数据：MySQL 缓存 + 配额） - GET /api/intent-metrics —… |
| `src/backend/routes/routes/_plugin.py` | 174 | GET /api/plugin-metrics — Step4 插件指标 |
| `src/backend/routes/routes/_token.py` | 420 | Token 相关路由 ============== - GET /api/token-stats — Token 用量统计（当前会话 + 历史累计） - GET /api/token-dashboard — Token 历史 Dashbo… |
| `src/backend/routes/synonym_routes.py` | 276 | 同义词库管理 API (Synonym Library Blueprint) ============================================= 提供同义词库的 CRUD / 批量导入。 MySQL ``synon… |
| `src/backend/routes/system_config_api.py` | 163 | 系统级配置 API（跨项目共享） ============================ GET /api/system-config — 读取全部系统配置 POST /api/system-config — 更新系统配置（批量 key… |
| `src/backend/routes/version.py` | 523 | 项目版本管理路由 Blueprint =========================== 版本 CRUD + 激活切换 + 版本需求快照查询。 |
| `src/backend/state.py` | 751 | 全局状态管理模块 ================ 从 app.py 提取，所有模块级全局变量和线程安全的 getter/setter。 |

## src/bundle　（1 个文件 / 16 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/bundle/entry_main.py` | 16 | 引擎后端 PyInstaller 入口（绿色自包含目录用）。 |

## src/calibration　（3 个文件 / 420 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/calibration/__init__.py` | 16 | P1-6: 校准环模块 — 人改回流 + 主动学习样本管理。 |
| `src/calibration/sampler.py` | 204 | P1-6: 校准样本采样器 — 人改回流到 calibration_samples 表。 |
| `src/calibration/trainer.py` | 200 | P1-6: 校准统计训练器 — 从 calibration_samples 读取样本 + 计算校准指标。 |

## src/core　（69 个文件 / 12123 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/core/__init__.py` | 3 | （无模块文档串） |
| `src/core/_auto_tune.py` | 377 | 流水线自动调参模块（从 pipeline_config.py 拆分，§4.3 单文件≤800行）。 |
| `src/core/_config_recommender.py` | 282 | 流水线推荐值统一算法（独立专文件，§4.3 单文件≤800行）。 |
| `src/core/case_normalizer.py` | 200 | 测试用例字段归一化工具函数 ====================== 从 ExcelHandler 类中提取的纯静态方法，处理意图动作文本清洗、标题/类型/预期结果规范化。 |
| `src/core/config_loader/__init__.py` | 252 | 全局配置加载器 (ConfigLoader) ============================== 本模块是系统的"唯一配置入口"，负责按以下优先级加载配置： |
| `src/core/config_loader/_dotenv.py` | 90 | 最小 .env 加载器（fail-open，无第三方依赖） |
| `src/core/config_loader/ai.py` | 631 | AI Provider 配置 mixin |
| `src/core/config_loader/api_docs.py` | 42 | 接口文档（YApi / Swagger）配置 mixin |
| `src/core/config_loader/base.py` | 438 | ConfigLoader 核心基础 mixin |
| `src/core/config_loader/confluence.py` | 79 | Confluence 配置 mixin |
| `src/core/config_loader/database.py` | 76 | 数据库配置 mixin |
| `src/core/config_loader/figma.py` | 39 | Figma 配置 mixin |
| `src/core/config_loader/playwright.py` | 32 | Playwright 浏览器配置 mixin |
| `src/core/config_loader/pricing.py` | 23 | 模型定价查询 mixin |
| `src/core/config_loader/project_meta.py` | 29 | 项目元信息配置 mixin |
| `src/core/config_loader/smart_session.py` | 299 | 顶层类：_ConfigSmartSessionMixin |
| `src/core/config_loader/target.py` | 253 | 顶层类：_ConfigTargetMixin |
| `src/core/config_session.py` | 143 | 顶层函数：_get_thread_local、set_current_user、get_current_user、set_session_key、clear_session_key、get_session_key |
| `src/core/config_ui.py` | 627 | 引擎参数 Web 配置界面 (Config UI) =================================== 提供 Flask Web 界面，让用户通过浏览器配置引擎参数，无需修改代码。 |
| `src/core/constants.py` | 355 | 系统级常量集中点 (v6.1) |
| `src/core/db_config.py` | 76 | 数据库配置加载模块 — 从 db_manager 拆分而来。 |
| `src/core/db_manager/__init__.py` | 98 | 统一 MySQL 连接管理器（PooledDB 版） ===================================== 使用 DBUtils.PooledDB 管理连接池，替代旧的手写线程字典池。 |
| `src/core/db_manager/_pool.py` | 96 | 顶层函数：_open、_init_pool、close_all |
| `src/core/db_manager/_utils.py` | 93 | 工具函数：线程连接追踪、连接释放、健康检查、启动 schema 初始化。 |
| `src/core/db_manager/_wrapper.py` | 181 | 连接包装器：_PooledConnectionWrapper + _AutoCloseCursor。 |
| `src/core/db_schema.py` | 285 | 数据库 Schema 初始化模块 — 从 db_manager 拆分而来。 |
| `src/core/db_security.py` | 127 | SQL 安全模块 — 表名白名单 + 列名校验 — 从 db_manager 拆分而来。 |
| `src/core/excel_cases/__init__.py` | 62 | 测试用例表 CRUD 工具函数 (Package Facade) ========================================== 从 ExcelHandler 类中提取的测试用例表操作方法，转为独立函数。 所有方法通… |
| `src/core/excel_cases/_delete.py` | 36 | 测试用例表 删除操作 |
| `src/core/excel_cases/_read.py` | 75 | 测试用例表 读取操作 |
| `src/core/excel_cases/_update.py` | 179 | 测试用例表 更新操作 |
| `src/core/excel_cases/_write.py` | 221 | 顶层函数：write_test_cases、init_test_cases_file、append_intents、parse_and_write_intents、write_case_meta |
| `src/core/excel_handler.py` | 647 | 顶层类：ExcelHandler、ExcelHandlerRegistry |
| `src/core/excel_io_utils.py` | 86 | Excel I/O 工具函数（导入辅助 + 导出保存） ========================================== 从 ExcelHandler 类中提取的静态方法，仅依赖 openpyxl，无 self 状态。… |
| `src/core/excel_normalizer.py` | 73 | Excel 测试用例归一化工具函数 ============================ 从 ExcelHandler 类中提取的归一化方法，处理意图数据规范化、schema 保证、模块查找。 |
| `src/core/excel_requirements.py` | 222 | 需求拆解表 CRUD 工具函数 ========================== 从 ExcelHandler 类中提取的需求拆解表操作方法，转为独立函数。 所有方法通过 requirement_store 操作 MySQL，不直接操… |
| `src/core/excel_utils.py` | 163 | Excel 通用 Sheet I/O 工具函数 ============================== 从 ExcelHandler 类中提取的纯静态方法，不依赖类实例。 用于 _create_sheet / _write_data… |
| `src/core/exceptions.py` | 78 | KEEP-34: 可恢复/不可恢复异常体系。 |
| `src/core/failure_analyzer.py` | 187 | 顶层函数：_rag_failure_analysis、_auto_failure_analysis、mark_flaky |
| `src/core/guarded.py` | 38 | 防御原语 —— best-effort 段落的统一收口。 |
| `src/core/i18n.py` | 177 | 国际化 / 本地化基础支持（F6）。 |
| `src/core/known_tables.py` | 12 | 引擎自身逻辑表名唯一事实源 —— 清理/权限校验共用，勿在下游重抄。 |
| `src/core/log_utils.py` | 22 | 顶层函数：fmt_log |
| `src/core/offline_store.py` | 269 | 离线兜底存储（文件持久化 SQLite）+ MySQL 恢复后自动回放同步（Outbox 模式）。 |
| `src/core/paths/__init__.py` | 68 | 统一路径管理模块 (ProjectPaths) =============================== 集中管理项目下所有文件/目录路径，避免各模块硬编码路径。 |
| `src/core/paths/analysis_cache.py` | 95 | 智能分析 + 运行缓存 + 项目配置目录 — 从 paths.py 拆出，零业务逻辑变更。 |
| `src/core/paths/legacy_aliases.py` | 30 | 仍在使用的属性（raw_figma_cache_db / process_figma_change_log_by_version）— 从 paths.py 拆出。 |
| `src/core/paths/process_result.py` | 71 | 过程产物 + 测试结果目录 — 从 paths.py 拆出，零业务逻辑变更。 |
| `src/core/paths/raw_dirs.py` | 126 | 原始数据目录（Confluence/Figma/YApi/DB/目标系统）— 从 paths.py 拆出，零业务逻辑变更。 |
| `src/core/pipeline_config.py` | 982 | 流水线集中配置入口。 |
| `src/core/pipeline_config_selectors.py` | 131 | 流水线 DOM 选择器集中默认值。 |
| `src/core/prompt_seeds.py` | 200 | Prompt 模板种子默认值（DB 唯一事实源的单一源） ================================================= 5 个种子模板的默认文本。DB 初始化时由 PromptTemplateMana… |
| `src/core/prompt_templates.py` | 170 | Prompt 模板集中管理（2026-07-14 重构：DB 唯一事实源） |
| `src/core/semantic_extractor.py` | 231 | semantic_extractor.py — AI 语义提取：需求条目 → 控件组合意图 |
| `src/core/session_context/__init__.py` | 49 | 会话上下文管理器 (SessionContext) ================================== 核心职责： 1. 跟踪浏览器会话状态（是否已登录、当前用户/角色、当前页面） 2. 按流水线阶段分级提供系统知识： … |
| `src/core/session_context/db_meta.py` | 601 | 顶层类：SessionDbMetaMixin |
| `src/core/session_context/knowledge.py` | 110 | SessionKnowledgeMixin - 流水线知识注入 ================================== 负责：按流水线阶段分级提供系统知识（Step2/Step3/Step4）、 TraceabilityEn… |
| `src/core/session_context/state.py` | 180 | SessionStateMixin - 会话状态管理 ================================== 负责：浏览器会话状态跟踪、登录检测、缓存管理。 |
| `src/core/severity.py` | 5 | 严重级序唯一事实源 —— security/a11y 报告共用，勿在下游重抄。 |
| `src/core/structured_logging.py` | 120 | KEEP-33: 结构化日志基础设施。 |
| `src/core/system_config_schema.py` | 275 | 系统配置归一化分类 —— 前后端对齐的单一事实源。 |
| `src/core/utils/__init__.py` | 1 | （无模块文档串） |
| `src/core/utils/num.py` | 95 | 数值容错工具 —— 全仓 ``_safe_float`` 的**单一真源**（整改 B3.1）。 |
| `src/core/utils/per_project_registry.py` | 154 | PerProjectRegistry — 通用按 project 隔离的单例注册表 ===================================================== |
| `src/core/utils/redact.py` | 106 | 通用敏感信息脱敏（单一真源，禁止各处另写）。 |
| `src/core/utils/text_utils.py` | 68 | 统一文本处理工具 — 关键词提取、字符串清理等。 |
| `src/core/utils/yapi_utils.py` | 157 | 顶层函数：parse_yapi_endpoints、extract_api_list、extract_response_schemas、_parse_res_body |
| `src/core/version_baseline.py` | 106 | 版本基线复制。 |
| `src/core/versioning.py` | 219 | 项目版本上下文工具。 |

## src/eval　（3 个文件 / 613 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/eval/__init__.py` | 3 | P2-12 SelfEval — 引擎自评估（golden_set + 评估运行器）。 |
| `src/eval/golden.py` | 316 | P2-12 GoldenSetStore — 自评估黄金集存储（CRUD）。 |
| `src/eval/runner.py` | 294 | P2-12 SelfEvalRunner — 引擎自评估运行器。 |

## src/extensions　（176 个文件 / 31839 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/extensions/__init__.py` | 1 | （无模块文档串） |
| `src/extensions/a11y/__init__.py` | 23 | 可访问性（a11y）检查能力 — 通用规则驱动，不依赖任何靶子系统。 |
| `src/extensions/a11y/checker.py` | 429 | 可访问性（a11y）检查器 — 通用规则驱动（无第三方依赖，无 AI 依赖）。 |
| `src/extensions/change_impact_kb/__init__.py` | 25 | 变更影响面 → 回归范围 知识库（零配置可用）。 |
| `src/extensions/change_impact_kb/kb.py` | 258 | 变更影响面 → 回归范围 知识库核心逻辑。 |
| `src/extensions/change_impact_kb/report_section.py` | 117 | 变更影响→回归范围 知识库：Step5 报告区块。 |
| `src/extensions/confidence_calculator.py` | 122 | 置信度计算器 (ConfidenceCalculator) — facade ===================================== 基于多因子计算测试用例执行结果的可信度。 |
| `src/extensions/confidence_calculator_mixins/__init__.py` | 16 | 置信度计算器 mixins 包 ===================================== ``confidence_calculator.py`` facade 的内部实现拆分。 |
| `src/extensions/confidence_calculator_mixins/_block.py` | 157 | 置信度计算器 —— 拦截判定 mixin ===================================== 低置信度拦截 + 批量扫描 + 批量置信度计算。从 ``confidence_calculator.py`` 拆分而来，… |
| `src/extensions/confidence_calculator_mixins/_calculate.py` | 459 | 置信度计算器 —— 计算核心 mixin ===================================== 权重管理 + 单条测试用例置信度计算。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不… |
| `src/extensions/confidence_calculator_mixins/_feedback.py` | 195 | 置信度计算器 —— QA 反馈闭环 mixin ===================================== QA 标注反馈记录 + 统计 + 权重自动校准。从 ``confidence_calculator.py`` 拆分… |
| `src/extensions/confidence_calculator_mixins/_meta.py` | 42 | 置信度计算器 —— 元类与描述符 ===================================== 提供 ``_DualMethod``（类/实例双态调用描述符）与 ``_ConfidenceMeta`` （``WEIGHTS`… |
| `src/extensions/confidence_calculator_mixins/_trend.py` | 107 | 置信度计算器 —— 趋势告警 mixin ===================================== 跨版本置信度趋势检测（下降 >15% 自动告警）。从 ``confidence_calculator.py`` 拆分而来… |
| `src/extensions/coverage_tracker/__init__.py` | 92 | 需求→测试用例覆盖追踪器 (CoverageTracker) — v6（包入口） ========================================================== 在 Step3 测试用例生成完成后，对… |
| `src/extensions/coverage_tracker/evaluator.py` | 310 | 覆盖维度评估 mixin — 从 coverage_tracker.py 拆出，零逻辑变更。 |
| `src/extensions/coverage_tracker/gap_filler.py` | 167 | 缺口自动补全 + Markdown 报告章节 — 从 coverage_tracker.py 拆出，零逻辑变更。 |
| `src/extensions/coverage_tracker/keywords.py` | 67 | CoverageTracker 用到的关键词常量 — 从 coverage_tracker.py 拆出，零内容变更。 |
| `src/extensions/coverage_tracker/persistence.py` | 88 | 顶层类：_PersistenceMixin |
| `src/extensions/db/__init__.py` | 1 | （无模块文档串） |
| `src/extensions/db/db_adapters.py` | 27 | 数据库适配器 Mixin ================== 包含所有 DB 类型特定的连接、查询、语法差异方法。 DatabaseAgent 通过多重继承整合此类。 |
| `src/extensions/db/db_adapters_mixins/__init__.py` | 17 | db_adapters_mixins ================== DbAdapterMixin 的拆分实现（facade + mixins 模式）。 |
| `src/extensions/db/db_adapters_mixins/_connect.py` | 120 | 连接 Mixin ========== 包含所有 DB 类型特定的连接方法。 |
| `src/extensions/db/db_adapters_mixins/_execute.py` | 298 | 顶层类：DbAdapterExecuteMixin |
| `src/extensions/db/db_adapters_mixins/_schema.py` | 91 | Schema 操作 Mixin ================= 包含表存在性检查、FLASHBACK 回滚等 Schema 相关方法。 |
| `src/extensions/db/db_agent/__init__.py` | 62 | 顶层类：DatabaseAgent、_ThreadSafeDbAgent |
| `src/extensions/db/db_agent/cleanup.py` | 150 | 顶层类：DatabaseAgentCleanupMixin |
| `src/extensions/db/db_agent/connection.py` | 177 | 顶层类：DatabaseAgentConnectionMixin |
| `src/extensions/db/db_agent/insert.py` | 129 | 顶层类：DatabaseAgentInsertMixin |
| `src/extensions/db/db_agent/schema.py` | 232 | 顶层类：DatabaseAgentSchemaMixin |
| `src/extensions/db/db_agent/test_data.py` | 127 | 顶层类：DatabaseAgentTestDataMixin |
| `src/extensions/db/db_core/__init__.py` | 67 | 通用数据库操作工具 (DbExecutor) =============================== 核心设计：彻底解耦业务逻辑，通过 LLM 将自然语言意图自动转换为 SQL， 避免为每个测试用例写硬编码的数据准备脚本。 |
| `src/extensions/db/db_core/base.py` | 53 | DbCoreBaseMixin: __init__, reload_config, read_schema_file |
| `src/extensions/db/db_core/execute.py` | 344 | DbCoreExecuteMixin: _execute_sql + 5 个数据库实现 |
| `src/extensions/db/db_core/generate.py` | 107 | DbCoreGenerateMixin: _generate_sql — LLM SQL 生成（含大段 prompt 模板） |
| `src/extensions/db/db_core/orchestrate.py` | 125 | 顶层类：_DbCoreOrchestrateMixin |
| `src/extensions/db/db_core/validate.py` | 54 | DbCoreValidateMixin: _validate_sql — SQL 安全校验 |
| `src/extensions/db/table_patterns_config.py` | 165 | 表模式推断配置 (TablePatternsConfig) ===================================== 将硬编码的表模式推断改为配置驱动，支持不同项目。 |
| `src/extensions/deployment/__init__.py` | 9 | 部署文档匹配 包 ============== parser: 数据类 + 文档解析 matcher: 语义匹配 + 报告 preview: 轻量预览 |
| `src/extensions/deployment/matcher/__init__.py` | 137 | 部署文档 匹配模块 ================ DeploymentMatcher 类 + 两阶段语义匹配 + 报告格式化。 |
| `src/extensions/deployment/matcher/base.py` | 127 | 顶层类：_MatcherBaseMixin |
| `src/extensions/deployment/matcher/orchestrate.py` | 283 | 顶层类：_MatcherOrchestrateMixin |
| `src/extensions/deployment/matcher/phase1.py` | 179 | 顶层类：_MatcherPhase1Mixin |
| `src/extensions/deployment/matcher/phase2.py` | 147 | Matcher Phase2 Mixin: 功能点语义匹配 |
| `src/extensions/deployment/parser/__init__.py` | 61 | 部署文档 解析模块 ================ 数据类 + 文档解析 + 表格解析 + 版本过滤 + 静态工具函数。 |
| `src/extensions/deployment/parser/_models.py` | 81 | parser._models — 数据结构 + 常量 ================================ DeploymentRow, MatchDetail, FeatureMatchResult, MatchReport… |
| `src/extensions/deployment/parser/_section_parser.py` | 120 | 顶层函数：parse_markdown_sections、detect_transfer_scope_section |
| `src/extensions/deployment/parser/_table_parser.py` | 95 | parser._table_parser — 表格解析 + 版本过滤 =========================================== parse_table, filter_by_version, list_ver… |
| `src/extensions/deployment/parser/_utils.py` | 35 | parser._utils — 静态工具方法 ============================ extract_keywords_static, cosine_sim_static |
| `src/extensions/deployment/preview.py` | 54 | 部署文档 预览模块 ================ 轻量预览：解析文档中的转测范围表格，返回版本列表和功能点概览。 不做语义匹配，不需要 embedding。前端用它展示版本下拉框。 |
| `src/extensions/deployment_matcher.py` | 37 | 部署文档 → 测试用例匹配 模块（兼容层） =============================== 此文件已拆分为 src/extensions/deployment/ 包。 保留此文件以确保向后兼容 — 所有导入路径不变。 |
| `src/extensions/diff_engine/__init__.py` | 37 | 变更对比引擎 (DiffEngine) + 影响分析器 (ImpactAnalyzer) — v4.0（包入口） ==============================================================… |
| `src/extensions/diff_engine/engine.py` | 421 | 顶层类：DiffEngine |
| `src/extensions/diff_engine/impact.py` | 168 | ImpactAnalyzer — 基于 TraceLink 交叉分析变更影响。从 diff_engine.py 拆出，零逻辑变更。 |
| `src/extensions/diff_engine/models.py` | 87 | DiffEngine / ImpactAnalyzer 的数据模型 — 从 diff_engine.py 拆出，零内容变更。 |
| `src/extensions/evidence_manager.py` | 331 | C5 执行证据留存 — ExecutionEvidenceManager ============================================= |
| `src/extensions/health_checker.py` | 90 | 外部依赖健康检查器 (HealthChecker) ==================================== 轻量级、非阻塞地检查所有外部依赖状态。 每个检查返回统一结构：{name, status, latency_ms… |
| `src/extensions/health_checker_mixins/__init__.py` | 3 | health_checker_mixins — 健康检查混入模块 |
| `src/extensions/health_checker_mixins/_ai.py` | 138 | AI 服务健康检查 mixin。 |
| `src/extensions/health_checker_mixins/_base.py` | 75 | HealthChecker 基础 mixin（单例、缓存、结果结构、批量检查）。 |
| `src/extensions/health_checker_mixins/_external.py` | 125 | 外部服务健康检查 mixin。 |
| `src/extensions/health_checker_mixins/_system.py` | 51 | 系统资源健康检查 mixin。 |
| `src/extensions/metrics_collector/__init__.py` | 67 | AI 健康度指标收集器 (MetricsCollector) ====================================== 单例模式，即时写入 MySQL（immediate=True 降级到缓冲）。 在流水线各步骤埋点收… |
| `src/extensions/metrics_collector/health.py` | 1670 | 顶层类：_RecordHealthMixin |
| `src/extensions/metrics_collector/persistence.py` | 529 | 持久化（_insert_single / flush_run / flush_all / _insert_batch）+ 置信度趋势 — 从 metrics_collector.py 拆出。 |
| `src/extensions/metrics_collector/plugins.py` | 312 | 插件指标埋点（capability / fuzzy_match / dom_sizer / region_tree / click_fallback / dom_diff）— 从 metrics_collector.py 拆出，零业务逻辑… |
| `src/extensions/metrics_collector/prometheus.py` | 193 | Prometheus 指标导出 mixin — 从 metrics_collector.py 拆出，零业务逻辑变更。 |
| `src/extensions/perf_baseline/__init__.py` | 126 | 性能基线与门禁（perf_gate）— 采集 → 暂存 → 基线 → 门禁 → 报告。 |
| `src/extensions/perf_baseline/baseline.py` | 326 | 性能基线存储 — 按靶子分文件、多指标聚合、原子写、可重置。 |
| `src/extensions/perf_baseline/collector.py` | 398 | 性能指标采集 — Web（Playwright）+ API（ApiTracker）→ :class:`PerfSample`。 |
| `src/extensions/perf_baseline/config.py` | 265 | 性能门禁规则加载 — ``perf_rules.yaml`` 深合并（唯一配置源）。 |
| `src/extensions/perf_baseline/context.py` | 183 | 性能采集暂存 — step4 采集 → step5 消费的跨阶段传递通道。 |
| `src/extensions/perf_baseline/detector.py` | 80 | 性能回归检测器 — 实时检测性能退化 |
| `src/extensions/perf_baseline/gate.py` | 291 | 性能门禁判定 — 全链路唯一判定入口（任何地方不得重复实现判定逻辑）。 |
| `src/extensions/perf_baseline/regression.py` | 562 | 性能回归对比 — 跨运行门禁结果对比，检测性能退化与改进。 |
| `src/extensions/perf_baseline/report.py` | 347 | 性能门禁报告 — 结构化 JSON + Markdown 可读摘要。 |
| `src/extensions/perf_baseline/schemas.py` | 376 | 性能门禁契约层 — 数据结构 + key 归一化（唯一单点实现）。 |
| `src/extensions/perf_baseline/tracker.py` | 110 | 性能追踪器 — 记录每条测试用例的执行时间 |
| `src/extensions/perf_baseline/trend.py` | 146 | 趋势分析器 — 分析执行时间变化趋势 |
| `src/extensions/quality/__init__.py` | 1 | （无模块文档串） |
| `src/extensions/quality/baseline_health.py` | 247 | 基准健康度检查器 (BaselineHealthChecker) ========================================== P1: 检查Flash校验和交叉校验的基准数据是否就绪。 防止"无基准乱校验"和"… |
| `src/extensions/quality/case_validator/__init__.py` | 31 | Intent Validator - 测试用例校验器 |
| `src/extensions/quality/case_validator/ai_check.py` | 90 | Intent Validator - AI 深度校验（使用 Flash 模型） |
| `src/extensions/quality/case_validator/constants.py` | 47 | Intent Validator - 常量与配置 |
| `src/extensions/quality/case_validator/cross_check/__init__.py` | 27 | Intent Validator - P0 防幻觉：交叉校验（Package Facade） |
| `src/extensions/quality/case_validator/cross_check/_action.py` | 231 | Intent Validator - P0 防幻觉：Action 类型确定性校验 (v3.0) =========================================================== 从 actions[{… |
| `src/extensions/quality/case_validator/cross_check/_api.py` | 183 | Intent Validator - P0 防幻觉：API 路径确定性校验 (v3.0) ======================================================== 从 checks[{track=a… |
| `src/extensions/quality/case_validator/cross_check/_data.py` | 162 | Intent Validator - P0 防幻觉：交叉校验数据加载 |
| `src/extensions/quality/case_validator/cross_check/_db_trace.py` | 223 | Intent Validator - P0 防幻觉：DB 表/字段确定性校验 (v3.0) ========================================================== 从 checks[{trac… |
| `src/extensions/quality/case_validator/cross_check/_enum.py` | 96 | Intent Validator - P0 防幻觉：参数枚举值确定性校验 (v3.0) ========================================================== 从 checks[].expec… |
| `src/extensions/quality/case_validator/cross_check/_runner.py` | 73 | Intent Validator - P0 防幻觉：交叉校验汇总执行 (v3.0) ====================================================== v3.0 关键变化: - 所有子校验从 ch… |
| `src/extensions/quality/case_validator/cross_check/_ui.py` | 202 | Intent Validator - P0 防幻觉：UI 元素确定性校验 (v3.0) ======================================================== 从 checks[{track=ui… |
| `src/extensions/quality/case_validator/feedback.py` | 36 | Intent Validator - 校验结果反馈格式化 |
| `src/extensions/quality/case_validator/main.py` | 238 | Intent Validator - 主入口：三层校验编排 (v3.0) |
| `src/extensions/quality/case_validator/prompts.py` | 31 | Intent Validator - 提示词模板注册 |
| `src/extensions/quality/case_validator/result.py` | 37 | Intent Validator - 校验结果数据类 |
| `src/extensions/quality/case_validator/rule_check.py` | 374 | Intent Validator - 快速规则校验（纯代码，不调 AI） |
| `src/extensions/quality/case_validator/utils.py` | 52 | Intent Validator - 通用工具函数 (v3.0) |
| `src/extensions/quality/hallucination_logger/__init__.py` | 38 | P2-2: 幻觉案例记录与分析器 (HallucinationLogger) ==================================================== 执行失败的测试用例中，自动分类失败原因——"环境问题"… |
| `src/extensions/quality/hallucination_logger/_analyzer.py` | 99 | 分析与规则更新 Mixin。 |
| `src/extensions/quality/hallucination_logger/_classifier.py` | 65 | 失败原因分类 Mixin。 |
| `src/extensions/quality/hallucination_logger/_cluster.py` | 249 | 失败测试用例聚类与反馈 Mixin。 |
| `src/extensions/quality/hallucination_logger/_feedback.py` | 161 | P2-3: 闭环反馈 — 历史失败率查询 & Prompt注入 ================================================ 基于 hallucination_logger 沉淀的失败样本，查询需求/模… |
| `src/extensions/quality/hallucination_logger/_logger.py` | 102 | 顶层类：HallucinationLogger |
| `src/extensions/quality/hallucination_logger/_persistence.py` | 78 | 样本持久化 Mixin。 |
| `src/extensions/quality/requirement_gate/__init__.py` | 35 | 需求文档门禁 (RequirementGate) — Facade ====================================== 采用 mixin 多继承模式，实际逻辑分布在 requirement_gate_mixins… |
| `src/extensions/quality/requirement_gate/base.py` | 124 | 门禁基础 Mixin 包含：__init__, _load_merged_requirements |
| `src/extensions/quality/requirement_gate/check.py` | 262 | 门禁检查 Mixin (Facade) 通过多继承组合拆解、意图、持久化三个子 mixin，并保留共享的匹配算法。 |
| `src/extensions/quality/requirement_gate/check_mixins/__init__.py` | 13 | check_mixins —— 门禁检查拆分后的 mixin 子模块。 |
| `src/extensions/quality/requirement_gate/check_mixins/_decomposition.py` | 210 | 门禁1：需求拆解后门禁 Mixin 包含：check_decomposition |
| `src/extensions/quality/requirement_gate/check_mixins/_intents.py` | 216 | 门禁2：测试用例生成后门禁 Mixin 包含：check_intents |
| `src/extensions/quality/requirement_gate/check_mixins/_persist.py` | 55 | 门禁3：持久化 Mixin 包含：persist_scores |
| `src/extensions/quality/requirement_gate/constants.py` | 301 | 门禁常量 Mixin + GateResult 数据类 包含：阈值参数、同义词映射、否定词列表、MySQL 同义词加载 |
| `src/extensions/quality/risk_analyzer.py` | 625 | 顶层函数：_load_knn_samples、_try_knn_classification、_semantic_high_risk_hits、_embedding_semantic_risk、_keyword_based_risk、_l… |
| `src/extensions/quality/test_rules_kb/__init__.py` | 49 | 顶层类：TestRulesKB |
| `src/extensions/quality/test_rules_kb/default_rules.py` | 168 | 默认测试规则 + 全局约束（内置常量）— 从 test_rules_kb.py 拆出，零业务逻辑变更。 |
| `src/extensions/quality/test_rules_kb/formatting.py` | 116 | 禁止词列表 + Prompt 上下文格式化 — 从 test_rules_kb.py 拆出，零业务逻辑变更。 |
| `src/extensions/quality/test_rules_kb/loader.py` | 190 | 规则加载（从 MySQL test_rules 表）+ 自动发现规则保存 — 从 test_rules_kb.py 拆出，零业务逻辑变更。 |
| `src/extensions/quality/test_rules_kb/matching.py` | 133 | 规则匹配 + 命中率统计 — 从 test_rules_kb.py 拆出，零业务逻辑变更。 |
| `src/extensions/rag_engine/__init__.py` | 9 | RAG 引擎包 —— 向后兼容的重新导出 |
| `src/extensions/rag_engine/_index_builders.py` | 647 | RAG 索引构建器模块 — 从 engine_core.py 拆分（§4.3 单文件≤800行）。 |
| `src/extensions/rag_engine/chunking_mixin.py` | 28 | 文档分块、格式化、关键词提取、去重、智能分段 |
| `src/extensions/rag_engine/chunking_mixin_mixins/__init__.py` | 3 | chunking_mixin_mixins — RAG 分块混入子模块 |
| `src/extensions/rag_engine/chunking_mixin_mixins/_dedup.py` | 118 | 去重 mixin |
| `src/extensions/rag_engine/chunking_mixin_mixins/_format.py` | 140 | 格式化 mixin |
| `src/extensions/rag_engine/chunking_mixin_mixins/_keywords.py` | 55 | 关键词提取 mixin |
| `src/extensions/rag_engine/chunking_mixin_mixins/_parse.py` | 473 | 解析 mixin |
| `src/extensions/rag_engine/chunking_mixin_mixins/_segment.py` | 449 | 智能分段引擎 (Smart Segmenter) ============================== 按自然语言边界分段，禁止硬截断。 |
| `src/extensions/rag_engine/chunking_mixin_mixins/_upload.py` | 348 | 上传文件分块 mixin |
| `src/extensions/rag_engine/embedding_mixin.py` | 334 | 顶层类：EmbeddingMixin |
| `src/extensions/rag_engine/engine_core.py` | 686 | 顶层类：RagEngine |
| `src/extensions/rag_engine/entity_resolver.py` | 257 | v4: EntityResolver — 源数据精确查询层。 |
| `src/extensions/rag_engine/persistence_mixin.py` | 26 | MySQL 持久化、ChromaDB 交互、索引加载/清空 |
| `src/extensions/rag_engine/persistence_mixin_mixins/__init__.py` | 3 | persistence_mixin_mixins — RAG 持久化混入子模块 |
| `src/extensions/rag_engine/persistence_mixin_mixins/_export.py` | 11 | 导出 mixin：JSON 导出/导入（预留） |
| `src/extensions/rag_engine/persistence_mixin_mixins/_load.py` | 315 | 加载 mixin：ChromaDB / MySQL 索引加载、清空 |
| `src/extensions/rag_engine/persistence_mixin_mixins/_save.py` | 294 | 保存 mixin：MySQL 持久化（rag_chunks 表）、ChromaDB 交互 |
| `src/extensions/rag_engine/rag_filter.py` | 837 | RAG 10 层过滤管道（v1.0 — 2026-07-12） ======================================= 共享公共方法，Step3（用例生成）和 Step4（检查执行）统一调用。 |
| `src/extensions/rag_engine/rag_rerank.py` | 286 | rag_rerank.py — RAG 重排与 LLM 判断模块 ========================================== 从 rag_filter.py 拆分（§4.3 单文件≤800行红线）。 |
| `src/extensions/rag_engine/rag_usage_logger.py` | 192 | RAG 使用日志共享工具 — 供 Step2/Step3/Step4/校验器/分析器等所有 RAG 检索点共用。 |
| `src/extensions/rag_engine/search_mixin.py` | 31 | 搜索核心、query 改写、rerank、BM25 |
| `src/extensions/rag_engine/search_mixin_mixins/__init__.py` | 3 | SearchMixin 子模块拆分包。 |
| `src/extensions/rag_engine/search_mixin_mixins/_blue_red.py` | 315 | RAG 检索 — 蓝红对抗引擎 ======================== Blue Team: 语义向量/BM25关键词/Hybrid混合 三策略并行检索 Red Team: 相关性评分 + 完整性检查 + 幻觉检测（结构化） A… |
| `src/extensions/rag_engine/search_mixin_mixins/_bm25.py` | 67 | BM25 搜索 mixin。 |
| `src/extensions/rag_engine/search_mixin_mixins/_hybrid.py` | 196 | 混合搜索 (RRF) mixin。 |
| `src/extensions/rag_engine/search_mixin_mixins/_query_rewrite.py` | 186 | 顶层类：SearchQueryRewriteMixin |
| `src/extensions/rag_engine/search_mixin_mixins/_rerank.py` | 47 | Rerank mixin。 |
| `src/extensions/rag_engine/search_mixin_mixins/_search.py` | 92 | 统一搜索入口 mixin。 |
| `src/extensions/rag_engine/search_mixin_mixins/_vector.py` | 58 | 向量搜索 mixin。 |
| `src/extensions/rag_engine/semantic_renderer.py` | 195 | v4: SemanticPatternRenderer — 语义模式渲染器。 |
| `src/extensions/security_scanner/__init__.py` | 790 | 安全扫描器 — OWASP Top 10 基础检测 |
| `src/extensions/semantic_index/__init__.py` | 19 | 语义嵌入工具：实时向量生成 + 相似度计算。 无状态设计，不依赖任何存储表。向量持久化由 rag_chunks 统一承载。 |
| `src/extensions/semantic_index/_engine.py` | 188 | 顶层函数：_ensure_local_model、_get_engine、_normalize、_try_embed_ollama |
| `src/extensions/semantic_index/_index.py` | 140 | 顶层函数：embed、cosine、similarity、get_model_status |
| `src/extensions/semantic_index/_ngram.py` | 40 | 字符 n-gram 轻量级向量（捕获部分语义，比 hash 更精准）。 |
| `src/extensions/system_data_resolver/__init__.py` | 323 | 系统数据目录解析器 (SystemDataResolver) — v3.2 |
| `src/extensions/system_data_resolver/ai_fallback.py` | 57 | 顶层类：AiFallbackMixin |
| `src/extensions/system_data_resolver/cache.py` | 211 | 顶层类：CacheMixin |
| `src/extensions/system_data_resolver/constants.py` | 76 | SystemDataResolver — 常量与匹配模式定义。 |
| `src/extensions/system_data_resolver/db_access.py` | 242 | SystemDataResolver — 数据库访问（独立连接，用完即关）。 |
| `src/extensions/system_data_resolver/heuristic.py` | 166 | 顶层类：HeuristicMixin |
| `src/extensions/system_data_resolver/sampling.py` | 776 | SystemDataResolver — 样本数据提取与目录文本构建。 |
| `src/extensions/trace_validator/__init__.py` | 384 | 追溯链路校验器 (TraceValidator) ================================ 校验测试用例中的 db_checks / api_checks 是否匹配追溯链路中的表/API。 |
| `src/extensions/trace_validator/api_extractors.py` | 85 | 顶层类：_ApiAndExtractMixin |
| `src/extensions/trace_validator/db_checks.py` | 180 | 顶层类：_DbChecksMixin |
| `src/extensions/trace_validator/fuzzy_match.py` | 44 | 顶层类：_FuzzyMatchMixin |
| `src/extensions/traceability/__init__.py` | 26 | 追溯链路引擎子模块聚合入口。 |
| `src/extensions/traceability/ai_builder.py` | 243 | 追溯链路引擎 — AI 驱动的链路构建 ================================ 调用 AI 批量构建追溯链路；分批处理，每批 10 条需求。 批次 AI 调用并发执行（复用现有 thread-local 上下文传… |
| `src/extensions/traceability/coverage.py` | 199 | 顶层类：TraceabilityCoverage |
| `src/extensions/traceability/data_source.py` | 416 | 追溯链路引擎 — 数据源提取 |
| `src/extensions/traceability/engine.py` | 366 | 顶层类：TraceabilityEngine |
| `src/extensions/traceability/formatter.py` | 107 | 追溯链路引擎 — 报告格式化 ======================== 将覆盖率报告和追溯链路格式化为 Markdown 文本（注入 Step5 报告）。 |
| `src/extensions/traceability/heuristic.py` | 199 | 追溯链路引擎 — 启发式回退 ======================== 当 AI 不可用时，使用关键词启发式匹配为需求构建追溯链路。 |
| `src/extensions/traceability/models.py` | 48 | 追溯链路引擎 — 数据模型 ====================== 定义 TraceLink（单条需求追溯链路）和 CoverageReport（覆盖率报告）两个核心 dataclass。 |
| `src/extensions/traceability/persistence.py` | 149 | 顶层类：TraceabilityPersistence |
| `src/extensions/traceability/queries.py` | 68 | 追溯链路引擎 — 对外查询接口 ========================== 提供按需求、API、DB 表等维度的查询方法。 |
| `src/extensions/traceability_model.py` | 33 | 追溯链路引擎 (TraceabilityEngine) — v4.0 ====================================== 构建结构化的 REQ → API → DB → Page 追溯链路， 在 Step2（需求… |

## src/frontend　（3 个文件 / 495 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/frontend/__init__.py` | 1 | （无模块文档串） |
| `src/frontend/selftest_verify_lock.py` | 214 | verify_lock.py「悬空属性门」检查的自测。 |
| `src/frontend/verify_lock.py` | 280 | （解析失败：SyntaxError: invalid non-printable character U+FEFF） |

## src/models　（3 个文件 / 503 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/models/__init__.py` | 9 | 数据模型层 — 统一的数据访问接口 =============================== 提供： - base.py: DB 连接池 + 通用 CRUD - permission.py: 功能权限管理（按钮级访问控制） |
| `src/models/base.py` | 95 | 顶层函数：safe_add_column、get_conn、query_one、query_all、execute、execute_insert |
| `src/models/permission.py` | 399 | 功能权限管理系统（三层权限模型） =================================== 简化设计：系统级 + 项目级 + 个人级，三层正交不重叠。 |

## src/oracle　（9 个文件 / 1498 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/oracle/__init__.py` | 11 | P1-5: VLM Oracle 模块 — 视觉真值校验层。 |
| `src/oracle/correctness_vlm.py` | 368 | P1-5: VLM Oracle 视觉校验入口 — correctness 类型 oracle。 |
| `src/oracle/store.py` | 477 | P1-5: Oracle 结果持久化 — oracle_results + visual_review 表写入。 |
| `src/oracle/vlm/__init__.py` | 24 | T04.1: VLM 视觉校验统一入口 [已复核-DeepSeek-V3 2026-08-16] |
| `src/oracle/vlm/backends/__init__.py` | 3 | T04.1: 本地视觉校验后端实现 [已复核-DeepSeek-V3 2026-08-16] |
| `src/oracle/vlm/backends/local.py` | 203 | T04.1: 本地"见图转换"视觉校验后端 LocalBackend [已复核-DeepSeek-V3 2026-08-16] 纯计算、零外部依赖（PIL 可选）；无充分证据返回 NO_DATA 而非假 PASS。 |
| `src/oracle/vlm/protocol.py` | 54 | T04.1: VLM 视觉校验统一协议 [已复核-DeepSeek-V3 2026-08-16] 状态/结果/适配器 ABC，与 _verify_visual.py STATUS_* 三态语义对齐。 |
| `src/oracle/vlm/registry.py` | 71 | T04.1: VLM 后端注册与发现 [已复核-DeepSeek-V3 2026-08-16] 按 priority 排序；get_available() 返回可用列表；verify_with_fallback 依次降级。 |
| `src/oracle/vlm_adapter.py` | 287 | P1-5: VLM Provider 适配层 — 封装 Ollama 本地视觉大模型调用。 |

## src/pipeline　（616 个文件 / 175316 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/pipeline/__init__.py` | 46 | src.pipeline —— 流水线包（分层说明） |
| `src/pipeline/_nav_map_builder.py` | 613 | 多源导航地图融合构建器。 |
| `src/pipeline/_step1_helpers.py` | 147 | Step1 辅助函数：线程上下文工具 + 并行数据源包装器 + 数据清理。 |
| `src/pipeline/_step1_phases.py` | 242 | Step1 Phase 函数模块 — 从 step1.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/api_login_helpers.py` | 559 | 顶层函数：_make_http_request、_try_api_login、_kill_proc_tree、_try_playwright_login、_playwright_login_impl、_try_urllib_login |
| `src/pipeline/api_only_executor.py` | 1009 | 顶层函数：_build_url、_parse_api_assertion、_verify_single_assertion、_run_coro_sync、execute_api_only_intents |
| `src/pipeline/api_prefix_detector.py` | 361 | API 前缀自动探测器 ================== |
| `src/pipeline/datasource_registry.py` | 144 | 数据源注册表 — 通用数据源管理，支持热插拔。 |
| `src/pipeline/diagnostics/__init__.py` | 3 | 诊断中心 — 流水线自检 + 失败归因 + Step 健康快照 |
| `src/pipeline/diagnostics/self_test.py` | 495 | 流水线自检引擎 — 轻量验证每个 Step 的基础设施可用性。 |
| `src/pipeline/evidence/__init__.py` | 23 | 证据引擎 (Evidence Engine) — 通用置信度校准框架 ================================================ 核心思想: - AI 不做裁判，只做证据收集器 - 置信度 = 独立证… |
| `src/pipeline/evidence/calibration.py` | 345 | 顶层函数：_bayesian_posterior、record_calibration、get_calibration、_ensure_calibration_table |
| `src/pipeline/evidence/confidence.py` | 434 | 顶层函数：_get_default_weights、_get_confidence_thresholds、_ai_raw_confidence、_extraction_quality、_compute_evidence_strength、… |
| `src/pipeline/fetchers/__init__.py` | 15 | pipeline.fetchers — Step1 数据源拉取拆分包 |
| `src/pipeline/fetchers/base.py` | 166 | Step1DataFetcher 基础 mixin |
| `src/pipeline/fetchers/confluence.py` | 361 | Step1DataFetcher Confluence 拉取 mixin |
| `src/pipeline/fetchers/database.py` | 250 | Step1DataFetcher 数据库查询 mixin |
| `src/pipeline/fetchers/figma.py` | 465 | Step1DataFetcher Figma 拉取 mixin |
| `src/pipeline/fetchers/persist.py` | 266 | Step1DataFetcher 持久化与摘要 mixin |
| `src/pipeline/fetchers/yapi.py` | 327 | Step1DataFetcher YApi 拉取 mixin |
| `src/pipeline/full_pipeline_runner.py` | 174 | 完整 Step1→Step5 流水线非交互驱动（人机协同，审核闸门自动放行 green lane）。 |
| `src/pipeline/helpers.py` | 711 | 顶层函数：analyze_requirement_changes、build_db_context、build_rag_index、load_step2_dual_source_context、build_traceability_and… |
| `src/pipeline/helpers/__init__.py` | 36 | pipeline helpers package ======================= 从 pipeline_scheduler.py 拆分而来的独立辅助函数。 注意：实际实现在上级目录的 helpers.py 中，本包通过 i… |
| `src/pipeline/pipeline_contracts.py` | 70 | Pipeline Step 数据契约校验。 |
| `src/pipeline/plugins/__init__.py` | 39 | pipeline/plugins/ — 全流水线插件系统 ===================================== 跨阶段的插件注册与事件总线。 各阶段通过事件总线通信，插件可订阅任意阶段的事件。 |
| `src/pipeline/plugins/bootstrap.py` | 190 | 顶层函数：init_pipeline_plugins、_wrap、_register_builtin_strategies、_feature_driven_strategy_stub、get_plugin、list_plugins |
| `src/pipeline/plugins/pipeline_bus.py` | 124 | Pipeline Bus — 流水线事件总线 ============================== 跨阶段的事件发布/订阅机制。 |
| `src/pipeline/plugins/step1_datasource.py` | 97 | 顶层类：Step1DatasourcePlugin |
| `src/pipeline/plugins/step2_analyzer.py` | 141 | 顶层类：Step2AnalyzerPlugin |
| `src/pipeline/plugins/step3_generator.py` | 205 | 顶层类：Step3GeneratorPlugin |
| `src/pipeline/plugins/step5_report.py` | 250 | 顶层类：Step5ReportPlugin |
| `src/pipeline/pre_step_gate.py` | 428 | Pre-Step Gate — 步骤启动前统一准入检查 ======================================== |
| `src/pipeline/pw_login_worker.py` | 62 | pw_login_worker — Playwright 登录探测子进程 worker（根治 teardown 死锁） ===========================================================… |
| `src/pipeline/review_gate.py` | 85 | review_gate.py — 需求审核闸门 ============================= 从 scheduler.py 拆分出的独立模块，负责 Step2 后的需求拆解结果人工审核。 |
| `src/pipeline/saga.py` | 51 | Pipeline Saga 补偿机制。 |
| `src/pipeline/saga_compensators.py` | 97 | saga_compensators.py — Pipeline Saga 补偿钩子 ============================================== 从 scheduler.py 拆分出的独立模块，负责各 St… |
| `src/pipeline/scheduler.py` | 976 | 顶层类：PipelineScheduler |
| `src/pipeline/scheduler_executor.py` | 201 | scheduler_executor.py — 步骤执行器：ThreadPoolExecutor + 超时轮询。 |
| `src/pipeline/scheduler_helpers.py` | 257 | scheduler_helpers.py — 从 scheduler.py 提取的独立静态工具函数 ============================================================ 包含文档解析、关… |
| `src/pipeline/step1.py` | 771 | Step 1 — 需求与上下文拉取 ========================= |
| `src/pipeline/step1_context_loader.py` | 141 | Step1 上下文加载器 — 从 MySQL 读取 Step1 产出的 3 个上下文。 |
| `src/pipeline/step1_health_checks.py` | 177 | Step1 数据源健康检查模块。 |
| `src/pipeline/step2.py` | 1000 | Step 2: 功能清单驱动的需求拆解（v3） |
| `src/pipeline/step2_context_restore.py` | 106 | Step 2 上下文恢复函数。 从 step2.py 拆分出。 2026-06-30: _nav_context 已移除（nav_map 整条链路删除）。 |
| `src/pipeline/step2_coverage.py` | 174 | Step 2 覆盖率校验函数。 从 step2.py 拆分出。 v4.0 (2026-07-12): 适配切片驱动架构，接入 feature_driven_breakdown 实时计算。 |
| `src/pipeline/step2_hashes.py` | 59 | Step 2 哈希工具函数。 从 step2.py 拆分出。 |
| `src/pipeline/step2_helpers.py` | 252 | Step2 辅助函数：上传文档哈希 / 敏感数据检测 / 多源统计。 |
| `src/pipeline/step2_versioning.py` | 537 | Step 2 版本管理工具函数。 从 step2.py 拆分出。 |
| `src/pipeline/step3/__init__.py` | 678 | Step 3: AI 建模测试用例（含 UI 上下文 + RAG + Traceability） 从 pipeline_scheduler.py 拆分出。 |
| `src/pipeline/step3/_check_guard.py` | 647 | 检查项定稿守卫（单一咽喉点， 全量判定审计后的根因修复）。 |
| `src/pipeline/step3/_control_dom_utils.py` | 341 | _control_dom_utils.py — DOM 文本解析 + URL 规范化纯工具函数。 |
| `src/pipeline/step3/_control_validation.py` | 858 | Step3 控件存在性预验证 ======================== 通用机制：用 Step1 抓取的 DOM 快照校验 AI 生成的用例动作目标控件是否真实存在于目标页面。 - 若控件在当前页面缺失但在同基础路径的其他 tab… |
| `src/pipeline/step3/_fallback_cases.py` | 2468 | 兜底意图生成（v10）—— AI 不可用时使用，输出含基础 actions 的可执行用例。 |
| `src/pipeline/step3/_identity_assign.py` | 317 | 访问控制型用例的执行身份指派。 |
| `src/pipeline/step3/_intent_quality.py` | 68 | 顶层函数：summarize_case_modeling_quality |
| `src/pipeline/step3/_methodology_validator.py` | 434 | Step3 方法论白名单后置校验器（MethodologyValidator）—— 流水线强化专项 T2。 |
| `src/pipeline/step3/_modeling_engine.py` | 1266 | 建模引擎（v11）：并行独立意图建模 ================================ 每个需求独立 AI 调用（含独立 RAG 检索），ThreadPoolExecutor 并行执行。 |
| `src/pipeline/step3/_modeling_intents.py` | 1079 | 顶层函数：_summarize_project_context、_write_intents_to_store、_get_valid_action_names_for_cross、_generate_cross_requirement_i… |
| `src/pipeline/step3/_modeling_rag.py` | 364 | 建模引擎 - RAG 模块（从 _modeling_engine.py 拆分，单文件<=800行）。 |
| `src/pipeline/step3/_modeling_validators.py` | 565 | 建模引擎 - 验证器模块（从 _modeling_engine.py 拆分，单文件<=800行）。 |
| `src/pipeline/step3/_navigation_resolver.py` | 704 | 顶层函数：_menu_nodes_sig、_get_llm_provider、_llm_match_menu、_llm_semantic_pick、_load_menu_nodes、_match_menu |
| `src/pipeline/step3/_requirement_selector.py` | 252 | 需求选择与状态标记（从 step3.py 拆分） |
| `src/pipeline/step3/_rule_extractor.py` | 910 | 顶层函数：_project_source_dir、_examples_dir、_get_adapter、_parse_sys_path_dirs、_scan_apps_in_dir、_discover_django_projects |
| `src/pipeline/step3/_rule_extractor_actions.py` | 605 | _rule_extractor_actions — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。 |
| `src/pipeline/step3/_rule_extractor_api.py` | 793 | 顶层函数：_load_trace_links_for_req、_merge_trace_and_rag_apis、_enrich_apis_with_rag_details、_match_apis_from_rag_chunks、_par… |
| `src/pipeline/step3/_rule_extractor_buttons.py` | 531 | _rule_extractor_buttons — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。 |
| `src/pipeline/step3/_rule_extractor_checks.py` | 658 | 顶层函数：_shorten_expected_feedback、_build_ui_check_by_operation、_classify_operation_for_check、_extract_op_core_verb、_selec… |
| `src/pipeline/step3/_rule_extractor_dom.py` | 1015 | 顶层函数：_nav_dom_rows_by_prefix、_load_nav_flat_list、_load_dom_elements_for_page、_extract_searchable_data_from_dom、_find_pa… |
| `src/pipeline/step3/_rule_extractor_downgrade.py` | 195 | 顶层函数：_infer_control_type_from_operation、_infer_page_from_api_path |
| `src/pipeline/step3/_rule_extractor_fields.py` | 219 | 需求文本 → 表单字段规格 → 录入动作（单一实现 SSOT）。 |
| `src/pipeline/step3/_rule_extractor_keywords.py` | 1108 | 顶层函数：_is_system_integration_requirement、_extract_operation_keywords_v16、_match_nav_page_names_in_text、is_api_operation、… |
| `src/pipeline/step3/_rule_extractor_nav.py` | 673 | _rule_extractor_nav — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。 |
| `src/pipeline/step3/_rule_extractor_pages.py` | 552 | 顶层函数：_match_page_path、_load_page_paths_from_db、_match_best_page_for_req、_match_dom_element、_load_action_library、_select… |
| `src/pipeline/step3/_rule_extractor_utils.py` | 608 | 顶层函数：_load_figma_confirm_nodes、_build_figma_ui_checks、_extract_field_names_from_data_dict、_is_i01_case、_build_field_che… |
| `src/pipeline/step3/_rule_extractor_validate.py` | 184 | §12.2规则3 通用修复(2026-07-26): case_rules 注册表路径后置统一校验门。 |
| `src/pipeline/step3/_security.py` | 33 | 安全/脱敏工具（从 step3.py 拆分） |
| `src/pipeline/step3/_version_check.py` | 13 | 版本一致性检查（从 step3.py 拆分） |
| `src/pipeline/step3/case_rules/__init__.py` | 212 | 顶层函数：_discover_rules、_ensure_rules、get_all_rules、get_rule_meta_list、match_rule、generate_cases |
| `src/pipeline/step3/case_rules/_intent_checks.py` | 685 | 顶层类：IntentChecksMixin |
| `src/pipeline/step3/case_rules/base.py` | 2464 | 顶层类：BaseCaseRule |
| `src/pipeline/step3/case_rules/button_resolver.py` | 607 | 按钮定位辅助函数（从 case_rules/base.py 拆分，§4.3 单文件≤800行）。 |
| `src/pipeline/step3/case_rules/button_rule.py` | 208 | 按钮规则：处理 button / submit 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/checkbox_rule.py` | 131 | 多选规则：处理 checkbox 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/date_rule.py` | 155 | 日期规则：处理 date_picker / time_picker 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/form_rule.py` | 187 | 表单规则：处理多字段复合表单，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/input_rule.py` | 533 | 输入框规则：处理 input / textarea 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/modal_rule.py` | 130 | 模态框规则：处理 modal / dialog 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/navigation_rule.py` | 125 | 导航规则：处理 navigation 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/radio_rule.py` | 129 | 单选规则：处理 radio 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/search_rule.py` | 358 | 搜索规则：处理 search 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/select_rule.py` | 129 | 下拉选择规则：处理 select 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/switch_rule.py` | 123 | 切换规则：处理 switch / toggle 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/table_rule.py` | 136 | 表格规则：处理 table 控件（CRUD 操作），生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/case_rules/upload_rule.py` | 210 | 上传规则：处理 upload 控件，生成 P01/N01/B01 三胞胎。 |
| `src/pipeline/step3/project_adapters/__init__.py` | 45 | 项目适配器层 — 支持多种项目类型（Django / Flask / Spring Boot / 纯前端等）。 |
| `src/pipeline/step3/project_adapters/base.py` | 116 | 项目适配器抽象基类 — 定义 auto-discovery 引擎的通用接口。 |
| `src/pipeline/step3/project_adapters/django.py` | 410 | Django 项目适配器 — 自动发现 Django 项目结构。 |
| `src/pipeline/step3/project_adapters/generic.py` | 46 | 通用项目适配器 — 兜底适配器，适用于非 Django 项目。 |
| `src/pipeline/step3/value_generator.py` | 204 | §13.3 通用输入值生成器 — 从 yapi_structured 提取 body_params。 |
| `src/pipeline/step4_case_reader.py` | 186 | step4_case_reader.py — Step4 测试用例读取器 =============================================== 从 scheduler.py 拆分出的独立模块，负责 Step4 阶… |
| `src/pipeline/step4_executor.py` | 1353 | 顶层函数：_run_data_lifecycle_for_cases、_merge_dlc_evidence、_replace_unique_in_str、_replace_unique_in_node、_materialize_run_… |
| `src/pipeline/steps/__init__.py` | 1 | （无模块文档串） |
| `src/pipeline/steps/step1/__init__.py` | 28 | 顶层函数：fetch_all |
| `src/pipeline/steps/step1/_rule_meta.py` | 130 | Step1 文档采集与 RAG 索引 — 算法规则元信息清单（只读，供"算法规则清单"展示）。 |
| `src/pipeline/steps/step1/_smart_fetcher_format.py` | 115 | smart_figma_fetcher 的 Layer4 格式化输出子模块 =============================================== 零业务逻辑变更，仅做物理代码搬迁。 |
| `src/pipeline/steps/step1/_smart_fetcher_nodes.py` | 169 | smart_figma_fetcher 的节点提取与变更检测子模块 =============================================== 零业务逻辑变更，仅做物理代码搬迁。 |
| `src/pipeline/steps/step1/_smart_fetcher_quota.py` | 74 | smart_figma_fetcher 的配额检测子模块 ==================================== 零业务逻辑变更，仅做物理代码搬迁。 |
| `src/pipeline/steps/step1/_smart_fetcher_utils.py` | 159 | 顶层函数：extract_file_key、parse_json_response、detect_paid_token、extract_top_pages、extract_elements_flat |
| `src/pipeline/steps/step1/confluence_client/__init__.py` | 73 | Step 1: Confluence 客户端 (ConfluenceClient) ============================================ 从 Confluence 拉取需求文档的原始内容（HTML / … |
| `src/pipeline/steps/step1/confluence_client/fetch.py` | 236 | HTTP 拉取（单页 / 多页 / Markdown 输出）— 从 confluence_client.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/confluence_client/html_convert.py` | 225 | HTML→Markdown 转换主入口 + 表格/图片/链接 — 从 confluence_client.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/confluence_client/macros.py` | 197 | Confluence 宏处理 + 富文本提取 — 从 confluence_client.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/figma_cache/__init__.py` | 61 | Figma 缓存管理 + 元数据检查 — v4.0 =================================== MySQL 持久化缓存层 + headless 浏览器元数据检查（不消耗 REST API 配额）。 从 figm… |
| `src/pipeline/steps/step1/figma_cache/_auto_login.py` | 143 | Figma 自动登录（headless Playwright）。 |
| `src/pipeline/steps/step1/figma_cache/_cache_info.py` | 197 | 缓存信息摘要（仅读本地 MySQL，不消耗 API 配额） |
| `src/pipeline/steps/step1/figma_cache/_meta_check.py` | 252 | 顶层函数：check_figma_modified_time、fetch_figma_nodes、_fetch_meta_via_headless_browser、_try_fetch_meta、_parse_meta_response、… |
| `src/pipeline/steps/step1/figma_cache/base.py` | 71 | _FigmaCacheBaseMixin — 基础方法：初始化、连接、锁、建表、键生成。 |
| `src/pipeline/steps/step1/figma_cache/health.py` | 15 | _FigmaCacheHealthMixin — 缓存健康检查方法（预留）。 |
| `src/pipeline/steps/step1/figma_cache/invalidate.py` | 15 | _FigmaCacheInvalidateMixin — 缓存失效方法（预留）。 |
| `src/pipeline/steps/step1/figma_cache/read.py` | 180 | 顶层类：_FigmaCacheReadMixin |
| `src/pipeline/steps/step1/figma_cache/write.py` | 33 | _FigmaCacheWriteMixin — 写入缓存方法：set。 |
| `src/pipeline/steps/step1/figma_client.py` | 308 | Figma 客户端 (FigmaClient) — v3 MCP 版 ====================================== 底层改为通过 MCP 协议与 figma-developer-mcp 通信，彻底解决 42… |
| `src/pipeline/steps/step1/figma_hasher.py` | 70 | Figma 节点哈希模块 ================== 节点级哈希计算 + 持久化，用于变更检测。 |
| `src/pipeline/steps/step1/figma_node_cache.py` | 88 | Figma 节点缓存模块 ================== 变更日志持久化 + 缓存读取辅助函数。 |
| `src/pipeline/steps/step1/figma_parser.py` | 433 | Figma 设计稿解析器 (FigmaParser) ================================ 从 Figma REST API 返回的 document 树中提取关键 UI 结构信息， 生成可供 AI Promp… |
| `src/pipeline/steps/step1/figma_quota.py` | 226 | Figma API 配额持久化追踪 — v4.0 ========================= 追踪 Figma 免费 API 的每月调用配额（6次/月），检测 429 限流状态。 |
| `src/pipeline/steps/step1/file_parser/__init__.py` | 135 | Step1 文件上传解析器 =================== 支持 CSV / Excel / Markdown / Word / XMind / JSON / SQL / TXT 格式的需求文档解析。 |
| `src/pipeline/steps/step1/file_parser/office_formats.py` | 190 | CSV / Excel / Word 解析器 — 从 file_parser.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/file_parser/text_formats.py` | 230 | Markdown / SQL / TXT 解析器（纯文本类）— 从 file_parser.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/file_parser/tree_formats.py` | 158 | XMind / JSON 解析器（树状/结构化数据）— 从 file_parser.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step1/mcp/__init__.py` | 1 | （无模块文档串） |
| `src/pipeline/steps/step1/mcp/confluence_mcp/__init__.py` | 58 | Confluence MCP 客户端 (package 模式) ==================================== facade，重导出 ConfluenceMCPClient / FileModeClient / … |
| `src/pipeline/steps/step1/mcp/confluence_mcp/_file_mode.py` | 138 | Confluence FileMode 客户端 (FileModeClient) =========================================== 本地文件模式客户端：读取 CONFLUENCE_URL 指定的本地文… |
| `src/pipeline/steps/step1/mcp/confluence_mcp/_mcp_client.py` | 344 | 顶层类：ConfluenceMCPClient |
| `src/pipeline/steps/step1/mcp/figma_mcp/__init__.py` | 68 | Figma MCP 客户端 — v4.0 (package 模式) ====================================== 通过 MCP 协议与 figma-developer-mcp 通信。 |
| `src/pipeline/steps/step1/mcp/figma_mcp/figma_client.py` | 465 | 顶层类：FigmaMCPClient |
| `src/pipeline/steps/step1/mcp/figma_mcp/mcp_stdio.py` | 303 | MCPStdioClient — JSON-RPC 2.0 通过 stdio 与 MCP Server 通信 |
| `src/pipeline/steps/step1/mcp/figma_mcp/session.py` | 160 | 顶层函数：_build_env、start_mcp_server、_try_start_session、stop_mcp_server、get_mcp_session |
| `src/pipeline/steps/step1/multi_source_features.py` | 434 | 顶层函数：_save_features_to_db、extract_features_from_figma、_extract_from_figma_nodes、_extract_from_figma_pages、_infer_figma_… |
| `src/pipeline/steps/step1/prototype_recognizer.py` | 354 | 原型图视觉识别模块 ================== 从需求文档中提取图片，调用视觉模型识别 UI 结构，输出结构化 JSON。 |
| `src/pipeline/steps/step1/requirement_merger.py` | 203 | 顶层类：SourceDocument、RequirementMerger |
| `src/pipeline/steps/step1/smart_figma_fetcher/__init__.py` | 135 | 智能分层 Figma 拉取器 (SmartFigmaFetcher) — v3.0（包入口 facade） ========================================================== 四层智能拉取… |
| `src/pipeline/steps/step1/smart_figma_fetcher/_entry.py` | 374 | 入口方法 fetch_smart — 从 __init__.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step1/smart_figma_fetcher/_layer1.py` | 98 | 顶层类：_Layer1Mixin |
| `src/pipeline/steps/step1/smart_figma_fetcher/_layer2.py` | 32 | Layer 2: 全量保留 — 不再做 AI 筛选（原红蓝对抗已移除）。 |
| `src/pipeline/steps/step1/smart_figma_fetcher/_layer3.py` | 130 | Layer 3: 批量拉取 + 节点级变更检测 — 从 __init__.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step1/smart_figma_fetcher/figma_quota.py` | 78 | 顶层函数：check_batch_quota |
| `src/pipeline/steps/step1/smart_figma_fetcher/incremental.py` | 419 | 增量拉取 + 全量回退 + 缓存模式 — 从 smart_figma_fetcher.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step1/smart_figma_fetcher/persistence.py` | 67 | 节点哈希 / 变更日志 持久化 — 从 smart_figma_fetcher.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step1/smart_figma_fetcher/version_aware.py` | 72 | 版本感知 API（set_version + inherit_hashes_from） — 从 smart_figma_fetcher.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step1/yapi_client.py` | 454 | 顶层类：YApiClient |
| `src/pipeline/steps/step2/__init__.py` | 3 | （无模块文档串） |
| `src/pipeline/steps/step2/ambiguity_report.py` | 600 | 需求歧义主动上报（A1，L2→L3）—— 生成用例前主动产出结构化歧义报告。 |
| `src/pipeline/steps/step2/analyzers/__init__.py` | 5 | analyzers 子包: 需求分析各职责模块 |
| `src/pipeline/steps/step2/analyzers/_contradiction.py` | 357 | 需求矛盾检测（纯算法，零 AI 调用）。 |
| `src/pipeline/steps/step2/analyzers/_fd_chunking.py` | 585 | 智能切片 + 多技术融合相似度计算（纯算法）。提取自 feature_driven.py。 |
| `src/pipeline/steps/step2/analyzers/_fd_completeness.py` | 504 | 需求完整性检查（纯算法）。提取自 feature_driven.py。 |
| `src/pipeline/steps/step2/analyzers/_fd_extraction.py` | 1136 | 三元组提取（规则优先 + AI 兜底）。提取自 feature_driven.py。 |
| `src/pipeline/steps/step2/analyzers/_fd_labels.py` | 413 | 功能标签（纯算法聚类）+ 切片持久化 + 完整性检查入口。提取自 feature_driven.py。 |
| `src/pipeline/steps/step2/analyzers/_fd_preprocess.py` | 255 | Step2 预处理层：文本清洗、表格提取、URL 处理、module 净化。 |
| `src/pipeline/steps/step2/analyzers/_fd_semantic_dedup.py` | 550 | 功能点语义聚合去重（semantic_merge_features）—— 流水线强化专项 T4。 |
| `src/pipeline/steps/step2/analyzers/_fd_theme_gate.py` | 304 | 主题一致性门禁（Theme Consistency Gate）—— 消除需求拆解"偏离整体需求"的增量守卫。 |
| `src/pipeline/steps/step2/analyzers/_fd_verification.py` | 414 | 5a 子串匹配 + 去重 + 逆向覆盖检查。提取自 feature_driven.py。 |
| `src/pipeline/steps/step2/analyzers/_guard_injection.py` | 117 | Step2 闭环反馈 + 幻觉护栏规则注入。 |
| `src/pipeline/steps/step2/analyzers/confidence_scorer.py` | 281 | 顶层函数：_extract_2gram_tokens、_jaccard、_compute_uniqueness、compute_confidence_scores |
| `src/pipeline/steps/step2/analyzers/constants.py` | 108 | 模块级常量（消除魔法数字）与 Prompt 模板 |
| `src/pipeline/steps/step2/analyzers/feature_driven.py` | 835 | 需求文档切片驱动的需求拆解（v4 — 切片驱动，事实源是需求文档） |
| `src/pipeline/steps/step2/analyzers/single_pass.py` | 46 | UI 功能点摘要提取（从 single_pass 拆分，保留 step1_context_loader 依赖） |
| `src/pipeline/steps/step2/breakdown_rules/__init__.py` | 240 | 需求拆解规则模块 — 规则优先，AI 兜底。 |
| `src/pipeline/steps/step2/breakdown_rules/ambiguity_detection.py` | 81 | 需求歧义检测规则（A1）：校验阶段适配器 —— 把歧义检测接入 run_check 流水线。 |
| `src/pipeline/steps/step2/breakdown_rules/base.py` | 116 | 需求拆解规则基类。 |
| `src/pipeline/steps/step2/breakdown_rules/boundary_rules.py` | 162 | 边界条件识别规则：校验阶段，识别需求中缺失的边界条件。 |
| `src/pipeline/steps/step2/breakdown_rules/completeness.py` | 129 | 完整性检查规则：校验阶段，检查单条需求的完整性。 |
| `src/pipeline/steps/step2/breakdown_rules/conditional_statement.py` | 87 | 条件句式提取规则：提取阶段，匹配"当...则..."等条件句式。 |
| `src/pipeline/steps/step2/breakdown_rules/dedup.py` | 37 | 去重合并规则：清洗阶段，按 (operation, expected_feedback) 去重。 |
| `src/pipeline/steps/step2/breakdown_rules/feature_description.py` | 88 | 功能描述提取规则：提取阶段，匹配"支持XX功能"、"提供XX能力"等功能描述句式。 |
| `src/pipeline/steps/step2/breakdown_rules/feature_label.py` | 64 | 功能标签规则：标签阶段，按操作关键词聚类打功能标签。 |
| `src/pipeline/steps/step2/breakdown_rules/image_filter.py` | 38 | 图片引用过滤规则：预处理阶段，过滤文本中的图片引用/路径说明。 |
| `src/pipeline/steps/step2/breakdown_rules/numbered_list.py` | 244 | 编号列表提取规则：提取阶段，匹配无显式标记的编号/项目符号列表。 |
| `src/pipeline/steps/step2/breakdown_rules/permission_check.py` | 43 | 权限条件检测规则: 校验阶段, 检测涉及权限/角色的操作缺少前置条件. |
| `src/pipeline/steps/step2/breakdown_rules/standard_structure.py` | 110 | 标准结构提取规则：提取阶段，匹配 **操作步骤** + **预期行为** 标准结构。 |
| `src/pipeline/steps/step2/breakdown_rules/table_rules.py` | 118 | 表格规则：从结构化表格（table_meta）提取原子需求三元组。 |
| `src/pipeline/steps/step2/breakdown_rules/title_filter.py` | 484 | 模块概述拆解 + 标题过滤规则：清洗阶段。 |
| `src/pipeline/steps/step2/breakdown_rules/vague_detection.py` | 44 | 宽泛描述检测规则: 校验阶段, 检测 expected_feedback 过于笼统. |
| `src/pipeline/steps/step2/clarification_service.py` | 984 | 交互式需求澄清领域服务（ClarificationService）—— 流水线强化专项 T3。 |
| `src/pipeline/steps/step2/utils/table_extractor.py` | 262 | Markdown 表格识别与结构化提取。 |
| `src/pipeline/steps/step3/__init__.py` | 3 | （无模块文档串） |
| `src/pipeline/steps/step3/case_generator/__init__.py` | 17 | Step 3: 测试用例生成模块 (CaseGenerator) — v3.1 ================================================ 每个需求独立 AI 调用（含独立 RAG 检索），Threa… |
| `src/pipeline/steps/step3/case_generator/_action_injection.py` | 885 | Step3 操作/断言关键字注入器 — build_action_injection_prompt。 |
| `src/pipeline/steps/step3/case_generator/_action_templates.py` | 690 | 动作模板库（T02, 2026-08-06）。 |
| `src/pipeline/steps/step3/case_generator/_combo_enhancer.py` | 381 | 组合断言后处理增强 — 方案 C。 |
| `src/pipeline/steps/step3/case_generator/_contexts.py` | 788 | 上下文构建（UI / Trace / 数据字典 / 禁止词 / Few-shot） — 从 case_generator.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step3/case_generator/_control_type.py` | 228 | 控件类型识别 + 测试方法论智能选择（07-05h 新增） ================================================ |
| `src/pipeline/steps/step3/case_generator/_explore_task_builder.py` | 802 | P0-refactor(2026-07-29): explore_task 统一构建器（从 _model.py 拆分） |
| `src/pipeline/steps/step3/case_generator/_helpers.py` | 32 | 上下文裁剪辅助 + token 预算常量 — 从 case_generator.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step3/case_generator/_method_router_sdet.py` | 140 | A2(2026-08-16): SDET 直觉方法论路由器（从 _test_method_rules.py 抽离，避免单文件超 800 行）。 |
| `src/pipeline/steps/step3/case_generator/_model.py` | 985 | _model.py — 单条测试用例生成（v3.0 精简版） ========================================== model_test_cases: 为单条需求生成完整测试用例（冒烟模式等少量场景用）。 … |
| `src/pipeline/steps/step3/case_generator/_prompts.py` | 424 | 测试用例生成 Prompt 常量（v4.0 意图驱动）—— explore_task + checks 格式。 |
| `src/pipeline/steps/step3/case_generator/_risk_priority.py` | 223 | 风险驱动的测试用例优先级推导（A4, 2026-08-16）。 |
| `src/pipeline/steps/step3/case_generator/_test_method_rules.py` | 1184 | 测试方法论确定性规则层（T01, 2026-08-06）。 |
| `src/pipeline/steps/step3/case_quality/__init__.py` | 37 | 测试用例质量检查 & 去重 & 交叉验证模块（包入口） ==================================================== 从 case_generator.py 提取的辅助函数： - Few-Sho… |
| `src/pipeline/steps/step3/case_quality/cross_validation.py` | 147 | 多模型交叉验证（高风险测试用例） — 从 case_quality.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step3/case_quality/dedup.py` | 318 | 测试用例语义/关键词去重 + 质量评分工具 (v3.1) — 从 checks[] 读取。 |
| `src/pipeline/steps/step3/case_quality/few_shot.py` | 220 | Few-Shot 历史示例加载与格式化 — 从 case_quality.py 拆出。 |
| `src/pipeline/steps/step3/case_quality/metrics.py` | 133 | 用例驱动指标计算 (v3.0) — 从 checks[] + actions[] 读取，不依赖废弃的 verification_plan。 |
| `src/pipeline/steps/step3/case_quality/orchestrator.py` | 110 | S3-014: 三层质量保障统一编排器 ================================== 规格设计的三层质量保障体系（Few-Shot→交叉验证→去重）统一编排入口。 |
| `src/pipeline/steps/step3/case_upload.py` | 499 | [未复核-需 GLM/DeepSeek 把关] 用例直传执行（从 step3 开始，跳过 step1/step2 生成）。 |
| `src/pipeline/steps/step3/nav_seed/__init__.py` | 100 | [未复核-需 GLM/DeepSeek 把关] nav_seed 包：靶子导航数据 seed 通用分发入口（引擎常驻）。 |
| `src/pipeline/steps/step3/nav_seed/seed_hospital_nav.py` | 288 | seed_hospital_nav.py — 给 hospital_sut 注入导航数据（引擎常驻，复用 nav_graph_store）。 |
| `src/pipeline/steps/step3/oracle/__init__.py` | 31 | ADI（Assertion Depth Index）门禁 Oracle 包（T01，W2 P0-3）。 |
| `src/pipeline/steps/step3/oracle/adi.py` | 326 | ADI 计算内核（T01，W2 P0-3）—— 深度式 Assertion Depth Index。 |
| `src/pipeline/steps/step3/oracle/adi_signal.py` | 290 | ADI 深断言感知信号闸门（P0-3 增量，新增模块）。 |
| `src/pipeline/steps/step3/oracle/guard.py` | 88 | ADI 门禁编排（T01/T02/T03，W2 P0-3）—— apply_adi_gate。 |
| `src/pipeline/steps/step3/smoke_generator/__init__.py` | 333 | 顶层函数：generate_smoke_excel、clear_smoke_cache |
| `src/pipeline/steps/step3/smoke_generator/cache.py` | 210 | 顶层函数：_write_smoke_excel_to_mysql、_get_cached_smoke_excel、_get_latest_cached_smoke_excel、_delete_cached_smoke_excel、_cle… |
| `src/pipeline/steps/step3/smoke_generator/excel_builder.py` | 186 | 冒烟用例 Excel 文件构建（openpyxl 渲染） — 从 smoke_generator.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step3/smoke_generator/prompts.py` | 38 | 冒烟用例 AI Prompt — 基于三元组串联主链路。 |
| `src/pipeline/steps/step3/triple_reader.py` | 150 | 三元组读取器 — 为冒烟用例和测试设计提供统一数据源 ================================================== 从 requirements 表读取原子需求点（三元组）， 替换原有的 excel… |
| `src/pipeline/steps/step3/xmind_designer.py` | 288 | XMind 测试设计生成器（MySQL 版 + 三元组驱动） ============================================== 数据源：requirements 表三元组（operation + conditi… |
| `src/pipeline/steps/step3/xmind_generator.py` | 345 | XMind 测试设计生成器 ===================== 将 AI 分析后的测试设计结构化为 XMind 思维导图文件 (.xmind)。 |
| `src/pipeline/steps/step3_inverse/__init__.py` | 99 | 反向覆盖生成器（Inverse Coverage Generator）—— v0.1 骨架 ======================================================== |
| `src/pipeline/steps/step3_inverse/_base.py` | 120 | 顶层类：InverseGenContext |
| `src/pipeline/steps/step3_inverse/ordering.py` | 95 | 执行顺序异常反向覆盖生成器：跳步 / 乱序 / 并发 / 中途返回。 |
| `src/pipeline/steps/step3_inverse/replay.py` | 112 | 重放 / 幂等性 / 防重提交 反向覆盖生成器。 |
| `src/pipeline/steps/step3_inverse/security.py` | 670 | 安全反向覆盖生成器：越权 / 注入 / CSRF / XSS / 敏感数据泄漏。 |
| `src/pipeline/steps/step4/__init__.py` | 3 | （无模块文档串） |
| `src/pipeline/steps/step4/_lifecycle_db.py` | 195 | TDL DB 受控通道（T04，W2 P0-2）—— 白名单双闸门 + 参数化写 + 精确回滚。 |
| `src/pipeline/steps/step4/action_contract.py` | 108 | 顶层函数：is_canonical、guided_gap、classify_action |
| `src/pipeline/steps/step4/action_dispatchers.py` | 473 | 顶层函数：_build_action_to_tool_map、_get_action_to_tool_map、invalidate_cache、_convert_action_to_tool_args、_convert_assertion… |
| `src/pipeline/steps/step4/action_hooks.py` | 467 | action_hooks — 可复用的 before_action / after_action 插件钩子 ============================================================ 从 br… |
| `src/pipeline/steps/step4/ai/__init__.py` | 17 | browser_ai.ai — BrowserAi 拆分包子包 |
| `src/pipeline/steps/step4/ai/base.py` | 156 | BrowserAi 基础 mixin |
| `src/pipeline/steps/step4/ai/dispatcher.py` | 177 | BrowserAi 动作分发 mixin |
| `src/pipeline/steps/step4/ai/login.py` | 311 | BrowserAi 登录 mixin |
| `src/pipeline/steps/step4/ai/text_mode.py` | 25 | BrowserAi 文本模式 mixin (facade) |
| `src/pipeline/steps/step4/ai/text_mode_mixins/__init__.py` | 10 | text_mode_mixins — BrowserAi 文本模式拆分子模块 |
| `src/pipeline/steps/step4/ai/text_mode_mixins/_call.py` | 74 | 顶层类：_BrowserAiTextCallMixin |
| `src/pipeline/steps/step4/ai/text_mode_mixins/_decide.py` | 291 | 顶层类：_BrowserAiDecideMixin |
| `src/pipeline/steps/step4/ai/text_mode_mixins/_helpers.py` | 49 | 辅助函数：指标记录与占位符填充 |
| `src/pipeline/steps/step4/ai/text_mode_mixins/_text_parse.py` | 252 | 顶层类：_BrowserAiTextParseMixin |
| `src/pipeline/steps/step4/ai/tool_mode.py` | 258 | BrowserAi 工具调用模式 mixin |
| `src/pipeline/steps/step4/ai/validator.py` | 63 | BrowserAi 响应校验 mixin |
| `src/pipeline/steps/step4/api_tracker.py` | 152 | API 轨迹追踪器 (ApiTracker) ============================ 在 Playwright 浏览器启动时注册网络拦截器，记录每个操作触发的 HTTP 请求/响应。 |
| `src/pipeline/steps/step4/browser_ai.py` | 149 | 浏览器 AI 交互模块 (BrowserAi) — AI 调用与操作分发 ==================================================== |
| `src/pipeline/steps/step4/browser_ai_capabilities.py` | 44 | browser_ai 标准能力 facade（从 capabilities 子包重新导出） ========================================================== 因原文件 674 行过大，已… |
| `src/pipeline/steps/step4/browser_ai_login.py` | 330 | 确定性登录能力（Deterministic Login） ===================================== 从 browser_ai.py 拆分出的独立模块。 优先用稳定语义定位完成登录，避免每次登录都退化为 A… |
| `src/pipeline/steps/step4/browser_ai_patch.py` | 45 | browser_ai.py dispatch_action 补丁 — 注入插件钩子 |
| `src/pipeline/steps/step4/browser_ai_tools.py` | 87 | 浏览器 AI 工具调用结果转换 (BrowserAiTools) ============================================ |
| `src/pipeline/steps/step4/browser_ai_validator/__init__.py` | 28 | browser_ai_validator — AI 响应解析与校验（从 BrowserAi 拆分出的单元 D） ===============================================================… |
| `src/pipeline/steps/step4/browser_ai_validator/_adversarial.py` | 553 | Step 4 红队对抗验证 (Adversarial Result Verification) ====================================================== 对执行结果进行独立对抗审查，不修… |
| `src/pipeline/steps/step4/browser_ai_validator/_metrics.py` | 37 | 指标记录辅助（与 browser_ai.py 保持一致） |
| `src/pipeline/steps/step4/browser_ai_validator/_parse.py` | 48 | 顶层函数：_parse_ai_response |
| `src/pipeline/steps/step4/browser_ai_validator/_validate.py` | 272 | 顶层函数：_load_allowed_actions_from_library、_load_action_meta_from_library、_get_action_meta、_is_custom_action、_validate_act… |
| `src/pipeline/steps/step4/browser_core/__init__.py` | 117 | BrowserCore — Step4 浏览器自动化执行核心 ======================================= |
| `src/pipeline/steps/step4/browser_core/_smart_locator/__init__.py` | 168 | 顶层类：MatchResult |
| `src/pipeline/steps/step4/browser_core/_smart_locator/_fusion.py` | 53 | Top-K 选择 + 置信度判定。 |
| `src/pipeline/steps/step4/browser_core/_smart_locator/_keyword_match.py` | 218 | 关键词评分 —— 多字段加权命中。 |
| `src/pipeline/steps/step4/browser_core/_smart_locator/_synonyms_common.py` | 94 | 通用 UI 同义词字典（与具体业务无关）。 |
| `src/pipeline/steps/step4/browser_core/_smart_locator/_synonyms_project.py` | 37 | 项目业务同义词字典（与当前项目业务相关）。 |
| `src/pipeline/steps/step4/browser_core/_worker_errors.py` | 223 | 顶层函数：set_worker_errors、get_worker_errors、set_worker_network、get_worker_network、make_worker_handlers、make_worker_network… |
| `src/pipeline/steps/step4/browser_core/base.py` | 707 | 顶层类：_BrowserCoreBaseMixin |
| `src/pipeline/steps/step4/browser_core/page_explorer.py` | 474 | 顶层类：PageExplorer |
| `src/pipeline/steps/step4/browser_core/parallel.py` | 531 | BrowserCore 并行执行 mixin |
| `src/pipeline/steps/step4/browser_core/serial.py` | 1093 | BrowserCore 串行执行 mixin |
| `src/pipeline/steps/step4/browser_core/session_login.py` | 159 | 通用会话登录（SessionAware 自动登录与 identity_switch 的单一实现）。 |
| `src/pipeline/steps/step4/browser_core/single.py` | 47 | BrowserCore 单意图执行 mixin —— Facade。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/__init__.py` | 19 | single.py 拆分后的 mixins 包。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_agent_checks.py` | 1245 | AgentExplorer 路径 mixin —— 从 _base.py 拆出（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_agent_executor.py` | 956 | P0-refactor(2026-07-29): 统一 Agent 执行器（从 _exploratory.py 拆分） |
| `src/pipeline/steps/step4/browser_core/single_mixins/_ai_loop.py` | 1291 | 单意图执行 — AI 多轮循环 mixin。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_ai_loop_helpers_mixin.py` | 433 | AI loop 类内辅助方法 Mixin — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_ai_loop_phases.py` | 890 | AI loop 阶段函数模块 — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_ai_loop_utils.py` | 266 | AI loop 模块级辅助函数 — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_assertion_verdict.py` | 372 | 断言判定共享谓词（单点事实源）—— 区分「产品不满足」与「引擎够不着/不可评估」。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_base.py` | 1487 | 单意图执行 — 基础框架 mixin。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_base_classify.py` | 128 | 顶层函数：_load_step_action_classification、_is_structured_action_step、_is_structured_assertion_step、_safe_int、_is_authoritat… |
| `src/pipeline/steps/step4/browser_core/single_mixins/_confidence_gate.py` | 378 | 置信度闸门 — 从 _base.py 拆出（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_evidence_extractor.py` | 293 | 从 step_results 提取三轨 evidence（ui/api/db）— 从 _base.py 拆出（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_exploratory.py` | 1322 | P0-refactor(2026-07-27): Agent 统一执行器（替代 display 降级用例 + 标准 step loop） |
| `src/pipeline/steps/step4/browser_core/single_mixins/_op_cache_recorder.py` | 81 | P2-10 AgentExplorer record 接入点 — 成功动作序列写入 op_cache_v2。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_op_cache_replayer.py` | 114 | P2-10 AgentExplorer replay 接入点 — op_cache_v2 命中重放。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_oracle_persist.py` | 67 | P1-5: Oracle 结果持久化接入 — 从 _base.py 拆出（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_core/single_mixins/_structured.py` | 69 | 单意图执行 — 结构化动作/断言执行 mixin。 |
| `src/pipeline/steps/step4/browser_evidence.py` | 451 | browser_evidence.py — 测试用例证据记录与截图方法。从 browser_core.py 拆分。 |
| `src/pipeline/steps/step4/browser_intent_parser/__init__.py` | 40 | browser_intent_parser — 测试用例解析器 ========================================== 从 browser_core.py 拆分出的独立模块，负责测试用例的结构解析、 意图折叠… |
| `src/pipeline/steps/step4/browser_intent_parser/_io.py` | 120 | browser_intent_parser._io — IO 分区相关函数 ============================================ extract_intent_schema, has_write_key… |
| `src/pipeline/steps/step4/browser_intent_parser/_steps.py` | 541 | 顶层函数：_try_capability_step、normalize_steps、format_steps、_build_check_list、_table_from_sql、_bridge_plan_field_check |
| `src/pipeline/steps/step4/browser_structured_executor.py` | 880 | 顶层函数：async _resolve_navigate_url、async _ensure_toast_observer、async _capture_transient_hints、async _structured_focus_ch… |
| `src/pipeline/steps/step4/browser_tools/__init__.py` | 54 | 浏览器工具调用层 (BrowserTools) ================================ |
| `src/pipeline/steps/step4/browser_tools/_basic_locator.py` | 611 | 基础定位模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_fallback_locator.py` | 506 | 统一 fallback 定位模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_fuzzy_selectors.py` | 81 | 模糊选择器常量模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_hint_utils.py` | 183 | hint 工具模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_intent_data.py` | 372 | 意图定位数据层模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_intent_executor.py` | 718 | 意图定位执行层模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/_network_fault.py` | 306 | 网络故障注入（W3 T04，L0-5）—— 基于 Playwright page.route，零新依赖。 |
| `src/pipeline/steps/step4/browser_tools/_service_mock.py` | 577 | 外部依赖 mock / 桩（B5，CAPABILITY_GAP_PRD §3.2）—— 轻量 route 拦截 / 请求改写层。 |
| `src/pipeline/steps/step4/browser_tools/_taxonomy.py` | 982 | 权威动作/断言分类体系 (Authoritative Taxonomy) ============================================== 动作/断言体系归一化的唯一权威分类骨架（四端共用：Step3 生成侧 … |
| `src/pipeline/steps/step4/browser_tools/_tool_definitions.py` | 962 | 工具 Schema 纯数据定义 ====================== - _make_schema 辅助函数 - ALL_TOOLS：所有工具的 JSON Schema 列表 - _TOOL_PREFIX_MAP：工具名前缀 → … |
| `src/pipeline/steps/step4/browser_tools/execution/__init__.py` | 15 | Tool 执行器 (package facade) ============================ - 批量执行 tool_calls - 单个工具的执行实现 - ToolExecutionResult 结果封装 |
| `src/pipeline/steps/step4/browser_tools/execution/_batch.py` | 131 | 顶层函数：_log_tool_batch、async execute_tool_calls |
| `src/pipeline/steps/step4/browser_tools/execution/_result.py` | 27 | 顶层类：ToolExecutionResult |
| `src/pipeline/steps/step4/browser_tools/execution/_single.py` | 1361 | 单个工具执行实现 ================ |
| `src/pipeline/steps/step4/browser_tools/execution/_single_exec_browser.py` | 933 | _execute_tool 的 browser_* 工具处理器（模块级函数） ================================================= 架构债清理(2026-07-25): 从 _single… |
| `src/pipeline/steps/step4/browser_tools/execution/_single_exec_verify.py` | 860 | _execute_tool 的 verify_* 工具处理器（模块级函数） ================================================ 架构债清理(2026-07-25): 从 _single.p… |
| `src/pipeline/steps/step4/browser_tools/execution/_single_menu.py` | 602 | 菜单导航与点击模块（从 _single.py 拆分） ======================================== 架构债清理(2026-07-25): 菜单导航回退 + 通用菜单点击 + 关键词直搜 + 按钮di… |
| `src/pipeline/steps/step4/browser_tools/execution/_single_nav_helpers.py` | 272 | 导航与基础操作辅助模块（从 _single.py 拆分） ============================================ 架构债清理(2026-07-25): 基础拖拽/填充/选择 + 浏览器导航处理器 拆分… |
| `src/pipeline/steps/step4/browser_tools/execution/_single_nav_lookup.py` | 847 | 导航路径查找模块（从 _single.py 拆分） ====================================== 架构债清理(2026-07-25): 导航关键词提取 + nav_map 查找 + Confluence… |
| `src/pipeline/steps/step4/browser_tools/execution/_single_nav_state.py` | 387 | 页面状态检测辅助 — 从 _single.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/execution/_single_upload.py` | 734 | upload_file 执行辅助 — 从 _single.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/locator.py` | 96 | Playwright 元素定位器（门面模块）— 从原 locator.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/browser_tools/query.py` | 37 | 顶层函数：get_tool_schema、tool_name_list、build_tool_schemas |
| `src/pipeline/steps/step4/browser_tools/schemas.py` | 37 | Tool Schema 定义与映射（facade） ================================= 纯数据定义已提取到 ._tool_definitions，本模块保留映射逻辑， 对外暴露与原来完全一致的公共 API。 |
| `src/pipeline/steps/step4/browser_tools/text_mode.py` | 151 | 顶层函数：build_text_mode_tool_description、parse_text_mode_tool_calls、_normalize_single_tool_call |
| `src/pipeline/steps/step4/browser_tools/validation.py` | 237 | 顶层类：SchemaValidationError |
| `src/pipeline/steps/step4/browser_verification.py` | 476 | browser_verification.py — 硬校验与意图证据判断方法。从 browser_core.py 拆分。 |
| `src/pipeline/steps/step4/capabilities/__init__.py` | 33 | capabilities 子包：从各能力模块重新导出全部标准能力函数。 |
| `src/pipeline/steps/step4/capabilities/approval.py` | 215 | 标准审批流操作能力 ================== 点击同意/驳回按钮，填写审批意见并提交，验证状态变更。 仅用于 capability_hint = approval_flow 类业务意图。 返回 True 表示已成功审批；Fal… |
| `src/pipeline/steps/step4/capabilities/batch.py` | 203 | 标准列表批量操作能力 ==================== 勾选多行，点击批量按钮并验证批量结果。 仅用于 capability_hint = list_batch_action 类业务意图。 返回 True 表示已成功批量操作；Fa… |
| `src/pipeline/steps/step4/capabilities/create.py` | 300 | 标准新增/创建业务记录能力 |
| `src/pipeline/steps/step4/capabilities/data_query.py` | 218 | 标准数据查询能力 ================ 填写查询条件，点击查询按钮，验证表格返回数据。 仅用于 capability_hint = data_query 类业务意图。 返回 True 表示已成功查询；False 表示回退 AI… |
| `src/pipeline/steps/step4/capabilities/delete.py` | 196 | 标准删除业务记录能力 |
| `src/pipeline/steps/step4/capabilities/export.py` | 129 | 标准导出数据能力 |
| `src/pipeline/steps/step4/capabilities/form_fill.py` | 256 | 标准表单填写与提交能力 ====================== 检测所有输入框，按 placeholder 推断测试数据后提交，验证成功提示。 仅用于 capability_hint = form_fill_and_submit 类… |
| `src/pipeline/steps/step4/capabilities/login.py` | 283 | 标准登录能力（账号密码登录） ============================ 用 Playwright 顺序推断用户名/密码输入框，填写后点击登录按钮并验证跳转。 仅用于 capability_hint = login_with… |
| `src/pipeline/steps/step4/capabilities/navigate.py` | 196 | 标准菜单导航能力 ================ 按层级点击侧边栏或顶部菜单项，进入目标页。 仅用于 capability_hint = navigate_menu 类业务意图。 返回 True 表示已成功导航；False 表示回退 A… |
| `src/pipeline/steps/step4/capabilities/pagination.py` | 192 | 标准分页控制能力 ================ 点击下一页或调整每页条数，验证页面数据变化。 仅用于 capability_hint = pagination_control 类业务意图。 返回 True 表示已成功翻页；False … |
| `src/pipeline/steps/step4/capabilities/search.py` | 247 | 标准搜索/筛选能力 |
| `src/pipeline/steps/step4/capabilities/table_export.py` | 220 | 标准表格导出能力 ================ 识别表格附近的 Excel/PDF/CSV 导出按钮，点击并验证下载。 仅用于 capability_hint = table_export 类业务意图。 返回 True 表示已成功导出… |
| `src/pipeline/steps/step4/capability_cache/__init__.py` | 379 | 顶层类：CapabilityCache |
| `src/pipeline/steps/step4/capability_cache/events.py` | 114 | 顶层类：_CapabilityCacheEventsMixin |
| `src/pipeline/steps/step4/capability_cache/keys.py` | 81 | 顶层函数：_now_sql、normalize_intent、_project_name、_active_version_id、_capability_name、make_capability_key |
| `src/pipeline/steps/step4/capability_cache/schema.py` | 27 | CapabilityCache 的建表/字段补齐/旧表注释修正 — 从 capability_cache.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/data_factory_core.py` | 705 | 顶层类：DataFactoryCore |
| `src/pipeline/steps/step4/data_gen.py` | 319 | 顶层类：DataGen |
| `src/pipeline/steps/step4/data_lifecycle_ops.py` | 183 | case 级 data_setup / data_teardown 数据生命周期 op 执行器（C 数据治理，L0 短板闭环）。 |
| `src/pipeline/steps/step4/db_verifier/__init__.py` | 162 | DB 校验器 (DbVerifier) — P1 加固版（包入口） ================================================== 在步骤执行后，通过 db_agent 查询数据库，验证数据的最终… |
| `src/pipeline/steps/step4/db_verifier/helpers.py` | 118 | 顶层函数：_compare_with_precision、_format_value、_get_field_type、_limit_clause、_get_schema_context、_quote_ident |
| `src/pipeline/steps/step4/db_verifier/safety.py` | 66 | SQL 安全校验 — 从 db_verifier.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/db_verifier/schema.py` | 114 | DB schema 自动加载 — 从 db_verifier.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/db_verifier/verifiers.py` | 399 | 具体校验器（structured / sql / natural / ai） — 从 db_verifier.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/diagnosis.py` | 129 | Step4 失败根因分类（executions.diagnosis 列）。 |
| `src/pipeline/steps/step4/empirical/__init__.py` | 51 | （无模块文档串） |
| `src/pipeline/steps/step4/empirical/a11y_checker.py` | 184 | 可访问性（a11y）检查器 — step4 集成包装器。 |
| `src/pipeline/steps/step4/empirical/authz_matrix.py` | 297 | [未复核-需 GLM/DeepSeek 把关] 权限矩阵验证能力（AuthzMatrixVerifier）。 |
| `src/pipeline/steps/step4/empirical/constants.py` | 44 | （无模块文档串） |
| `src/pipeline/steps/step4/empirical/cross_cut_gates.py` | 481 | 横切门禁统一入口 — a11y / perf / visual 三横切维度的单一执行源。 |
| `src/pipeline/steps/step4/empirical/deep_assertion_runner.py` | 891 | 顶层类：PaginationUnavailable、DeepAssertionRunner、BrowserAssertionRunner、_BlockedApiRunner |
| `src/pipeline/steps/step4/empirical/perf_checker.py` | 507 | 性能测试门禁 — step4 集成包装器（perf_gate 总入口）。 |
| `src/pipeline/steps/step4/empirical/plan.py` | 252 | [已复核 2026-09-04] 统一验证计划（VerificationPlan）与单一执行入口。 |
| `src/pipeline/steps/step4/empirical/probe_runner.py` | 94 | 无认证 API 靶子探针执行器（verification.cases 风格）。 |
| `src/pipeline/steps/step4/empirical/report_writer.py` | 42 | 顶层函数：write |
| `src/pipeline/steps/step4/empirical/security_checker.py` | 150 | 安全（security）检查器 — step4 集成包装器。 |
| `src/pipeline/steps/step4/empirical/sut_harness.py` | 546 | 顶层类：SutHarness |
| `src/pipeline/steps/step4/empirical/target_cfgs.py` | 563 | 顶层函数：_resolve_project_root、get_target_cfg |
| `src/pipeline/steps/step4/empirical/target_empirical.py` | 1223 | 顶层类：TargetEmpiricalVerifier |
| `src/pipeline/steps/step4/empirical/visual_checker.py` | 166 | 视觉横切门禁 — step4 集成包装器。 |
| `src/pipeline/steps/step4/execution_router.py` | 98 | 执行器双路径收敛（ExecutionRouter）—— 流水线强化专项 T6。 |
| `src/pipeline/steps/step4/failure_classifier.py` | 361 | 顶层类：FailureChain |
| `src/pipeline/steps/step4/op_cache/__init__.py` | 201 | Step4 操作指令语义缓存 (OperationCache) ======================================== 基于「页面结构指纹 + 步骤目标」的语义级缓存。 缓存时不存 ai_id，只保留 targe… |
| `src/pipeline/steps/step4/op_cache/_helpers.py` | 214 | 顶层函数：normalize_goal、compute_semantic_fingerprint、resolve_ai_ids、_find_by_hint、compute_config_hash、_strip_and_placeholder |
| `src/pipeline/steps/step4/op_cache/base.py` | 171 | OperationCache 基础工具 Mixin ============================== 提供数据库连接、建表、缓存键构建等通用方法。 |
| `src/pipeline/steps/step4/op_cache/cache_ops.py` | 194 | 顶层类：_OpCacheOpsMixin |
| `src/pipeline/steps/step4/op_cache/eviction.py` | 99 | OperationCache LRU 淘汰 + 失效 Mixin ====================================== LRU 容量控制、按成功率淘汰坏缓存。 |
| `src/pipeline/steps/step4/op_cache/stats.py` | 107 | OperationCache 统计 + 预热 Mixin ================================= 缓存统计、内存预热。 |
| `src/pipeline/steps/step4/op_cache/v2_store.py` | 333 | P1-8: op_cache_v2 读写存储 — 真实原子指令序列缓存。 |
| `src/pipeline/steps/step4/playwright_engine/__init__.py` | 16 | Playwright 执行引擎子模块包。 |
| `src/pipeline/steps/step4/playwright_engine/_readiness.py` | 94 | 浏览器依赖就绪检测（轻量模块，import 时不加载 playwright）。 |
| `src/pipeline/steps/step4/playwright_engine/actions/__init__.py` | 89 | 元素动作执行模块（包入口）：点击、填充、悬停、选择、拖拽、上传等。 |
| `src/pipeline/steps/step4/playwright_engine/actions/clear.py` | 48 | 清空输入动作 — clear_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/click.py` | 204 | 点击相关动作（含视觉兜底）— 从 actions.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/playwright_engine/actions/close_tab.py` | 49 | 关闭标签页动作 — close_tab_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/context_menu.py` | 84 | 右键菜单交互动作 — context_menu_click_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/drag_offset.py` | 57 | 偏移拖拽动作 — drag_offset_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/file_download.py` | 82 | 文件下载动作 — download_file_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/fill.py` | 102 | 填充输入框相关动作 — 从 actions.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/playwright_engine/actions/focus.py` | 65 | 聚焦/失焦动作 — focus_element + blur_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/go_back.py` | 37 | 浏览器导航动作 — go_back_element + go_forward_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/iframe_switch.py` | 81 | iframe 切换动作 — switch_to_iframe + switch_to_default。 |
| `src/pipeline/steps/step4/playwright_engine/actions/interactions.py` | 180 | hover/select_option/press/double_click/right_click/check 等轻量交互动作 — 从 actions.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/playwright_engine/actions/key_press.py` | 40 | 裸按键动作 — key_down_element + key_up_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/long_press.py` | 49 | 长按动作 — long_press_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/middle_click.py` | 35 | 中键点击动作 — middle_click_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/motion.py` | 127 | 页面级动作（导航/滚动/拖拽/上传）— 从 actions.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/playwright_engine/actions/multi_touch.py` | 75 | 多点触控动作 — multi_touch_pinch_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/page_reload.py` | 34 | 页面刷新动作 — page_reload_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/set_date.py` | 65 | 日期选择器动作 — set_date_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/swipe.py` | 63 | 滑动手势动作 — swipe_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/tab_switch.py` | 66 | 标签页切换动作 — switch_tab_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/type_text.py` | 45 | 逐字输入动作 — type_text_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/wheel.py` | 35 | 原生滚轮动作 — wheel_element。 |
| `src/pipeline/steps/step4/playwright_engine/actions/window_resize.py` | 34 | 窗口调整动作 — resize_window_element。 |
| `src/pipeline/steps/step4/playwright_engine/backend_resolver.py` | 261 | 顶层类：BrowserBackendResolver |
| `src/pipeline/steps/step4/playwright_engine/dom_extraction.py` | 241 | DOM 结构化提取模块。 |
| `src/pipeline/steps/step4/playwright_engine/engine.py` | 39 | 顶层类：PlaywrightEngine |
| `src/pipeline/steps/step4/playwright_engine/lifecycle.py` | 810 | 顶层类：LifecycleMixin |
| `src/pipeline/steps/step4/playwright_engine/selectors.py` | 140 | 语义选择器构建模块。 |
| `src/pipeline/steps/step4/playwright_engine/stats.py` | 60 | 点击兜底命中率统计模块。 |
| `src/pipeline/steps/step4/plugins/__init__.py` | 64 | Step4 插件系统 ================ 所有增强功能以独立插件形式存在，通过 PluginManager 统一管理。 插件在 browser_core 执行链路的关键节点被调用，不影响原有代码结构。 |
| `src/pipeline/steps/step4/plugins/_self_healing_ai_helpers_mixin.py` | 388 | SelfHealing AI/视觉辅助 mixin — 从 self_healing.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/plugins/_self_healing_strategies_mixin.py` | 525 | SelfHealing 策略 mixin — 从 self_healing.py 拆分（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/plugins/business_capabilities/__init__.py` | 76 | 顶层类：BusinessCapabilities |
| `src/pipeline/steps/step4/plugins/business_capabilities/base.py` | 257 | 顶层类：_BusinessCapBaseMixin |
| `src/pipeline/steps/step4/plugins/business_capabilities/detect.py` | 158 | 顶层类：_BusinessCapDetectMixin |
| `src/pipeline/steps/step4/plugins/business_capabilities/detectors.py` | 309 | _detectors.py — 12 个能力各自的 detector（规则实现，不调用 AI） ============================================================= 作为 _Busin… |
| `src/pipeline/steps/step4/plugins/business_capabilities/registry.py` | 218 | 顶层类：_BusinessCapRegistryMixin |
| `src/pipeline/steps/step4/plugins/business_capabilities_plugin.py` | 128 | 顶层类：BusinessCapabilitiesPlugin |
| `src/pipeline/steps/step4/plugins/cdp_metrics_plugin.py` | 183 | 顶层类：CdpMetricsPlugin |
| `src/pipeline/steps/step4/plugins/dynamic_dom_sizer/__init__.py` | 29 | dynamic_dom_sizer — 动态 DOM 元素上限 ==================================== 根据意图类型智能调整 MAX_DOM_ELEMENTS，简单场景（如登录）只保留少量元素， 复杂流程… |
| `src/pipeline/steps/step4/plugins/dynamic_dom_sizer/_metric.py` | 29 | KPI 埋点 |
| `src/pipeline/steps/step4/plugins/dynamic_dom_sizer/_override.py` | 37 | 覆盖机制 |
| `src/pipeline/steps/step4/plugins/dynamic_dom_sizer/_rules.py` | 267 | 顶层函数：_match_keywords、_extract_text、_size_to_hint、_match_rule、get_dom_limit、get_context_size_hint |
| `src/pipeline/steps/step4/plugins/flaky_guard.py` | 497 | 顶层类：FlakyGuardPlugin |
| `src/pipeline/steps/step4/plugins/fuzzy_match_engine/__init__.py` | 366 | 顶层类：FuzzyMatchEngine |
| `src/pipeline/steps/step4/plugins/fuzzy_match_engine/metrics.py` | 31 | KPI 埋点 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/plugins/fuzzy_match_engine/similarity.py` | 75 | 相似度算法：Jaccard + 双通路语义相似度 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/plugins/fuzzy_match_engine/tokenizer.py` | 375 | 分词器：停用词 + 语义同义词组 + tokenize + 概念/字符 token 拆分 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。 |
| `src/pipeline/steps/step4/plugins/ground_truth.py` | 159 | 顶层类：GroundTruthPlugin |
| `src/pipeline/steps/step4/plugins/harness_governance.py` | 231 | 顶层类：HarnessGovernancePlugin |
| `src/pipeline/steps/step4/plugins/long_term_memory.py` | 190 | 顶层类：LongTermMemoryPlugin |
| `src/pipeline/steps/step4/plugins/multi_agent.py` | 296 | 顶层类：AgentRole、MultiAgentPlugin |
| `src/pipeline/steps/step4/plugins/perf_collector_plugin.py` | 240 | 性能门禁采集插件（perf_collector） ================================== 职责：在 step4 执行链路上采集性能样本并投递到 ``PerfContext``，供 step5 ``_perf_… |
| `src/pipeline/steps/step4/plugins/performance_monitor.py` | 235 | 顶层类：PerformanceMonitorPlugin |
| `src/pipeline/steps/step4/plugins/plugin_manager.py` | 343 | 顶层类：PluginHook、BasePlugin、PluginManager |
| `src/pipeline/steps/step4/plugins/scorecard_oracle.py` | 403 | 顶层类：ScorecardOraclePlugin |
| `src/pipeline/steps/step4/plugins/self_healing.py` | 474 | 顶层类：SelfHealingPlugin |
| `src/pipeline/steps/step4/plugins/self_healing_vision.py` | 472 | 顶层类：SelfHealingVisionPlugin |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/__init__.py` | 78 | 顶层类：SemanticDomTree |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/base.py` | 27 | 顶层类：_SemanticDomBaseMixin |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/build.py` | 126 | semantic_dom_tree_mixins._build — 区域树构建 mixin（build_region_tree） |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/prompt.py` | 169 | 顶层类：_SemanticDomPromptMixin |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/utils/__init__.py` | 32 | semantic_dom_tree.utils — 模块级常量、辅助函数与 KPI 埋点（facade） |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/utils/_classify.py` | 190 | semantic_dom_tree.utils._classify — 元素类型识别与文本提取 |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/utils/_metrics.py` | 29 | semantic_dom_tree.utils._metrics — KPI 埋点 |
| `src/pipeline/steps/step4/plugins/semantic_dom_tree/utils/_region.py` | 208 | semantic_dom_tree.utils._region — 关键词提取、聚类与区域构建 |
| `src/pipeline/steps/step4/plugins/token_budget.py` | 266 | 顶层类：TokenBudgetPlugin |
| `src/pipeline/steps/step4/plugins/visual_baseline.py` | 496 | 顶层类：VisualBaselinePlugin |
| `src/pipeline/steps/step4/self_repair.py` | 180 | 自我修复编排器 ================ Step4 执行失败后，自动分析失败原因并重新生成用例（闭环）。 |
| `src/pipeline/steps/step4/strategy_router.py` | 140 | 顶层函数：_select_strategy |
| `src/pipeline/steps/step4/tab_navigation.py` | 298 | tab_navigation.py — Tab 页面切换通用逻辑（从 browser_structured_executor.py 拆分，§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/test_data_lifecycle.py` | 449 | 测试数据生命周期（T04，W2 P0-2）—— TestDataLifecycle。 |
| `src/pipeline/steps/step4/tool_execution_logger.py` | 176 | 融合架构工具执行日志持久化存储 ================================= 将工具调用链路追踪数据写入 MySQL，支持性能分析和问题排查。 |
| `src/pipeline/steps/step4/verification/__init__.py` | 1045 | 顶层函数：_source_track_confirms_data、_flag_config_hidden_suspect、_authz_call_status、_authz_call_matches、_authz_unauthorized… |
| `src/pipeline/steps/step4/verification/_a11y_gate.py` | 147 | 顶层函数：_a11y_declared、async _fetch_page_html、async _flag_a11y |
| `src/pipeline/steps/step4/verification/_api.py` | 244 | API 轨校验：3 级响应校验 + 自动检测 — 从 verification.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step4/verification/_api_contract.py` | 345 | F7 接口契约（响应结构/schema）检测 —— 汇总层跨轨增强（fail-open）。 |
| `src/pipeline/steps/step4/verification/_confidence.py` | 126 | 三轨置信度评分（多因子加权） — 从 verification.py 拆出。 |
| `src/pipeline/steps/step4/verification/_consistency.py` | 193 | 顶层函数：_extract_number、_normalize、async verify_consistency_async |
| `src/pipeline/steps/step4/verification/_db_ui.py` | 3211 | DB / UI 客观断言校验 — 从 verification.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step4/verification/_db_ui_helpers.py` | 148 | DB/UI 校验通用助手 —— 从 _db_ui.py 拆出（§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step4/verification/_op_quality.py` | 1215 | F8 幂等 / F2 响应性能 / F3 并发 / F9 网络 / F10 变异 —— 操作质量汇总层检测（fail-open）。 |
| `src/pipeline/steps/step4/verification/_rule_meta.py` | 332 | Step4 检查执行 — 算法规则元信息清单（只读，供"算法规则清单"展示）。 |
| `src/pipeline/steps/step4/verification/_security_full.py` | 541 | F1 安全完整（XSS 反射 + 敏感数据泄露）—— 汇总层跨轨增强（fail-open）。 |
| `src/pipeline/steps/step4/verification/_track_protocol.py` | 881 | 单轨校验器协议 + 三轨收敛（T03, 2026-08-06）。 |
| `src/pipeline/steps/step4/verification/_tracks.py` | 94 | 轨道需求推断器（高度解耦，单一职责）。 |
| `src/pipeline/steps/step4/verification/_url_match.py` | 42 | URL 匹配增强：通配符 + 正则匹配 — 从 verification.py 拆出，零业务逻辑变更。 |
| `src/pipeline/steps/step4/verification/_verify_animation.py` | 127 | 元素动画完成断言 — animation_complete_check。 |
| `src/pipeline/steps/step4/verification/_verify_api_ext.py` | 322 | API 扩展断言 — api_call_count_check / request_body_check。 |
| `src/pipeline/steps/step4/verification/_verify_attribute_all.py` | 111 | 多元素属性一致性断言 — element_attribute_all_check。 |
| `src/pipeline/steps/step4/verification/_verify_console.py` | 54 | Console 错误断言 — console_error_check。 |
| `src/pipeline/steps/step4/verification/_verify_contract.py` | 223 | 契约校验器（W3 T04，L0-4）—— 手写轻量 JSON Schema 子集，零新依赖。 |
| `src/pipeline/steps/step4/verification/_verify_count.py` | 69 | 元素数量断言 — element_count_check。 |
| `src/pipeline/steps/step4/verification/_verify_db_ext.py` | 495 | 顶层函数：_db_env_skip、_param_unresolved、_compare_values、async verify_db_ext、_check_record_count、_check_record_not_exists |
| `src/pipeline/steps/step4/verification/_verify_db_ui.py` | 104 | DB 与 UI 一致性断言 — db_ui_consistency_check。 |
| `src/pipeline/steps/step4/verification/_verify_dialog.py` | 60 | 弹窗断言 — dialog_check。 |
| `src/pipeline/steps/step4/verification/_verify_download.py` | 76 | 文件下载结果断言 — download_check。 |
| `src/pipeline/steps/step4/verification/_verify_geometry.py` | 348 | 几何断言（getBoundingClientRect，多点采样，容差支持）。 |
| `src/pipeline/steps/step4/verification/_verify_header.py` | 102 | API 响应头断言 — api_header_check。 |
| `src/pipeline/steps/step4/verification/_verify_image.py` | 71 | 图片加载断言 — image_loaded_check。 |
| `src/pipeline/steps/step4/verification/_verify_list.py` | 129 | 列表排序顺序断言 — list_order_check。 |
| `src/pipeline/steps/step4/verification/_verify_loading.py` | 83 | 异步加载完成断言 — loading_complete_check。 |
| `src/pipeline/steps/step4/verification/_verify_network.py` | 99 | 网络请求拦截断言 — network_check。 |
| `src/pipeline/steps/step4/verification/_verify_page_load.py` | 105 | 页面加载性能断言 — page_load_time_check。 |
| `src/pipeline/steps/step4/verification/_verify_pagination.py` | 103 | 分页数据正确性断言 — pagination_check。 |
| `src/pipeline/steps/step4/verification/_verify_perf.py` | 153 | API 性能断言 — response_time_check / response_header_check。 |
| `src/pipeline/steps/step4/verification/_verify_position.py` | 108 | 精确位置/尺寸断言 — position_check / exact_size_check。 |
| `src/pipeline/steps/step4/verification/_verify_refresh.py` | 105 | 页面刷新检测断言 — page_refresh_check。 |
| `src/pipeline/steps/step4/verification/_verify_regex.py` | 84 | 文本格式正则断言 — text_regex_check。 |
| `src/pipeline/steps/step4/verification/_verify_state.py` | 172 | 元素状态断言 — element_enabled_check / element_checked_check / select_value_check / element_text_equals_check。 |
| `src/pipeline/steps/step4/verification/_verify_storage.py` | 119 | 存储断言 — storage_check。 |
| `src/pipeline/steps/step4/verification/_verify_style.py` | 206 | 样式断言 — color_check / font_check / visibility_check / css_class_check / attribute_check / zindex_check。 |
| `src/pipeline/steps/step4/verification/_verify_text_contains.py` | 97 | 元素文本包含/不包含断言 — text_contains_check。 |
| `src/pipeline/steps/step4/verification/_verify_time.py` | 152 | 顶层函数：_parse_time、async verify_time_range |
| `src/pipeline/steps/step4/verification/_verify_toast.py` | 158 | Toast/Loading 断言 — toast_appear_check / toast_disappear_check / loading_state_check。 |
| `src/pipeline/steps/step4/verification/_verify_url.py` | 71 | URL 断言 — url_check。 |
| `src/pipeline/steps/step4/verification/_verify_visual.py` | 583 | 顶层函数：_no_data、_local_vlm_crash_hint、_verdict、_read_vision_gate、async _cloud_visual_review_via_verifier、async _try_vlm_f… |
| `src/pipeline/steps/step4/verification/_verify_websocket.py` | 95 | WebSocket 消息断言 — websocket_check。 |
| `src/pipeline/steps/step4/verification/contract_builder.py` | 797 | 契约断言生成器（W3 T04，L0-4）—— 从 YApi / OpenAPI 响应 schema 生成 api 轨断言。 |
| `src/pipeline/steps/step4/verification/tracks.py` | 94 | 三轨状态枚举（F-02 引入）。 |
| `src/pipeline/steps/step4/verification/verdict.py` | 101 | PASS 判定唯一入口（F-04 引入）。 |
| `src/pipeline/steps/step4/verification_plan_builder.py` | 758 | 执行时 verification_plan 自动构建器（v3.0） ============================================ Step 4 执行时，优先消费 Step3 产出的 checks[] + act… |
| `src/pipeline/steps/step5/__init__.py` | 3 | （无模块文档串） |
| `src/pipeline/steps/step5/_a11y_report.py` | 245 | 可访问性（a11y）step5 报告适配层（薄编排，零业务逻辑）。 |
| `src/pipeline/steps/step5/_blue_red_report.py` | 418 | Step 5 报告生成 — 蓝红对抗引擎 =============================== Blue Team: 决策者/技术详情 双视角并行生成报告 Red Team: 证据一致性 + 遗漏检查（结构化，不调 AI） Ar… |
| `src/pipeline/steps/step5/_bug_report.py` | 308 | Bug 报告规范 — severity 分级 + 复现步骤 + 根因分类 + 失败三分类/根因下钻（通用规则驱动）。 |
| `src/pipeline/steps/step5/_confidence/__init__.py` | 15 | 置信度与智能洞察相关函数（package facade） |
| `src/pipeline/steps/step5/_confidence/_insights.py` | 364 | 智能洞察报告 |
| `src/pipeline/steps/step5/_confidence/_summary.py` | 134 | 置信度汇总 |
| `src/pipeline/steps/step5/_confidence/_svg_charts.py` | 175 | SVG 图表生成（学习曲线 / 缓存仪表盘） |
| `src/pipeline/steps/step5/_db_utils.py` | 305 | 数据库工具函数 |
| `src/pipeline/steps/step5/_html.py` | 193 | HTML 报告生成与保存相关函数 |
| `src/pipeline/steps/step5/_manual_retest.py` | 285 | 人工补测建议清单生成器（v3.0） |
| `src/pipeline/steps/step5/_perf_gate_report.py` | 163 | 性能门禁 step5 适配层（薄编排，零业务逻辑）。 |
| `src/pipeline/steps/step5/_report_persist.py` | 497 | _report_persist.py — 报告持久化逻辑（从 report_generator.py 拆分，§4.3 单文件≤800行）。 |
| `src/pipeline/steps/step5/_report_sections.py` | 364 | Step5 报告新区块（W3 T02/T05）—— 归因 / 幂等 / 性能 区块渲染。 |
| `src/pipeline/steps/step5/_rule_meta.py` | 127 | Step5 报告生成 — 算法规则元信息清单（只读，供"算法规则清单"展示）。 |
| `src/pipeline/steps/step5/_security_report.py` | 297 | 安全（security）step5 报告适配层（薄编排，零业务逻辑）。 |
| `src/pipeline/steps/step5/_self_eval.py` | 116 | P2-12 SelfEval 接入点 — 引擎自评估章节生成。 |
| `src/pipeline/steps/step5/_svg.py` | 279 | SVG 图表生成工具函数 |
| `src/pipeline/steps/step5/_triage.py` | 573 | 失败三分类 + 根因下钻（P0-6 / P1-5 有界增强，规则驱动、fail-open）。 |
| `src/pipeline/steps/step5/_visual_report.py` | 203 | 视觉（visual）维度 step5 报告适配层（薄编排，零业务逻辑）。 |
| `src/pipeline/steps/step5/attribution.py` | 230 | 失败归因聚合（W3 T02，L0-1 消费闭环）—— 纯读聚合，零 DB 写。 |
| `src/pipeline/steps/step5/bug_ticket.py` | 556 | 结构化缺陷提单 schema（D4，L1→L3）—— 可直接推送到 issue 系统的 bug ticket。 |
| `src/pipeline/steps/step5/engine_health.py` | 212 | 引擎健康治理（W4-B，P0，用户红线②）—— 纯读聚合 HARNESS_ERROR，零 DB 写。 |
| `src/pipeline/steps/step5/fake_pass_audit.py` | 276 | 假 PASS 审计器（W4-A，P0，用户红线①）—— 纯读聚合 executions，零 DB 写（默认 dry_run）。 |
| `src/pipeline/steps/step5/idempotency.py` | 210 | 幂等比对工具（W3 T02，L0-6）—— 纯函数比对，零 DB 写。 |
| `src/pipeline/steps/step5/minimal_repro.py` | 670 | 最小复现路径（D3，L0→L2→L3 收敛）—— 从失败用例的执行步骤中提取最小复现路径。 |
| `src/pipeline/steps/step5/perf_baseline.py` | 225 | 性能基线（W3 T05，L0-7）—— ApiTracker elapsed_ms 采集 + 跨 run 对比（零 DDL）。 |
| `src/pipeline/steps/step5/report_generator.py` | 1242 | 测试报告生成器 (ReportGenerator) ================================= 从 Validation_Intent-*.xlsx 和 requirements_breakdown.xlsx 读取… |
| `src/pipeline/streaming_scheduler.py` | 497 | 流式管道调度器 (StreamingPipelineScheduler) — v4.0 ================================================== v4.0: Step1 阻塞 → Step2… |
| `src/pipeline/utils/__init__.py` | 1 | （无模块文档串） |
| `src/pipeline/utils/action_enrichment.py` | 179 | 顶层函数：_build_known_actions、enrich_action_applicable_tags |
| `src/pipeline/utils/action_library_prompt.py` | 106 | Action Library Prompt 生成器 ============================== 从 MySQL action_library_store 读取用户自定义操作库，生成注入到 Step3/Step4 prom… |
| `src/pipeline/utils/action_library_store/__init__.py` | 83 | 操作指令库 MySQL 事实源 (ActionLibraryStore) ================================================ |
| `src/pipeline/utils/action_library_store/_verification_category.py` | 605 | 断言校验(verification)类别默认数据 ================================================ 从 defaults.py 拆分而来，仅包含 verification 类别的操作定义。 … |
| `src/pipeline/utils/action_library_store/base.py` | 80 | ActionLibraryStore 基础工具 Mixin ================================== 提供数据库连接、建表、JSON 序列化等通用方法。 |
| `src/pipeline/utils/action_library_store/defaults.py` | 705 | 操作指令库默认数据 (defaults) ================================================ |
| `src/pipeline/utils/action_library_store/health.py` | 51 | ActionLibraryStore 健康统计 Mixin ================================== 只读统计计数。 |
| `src/pipeline/utils/action_library_store/io.py` | 86 | ActionLibraryStore 导入/导出 Mixin ==================================== JSON 文件导入导出操作。 |
| `src/pipeline/utils/action_library_store/read.py` | 534 | ActionLibraryStore 读取操作 Mixin ================================== 整体库加载、白名单加载、导入关键词加载。 |
| `src/pipeline/utils/action_library_store/write.py` | 337 | 顶层类：_ActionLibraryWriteMixin |
| `src/pipeline/utils/ai_generation_logs_store.py` | 50 | AI 生成日志 MySQL 事实源存储 ============================= |
| `src/pipeline/utils/ai_generation_logs_store_mixins/__init__.py` | 3 | AiGenerationLogsStore 拆分 mixin 包。 |
| `src/pipeline/utils/ai_generation_logs_store_mixins/_base.py` | 62 | AiGenerationLogsStore 基础工具 Mixin ===================================== 提供数据库连接、建表、JSON 序列化等通用方法。 |
| `src/pipeline/utils/ai_generation_logs_store_mixins/_health.py` | 104 | 顶层类：AiGenerationLogsStoreHealthMixin |
| `src/pipeline/utils/ai_generation_logs_store_mixins/_io.py` | 97 | AiGenerationLogsStore 导入导出 Mixin ===================================== 提供 ``export_to_jsonl`` / ``import_from_jsonl`` 方… |
| `src/pipeline/utils/ai_generation_logs_store_mixins/_read.py` | 127 | 顶层类：AiGenerationLogsStoreReadMixin |
| `src/pipeline/utils/ai_generation_logs_store_mixins/_write.py` | 216 | AiGenerationLogsStore 写入 Mixin ================================= 提供 ``insert_log`` 等 AI 生成日志写入方法。 |
| `src/pipeline/utils/case_requirement_tracker.py` | 298 | 测试用例→需求 映射追踪 (IntentRequirementTracker) ==================================== MySQL 持久化追踪 case_id → req_id 映射，替代旧的 inten… |
| `src/pipeline/utils/case_store/__init__.py` | 16 | 测试用例 MySQL 镜像存储 ====================== |
| `src/pipeline/utils/case_store/_constants.py` | 31 | 测试用例存储模块常量 (v3.0) |
| `src/pipeline/utils/case_store/_helpers.py` | 61 | 测试用例存储模块内部工具函数 |
| `src/pipeline/utils/case_store/_store_core.py` | 51 | 测试用例数据存储核心 |
| `src/pipeline/utils/case_store/crud/__init__.py` | 14 | case_store.crud — CaseStore 的 CRUD 拆分包 |
| `src/pipeline/utils/case_store/crud/base.py` | 188 | 顶层类：_VisBaseMixin |
| `src/pipeline/utils/case_store/crud/export.py` | 218 | CaseStore 导出 mixin |
| `src/pipeline/utils/case_store/crud/filter.py` | 184 | CaseStore 过滤查询 mixin |
| `src/pipeline/utils/case_store/crud/read.py` | 523 | 顶层类：_VisReadMixin |
| `src/pipeline/utils/case_store/crud/write.py` | 663 | CaseStore 写入 mixin |
| `src/pipeline/utils/checkpoint_store.py` | 365 | 顶层类：CheckpointStore |
| `src/pipeline/utils/confidence_scorer.py` | 311 | 顶层函数：_expected_specificity、score_requirement、score_test_case、confidence_color、get_avg_confidence |
| `src/pipeline/utils/consistency_checker/__init__.py` | 250 | 顶层函数：run_all_checks、run_and_print_checks、main |
| `src/pipeline/utils/consistency_checker/env.py` | 40 | MySQL 环境检查模块 ================== 检查 MySQL 数据库连接状态。 |
| `src/pipeline/utils/consistency_checker/executability.py` | 70 | 顶层函数：check_intent_executability |
| `src/pipeline/utils/consistency_checker/mapping.py` | 334 | 顶层函数：check_intent_mapping_consistency、build_intent_mapping_repair_plan |
| `src/pipeline/utils/consistency_checker/migration.py` | 310 | 顶层函数：check_mysql_migration_health |
| `src/pipeline/utils/consistency_checker/printer.py` | 74 | 一致性检查结果打印模块 ==================== 把检查结果打印成短日志。只打印，不抛异常。 |
| `src/pipeline/utils/consistency_helpers/__init__.py` | 55 | 流水线一致性检查器 — 辅助函数与子检查 ====================================== |
| `src/pipeline/utils/consistency_helpers/base.py` | 139 | 基础工具函数 |
| `src/pipeline/utils/consistency_helpers/cli.py` | 272 | CLI 处理辅助 |
| `src/pipeline/utils/consistency_helpers/mysql.py` | 248 | 顶层函数：_check_store_empty、_check_mysql_store_tables、_check_mysql_trace_consistency、_check_mysql_mapping_consistency |
| `src/pipeline/utils/consistency_helpers/print.py` | 162 | 打印辅助 |
| `src/pipeline/utils/consistency_helpers/repair.py` | 170 | 顶层函数：apply_stale_mapping_repair、apply_safe_missing_mapping_repair、reset_user_db、sync_excel_to_mysql |
| `src/pipeline/utils/consistency_helpers/store.py` | 50 | 顶层函数：_try_health_stats、_try_load_store_data |
| `src/pipeline/utils/coverage_store/__init__.py` | 53 | 覆盖率 MySQL 事实源存储 ======================== |
| `src/pipeline/utils/coverage_store/base.py` | 71 | 覆盖率 MySQL 事实源存储 - 基础 Mixin |
| `src/pipeline/utils/coverage_store/health.py` | 128 | 顶层类：_CoverageHealthMixin |
| `src/pipeline/utils/coverage_store/io.py` | 213 | 覆盖率 MySQL 事实源存储 - 导入导出 Mixin |
| `src/pipeline/utils/coverage_store/read.py` | 173 | 覆盖率 MySQL 事实源存储 - 读取 Mixin |
| `src/pipeline/utils/coverage_store/write.py` | 195 | 覆盖率 MySQL 事实源存储 - 写入 Mixin |
| `src/pipeline/utils/data_integrity_checker.py` | 148 | 顶层函数：validate_step_input |
| `src/pipeline/utils/diff_snapshot_store/__init__.py` | 134 | 版本快照 MySQL 事实源存储 (DiffSnapshotStore)（包入口） ======================================================== |
| `src/pipeline/utils/diff_snapshot_store/io_json.py` | 153 | 顶层类：_IoJsonMixin |
| `src/pipeline/utils/diff_snapshot_store/read.py` | 275 | 顶层类：_ReadMixin |
| `src/pipeline/utils/diff_snapshot_store/write.py` | 156 | 顶层类：_WriteMixin |
| `src/pipeline/utils/feedback_selectors.py` | 379 | 反馈条选择器 —— 全引擎**单一事实源**（错误/校验/成功提示）。 |
| `src/pipeline/utils/figma_image_store.py` | 508 | 顶层函数：_resolve_image_dir、_resolve_project、_extract_node_id、_detect_image_type、_get_image_dimensions、_collect_image_files |
| `src/pipeline/utils/figma_page_store.py` | 497 | Figma 事实表存储模块 ==================== 从 Figma document 中提取页面/节点/组件，写入 3 张独立事实表。 RAG 索引重建时从这 3 张表读取。 |
| `src/pipeline/utils/figma_state_store/__init__.py` | 155 | Figma 运行时状态 MySQL 事实源存储（包入口） ============================================= 把 Step1 figma 拉取链路的三份 JSON 状态文件沉淀到 MySQL 三张表… |
| `src/pipeline/utils/figma_state_store/change_log.py` | 195 | figma_change_sessions 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。 |
| `src/pipeline/utils/figma_state_store/node_hashes.py` | 212 | figma_node_hashes 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。 |
| `src/pipeline/utils/figma_state_store/quota.py` | 152 | figma_api_quota 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。 |
| `src/pipeline/utils/identity_orchestrator.py` | 844 | 多身份 / 多角色编排（B3，CAPABILITY_GAP_PRD §3.2）—— 登录态隔离 + 越权协同。 |
| `src/pipeline/utils/identity_pool.py` | 540 | 身份池容器（W3 T03，L0-2）—— 零 DDL：user_credentials_v2 单行 key=IDENTITY_POOL。 |
| `src/pipeline/utils/menu_matcher.py` | 138 | 需求→菜单纯算法匹配器（v5.14, 2026-08-04 通用化）。 |
| `src/pipeline/utils/nav_graph_read.py` | 350 | 导航数据只读适配层（P0-1 读侧迁移）。 |
| `src/pipeline/utils/nav_graph_store.py` | 760 | P0-3 3b: 导航图结构化存储（nav_graph_nodes / nav_graph_edges 读写）。 |
| `src/pipeline/utils/nav_match_logger.py` | 247 | 导航匹配日志落库（v5.15, 2026-08-04）——L2 LLM 语义匹配可观测化。 |
| `src/pipeline/utils/navigation_tree_builder.py` | 977 | 导航树构建器 ============ 2026-07-08 新增：合并 nav_graph_nodes（原 system_nav_maps，已于 2026-08-05 DROP）+ system_feature_list， 构建完整… |
| `src/pipeline/utils/pause_controller.py` | 165 | 全流程暂停控制器 (PauseController) =================================== A2 改造：由"进程级单例"改为"按 project 隔离的多实例字典"。 原实现：单 threading.… |
| `src/pipeline/utils/privilege_matrix.py` | 312 | 越权矩阵生成器（W4-C，P1）—— 纯函数，零 DB 写，零依赖。 |
| `src/pipeline/utils/requirement_slice_store/__init__.py` | 432 | 顶层类：RequirementSliceStore |
| `src/pipeline/utils/requirement_store/__init__.py` | 154 | 需求 MySQL 镜像存储（包入口） ============================== |
| `src/pipeline/utils/requirement_store/crud.py` | 16 | 需求 CRUD facade — 将读写操作拆分为 _write / _read mixin，本文件仅做组合。零逻辑变更。 |
| `src/pipeline/utils/requirement_store/crud_mixins/__init__.py` | 2 | （无模块文档串） |
| `src/pipeline/utils/requirement_store/crud_mixins/_read.py` | 78 | 顶层类：RequirementStoreReadMixin |
| `src/pipeline/utils/requirement_store/crud_mixins/_write.py` | 391 | 写入 mixin：upsert / update / reset_all_is_generated / delete。 |
| `src/pipeline/utils/requirement_store/excel_io.py` | 77 | Excel 导出 — 从 requirement_store.py 拆出，零逻辑变更。 |
| `src/pipeline/utils/requirement_store/health.py` | 91 | 顶层类：_HealthMixin |
| `src/pipeline/utils/review_lane_store.py` | 358 | P0-3 3a / P0-4: 三车道路由持久化（review_lane 表读写）。 |
| `src/pipeline/utils/review_store/__init__.py` | 31 | 审核 MySQL 事实源（ReviewStore） ================================== |
| `src/pipeline/utils/review_store/base.py` | 54 | ReviewStore 基础工具 Mixin ========================== 提供数据库连接、建表、时间工具等通用方法。 |
| `src/pipeline/utils/review_store/case_meta.py` | 883 | 顶层类：_ReviewIntentMetaMixin |
| `src/pipeline/utils/review_store/health.py` | 87 | ReviewStore 健康统计 + 委托 Mixin ================================== 健康统计和委托到其他 store 的方法。 |
| `src/pipeline/utils/review_store/review_log.py` | 155 | 顶层类：_ReviewLogMixin |
| `src/pipeline/utils/source_hashes_store/__init__.py` | 82 | Step1/Step2 源数据哈希存储（MySQL 唯一事实源） ================================================= |
| `src/pipeline/utils/source_hashes_store/base.py` | 84 | SourceHashesStore 基础 mixin：__init__、last_error、_project、_get_db、 _ensure_tables、_to_json、_from_json 以及模块级常量。 |
| `src/pipeline/utils/source_hashes_store/breakdown.py` | 211 | 顶层类：_SourceHashesBreakdownMixin |
| `src/pipeline/utils/source_hashes_store/confluence.py` | 184 | 顶层类：_SourceHashesConfluenceMixin |
| `src/pipeline/utils/source_hashes_store/health.py` | 55 | SourceHashesStore 健康统计 mixin：health_stats。 |
| `src/pipeline/utils/source_hashes_store/yapi.py` | 122 | SourceHashesStore YApi mixin： save_yapi_hash, load_yapi_hash, export_yapi_to_json, import_yapi_from_json。 |
| `src/pipeline/utils/trace_link_store/__init__.py` | 88 | 追溯链路 MySQL 事实源存储 ========================= |
| `src/pipeline/utils/trace_link_store/crud.py` | 345 | trace_links 写入：upsert / clear — 从 trace_link_store.py 拆出，零业务逻辑变更。 |
| `src/pipeline/utils/trace_link_store/io_export.py` | 97 | JSON 显式导入/导出 + Markdown 矩阵导出 — 从 trace_link_store.py 拆出，零业务逻辑变更。 |
| `src/pipeline/utils/trace_link_store/read_health.py` | 378 | 顶层类：_ReadHealthMixin |
| `src/pipeline/utils/validation.py` | 96 | 全仓唯一「无效断言」判定（F-00 引入，F-06a 复用）。 |

## src/rag　（4 个文件 / 973 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/rag/__init__.py` | 3 | P2-11 MultiModalRAG — 多模态 RAG 块存储与联合检索。 |
| `src/rag/integration.py` | 237 | P2-11 接入主流程: MultiModalRAG 索引/检索集成层。 |
| `src/rag/retriever.py` | 288 | P2-11 MultimodalRetriever — 多模态联合检索器。 |
| `src/rag/store.py` | 445 | P2-11 MultimodalStore — 多模态 RAG 块存储（CRUD）。 |

## src/testing_methods　（17 个文件 / 6380 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/testing_methods/__init__.py` | 38 | 测试方法引擎 — 12种经典软件测试设计方法 + 多层分类学筛选 |
| `src/testing_methods/base.py` | 105 | 测试方法基类 — 所有12种方法的标准接口 |
| `src/testing_methods/boundary_value.py` | 305 | 顶层类：BoundaryValueAnalysis |
| `src/testing_methods/branch_coverage.py` | 623 | 分支覆盖/判定覆盖 (Branch/Decision Coverage) — 白盒测试方法 |
| `src/testing_methods/catalog.py` | 77 | 测试方法论单一权威目录（18 种，覆盖 Web 应用对标高级 SDET 的用例设计全集）。 |
| `src/testing_methods/cause_effect_graph.py` | 529 | 因果图法 — 黑盒测试方法 |
| `src/testing_methods/condition_coverage.py` | 705 | 条件覆盖 (Condition Coverage) — 白盒测试方法 |
| `src/testing_methods/decision_table.py` | 490 | 判定表法 — 黑盒测试方法 |
| `src/testing_methods/equivalence_partitioning.py` | 325 | 等价类划分法 — 黑盒测试方法 |
| `src/testing_methods/error_guessing.py` | 522 | 错误推测法 — 基于测试经验和常见缺陷模式，推测可能出错的场景并设计测试用例 |
| `src/testing_methods/method_router.py` | 235 | 方法路由器 — 根据需求特征自动选择适用的测试方法组合 |
| `src/testing_methods/orthogonal_array.py` | 311 | 正交实验法 — 利用正交表从全组合中选取代表性测试点，以最少用例覆盖最多的参数组合 |
| `src/testing_methods/path_coverage.py` | 664 | 路径覆盖 (Path Coverage) — 白盒测试方法 |
| `src/testing_methods/scenario_method.py` | 324 | 场景法 — 通过分析业务流程的基本流和备选流，设计端到端场景测试用例 |
| `src/testing_methods/state_transition.py` | 230 | 状态迁移法 — 通过状态迁移图分析状态之间的转换规则，验证合法迁移和非法迁移 |
| `src/testing_methods/statement_coverage.py` | 436 | 语句覆盖 (Statement Coverage) — 白盒测试方法 |
| `src/testing_methods/taxonomy_filter.py` | 461 | 多层分类学筛选 — 防止 AI token 爆炸，三级漏斗过滤操作/断言 |

## src/users　（23 个文件 / 4776 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/users/__init__.py` | 92 | 用户管理模块 ============ 统一管理用户、项目授权、执行记录。 DB: MySQL (ai_test_engine) via db_manager |
| `src/users/_user_db_core.py` | 18 | 用户数据库模块 — 共享核心（常量 + _conn） |
| `src/users/audit.py` | 581 | 用户审计模块 — 执行跟踪 从 manager.py 拆分。 |
| `src/users/audit_logs/__init__.py` | 18 | 操作日志与配置历史模块 (package facade) |
| `src/users/audit_logs/_action_logs.py` | 270 | 操作日志相关函数（action_logs 表） |
| `src/users/audit_logs/_config_history.py` | 161 | 配置历史相关函数（config_history 表） |
| `src/users/auth/__init__.py` | 108 | 用户认证模块 — 加密 + 密码 + 登录 从 manager.py 拆分。 |
| `src/users/auth/encryption.py` | 354 | Fernet 密码加密：密钥读取/写入/缓存 + 加解密入口 — 从 auth.py 拆出，零业务逻辑变更。 |
| `src/users/auth/login.py` | 137 | 顶层函数：has_api_key、get_user_api_key、login、_record_login_tracking |
| `src/users/auth/password.py` | 152 | 密码强度校验 + SHA-256 哈希。 |
| `src/users/auth/rate_limit.py` | 127 | 登录锁定机制（MySQL 持久化）— 连续5次失败锁定15分钟。 |
| `src/users/bootstrap.py` | 199 | 顶层函数：_gen_random_password、_resolve_bootstrap_password、ensure_bootstrap_admin、ensure_bootstrap_default_project、run_all |
| `src/users/credentials.py` | 317 | 用户凭证管理模块 |
| `src/users/documents.py` | 197 | 顶层函数：record_document_version、get_document_versions、record_source_document、get_source_document、save_screenshot、get_scree… |
| `src/users/manager.py` | 116 | 用户管理器 — 兼容层 ==================== 此文件已拆分为三个模块: - src/users/auth.py — 加密 + 密码 + 登录 - src/users/user_db.py — DB连接 + 用户CRUD… |
| `src/users/project_auth.py` | 865 | 顶层函数：assign_project、transfer_owner、revoke_project、cleanup_project_roles、_role_cache_get、_role_cache_set |
| `src/users/reports.py` | 180 | 顶层函数：save_generated_report、get_generated_report、list_generated_reports、delete_generated_reports |
| `src/users/role_rbac/__init__.py` | 65 | 角色与 RBAC 管理模块 (package facade) |
| `src/users/role_rbac/_permission.py` | 230 | 权限管理 |
| `src/users/role_rbac/_role_crud.py` | 125 | 顶层函数：create_role、update_role、delete_role、get_all_roles、get_role |
| `src/users/role_rbac/_user_role.py` | 88 | 用户角色管理 |
| `src/users/user_crud.py` | 265 | 顶层函数：create_user、list_login_users、list_users、get_user_by_id、update_user、get_user_last_project |
| `src/users/user_db.py` | 111 | 用户数据库模块 — 兼容层（重新导出所有公开函数） |

## src/verification　（8 个文件 / 1569 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `src/verification/__init__.py` | 37 | 验证充分性核心模块（verification）。 |
| `src/verification/aggregate.py` | 155 | 台账聚合读函数（aggregate_ledger_summary）—— 流水线强化专项 T1/T5 共用。 |
| `src/verification/cli.py` | 214 | 验证台账命令行入口（verify CLI）。 |
| `src/verification/hooks.py` | 245 | 验证台账核对钩子（VerificationHook）—— 流水线强化专项 T1。 |
| `src/verification/ledger_store.py` | 185 | 验证台账存储层（LedgerStore）。 |
| `src/verification/remediation.py` | 163 | 补做状态机（RemediationStateMachine）。 |
| `src/verification/runner.py` | 336 | 验证运行器（VerificationRunner）：引擎接入 + 比对闭环。 |
| `src/verification/schema.py` | 234 | 验证台账数据模型与枚举定义（schema）。 |

## target_systems/　（27 个文件 / 2441 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `target_systems/__init__.py` | 9 | 靶子系统包（target_systems）。 |
| `target_systems/fullcov_sut/app.py` | 104 | fullcov_sut - 全能力覆盖靶子（资产管理域，Flask，127.0.0.1:8050）。 |
| `target_systems/fullcov_sut/auth.py` | 40 | fullcov_sut 认证：form_cookie 会话 + 角色守卫。 |
| `target_systems/fullcov_sut/blueprints/__init__.py` | 2 | （无模块文档串） |
| `target_systems/fullcov_sut/blueprints/assets_bp.py` | 187 | 资产 CRUD 蓝图：view/create/edit/delete/search + 分页 + 双重校验。 |
| `target_systems/fullcov_sut/blueprints/audit_bp.py` | 32 | 审计日志（admin 只读）+ 健康检查。 |
| `target_systems/fullcov_sut/blueprints/auth_bp.py` | 29 | 顶层函数：login、logout |
| `target_systems/fullcov_sut/config.py` | 19 | fullcov_sut 配置（全能力覆盖靶子 / 资产管理域）。 |
| `target_systems/fullcov_sut/db.py` | 68 | fullcov_sut 数据库辅助：连接、建表、种子、关闭。 |
| `target_systems/hospital_sut/app.py` | 104 | Hospital / Clinic Management SUT - Flask application entry point. |
| `target_systems/hospital_sut/auth.py` | 85 | Authentication and role-based access control helpers. |
| `target_systems/hospital_sut/blueprints/auth_bp.py` | 41 | Authentication blueprint: login / logout. |
| `target_systems/hospital_sut/blueprints/billing_bp.py` | 206 | Billing blueprint: CRUD + patient/medication association, amount auto-calc, and idempotency_key-based duplicate-submiss… |
| `target_systems/hospital_sut/blueprints/medications_bp.py` | 243 | Medications blueprint: CRUD (incl. stock) + CSV batch import (upsert by code). |
| `target_systems/hospital_sut/blueprints/patients_bp.py` | 236 | Patients blueprint: list (search / filter / paginate / batch-tag) + CRUD + detail. |
| `target_systems/hospital_sut/blueprints/records_bp.py` | 148 | Medical records blueprint: CRUD + patient association. |
| `target_systems/hospital_sut/blueprints/schedule_bp.py` | 134 | Schedule blueprint: CRUD + filter by date / department. |
| `target_systems/hospital_sut/config.py` | 33 | Application configuration for the Hospital / Clinic Management SUT. |
| `target_systems/hospital_sut/db.py` | 95 | Thin database layer: connection management, schema bootstrap and a generic server-side pagination helper shared by all … |
| `target_systems/hospital_sut/seed.py` | 203 | Deterministic seed data for the Hospital SUT. |
| `target_systems/hospital_sut/tests/conftest.py` | 89 | Pytest fixtures for the Hospital / Clinic Management SUT. |
| `target_systems/hospital_sut/tests/test_auth.py` | 44 | Authentication landing points (F-series: auth / session). |
| `target_systems/hospital_sut/tests/test_billing.py` | 52 | Billing landing points (F8 idempotency + amount auto-calc). |
| `target_systems/hospital_sut/tests/test_medications.py` | 86 | Medication CSV import landing points (F7 contract + F8 idempotency). |
| `target_systems/hospital_sut/tests/test_misc.py` | 49 | Misc landing points: pagination, search, batch-tag, error page. |
| `target_systems/hospital_sut/tests/test_patients.py` | 53 | Patient CRUD landing points. |
| `target_systems/hospital_sut/tests/test_rbac.py` | 50 | Role-based access control landing points (F1: privilege escalation / 越权). |

## tests/　（41 个文件 / 5783 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `tests/metrics_fetcher_acceptance.py` | 254 | （解析失败：SyntaxError: invalid non-printable character U+FEFF） |
| `tests/test_a11y/test_checker.py` | 256 | 单元测试：a11y 通用检查器（src/extensions/a11y/checker.py）。 |
| `tests/test_ai/test_base_hard_timeout.py` | 60 | 镜像 src/ai/ai_client_mixins/_base.py 的 _chat_with_hard_timeout 硬超时守护。 |
| `tests/test_ai/test_visual_verify_gate.py` | 65 | 回归测试：visual_verify 以「模型能力」判定是否发起视觉验证（2026-08-30 根因修复）。 |
| `tests/test_api/test_api_evidence.py` | 178 | 证据完整性回归测试：API 直调执行器必须把"实际观测"回写进结构化证据。 |
| `tests/test_case_generator/test_action_templates.py` | 284 | 单元测试：动作模板映射摆脱"假覆盖"——复杂控件不再统一落到 combo_key(Control+A)。 |
| `tests/test_case_generator/test_taxonomy_safety.py` | 93 | 回归测试：TaxonomyFilter 三层减量的「准确性安全属性」（2026-08-30 通用性加固）。 |
| `tests/test_chains/test_cross_cut_lifecycle.py` | 178 | 链路生命周期测试（L2 链级保障）—— 用真编排器走完整链，只在网络边界外全真。 |
| `tests/test_config_loader/test_dotenv_frozen.py` | 37 | 回归测试：.env 默认路径在 frozen(打包exe) 与源码态下正确选择。 |
| `tests/test_core/test_config_recommender.py` | 258 | 流水线推荐值统一算法（src/core/_config_recommender.py）单元测试。 |
| `tests/test_core/test_duplicate_definition_gate.py` | 121 | 常驻静态门禁：禁止「同一常量/词表被重复定义」（禁止重复造轮子的机械判据）。 |
| `tests/test_core/test_instruction_contract_gate.py` | 559 | 常驻「指令契约」门禁：生成端 ↔ 执行端必须共用同一事实源。 |
| `tests/test_core/test_redact.py` | 54 | 回归测试：通用敏感信息脱敏（src/core/utils/redact.py）。 |
| `tests/test_core/test_static_scope_gate.py` | 68 | 常驻静态作用域门禁：禁止「局部变量先读后写」（ruff F823 / F821）。 |
| `tests/test_frontend/test_design_tokens.py` | 80 | 设计令牌审计回归（2026-08-30 全站样式统一门禁） |
| `tests/test_frontend/test_test_methods_e2e.py` | 160 | 「测试方法论清单」端到端验证（Playwright 真实浏览器） 验证：导航 JS 注入 → 卡片渲染 → 点卡跳转筛选 → 横幅/返回闭环。 运行：python -m pytest tests/test_frontend/test_te… |
| `tests/test_frontend/test_ui_regression.py` | 178 | 前端 UI 回归（2026-09-15 锁定）— 防把已稳定的界面交互改坏的门禁。 |
| `tests/test_oracle/test_correctness_vlm.py` | 54 | correctness_vlm / VLMOracle 视觉校验单测。 |
| `tests/test_oracle/test_taxonomy_drift.py` | 77 | 常驻漂移门禁：三词表一致性（F5 根因修复的守护）。 |
| `tests/test_perf/test_perf_baseline_integration.py` | 143 | 性能·基线层 + 集成层(report/regression) 测试：跨运行回比与退化预警。 |
| `tests/test_perf/test_perf_config_collector.py` | 128 | 性能三层体系(采集→门禁→集成)·配置合并 + 采集数据契约测试。 |
| `tests/test_perf/test_perf_gate.py` | 126 | 性能·门禁层(gate)测试：绝对阈值、相对退化、NO_DATA fail-closed、汇总。 |
| `tests/test_perf/test_perf_oracle_persist.py` | 172 | 性能 oracle 唯一事实源落库测试（2026-09-03 通用性修复）。 |
| `tests/test_perf/test_step5_trend_section.py` | 155 | 单元测试：Step5 性能跨运行趋势区块渲染（render_perf_trend_section）。 |
| `tests/test_step3/test_methodology_full_flow.py` | 144 | 方法论全链路集成测试 — 用例生成真实用上 18 码。 |
| `tests/test_step4/test_action_contract.py` | 94 | 单元测试：统一 action 契约（双执行路径契约统一）。 |
| `tests/test_step4/test_agent_executor_authority.py` | 170 | 单元测试：step4 Agent 执行器「需求权威性 SKIP/FAIL 判决」（2026-08-30 通用性修复）。 |
| `tests/test_step4/test_checks_failure_classify.py` | 301 | 单元测试：Agent 探索成功后业务 checks 验证失败的失败归类（2026-09-02 根因修复）。 |
| `tests/test_step4/test_cross_cut_async.py` | 126 | 回归测试：横切门禁 async 统一入口（常规流水线/串行后端挂载用）。 |
| `tests/test_step4/test_empirical_executions.py` | 86 | 回归测试：方案B —— TargetEmpiricalVerifier 报告回写 executions（MySQL 唯一事实源）。 |
| `tests/test_step4/test_evidence_extractor_expected.py` | 104 | 回归测试：evidence 提取器中 expected 字段的通用性修复（报告阶段）。 |
| `tests/test_step4/test_sut_harness_resolve.py` | 37 | 回归测试：差距1 —— SutHarness._resolve_python_cmd 统一 python 解析。 |
| `tests/test_step4/test_verification_rule_meta.py` | 73 | 单元测试：Step4 检查执行算法规则元信息清单。 |
| `tests/test_step4/test_visual_gate.py` | 117 | 单元测试：step4 视觉横切门禁（visual_checker.run_visual_gate）。 |
| `tests/test_step4/test_visual_local_backend.py` | 44 | 回归测试：差距3 —— 本地视觉降级链在无 VLM 时能产出真实判定（非恒 NO_DATA）。 |
| `tests/test_step5/test_fake_pass_audit.py` | 150 | 假绿审计·正确口径测试：把「无证据(真假绿)」与「低置信带证据(需复核)」分开， 避免把真实带证据的通过误标为假 PASS（如 hospital_sut 4.76% 误标）。 镜像 src/pipeline/steps/step5/fak… |
| `tests/test_step5/test_report_rule_meta.py` | 38 | 单元测试：Step5 报告生成算法规则元信息清单。 |
| `tests/test_step5/test_run_summary_confidence.py` | 124 | 通用口径测试：SKIP（未真正验证）不应稀释平均置信度。 |
| `tests/test_step5/test_visual_report.py` | 91 | visual 报告区块测试（2026-09-03 易用性差距修复）。 |
| `tests/test_test_methods_acceptance.py` | 186 | 「测试方法论清单」API 验收测试 |
| `tests/test_users/test_project_auth.py` | 160 | 单元测试：项目单 owner 约束（自动降级）与会话迁移。 |
