# 第 5 章　Step1 · 需求数据拉取

## 概述

Step1 的职责是把分散在各处的需求来源收敛为一份可被后续步骤消费的文本事实。入口函数是 `step1_fetch_data()`（`src/pipeline/step1.py:106`），它的产物是一个 dict，包含 `req_text`（需求正文）、`ui_context`（Figma UI 结构）、`api_docs_context`（接口文档）、`test_accounts`（测试账号）、`sources_status`（各源拉取状态与后处理结果）、`version_label`（本次运行版本）与 `requirement_sut_match`（需求模块 vs SUT 页面匹配结论）。导航地图由 Step2 生成，不在此返回。

四个数据源——Confluence、Figma、YApi、数据库——由 `ThreadPoolExecutor(max_workers=4)` 并行拉取（`src/pipeline/step1.py:254`），整体耗时约等于最慢单源。MySQL 是 Step1 唯一的结构化事实源，磁盘缓存概念已移除。

## 数据源注册表（DataSourceRegistry）

注册表实现在 `src/pipeline/datasource_registry.py`。模块加载时实例化全局单例 `registry = DataSourceRegistry()`（`:96`），并调用 `_register_builtin_sources()`（`:103`）把内置源注册进去。

内置源 key 由常量 `BUILTIN_SOURCE_KEYS = ("confluence", "figma", "yapi", "database")` 唯一定义（`:21`），这是 Step1 阻断判定与 fetcher 兜底的派生根。每个源通过 `register(name, fetch_func, config_schema, timeout_seconds, group)`（`:33`）登记：`fetch` 是拉取函数，`config_schema` 供前端渲染表单，`timeout_seconds` 是超时（Confluence/YApi 为 300，Figma/数据库为 60）。`source_keys()`（`:90`）返回已注册 key，被 `_build_sources_status()` 用于动态构建 `sources_status`，避免硬编码 7 个 key（`src/pipeline/fetchers/base.py:148`）。

新增数据源只需 `registry.register("jira", fetch_jira, schema=...)`，无需改动 Step1 核心代码——这是注册表模式的目的（模块 docstring `:4`）。

## 四个内置抓取器（Fetchers）

抓取逻辑按数据源拆分进 `src/pipeline/fetchers/` 下的 mixin，`Step1DataFetcher`（`src/pipeline/step1.py:51`）继承全部 mixin，对外接口兼容原 `step1.py`。

### Confluence（`confluence.py`）

`_fetch_confluence`（`confluence.py:27`）先查 MySQL `source_documents(doc_type='confluence', version_label=...)` 做缓存新鲜度判断，`_CACHE_TTL_SECONDS = 7776000`（90 天，定义在 `base.py:22`）。缓存新鲜则跳过 API 调用；否则通过 `get_confluence_client().fetch_confluence_all()` 拉取，支持三种模式：`file`（本地文件）、`mcp`、`rest`（凭据校验见 `base.py:90` 的 `_validate_config`）。拉取后剥离 `\n---\n` 元数据头，用 `normalize_content()` 标准化文本（`:139`），与历史哈希比对得出 `cf_changed`，最后由 `_write_confluence_to_mysql`（`confluence.py:229`）在单连接内原子写入 `source_documents` + `source_hashes`，并额外写 `source_confluence_hashes` 细表（`:295`）。返回元组含 `image_urls`（原型图 URL，供视觉识别）。

上传文档通过 `_merge_uploaded_docs`（`confluence.py:309`）与 Confluence 正文合并，权重 1.0。

### Figma（`figma.py`）

`_fetch_figma`（`figma.py:150`）是分层智能拉取。版本感知基线继承：当前版本无节点哈希时，依次尝试继承上一活跃版本（`inherit_hashes_from`）、全局基线（`version_label=""`）、任意历史版本（查 `figma_node_hashes` 表，`:241`），消除全量重拉。`should_fetch` 决策（`:292`）综合 `cf_changed`、`figma_force_refresh`、缓存陈旧、Confluence 哈希是否存在。`smart_figma_fetcher.fetch_smart()` 执行拉取，缓存为空时自动回退全量（`:347`）。拉取成功后由 `save_figma_to_tables`（`figma.py:410`）写入 `figma_pages`/`figma_nodes`/`figma_components` 独立表（供 RAG 索引）。`_scan_image_nodes`（`:29`）递归识别 VECTOR/SVG、IMAGE/PNG、GIF 节点并下载入库。

### YApi（`yapi.py`）

`_fetch_yapi`（`yapi.py:25`）同样先查 MySQL `source_documents(doc_type='yapi')` 新鲜度；API 成功后 `_write_yapi_to_mysql`（`:198`）写入 `source_documents(doc_type='yapi')`（Markdown 文本，供 AI prompt）、`doc_type='yapi_structured'`（结构化 JSON，含完整 interfaces，供 RAG/解析器）与 `source_hashes`，并写 `source_yapi_hashes` 细表。API 失败时读 MySQL 最近一条 `yapi` 缓存兜底（`:159`），仍失败才标记 `failed`。

### 数据库（`database.py`）

`_fetch_database`（`database.py:29`）查询真实测试账号与 DB Schema。`_fetch_and_persist_schema`（`:63`）把 Schema 写入 `source_documents(doc_type='database', weight=0.80, category='database')`（`_persist_schema_row`，`:113`），并在 INSERT 后 SELECT 复核行真实存在（状态-落库一致性断言，`:141`），消除"配置但失败→降级 warning"产生的假 ok。`_build_db_status`（`:176`）把状态与落库事实绑定：Schema 非空但未落库 → `failed`。

## Confluence 三种接入模式与客户端选择

上文 `### Confluence` 提到的 `file`/`mcp`/`rest` 三种模式，由工厂函数 `get_confluence_client()`（`src/pipeline/steps/step1/mcp/confluence_mcp/__init__.py:25`）按 `config.confluence_mode` 分派：

| `confluence_mode` | 返回实例 | 实现文件 |
|:---|:---|:---|
| `file` | `FileModeClient` | `_file_mode.py:18` |
| `mcp` | `ConfluenceMCPClient`（启动失败回退 REST） | `_mcp_client.py:32` |
| 其它/缺省 | REST `confluence_client` | `src/pipeline/steps/step1/confluence_client/` |

三种客户端对外提供一致接口 `fetch_confluence_all()`，所以 fetcher 侧（`confluence.py:27`）无需感知模式差异。

- **MCP 模式**：`ConfluenceMCPClient`（`_mcp_client.py:32`）通过 MCP stdio 协议拉起子进程 `npx -y @atlassian-dc-mcp/confluence`（Windows 下经 `cmd /c` 包装，`:56-59`），把 `CONFLUENCE_API_BASE_PATH`/`CONFLUENCE_API_TOKEN` 注入子进程 env（`:52-54`）。请求-应答用自增 `_request_id` + `queue.Queue` 关联（`:38-39`），独立读线程消费 stdout 并分发回对应 pending 队列。依赖本机 Node.js + npm 安装该 npm 包。
- **File 模式**：`FileModeClient`（`_file_mode.py:18`）读取 `CONFLUENCE_URL` 指定的本地路径，支持逗号分隔多文件与目录展开（`:46-50`），解析后返回与 Confluence 客户端一致的格式。
- **回退链**（`__init__.py:33-50`）：`mcp` 模式下 `confluence_pat`/`api_base_path` 任一缺失即回退 REST（`:36-40`）；`client.start()` 抛异常同样回退 REST（`:46-50`）。回退只记 `info`/`warning` 不抛错，保证 Step1 不因接入方式失败而中断。

## 上传文件多格式解析（file_parser）

需求文档除来自数据源外，还可由用户直接上传。解析器 `FileParser`（`src/pipeline/steps/step1/file_parser/__init__.py:28`）是格式分发的统一门面，把上传文件统一转成标准文本供 Step2/Step3 消费。

- **统一入口**：`FileParser.parse(file_path)`（`:49`）按扩展名查 `EXTENSION_MAP`（`:32`）分派到具体解析器，返回 `{success, text, format, metadata}`；`parse_file`/`parse_files`（`:124`/`:129`）是单文件/批量便捷函数。
- **支持的格式**（`EXTENSION_MAP`，`:32-46`）：`.csv`、`.xlsx`/`.xls`、`.md`/`.markdown`、`.docx`/`.doc`、`.xmind`、`.json`、`.sql`、`.txt`，以及 `S1-001` 新增的 `.pptx`。
- **按格式族拆分**（模块文档串 `:9-13`）：`office_formats.py`（CSV/Excel/Word 表格与文档类）、`tree_formats.py`（XMind/JSON 树状与结构化）、`text_formats.py`（Markdown/SQL/TXT 纯文本与 DDL），三者以 Mixin 形式被 `FileParser` 多重继承（`:23-28`）。
- **错误处理**：文件不存在（`:63`）或格式不支持（`:74`）返回 `success=False` 而非抛异常；解析异常被捕获转成 `error` 字段（`:108-116`），单文件失败不阻塞批量解析（`:129-134`）。

`FileModeClient` 的本地文件读取即复用 `FileParser`——它用 `FileParser.EXTENSION_MAP` 判定目录内可解析文件（`_file_mode.py:58`），再逐个调 `FileParser.parse`（`:85`），二者共用同一套格式映射。

## 抓取编排与并行

`step1_fetch_data` 先做前置：版本管理 `_setup_version`（`base.py:33`，无活跃版本时自动创建）、健康检查 `_check_health`（`base.py:84`）、凭据校验 `_validate_config`（`base.py:90`）、初始化 `sources_status`（`base.py:148`）。随后 `ThreadPoolExecutor` 提交四个源任务（用户可在 `skip_sources` 中跳过），每源完成时通过 `_step1_report_progress`（`:239`）增量写 `sources_status` 到 MySQL 供前端轮询。后处理阶段（原型图识别 + RAG 构建 + 多源功能提取）计入进度总量，保证 100% 仅在 Step1 全部完成时出现（避免"假 100%"，`:218`）。

## 插件机制（注册与发现）

插件系统在 `src/pipeline/plugins/`。`bootstrap.init_pipeline_plugins()`（`bootstrap.py:25`）实例化 4 个插件（`step1_datasource`、`step2_analyzer`、`step3_generator`、`step5_report`）并订阅 9 个事件到 `pipeline_bus`。`PipelineBus`（`pipeline_bus.py:39`）提供 `subscribe`/`publish`，`PipelineEvent` 枚举定义 12 个阶段事件（`pipeline_bus.py:23`）。`Step1DatasourcePlugin`（`step1_datasource.py:19`）暴露 `register_datasource(name, fetch_func, config_schema)`（`:32`），在 `on_step1_start`（`:49`）把已注册源注入 `ctx["datasources"]`。`STEP1_START` 事件的发布在 `step1.py:139`。

## 诊断与自检（diagnostics）

`src/pipeline/diagnostics/self_test.py` 的 `run_self_test()`（`:34`）执行流水线自检，分别对 Step2/3/4/5 跑检查（Step2 用内置需求调用 `feature_driven_breakdown` 实际拆解，`:102`），汇总 `overall` 为 `HEALTHY`（无失败）/ `DEGRADED`（失败<2）/ `BROKEN`。结果由 `_persist_self_test_result`（`:400`）写入 `pipeline_self_test_results` 表（列：`steps_json, overall, passed, failed, skipped`）。

## 证据与置信度校准（evidence）

`src/pipeline/evidence/` 提供跨步骤复用的置信度能力。`calibration.record_calibration()`（`calibration.py:101`）按 `(project, calibration_key, calibration_value)` 原子递增 `total_predictions`/`total_correct`，写入 `confidence_calibration` 表。`confidence.compute_evidence_confidence()`（`confidence.py:257`）综合 AI 原始置信度、历史校准准确率（`get_calibration`）、独立证据强度三路，输出校准后置信度与 `confidence_level`、`verdict`（如 `evidence_calibrated`/`cold_start`）。Step2 的置信度评分即复用该模块接口。

## 拉取结果落库表

| 表 | 写入位置 | 内容 |
|:---|:---|:---|
| `source_documents` | persist/confluence/figma/database/yapi | 各源正文，`doc_type` 区分 |
| `source_hashes` | 各 fetcher | 统一哈希（同事务） |
| `source_confluence_hashes` | `confluence.py:295` | Confluence 细表哈希 |
| `source_yapi_hashes` | `yapi.py:309` | YApi 细表哈希 |
| `figma_pages` / `figma_nodes` / `figma_components` | `figma.py:410` | Figma 结构事实 |
| `figma_node_hashes` | `figma.py`（hasher） | Figma 节点基线哈希 |
| `pipeline_self_test_results` | `self_test.py:410` | 自检结果 |
| `confidence_calibration` | `calibration.py:101` | 置信度校准样本 |

## 错误处理

每个 fetcher 不向上抛异常，而是返回 `ok` 标志与状态 dict，由 `sources_status` 记录 `status`/`detail`。YApi 在 API 失败时用 MySQL 缓存兜底；数据库用状态-落库一致性断言消除假 ok；Confluence/YApi 用哈希比对决定是否跳过写入；Figma 缓存为空自动回退全量。缺凭据的数据源在 `_validate_config` 收集警告并被标记 `skipped`。

## 设计取舍

- 注册表模式把"新增数据源"从改核心代码降级为一次注册调用，代价是 fetcher 与注册表之间的延迟导入（避免循环依赖）增加了阅读成本。
- 缓存统一收敛到 MySQL（`source_documents`），放弃磁盘缓存，换来版本/项目维度的一致查询，代价是每次新鲜度判断多一次 SQL。
- 数据源并行（4 worker）用 max(单源耗时) 近似总耗时，但数据库 Schema 拉取与 Figma 分层拉取各自有独立失败兜底，互不阻断。

## 本章代码依据

- `src/pipeline/datasource_registry.py`：`registry`（:96）、`BUILTIN_SOURCE_KEYS`（:21）、`register`（:33）、`_register_builtin_sources`（:103）。
- `src/pipeline/step1.py`：`step1_fetch_data`（:106）、`Step1DataFetcher`（:51）、并行提交（:254）、事件发布（:139）、进度总量（:218）。
- `src/pipeline/fetchers/base.py`：`_setup_version`（:33）、`_validate_config`（:90）、`_build_sources_status`（:148）、`_CACHE_TTL_SECONDS`（:22）。
- `src/pipeline/fetchers/confluence.py`：`_fetch_confluence`（:27）、`_write_confluence_to_mysql`（:229）、`_merge_uploaded_docs`（:309）。
- `src/pipeline/fetchers/figma.py`：`_fetch_figma`（:150）、`should_fetch`（:292）、`save_figma_to_tables` 调用（:410）、`_scan_image_nodes`（:29）。
- `src/pipeline/fetchers/yapi.py`：`_fetch_yapi`（:25）、`_write_yapi_to_mysql`（:198）、MySQL 兜底（:159）。
- `src/pipeline/fetchers/database.py`：`_fetch_database`（:29）、`_persist_schema_row`（:113）、`_build_db_status`（:176）。
- `src/pipeline/plugins/bootstrap.py`：`init_pipeline_plugins`（:25）；`pipeline_bus.py`：`PipelineBus`（:39）、`PipelineEvent`（:23）；`step1_datasource.py`：`register_datasource`（:32）。
- `src/pipeline/diagnostics/self_test.py`：`run_self_test`（:34）、`_check_step2`（:102）、`_persist_self_test_result`（:400）。
- `src/pipeline/evidence/calibration.py`：`record_calibration`（:101）；`confidence.py`：`compute_evidence_confidence`（:257）。
- `src/pipeline/steps/step1/mcp/confluence_mcp/__init__.py`：`get_confluence_client`（:25）、回退链（:33-50）；`_mcp_client.py`：`ConfluenceMCPClient`（:32）、子进程启动（:56-59）；`_file_mode.py`：`FileModeClient`（:18）、复用 `FileParser`（:58、:85）。
- `src/pipeline/steps/step1/file_parser/__init__.py`：`FileParser`（:28）、`EXTENSION_MAP`（:32）、`parse`（:49）、`parse_file`（:124）、`parse_files`（:129）；`office_formats.py` / `tree_formats.py` / `text_formats.py`。

## 待核实

- ~~`dom_crawl` / `upload` / `multi_source_features` 是否仍活跃~~ → **已核实（2026-10-15）：仍是活跃字段。** `preflight.py:276-292` 与 `:534-536` 仍在写 `sources["dom_crawl"]` 并给出 ok/failed/not_configured 三态；`pipeline_progress.py:157`(`dom_crawl`)、`:178`(`multi_source_features`)、`:212`(`three_layer_match`) 仍在产出前端步骤卡。结论：**被废弃的是 `dom_crawler` 爬取策略，不是 `dom_crawl` 这个状态字段**——字段仍在用，只是不再真爬取。
- ~~`on_step1_complete` 的 `item_count` 是否恒为 0~~ → **已核实（2026-10-15）：部分成立——只有 YApi 有 `items`，其余数据源恒 0。** 首先**更正路径**：该函数在 `src/pipeline/plugins/step1_datasource.py:69`（不在 `steps/step1/`）。实测 `src/pipeline/steps/step1/` 全目录检索 `"items"` 字面量**只有 1 处命中**：`yapi_client.py:221` 的 `self._ok({"total": total, "items": items})`。即 Confluence / Figma / file 等数据源的返回结构里**没有 `items` 键**，`len(result.get("items", []))` 对它们恒为 `0`。该统计的唯一调用方是 `plugins/bootstrap.py:59`。**结论：不是「可能恒 0」而是「除 YApi 外确定恒 0」**，属统计口径与 fetcher 契约未对齐的真实小缺陷。次完整 Step1 确认。
- ~~`self_test.py` 是否只覆盖 Step2/3/4/5~~ → **已核实（2026-10-15）：确只覆盖 2/3/4/5，Step1 无自检，书稿所述成立。** **更正路径**：该文件是 `src/pipeline/diagnostics/self_test.py`（不是 `src/diagnostics/`，少了一层 `pipeline/`）。实测 `:55-64` 依次调用 `_check_step2` / `_check_step3` / `_check_step4` / `_check_step5`，`:44` 的返回结构注释也自述 `steps: {step2/3/4/5: {...}}` —— **全文件无 `_check_step1`**。即 Step1 的连通性确实只能靠数据源各自的健康检查，没有统一自检入口。
- Confluence 三客户端的**实测状态**：本次仅静态读码确认分派与回退链，未实际跑通 `mcp`（需本机 Node.js + `@atlassian-dc-mcp/confluence`）与 `file` 两种模式；生产默认走哪一模式需核对运行环境 `CONFLUENCE_MODE`。
- **【真实代码缺陷 · 已核实 2026-10-15】** `FileParser.EXTENSION_MAP` 声明支持 `.pptx`（`src/pipeline/steps/step1/file_parser/__init__.py:45`），实现体 `_parse_pptx` 已存在（`text_formats.py:186`，依赖 `python-pptx`），但 `FileParser.parse` 的分派 if/elif（`:84-107`）只覆盖 csv/excel/markdown/word/xmind/json/sql/text —— **没有 pptx 分支**，`.pptx` 必然落 `else` 返回"未实现的解析器: pptx"（`:100-107`）。即已实现的解析器不可达。正解是改为按 `getattr(self, f"_parse_{fmt}")` 统一分派（消除"声明-实现-分派"三处需手工同步的同一语义），而不是再补第 9 个 elif 分支。本章原文路径 `src/file_parser/...` 有误，正确路径为 `src/pipeline/steps/step1/file_parser/`。
