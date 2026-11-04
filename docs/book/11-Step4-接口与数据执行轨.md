# 第 11 章　Step4 · 接口与数据执行轨

## 概述

本章覆盖纯接口用例的执行、工具/动作库的注册机制，以及 DB 执行轨如何连库与执行 SQL。纯接口用例不启动浏览器，走 `api_only_executor.py` 的 HTTP 直连快速通道；浏览器内动作由 `browser_tools/` 的工具 schema 与执行器分派；DB 执行轨由 `extensions/db/` 的 `DatabaseAgent` 承载，与第 10 章的 SQL 生成层对接。三条轨道的 check 仍最终汇入第 10 章的 `verify_step` 管道。

## 纯接口用例执行

纯接口执行器 `api_only_executor.py` 从 `scheduler.py` 拆分，零业务逻辑变更（`api_only_executor.py:5-14`）。其 HTTP 客户端与登录函数组 re-export 自 `api_login_helpers`（`api_only_executor.py:31-37`）：

- `_make_http_request`：底层 HTTP 请求，支持 method+path 解析（`api_only_executor.py:40-72`，`_build_url` 从 `target` 中剥离 `POST /login` 这类 method 前缀，只取 path；`target` 为完整 URL 则直用）。
- 鉴权：`_AUTH_CACHE`（`api_only_executor.py:23`，TTL 600 秒）缓存登录态避免每个用例重登（18s/次）；登录尝试顺序 `_try_api_login → _try_playwright_login → _try_urllib_login`（`api_only_executor.py:34-36`），三路回退覆盖有头/无头环境。
- 参数化：`_build_url`（`api_only_executor.py:40`）自动探测 API 前缀（`api_prefix_detector`），`base_url` 含路径时只取 `scheme+host+port` 防多层路径拼接（`api_only_executor.py:69-72`）。
- 响应断言：`_parse_api_assertion`（`api_only_executor.py:75`）把自然语言断言解析为结构化检查——`status`（状态码，200 放宽为 `[200,201,204]`，`api_only_executor.py:94-98`）、`body_contains`、`json_field`。管道内的 API 轨断言由 `_verify_api`（`verification/_api.py:49`）完成，用 `_extract_json_path`（`_api.py:209`）取响应体字段并与期望值比对。

## 工具与动作库的注册机制

动作/断言体系分"定义"与"执行"两层，单一事实源集中在 `browser_tools/_taxonomy.py`。

**定义层**

- `ALL_TOOLS`（`_tool_definitions.py:29` 起）：所有工具的 JSON Schema 列表，每个条目由 `_make_schema(name, description, properties, required_fields)` 构造，供 LLM/驱动侧作为可调用的工具清单。
- `_TOOL_PREFIX_MAP`（`_tool_definitions.py:854-950`）：工具名前缀 → 动作类型映射，是"工具名"到"执行语义"的桥梁，例如 `browser_click→click`、`browser_fill→fill`、`verify_db_field_value→db_field_value_check`、`api_verify→api_verify`。
- `CHECK_TAXONOMY`（`_taxonomy.py:497`）：断言类（verify_*/check）权威枚举；`ACTION_TAXONOMY`（`_taxonomy.py:31`）为动作权威枚举；`resolve_check_type`（`_taxonomy.py:913`）把任意 check 类型归一为权威 key（直接命中 → 遍历 aliases → 去 `_check` 后缀）。
- 漂移门禁：`tests/test_oracle/test_taxonomy_drift.py` 常驻，断言"执行注册表 ⊆ CHECK_TAXONOMY"（`_taxonomy.py:838`），防止生成端发射执行端不认的类型。

**执行层**

- `_execute_tool`（`execution/_single.py:173`）：单个工具执行入口，先处理 `finish`，再用 `_TOOL_HANDLERS`（`_single.py:1282`）闭包分派到具体实现（`_api_verify`、`_api_request_impl`、`_network_fault_impl`、`_service_mock_impl` 等）。
- 声明↔执行漂移自愈：`_auto_registered_impls`（`_single.py:1292`）遍历 `CHECK_TAXONOMY`，按每项 `tool_name` 在 `verification` 包内按函数名查找实现（`_resolve_verify_impl`，`_single.py:1311`），把"在 verification 包有实现、却未登记进 `_TOOL_HANDLERS`"的断言自动接入。缓存于 `_AUTO_REGISTERED_CACHE`（`_single.py:1289`）。
- 动作契约守卫：`action_contract.py` 的 `sanitize_case_actions` 在用例落库前拦截契约外动作（`action_contract.py:50`），与第 10 章 `_check_guard` 的 check 守卫对称。

## DB 执行轨：连库、执行与数据库分派

DB 执行轨的 SQL 生成在第 10 章已述，执行侧由 `DatabaseAgent`（`extensions/db/db_agent/__init__.py:21`）承载，其连接与执行能力分散在 mixin：

- 连接：`DatabaseAgentConnectionMixin`（`db_agent/connection.py:15`）处理 sqlite 用 `TARGET_DB_FILE`、其余用库名的差异（`db_agent/connection.py:71-79`）。底层驱动分派在 `db_adapters_mixins/_connect.py:48-115`，默认 `db_type="mysql"`（AGENTS.md §3 红线：MySQL 是唯一结构化事实源），按类型导入 `pymysql` / `psycopg2` / `sqlite3` / `yaspy` 或 `cx_Oracle`。
- 执行：`DatabaseAgent` 复用 `extensions/db/db_core/execute.py` 的分派函数 `_execute_sqlite / _execute_mysql / _execute_postgresql`（`db_core/execute.py:38-56`）；错误信息（`db_core/execute.py:52,56`）明示支持 `yasdb / oracle / mysql / postgresql / sqlite` 五类。只读校验与结果规整在 `db_adapters_mixins/_execute.py:36`（`execute`）、`:100`（`query`）。
- 类型分派：以 `db_type` 字符串 switch 分派到对应驱动（`db_core/execute.py:38-56`、`_connect.py:48-115`），无注册表字典，新增库类型需在施工点同步加分支。

## 设计取舍

- 纯接口执行器与浏览器执行器物理分离（从 `scheduler.py` 拆出），但共享 `verification` 管道，避免两套断言语义漂移。
- 工具定义（`ALL_TOOLS` JSON Schema）与执行（`_TOOL_HANDLERS` / `_auto_registered_impls`）解耦：定义侧面向 LLM 契约，执行侧面向 Playwright 实现，自愈机制让"新增 verification handler"自动获得工具入口。
- DB 类型分派用字符串 switch 而非注册表，与 UI/check 的字典分派风格不一致；优点是驱动依赖在 import 期即失败可见，缺点是新增库类型需改多处施工点（见待核实）。
- 接口登录三路回退（`api/playwright/urllib`）覆盖有头无头，代价是 playwright 登录路径仍依赖浏览器栈，纯接口场景下可能引入非必要依赖。

## 本章代码依据

- 接口执行：`api_only_executor.py:23`（`_AUTH_CACHE`）、`:31-37`（登录 re-export）、`:40`（`_build_url`）、`:75`（`_parse_api_assertion`）、`:94-98`（200 放宽）
- 接口断言：`verification/_api.py:49`（`_verify_api`）、`:209`（`_extract_json_path`）
- 工具定义：`browser_tools/_tool_definitions.py:29`（`ALL_TOOLS`）、`:854-950`（`_TOOL_PREFIX_MAP`）、`:956-961`（re-export 分类常量）
- 分类权威源：`browser_tools/_taxonomy.py:497`（`CHECK_TAXONOMY`）、`:913`（`resolve_check_type`）
- 执行分派：`browser_tools/execution/_single.py:173`（`_execute_tool`）、`:1282`（`_TOOL_HANDLERS`）、`:1292`（`_auto_registered_impls`）、`:1311`（`_resolve_verify_impl`）
- DB 执行：`extensions/db/db_agent/__init__.py:21`（`DatabaseAgent`）、`db_agent/connection.py:15,71-79`、`db_core/execute.py:38-56`、`db_adapters_mixins/_connect.py:48-115`、`db_adapters_mixins/_execute.py:36,100`

## 待核实

- ~~`_execute_yasdb` / `_execute_oracle` 是否真实存在~~ → **已核实（2026-10-15）：真实存在，是两个独立函数。** `src/extensions/db/db_core/execute.py:201` `def _execute_yasdb`（`yaspy` 驱动，未安装时返回"yaspy 未安装"）与 `:240` `def _execute_oracle`（`cx_Oracle` 驱动，未安装时提示 pip install）；分派见 `:44-47`。此前 grep 只命中三个函数是因为按 `_execute_` 前缀搜时 limit 截断，不是不存在。
- ~~两套连接 mixin 是否重复~~ → **已核实（2026-10-15）：是两个不同类，路径需更正。** `DatabaseAgentConnectionMixin` 在 `src/extensions/db/db_agent/connection.py:15`；`DbAdapterMixin` 在 `src/extensions/db/db_adapters.py:21`（本章原写的 `src/db_agent/`、`src/db_adapters.py` 均不存在，缺 `extensions/db/` 前缀）。二者是否重复实现连接逻辑仍需逐行比对，此项保留。
- ~~`CHECK_TAXONOMY` 是否全量条目都带 `tool_name`~~ → **已核实（2026-10-15）：59 项全部带 `tool_name`，本章担心的情况不存在。** 用 AST 解析 `src/pipeline/steps/step4/browser_tools/_taxonomy.py:497` 的 `CHECK_TAXONOMY`（注意：它是**带类型注解的赋值** `CHECK_TAXONOMY: dict[str, dict[str, Any]] = {...}`，按 `Assign` 检索会零命中，这是本章原判据的漏洞）：顶层键 **59** 个，逐项检查内层字典键，缺 `tool_name` 的**为 0**，非 dict 形态的**为 0**。即自愈注册对所有断言类型都成立。
- `api_only_executor.py` 与 `scheduler.py` 的门面兼容关系（文件头称"原文件通过门面模式兼容"）未在 scheduler 侧逐行核对。
- 纯接口用例的响应断言（`_verify_api`）是否在 `api_only_executor` 路径下实际调用 `verify_step`，还是另走轻量断言，未确认。
