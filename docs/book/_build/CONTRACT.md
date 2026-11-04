# 《E2E 智能测试引擎 · 技术设计与实现》写作契约

本文件是本书的**大纲 + 文体规范 + 事实纪律**三重契约。所有章节必须遵守。

---

## 一、全书大纲（7 篇 / 34 章 + 4 附录）

| 编号 | 章名 | 归属篇 | 覆盖代码面 |
|:---|:---|:---|:---|
| 00 | 前言：这本书怎么写成的 | 卷首 | — |
| 01 | 系统定位与能力边界 | 一 总览 | `AGENTS.md`、`src/架构点.md`、实际代码 |
| 02 | 技术选型 | 一 总览 | `requirements.txt`、各模块 import |
| 03 | 分层架构与模块地图 | 一 总览 | `src/` 19 个一级包 |
| 04 | 端到端数据流 | 一 总览 | Step1→5 全链路 + 94 张表 |
| 05 | Step1 · 需求数据拉取 | 二 流水线 | `pipeline/fetchers`、`plugins` |
| 06 | Step2 · 需求拆解与防幻觉 | 二 流水线 | `pipeline/step2.py`、`steps/step2/` |
| 07 | Step3 · 用例建模 | 二 流水线 | `pipeline/step3/`、`steps/step3/` |
| 08 | Step4 · 混合执行引擎总览 | 二 流水线 | `step4_executor.py`、`scheduler.py` |
| 09 | Step4 · 浏览器执行核心 | 二 流水线 | `steps/step4/browser_core/` |
| 10 | Step4 · 三轨校验与断言 | 二 流水线 | `steps/step4/verification/`、`empirical/` |
| 11 | Step4 · 接口与数据执行轨 | 二 流水线 | `api_only_executor.py`、`browser_tools/` |
| 12 | Step5 · 报告与补测建议 | 二 流水线 | `steps/step5/` |
| 13 | 度量与健康 | 三 横切 | `extensions/metrics_collector/`、`health_checker_mixins/` |
| 14 | 横切质量门禁 | 三 横切 | `extensions/a11y/`、`security_scanner/`、`perf_baseline/`、`quality/` |
| 15 | 智能体与动作库 | 三 横切 | `agent/`、`steps/step4/browser_tools/`、`testing_methods/` |
| 16 | 检索与知识资产 | 三 横切 | `extensions/rag_engine/`、`semantic_index/`、`change_impact_kb/`、`src/rag/` |
| 17 | 置信度与预言机 | 三 横切 | `extensions/confidence_calculator_mixins/`、`src/oracle/`、`src/verification/` |
| 18 | Web 控制台 · 装配与中间件 | 四 控制台 | `backend/app.py`、`_middleware.py`、`_sse_routes.py`、`state.py` |
| 19 | Web 控制台 · 路由族 | 四 控制台 | `backend/routes/` 25 个子域 |
| 20 | Web 控制台 · 前端与实时日志 | 四 控制台 | `src/frontend/`、`templates/`、`static/` |
| 21 | 数据模型与数据库 | 五 数据层 | `core/schema.sql`（94 表）、`db_manager/` |
| 22 | 配置体系 | 五 数据层 | `core/config_loader/`、`config.yaml/json`、`pipeline_config/` |
| 23 | 用户与权限 | 五 数据层 | `src/users/`、`backend/routes/auth/` |
| 24 | 测试体系 | 六 工程 | `tests/` 16 组 |
| 25 | 质量门禁防线 | 六 工程 | `tests/test_core/*gate*`、`.pre-commit-config.yaml` |
| 26 | 部署与运行 | 六 工程 | `main.py`、`run.py`、`Dockerfile`、`SmartShopEngine.spec` |
| 27 | 诊断与运维工具 | 六 工程 | `scripts/`、`pipeline/diagnostics/`、`evidence/` |
| 28 | 关键设计一 · 防幻觉体系 | 七 设计 | Step2 两阶段核对 + 置信度 |
| 29 | 关键设计二 · 单管道三策略 | 七 设计 | `steps_from_case` 路由 |
| 30 | 关键设计三 · 三轨校验与精度模型 | 七 设计 | `precision: exact/fuzzy` 唯一分支 |
| 31 | 关键设计四 · 语义唯一源 | 七 设计 | 极性 / 身份 / 定位器 / 词表权威源 |
| 32 | 关键设计五 · 误判根因库 | 七 设计 | 假 PASS / 假 FAIL / 异常 SKIP / 假 Bug |
| 33 | 关键设计六 · 三十万行规模治理 | 七 设计 | 防御原语、可见性收敛、门禁 |
| A | 附录 · 文件职责索引 | 附录 | 全量 1,322 文件 |
| B | 附录 · 数据库表速查 | 附录 | 94 张表 |
| C | 附录 · HTTP 接口清单 | 附录 | Flask 全部路由 |
| D | 附录 · 术语与命名对照 | 附录 | 旧名→新名 |

---

## 二、文体规范（硬约束，违反即返工）

**禁止项：**

1. 禁止 AI 口头禅：`本文旨在`、`让我们`、`综上所述`、`值得注意的是`、`深入探讨`、`在当今……的背景下`、`首先/其次/最后` 式排比、`不仅……而且……`、`总而言之`。
2. 禁止 emoji 与颜文字。
3. 禁止空话形容词：`强大`、`优秀`、`高效`、`丰富`、`极大提升`、`全面`、`赋能`、`助力`。凡形容必给数字或机制。
4. 禁止"应该是 / 大概 / 可能"式结案。不确定的内容写进文末「待核实」清单，不在正文出现。
5. 禁止出现"修复轮次/第 N 轮/XX 日期修复"这类事件叙事注释（`AGENTS.md` §6 明令）；机制注释只留一行为什么。
6. 禁止把推断写成事实。文末必须区分：**代码事实 / 实测结果 / 待核实**。

**要求：**

1. 陈述句为主，主语明确，一句话一个意思。
2. 术语全文一致；首次出现的模块名后紧跟其代码路径。
3. 每个机制性结论必须能落到 `文件路径:行号` 或明确的符号名（类/函数/常量）。
4. 数字只写实测值，并说明来源（脚本 / SQL / `wc`）。
5. 表格优先于长段落罗列；流程用 mermaid 或 ASCII 图。
6. 章节结构统一：`## 概述` → 各小节 → `## 设计取舍` → `## 本章代码依据` → `## 待核实`（可空）。

---

## 三、事实纪律

- 只写读到的代码，不写"通常是这么做的"。
- 与 `AGENTS.md`、`docs/引擎验收体系_20260923.md` 等既有文档冲突时，**以当前代码为准**，并在正文标注差异。
- 无法核实的（如某模块是否真的被调用），写进「待核实」并给出核实方法。
