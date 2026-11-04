# 附录 D · 术语与命名对照

本附录集中给出全书使用的术语、字段名与历史命名对照。开发中遇到的"字段名记错""旧名找不到表"这类问题，多半能在这一页解决。

## 一、核心概念

| 术语 | 对应符号/字段 | 含义 |
|:---|:---|:---|
| 用例 | `TestCase`（`test_cases` 表） | 核心测试资产，结构为 `{case_id, actions, checks}` |
| 动作 | `actions` | 结构化操作序列，元素形如 `{type, target_hint, value}` |
| 检查点 | `checks` | 结构化验证点，元素形如 `{track, precision, check, expect}` |
| 轨 | `track` | 检查点所属执行通道，取值 `ui` / `api` / `db` |
| 精度 | `precision` | 检查点判定方式，取值 `exact`（规则） / `fuzzy`（AI 辅助） |
| 检查项类型 | `check` | 检查点的类型标识，取值受执行端注册表约束 |
| 期望值 | `expect` | 检查点的期望结果 |
| 执行策略 | `STRATEGY_*`（`strategy_router.py`） | 按数据完整度选择的三条执行路径 |
| 终判 | `decide_verdict`（`verification/verdict.py`） | 全仓唯一决定用例最终结论的函数 |
| 置信度 | `confidence` | 判定可信程度，阈值常量 0.70 |
| 车道 | `review_lane` | 审核分流：green / yellow / red |
| 闸门 | gate（`review_gate.py`、`pre_step_gate.py`） | 步骤或审核的前置校验 |
| 指标族 | `ALL44`（`scripts/_audit_44fam_round_scope.py:18`） | 44 项引擎能力指标的权威清单 |

## 二、数据结构字段

| 字段 | 所属 | 说明 |
|:---|:---|:---|
| `case_id` | `test_cases` | 用例主键 |
| `case_source` | `test_cases` | 用例来源：`generated` / `inherited` / `inherited_virtual` / `manual_override` |
| `valid_status` | `test_cases` | 用例有效性状态（**不是** `status`） |
| `case_actions_json` | `test_cases` | `actions` 的 JSON 存储 |
| `expected_outcome` / `expected_result` | `test_cases` | 期望结果字段 |
| `review_status` | `case_meta` | 审核状态（`pending` / 已确认等） |
| `req_id` | `requirements` | 需求编号 |
| `source_quote` | `requirements` | 需求原文引用，防幻觉核对的事实源 |
| `_verify_status` | `requirements` | 原文核对结果：`passed` / `passed_extended` / `partial_match` / `unverified` / `empty_quote` / `skipped_implicit` |
| `_evidence_confidence` | `requirements` | 证据置信度（0.0–1.0） |
| `is_implicit` | `requirements` | 需求显隐性等级：`0` 显式 / `1` 通用隐性 / `2` 业务隐性 |
| `status` | `executions` | 执行结论（**不是** `result`） |
| `failure_reason` | `executions` | 失败原因（**不是** `error`） |
| `failure_category` | `executions` | 失败归类：`PRODUCT_BUG` / `HARNESS_ERROR` / `ENVIRONMENT_ERROR` |
| `failure_chain` | `executions` | 失败链路 |
| `verification_track` | `executions` | 校验轨 |
| `cross_cut_dims` | `executions` | 横切维度结果 |
| `confidence_factors` | `executions` | 置信度分项 |
| `step_num` | `run_logs` | 步骤序号（**不是** `step`） |
| `value` | `ai_health_metrics` | 指标值（**不是** `metric_value`）；`metric_type='token_usage'` 时承载 token 用量 |
| `metric_type` / `metric_name` | `ai_health_metrics` | 指标类型与名称 |
| `key` / `value` | `user_credentials_v2` | 凭据键值对（**不是** `name` / `secret`） |
| `project` / `config_json` | `project_configs` | 项目配置表的全部业务列 |
| `slice_id` | `requirements` | 来源切片，指向 `requirement_slices.slice_id` |

## 三、历史命名对照（旧名已废弃）

以下旧名在当前代码与数据库中已不存在，遇到时按下表换算：

| 旧名 | 现名 | 类型 |
|:---|:---|:---|
| `validation_intents` | `test_cases` | 表 |
| `intent_meta` | `case_meta` | 表 |
| `intent_requirement_mapping` | `case_requirement_mapping` | 表 |
| `intent_id` | `case_id` | 列 |
| `intent_type` | `case_type` | 列 |
| `intent_generator` | `case_generator` | 模块 |
| `model_validation_intents` | `model_test_cases` | 函数 |
| `steps_from_intent_schema` | `steps_from_case` | 函数 |
| `what_to_check` | `checks[{check, expect}]` | 数据结构 |

**保留不改的命名**：`browser_intent_parser/` 是 Step4 运行时的模块名，与数据模型无关，不参与上述改名。

## 四、执行状态取值

`executions.status` 用到的取值：

| 取值 | 含义 | 是否计入通过率分母 |
|:---|:---|:---:|
| `PASS` | 检查点通过且置信度达标 | 是 |
| `FAIL` | 判定不通过 | 是 |
| `SKIP` | 未执行 | 是 |
| `ERROR` | 执行侧错误 | 否 |
| `UNKNOWN` | 无法判定 | 否 |
| `BLOCKED` | 前置阻塞 | 否（分母默认含，严格模式剔除） |
| `PRE_GATE_BLOCKED` | 前置闸门阻塞 | 否（同上） |
| `FLAKY` | 识别为不稳定用例 | 否 |
| `MANUAL_REVIEW` | 交人工复核 | 否 |

通过率分母的口径依据 `src/pipeline/steps/step5/report_generator.py:86-101`。

## 五、易混接口与端点

| 端点 | 语义 | 是否需 body |
|:---|:---|:---|
| `POST /api/login` | 登录，JSON 键 `user_id`（兼容别名 `username`） | 是 |
| `POST /api/project` | 切换当前会话项目，键 `name`（兼容别名 `project`） | 是 |
| `POST /api/step/<n>` | 触发第 n 步流水线，body 含 `project` | 是 |
| `POST /api/review/approve` | 批量批准全部**需求**（Step2 闸门） | 否 |
| `POST /api/cases/review/approve` | 批准单条**用例** | 是（`case_id`） |
| `POST /api/cases/review/batch-approve` | 用例 green 车道批量放行 | 否 |
| `POST /api/cases/review/confirm` | 用例确认收尾 | 否 |
| `POST /api/review/lane/confirm` | 逐条人工审核确认 | 是 |

审核类端点的项目上下文来自**会话级**当前项目（通过 `POST /api/project` 设置），不从请求体读取 `project` 字段。多项目环境下操作前必须先切换会话项目。

## 六、常见混淆点

| 混淆 | 正确区分 |
|:---|:---|
| `SKIP` 与 `ERROR` | SKIP 是"没跑"（前置条件不满足）；ERROR 是"跑了但引擎/环境出错" |
| `HARNESS_ERROR` 与 `PRODUCT_BUG` | 前者是引擎能力边界或执行异常，后者是被测系统行为不符合需求 |
| 精度阈值 0.70 与车道阈值 85/60 | 0.70 是 Step4 终判阈值（用例是否算通过）；85/60 是 Step2 置信度车道阈值（需求是否可信）。两套口径不可混用 |
| `src/verification/` 与 `steps/step4/verification/` | 前者是引擎准确性验证台账（回答"引擎预测对不对"）；后者是执行期断言库（回答"用例过没过"） |
| `src/oracle/` 与普通断言 | oracle 是视觉预言机（VLM 判定视觉真值），提供独立真值源，不是断言执行器 |
| `token_usage` 表 | 不存在此表；token 用量是 `ai_health_metrics` 中 `metric_type='token_usage'` 的行 |
| 94 张表 | 实际 90 张。`schema.sql` 中 `CREATE TABLE` 字面出现 94 次，其中 4 次在注释行 |
| `admin/admin123` | 不是默认账号。管理员初始密码来自环境变量 `BOOTSTRAP_ADMIN_PASSWORD`、配置 `BOOTSTRAP_DEFAULT_PASSWORD`，或随机生成 |

## 七、术语来源

本附录的字段名与取值全部取自 `src/core/schema.sql` 的建表语句与相应代码（模块名、常量名与函数名给出所在文件）。历史命名对照取自 `src/架构点.md` 的命名约定一节，并已按当前代码复核。

复核结果需要如实说明：上表"旧名"中的 `validation_intents`、`intent_meta`、`intent_requirement_mapping`、`intent_type`、`steps_from_intent_schema`、`what_to_check`、`model_validation_intents` 在 `src/` 的 `.py` 与 `.sql` 文件中**已无残留**（逐名检索命中数为 0）。但 `intent_id` **仍有残留**，集中在三处：

| 残留位置 | 形态 |
|:---|:---|
| `plugin_interventions.intent_id`（`src/core/schema.sql:2018` 起） | 表列名。该表同时存在 `case_id` 与 `intent_id` 两列 |
| `src/pipeline/steps/step4/plugins/plugin_manager.py:318-323` | 向上述列写入 |
| `src/pipeline/steps/step4/browser_core/parallel.py:370` | `intent.get("id", intent.get("intent_id", "?"))` 兼容读取 |
| `assign_intent_identities`（`src/pipeline/step3/_identity_assign.py:245`） | 保留旧词根的函数名 |

这些残留不影响功能（`plugin_interventions` 的两个列名并存，读取方按各自的键取值），但会让按旧名检索的人得到"存在"的结论。修改表列名涉及数据迁移，列名残留属于已知状态。
