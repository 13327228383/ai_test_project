# 第 20 章　Web 控制台 · 前端与实时日志

## 概述

Web 控制台前端位于 `src/frontend/`，由三块构成：服务端渲染的 Jinja 模板（`templates/`）、原生 JS/CSS 静态资源（`static/`）、以及一个独立的实时日志面板子项目（`log-panel-ui/`）。`verify_lock.py` 与 `FRONTEND_LOCK.json` 构成前端完整性校验机制。本镜像为 Flask 服务端模板渲染，主体不依赖前端框架；只有日志面板使用 React/TypeScript 单页应用，独立构建后嵌入。

`templates/` 下共 **40 个 HTML 模板**（含公共布局壳 `_layout_base.html`，Glob 实测）。其中约 30 个在侧栏有入口，其余为流程内跳转的子页（如 `pipeline.html`、`intent-edit.html`、`file-upload.html`、`log-panel.html`、`login.html`、`register.html`）。

## 侧栏导航结构

`_layout_base.html` 是所有页面的公共外壳，定义侧栏的 8 个可折叠分区（`data-section` 属性）+ 常驻的「控制台」入口与「步骤区」。分区归属以此文件为准（`_layout_base.html:118`-`210`）：

| 分区 | `data-section` | 侧栏页面（URL 路径 → 模板） |
|:---|:---|:---|
| 步骤区（常驻） | — | Step1–5 徽标 + 「已执行」计数 + 「一键执行」按钮（`:119`-`138`） |
| 控制台 | — | 控制台 `/dashboard` → `dashboard.html`（`:145`） |
| 审核区 | `review` | 需求审核 `/review`、用例审核 `/intent-review`、需求澄清 `/clarify`、置信度拦截 `/confidence-block`（`:147`-`153`） |
| 指标区 | `metrics` | 深度指标 `/metrics-page`、AI 健康度 `/ai-health`、质量治理面板 `/quality-panel`、指令治理面板 `/action-lib`、算法规则清单 `/skill-rules`、Prompt 模板 `/prompt-templates`（`:156`-`164`） |
| 文档区 | `docs` | 文档中心 `/docs`、可视化报告 `/charts`、站点导航树 `/nav-tree`、数据导出 `/export-page`、清除数据 `/clear-files`（`:167`-`174`） |
| 日志区 | `logs` | 实时日志面板 `/realtime-logs`、诊断中心 `/diagnostics`、审计日志 `/audit-logs`（`:177`-`182`） |
| 项目区 | `project` | 项目列表 `/project-mgmt`、项目配置 `/config`、版本管理 `/version-mgmt`、配置历史 `/config-history`、新建项目 `/new-project`（`:185`-`192`） |
| 配置区 | `config` | 流水线配置 `/pipeline-config`、Step4 执行配置 `/step4-config`、系统配置 `/system-config`、CI 集成 `/ci-integration`（`:195`-`201`） |
| 用户区 | `user` | 个性化配置 `/personal-config`、修改密码 `/change-password`、用户管理 `/user-mgmt`、角色管理 `/role-mgmt`（`:204`-`210`） |

侧栏样式由 `app-common.js` 的「侧栏统一定型入口」（`app-common.js:663`）在首帧前完成，手风琴折叠靠 `sidebar-section collapsible` 类与 `active_nav` 判定（`:563`）；另有侧栏一键隐藏（`localStorage` 记忆，`:548`）。`verify_lock.py:245` 从 `_layout_base.html` 提取 `sidebar-section-title` 区块标题，与锁文件 `expected_sections`（8 个）比对。

可见性由权限驱动：带 `data-role`/`data-perm` 的 `nav-item` 由 `app-common.js`「权限驱动显隐」按用户权限隐藏（`:492`）。

## 前端功能清单

各页面的对外功能（依据侧栏标签、页面 URL 与对应静态 JS；未逐一精读页面内部实现的项见「待核实」）：

| 页面 | 功能 |
|:---|:---|
| `/dashboard` 控制台 | 首页总览：KPI 卡片、执行状态分布、趋势、台账不符明细（5 步进度与「一键执行」入口在侧栏步骤区） |
| `/review` 需求审核 | Step2 闸门：需求逐条批准/驳回 |
| `/intent-review` 用例审核 | Step3 闸门：用例逐条审核（配合三车道 review_lane） |
| `/clarify` 需求澄清 | 歧义卡片逐条作答 → 回填 `requirements` → 解除闸门（`clarify.js`） |
| `/confidence-block` 置信度拦截 | 低置信度用例的拦截与复核 |
| `/metrics-page` 深度指标 | 指标中心主体，消费 `/api/metrics-summary` 的 12 域，含统一 KPI tab |
| `/ai-health` AI 健康度 | AI 调用健康、幻觉、token 等 |
| `/quality-panel` 质量治理面板 | D1 九类能力只读摘要 + D2 开关明细（`quality-panel.js`） |
| `/action-lib` 指令治理面板 | 操作指令库（`action_actions`）的查看与治理 |
| `/skill-rules` 算法规则清单 | 展示 Step2/3/4 的规则注册表（含第 10 章的 `_rule_meta` 24 条） |
| `/prompt-templates` Prompt 模板 | AI 提示词模板管理 |
| `/docs` 文档中心 | 5 Tab 工作台：追溯矩阵 / 覆盖 / 冒烟 / 版本 等（`docs-workbench.js`） |
| `/charts` 可视化报告 | Step5 报告的图表化呈现（消费 `/api/report/chart-data`） |
| `/nav-tree` 站点导航树 | 靶子站点导航图（`nav_graph_nodes`）浏览 |
| `/export-page` 数据导出 | 结果数据导出 |
| `/clear-files` 清除数据 | 产物清理（`data-perm="file.clear"`） |
| `/realtime-logs` 实时日志面板 | SSE 实时日志（内嵌 `log-panel.html` + `log-panel-ui` 产物） |
| `/diagnostics` 诊断中心 | 依赖探针与运行时诊断 |
| `/audit-logs` 审计日志 | 操作审计 |
| `/project-mgmt`·`/new-project`·`/version-mgmt` | 项目选择/新建、版本管理 |
| `/config`·`/pipeline-config`·`/step4-config`·`/system-config`·`/config-history` | 项目配置、流水线配置、Step4 执行配置、系统配置、配置变更历史 |
| `/ci-integration` CI 集成 | 外部 CI 触发入口 |
| `/personal-config`·`/change-password`·`/user-mgmt`·`/role-mgmt` | 个性化（含背景与配色）、改密、用户与角色管理 |
| `/pipeline`·`/step3-case-upload`·`/intent-edit`·`/file-upload` | 流水线控制台与流程内子页（上传用例、编辑用例、文件上传） |

## 共享运行时（app-common.js）

`src/frontend/static/app-common.js`（2,038 行）是全部页面共用的运行时，挂 `window._A`。它提供（分节标题见源文件）：

- **基础设施**：命名空间、DOM 工具、时间格式化、`A.api()`、`GET` 微缓存（`:92`，修复「每页进入都重新加载数据」）、Toast、日志兼容函数。
- **交互组件**：Tooltip（含边界避让 `:258`、鼠标跟随 `:1589`、自动 ellipsis `:1445`）、按钮防重放/加载态（`:306`）、Loading、导航加载条（`:1669`）、骨架屏（`:1721`）。
- **鉴权与权限**：登录态（`:405`）、权限加载与首帧快照（`:443`）、权限驱动显隐（`:492`）、退出登录（`:919`）。**权限显隐在首帧完成**，避免无权限元素闪现。
- **框架级逻辑**：`_onAuthOk` 鉴权后回调（`:862`，与页面初始化分离）、当前项目/版本后台加载（`:831`/`:847`）、页面引导 `_pageInit`（`:1422`）、BFCache 恢复只刷鉴权与徽标不重跑初始化（`:1436`）。
- **侧栏**：一键隐藏（`:548`）、手风琴折叠（`:563`）、统一定型入口（`:663`）、Step 徽章全局轮询（`:938`，每 5 秒更新 `stepBadge1-5`，全页共享）。
- **个性化**：背景纯绘制（`:1091`）、个性化配置全局应用（`:1138`）、4 色块背景与文字色联动（`:1360`）。

约束：禁止外部 CDN（CSP `default-src 'self'`），图形一律 CSS/内联 SVG 手写；拼进 `innerHTML` 的动态数据必须经 `escHtml` 转义。

## 指标口径单一定义源（metrics-def.js）

`src/frontend/static/metrics-def.js`（269 行）是**全站 KPI 口径的唯一事实源**。它把「取哪里、怎么判」从模板里抽出，三个模板（`dashboard.html`/`charts.html`/`metrics.html`）只负责取值与渲染、不再自带阈值数字。历史动因是同一 KPI 在三处各写阈值导致打架（P2-1，见第 13 章「口径收敛的由来」）。

它定义 16 个 KPI（`metrics-def.js:39`-`148`），每个含 `thresholds{dir,good,warn}` + `endpoint`/`path`/`fallback`（数据源与取值路径）+ `desc`（三段式口径：是什么 / 怎么算 / 大小意义）：

| KPI | 单位 | 阈值（方向/绿/黄） | 数据源路径 |
|:---|:---|:---|:---|
| `total_cases` / `passed_cases` / `failed_cases` / `skipped_cases` | — | 无（纯计数） | `/api/report/chart-data` `overview.*` |
| `case_pass_rate` | % | up / 90 / 70 | `/api/metrics-summary` `step4_plugins.case_pass_rate` |
| `step_pass_rate` | % | up / 90 / 70 | `step4_plugins.step_pass_rate` |
| `recipe_only_pass_rate` | % | up / 90 / 70 | `step4_plugins.recipe_only_pass_rate` |
| `flaky_rate` | % | down / 5 / 15 | `ai_overall.flaky.rate` |
| `three_track_inconsistency` | % | down / 10 / 20 | `step4_plugins.verification_disagree_rate` |
| `req_coverage` | % | up / 90 / 80 | `step2_breakdown.coverage.forward_rate` |
| `evidence_coverage` | % | up / 80 / 60 | `ai_overall.evidence_coverage` |
| `case_completeness` | % | up / 80 / 60 | `ai_overall.case_completeness` |
| `ai_confidence` | — | up / 70 / 50 | `step2_breakdown.confidence.mean` |
| `ai_loop_pass_rate` | % | up / 80 / 60 | `fusion.ai_loop_pass_rate` |
| `hallucination_total` | — | down / 0 / 5 | `ai_overall.hallucination.total` |
| `guard_reject_rate` | % | down / 30 / 50 | `step4_plugins.guard_reject_rate` |

`metrics-def.js` 的 `value()`（`:185`）按定义从 summary 或 chart 响应体取值，取不到时走 `fallback`，仍取不到返回 `null`；`state()`（`:200`）按 `dir` 把值映射为 `success/warning/error/neutral`——**无阈值定义或取不到值一律 `neutral`，不允许把「没数据」渲染成「达标」**；`format()`（`:230`）取不到值统一渲染 `—`，**禁止用 0 冒充**。这套「缺失即中性、占位不造假」的约定与引擎的判定纪律一致。

## 专用 JS 模块

- `kpi-overview.js`（259 行）：统一 KPI 渲染（`window._renderUnifiedKpi`/`_loadUnifiedKpi`）。渲染 13 张卡片（每张带**固定** `source` 表标注，`:49`-`63`）、状态分布（PASS/FAIL/FLAKY/SKIP/BLOCKED/PRE_GATE_BLOCKED/ERROR，`:98`）、趋势折线（内联 SVG，近 10 次，数据缺口断线且图例标「暂无数据」，`:131`-`190`）、台账不符明细表（`:193`）。数据源 `GET /api/kpi/overview`，惰性加载 + 缓存 + `force` 强刷（`:236`）。
- `quality-panel.js`（768 行）：W3 质量治理面板。数据源全为只读 GET（`/api/quality/w3-summary`、`/api/config/flags`），唯一写操作是 `/api/config/toggle`；**绝不触发任何执行端点**；全文件零 `innerHTML`（`textContent` 落地，XSS 红线）；图表手写 SVG（`createElementNS`），无外部库。
- `docs-workbench.js`（809 行）：文档中心 5 Tab 工作台，覆盖 20 个 `docs_bp` 接口（追溯/覆盖/冒烟/版本），Tab 懒加载（首次激活才发请求）。
- `clarify.js`（230 行）：需求澄清问答页，从 `/api/step2/clarify/sessions` 拉歧义卡片，作答 POST `/api/step2/clarify/answers` 回填 `requirements`，再 POST `/api/step2/clarify/release` 解除闸门；作答/解除按钮带 `data-perm="execution.run"`。

## 技术栈

主体控制台是原生服务端模板 + 原生 JS，不含前端框架。`src/frontend/static/` 下的 `app-common.js`、`app-design.css` 等为普通 ES5/ES6 脚本与 CSS（`FRONTEND_LOCK.json:14-22` 锁定的正是 `_layout_base.html` 与 `app-design.css`）。

日志面板是唯一使用框架的部分：`log-panel-ui/package.json:14-21` 声明 `react@18`、`react-dom`、`react-router-dom@7`、`zustand`（状态管理）、`tailwindcss`、`vite`。它用 TypeScript + Vite 构建，产出物部署到 `src/frontend/static/log-panel/`（`index.html` + `index-*.js` + `index-*.css`，由 `log-panel-ui/build-and-deploy.sh` 完成拷贝）。`log-panel.html` 作为壳页加载该产物。

## 静态资源组织（static/）

`src/frontend/static/` 按职责拆文件：公共脚本 `app-common.js`；专用脚本 `kpi-overview.js`、`metrics-def.js`、`quality-panel.js`、`docs-workbench.js`、`clarify.js`；公共样式 `app-design.css`（3,331 行，含 design token）；`log-panel/` 子目录存放构建后的日志面板。图片资源 `favicon.jpg`、`logo.jpg`、`user_bg/`、`BG/`（含 `bg_config.json`）单独存放。

## 实时日志面板（log-panel-ui）

`log-panel-ui` 是独立 React+TS 项目，消费后端的 SSE 实时日志流。关键实现：

- 订阅方式：`src/frontend/log-panel-ui/src/hooks/useSSELogStream.ts:60-63` 使用浏览器原生 `EventSource` 连接 `/api/log-stream`，若带 `run_id` 则连接 `/api/log-stream?run_id=<run_id>`，否则订阅全局流。
- 消息格式：`useSSELogStream.ts:4-9` 约定后端推送 `data: {"log": "...", "run_id": "...", "priority": "high|normal|low"}`、`{"heartbeat": true}`、`{"health_summary": {...}}` 三类消息。
- 解析：`parseLog`（`src/frontend/log-panel-ui/src/lib/logParser.ts`）将纯文本转为结构化条目；组件层含 `LogStream.tsx`、`LogDetail.tsx`、`FilterTabs.tsx`、`StatusBar.tsx` 等（`src/frontend/log-panel-ui/src/components/`）。
- 重连：依赖 `EventSource` 原生重连；`useSSELogStream.ts:105-121` 在持续断连超过 30 秒时通过 `onerror` 回调提示。
- 状态管理：`src/frontend/log-panel-ui/src/store/logStore.ts`（zustand）保存日志条目；`App.tsx`、`pages/LogPanel.tsx` 为入口。

该面板与主体控制台解耦：主体页面通过 `log-panel.html` 内嵌构建产物，SSE 由 Flask 后端 `_sse_routes.py` 提供（见第 18 章）。

## 前端完整性校验（FRONTEND_LOCK.json + verify_lock.py）

`FRONTEND_LOCK.json` 与 `src/frontend/verify_lock.py` 不是代码功能校验，而是**前端布局产物的完整性 / 一致性锁**。它由四道检查构成（`verify_lock.py:214-268` 的 `main()`）：

1. 文件级 sha256（`verify_lock.py:206-242`）：对锁定文件计算换行归一化（CRLF→LF）后的 SHA-256，与 `FRONTEND_LOCK.json:14-23` 中记录的 `sha256`、`size` 比对，不一致则 `HASH MISMATCH` 并 `exit 1`。
2. Section 名语义校验（`verify_lock.py:244-260`）：从 `_layout_base.html` 提取侧栏分区标题，与 `FRONTEND_LOCK.json:4-13` 的 `expected_sections`（8 个分区）严格比对。
3. 悬空属性门检查（`verify_lock.py:119-155`，`check_attr_gates`）：扫描 `app-design.css` 中根元素（`html/:root/body`）上的 `data-*` 属性门，反查 JS/模板中是否有写入点（`setAttribute`/`toggleAttribute`/`dataset.x=`/静态属性）；无写入点的规则会恒成立导致 UI 卡死，判为 `DANGLING ATTR GATE` 失败。
4. 换行策略检查（`verify_lock.py:184-203`）：要求仓库根 `.gitattributes` 含 `* text=auto eol=lf`，防止锁指纹因平台换行差异漂移。

`src/frontend/selftest_verify_lock.py` 为上述检查的自测：8 个单元场景覆盖正例/反例（`selftest_verify_lock.py:29-54`），并含一次真实历史回归（`run_history`，模拟修复前 `data-sb-ready` 悬空门被拦下），以及 `.gitattributes` 守卫测试。

`FRONTEND_LOCK.json:35-36` 的 `attr_gate_policy` 说明：根元素上的 `data-*` 门必须有代码写入点，白名单仅用于确由外部/第三方设置的属性。

## 设计取舍

- 主体控制台采用服务端模板 + 原生 JS，而非 React 全局化：降低首屏复杂度，仅把实时性要求最高的日志面板独立成 SPA，构建产物静态托管，避免整站重渲染。
- 前端锁只锁两个核心文件（`_layout_base.html` + `app-design.css`）而非全量：聚焦「布局骨架 + 侧栏交互门」这一反复出问题的面，降低误报率。
- 换行归一化比对（而非裸字节 sha256）：解决 Windows/CRLF 与 Linux/LF 导致 CI 必误报的问题，代价是多一层 `_norm()` 处理（`verify_lock.py:158-169`）。
- 指标口径抽出到单一定义源 `metrics-def.js`：牺牲「就地改模板即生效」的便利，换得三处 KPI 阈值不再打架、新增指标只改一处。
- 前端「缺失即中性」：无数据渲染 `—`/`neutral` 而非 0/绿，与引擎「禁止用 0 冒充」的判定纪律一致。

## 本章代码依据

- `src/frontend/templates/_layout_base.html:118`-`210` — 8 分区侧栏与常驻控制台/步骤区；`:145` 控制台入口。
- `src/frontend/FRONTEND_LOCK.json:4-13`（8 分区）、`:14-23`（锁定文件 sha256/size）。
- `src/frontend/verify_lock.py:214-268`（四道检查）、`:158-169`（`_norm`）、`:245`（分区标题提取）。
- `src/frontend/selftest_verify_lock.py:29-54` — 悬空门单元自测场景。
- `src/frontend/static/app-common.js:92/258/306/405/443/492/548/563/663/862/938/1091/1360/1422/1436/1721` — 运行时各分节。
- `src/frontend/static/metrics-def.js:20/39-148/185/200/230` — KPI 单一定义源。
- `src/frontend/static/kpi-overview.js:49-63/98/131-190/193/236` — 统一 KPI 渲染。
- `src/frontend/static/quality-panel.js` 文件头（只读红线）、`docs-workbench.js` 文件头（5 Tab / 20 接口）、`clarify.js:1-8`。
- `src/frontend/log-panel-ui/package.json:14-21` — 日志面板技术栈；`src/hooks/useSSELogStream.ts:4-9/60-121` — SSE 订阅与重连。

## 待核实

- 各页面的**功能描述**依据侧栏标签、URL 与对应静态 JS 归纳；除 `quality-panel.js`/`docs-workbench.js`/`clarify.js`/`kpi-overview.js`/`metrics-def.js` 外，未逐页精读每个模板的内部实现（如 `review.html`、`config.html`、`pipeline.html` 的字段与交互）。
- ~~`/api/log-stream` 的后端实现~~ → **已核实（2026-10-15）：后端真实存在。** `app.py:441` `app.route("/api/log-stream")(api_log_stream)`，实现在 `src/backend/_sse_routes.py:363` `def api_log_stream()`（同文件 `:5` 说明还含 `/metrics`、`/api/log-history`、`/api/log-history/<run_id>`）。`_middleware.py:33` 把 `/api/log-stream` 列入免鉴权/长连接白名单，`:492` 对其做 `startswith` 特判。
- ~~`build-and-deploy.sh` 的拷贝目标~~ → **已核实（2026-10-15）：文件存在，拷贝目标为 `../static/log-panel`。** `src/frontend/log-panel-ui/build-and-deploy.sh:31` `DIST_DIR="../static/log-panel"`（相对该脚本自身目录，即 `src/frontend/static/log-panel/`），与正文一致。
  - **勘误**：核实过程中曾误判该文件"不存在"——当时检索的是仓库根目录与 `scripts/` 下的同名文件（这两处确实没有），而书稿原文写的是 `log-panel-ui/build-and-deploy.sh`，路径本就正确。属核实方检索路径错误，非书稿错误，正文无需改动。
- `metrics-def.js:20` 注释写 `/api/metrics-summary` 返回「13 域」，代码实际返回 12 域（见第 13 章待核实）；以代码为准。
- ~~模板总数与侧栏入口数~~ → **已核实（2026-10-15）：模板 40 个（Glob 实测，含 `_layout_base.html`）；侧栏 **8 分区成立**，正文无需更正。** 权威值在 `FRONTEND_LOCK.json`：`section_count = 8`，`expected_sections == actual_sections == [步骤区, 审核区, 指标区, 文档区, 日志区, 项目区, 配置区, 用户区]`。
  - **勘误（重要，判据教训 · 第三次同类错误）**：核实过程中曾按 `_layout_base.html` 的 `data-section` 属性计数，得 7 个便断言"8 分区有误"——这是**判据选错**。实测 `_layout_base.html:118-126`：步骤区是**常驻可见、不折叠**的，用 `data-step`（1–5）标记，不参与手风琴；其余 7 个**才是折叠区**用 `data-section`。`FRONTEND_LOCK.json` 的权威值 `section_count = 8`。**7 + 1 = 8，两个数字都对，只是口径不同。**
  **本轮又犯了一次同类错误（工具层面）**：核实 `step4_executor.py` 时用 `find src -iname "*step4*executor*"` 得零命中，一度断言"该文件不存在、书稿 23 处引用全部失效"。**实测 `src/pipeline/step4_executor.py` 存在**（65,662 字节），书稿引用的 `:594`（ADI 门禁）、`:813`（TDL fail-closed）、`:1069`（红队 skipped）、`:1165`（`if not res.get("checks")`）**四个行号全部对得上**。根因：本环境的 `find` 对部分中文路径/深层目录返回不可靠结果（同一脚本里 `_taxonomy.py` 也被漏过）。
  **合并教训（写死，后续核实必须遵守）**：① 计数类核实先确认被计对象的表达方式是否唯一；② **"文件不存在"类结论必须用第二种方式交叉验证**（`test -f` / `os.path.exists` / `ls -l`），不能只信 `find` 或 `glob`；③ 读到 diff 统计时看**符号**——`git show --stat` 里 `| 12 +` 是新增 12 行，不是删除。才是 `collapsible` 折叠区、用 `data-section`（review/metrics/docs/logs/project/config/user）。**7 + 1 = 8，两个数字都对，只是口径不同。** 凡"计数类"核实，必须先确认被计对象的表达方式是否唯一，否则会得出反向结论。
