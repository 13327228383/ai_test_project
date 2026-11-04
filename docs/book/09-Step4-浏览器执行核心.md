# 第 9 章　Step4 · 浏览器执行核心

## 概述

单用例的浏览器执行由 `browser_core` 包承担。执行体 `PlaywrightEngine` 在一个 run 内跨用例复用，单用例由 `_execute_one_intent`（`single_mixins/_base.py:64`）驱动其生命周期。能力以 mixin 组合方式拆分：结构化动作、AI 多轮兜底、Agent 探索后校验。策略 2（AI 自主探索）走 `_run_ai_round_loop`，受多重边界约束收敛空转。验证管道与策略 1/3 共用。

## 单用例执行生命周期

启动阶段在 `_execute_all`（`serial.py:174`）完成：创建 `PlaywrightEngine`、`BrowserAi`、`ApiTracker`、`DataFactory` 并 `await engine.start()`（`serial.py:244`-`253`）。运行级登录 `ai.login()` 失败时不中止全体，而是把全批候选标记为 `BLOCKED`（failure_category=`ENVIRONMENT_ERROR`）写入 `executions`（`serial.py:258`-`308`）。

每用例进入 `_execute_one_intent`（`_base.py:64`）：先车道闸门 `check_lane_gate`（`_base.py:106`）、强制停止检查（`_base.py:132`）、暂停检查点（`_base.py:147`），再 `_steps_from_case` → `_normalize_steps` 规整步骤（`_base.py:96`）。步骤循环内按 `step_action` 走结构化动作或 AI 兜底路径；断言与三轨校验在步骤末统一执行。证据在循环内由 `_record_intent_evidence`（`base.py:667`）采集（截图、DOM 文本）。关闭阶段由 `_execute_all` 的 `finally` 停止 engine 并 `_release_test_accounts`（`serial.py:605`，`:970` 注释）。

## mixin 组合与职责

`single.py:31` 的 `_BrowserCoreSingleMixin` 由四个单一职责 mixin 线性组合（`single.py:31`-`36`）：

| mixin | 职责 | 位置 |
|:---|:---|:---|
| `_BrowserCoreSingleBaseMixin` | 主框架：前置检查、插件、会话感知、导航、步骤循环、硬验证、结果汇总 | `single_mixins/_base.py:59` |
| `_BrowserCoreSingleStructuredMixin` | 结构化动作/断言执行（白名单 click/fill/select_option/navigate/upload/assert/verify） | `single_mixins/_structured.py:16` |
| `_BrowserCoreSingleAiLoopMixin` | AI 多轮循环兜底路径（仅当 step_action 不在结构化白名单时触发） | `single_mixins/_ai_loop.py:98` |
| `_BrowserCoreAgentChecksMixin` | Agent 探索后 checks 验证（§4.3 拆出） | `single_mixins/_agent_checks.py:249` |

组合顺序决定 MRO：Base 在前承接入口，Structured 与 AiLoop 提供动作实现，AgentChecks 处理策略 2 的校验收口。辅助模块（置信度门 `_confidence_gate.py`、断言判定 `_assertion_verdict.py`、证据抽取 `_evidence_extractor.py`、操作缓存 replay/record）以函数形式被 mixin 调用，不进入继承链。

## 结构化动作分发表

结构化动作（`click`/`fill`/`select_option`/`navigate`/`upload` 等）与结构化断言（`assert`/`verify`）走查表执行，不经过 AI。入口是 `_execute_structured_actions`（`browser_core/base.py:625`），它转调 `browser_structured_executor.execute_structured_actions`。需要留意的是 `single_mixins/_structured.py:19` 的同名方法只是占位声明（直接 `raise NotImplementedError`，注释标「实际实现由 BrowserCore 主类或其他 mixin 提供」），真正的实现在前一处；读代码时若只看 `_structured.py` 会误判为未实现。

查表执行器是 `dispatch_action`（`action_dispatchers.py:285`）与 `dispatch_assertion`（`:378`）。动作类型到执行工具的映射不硬编码在代码里，而是从数据库表 `action_actions` 的 `tool_name` 列生成（`_build_action_to_tool_map`，`action_dispatchers.py:41`），DB 是唯一事实源。映射状态分四态（`action_dispatchers.py:32`-`35`）：

| 状态 | 条件 | 处置 |
|:---|:---|:---|
| `VALID` | `tool_name` 非空且在 `ALL_TOOLS` 中 | 调 `_execute_tool(tool_name, args, engine)` 执行 |
| `EMPTY` | `tool_name` 为空且非工作流 | 返回 `handled=False` + `EMPTY_TOOL`，上层兜底 |
| `INVALID` | `tool_name` 非空但不在 `ALL_TOOLS` | 记 warning，返回 `handled=False` + `INVALID_TOOL` |
| `WORKFLOW` | `is_workflow=1` 且 `tool_name` 空 | 交工作流专用通道，返回 `WORKFLOW_ONLY` |

四态的意义是消除历史上的「静默剔除」：工具缺失或配置错误不再表现为「这个动作不存在」，而是带明确 reason 回退，让上层与日志都看得到（`:320`-`342`）。参数由 `_convert_action_to_tool_args`（`:97`）统一归一（`target_hint`/`selector`/`text`/`option_text`/`file_path`/`url`/`key` 等），断言由 `_convert_assertion_to_tool_args`（`:240`）按白名单透传。`navigate` 的 URL 在送入工具前由 `_resolve_navigate_url`（`browser_structured_executor.py:38`）预处理：识别 `intent:` 语义前缀走完整菜单导航、中文页面名查路由表、相对路径补全为绝对路径。

新增一个动作的路径只有两步：在 `action_actions` 写入含 `tool_name` 的记录，并保证该 tool 已注册进 `ALL_TOOLS`，代码零改动。这是「不重复造轮子」约束的直接落地——Playwright 原子操作只在 `browser_tools` 实现一次。`dispatch_action` 不静默 miss 的边界：action 不在库中（AI 编造操作名）返回 `UNKNOWN_ACTION`；在库但不在映射中返回 `NO_TOOL_MAPPING`；两者都记 `record_op_cache_miss` 指标（`:308`-`317`）。映射结果缓存在 `_action_to_tool_map_cache`，库写操作后由 `invalidate_cache`（`:83`）广播失效，避免脏缓存。

## AI 探索循环

策略 2 的动作执行主体是 `_run_ai_round_loop`（`_ai_loop.py:107`）。循环条件 `while ai_round < _effective_max_rounds and not ai_passed`（`_ai_loop.py:223`）。轮数上限 `_effective_max_rounds`：结构化动作全失败时取 5，否则取 `MAX_AI_ROUNDS`（`_ai_loop.py:140`）。`MAX_AI_ROUNDS` 经 property 解析，默认 8，受 `browser_max_ai_rounds`/`max_ai_rounds` 配置覆盖（`ai/base.py:24`-`25`）。

每轮流程：取页面指纹与剪枝后 DOM（`_get_dom_with_retry`，`_ai_loop.py:265`）→ SmartLocator 三层分流（`:271`-`315`）→ 拼 prompt（`_build_ai_prompt`，`_ai_loop_phases.py:263`）→ 查 op_cache（`_query_op_cache_v2`，`_ai_loop_phases.py:414`）→ `ai.decide_action` 决策（`_ai_loop.py:389`/`:477`）→ 派发 DOM 交互 → 回填交互标记。

## DOM 智能剪枝：八层过滤

`get_structured_state`（`playwright_engine/dom_extraction.py:20`）是页面 DOM 进入引擎的唯一入口。它在浏览器内执行一段 JS 遍历（`:53`-`182`），按序施加八层过滤，只把交互元素留下：

| 层 | 判定 | 代码 |
|:---|:---|:---|
| 1 节点类型 | `node.nodeType !== 1`（文本/注释节点）跳过 | `:130` |
| 2 跳标签 | `SKIP_TAGS = {script, style, svg, noscript, template}` | `:55`, `:132` |
| 3 无障碍隐藏 | `aria-hidden="true"` 跳过 | `:133` |
| 4 可见性 | `isVisible()`：`display:none` / `visibility:hidden\|collapse` / `offsetWidth==0 && offsetHeight==0` | `:83`, `:134` |
| 5 装饰图 | `img` 无 `alt` 或 `alt` 仅空白跳过 | `:135`-`138` |
| 6 交互性 | `isInteractive()`；非交互节点不保留自身，仅递归其子节点 | `:114`, `:139` |
| 7 视口 | `viewport_only=true` 时 `isInViewport(bbox)` 之外只递归不保留 | `:105`, `:171` |
| 8 文本截断 | `text` 长度 `< 200` 才收 | `:146` |

八层里第 3、4、5、6 层回答「这个节点要不要看」，第 7 层回答「这一轮要不要给」，第 8 层回答「给多少」——三种语义不同，不能合并成一条。留下的元素只带属性白名单：`tag`/`text`/`html_id`/`class`/`name`/`role`/`aria-label`/`href`/`value`/`placeholder`/`type`（`:144`-`163`），其余属性丢弃。

第 6 层的 `isInteractive()` 是剪枝正确性的关键，也是历史上出过最严重误剪的地方。原实现只认 `role='button'`，导致遵循 W3C ARIA 的 `menuitem`/`tab`/`treeitem` 以及 Ant Design/ElementUI 的侧边栏菜单（`<li role="menuitem">`）全部被过滤；某 SPA 导航后只识别出 17 个交互元素，AI 八轮循环找不到目标菜单，300s 超时（`dom_extraction.py:25`-`39` 根因记录）。修复是三层扩展：

- `INTERACTIVE_ROLES` 补齐 W3C ARIA 的全部交互角色（`button`/`link`/`menuitem`/`menuitemcheckbox`/`menuitemradio`/`tab`/`treeitem`/`option`/`checkbox`/`radio`/`switch`/`slider`/`spinbutton`/`combobox`/`textbox`/`scrollbar`，`:62`-`66`）；
- 框架菜单 class 兜底 `hasFrameworkMenuClass()`（`.ant-menu-item`/`.ant-pro-menu-item`/`.el-menu-item`/`.el-submenu__title`/`.n-menu-item`，`:73`-`81`）；
- 业务自定义菜单属性 `[data-key]`/`[data-menu-id]`。

`isInteractive()` 还认 `onclick` 与 `tabindex` 属性（`:119`-`120`）。这次修复不是增补一个选择器，而是把「什么算可交互」从「只认 button」改回 W3C 规范定义——同一语义（可交互角色）只有一处权威定义。它同时影响 DOM 探索、AI prompt 构建、元素定位三条链路。

剪枝解决的核心问题是 token 成本与定位不确定性：把整页 `innerHTML` 喂给 LLM 的规模不可控，剪枝后只留交互元素的紧凑向量，规模有上界。`get_structured_state` 另有一个反「沉默即失败」的约束：`_page` 为空时历史上返回 `[]`，调用方无法区分「页面确无元素」与「page 已失效」，进而在 `None` 上 `wait_for_timeout` 崩溃；现改为抛 `RuntimeError`（`:41`-`50`），让 18 处调用方在各自 `try` 内统一感知 page 失效。截图 `get_screenshot()` 同理加 15s 硬超时（`:11`、`:229`），因为 `page.screenshot` 在 Windows IOCP 上会无超时挂起（`:223`-`225`）。

## data-ai-id 注入与定位可复现

剪枝遍历在保留每个交互元素时做两件事：分配序号并写回 `data-ai-id`（`node.setAttribute('data-ai-id', 'ai-N')`，`:166`-`167`），以及记录 `bbox`（`:169`）。`data-ai-id` 是该轮页面的稳定锚：AI 决策只需回传 `ai-7` 这样的 id，执行端用 `[data-ai-id="ai-7"]` 精确定位，不必让 AI 推断 CSS 选择器。这消掉了「AI 猜选择器」这一整类不确定性。

元素总数在剪枝计数时由 JS 一并返回（`{elements, total}`，`:181`），引擎把它记到 `_last_original_count`（`:193`），作为「剪枝前规模」用于日志与证据。页面指纹 `get_page_fingerprint`（`:205`）返回 `{url, title, ai_elements}`，其中 `ai_elements` 是当前 `[data-ai-id]` 计数（`:212`-`:214`），供 AI 循环判断「这一轮页面是否变化」。

## SmartLocator：三层置信度分流

剪枝后的元素列表在喂给 AI 之前，先经过 `SmartLocator` 做一次本地匹配（`browser_core/_smart_locator/__init__.py:68` `match_step_to_element`）。它的目的写在模块头注释里：不让 AI 在大 DOM 中「漫游」，能确定性定位的就别调 AI。流程四步：

1. 关键词提取 `extract_keywords`（`_keyword_match.py:66`）：中文取 2-6 字连续片段，并按 2/3/4 字滑窗切分（覆盖「点击保存按钮」这类无空格串）；英文取 ≥3 字符词并过滤停用词。来源是 `step_desc` + `step_expected` + 用例标题 + `actions` 的 `target_hint`/`value` 等（`__init__.py:93`-`104`）。
2. 同义词扩展 `expand_with_synonyms`（`_keyword_match.py:115`）：通用字典 `COMMON_LOOKUP` + 项目字典 `PROJECT_LOOKUP`，原词与同义词分开返回。
3. 加权评分 `score_element`（`:151`）：字段权重 `text`+10 / `aria-label`+8 / `placeholder`+6 / `id`+5 / `html_id`+5 / `name`+4 / `href`+3 / `class`+2；另有 tag 隐式加权——意图含「输入」时 `input`/`textarea` +5、「点击」时 `button`/`a` +5、「选择」时 `select` +5，`checkbox`/`radio`/`file` 同理（`:49`-`63`）。计分规则是原词全额、同义词仅在该字段无原词命中时计入（`:179`-`185`），避免同义扩展压过原词。
4. 置信度分类 `classify_confidence`（`_fusion.py:24`）：`HIGH` = top1 ≥ 15 且 top1−top2 ≥ 5；`MEDIUM` = top1 ≥ 8；否则 `LOW`（阈值常量 `_fusion.py:16`-`21`）。

分流决策在 `_ai_loop.py:288`-`315`：

| 置信度 | 处置 | 相对成本 |
|:---|:---|:---|
| HIGH | 直接定位执行（`_execute_0token_smart_locator`），跳过本轮 AI | 0 token |
| MEDIUM | Top-3 候选喂 AI 二选一 | 约 200 token |
| LOW | 全 DOM 兜底 | 约 1500 token |

HIGH 路径若执行失败会回退到 AI（返回状态 `fallback_to_ai`，`_ai_loop.py:290`-`302`），不做静默成功。整个模块零 AI / embedding / RAG 依赖，是纯本地规则加字典。边界要写清：`SmartLocator` 只服务于 `single.py` 的 AI 兜底分支，不是结构化动作的主路径——主路径查表执行，不经过它。

## 探索边界约束

循环内置多层熔断，全部从配置读取默认值：

| 约束 | 默认值 | 配置键 | 行号 |
|:---|:---|:---|:---|
| 轮数上限（正常） | 8 | `browser_max_ai_rounds` | `ai/base.py:25` |
| 结构化全失败轮数 | 5 | — | `_ai_loop.py:140` |
| 连续无进展（指纹不变且无成功交互）早停 | 3 | `ai_loop_max_no_progress_rounds` | `_ai_loop.py:185`-`187` |
| 连续同动作重复 | 2 | `ai_loop_max_no_change_rounds` | `_ai_loop.py:165`-`167` |
| 同动作重复最多重置次数 | 1 | — | `_ai_loop.py:200`-`201` |
| AI 无响应重试 | 2 | — | `_ai_loop.py:444` |
| 无 DOM 动作空闲超时 | 60s | `step4_idle_timeout_sec` | `_ai_loop.py:205` |
| 单用例 AI 兜底总预算 | 120s | `step4_ai_loop_budget_sec` | `_ai_loop.py:214`-`216` |

判据改进点：重复检测从「只看动作签名」改为「页面指纹（URL+可见文本长度）+ 是否成功派发 DOM 交互」双维（`_ai_loop.py:170`-`193`），避免表单填充等正文不变场景被误杀（Fix B/G，2026-09-27）。总预算熔断独立于整用例 asyncio 硬超时，到点主动收口保留证据（`_ai_loop.py:208`-`236`）。

## 探索导航边界

策略 2 的导航由 `_exploratory.py` 承担，职责边界为「Agent 做决策、Step4 只调度」（`_exploratory.py:8`）。导航图 `nav_graph_nodes` 在 `_preflight_build_nav_graph` 中构建写入（`_exploratory.py:82`，INSERT 于 `:278`）；当 `nav_graph_nodes` 计数 ≤10 时执行预检展开侧边栏（`:122`）。菜单定位用 `_match_module_to_menu`，相似度基于 LCS `_lcs_similarity`（`:797`）与 Jaccard `_jaccard_char_similarity`（`:822`）。侧边栏菜单文本经 `_get_sidebar_cache` 同批次用例共享，避免重复 `_expand_and_capture_full_menu`（`:63`）。生成端 `test_steps` 作为已知操作路径注入探索 prompt，非强制指引（`_steps.py:468`）。

## 页面上下文保持

单个 `PlaywrightEngine` 在 `_execute_all` 内创建并跨全部候选用例复用（`serial.py:244`-`252`），上下文（cookies/localStorage/页面）在用例间不重建。每用例执行前做全局状态清理：`_reset_page_state` 关闭残余弹窗（Escape + 常见关闭按钮选择器，`serial.py:23`）与 `_recover_network_fault` 强制 `set_offline(False)` 恢复断网（`serial.py:69`）。跨用例身份由 `_switch_case_identity`/`_restore_case_identity` 切走再切回，避免串号（`serial.py:88`-`155`）。

## 失败归因

用例失败经 `record_execution` 落 `executions`，写入 `failure_category` 与 `failure_chain`（`audit.py:131`-`133`）。登录失败归 `ENVIRONMENT_ERROR`（`serial.py:299`）；车道阻断归 `LANE_BLOCKED`（`_base.py:127`）；AI 未能在轮数内完成归为 ERROR 并带 `step_error`（`_ai_loop.py:1237`）。分类器实现见 `failure_classifier.py`，逐类枚举由该模块产出（本次未逐行精读，见待核实）。

## 设计取舍

- 单 engine 跨用例复用：省去每例冷启动开销，代价是跨用例状态污染风险，靠 `_reset_page_state`/`_recover_network_fault` 兜底。
- AI 循环多判据收敛：用墙钟预算 + 指纹 + 交互三维替代单一「同动作」判据，代价是配置项增多、调参面变宽。
- 结构化白名单外才进 AI 兜底：把发散/非结构化动作限制在兜底路径，保证主路径可预测、可观测。
- 剪枝先于 AI：用一次 JS 遍历换取 prompt 规模上界与 `data-ai-id` 稳定锚，代价是剪枝规则本身成为正确性单点（`isInteractive` 误剪即全链路失效）。
- SmartLocator 前置确定性匹配：HIGH 走 0-token、MEDIUM 收窄到 Top-3，把 AI 调用压到最小；代价是关键词/同义词字典与阈值需维护，误匹配时仍要回退 AI。

## 本章代码依据

- 生命周期：`serial.py:174` `_execute_all`；`:244`-`253` 启动；`:258`-`308` 登录失败 BLOCKED；`_base.py:64` `_execute_one_intent`；`base.py:667` `_record_intent_evidence`
- mixin：`single.py:31` `_BrowserCoreSingleMixin`；各 mixin 行号见上表
- 结构化动作：`base.py:625` `_execute_structured_actions`；`:632` `_execute_structured_assertions`；`single_mixins/_structured.py:19`（占位）；`browser_structured_executor.py:38` `_resolve_navigate_url`；`action_dispatchers.py:285` `dispatch_action`、`:378` `dispatch_assertion`、`:41` `_build_action_to_tool_map`、`:32`-`35` 四态常量、`:97`/`:240` 参数转换、`:83` `invalidate_cache`
- AI 循环：`_ai_loop.py:107` `_run_ai_round_loop`；`:223` 循环条件；`:140` 轮数；`:185`/`:205`/`:214` 约束
- DOM 剪枝：`playwright_engine/dom_extraction.py:20` `get_structured_state`；`:53`-`182` JS 遍历；`:55` `SKIP_TAGS`；`:62`-`66` `INTERACTIVE_ROLES`；`:73`-`81` 框架菜单 class；`:114` `isInteractive`；`:166` `data-ai-id`；`:41`-`50` page 失效抛错；`:11`/`:229` 截超时；`:205` `get_page_fingerprint`
- SmartLocator：`_smart_locator/__init__.py:68` `match_step_to_element`；`_keyword_match.py:66`/`:115`/`:151`；`_fusion.py:24` `classify_confidence`、`:16`-`21` 阈值；接入点 `_ai_loop.py:271`-`315`
- 轮数默认：`ai/base.py:24`-`25` `MAX_AI_ROUNDS`
- 探索：`_exploratory.py:82` `_preflight_build_nav_graph`；`:278` 写 `nav_graph_nodes`；`:797`/`:822` 相似度；`:63` 侧边栏缓存
- 上下文：`serial.py:23` `_reset_page_state`；`:69` `_recover_network_fault`；`:88` `_switch_case_identity`
- 归因：`audit.py:131`-`133` `failure_category`/`failure_chain`

## 待核实

- `failure_classifier.py` 的 `failure_category` 取值枚举与 `failure_chain` 结构未逐行精读，章节仅依据 `audit.py` 形参与 `serial.py` 调用处推断。
- 策略 2 的 `verify_step` 与策略 1 三轨校验是否走同一 `verification` 管道的同一入口，需在 `verification/__init__.py` 进一步核对（第 10 章范围）。
- 单 engine 复用时 `engine._page` 在并行路径 `parallel.py` 下的归属（每 task 是否各自 page）未确认。
- ~~`SmartLocator` 三个阈值的取值~~ → **已核实（2026-10-15）：三个常量确在代码中，但本章引的路径需更正，且 `MEDIUM_SCORE_MIN` 的值存疑。** 实测位置是 `src/pipeline/steps/step4/browser_core/_smart_locator/_fusion.py:16-18`（不在 `SmartLocator` 主文件）：`HIGH_SCORE_MIN = 15`（`:16`，注释说明是「text 命中原词 +10 与 tag 加权 +5 的起步分」）、`HIGH_GAP_MIN = 5`（`:17`，注释说明是为避免 Top1/Top2 歧义）。`MEDIUM_SCORE_MIN`（`:18`）**不是本章写的 8**，它是跨行的表达式赋值，需按实际表达式求值，本章的 8 未获代码证实。阈值敏感性回放仍**未做**，此项保留。
