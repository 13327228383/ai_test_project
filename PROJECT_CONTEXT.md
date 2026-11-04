# PROJECT_CONTEXT · E2E 智能测试引擎 · GLM-5.2 开工必读（全量合并）

> 本文件是模型开工前必须**通读全文**的唯一上下文源。它合并了：AGENTS.md 红线 + 蓝图 §9/§10/§11 + Step1 改造 + P0-1~P0-4 + P1-P2 全部细化卡。
> 相对路径基址：`d:\AI\AI_Test_Preject\`（Trae 项目根）。

---

## 0. 硬红线（每条任务前自检，违反即停下重构）
- 单文件 ≤ 800 行；一个文件只做一件事；单函数 ≤ 80 行。
- 高度解耦：文件按功能/模块分文件夹（`src/pipeline/steps/*`、`src/oracle`、`src/calibration`、`src/rag`、`src/agent`、`src/eval`）；同功能聚同目录，禁平铺根目录、禁全塞 `utils/`。
- 任何 Python 改码后必须重启后端（`python -m src.backend.app --port 5001`）才生效；改完不重启 = 等于没改。
- 改前必做 §10.9 根因核查：现象→≥3 层 why→全局观→职责边界→通用性自检→自我反驳 3 次，缺一不可；禁止表层打补丁。
- 逐单元验证 §10.1：改 1 文件→重启→五链路两遍(§8)→通过才下一单元；严禁"改 N 文件后一起验证"。
- 禁止试错循环；禁止 `except:pass`（异常必须记录详情）；通用性优先；一条一例串行。
- MySQL 是唯一事实源；Step 之间只经表传递，禁内存变量跨 Step。
- 前端只做功能修复/新增，禁改样式布局。

---

## 1. 现状诊断（DB 实锤 · 为什么这么设计）
- `case_meta.confidence` 192 条**全 = 85.00**：基准分 85，与真实性零相关。
- `requirements.review_status` 63 条**全 approved**：`step2.py:660` `conf>=80` 自动置 approved；实际 `_evidence_confidence` 仅 0.65–0.70。
- `system_nav_maps` 34 条**0 条真实爬取**（4 confluence + 30 figma 图层名）。
- `page_dom_snapshots` **仅 5 页**；`step4_op_cache` **0 行**；`calibration` 休眠（`min_samples=30`）。
- 爬取不全根因：预算倒挂（180s 的 80% 给发现 / 20% 给快照）+ 零并发串行 + `max_elements=80` 页内截断 + `DomPruner` 30000 字截断。

---

## 2. P0-1 · DB schema v2（落点 `src/core/schema.sql`）
> schema 全部定义在 `src/core/schema.sql`，由 `db_schema._init_schema` 增量执行（`CREATE TABLE IF NOT EXISTS` 仅缺失表 + 幂等 `ALTER` 带 1060/1091 跳过）。**改完必须重启后端。**
> 通用约定（强制一致）：`ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`；`id BIGINT NOT NULL AUTO_INCREMENT`；`created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`；`updated_at ... ON UPDATE CURRENT_TIMESTAMP`；`project VARCHAR(128) NOT NULL`；版本隔离两维度（case/exec 用 `version_id BIGINT`，source/nav/dom 用 `version_label VARCHAR(128)`+FK）；FK 全 `ON DELETE CASCADE`；列名 snake_case 与 Python/JSON 逐字一致。

### 2.1 9 张新表 DDL
```sql
CREATE TABLE IF NOT EXISTS `nav_graph_nodes` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '节点自增ID',
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `page_url`      VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '完整页面URL（含 hash/query，Fix6 去重键）',
    `page_path`     VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '页面路径（path 部分，仅兜底去重）',
    `page_title`    VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '页面标题',
    `element_count` INT          NOT NULL DEFAULT 0 COMMENT '该页可交互元素数',
    `snapshot_id`   BIGINT       NULL     COMMENT '关联 page_dom_snapshots.id（真实 DOM 来源）',
    `source`        VARCHAR(32)   NOT NULL DEFAULT 'dom' COMMENT '来源: dom / manual',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_nav_node_url` (`project`, `version_label`, `page_url`(255)),
    INDEX `idx_nav_node_project` (`project`, `version_label`),
    INDEX `idx_nav_node_snapshot` (`snapshot_id`),
    CONSTRAINT `fk_nav_node_version` FOREIGN KEY (`project`, `version_label`) REFERENCES `project_versions` (`project`, `version_label`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='真实爬取导航图节点（替 paper nav_map）';

CREATE TABLE IF NOT EXISTS `nav_graph_edges` (
    `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '边自增ID',
    `project`        VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `from_node`      BIGINT       NOT NULL COMMENT '源节点 → nav_graph_nodes.id',
    `to_node`        BIGINT       NOT NULL COMMENT '目标节点 → nav_graph_nodes.id',
    `trigger_action` JSON         NULL     COMMENT '触发动作（json: {type, selector, value}）',
    `via_tab`        VARCHAR(128) NOT NULL DEFAULT '' COMMENT '经 Tab（如 叶组配方/掺配物配方）',
    `via_sidebar`    VARCHAR(128) NOT NULL DEFAULT '' COMMENT '经侧边栏菜单路径',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_nav_edge_project` (`project`, `version_label`),
    INDEX `idx_nav_edge_nodes` (`from_node`, `to_node`),
    CONSTRAINT `fk_nav_edge_version` FOREIGN KEY (`project`, `version_label`) REFERENCES `project_versions` (`project`, `version_label`) ON DELETE CASCADE,
    CONSTRAINT `fk_nav_edge_from` FOREIGN KEY (`from_node`) REFERENCES `nav_graph_nodes` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_nav_edge_to` FOREIGN KEY (`to_node`) REFERENCES `nav_graph_nodes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='页面间转移边（状态转移，喂 op_cache）';

CREATE TABLE IF NOT EXISTS `op_cache_v2` (
    `id`               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '缓存记录ID',
    `project`          VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`       BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `intent_hash`      CHAR(64)     NOT NULL COMMENT '意图哈希（同意图复现键）',
    `page_fingerprint` VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '页面指纹（定位命中）',
    `action_sequence`  JSON         NOT NULL COMMENT '真实原子指令序列（json 数组）',
    `hit_count`        INT          NOT NULL DEFAULT 0 COMMENT '命中次数（SOTA replay 统计）',
    `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`       DATETIME     NULL     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_op_cache_intent` (`project`, `version_id`, `intent_hash`),
    INDEX `idx_op_cache_fp` (`page_fingerprint`),
    INDEX `idx_op_cache_project` (`project`, `version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='真实原子指令序列缓存（替 0 行 op_cache，SOTA 三拍第①拍）';

CREATE TABLE IF NOT EXISTS `calibration_samples` (
    `id`               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '样本ID',
    `project`          VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`       BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id',
    `case_id`          VARCHAR(128) NOT NULL DEFAULT '' COMMENT '关联用例ID → test_cases.case_id',
    `predicted_verdict` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '模型预测结论: PASS/FAIL',
    `human_verdict`    VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '人工结论: PASS/FAIL',
    `features`         JSON         NULL     COMMENT '特征向量（json，供校准模型）',
    `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '回流时间',
    PRIMARY KEY (`id`),
    INDEX `idx_cal_project` (`project`, `version_id`),
    INDEX `idx_cal_case` (`case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='主动学习样本（替休眠 calibration，解 85 常量根因）';

CREATE TABLE IF NOT EXISTS `oracle_results` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '结果ID',
    `project`      VARCHAR(128) NOT NULL COMMENT '项目名',
    `version_id`   BIGINT       NOT NULL DEFAULT 0 COMMENT '版本ID → project_versions.id',
    `execution_id` BIGINT       NOT NULL DEFAULT 0 COMMENT '关联 executions.id',
    `oracle_type`  VARCHAR(32)  NOT NULL COMMENT 'oracle 类型: existence/correctness/completeness',
    `verdict`      VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '结论: pass/fail/warn',
    `score`        DECIMAL(5,4) NOT NULL DEFAULT 0 COMMENT '得分 0~1',
    `detail`       JSON         NULL     COMMENT '明细（json）',
    `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_oracle_exec` (`execution_id`, `oracle_type`),
    INDEX `idx_oracle_project` (`project`, `version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每次执行的三种 oracle 结论';

CREATE TABLE IF NOT EXISTS `visual_review` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '复核会话ID',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名',
    `version_id`      BIGINT       NOT NULL DEFAULT 0 COMMENT '版本ID',
    `execution_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '关联 executions.id',
    `baseline_id`     BIGINT       NULL     COMMENT '关联 visual_baselines.id',
    `screenshot_path` VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '截图路径',
    `vlm_diff`        JSON         NULL     COMMENT 'VLM 差异区域（json: [{x,y,w,h,desc}]）',
    `human_confirm`   VARCHAR(32)  NULL     COMMENT '人工确认: match/mismatch/pending',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_visual_exec` (`execution_id`),
    INDEX `idx_visual_baseline` (`baseline_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='VLM 视觉 oracle 复核会话';

CREATE TABLE IF NOT EXISTS `multimodal_chunks` (
    `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '块ID',
    `project`     VARCHAR(128) NOT NULL COMMENT '项目名',
    `source_type` VARCHAR(32)  NOT NULL COMMENT '来源类型: requirement/dom/figma',
    `ref_id`      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源对象ID',
    `content`     MEDIUMTEXT   NOT NULL COMMENT '块内容',
    `embedding`   MEDIUMTEXT   NULL     COMMENT '向量（json 数组或外部向量库 ID）',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_mm_source` (`project`, `source_type`, `ref_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='多模态 RAG 块（需求+DOM+figma）';

CREATE TABLE IF NOT EXISTS `review_lane` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '分流记录ID',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名',
    `version_id`      BIGINT       NOT NULL DEFAULT 0 COMMENT '版本ID',
    `case_id`         VARCHAR(128) NOT NULL DEFAULT '' COMMENT '关联用例ID → test_cases.case_id',
    `lane`            VARCHAR(16)  NOT NULL COMMENT '车道: green/yellow/red',
    `routed_to_human` TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否送人审: 0/1',
    `reason`          VARCHAR(512) NOT NULL DEFAULT '' COMMENT '分流原因（锚点合取分说明）',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_lane_case` (`project`, `version_id`, `case_id`),
    INDEX `idx_lane_lane` (`lane`),
    INDEX `idx_lane_project` (`project`, `version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='三车道路由（绿/黄/红），替自动 approved';

CREATE TABLE IF NOT EXISTS `golden_set` (
    `id`                  BIGINT       NOT NULL AUTO_INCREMENT COMMENT '条目ID',
    `project`             VARCHAR(128) NOT NULL COMMENT '项目名',
    `scenario`            VARCHAR(512) NOT NULL DEFAULT '' COMMENT '场景描述',
    `expected_verdict`    VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '期望结论: PASS/FAIL',
    `expected_confidence` DECIMAL(5,4) NOT NULL DEFAULT 0 COMMENT '期望置信度 0~1',
    `notes`               TEXT         NULL     COMMENT '备注',
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_golden_project` (`project`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='引擎自评估黄金集';
```

### 2.2 既有表最小修正（schema 层）
```sql
-- 标记纸地图废弃（P0-3 停止写入 system_nav_maps，迁移到 nav_graph_*）
ALTER TABLE `system_nav_maps` ADD COLUMN `deprecated` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '★P0-1: 纸地图废弃标记，1=不再写入/读取';
```
- `case_meta.confidence`：列保留，语义由"恒定 85"改为"锚点合取真分"——写入逻辑在 P0-3 改，schema 不改列。
- `requirements.review_status`：列保留，实际分流改走 `review_lane`（P0-4）。

### 2.3 落地分组（§10.1）
批 A：`nav_graph_nodes` + `nav_graph_edges` → 批 B：`op_cache_v2` + `calibration_samples` + `golden_set` → 批 C：`oracle_results` + `visual_review` + `multimodal_chunks` + `review_lane` + `system_nav_maps.deprecated`。每批：写 DDL → 重启 → 五链路两遍 → 过才下一批。
### 2.4 验证（五链路两遍）
- 可运行：对每张表 `SHOW CREATE TABLE` 断言列/引擎/字符集；查 `information_schema.statistics` 断言索引；查 `table_constraints` 断言 FK 级联；`(project,version_label)` 隔离 INSERT/DELETE 校验。
- 第一遍脚本全绿；第二遍重启后 `_init_schema` 幂等路径再跑，确认已存在表不重复 CREATE、ALTER 幂等。

---

## 3. P0-2 · Step1 爬全（Fix1 + Fix2）— **已废弃 · 2026-07-29**
> **状态**: 代码已实现（预算反转 `_DOM_CRAWLER_MAX_PAGES=0` + 真并行快照），但**方向已废弃**。
> **废弃原因**（用户决策 + 行业共识）：静态 DOM 爬取是"先爬全再测试"的错误模式——Step1 爬的时候不知道 Step4 要测什么，页面可能已变化。行业前沿（BrowserGym、WebVoyager、Agent-E）已全面转向 Agent 实时探索（感知→决策→执行闭环）。**不再对 P0-2 做任何验证和修复。**
> **替代方案**: P2-9 CoverageCrawl（覆盖率驱动探索）+ P2-10 AgentExplorer（实时页面探索），提至 P1 优先级。
> **落点**（仅保留代码不删，供参考）：`src/pipeline/steps/step1/dom_crawler.py`、`dom/navigator.py`、`dom/extractor.py`。

---

## 4. P0-3 · 预言层补丁
落点：`src/pipeline/steps/step2/`（score_test_case、step2.py:660）、`src/pipeline/steps/step3/`。
- **3a 删 85 恒定分→锚点合取真分**：定位 `score_test_case` 基准分 85 → 删恒定基准，改锚点合取（source_traceable/source_verified/action_reachable/novelty 加权）；定位 `step2.py:660` `conf>=80→approved` → 删自动 approved，改算锚点合取分并写 `review_lane.lane`+`reason`；`case_meta.confidence` 写入真分（列保留）。验证：confidence 不再常数 85；requirements 不再全 approved；review_lane 有分流。
- **3b nav_map 纸→nav_graph**：Step1(P0-2) 写 `nav_graph_nodes/edges`；Step3 改读 nav_graph（替 system_nav_maps）；system_nav_maps 标 deprecated 停写；Step3 读 `dom_summary` 同步改读 `page_elements` 结构化字段（Fix6）。验证：导航来源=nav_graph_nodes；system_nav_maps 无新写入。

---

## 5. P0-4 · 三车道后端
落点：`src/backend/routes/`（新增 review 路由）、`step2|step3/`。
- 路由：锚点合取分→green(自动, routed_to_human=0)/yellow(跑但待审,=1)/red(阻塞,=1)；禁 AI 自评。
- API：`GET /api/review/lane?project=&version_id=` 返回 `case_id/lane/reason`；`POST /api/review/lane/confirm` 人工确认；**删除/重构** `/api/cases/review/batch-approve` 一键全过（击穿闸门）。
- 验证：green 自动、黄红送人；批量绕过质量闸门已消除；`review_lane` 列↔JSON key 一致。

### 5.5 P0-降级清理 · 违背测试理论全面重构（2026-07-29 完成）
> **状态**: 已完成。降级机制已从系统完全删除。所有违背测试理论的问题已修复。

**背景**: 用户决策"降级=失败"，测试引擎不应对用例做降级处理。失败就是失败，成功就是成功。

**已修复项**:
| 类别 | 修复项 | 文件 |
|:---|:---|:---|
| A1 | 检测异常(宽容通过) → FAIL | `_base.py:1006-1007` |
| A2 | 无API调用记录(宽容通过) → FAIL | `_base.py:1025-1027` |
| A3 | 无法获取状态保守通过 → False | `ground_truth.py:111-112` |
| A4 | Confidence Gate异常绕过 → 视为FAIL | `_confidence_gate.py:103-107` |
| B1 | 异常降级(pass) → 记录异常+FAIL | 多文件 |
| C1 | 删除display_rule.py | 已删除 |
| C2 | 删除_build_downgrade_cases调用 | case_rules/ 全部9文件 |
| C3 | _validate_actions_against_dom 重写 | `_rule_extractor_validate.py` |
| C4 | 清理降级残留注释 | case_rules/, _fallback_cases.py, _control_validation.py |
| D1 | 删除low_confidence豁免门禁 | `_intents.py:77-80` |
| E1 | PRE_GATE不再算PASS | `pipeline_progress.py:274` |

**原则**: 降级=失败，display用例已删除，宽容通过/保守通过已修复为FAIL，低置信度不豁免门禁。

---

## 6. P1 · Agent 探索 + 保真闭环（P0-2 废弃后的新主线）
> **2026-07-29 重组**: P0-2（静态 DOM 爬取）已废弃。原 P2-9/P2-10 提至 P1 作为新主线。目标：用 Agent 实时探索替代静态 DOM 爬取。

### P1-0 AgentExplorer 接入主流程（最优先）
落点：`src/agent/explorer.py`（已有代码）+ `src/pipeline/steps/step4/`（接入点）。
- **目标**: Step4 不再依赖 Step1 的静态 DOM 快照，改为 AgentExplorer 在浏览器中实时感知页面、动态决策操作。
- **接入点**: Step4 的 `_execute_one_intent` 中，当 `nav_graph` 无对应页面时，AgentExplorer 自主探索（navigate→snapshot→semantic match→click→observe），找不到按钮直接 FAIL，不降级。
- **验证**: 一条用例在陌生页面（nav_graph 无记录）上，AgentExplorer 能自主找到按钮并操作，找不到直接 FAIL。

### P1-1 CoverageCrawl 接入 Step1（替代 DOM 爬取）
落点：`src/agent/coverage_crawl.py`（已有代码）+ `src/pipeline/steps/step1/`。
- **目标**: Step1 不再做静态 DOM 爬取，改为 CoverageCrawl 覆盖率驱动探索——"哪些页面还没爬？哪些按钮还没点？"
- **接入点**: Step1 Phase 3（原 DOM 爬取阶段），改为调用 CoverageCrawl.feedback() 获取未覆盖目标，AgentExplorer 逐目标探索。
- **验证**: Step1 完成后 nav_graph_nodes 的页面数 ≥ 菜单项数，且每个节点有真实 page_elements。

### P1-2 VLM oracle（视觉断言）
落点：`src/oracle/vlm_adapter.py`+`correctness_vlm.py`，接本地 Ollama+Qwen2.5-VL（零成本）。Ollama 不可用时降级不阻断。

### P1-3 校准环
落点：`src/calibration/sampler.py`+`trainer.py`，人改→回流 `calibration_samples`。

### P1-4 前端人审界面
落点：`src/ui/`，新增人审/三车道分色/视觉复核/校准回流界面（禁改样式布局）。

### P1-5 op_cache 接通
落点：`step4/` + `op_cache_v2`，SOTA 三拍 record→replay→repair。验证：同意图二次执行命中 `op_cache_v2`（`hit_count+1`）不调 AI。

---

## 7. P2 · 前沿增强（H2，先小样本证伪再扩）
- **P2-1 MultiModalRAG**：`src/rag/`，需求+DOM+figma 联合检索，防幻觉。
- **P2-2 SelfEval**：`src/eval/`，golden_set 自评估，`run(golden_set) -> {accuracy, false_pass_rate, false_fail_rate, coverage}`。

---

## 8. 执行顺序与门禁（严格串行，每单元独立验证）
> **2026-07-29 更新**: P0-2 已废弃跳过。P0-1→P0-3→P0-4 已完成。P0-降级清理 已完成。
> **2026-07-29 重构方案**: 详见 `docs/五步流水线重构方案_2026-07-29.md`。

**已完成**: P0-1（DB schema v2）→ ~~P0-2（已废弃）~~ → P0-3（预言层补丁）→ P0-4（三车道后端）→ P0-降级清理（A/B/C/D/E类修复）
**重构方案（A类→B类→C类）**: A1~A8 ✓ → B1~~跳过~~ B2~B8 ✓ → C1-1(py_compile) ✓ → C1-2(数据链路) ✓ → C1-3(API探活) → C1-4(两遍)
**架构决策(2026-07-29下午)**: 不合并Step3+Step4。Agent从"探索者"变为"执行者"——按Step3生成的测试方法执行，不自由探索。
**下一阶段**: R1-R4（强化Step3→Step4协作），详见 `docs/CHANGE_LOG.md`。

每个单元：先读本文件对应节 → §10.9 根因核查 → 改码 → 重启后端 → 五链路两遍 → 通过才下一单元。
授权：P0 已全部完成。重构方案执行中。诚实记录 PASS/FAIL，禁止假 PASS。
