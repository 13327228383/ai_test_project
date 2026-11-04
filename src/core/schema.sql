-- ============================================================================
-- E2E 智能测试引擎 — MySQL 核心 Schema
-- ============================================================================
-- 版本: 3.0
-- 说明: 本文件定义所有 MySQL 表结构（统一管理，消除 store 文件分散建表）。
--       由 db_manager.py 在首次连接时自动执行（CREATE TABLE IF NOT EXISTS）。
--       所有以 project 为维度的表均设置 ON DELETE CASCADE 外键。
-- ============================================================================

-- ============================================================================
-- 第0域：项目 (Project) — 必须先于所有引用它的表
-- ============================================================================

CREATE TABLE IF NOT EXISTS `projects` (
    `name`        VARCHAR(128)  NOT NULL COMMENT '项目名（唯一标识，如 某SPA系统）',
    `status`      VARCHAR(32)   NOT NULL DEFAULT 'active' COMMENT '状态: active / archived / deleted',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`name`),
    INDEX `idx_projects_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目表 — 每个测试项目对应一条记录（元数据见 project_meta 表）';

CREATE TABLE IF NOT EXISTS `project_versions` (
    `id`            BIGINT        NOT NULL AUTO_INCREMENT COMMENT '版本记录ID',
    `project`       VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128)  NOT NULL COMMENT '版本标签（如 20260610_152230 / v1.2.3）',
    `description`   VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '版本描述',
    `status`        VARCHAR(32)   NOT NULL DEFAULT 'draft' COMMENT '状态: draft / active / archived / deleted',
    `created_by`    VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '创建者用户ID → users.id',
    `created_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `activated_at`  DATETIME      NULL     COMMENT '激活时间',
    `deleted_at`    DATETIME      NULL     COMMENT '★ D-1-1: 软删除时间（status=deleted 时填写）',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_project_version` (`project`, `version_label`),
    INDEX `idx_ver_status` (`project`, `status`),
    CONSTRAINT `fk_versions_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目版本表 — 每次Step1拉取或版本切换产生一个版本记录';

CREATE TABLE IF NOT EXISTS `project_configs` (
    `project`     VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `config_json` JSON         NOT NULL COMMENT '项目配置（JSON，合并到系统配置中）',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`),
    CONSTRAINT `fk_configs_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目配置表 — 存储项目级配置，替代旧的 projects/{name}/config.py 文件';

-- ============================================================================
-- 第1域：用户与权限 (User & Auth)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `users` (
    `id`              VARCHAR(64)   NOT NULL  COMMENT '用户ID（唯一标识）',
    `name`            VARCHAR(128)  NOT NULL  COMMENT '用户昵称/显示名',
    `password`        VARCHAR(256)  NOT NULL  COMMENT '密码哈希（SHA-256，不加盐）',
    `failed_attempts` INT           NOT NULL DEFAULT 0  COMMENT '连续登录失败次数（成功后归零）',
    `locked_until`    DATETIME      NULL      COMMENT '锁定截止时间（NULL=未锁定）',
    `last_login`      DATETIME      NULL      COMMENT '最后登录时间',
    `status`          VARCHAR(32)   NOT NULL DEFAULT 'active' COMMENT '用户状态（active/disabled）',
    `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `last_project` varchar(128) NOT NULL DEFAULT '' COMMENT '用户上次选择的项目（登录后自动恢复，空=未选过）',
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户账号表 — 存储登录用户的基本信息';

CREATE TABLE IF NOT EXISTS `roles` (
    `id`          VARCHAR(64)   NOT NULL  COMMENT '角色ID（如 admin / user / viewer）',
    `name`        VARCHAR(128)  NOT NULL  COMMENT '角色显示名（如 管理员 / 普通用户 / 只读用户）',
    `description` VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '角色描述',
    `is_system`   TINYINT(1)    NOT NULL DEFAULT 0  COMMENT '是否系统内置角色（1=是，不可删除）',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色定义表 — 定义系统中可用的角色';

CREATE TABLE IF NOT EXISTS `user_roles` (
    `user_id`    VARCHAR(64)  NOT NULL  COMMENT '用户ID → users.id',
    `role_id`    VARCHAR(64)  NOT NULL  COMMENT '角色ID → roles.id',
    `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    PRIMARY KEY (`user_id`, `role_id`),
    INDEX `idx_user_roles_role` (`role_id`),
    CONSTRAINT `fk_user_roles_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_user_roles_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户-角色关联表 — 一个用户可有多个角色';

CREATE TABLE IF NOT EXISTS `role_permissions` (
    `role_id`     VARCHAR(64)  NOT NULL  COMMENT '角色ID → roles.id',
    `permission`  VARCHAR(128) NOT NULL  COMMENT '权限标识（如 execution.run / project.manage / report.view）',
    `granted`     TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否授予（1=授予；0=显式撤销，用于角色权限覆盖）',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    PRIMARY KEY (`role_id`, `permission`),
    CONSTRAINT `fk_role_permissions_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色-权限关联表 — 定义每个角色拥有的权限集合';

CREATE TABLE IF NOT EXISTS `role_projects` (
    `role_id`     VARCHAR(64)  NOT NULL  COMMENT '角色ID → roles.id',
    `project`     VARCHAR(128) NOT NULL  COMMENT '项目名 → projects.name',
    `level`       VARCHAR(32)  NOT NULL DEFAULT 'member' COMMENT '项目级身份: owner/admin/member/viewer',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    PRIMARY KEY (`role_id`, `project`),
    INDEX `idx_rp_project` (`project`),
    CONSTRAINT `fk_rp_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_rp_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色-项目关联表 — 定义角色在项目中的身份级别(owner/admin/member/viewer)';

CREATE TABLE IF NOT EXISTS `project_members` (
    `user_id`    VARCHAR(64)  NOT NULL  COMMENT '用户ID → users.id',
    `project`    VARCHAR(128) NOT NULL  COMMENT '项目名 → projects.name',
    `role`       VARCHAR(32)  NOT NULL DEFAULT 'member' COMMENT '项目身份: owner/member',
    `added_by`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '添加者用户ID',
    `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    PRIMARY KEY (`user_id`, `project`),
    INDEX `idx_pm_project` (`project`),
    CONSTRAINT `fk_pm_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_pm_project_members` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目成员表 — 直接绑定用户与项目的身份关系(owner/member两级)';

CREATE TABLE IF NOT EXISTS `user_credentials_v2` (
    `user_id`  VARCHAR(64)  NOT NULL  COMMENT '用户ID → users.id',
    `project`  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '项目名（* 表示全局凭证）',
    `key`      VARCHAR(128) NOT NULL  COMMENT '凭证键名（如 API_KEY / API_MODEL / API_BASE_URL）',
    `value`    TEXT         NOT NULL  COMMENT '凭证值（加密存储）',
    `created_at` DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`user_id`, `project`, `key`),
    INDEX `idx_creds_project` (`project`),
    CONSTRAINT `fk_user_credentials_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户凭证表 — 存储用户在各项目中的 API Key / 模型偏好等敏感配置';

CREATE TABLE IF NOT EXISTS `login_tracking` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `user_id`     VARCHAR(64)   NOT NULL COMMENT '用户ID → users.id',
    `action`      VARCHAR(32)   NOT NULL COMMENT '动作: login / logout / login_failed',
    `ip_address`  VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '登录IP',
    `user_agent`  TEXT          NOT NULL COMMENT '浏览器UA',
    `detail`      TEXT          NOT NULL COMMENT '详细信息（失败原因等）',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
    PRIMARY KEY (`id`),
    INDEX `idx_login_user` (`user_id`, `created_at`),
    CONSTRAINT `fk_login_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='登录追踪表 — 记录用户的登录/登出/失败历史';

CREATE TABLE IF NOT EXISTS `flask_sessions` (
    `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `session_id`  VARCHAR(255) NULL     COMMENT 'Flask session ID（唯一）',
    `data`        BLOB         NULL     COMMENT '序列化后的 session 数据',
    `expiry`      DATETIME     NULL     COMMENT 'session 过期时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_session_id` (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Flask Session 存储表 — 服务端持久化用户登录态';

CREATE TABLE IF NOT EXISTS `api_tokens` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `token_hash`   VARCHAR(128) NOT NULL COMMENT 'token 的 SHA256 哈希（不存明文）',
    `project`      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '授权项目（* 表示所有项目，故不设置外键）',
    `label`        VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Token 用途标签（如 Jenkins CI / GitHub Actions）',
    `scopes`       TEXT         NULL     COMMENT '授权范围（JSON 数组，如 ["run","read"]）',
    `created_by`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '创建者 user_id → users.id',
    `last_used_at` DATETIME     NULL     COMMENT '最后使用时间',
    `use_count`    BIGINT       NOT NULL DEFAULT 0 COMMENT '累计使用次数',
    `is_active`    TINYINT      NOT NULL DEFAULT 1 COMMENT '是否启用（0=停用 1=启用）',
    `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`   DATETIME     NULL     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_apit_hash` (`token_hash`),
    INDEX `idx_apit_project` (`project`),
    INDEX `idx_apit_active` (`is_active`),
    CONSTRAINT `fk_apit_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='API Token 鉴权表 — CI/CD 外部调用凭据，SHA256 哈希存储';

-- ============================================================================
-- [墓碑] project_access — 已移除：role_projects 已覆盖项目级权限，无业务读写
-- ============================================================================

-- ============================================================================
-- 第3域：测试用例元数据 (Case Meta)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `case_meta` (
    `case_id`           VARCHAR(64)  NOT NULL COMMENT '测试用例编号',
    `project`           VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`        BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `case_asset_file`         VARCHAR(256) NOT NULL DEFAULT '' COMMENT '来源文件名',
    `confidence`        DECIMAL(5,2) NOT NULL DEFAULT 0.00 COMMENT '置信度评分（0-100）',
    `risk_level`        VARCHAR(32)  NOT NULL DEFAULT 'medium' COMMENT '风险等级: low / medium / high / critical',
    `risk_embedding`    JSON         NULL     COMMENT '风险分析用embedding向量(384维，用于k-NN分类)',
    `review_status`     VARCHAR(32)  NOT NULL DEFAULT 'pending' COMMENT '审核状态: pending / approved / rejected / modified',
    `reviewed_by`       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '审核人用户ID → users.id',
    `reviewed_at`       DATETIME     NULL     COMMENT '审核时间',
    `case_coverage`     FLOAT      DEFAULT NULL COMMENT '用例覆盖度（Step3 指标，与 case_completeness 双轨保留）',
    `case_completeness` FLOAT      DEFAULT NULL COMMENT '用例完整度（Step3 指标: 五字段填充率）',
    `evidence_total`     FLOAT      DEFAULT NULL COMMENT '证据总量（Step3 指标: UI+API+DB 三轨条目数，FLOAT 兼容浮点统计）',
    `avg_confidence`     FLOAT      DEFAULT NULL COMMENT '平均置信度（Step3 指标）',
    `case_driven_rate` FLOAT      DEFAULT NULL COMMENT '用例驱动率（Step3 指标）',
    `strictness_score`   FLOAT      DEFAULT NULL COMMENT '严格度评分（Step4 指标: must/should/may 加权）',
    `execution_factors`  JSON       NULL     COMMENT 'System B 执行置信度因子明细（Step4 指标: assertion_rate等6因子JSON）',
    `evidence_confidence` FLOAT     NULL     COMMENT 'System C 证据校准置信度（Step4 指标: 贝叶斯融合AI声称+历史校准+独立证据）',
    `evidence_ai_raw`    FLOAT      NULL     COMMENT 'System C AI原始声称置信度（0-1）',
    `evidence_cal_accuracy` FLOAT   NULL     COMMENT 'System C 历史校准准确率（0-1，无数据时NULL）',
    `evidence_strength`  FLOAT      NULL     COMMENT 'System C 独立证据强度（0-1）',
    `evidence_level`     VARCHAR(16) NULL    COMMENT 'System C 置信度等级: HIGH/MEDIUM/LOW',
    `evidence_verdict`   VARCHAR(32) NULL    COMMENT 'System C 判决类型: evidence_calibrated/calibrated_only/cold_start/ai_only',
    `evidence_cold_start` TINYINT   NULL     COMMENT 'System C 是否冷启动: 1=冷启动 0=有足够校准样本',
    `action_coverage`    VARCHAR(32)  DEFAULT '' COMMENT '动作覆盖率（Step3 指标: 动作被用例覆盖的比例）',
    `assertion_coverage` VARCHAR(32)  DEFAULT '' COMMENT '断言覆盖率（Step3 指标: 断言被用例覆盖的比例）',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `method_valid` tinyint NOT NULL DEFAULT '1' COMMENT '★T2: 方法论标签校验是否通过(1=通过/0=非法已处置)',
    `method_fix_note` varchar(255) NOT NULL DEFAULT '' COMMENT '★T2: 方法论标签修正/标记说明（如 白名单外test_method=XX 已就近映射为EP）',
    PRIMARY KEY (`project`, `version_id`, `case_id`),
    INDEX `idx_meta_review` (`project`, `review_status`),
    INDEX `idx_meta_risk` (`project`, `risk_level`),
    INDEX `idx_case_meta_version` (`project`, `version_id`),
    CONSTRAINT `fk_meta_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试用例元数据表 — 测试用例的审核/置信度/风险/覆盖率指标等元信息（schema.sql 是唯一事实源）';

-- ============================================================================
-- 第4域：执行记录 (Execution)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `executions` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '执行记录ID',
    `run_id`          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '流水线运行ID（关联 pipeline_checkpoints.run_id）',
    `version_id`      BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `user_id`         VARCHAR(64)   NOT NULL COMMENT '执行用户ID → users.id',
    `project`         VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `case_asset_file`       VARCHAR(256)  NOT NULL DEFAULT '' COMMENT '用例资产文件名',
    `case_row`        INT           NOT NULL DEFAULT 0  COMMENT '在文件中的行号（兼容字段）',
    `case_id`         VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '测试用例编号',
    `status`          VARCHAR(32)   NOT NULL COMMENT '结果: PASS / FAIL / SKIP / ERROR / FLAKY',
    `failure_reason`  TEXT          NOT NULL COMMENT '失败原因（FAIL/ERROR时填写）',
    `failure_category` VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '失败归属: PRODUCT_BUG=被测系统bug / HARNESS_ERROR=执行系统bug / ENVIRONMENT_ERROR=环境问题 / DATA_ERROR=数据问题',
    `failure_chain`   TEXT          NULL COMMENT '失败链路JSON：记录策略执行序列和每步结果，用于精准判定failure_category',
    `screenshot`      VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '失败截图路径',
    `executed_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间',
    `verification_track` varchar(32) NOT NULL DEFAULT '' COMMENT 'D6M 功能轨道: api_track/db_track/ui_track',
    `cross_cut_dims` varchar(200) NOT NULL DEFAULT '' COMMENT 'D6M 横切维度声明: security,accessibility,performance,visual',
    `check_type` varchar(32) NOT NULL DEFAULT '' COMMENT '检查类型: pre_gate/step_exec/...',
    `expected` MEDIUMTEXT NULL COMMENT '预期结果',
    `actual` MEDIUMTEXT NULL COMMENT '实际结果',
    `details` MEDIUMTEXT NULL COMMENT '详细信息',
    `confidence` double NOT NULL DEFAULT '0' COMMENT '置信度评分（0-1，ConfidenceGate 计算后写入，_run_step 直接读取）',
    `confidence_factors` text NULL COMMENT '置信度因子JSON（assertion_rate/case_completeness/hard_verify_rate/first_try_rate/semantic_align/cache_hit_rate）',
    `diagnosis` varchar(64) NULL COMMENT '失败根因分类: SUT_DB_NOT_MIGRATED/SUT_UNREACHABLE/CREDENTIAL_INVALID/UNKNOWN',
    PRIMARY KEY (`id`),
    INDEX `idx_exec_run` (`run_id`),
    INDEX `idx_exec_version` (`project`, `version_id`, `case_id`),
    INDEX `idx_exec_user` (`user_id`),
    INDEX `idx_exec_project` (`project`, `executed_at`),
    INDEX `idx_exec_case` (`project`, `case_id`),
    INDEX `idx_verification_status` (`project`, `status`),
    CONSTRAINT `fk_exec_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_exec_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='执行记录表 — 每次测试用例执行验证的结果记录';

-- ============================================================================
-- FlakyGuard 历史记录与检测结果
-- ============================================================================

CREATE TABLE IF NOT EXISTS `flaky_guard_history` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `run_id`          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '流水线运行ID',
    `project`         VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `version_id`      BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID',
    `case_id`         VARCHAR(64)   NOT NULL COMMENT '测试用例编号',
    `passed`          TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '是否通过: 1=PASS / 0=FAIL',
    `failure_detail`  TEXT          NOT NULL COMMENT '失败原因',
    `step_count`      INT           NOT NULL DEFAULT 0 COMMENT '步骤数',
    `recorded_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    PRIMARY KEY (`id`),
    INDEX `idx_flaky_hist_case` (`project`, `version_id`, `case_id`),
    INDEX `idx_flaky_hist_time` (`project`, `case_id`, `recorded_at`),
    INDEX `idx_flaky_hist_run` (`run_id`),
    CONSTRAINT `fk_flaky_hist_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='FlakyGuard 执行历史 — 每次测试用例执行后持久化，用于失败率统计';

CREATE TABLE IF NOT EXISTS `flaky_guard_detected` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `project`         VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `version_id`      BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID',
    `case_id`         VARCHAR(64)   NOT NULL COMMENT '测试用例编号',
    `failure_rate`    FLOAT         NOT NULL DEFAULT 0 COMMENT '失败率',
    `total_runs`      INT           NOT NULL DEFAULT 0 COMMENT '总执行次数',
    `failures`        INT           NOT NULL DEFAULT 0 COMMENT '失败次数',
    `root_cause`      VARCHAR(64)   NOT NULL DEFAULT 'unknown' COMMENT '根因分类',
    `fix_suggestion`  TEXT          NOT NULL COMMENT '修复建议',
    `first_seen`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次发现时间',
    `last_seen`       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近发现时间',
    `detected_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '检测时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_flaky_detected_case` (`project`, `version_id`, `case_id`),
    INDEX `idx_flaky_detected_project` (`project`, `version_id`),
    INDEX `idx_flaky_detected_case` (`case_id`),
    CONSTRAINT `fk_flaky_detected_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='FlakyGuard 检测结果 — 标记不稳定测试用例及根因';

CREATE TABLE IF NOT EXISTS `action_logs` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `user_id`     VARCHAR(64)   NOT NULL COMMENT '操作用户ID → users.id',
    `scope_type`  VARCHAR(32)   NOT NULL DEFAULT 'system' COMMENT '日志范围类型（system/project/user/global）',
    `scope_id`    VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '范围ID（项目名/用户ID/空）',
    `action`      VARCHAR(128)  NOT NULL COMMENT '操作标识（如 step1.run / step3.model / intent.approve）',
    `detail`      JSON          NULL     COMMENT '操作详情（JSON）',
    `ip_address`  VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '操作来源IP地址（PV-007 审计追踪）',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    PRIMARY KEY (`id`),
    INDEX `idx_act_user` (`user_id`, `created_at`),
    INDEX `idx_act_scope` (`scope_type`, `scope_id`, `action`),
    CONSTRAINT `fk_action_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='操作日志表 — 记录用户关键操作，用于审计追踪；默认保留90天且全表最多100000行';

CREATE TABLE IF NOT EXISTS `config_history` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `user_id`     VARCHAR(64)   NOT NULL COMMENT '操作用户ID → users.id',
    `scope_type`  VARCHAR(32)   NOT NULL DEFAULT 'project' COMMENT '配置范围类型（system/project/user/global）',
    `scope_id`    VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '范围ID（项目名/用户ID/空）',
    `section`     VARCHAR(128)  NOT NULL COMMENT '配置节（如 ai / playwright / storage）',
    `old_value`   JSON          NULL     COMMENT '旧值',
    `new_value`   JSON          NULL     COMMENT '新值',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '变更时间',
    PRIMARY KEY (`id`),
    INDEX `idx_cfg_scope` (`scope_type`, `scope_id`),
    CONSTRAINT `fk_config_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='配置变更历史表 — 记录配置项变更的前后值';

-- ============================================================================
-- 第5域：文档版本 (Document Versions)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `document_versions` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `project`     VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `doc_type`    VARCHAR(64)   NOT NULL COMMENT '文档类型: requirement / validation_intent / report / xmind',
    `version`     VARCHAR(128)  NOT NULL COMMENT '版本标识',
    `file_path`   VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '文件路径',
    `file_hash`   VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '文件MD5哈希',
    `file_size`   BIGINT        NOT NULL DEFAULT 0  COMMENT '文件大小（字节）',
    `created_by`  VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '创建者用户ID → users.id',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_doc_project` (`project`, `doc_type`),
    INDEX `idx_doc_hash` (`file_hash`),
    CONSTRAINT `fk_doc_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='文档版本表 — 记录导出文档（Excel/XMind/报告）的历史版本';

-- ============================================================================
-- 第6域：AI 健康度 (AI Health Metrics)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `ai_health_metrics` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `project`         VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `version_id`      BIGINT        NOT NULL DEFAULT 0 COMMENT '版本ID → project_versions.id，0=未指定/legacy',
    `run_id`          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '流水线运行ID（关联 pipeline_checkpoints.run_id）',
    `step`            INT           NOT NULL DEFAULT 0 COMMENT '流水线阶段编号（1=需求拆解, 2=覆盖率, 3=用例建模, 4=执行验证, 5=报告）',
    `metric_type`     VARCHAR(64)   NOT NULL COMMENT '指标大类: coverage / cross_check / deep_check / rule_check / guard_reject / action_attempt / verification / flaky / cove / op_cache 等',
    `metric_name`     VARCHAR(128)  NOT NULL COMMENT '具体指标名（如 api_mismatch / orphan_modules / guard_rejection / cache_hit 等）',
    `category`        VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '子分类（如 api/ui/enum/ai/action/reverse/forward/3rail 等）',
    `value`           DOUBLE        NOT NULL DEFAULT 0 COMMENT '指标数值（计数、比率或布尔值 0/1）',
    `total`           DOUBLE        NOT NULL DEFAULT 0 COMMENT '分母/总量（用于在查询时计算比率，纯计数时为 0）',
    `details`         JSON          NULL COMMENT '扩展明细（JSON 对象，如 step_desc / 三轨结果等）',
    `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_health_project` (`project`, `metric_type`),
    INDEX `idx_health_run` (`run_id`),
    INDEX `idx_health_version` (`version_id`),
    INDEX `idx_health_proj_ver` (`project`, `version_id`, `metric_type`),
    INDEX `idx_health_created` (`created_at`),
    INDEX `idx_health_run_full` (`project`, `run_id`, `step`, `metric_type`, `metric_name`),
    CONSTRAINT `fk_health_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 健康度指标表 — 流水线埋点收集的覆盖率/交叉校验/守卫拦截/三轨校验/Flaky/CoVe/缓存等运营指标';

-- ============================================================================
-- 第7域：Figma 相关 (Figma Cache & Quota & Sessions & Node Hashes)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `figma_mcp_cache` (
    `cache_key`     VARCHAR(256)  NOT NULL COMMENT '缓存键（project:version:file_key:node_id:depth 格式）',
    `project`       VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '所属项目名称；Figma 缓存按项目隔离',
    `version_label` VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '所属项目版本；为空表示未绑定具体版本',
    `file_key`      VARCHAR(128)  NOT NULL COMMENT 'Figma 文件 KEY',
    `node_id`       VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '节点 ID',
    `depth`         INT           NOT NULL DEFAULT 0  COMMENT '递归深度',
    `response_json` MEDIUMTEXT    NOT NULL COMMENT 'Figma API/MCP 响应 JSON（可达数MB）',
    `created_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`cache_key`),
    INDEX `idx_fmc_project_version` (`project`, `version_label`, `file_key`),
    INDEX `idx_fmc_file` (`file_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma MCP 项目/版本级缓存表 — 缓存当前项目版本的 Figma API/MCP 原始响应';

-- ★ v3: 对齐 figma_state_store.py 实际读写结构（每月配额 + Unix时间戳）
CREATE TABLE IF NOT EXISTS `figma_api_quota` (
    `provider`         VARCHAR(32)  NOT NULL DEFAULT 'figma' COMMENT '服务提供方（默认 figma）',
    `credential_scope` VARCHAR(64)  NOT NULL DEFAULT 'token' COMMENT '凭证范围类型（token/user/global/legacy_project）',
    `credential_hash`  VARCHAR(64)  NOT NULL DEFAULT 'default' COMMENT '凭证哈希，不保存明文 Token',
    `current_month`    VARCHAR(7)   NOT NULL COMMENT '当前月份（YYYY-MM）',
    `total_used`       INT          NOT NULL DEFAULT 0  COMMENT '当月已用请求数',
    `rate_limit_until` DOUBLE       NOT NULL DEFAULT 0  COMMENT '限流结束时间戳（Unix秒）',
    `last_call_at`     DOUBLE       NOT NULL DEFAULT 0  COMMENT '最后调用时间戳（Unix秒）',
    `last_error`       LONGTEXT     NULL     COMMENT '最后错误信息',
    `updated_at`       DOUBLE       NOT NULL COMMENT '更新时间戳（Unix秒）',
    PRIMARY KEY (`provider`, `credential_scope`, `credential_hash`, `current_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma API 配额表 — 按凭证追踪Figma API调用配额，防止触发429限流';

-- ★ v3: 对齐 figma_state_store.py 实际读写结构（session_json 文本存储 + Unix时间戳）
CREATE TABLE IF NOT EXISTS `figma_change_sessions` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL COMMENT '版本标签',
    `session_id`    VARCHAR(255) NOT NULL COMMENT '会话ID',
    `session_json`  LONGTEXT     NOT NULL COMMENT '会话完整JSON',
    `created_at`    DOUBLE       NOT NULL COMMENT '创建时间戳（Unix秒）',
    PRIMARY KEY (`project`, `version_label`, `session_id`),
    CONSTRAINT `fk_figma_session_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 变更会话表 — 记录每次Figma设计稿变更检测会话';

-- ★ v3: figma_node_hashes（原由 figma_state_store.py 自管，现统一到 schema.sql）
CREATE TABLE IF NOT EXISTS `figma_node_hashes` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL COMMENT '版本标签',
    `node_id`       VARCHAR(255) NOT NULL COMMENT 'Figma 节点ID',
    `hash_value`    VARCHAR(255) NOT NULL COMMENT '节点内容MD5哈希',
    `updated_at`    DOUBLE       NOT NULL COMMENT '更新时间戳（Unix秒）',
    PRIMARY KEY (`project`, `version_label`, `node_id`),
    CONSTRAINT `fk_figma_nh_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 节点哈希表 — 节点级MD5哈希字典，用于变更检测锚点';

-- ★ v3: figma_images — Figma MCP download_figma_images 图片 LONGBLOB 存储（全走MySQL）
-- 链路：image → node_id+page_id → trace_links.figma_pages_json → req_id
CREATE TABLE IF NOT EXISTS `figma_images` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '图片自增ID',
    `project`       VARCHAR(128)    NOT NULL DEFAULT ''    COMMENT '所属项目 → projects.name',
    `version_label` VARCHAR(128)    NOT NULL DEFAULT ''    COMMENT '版本标签',
    `file_key`      VARCHAR(128)    NOT NULL               COMMENT 'Figma 文件 KEY',
    `page_id`       VARCHAR(255)    NOT NULL DEFAULT ''    COMMENT '所属 Figma 页面/FRAME 节点ID',
    `page_name`     VARCHAR(255)    NOT NULL DEFAULT ''    COMMENT '所属 Figma 页面/FRAME 名称（与 trace_links.figma_pages_json 对齐）',
    `node_id`       VARCHAR(255)    NOT NULL DEFAULT ''    COMMENT 'Figma 节点ID（I格式）',
    `file_name`     VARCHAR(255)    NOT NULL               COMMENT '原始文件名（含扩展名）',
    `image_data`    LONGBLOB        NOT NULL               COMMENT '图片二进制数据（PNG/SVG/GIF）',
    `image_type`    VARCHAR(10)     NOT NULL DEFAULT 'png' COMMENT '图片类型扩展名（png/svg/gif）',
    `width`         INT             NULL     DEFAULT NULL  COMMENT '图片宽度（像素，SVG可能为空）',
    `height`        INT             NULL     DEFAULT NULL  COMMENT '图片高度（像素，SVG可能为空）',
    `image_size`    INT             NOT NULL DEFAULT 0     COMMENT '文件大小（字节）',
    `created_at`    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_fi_project_version` (`project`, `version_label`, `file_key`),
    INDEX `idx_fi_node` (`file_key`, `node_id`),
    INDEX `idx_fi_page` (`file_key`, `page_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 图片存储表 — Figma MCP download_figma_images 下载的图片全量转存MySQL，不落磁盘';

-- ============================================================================
-- 第8域：流水线检查点 (Pipeline Checkpoints)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `pipeline_checkpoints` (
    `run_id`       VARCHAR(64)  NOT NULL COMMENT '运行ID',
    `project`      VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`   BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `step`         INT          NOT NULL COMMENT '阶段编号(1-5)',
    `status`       VARCHAR(32)  NOT NULL COMMENT '状态: running/success/failed',
    `started_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
    `completed_at` DATETIME     DEFAULT NULL COMMENT '完成时间',
    `error_msg`    TEXT         COMMENT '失败原因',
    `integrity`    VARCHAR(32)  DEFAULT NULL COMMENT '完整性标记（兼容旧 checkpoint_store 表）',
    PRIMARY KEY (`run_id`, `step`),
    INDEX `idx_checkpoint_run` (`run_id`),
    INDEX `idx_checkpoint_project` (`project`, `started_at`),
    CONSTRAINT `fk_checkpoint_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='流水线检查点表';

-- ============================================================================
-- 第9域：原始文档内容 (Source Documents) — 取代磁盘md/json
-- ============================================================================

CREATE TABLE IF NOT EXISTS `source_documents` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `doc_type`      VARCHAR(64)  NOT NULL COMMENT '文档类型: confluence / yapi / yapi_structured / xmind / deployment / requirement / upload_*（report/coverage_report/html_report 已迁移到 generated_reports）',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `content`       LONGTEXT     NOT NULL COMMENT '文档原始内容（可能很大）',
    `content_hash`  VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '内容MD5哈希',
    `char_count`    INT          NOT NULL DEFAULT 0 COMMENT '字符数',
    `filename`      VARCHAR(255) NOT NULL DEFAULT '' COMMENT '原始文件名（仅上传文档使用）',
    `file_format`   VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '文件格式: xlsx/csv/docx/md/sql/json/xmind/txt',
    `weight`        DECIMAL(3,2) NOT NULL DEFAULT 0.50 COMMENT '权重 0-1（仅上传文档使用）',
    `category`      VARCHAR(50)  NOT NULL DEFAULT 'requirement' COMMENT '分类: requirement/data_dict/api_supplement/design_supplement/test_data/ddl_schema',
    `source_url`    VARCHAR(512) NOT NULL DEFAULT '' COMMENT '原始来源 URL（Confluence/YApi/Figma 等远程数据源）',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (`project`, `doc_type`, `version_label`),
    INDEX `idx_sdoc_hash` (`content_hash`),
    INDEX `idx_sdoc_type` (`doc_type`),
    CONSTRAINT `fk_sdoc_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE,
    CONSTRAINT `fk_sdoc_version_label` FOREIGN KEY (`project`, `version_label`) REFERENCES `project_versions` (`project`, `version_label`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='统一文档内容表（含上传文件元数据）';

-- ★ 07-08 添加 source_url 字段（存储 Confluence/YApi/Figma 等远程数据源的原始 URL）
ALTER TABLE source_documents ADD COLUMN source_url VARCHAR(512) NOT NULL DEFAULT '' COMMENT '原始来源 URL（Confluence/YApi/Figma 等远程数据源）';

-- ============================================================================
-- 第9.1域：流水线生成报告 (Generated Reports) — Step3/Step5 输出，与 source_documents 分离
-- ============================================================================

CREATE TABLE IF NOT EXISTS `generated_reports` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '报告记录ID',
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `run_id`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '运行批次ID（coverage_report/html_report/report 均关联）',
    `report_type`   VARCHAR(64)  NOT NULL COMMENT '报告类型: report / html_report / coverage_report',
    `content`       LONGTEXT     NOT NULL COMMENT '报告内容（Markdown/HTML/覆盖率文本）',
    `content_hash`  VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '内容MD5哈希',
    `char_count`    INT          NOT NULL DEFAULT 0 COMMENT '字符数',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    UNIQUE KEY `uk_generated_reports` (`project`, `version_id`, `run_id`, `report_type`),
    INDEX `idx_gen_report_project_type` (`project`, `report_type`),
    INDEX `idx_gen_report_run` (`project`, `run_id`),
    INDEX `idx_gen_report_hash` (`content_hash`),
    CONSTRAINT `fk_gen_report_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='流水线生成报告表（与 source_documents 分离，避免源文档与生成物混用）';

-- ============================================================================
-- 第9.5/9.6域（已废弃并删除）：page_dom_snapshots / system_nav_maps
-- ----------------------------------------------------------------------------
-- ★ P0-1 死表清理(2026-08-05):
--   两表已 DROP。清理前实测：0 行、全仓无任何写入方、运行时零读取、无入向外键。
--   其职责已由第42域 nav_graph_nodes / nav_graph_edges 承接（Step1 真实爬取产物）。
--   读取统一走 src/pipeline/utils/nav_graph_read.py 适配层。
--   备份（DDL+数据）: data/backups/dropped_nav_tables_<timestamp>.json
--   注：原 DDL 声明的 `system_nav_maps.nav_json` 与线上实际列名 `nav_map_json`
--   长期不一致，是历史上 `SELECT nav_json` 恒抛 Unknown column 的直接原因。
-- ============================================================================

-- ============================================================================
-- 第9.7域：系统功能清单 (System Feature List)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `system_feature_list` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '功能清单ID',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `page_url`        VARCHAR(512) NOT NULL COMMENT '页面完整URL',
    `page_path`       VARCHAR(256) NOT NULL DEFAULT '' COMMENT '页面路径',
    `page_title`      VARCHAR(256) NOT NULL DEFAULT '' COMMENT '页面标题',
    `features_json`   LONGTEXT     NOT NULL COMMENT '功能列表JSON（20类元素：button/input/chart/dropdown/tab/modal/upload等）',
    `statistics_json` TEXT         NULL COMMENT '统计信息JSON',
    `source`          VARCHAR(32)  NOT NULL DEFAULT 'dom' COMMENT '功能来源：dom(目标系统运行时) / figma(设计稿) / requirement(需求文档) / yapi(接口契约)',
    `source_status`   VARCHAR(32)  NOT NULL DEFAULT 'implemented' COMMENT '功能状态：implemented(dom有) / designed(figma有dom无) / planned(需求有figma无) / api_only(yapi有ui无)',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    INDEX `idx_feature_project` (`project`, `version_label`),
    INDEX `idx_feature_path` (`page_path`),
    INDEX `idx_feature_source` (`source`, `source_status`),
    CONSTRAINT `fk_feature_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE,
    CONSTRAINT `fk_feature_version_label` FOREIGN KEY (`project`, `version_label`) REFERENCES `project_versions` (`project`, `version_label`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='目标系统功能清单表（多源融合：DOM/Figma/需求/YApi）';

-- ============================================================================
-- 第10域：截图存储 (Execution Screenshots) — 取代磁盘png，保留6个月
-- ============================================================================

CREATE TABLE IF NOT EXISTS `execution_screenshots` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '截图ID',
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `case_id`       VARCHAR(64)  NOT NULL COMMENT '测试用例编号',
    `run_id`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '运行ID',
    `step_num`      INT          NOT NULL DEFAULT 0 COMMENT '截图序号/动作序号',
    `image_data`    MEDIUMBLOB   NOT NULL COMMENT '截图二进制数据（PNG）',
    `file_size`     INT          NOT NULL DEFAULT 0 COMMENT '文件大小（字节）',
    `status`        VARCHAR(16)  DEFAULT NULL COMMENT '用例终态 PASS/FAIL/BLOCKED',
    `version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_ss_intent` (`project`, `case_id`),
    INDEX `idx_ss_version` (`project`, `version_id`),
    INDEX `idx_ss_run` (`run_id`),
    INDEX `idx_ss_created` (`created_at`),
    CONSTRAINT `fk_ss_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='截图存储表 — 存储 Step4 执行失败的截图（取代磁盘PNG，自动保留6个月后清理）';

-- ============================================================================
-- 第10.1域：视觉基线 (Visual Baselines) — Step4 视觉回归对比基线（取代磁盘 PNG）
-- ============================================================================
CREATE TABLE IF NOT EXISTS `visual_baselines` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `baseline_key`  VARCHAR(256) NOT NULL COMMENT '基线键 {project}_{case_id}_step{n}',
    `image_data`    MEDIUMBLOB   NOT NULL COMMENT '基线截图二进制（PNG）',
    `image_hash`    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '基线截图 MD5（快速比对路径）',
    `diff_data`     MEDIUMBLOB   NULL     COMMENT '差异高亮图二进制（mismatch 时生成）',
    `version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '基线建立时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近对比时间',
    PRIMARY KEY (`project`, `baseline_key`),
    INDEX `idx_vb_hash` (`image_hash`),
    CONSTRAINT `fk_vb_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='视觉基线表 — Step4 视觉回归对比基线截图（取代磁盘 visual_baselines/*.png）';

-- ============================================================================
-- 第10.2域：视觉自愈缓存 (Vision Heal Cache) — 取代磁盘 vision_healing_cache/*.json
-- ============================================================================
CREATE TABLE IF NOT EXISTS `vision_heal_cache` (
    `cache_key`     VARCHAR(64)  NOT NULL COMMENT '缓存键 MD5(ai_id:target_hint)',
    `project`       VARCHAR(128) NOT NULL DEFAULT '' COMMENT '项目名 → projects.name',
    `version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=跨版本复用',
    `ai_id`         VARCHAR(256) NOT NULL DEFAULT '' COMMENT '动作AI标识',
    `original_hint` TEXT         NOT NULL COMMENT '原定位提示',
    `new_hint`      TEXT         NOT NULL COMMENT '自愈后新定位提示',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    PRIMARY KEY (`cache_key`),
    INDEX `idx_vhc_project` (`project`),
    INDEX `idx_vhc_ai` (`ai_id`(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='视觉自愈缓存 — 成功的元素修复记录（取代磁盘JSON，跨session复用）';

-- ============================================================================
-- 第10.3域：Step4 自愈补丁持久化 (Self Heal Patches)
-- ============================================================================
CREATE TABLE IF NOT EXISTS `self_heal_patches` (
    `id`            BIGINT        NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `project`       VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '项目名 → projects.name',
    `version_id`    BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id',
    `case_id`       VARCHAR(256)  NOT NULL DEFAULT '' COMMENT '测试用例ID → case_meta.case_id',
    `original_hint` TEXT          NULL     COMMENT '原始定位器（修复前）',
    `healed_hint`   TEXT          NULL     COMMENT '修复后定位器',
    `strategy`      VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '自愈策略名称',
    `verified`      TINYINT       NOT NULL DEFAULT 0 COMMENT '是否已验证（0=待验证 1=验证通过 2=验证失败）',
    `verify_at`     DATETIME      NULL     COMMENT '验证时间',
    `verify_detail` TEXT          NULL     COMMENT '验证详情（错误信息等）',
    `page_url`      VARCHAR(1024) NULL     DEFAULT '' COMMENT '当前页面URL',
    `created_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '补丁创建时间',
    `updated_at`    DATETIME      NULL     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '补丁更新时间',
    PRIMARY KEY (`id`),
    INDEX `idx_shp_project` (`project`),
    INDEX `idx_shp_intent` (`case_id`(64)),
    INDEX `idx_shp_project_verified` (`project`, `verified`),
    INDEX `idx_shp_case_key` (`project`, `version_id`, `case_id`(64)),
    CONSTRAINT `fk_shp_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='自愈补丁持久化表 — 记录成功修复的定位器补丁，验证通过后跨运行复用';

-- ============================================================================
-- 第11域：Step4 操作指令语义缓存 (Op Cache) — 取代 op_cache_store
-- ============================================================================

-- ★ v3: 对齐 op_cache.py 实际使用的 step4_op_cache 表结构（原 op_cache_store 为死表，已删除）
CREATE TABLE IF NOT EXISTS `step4_op_cache` (
    `cache_key`       VARCHAR(64)  NOT NULL COMMENT '缓存键（MD5哈希）',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`      BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=跨版本复用',
    `goal_normalized` TEXT         NOT NULL COMMENT '归一化的操作目标',
    `page_path`       TEXT         NOT NULL COMMENT '页面路径（netloc + path）',
    `dom_fingerprint` TEXT         NOT NULL COMMENT 'DOM结构指纹',
    `config_hash`     VARCHAR(16)  NOT NULL COMMENT '配置哈希',
    `actions_json`    LONGTEXT     NOT NULL COMMENT 'AI返回的操作指令JSON',
    `hit_count`       INT          NOT NULL DEFAULT 0 COMMENT '命中次数',
    `success_count`   INT          NOT NULL DEFAULT 0 COMMENT '成功执行次数',
    `created_at`      DOUBLE       NOT NULL COMMENT '创建时间戳（Unix秒）',
    `last_hit_at`     DOUBLE       NULL COMMENT '最后命中时间戳（Unix秒）',
    PRIMARY KEY (`cache_key`),
    INDEX `idx_opcache_project` (`project`),
    INDEX `idx_opcache_config` (`config_hash`),
    CONSTRAINT `fk_opcache_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step4 操作指令语义缓存表 — 基于页面DOM指纹+步骤目标的语义级缓存';

-- ============================================================================
-- 第12域：RAG 文档分块 (RAG Chunks) — 取代 rag_index.db
-- ============================================================================

CREATE TABLE IF NOT EXISTS `rag_chunks` (
    `scope_type`    VARCHAR(32)  NOT NULL DEFAULT 'project' COMMENT '知识范围类型（global/project）',
    `scope_id`      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '范围ID（global为空，project为项目名）',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '项目版本标签（审计：最后构建版本）',
    `chunk_id`      VARCHAR(256) NOT NULL COMMENT '分块唯一ID（稳定键：api:POST:/path / db:table / figma:page / req:stable_key）',
    `type`          VARCHAR(32)  NOT NULL COMMENT '分块类型：api / db_table / figma_page / requirement / upload_*',
    `text`          MEDIUMTEXT   NOT NULL COMMENT '分块文本内容',
    `meta`          JSON         NULL     COMMENT '元数据（JSON：endpoint/table/page信息）',
    `keywords`      JSON         NULL     COMMENT '关键词数组（用于 BM25 关键词匹配）',
    `vector_blob`   MEDIUMBLOB   NULL     COMMENT '向量嵌入二进制（float32 紧凑存储）',
    `vector_json`   MEDIUMTEXT   NULL     COMMENT '向量嵌入 JSON（便于调试/人工读取）',
    `vector_norm`   DOUBLE       NULL     COMMENT '向量 L2 范数（=1 表示已归一化，可用点积）',
    `embed_dim`     INT          NOT NULL DEFAULT 0 COMMENT '向量维度（0=未构建向量）',
    `embed_model`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '生成向量的嵌入模型名称（如 nomic-embed-text）',
    `chapter_hash`  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '段落内容哈希（MD5前16位，增量重建时比对：相同则保留旧向量）',
    `is_active`     TINYINT      NOT NULL DEFAULT 1 COMMENT '是否有效（0=已被新版本取代，1=当前有效）',
    `source_version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '最后构建版本标签（审计）',
    `deactivated_version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '失效版本标签（审计）',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `user_id` varchar(64) NOT NULL DEFAULT '' COMMENT '用户ID（空=历史数据/共享）',
    `slice_id` varchar(128) NOT NULL DEFAULT '' COMMENT '来源切片ID → requirement_slices.slice_id',
    PRIMARY KEY (`scope_type`, `scope_id`, `version_label`, `chunk_id`),
    INDEX `idx_rag_scope_type` (`scope_type`, `scope_id`, `type`),
    INDEX `idx_rag_project_active` (`scope_type`, `scope_id`, `is_active`, `type`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='RAG 项目级文档分块表 — 项目级累积，按稳定 chunk_id upsert，is_active 软删除';

-- ============================================================================
-- [墓碑] semantic_index — 已移除：冗余，向量存储由 rag_chunks 统一承载
-- ============================================================================

-- ============================================================================
-- 第13域：流水线运行日志 (Run Logs) — 取代磁盘 step_*.log
-- ============================================================================

CREATE TABLE IF NOT EXISTS `run_logs` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `run_id`      VARCHAR(64)   NOT NULL COMMENT '运行ID（关联 pipeline_checkpoints.run_id）',
    `project`     VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `version_id`  INT           NOT NULL DEFAULT 0 COMMENT '版本ID → project_versions.id（★ 2026-08-14 补版本隔离）',
    `step_num`    INT           NOT NULL DEFAULT 0 COMMENT '流水线阶段 (0=全流程)',
    `log_level`   VARCHAR(16)   NOT NULL DEFAULT 'INFO' COMMENT '日志级别：DEBUG/INFO/WARNING/ERROR',
    `message`     MEDIUMTEXT    NOT NULL COMMENT '日志内容',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_run_logs_run` (`run_id`),
    INDEX `idx_run_logs_project` (`project`, `created_at`),
    INDEX `idx_run_logs_step` (`project`, `step_num`),
    INDEX `idx_run_logs_version` (`project`, `version_id`),
    CONSTRAINT `fk_run_logs_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='流水线运行日志表 — 存储每次执行的详细日志（取代磁盘 *.log 文件）';

-- ============================================================================
-- [墓碑] db_schema_catalog — 已移除：无任何代码读写此表
-- ============================================================================

-- ============================================================================
-- [墓碑] coverage_matrix — 已移除：无任何代码 INSERT/SELECT/UPDATE，
--       覆盖率数据由 coverage_results + coverage_summaries 表承载
-- ============================================================================

-- ============================================================================
-- 第16域：QA 反馈 (QA Feedback) — 取代 .ai_test_engine/qa_feedback.jsonl
-- ============================================================================

CREATE TABLE IF NOT EXISTS `qa_feedback` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `scope_type`   VARCHAR(32)  NOT NULL DEFAULT 'project' COMMENT '反馈范围类型（project/global）',
    `scope_id`     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '范围ID（project为项目名，global为空）',
    `case_id`      VARCHAR(64)  NOT NULL COMMENT '测试用例编号',
    `qa_decision`  VARCHAR(32)  NOT NULL COMMENT 'QA 判定: correct / false_positive / false_negative / needs_review',
    `qa_notes`     TEXT         NOT NULL COMMENT 'QA 备注',
    `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_qa_scope` (`scope_type`, `scope_id`, `case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='QA 反馈表 — 记录项目反馈和全局反馈模式（取代磁盘 JSONL）';

-- ============================================================================
-- 第17域：测试规则 (Test Rules) — 取代 test_rules.json
-- ============================================================================

CREATE TABLE IF NOT EXISTS `test_rules` (
    `scope_type`  VARCHAR(32)  NOT NULL DEFAULT 'global' COMMENT '规则范围类型（global/project）',
    `scope_id`    VARCHAR(128) NOT NULL DEFAULT '' COMMENT '范围ID（global为空，project为项目名）',
    `rules_json`  JSON         NOT NULL COMMENT '规则定义 JSON（patterns + global_constraints）',
    `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`scope_type`, `scope_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试规则表 — 存储全局默认规则和项目覆盖规则；自动发现规则每项目最多5000条';

-- ============================================================================
-- 第18域：需求列表 (Requirements) — 原由 requirement_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `requirements` (
    `req_id`            VARCHAR(128) NOT NULL COMMENT '需求编号',
    `project`           VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`        BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `stable_req_key`    VARCHAR(255) NOT NULL DEFAULT '' COMMENT '跨版本稳定需求键',
    `source_anchor`     VARCHAR(512) NOT NULL DEFAULT '' COMMENT '源文档锚点（如 Confluence 页面标题层级路径）',
    `content_hash`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '规范化文本哈希',
    `semantic_hash`     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '语义哈希',
    `change_type`       VARCHAR(32)  NOT NULL DEFAULT 'unknown' COMMENT 'added/modified/unchanged/removed/impacted/unknown',
    `origin_req_id`     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源版本需求编号',
    `origin_version_id` BIGINT       NOT NULL DEFAULT 0 COMMENT '来源项目版本ID，0=无',
    `feature_id`        BIGINT       NOT NULL DEFAULT 0 COMMENT '关联功能清单ID → system_feature_list.id',
    `module`            VARCHAR(128) NOT NULL COMMENT '功能模块',
    `description`       TEXT         NOT NULL COMMENT '需求描述',
    `operation`         TEXT         NOT NULL COMMENT '操作描述（三元组：用户执行了什么操作）',
    `condition_expr`    TEXT         NOT NULL COMMENT '条件描述（三元组：在什么条件下触发）',
    `expected_feedback` TEXT         NOT NULL COMMENT '可观察反馈（三元组：系统如何响应）',
    `is_implicit`       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否隐含需求（需求文档未明确描述，标记待确认）',
    `is_generated`      TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已建模测试用例',
    `dependency_req_id` VARCHAR(255) NOT NULL COMMENT '依赖需求编号',
    `review_status`     VARCHAR(32)  NOT NULL COMMENT '审核状态',
    `payload_json`      JSON         NOT NULL COMMENT '完整需求原始JSON',
    `contains_sensitive_data` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '★ S2-018 是否包含敏感信息（身份证/手机号/邮箱/IP）',
    `created_at`        DATETIME     NOT NULL COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL COMMENT '更新时间',
    `slice_id` varchar(128) NOT NULL DEFAULT '' COMMENT '来源切片ID → requirement_slices.slice_id',
    `source_quote` text NOT NULL COMMENT '需求原文引用（防幻觉5a子串匹配事实源，必须来自所属切片真实文本）',
    `_verify_status` varchar(32) NOT NULL DEFAULT '' COMMENT 'source_verify 校验状态: passed/passed_corrected/partial_match/unverified/blocked/no_quote/skipped_implicit/empty_quote',
    `_evidence_confidence` double NOT NULL DEFAULT '0' COMMENT '证据置信度评分 0.0~1.0',
    PRIMARY KEY (`project`, `version_id`, `req_id`),
    INDEX `idx_req_version` (`project`, `version_id`),
    INDEX `idx_req_stable` (`project`, `stable_req_key`),
    INDEX `idx_req_change` (`project`, `version_id`, `change_type`),
    INDEX `idx_req_project` (`project`),
    INDEX `idx_req_feature` (`feature_id`),
    CONSTRAINT `fk_req_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='需求列表表 — 功能清单驱动拆解，存储原子需求三元组';

-- ============================================================================
-- 第19域：测试用例 (Test Cases) — 原由 validation_intent_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `test_cases` (
    `case_id`         VARCHAR(128) NOT NULL COMMENT '测试用例编号',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`      BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `origin_case_id`  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源版本测试用例编号',
    `origin_version_id` BIGINT     NOT NULL DEFAULT 0 COMMENT '来源项目版本ID，0=无',
    `case_source`     VARCHAR(32)  NOT NULL DEFAULT 'generated' COMMENT 'generated/inherited/regenerated/manually_added',
    `valid_status`    VARCHAR(32)  NOT NULL DEFAULT 'valid' COMMENT 'valid/stale/retired',
    `req_id`          VARCHAR(128) NOT NULL COMMENT '关联需求编号',
    `case_asset_file`       VARCHAR(256) NOT NULL COMMENT '来源文件名',
    `title`           TEXT         NOT NULL COMMENT '测试用例标题',
    `case_type`       VARCHAR(32)  NOT NULL COMMENT '测试用例类型',
    `risk_level`        VARCHAR(32)  NOT NULL COMMENT '风险等级',
    `case_actions_json`      TEXT         NOT NULL COMMENT '用例动作JSON（v3.0: 从 actions[] 序列化）；原始用例见payload_json（actions + checks）',
    `expected_outcome` TEXT         NOT NULL COMMENT '预期结果（从 checks[].expect 提取）',
    `test_method`     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '★ 测试方法论编码(07-05h): EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI，与scenario_type正交',
    `control_type`    VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '★ 控件类型(07-05h): input/select/button/upload/display/nav/interactive/composite',
    `priority`        VARCHAR(32)  NOT NULL DEFAULT 'medium' COMMENT '★ 风险驱动优先级(08-16h): critical/high/medium/low，由 derive_priority 推导',
    `payload_json`    JSON         NOT NULL COMMENT '完整测试用例原始JSON',
    `created_at`      DATETIME     NOT NULL COMMENT '创建时间',
    `updated_at`      DATETIME     NOT NULL COMMENT '更新时间',
    `precondition` text NULL COMMENT '★ 前置条件(2026-08-09 IEEE 829): 编号列表，如"1.系统已登录 2.测试数据已准备"',
    `test_steps` text NULL COMMENT '★ 测试步骤(2026-08-09 IEEE 829): JSON数组，描述操作步骤序列',
    `expected_result` text NULL COMMENT '★ 预期结果(2026-08-09 IEEE 829): 精确描述操作后的预期状态',
    PRIMARY KEY (`project`, `version_id`, `case_id`),
    INDEX `idx_case_version` (`project`, `version_id`),
    INDEX `idx_case_origin` (`project`, `origin_version_id`, `origin_case_id`),
    INDEX `idx_case_valid` (`project`, `version_id`, `valid_status`),
    INDEX `idx_case_project` (`project`),
    INDEX `idx_case_req` (`project`, `req_id`),
    CONSTRAINT `fk_case_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试用例表 — v3.0 用例驱动，核心数据见payload_json（actions + checks）';

CREATE TABLE IF NOT EXISTS `case_requirement_mapping` (
    `case_id`         VARCHAR(128) NOT NULL COMMENT '测试用例ID',
    `req_id`            VARCHAR(128) NOT NULL COMMENT '需求ID',
    `project`           VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`        BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID',
    `req_version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '需求版本ID',
    `case_version_id` BIGINT       NOT NULL DEFAULT 0 COMMENT '测试用例版本ID',
    `mapping_source`    VARCHAR(32)  NOT NULL DEFAULT 'generated' COMMENT '映射来源',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `version_id`, `case_id`),
    INDEX `idx_case_req_map_req` (`project`, `req_id`),
    INDEX `idx_case_req_map_req_version` (`project`, `req_version_id`),
    CONSTRAINT `fk_case_req_map_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试用例-需求映射表';

-- ============================================================================
-- 第20域：审核日志 (Review Log) — 原由 review_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `review_log` (
    `id`         BIGINT       NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `project`    VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id` BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `item_type`  VARCHAR(255) NOT NULL COMMENT '审核对象类型（case / requirement）',
    `item_id`    VARCHAR(255) NOT NULL COMMENT '审核对象ID',
    `action`     VARCHAR(255) NOT NULL COMMENT '审核动作（approve / reject / modify）',
    `reviewer`   VARCHAR(255) NOT NULL COMMENT '审核人用户ID',
    `comment`    TEXT         NOT NULL COMMENT '审核备注',
    `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_review_log_prj` (`project`, `item_type`),
    CONSTRAINT `fk_review_log_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审核操作日志表 — 记录谁在什么时间对什么做了什么审核动作';

-- ============================================================================
-- 第21域：源数据哈希 (Source Hashes) — 原由 source_hashes_store.py 自管
-- ============================================================================

-- ★ v3: step1.py/step2.py 使用的统一哈希表（按 doc_type 区分数据源）
CREATE TABLE IF NOT EXISTS `source_hashes` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `doc_type`      VARCHAR(32)  NOT NULL COMMENT '文档类型: confluence / yapi / breakdown',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签',
    `content_hash`  VARCHAR(64)  NOT NULL COMMENT '内容MD5哈希',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `doc_type`, `version_label`),
    CONSTRAINT `fk_sh_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='源数据统一哈希表 — Step1/Step2 各数据源的总体内容哈希（断点续跑锚点）';

-- 以下三表由 source_hashes_store.py 管理，存储更细粒度的 per-page 哈希

CREATE TABLE IF NOT EXISTS `source_confluence_hashes` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL COMMENT '版本标签',
    `hash_key`      VARCHAR(255) NOT NULL COMMENT '哈希键（__overall__ 或 page:<page_id>）',
    `hash_value`    VARCHAR(255) NOT NULL COMMENT '哈希值',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `version_label`, `hash_key`),
    CONSTRAINT `fk_sch_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Confluence 源数据哈希表 — Step1断点续跑用';

CREATE TABLE IF NOT EXISTS `source_yapi_hashes` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL COMMENT '版本标签',
    `overall_hash`  TEXT         NOT NULL COMMENT '整体哈希值',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `version_label`),
    CONSTRAINT `fk_syh_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='YApi 源数据哈希表 — Step1断点续跑用';

CREATE TABLE IF NOT EXISTS `source_breakdown_hashes` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL COMMENT '版本标签',
    `hash_key`      VARCHAR(255) NOT NULL COMMENT '哈希键（__overall__ 或 page:<page_id>）',
    `hash_value`    VARCHAR(255) NOT NULL COMMENT '哈希值',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `meta_json`     TEXT         NULL COMMENT '元数据JSON',
    PRIMARY KEY (`project`, `version_label`, `hash_key`),
    CONSTRAINT `fk_sbh_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step2 需求拆解哈希表 — 断点续跑用';

-- ============================================================================
-- 第22域：覆盖率结果 & 汇总 (Coverage Results & Summaries) — 原由 coverage_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `coverage_results` (
    `project`           VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`        BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `run_id`            VARCHAR(64)  NOT NULL COMMENT '运行批次ID',
    `req_id`            VARCHAR(128) NOT NULL COMMENT '需求编号',
    `dim_positive`      VARCHAR(32)  NOT NULL DEFAULT 'missing' COMMENT '正向覆盖状态',
    `dim_negative`      VARCHAR(32)  NOT NULL DEFAULT 'missing' COMMENT '异常覆盖状态',
    `dim_boundary`      VARCHAR(32)  NOT NULL DEFAULT 'missing' COMMENT '边界值覆盖状态',
    `dim_security`      VARCHAR(32)  NOT NULL DEFAULT 'missing' COMMENT '安全覆盖状态',
    `dim_api_check`     VARCHAR(32)  NOT NULL DEFAULT 'missing' COMMENT 'API轨校验覆盖状态',
    `dim_api_coverage`  VARCHAR(32)  NOT NULL DEFAULT 'n/a' COMMENT 'API接口覆盖状态',
    `dim_db_coverage`   VARCHAR(32)  NOT NULL DEFAULT 'n/a' COMMENT 'DB表覆盖状态',
    `dim_page_coverage` VARCHAR(32)  NOT NULL DEFAULT 'n/a' COMMENT '页面覆盖状态',
    `dim_trace_complete` VARCHAR(32) NOT NULL DEFAULT 'n/a' COMMENT '追溯链路完整性',
    `total_cases`       INT          NOT NULL DEFAULT 0 COMMENT '该需求关联测试用例数',
    `has_gaps`          INT          NOT NULL DEFAULT 0 COMMENT '是否存在缺口(0/1)',
    `gap_dims_json`     JSON         NOT NULL COMMENT '缺口维度列表JSON',
    `payload_json`      JSON         NOT NULL COMMENT '完整覆盖结果原始JSON',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `run_id`, `req_id`),
    INDEX `idx_cov_version` (`project`, `version_id`, `run_id`),
    INDEX `idx_cov_project` (`project`, `run_id`),
    CONSTRAINT `fk_cov_results_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='覆盖率结果表 — 每个需求在各维度的覆盖状态';

CREATE TABLE IF NOT EXISTS `coverage_summaries` (
    `project`               VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`            BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `run_id`                VARCHAR(255) NOT NULL COMMENT '运行批次ID',
    `total_requirements`    INT          NOT NULL DEFAULT 0 COMMENT '总需求数',
    `total_cases`           INT          NOT NULL DEFAULT 0 COMMENT '总测试用例数',
    `requirements_with_gaps` INT         NOT NULL DEFAULT 0 COMMENT '有缺口的需求数',
    `coverage_rate`         DOUBLE       NOT NULL DEFAULT 0.0 COMMENT '覆盖率(0.0-1.0)',
    `total_gaps`            INT          NOT NULL DEFAULT 0 COMMENT '总缺口数',
    `missing_by_dim_json`   JSON         NOT NULL COMMENT '按维度统计缺口JSON',
    `payload_json`          JSON         NOT NULL COMMENT '完整汇总原始JSON',
    `created_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`project`, `run_id`),
    CONSTRAINT `fk_cov_summaries_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='覆盖率汇总表 — 每次运行的覆盖率总体统计';

-- ============================================================================
-- ★ S3-012: RAG 使用日志 (rag_usage_log) — 记录 RAG 检索结果→用例字段映射
-- ============================================================================

CREATE TABLE IF NOT EXISTS `rag_usage_log` (
    `id`               INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `project`          VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`       BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `case_id`          VARCHAR(64)  NOT NULL COMMENT '用例 ID → test_cases.case_id',
    `check_id`         VARCHAR(64)  NOT NULL COMMENT '检查点 ID',
    `req_id`           VARCHAR(128) NOT NULL DEFAULT '' COMMENT '需求编号（用于 RAG 检索时建立映射）',
    `rag_source_type`  ENUM('db_schema','api_doc','figma_page') NOT NULL COMMENT 'RAG 来源类型',
    `rag_source_id`    VARCHAR(256) NOT NULL COMMENT 'RAG 来源标识（表名/API路径/页面ID）',
    `field_used`       VARCHAR(256) NOT NULL DEFAULT '' COMMENT '实际使用的字段（表名/API路径等）',
    `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_rag_case` (`case_id`),
    INDEX `idx_rag_source` (`rag_source_id`),
    INDEX `idx_rag_version` (`project`, `version_id`),
    CONSTRAINT `fk_rag_usage_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='RAG 使用日志 — 记录 RAG 检索结果到用例字段的映射';

-- ============================================================================
-- 第23域：差异快照 (Diff Snapshots) — 原由 diff_snapshot_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `diff_snapshots` (
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`   VARCHAR(128) NOT NULL COMMENT '版本标签',
    `snapshot_type`   VARCHAR(255) NOT NULL COMMENT '快照类型: requirements/api_schema/db_schema/test_cases/cross_dependencies',
    `payload_json`    LONGTEXT     NOT NULL COMMENT '快照内容JSON',
    `item_count`      INT          NOT NULL DEFAULT 0 COMMENT '元素个数',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`project`, `version_label`, `snapshot_type`),
    CONSTRAINT `fk_diff_snapshot_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='版本差异快照表 — 存储各版本的需求/API/DB/测试用例快照';

-- ============================================================================
-- 第24域：追溯链路 (Trace Links) — 原由 trace_link_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `trace_links` (
    `req_id`             VARCHAR(128) NOT NULL COMMENT '需求编号',
    `project`            VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`         BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `feature_id`         BIGINT       NOT NULL DEFAULT 0 COMMENT '关联功能清单ID → system_feature_list.id',
    `module`             VARCHAR(255) NOT NULL COMMENT '功能模块',
    `description`        LONGTEXT     NOT NULL COMMENT '需求描述',
    `related_reqs_json`  JSON         NOT NULL COMMENT '关联需求JSON',
    `confidence`         VARCHAR(32)  NOT NULL DEFAULT 'medium' COMMENT '置信度: high/medium/low',
    `payload_json`       JSON         NOT NULL COMMENT '链路元数据快照（不含API/DB/Figma资源详情）',
    `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`req_id`, `project`, `version_id`),
    INDEX `idx_trace_feature` (`feature_id`),
    CONSTRAINT `fk_trace_link_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='追溯链路表 — 需求级元数据（资源详情走 trace_link_chunks + rag_chunks）';

-- ============================================================================
-- ★ 新增(07-05h): 需求切片表 — 持久化智能切片结果，支持关系链路查看
-- ============================================================================
CREATE TABLE IF NOT EXISTS `requirement_slices` (
    `slice_id`      VARCHAR(128) NOT NULL COMMENT '切片唯一ID（如 req_raw:0 / req_raw:pre:1 / req_raw:1:2）',
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`    BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签',
    `doc_type`      VARCHAR(64)  NOT NULL DEFAULT 'requirement' COMMENT '来源文档类型（requirement/upload_*）',
    `doc_anchor`    VARCHAR(512) NOT NULL DEFAULT '' COMMENT '来源文档锚点（章节路径）',
    `section`       VARCHAR(255) NOT NULL DEFAULT '' COMMENT '章节标题（H2/H3路径，空表示无章节结构）',
    `slice_index`   INT          NOT NULL DEFAULT 0 COMMENT '切片序号（0-based，按原文顺序）',
    `text`          MEDIUMTEXT   NOT NULL COMMENT '切片原文（≤600字，智能切片保证：基础≤300字，跨模式合并后最大600字）',
    `text_hash`     CHAR(16)     NOT NULL DEFAULT '' COMMENT '★ 防同文本重复唯一约束键：md5(text.strip())[:16]',
    `char_count`    INT          NOT NULL DEFAULT 0 COMMENT '字符数',
    `needs_review`  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否需人工审核（相似度合并触发）',
    `merge_reasons_json` JSON NULL COMMENT '合并原因JSON（如["Jaccard相似度=0.18≥0.15"]）',
    `feature_label` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '所属功能标签（Phase4聚类结果）',
    `prev_context`  TEXT NULL COMMENT '★ 改进3(L3): 前文上下文（前一切片最后100字，供审核参考）',
    `next_context`  TEXT NULL COMMENT '★ 改进3(L3): 后文上下文（后一切片前100字，供审核参考）',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`project`, `version_id`, `slice_id`),
    UNIQUE KEY `uk_rs_text` (`project`, `version_id`, `text_hash`),
    INDEX `idx_rs_version` (`project`, `version_id`),
    INDEX `idx_rs_doc` (`project`, `version_id`, `doc_type`),
    CONSTRAINT `fk_rs_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='需求切片表 — 持久化智能切片结果，支持关系链路追溯到切片级别；uk_rs_text 防同文本不同 slice_id 的重复写入（Step2 多次触发/重试/并发均无法绕过）';

-- ============================================================================
-- ★ 新增(07-06): 单一事实源外键 — 消除需求切片/原子需求/RAG chunk 数据分叉
-- ============================================================================
-- requirements.slice_id 指向 requirement_slices.slice_id，_chunk_text 不再重复存储
ALTER TABLE `requirements` ADD COLUMN `slice_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源切片ID → requirement_slices.slice_id' AFTER `feature_id`,
                           ADD INDEX `idx_req_slice` (`project`, `version_id`, `slice_id`);

-- ★ 新增(07-07): requirements.source_quote — 需求原文引用（防幻觉5a子串匹配的事实源）
--   原先 source_quote 只存在 payload_json 里，门禁 5a 需要额外解析 JSON；
--   独立列后可直接 SQL 查询，且要求非空（Hard Constraint: source_quote 必须非空且来自真实切片）
ALTER TABLE `requirements` ADD COLUMN `source_quote` TEXT NOT NULL COMMENT '需求原文引用（防幻觉5a子串匹配事实源，必须来自所属切片真实文本）' AFTER `expected_feedback`;

-- rag_chunks.slice_id 让检索索引可以追溯到原始切片，避免文本双写
ALTER TABLE `rag_chunks` ADD COLUMN `slice_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源切片ID → requirement_slices.slice_id' AFTER `type`,
                         ADD INDEX `idx_rag_slice` (`scope_type`, `scope_id`, `slice_id`);

-- ★ G1 (2026-07-10): rag_chunks.chapter_hash 段落级哈希，增量重建时比对
--   相同 chapter_hash 的 chunk 保留旧 vector_blob，不重新嵌入（省 70%+ 嵌入计算）
ALTER TABLE `rag_chunks` ADD COLUMN `chapter_hash` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '段落内容哈希（MD5前16位，增量重建比对）' AFTER `embed_model`;

-- ★ B3 (2026-07-16): rag_chunks.user_id 用户级隔离，防止A用户修改嵌入模型清除B用户RAG
ALTER TABLE `rag_chunks` ADD COLUMN `user_id` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户ID（空=历史数据/共享）' AFTER `scope_id`,
                         ADD INDEX `idx_rag_user` (`user_id`, `scope_type`, `scope_id`, `is_active`);

-- ============================================================================
-- ★ 新增(07-06): 追溯链路关联表 — trace_links 不再冗余存储 api/db/figma JSON
-- ============================================================================
CREATE TABLE IF NOT EXISTS `trace_link_chunks` (
    `req_id`      VARCHAR(128) NOT NULL COMMENT '需求编号 → requirements.req_id',
    `project`     VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`  BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id',
    `chunk_type`  VARCHAR(32)  NOT NULL COMMENT '资源类型: api / db_table / figma_page / dom / requirement / upload_*',
    `chunk_id`    VARCHAR(256) NOT NULL COMMENT 'RAG chunk_id → rag_chunks.chunk_id',
    `rel_type`    VARCHAR(32)  NOT NULL DEFAULT 'matched' COMMENT '关联方式: ai / heuristic / matched / implicit',
    `confidence`  VARCHAR(16)  NOT NULL DEFAULT 'medium' COMMENT '置信度: high / medium / low',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`req_id`, `project`, `version_id`, `chunk_type`, `chunk_id`),
    INDEX `idx_tlc_chunk` (`chunk_type`, `chunk_id`),
    INDEX `idx_tlc_project` (`project`, `version_id`),
    INDEX `idx_tlc_slice` (`project`, `version_id`, `req_id`),
    CONSTRAINT `fk_tlc_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='追溯链路关联表 — 需求与RAG chunk的单一对多关联（消除资源数据分叉）';

-- ============================================================================
-- ★ 新增(07-05h): test_cases 表新增 test_method 列（VARCHAR(32)）
-- ============================================================================
-- 注意：test_cases 表已在 schema.sql 第19域定义，此处仅补充列说明
-- 实际DDL变更通过迁移脚本 scripts/migrations/001_add_test_method.sql 执行


-- ============================================================================
-- 第25域：操作指令库 (Action Library) — 原由 action_library_store.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `action_categories` (
    `category_id` VARCHAR(128) NOT NULL COMMENT '通用操作分类ID（如 mouse / form / keyboard）',
    `name`        VARCHAR(256) NOT NULL DEFAULT '' COMMENT '分类名称',
    `icon`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '分类图标',
    `sort_order`  INT          NOT NULL DEFAULT 0 COMMENT '排序序号',
    `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通用操作指令分类表 — 全项目共享的浏览器操作分类目录';

CREATE TABLE IF NOT EXISTS `action_actions` (
    `category_id`   VARCHAR(128)  NOT NULL COMMENT '所属通用分类ID → action_categories.category_id',
    `action_id`     VARCHAR(128)  NOT NULL COMMENT '通用操作ID（如 click / fill / verify）',
    `label`         VARCHAR(256)  NOT NULL DEFAULT '' COMMENT '操作标签',
    `description`   VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '操作描述',
    `keywords_json` LONGTEXT      NOT NULL COMMENT '关键词列表JSON',
    `params_json`   LONGTEXT      NOT NULL COMMENT '参数列表JSON',
    `needs_ai_id`   INT           NOT NULL DEFAULT 0 COMMENT '是否需要AI定位元素',
    `is_workflow`   INT           NOT NULL DEFAULT 0 COMMENT '是否工作流',
    `example`       LONGTEXT      NOT NULL COMMENT '示例',
    `updated_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `applicable_tags` json NULL COMMENT '适用控件标签/role列表（如 ["input","textarea"]），Step3按控件过滤、Step4校验使用',
    `total_count` int NOT NULL DEFAULT '0' COMMENT '调用总次数',
    `success_count` int NOT NULL DEFAULT '0' COMMENT '调用成功次数',
    `fail_count` int NOT NULL DEFAULT '0' COMMENT '调用失败次数',
    `tool_name` varchar(80) NOT NULL DEFAULT '' COMMENT '对应 browser_tools 工具名，action_dispatcher 直接查表执行',
    PRIMARY KEY (`category_id`, `action_id`),
    INDEX `idx_action_actions_action` (`action_id`),
    CONSTRAINT `fk_aa_category` FOREIGN KEY (`category_id`) REFERENCES `action_categories` (`category_id`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通用操作指令详情表 — 全项目共享的浏览器操作定义';

-- 2026-07-06: 增加控件类型映射字段，支持 Step3 按控件过滤操作、Step4 按控件校验操作
ALTER TABLE `action_actions` ADD COLUMN `applicable_tags` JSON NULL COMMENT '适用控件标签/role列表（如 ["input","textarea"]），Step3按控件过滤、Step4校验使用';

-- 2026-07-06: 增加调用统计字段，支持原子操作指令执行次数统计（总数/成功/失败）
ALTER TABLE `action_actions` ADD COLUMN `total_count` INT NOT NULL DEFAULT 0 COMMENT '调用总次数';
ALTER TABLE `action_actions` ADD COLUMN `success_count` INT NOT NULL DEFAULT 0 COMMENT '调用成功次数';
ALTER TABLE `action_actions` ADD COLUMN `fail_count` INT NOT NULL DEFAULT 0 COMMENT '调用失败次数';

-- 2026-07-06: 增加 tool_name 字段，将 _TOOL_PREFIX_MAP 映射并入 DB，action→tool 单一事实源
ALTER TABLE `action_actions` ADD COLUMN `tool_name` VARCHAR(80) NOT NULL DEFAULT '' COMMENT '对应 browser_tools 工具名，action_dispatcher 直接查表执行';

-- [墓碑] project_prompt_overrides — 已移除：prompt_templates 统一为系统级，无项目覆盖

CREATE TABLE IF NOT EXISTS `project_synonym_overrides` (
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `core_word`     VARCHAR(128) NOT NULL COMMENT '核心词 → synonym_library.core_word',
    `synonym_group` TEXT         NOT NULL COMMENT '项目覆盖同义词组JSON',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`, `core_word`),
    CONSTRAINT `fk_pso_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目同义词覆盖表 — 全局同义词库的项目级覆盖';

-- ============================================================================
-- [墓碑] uploaded_requirements — 已移除：合并到 source_documents
-- ============================================================================


-- ============================================================================
-- 第27域：项目元数据 (Project Meta) — 原由 project.py 自管
-- ============================================================================

CREATE TABLE IF NOT EXISTS `project_meta` (
    `project`      VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `display_name` VARCHAR(256) NOT NULL DEFAULT '' COMMENT '显示名称',
    `description`  VARCHAR(512) NOT NULL DEFAULT '' COMMENT '项目描述',
    `status`       VARCHAR(32)  NOT NULL DEFAULT 'active' COMMENT '状态: active/archived/deleted',
    `created_by`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '创建者用户ID',
    `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`project`),
    CONSTRAINT `fk_pmeta_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目元数据表 — 项目的显示名/描述/状态等辅助信息';

-- ============================================================================
-- 第28域：用户背景图库 (User BG Gallery) — 来自 bg.py
-- ============================================================================

CREATE TABLE IF NOT EXISTS `user_bg_gallery` (
    `id`          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '图片ID',
    `user_id`     VARCHAR(100) NOT NULL COMMENT '用户ID → users.id',
    `image_data`  LONGBLOB     NOT NULL COMMENT '图片二进制数据',
    `mime_type`   VARCHAR(50)  NOT NULL COMMENT 'MIME类型（image/jpeg等）',
    `image_size`  INT          NOT NULL DEFAULT 0 COMMENT '文件大小（字节）',
    `is_current`  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否当前激活（每用户仅1张）',
    `created_at`  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_user_id` (`user_id`),
    INDEX `idx_user_current` (`user_id`, `is_current`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户背景图库表 — 每用户最多20张背景图，LONGBLOB存储';

-- ============================================================================
-- 第29域：AI生成日志 (AI Generation Logs) — 来自 ai_generation_logs_store
-- ============================================================================

CREATE TABLE IF NOT EXISTS `ai_generation_logs` (
    `project`                VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`             BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
    `log_id`                 VARCHAR(255) NOT NULL COMMENT '日志唯一ID',
    `case_id`              VARCHAR(255) NOT NULL COMMENT '测试用例ID',
    `req_id`                 VARCHAR(128) NOT NULL COMMENT '需求ID',
    `module`                 VARCHAR(255) NOT NULL COMMENT '模块名',
    `title`                  TEXT         NOT NULL COMMENT '标题',
    `failure_reason`         LONGTEXT     NOT NULL COMMENT '失败原因',
    `classification`         VARCHAR(32)  NOT NULL DEFAULT 'unknown' COMMENT '分类: hallucination / env / unknown',
    `raw_steps_json` LONGTEXT    NOT NULL COMMENT '原始用例行为JSON',
    `source`                 VARCHAR(64)  NOT NULL DEFAULT 'hallucination_logger' COMMENT '来源',
    `created_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`project`, `log_id`),
    CONSTRAINT `fk_aig_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI生成日志表 — 记录幻觉/环境等AI生成异常';

-- ============================================================================
-- 第31域：Prompt模板 & 同义词库 — 来自 m001/m002 migration
-- ============================================================================

CREATE TABLE IF NOT EXISTS `prompt_templates` (
    `name`          VARCHAR(128) NOT NULL COMMENT '模板名称（主键）',
    `template_text` MEDIUMTEXT   NOT NULL COMMENT 'Prompt 模板正文（动态模板含 {{占位符}}，render 时注入运行时上下文）',
    `category`      VARCHAR(64)  NOT NULL DEFAULT '其他' COMMENT '分类: Step2 需求拆解 / Step2 需求去重 / Step3 用例建模 / Step4 浏览器操作 / 质量校验 / 自定义',
    `description`   VARCHAR(255) NOT NULL DEFAULT '' COMMENT '模板用途描述',
    `is_seed`       TINYINT      NOT NULL DEFAULT 0 COMMENT '是否种子模板(1=系统内置不可删除，可恢复默认，0=用户自定义)',
    `enabled`       TINYINT      DEFAULT 1 COMMENT '是否启用',
    `updated_at`    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统级 Prompt 模板表 — DB 唯一事实源，前端可编辑，is_seed=1 的种子模板可恢复默认';

CREATE TABLE IF NOT EXISTS `synonym_library` (
    `id`             INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    `core_word`      VARCHAR(128) NOT NULL COMMENT '核心词',
    `synonym_group`  TEXT         NOT NULL COMMENT '同义词组（JSON 或分隔文本）',
    `created_at`     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY `uk_core_word` (`core_word`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统级同义词库 — 全局默认同义词；项目差异通过 project_synonym_overrides 覆盖';

-- ============================================================================
-- 第33域：Step4 能力缓存 (Capability Cache) — 来自 capability_cache/schema.py
-- ============================================================================

CREATE TABLE IF NOT EXISTS `capability_cache` (
    `capability_key`        VARCHAR(64)   NOT NULL PRIMARY KEY COMMENT '能力缓存主键，按项目、能力类型和业务意图生成',
    `project`               VARCHAR(128)  NOT NULL COMMENT '所属项目名称；能力缓存是项目级资产',
    `version_id`            BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=跨版本复用',
    `capability_name`       VARCHAR(128)  NOT NULL COMMENT '能力名称',
    `case_normalized`     TEXT          NOT NULL COMMENT '归一化后的业务意图文本',
    `preconditions_json`    LONGTEXT      NULL COMMENT '能力适用前置条件 JSON',
    `inputs_schema_json`    LONGTEXT      NULL COMMENT '测试数据约束或输入结构 JSON',
    `implementation_json`   LONGTEXT      NULL COMMENT '最近一次成功运行的实现经验 JSON',
    `success_evidence_json` LONGTEXT      NULL COMMENT '最近一次成功证据 JSON',
    `success_count`         INT           NOT NULL DEFAULT 0 COMMENT '成功沉淀次数',
    `failure_count`         INT           NOT NULL DEFAULT 0 COMMENT '失败次数',
    `hit_count`             INT           NOT NULL DEFAULT 0 COMMENT '复用命中次数',
    `last_status`           VARCHAR(32)   NULL COMMENT '最近状态：PASS/FAIL/INVALID',
    `confidence`            DOUBLE        NULL COMMENT '最近一次能力置信度',
    `is_valid`              TINYINT       NOT NULL DEFAULT 1 COMMENT '是否仍可复用',
    `page_signature_hash`   VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '能力适用页面签名哈希',
    `invalidation_reason`   TEXT          NULL COMMENT '最近一次失效原因',
    `invalidated_at`        DATETIME      NULL COMMENT '最近一次失效时间',
    `last_verified_at`      DATETIME      NULL COMMENT '最近一次复用前校验时间',
    `created_at`            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `last_hit_at`           DATETIME      NULL COMMENT '最近一次命中时间',
    INDEX `idx_capability_project` (`project`),
    INDEX `idx_capability_name` (`project`, `capability_name`),
    INDEX `idx_capability_valid` (`project`, `capability_name`, `is_valid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step4 项目级业务能力缓存表';

CREATE TABLE IF NOT EXISTS `capability_cache_events` (
    `id`                   BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '事件自增主键',
    `project`              VARCHAR(128)  NOT NULL COMMENT '所属项目名称',
    `version_id`           BIGINT        NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=跨版本复用',
    `capability_key`       VARCHAR(64)   NOT NULL COMMENT '关联 capability_cache.capability_key',
    `capability_name`      VARCHAR(128)  NOT NULL COMMENT '能力名称',
    `event_type`           VARCHAR(32)   NOT NULL COMMENT '事件类型：HIT/INVALIDATE/FAIL/PASS',
    `reason`               TEXT          NULL COMMENT '事件原因或说明',
    `old_signature_hash`   VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '事件发生前的页面签名哈希',
    `new_signature_hash`   VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '事件发生时的页面签名哈希',
    `context_json`         LONGTEXT      NULL COMMENT '事件上下文 JSON',
    `created_at`           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件创建时间',
    INDEX `idx_capability_event_project` (`project`),
    INDEX `idx_capability_event_key` (`capability_key`),
    INDEX `idx_capability_event_type` (`project`, `event_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step4 项目级能力缓存事件表';

CREATE TABLE IF NOT EXISTS `capability_cache_policy` (
    `project`              VARCHAR(128) NOT NULL PRIMARY KEY COMMENT '所属项目名称；能力治理策略按项目隔离',
    `max_capabilities`     INT          NOT NULL DEFAULT 1000 COMMENT '单项目能力缓存最大条目数',
    `event_retention_days` INT          NOT NULL DEFAULT 3 COMMENT '能力事件保留天数',
    `created_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '策略创建时间',
    `updated_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '策略更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step4 项目级能力库治理策略表';

-- ============================================================================
-- 第34域：Step5 运行摘要 (Run Summary) — 来自 _db_utils.py
-- ============================================================================

CREATE TABLE IF NOT EXISTS `run_summary` (
    `id`                   BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    `project`              VARCHAR(128) NOT NULL COMMENT '所属项目名称；运行摘要按项目隔离',
    `version_id`           VARCHAR(128) NOT NULL DEFAULT '' COMMENT '项目版本标识',
    `run_id`               VARCHAR(64)  NOT NULL COMMENT '流水线运行 ID',
    `user_id`              VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '★ 方案B: 执行用户ID，支持用户级隔离',
    `report_version`       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '报告版本号',
    `total_cases`        INT          NOT NULL DEFAULT 0 COMMENT '本次报告测试用例总数',
    `executed_cases`     INT          NOT NULL DEFAULT 0 COMMENT '本次已测试用例数',
    `total_passed`         INT          NOT NULL DEFAULT 0 COMMENT '本次通过测试用例数',
    `total_failed`         INT          NOT NULL DEFAULT 0 COMMENT '本次失败测试用例数',
    `total_skipped`        INT          NOT NULL DEFAULT 0 COMMENT '本次跳过测试用例数',
    `total_pending`        INT          NOT NULL DEFAULT 0 COMMENT '本次待测试用例数',
    `total_flaky`          INT          NOT NULL DEFAULT 0 COMMENT '本次 Flaky 测试用例数',
    `pass_rate`            DOUBLE       NOT NULL DEFAULT 0 COMMENT '本次达成率，范围 0-1',
    `avg_confidence`       DOUBLE       NOT NULL DEFAULT 0 COMMENT '本次平均置信度，范围 0-1',
    `low_confidence_count` INT          NOT NULL DEFAULT 0 COMMENT '低置信度测试用例数量',
    `op_cache_hit_rate`    DOUBLE       NOT NULL DEFAULT 0 COMMENT 'Step4 操作缓存命中率',
    `capability_hit_rate`  DOUBLE       NOT NULL DEFAULT 0 COMMENT 'Step4 能力库命中率',
    `total_duration_sec`   DOUBLE       NOT NULL DEFAULT 0 COMMENT '本次执行验证总耗时秒数',
    -- ★ v7: 证据引擎指标
    `evidence_calibrated_confidence` DOUBLE NOT NULL DEFAULT 0 COMMENT '证据引擎校准后的平均置信度（贝叶斯后验+独立验证）',
    `evidence_coverage`     DOUBLE       NOT NULL DEFAULT 0 COMMENT '有独立证据验证的主张比例',
    `evidence_cold_start`   TINYINT      NOT NULL DEFAULT 0 COMMENT '是否冷启动（校准样本<阈值）',
    `summary_json`         LONGTEXT     NULL COMMENT '运行摘要扩展 JSON',
    `created_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '摘要创建时间',
    `updated_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '摘要更新时间',
    `ai_loop_pass_rate` decimal(5,4) NULL DEFAULT '0.0000',
    UNIQUE KEY `uk_run_summary_run` (`project`, `run_id`),
    KEY `idx_run_summary_project_time` (`project`, `created_at`),
    KEY `idx_run_summary_project_version` (`project`, `version_id`, `created_at`),
    KEY `idx_run_summary_user` (`project`, `version_id`, `user_id`, `created_at`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Step5 项目运行级摘要表 — 记录每次流水线报告结果，用于趋势对比和历史质量分析';

-- ============================================================================
-- 初始化：插入默认角色
-- ============================================================================
INSERT IGNORE INTO `roles` (`id`, `name`, `description`, `is_system`) VALUES
    ('admin',           '系统管理员', '拥有所有权限，可管理用户/项目/配置', 1),
    ('project_admin',   '项目管理员', '可创建项目、管理项目成员、执行全部步骤', 1),
    ('project_member',  '项目成员',   '可执行步骤4-5、查看报告与项目信息', 1);

-- ============================================================================
-- 第X域：融合架构工具调用 (Fusion Tool Calling)
-- ============================================================================

CREATE TABLE IF NOT EXISTS `tool_execution_log` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增ID',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `case_id`         VARCHAR(128) NOT NULL DEFAULT '' COMMENT '测试用例ID',
    `step_desc`       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '动作目标描述',
    `trace_id`        VARCHAR(64)  NOT NULL COMMENT '链路追踪ID（同一批tool_call共享）',
    `tool_name`       VARCHAR(64)  NOT NULL COMMENT '工具名（如browser_click）',
    `tool_args_json`  JSON         NULL     COMMENT '工具参数JSON（摘要）',
    `result_ok`       TINYINT(1)   NOT NULL COMMENT '执行结果（1=成功，0=失败）',
    `result_content`  TEXT         NULL     COMMENT '执行结果内容或错误信息',
    `elapsed_ms`      INT          NOT NULL DEFAULT 0 COMMENT '执行耗时（毫秒）',
    `atomic_rollback` TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否因原子性被跳过（1=是）',
    `tool_mode`       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否来自原生工具调用模式（1=是，0=文本模式）',
    `model`           VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '使用的AI模型名',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `version_id` bigint NOT NULL DEFAULT '0' COMMENT '版本数字ID（版本隔离）',
    PRIMARY KEY (`id`),
    INDEX `idx_tel_project` (`project`),
    INDEX `idx_tel_trace` (`trace_id`),
    INDEX `idx_tel_intent` (`project`, `case_id`),
    INDEX `idx_tel_tool` (`project`, `tool_name`),
    INDEX `idx_tel_created` (`created_at`),
    CONSTRAINT `fk_tel_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='融合架构工具执行日志表 — 记录每个tool_call的执行详情，用于链路追踪和性能分析';

-- ============================================================================
-- 初始化：默认管理员
-- ★ P0-03: 不再在 DDL 中硬编码密码哈希。系统启动时由 bootstrap 流程
--   (src/backend/auth/_bootstrap.py) 检测 users 表是否为空，为空则创建
--   用户名"admin" + 随机生成临时密码并打印到日志（强制首次启动必须改密码）。
--   详见 src/core/constants.py 的 BOOTSTRAP_ADMIN_ID / BOOTSTRAP_DEFAULT_PASSWORD。
-- ============================================================================
INSERT IGNORE INTO `users` (`id`, `name`, `password`) VALUES
    -- ★ 密码哈希已移出 DDL，由 bootstrap 流程动态设置
    ('admin', '管理员', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9');
INSERT IGNORE INTO `user_roles` (`user_id`, `role_id`) VALUES
    ('admin', 'admin');

-- ============================================================================
-- 初始化：admin 角色权限（全部 34 个功能权限）
-- ============================================================================
INSERT IGNORE INTO `role_permissions` (`role_id`, `permission`) VALUES
    -- admin：全部权限
    ('admin', 'project.create'),
    ('admin', 'project.delete'),
    ('admin', 'project.config.view'),
    ('admin', 'project.config.edit'),
    ('admin', 'project.config.history'),
    ('admin', 'project.config.rollback'),
    ('admin', 'project.member.manage'),
    ('admin', 'project.version.create'),
    ('admin', 'project.version.edit'),
    ('admin', 'project.version.archive'),
    ('admin', 'user.manage'),
    ('admin', 'user.role.set'),
    ('admin', 'user.project.assign'),
    ('admin', 'file.clear'),
    ('admin', 'file.upload'),
    ('admin', 'file.delete'),
    ('admin', 'file.download'),
    ('admin', 'datasource.figma.refresh'),
    ('admin', 'datasource.yapi.refresh'),
    ('admin', 'datasource.db.test'),
    ('admin', 'datasource.bg.upload'),
    ('admin', 'execution.run'),
    ('admin', 'execution.step1'),
    ('admin', 'execution.step2'),
    ('admin', 'execution.step3'),
    ('admin', 'execution.step4'),
    ('admin', 'execution.step5'),
    ('admin', 'execution.stop'),
    ('admin', 'execution.retry'),
    ('admin', 'docs.view'),
    ('admin', 'docs.edit'),
    ('admin', 'docs.export'),
    ('admin', 'system.settings'),
    ('admin', 'system.rag.rebuild'),
    ('admin', 'health.view'),

    -- project_admin：项目执行+配置+成员管理，不含系统管理员独有权限(user.manage/user.role.set/user.project.assign/system.settings/project.delete)
    ('project_admin', 'project.create'),
    ('project_admin', 'project.config.view'),
    ('project_admin', 'project.config.edit'),
    ('project_admin', 'project.config.history'),
    ('project_admin', 'project.config.rollback'),
    ('project_admin', 'project.member.manage'),
    ('project_admin', 'project.version.create'),
    ('project_admin', 'project.version.edit'),
    ('project_admin', 'project.version.archive'),
    ('project_admin', 'file.clear'),
    ('project_admin', 'file.upload'),
    ('project_admin', 'file.delete'),
    ('project_admin', 'file.download'),
    ('project_admin', 'datasource.figma.refresh'),
    ('project_admin', 'datasource.yapi.refresh'),
    ('project_admin', 'datasource.db.test'),
    ('project_admin', 'datasource.bg.upload'),
    ('project_admin', 'execution.run'),
    ('project_admin', 'execution.step1'),
    ('project_admin', 'execution.step2'),
    ('project_admin', 'execution.step3'),
    ('project_admin', 'execution.step4'),
    ('project_admin', 'execution.step5'),
    ('project_admin', 'execution.stop'),
    ('project_admin', 'execution.retry'),
    ('project_admin', 'docs.view'),
    ('project_admin', 'docs.edit'),
    ('project_admin', 'docs.export'),
    ('project_admin', 'system.rag.rebuild'),
    ('project_admin', 'health.view'),

    -- project_member：创建项目 + step3-5 + 文档查看/导出 + 健康度 + 下载
    ('project_member', 'project.create'),
    ('project_member', 'execution.step3'),
    ('project_member', 'execution.step4'),
    ('project_member', 'execution.step5'),
    ('project_member', 'docs.view'),
    ('project_member', 'docs.export'),
    ('project_member', 'health.view'),
    ('project_member', 'file.download');

-- ============================================================================
-- 第27域：系统配置 (System Config) — 加密密钥、全局参数等
-- ============================================================================

CREATE TABLE IF NOT EXISTS `system_config` (
    `key`         VARCHAR(128)  NOT NULL COMMENT '配置键（如 encryption_key_v2）',
    `value`       TEXT          NOT NULL COMMENT '配置值',
    `description` VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '配置说明',
    `updated_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置表 — 加密密钥、全局参数等';

-- ============================================================================
-- 初始化：默认演示项目
-- ★ 不再在 DDL 中硬编码预设项目数据，所有项目必须通过前端 UI 创建。
--   bootstrap 流程仅检测并重命名占位某SPA系统（如有），不创建项目。
-- ============================================================================

-- ============================================================================
-- 第35域：置信度校准 (Confidence Calibration) — 证据引擎核心表
-- ============================================================================
-- 记录 AI 声称的置信度 vs Step4 实际执行通过率的校准数据。
-- 用于贝叶斯后验校准，将置信度从 AI 自评升级为历史实证。
-- 校准维度覆盖: 共识级别/视角数量/source_quote/需求类型/风险等级/测试用例类型
CREATE TABLE IF NOT EXISTS `confidence_calibration` (
    `id`                BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `project`           VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `calibration_key`   VARCHAR(128) NOT NULL COMMENT '校准维度: consensus_level / perspective_count / source_quote / requirement_type / risk_level / case_type',
    `calibration_value` VARCHAR(128) NOT NULL COMMENT '校准值（如 HIGH / 2 / yes / functional / P0 / 功能）',
    `total_predictions` INT          NOT NULL DEFAULT 0 COMMENT 'AI 声称高置信度的总次数',
    `total_correct`     INT          NOT NULL DEFAULT 0 COMMENT 'Step4 实际通过次数',
    `accuracy`          DOUBLE       NOT NULL DEFAULT 0.0 COMMENT '实际准确率 = total_correct / total_predictions',
    `last_updated`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_calibration` (`project`, `calibration_key`, `calibration_value`),
    INDEX `idx_cal_project` (`project`),
    CONSTRAINT `fk_cal_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='置信度校准表 — 证据引擎：记录AI声称置信度 vs 实际执行通过率，驱动贝叶斯校准';

-- ============================================================================
-- 第36域：流水线用户配置 (Pipeline User Config)
-- ============================================================================
-- 代码运行时会自行 CREATE TABLE IF NOT EXISTS，为统一 schema 管理迁移至此。
CREATE TABLE IF NOT EXISTS `pipeline_user_config` (
    `id`          BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    `user_id`     VARCHAR(128) NOT NULL COMMENT '用户ID → users.id',
    `config_key`  VARCHAR(128) NOT NULL COMMENT '配置键名（如 ai_model / op_cache_max_capacity）',
    `config_value` TEXT        NOT NULL COMMENT '配置值（字符串存储，读取时按类型转换）',
    `source`      VARCHAR(16)  NOT NULL DEFAULT 'manual' COMMENT '来源: manual=用户手动保存 / auto=切项目自动套用推荐值',
    `updated_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY `uk_user_key` (`user_id`, `config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='流水线用户级配置覆盖表 — 优先级高于环境变量和系统默认配置';

-- ============================================================================
-- Figma 事实源表（独立于 RAG 索引，RAG 重建时从此读取）
-- ============================================================================

CREATE TABLE IF NOT EXISTS `figma_pages` (
    `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `project`        VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签（空字符串 = 全局基线）',
    `page_id`        VARCHAR(100) NOT NULL COMMENT 'Figma 节点 ID',
    `name`           VARCHAR(500) NOT NULL COMMENT '页面名称',
    `canvas`         VARCHAR(500) NOT NULL DEFAULT '' COMMENT '所属画布名',
    `page_type`      VARCHAR(50)  NOT NULL DEFAULT 'FRAME' COMMENT '页面类型: FRAME / COMPONENT_SET',
    `ui_structure`   MEDIUMTEXT   COMMENT 'parse_figma_document() 输出的 UI 结构描述',
    `element_count`  INT          NOT NULL DEFAULT 0 COMMENT '页面内交互元素数量',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_fp_proj_ver_page` (`project`, `version_label`, `page_id`),
    INDEX `idx_fp_project` (`project`),
    INDEX `idx_fp_version` (`project`, `version_label`),
    CONSTRAINT `fk_fp_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 页面事实表 — 顶层 FRAME/CANVAS，RAG 重建时读取';

CREATE TABLE IF NOT EXISTS `figma_nodes` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签（空字符串 = 全局基线）',
    `node_id`         VARCHAR(100) NOT NULL COMMENT 'Figma 节点 ID',
    `page_id`         VARCHAR(100) NOT NULL COMMENT '所属页面 ID → figma_pages.page_id',
    `parent_node_id`  VARCHAR(100) NOT NULL DEFAULT '' COMMENT '父节点 ID',
    `name`            VARCHAR(500) NOT NULL DEFAULT '' COMMENT '节点名称',
    `node_type`       VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '节点类型: TEXT / INSTANCE / FRAME / COMPONENT / ...',
    `text_content`    TEXT         COMMENT '节点的文本内容（TEXT 节点的 characters）',
    `meta_json`       JSON         COMMENT 'variants / componentProperties 等元数据',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_fn_proj_ver_node` (`project`, `version_label`, `node_id`),
    INDEX `idx_fn_page` (`project`, `version_label`, `page_id`),
    INDEX `idx_fn_type` (`node_type`),
    CONSTRAINT `fk_fn_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 节点事实表 — 页面内的交互元素，RAG 重建时读取';

CREATE TABLE IF NOT EXISTS `figma_components` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `project`         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签（空字符串 = 全局基线）',
    `component_id`    VARCHAR(100) NOT NULL COMMENT 'Figma 组件 ID',
    `name`            VARCHAR(500) NOT NULL DEFAULT '' COMMENT '组件名称',
    `description`     TEXT         COMMENT '组件描述（name + variant 选项摘要）',
    `variant_options` JSON         COMMENT 'componentPropertyDefinitions 的 variant 选项',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_fc_proj_ver_comp` (`project`, `version_label`, `component_id`),
    INDEX `idx_fc_project` (`project`),
    CONSTRAINT `fk_fc_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Figma 组件事实表 — 可复用组件定义，RAG 重建时读取';

-- ============================================================================
-- 第38域：Streaming pipeline 状态列（v4.0 流式管道）
-- ============================================================================
-- 新增列使用 ALTER TABLE ... ADD COLUMN 语法（db_schema.py 通过 error code 1060 保证幂等）。
-- 对已存在的数据库，执行以下语句；新安装由 CREATE TABLE 定义自动包含。

-- self_heal_patches: 删除错误的 fk_shp_intent 外键（case_id 实际存的是 ai_id，非 case_meta.case_id）
ALTER TABLE `self_heal_patches` DROP FOREIGN KEY `fk_shp_intent`;

-- 索引（IF NOT EXISTS 对 INDEX 不适用，用存储过程或忽略 duplicate error）
-- CREATE INDEX IF NOT EXISTS `idx_req_streaming` ON `requirements` (`project`, `version_id`, `modeling_status`);
-- CREATE INDEX IF NOT EXISTS `idx_case_streaming` ON `test_cases` (`project`, `version_id`, `execution_status`);

-- ============================================================================
-- 第39域：System B/C 置信度打通（v4.1 三置信度融合）
-- ============================================================================
-- System B: 执行质量因子明细（JSON）+ System C: 证据校准置信度

-- ★ Fix (2026-07-12): confidence 原类型 INT 会导致 0-100 百分比被截断/四舍五入为 0/1
ALTER TABLE `case_meta` MODIFY COLUMN `confidence` DECIMAL(5,2) NOT NULL DEFAULT 0.00 COMMENT '置信度评分（0-100）';
ALTER TABLE `case_meta` ADD COLUMN `execution_factors` JSON NULL COMMENT 'System B 执行置信度因子明细（Step4 指标: assertion_rate等6因子JSON）';
ALTER TABLE `case_meta` ADD COLUMN `evidence_confidence` FLOAT NULL COMMENT 'System C 证据校准置信度（Step4 指标: 贝叶斯融合AI声称+历史校准+独立证据）';
ALTER TABLE `case_meta` ADD COLUMN `evidence_ai_raw` FLOAT NULL COMMENT 'System C AI原始声称置信度（0-1）';
ALTER TABLE `case_meta` ADD COLUMN `evidence_cal_accuracy` FLOAT NULL COMMENT 'System C 历史校准准确率（0-1，无数据时NULL）';
ALTER TABLE `case_meta` ADD COLUMN `evidence_strength` FLOAT NULL COMMENT 'System C 独立证据强度（0-1）';
ALTER TABLE `case_meta` ADD COLUMN `evidence_level` VARCHAR(16) NULL COMMENT 'System C 置信度等级: HIGH/MEDIUM/LOW';
ALTER TABLE `case_meta` ADD COLUMN `evidence_verdict` VARCHAR(32) NULL COMMENT 'System C 判决类型: evidence_calibrated/calibrated_only/cold_start/ai_only';
ALTER TABLE `case_meta` ADD COLUMN `evidence_cold_start` TINYINT NULL COMMENT 'System C 是否冷启动: 1=冷启动 0=有足够校准样本';

-- ============================================================================
-- 第40域：测试方法论 + 控件类型（v4.2 07-05h 关系链路 + 智能测试方法论）
-- ============================================================================
-- test_cases 新增 test_method（12编码 EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI）
-- 与 control_type（8大控件类 input/select/button/upload/display/nav/interactive/composite）
ALTER TABLE `test_cases` ADD COLUMN `test_method` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '★ 测试方法论编码(07-05h): EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI，与scenario_type正交';
ALTER TABLE `test_cases` ADD COLUMN `control_type` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '★ 控件类型(07-05h): input/select/button/upload/display/nav/interactive/composite';
ALTER TABLE `test_cases` ADD COLUMN `precondition` TEXT NULL COMMENT '★ 前置条件(2026-08-09 IEEE 829): 编号列表，如"1.系统已登录 2.测试数据已准备"';
ALTER TABLE `test_cases` ADD COLUMN `test_steps` TEXT NULL COMMENT '★ 测试步骤(2026-08-09 IEEE 829): JSON数组，描述操作步骤序列';
ALTER TABLE `test_cases` ADD COLUMN `expected_result` TEXT NULL COMMENT '★ 预期结果(2026-08-09 IEEE 829): 精确描述操作后的预期状态';

-- ★ Fix (2026-07-12): requirements 表缺少 _verify_status / _evidence_confidence 列，导致 Step3 门禁 SELECT 失败
ALTER TABLE `requirements` ADD COLUMN `_verify_status` VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'source_verify 校验状态: passed/passed_corrected/partial_match/unverified/blocked/no_quote/skipped_implicit/empty_quote' AFTER `source_quote`;
ALTER TABLE `requirements` ADD COLUMN `_evidence_confidence` DOUBLE NOT NULL DEFAULT 0 COMMENT '证据置信度评分 0.0~1.0' AFTER `_verify_status`;

-- ★ Fix (2026-07-12): executions 表缺少 check_type/expected/actual/details 列，导致 scheduler 门禁写入失败
ALTER TABLE `executions` ADD COLUMN `check_type` VARCHAR(32) NOT NULL DEFAULT '' COMMENT '检查类型: pre_gate/step_exec/...' AFTER `case_id`;
ALTER TABLE `executions` ADD COLUMN `expected` TEXT COMMENT '预期结果' AFTER `status`;
ALTER TABLE `executions` ADD COLUMN `actual` TEXT COMMENT '实际结果' AFTER `expected`;
ALTER TABLE `executions` ADD COLUMN `details` TEXT COMMENT '详细信息' AFTER `actual`;

-- ★ v23 根源修复(2026-07-21): executions 表新增 confidence + confidence_factors 列，
--   消除置信度双计算路径（架构红线4：禁止格式转换层）。
--   根因: ConfidenceGate 在 _execute_one_intent 中已正确计算 confidence（如83%），
--   但只写日志不持久化；_run_step 完成后又从 DB 重新构造 block_in（step_results=[]）
--   再算一次，导致 confidence 被严重低估为 35%，假 PASS 漏检。
--   修复: confidence 在执行时一次计算并写入 executions，_run_step 直接读 DB。
ALTER TABLE `executions` ADD COLUMN `confidence` DOUBLE NOT NULL DEFAULT 0 COMMENT '置信度评分（0-1，ConfidenceGate 计算后写入，_run_step 直接读取）' AFTER `details`;
ALTER TABLE `executions` ADD COLUMN `confidence_factors` TEXT NULL COMMENT '置信度因子JSON（assertion_rate/case_completeness/hard_verify_rate/first_try_rate/semantic_align/cache_hit_rate）' AFTER `confidence`;

-- ★ 根因修复(2026-10-28) 证据列容量：expected/actual/details 承载逐用例证据
--   （console/network/check 明细）。TEXT 上限 65535 字节，日志量大的页面会被击穿
--   （实测 118 例 details 超限 → MySQL 1406 → 落库异常 → 该用例整行丢失）。
--   证据体量由被测系统日志量决定，属跨系统通用问题，故按证据列语义统一扩容。
--   幂等：db_schema._alter_already_applied 对 MODIFY 按 information_schema.DATA_TYPE
--   比对，类型已一致即跳过，不会每次启动重建表。
ALTER TABLE `executions` MODIFY COLUMN `expected` MEDIUMTEXT NULL COMMENT '预期结果';
ALTER TABLE `executions` MODIFY COLUMN `actual` MEDIUMTEXT NULL COMMENT '实际结果';
ALTER TABLE `executions` MODIFY COLUMN `details` MEDIUMTEXT NULL COMMENT '详细信息';

-- ★ v4.0 (2026-07-12): 诊断中心 — 流水线自检结果表
CREATE TABLE IF NOT EXISTS `pipeline_self_test_results` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `project`         VARCHAR(128)  NOT NULL COMMENT '项目名 → projects.name',
    `run_id`          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '自检运行ID',
    `user_id`         VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '执行用户ID',
    `steps_json`      JSON          NOT NULL COMMENT '各Step自检结果（status/detail/checks/duration_ms）',
    `overall`         VARCHAR(16)   NOT NULL DEFAULT 'UNKNOWN' COMMENT '总体状态: HEALTHY/DEGRADED/BROKEN',
    `passed`          INT           NOT NULL DEFAULT 0 COMMENT '通过Step数',
    `failed`          INT           NOT NULL DEFAULT 0 COMMENT '失败Step数',
    `skipped`         INT           NOT NULL DEFAULT 0 COMMENT '跳过Step数',
    `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_pstr_project` (`project`, `created_at`),
    INDEX `idx_pstr_run` (`run_id`),
    CONSTRAINT `fk_pstr_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='流水线自检结果表 — 诊断中心存储每次自检的Step级结果';

-- ============================================================================
-- 默认演示项目种子数据（清库后自动恢复，INSERT IGNORE 保证幂等）
-- 生成时间: 2026-08-12（替换原某SPA系统种子，改用本地 SmartShop 系统）
-- ============================================================================

-- 加密密钥（全局，与 credentials 解密绑定）
INSERT IGNORE INTO `system_config` (`key`, `value`, `description`) VALUES
    ('encryption_key_v2', 'oFO-llTsrTtgmtPPX3KovyCxrx80dRKIfUssmyzNma4=', 'Fernet 密码加密密钥（v2）');

-- 默认演示项目：SmartShop（本地 Flask 电商系统，端口 8020）
INSERT IGNORE INTO `projects` (`name`, `status`) VALUES ('SmartShop', 'active');

-- 版本
INSERT IGNORE INTO `project_versions` (`project`, `version_label`, `status`) VALUES ('SmartShop', 'v1.0', 'active');

-- 项目配置（★ 2026-09-28：由早期 Django 8000 遗留修正为 Flask 8020 真值，
--   随 examples/smartshop/app.py 拉起的实际靶子；TARGET_URL 亦会被 target_cfgs 兜底）
INSERT IGNORE INTO `project_configs` (`project`, `config_json`) VALUES ('SmartShop', '{"TARGET_URL": "http://127.0.0.1:8020", "ADMIN_APP_NAME": "", "TARGET_LOGIN_PATH": "", "LOGIN_USERNAME_SELECTOR": "", "LOGIN_PASSWORD_SELECTOR": "", "LOGIN_SUBMIT_SELECTOR": ""}');

-- 项目元数据
INSERT IGNORE INTO `project_meta` (`project`, `display_name`, `description`, `status`) VALUES ('SmartShop', 'SmartShop 电商平台', 'Flask 电商系统（Product/Order/Supplier/Category）', 'active');

-- 用户凭证 (admin, project='*' 全局) — 2026-07-28 更新
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'AGNES_API_BASE_URL', 'gAAAAABqaDLNZuaD4sTvBh8Thx3Cg7nogWI2G0lbyg-N91guyCX2bKL460Qyw1df1w6mlgEvl54T0xLuvsWUCm6aCL26wC3c47FtqOTy5LBIQkYcy9uqJBI=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'AGNES_API_KEY', 'gAAAAABqaDLNRn3R8SflUbRZ0pu0IURuMJqfszi-f83M5GDUz7axs_ielxYHbG-5ugGehIW6KtxdK9-7F6Q42ezUthZgL3NI2w7dlPUs94jiXwzY_M_FKlVo_qilPWBod1XbtRx-z5wU4kpNaKDoJb_IGNburHt_pA==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'AGNES_ENTERPRISE_ID', 'gAAAAABqaDLNAkACSSQB8PKWDeXvwx5sAw4FgvZ15XiBC36-nRcZcfEEmj6x6ZhBYd6WcgPfkGL7NnmSduUR20SCjbiNlTA2nzqs9Nu4bocrfOrfDs9Yus4=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'API_BASE_URL', 'gAAAAABqaDLNd2DAglwRR3M5soHbehlPzEjpYHF6goPIYn3C267o6PHeUjZ10NCU0spmihY9oLUBL8nkSj32nEI6Vd8INB9H83f7u5YkS0cUqDVvx2T3PMo=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'API_DOCS_PASSWORD', 'gAAAAABqaDLNV7P3_gpCN2nS52EvUF_ZqLbgs702b9q7roJoc-MdOY-rzJ89nRe0KD39n8GB_hwCGWcxKZXK0FF8mBXil1pOTw==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'API_DOCS_USERNAME', 'gAAAAABqaDLNT0bL70h5F3PRANnTY0xgEruEoXgxRqeBsnVdRnMp9jDqtVC3jmOoUMobSs3KHmmPhGp4jIZY4oEUWYudBedUDA==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'API_KEY', 'gAAAAABqaDLNcJu2MyscxukugEsZlXPa7gikLyiYEU3KjJQDOPw-sUBgPqaenXvl2XZM_tEVViLMbvEsXqU8i1xmL1PLvZrF62WSGdQQyfC4r85GFGXF8grl_QP0jRxj9T61xaTFIzMYmso3RcO3zLHRA4QWV40-_g==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'API_MODEL', 'gAAAAABqaDLNQ7AG3z6EWU1LaO0vPfsSnvawWmtkVDBRB-MmkZ6mapP3Dxf4pWhHaDkZ4PjKo6WVC_YVXOBQAwX7IEije_mHZQ==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CLOUDBASE_API_BASE_URL', 'gAAAAABqaDLNKyKQfSRnOZo0FN-79TsaDqJY3S0cGp4RrQ9VvPf6K58JjXzeE3SX6_POam87O-LUeV1yhnjtdIttTb71nStnNyl145NO77p3NL9X_U8RzpWBW19qynDVOid7mHJxkCX6uuoArOjmTJ04g6_l87I7PLVh19Qxk-sxpyn1RpI0K4Y=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CLOUDBASE_API_KEY', 'gAAAAABqaDLNOG9josrAhSIvWB2xmcPJ3bQlQ1GJwWtwCRQe4vzRPcXKyVNoUKOpvZbBpXYgWzSZs8PysHFGmQMemEIcosbYCe1KZwzZhOGMboYt-Ox836v0NQ_k0l-dixUu3BW7-9TkFgJDEWQnaYMgcjR_OaUKOkLSG8-FlE32tipnqq8L1DK9bhygUuc-q1qePMeRiaElampTwEQF1dcxiyk4POUv8_AnkqS1Fi6rzn2zkBr0WNCbVBjewE43kG-0ef_CTyvHbovxebjOW1k98iOJsAWwUvp-Ewsjt2y5rKYkov4eUshZxnmQA2B73TV7lZiT8hUwMuvHEj00-u1LCr6h9VOOYCdLpbJ_c5NIkJFNjhu5BDMXnlYhnp-FUdXtK71NkOXqzvip97lJYb5qNx9xWlZSLT6K3z-bEryScCl4fRFXvFuODcmueAtrRE0NJ1SOlGvp7T3PKs_kb30T60WLeABHeB66VnPCLaE8eoQ9mE1-4ThoM5KtzJsx79Es5btW54ltifTazsfGq_zsA8B1vJ9A4BgwZYSp1Q-KtnWvPJMLQYU1qT9fS5EiTUytC_IcEuhhHoCnMeQkoXKBmdjLS-UgRxqoFm9gglrSsKz-l918I8yfGFE2v53rzelvWYBGTQdxdgLxMJJuu4wG9zDOYQqit4lmiruMLYr_rxVFIreeQWj_Db2FJn1rZt2VDCCHZ-_x4r2ICFAlwE3hk6ybA3bfejYYFhd9nnE0kqaktII8j19Hv1rcZmQ3Lo0Hx109o6xasyl3xFKk8FhL1SAaTGNZP62aAQeFyESgelfoPRj_w7YwRNQ4ED0hEgzPaL1R36gKa76Ax2DxYhqbDe2b8FmKUy6i8-5_IREFRi_CkszoT_jHC4TZdvosyADdH0kfflC6WR_5VDxILylPOLgUssabHA2u6-j9C8QetUOvEEKEQcrPJSN3iIp1_T7dtHwhMCycL3QMYZZ9B92cAk58u28qgB9qMgxUj9ka9Xaezl5TGwx2NmILewkqc8CWcMzYi3vQYWcBceJtcnFNPfCCW5_b22Djo2w_MWSiIHkbXpZx2RlugPZa4pqsze5GIkdCr1NHLLn8VluHnEVqqFZ4C64YxqWfeyliMQRpAZHAZ65THbXwRw1273LkfKIxeR4AVNys');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CLOUDBASE_PUBLISHABLE_KEY', 'gAAAAABqaDLNOLkvxmsyV2W9Gif74GSxER2Ypc_kPw-UxGRnbV3Wi8pTJ6ge1cqmf5L0lSGifxsA1YYl8gc4zPyz15AgAzeykzTP7Nfbx8EyWRGnPCxP7E_3m2x_NXFXOhGhUSdVYHnuM3NUS-w8qr_WNjWENgzC4bMSodi71nq_PdxhmEkd2uK_vH1gvDKuK9bvxuDUc23ZfCE7HX73JGRq9-TYGLFokzkQIi3LsfbMOG17QN87bvNtFEmKDOtKUlGS8ka3OoZyb8pP_u4Ws_Z-xT_rVaToIwmPXVxrj1WFKqDDxNIIMr9IVEdlshRk0i1beZn3HA9vx0UQ-yn_tk18p0zltD6oFY8L7EDhrdf_-xidWnXodXWFpvPaxncqsO8FVrqTu3kK1MD8w3BuKUrSz1YmqGYA0u5hcqHI14IZuJlGlach7RtzUKuXgGmCvV8dqgx77yfYDS6TuJK271IiCZN0wu6WO8N6pmyxTdhJXyxQHKm2KKAUQ1Od5XNw7p0MMNAPFaVx5EYj_PZ_FKeFXjZ_T1kNPuPCIE3UdUeXMr1xHhis0C_gfZ3ST3NsEUW_ls_1AgApt_OncI-jgVlUZ71b8wP782CTedDw318yqzfw4if5TAVB5QbaFu3W-gvlrczLLjwI8pzKJDdiju3QtJ86S1Mm3ic3OWXX0bwA28_13x7yvaNd_dcEwwspOgcm9COa0XvB65SxA9Cd7fo5IoztYPeWMv2fIYSm9cglgqkiCaNCWAKPe6tnpicksXAgAvKJ3QRf9z8qyBzheodl7IKNZYw1wHhmGvrICy2XP4mvMR3XHBCUHseP-adNkf3EUoxYRWr9hF8FQ8LoSHlcujUiZdsbJQwYc2zClyL8s6BpAvXSMkIvLGKmGOMTWG6rypmFJFK7TLN1FJR_ZL_o2XDWU5Sw_Rke7545BoE4IYSBL-tTRi1-ewng0Bk1_cBy4k8eUmEeXSFJ4rPzWrvHC3umadk3J4J9OSD9Us-OPKfI0FWrToUhSGLV00GYa9bxfeA6R4GcYEGdDMggcgelVMI846rGYMT8U7SYQdxx-Xnf3-qAikX4dNBab19FLH9tfHEbg7cy5DQtt2GeH_SSrlSl198sOI2nyODYHM2xUYpTdYRddFsQxpkp55kSNDtaP_r9FL-vRrdkLaISyunEGs3i19QouKeIfrqOkKTrVZA-xgvDgXNfqkpu2IAXitQhYZczg3Kd5p2-Ui_i38sYF8M-5wye4Nbfr9zZB0LQKEXAsZGwnJLy3vIFbrdZoVmM6bdQOZTFvILRjXSHpUDegFoiYqMw_Yf8fn2J2Fvswl4JYnBBuvHPLyraNrecbIVng98RqXQxwQOeXaDSF8qz3DcyGGYKW4PRFfHZKweFCXxmfI3Coak=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CONFLUENCE_PASSWORD', 'gAAAAABqaDLN5N1hxRqkOCVbEW6-_03OgBP7vqGfIrM7MwKK92q7iVpcnRTvY5NoA2oCnin2pE01jegptOsRws6mvwCu9Nx9SQ==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CONFLUENCE_PAT', 'gAAAAABqaDLN0_kh3BDhvMbxptH1tn86hYDP-aWGwUVGjxjdKkUfPbZbQ7EkGO8p31rjs1U7mjBOq-y6pgMtFNkmceZkLurblVU7z4Lw2YHpke4JYyWo6efVYcz7gzwOhLOBjnn45FlX');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'CONFLUENCE_USERNAME', 'gAAAAABqaDLNRQPs56YcPnCbtm_enrJw702N0UqFAeNJMyoYinAdpXlZsefQCHTVUvOSgHc97Dq8u8qlX8A04FFN9x89ijT94g==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'DB_PASSWORD', 'gAAAAABqaDLNZZrV0YAZkicS4csXoFcxEuvEzVMovAdJEpTeSXMwbh5B-vfrPTokmo1HlUrTKuDPY5V5qxKwDEj5inzNP2OUjQ==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'DB_USER', 'gAAAAABqaDLN9jDWD2e6TDmnLR2-N9vwnOUqp-4uA-Gpr-Idh7vgar9ywJAh8ZdNyOeENXDGd8aoWdU8O35dqPMRYUECKTw8og==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'EMBED_MODEL', 'gAAAAABqaDLN3sUVBlR2PqxcYd49ln0eqbhIhQmS3LbKipC0VdZvCfRgYzqSkVi5ioMX41wmA_nbTA79-zZ3lXAUJVbflao55A==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'EMBED_PROVIDER', 'gAAAAABqaDLNoohoEo5_eVu5i1LqWOpiHuoHPXIDgTM5SxO_8xOChhP5mZgKC400lCKc4Ag5nxi4vzA83ld2a2U_oeT-HUKBdw==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'FIGMA_KEY', 'gAAAAABqaDLNKca4AMJUfNUDD4nOST8tWs6pBiyZmyAvDpQU_wWRSXutKJknqVs5FX1c_QaQKxD94FLyxXeMKO-YM5ul_90C8gK4vpATPkQSPu1w6HbQDtRE4UNHiDmLrXTM269geN9k');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'FIGMA_PASSWORD', 'gAAAAABqaDLNuvGtEj6IGXQNlFMfixbuTbsJd42ZeONb8HmZQdsdKx7B6nuJr6tNZ9v5Vxw8anKkIG3OrherOBg-DxiyonbW3w==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'FIGMA_USERNAME', 'gAAAAABqaDLNPltoUsrpStnqHflWllVpRC9nFith92MyWagulcyf05geanqGvFBEFZTlgSUD00miFLYvVW26fscYwkYMrtuNOBFC8i_zqAiXH0y7JcmkqHQ=');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TARGET_PASSWORD', 'gAAAAABqaDLNpwODqcvPx9vFdZvcDm1ZkWOVLJz1_dTKp6RzCXlXwh5K1zcYTr_yk0hpjBl73EVaOIwT2ol3P4UnkiQGx8RyWg==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TARGET_USERNAME', 'gAAAAABqaDLN9fCE3e538hNjw9iuFCfAYHeMw7_z9v0KR6ztcp8KDy9J-ZiooVYIECuY_vKNnCEKZ-iCEBmypWSFRs1xiCSLAA==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TOKENAPI_API_BASE_URL', 'gAAAAABqaDLN6yTmEGh91OQMKbf4tzTEpUeHy0pzag1-tQqLDvU9MtMANC245i9mKvdHZ3SNJA1Sx_DEZsHKrmZPOBr3H4JLX3kM9bWYFQ3Ethxm3nHFLhvIif0zBzgvbKau4vLcN2Zt');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TOKENAPI_API_KEY', 'gAAAAABqaDLNyN3BhF2YGxVczlY81-tHN0AO2VyWK4agvlB1wnXgjwaNYbwFuHCK5C0d7IOb4QwFE9cMcMD-ZQP-GN2fo7CfPfHEuTfXeKfQaz36SUnBVXkimel-OrOTlwQFAQaHFHzgffQNiwWfyN3Om5M0QropeQ==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TOKENAPI_API_MODEL', 'gAAAAABqaDLNDwXqf57kPIjsFe8GxiLcLM4p0PzOGC3PkQJhFeQG9FFBMQTdKOa7x15t4_klrPqugdKXV91gTL0omeOxhT_uYg==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'TOKENAPI_VERIFICATION_MODEL', 'gAAAAABqaDLNoVIFmauURh4nFWa-L9BRbknDloioqiCfdHwvtZ8LvzyF9ZSehlc2HRHtdcDg3z41Jo-b3xyVxwHskO5E1CSaFQ==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'VERIFICATION_MODEL', 'gAAAAABqaDLNbMGJEJy-HdDaSVYnfyWTxmFCtDtRzjUJ2JEi3FbZWbcNPP4Pr_F4qsJfGG7GIVj9cEInE1D7g3iVCyZyxssKBA==');
INSERT IGNORE INTO `user_credentials_v2` (`user_id`, `project`, `key`, `value`) VALUES ('admin', '*', 'VISION_MODEL', 'gAAAAABqaDLNn5ceaEgYN3GQ9C67ETFUUmfID9XZrgV-D2rH_SWt-VT6z2K4u6dHJoLK9qdV1Nnm7tZe8t5__v_RS7d-rJIcJA==');
-- 某SPA系统项目级凭据（已移除 — 2026-08-12 合规清理）

-- ============================================================================
-- 第41域：prompt_templates 重构 — DB 唯一事实源（2026-07-14）
-- ============================================================================
-- 1. 加 category/description/is_seed 字段（已存在的库需 ALTER）
-- 2. 删除 project_prompt_overrides 表（不再支持项目级覆盖）
-- 3. 种子模板由 src/core/prompt_seeds.py 的 seed_if_empty() 写入，不在 DDL 硬编码
ALTER TABLE `prompt_templates` ADD COLUMN `category` VARCHAR(64) NOT NULL DEFAULT '其他' COMMENT '分类: Step2 需求拆解 / Step2 需求去重 / Step3 用例建模 / Step4 浏览器操作 / 质量校验 / 自定义';
ALTER TABLE `prompt_templates` ADD COLUMN `description` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '模板用途描述';
ALTER TABLE `prompt_templates` ADD COLUMN `is_seed` TINYINT NOT NULL DEFAULT 0 COMMENT '是否种子模板(1=系统内置不可删除可恢复默认, 0=用户自定义)';

-- 删除 project_prompt_overrides 表（幂等：IF EXISTS 避免新库报错）
DROP TABLE IF EXISTS `project_prompt_overrides`;

-- ============================================================================
-- 指标阈值表（2026-07-15）— 通用指标告警体系
-- ============================================================================
-- 系统级配置，按 metric_type + metric_name 粒度配置阈值
-- compare_op 支持: >= / <= / == / > / <
-- level: normal / warning / error / critical
-- 前端从 /api/metrics-summary 读取 alerts 字段渲染徽章
CREATE TABLE IF NOT EXISTS `metric_thresholds` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT,
    `metric_type`     VARCHAR(64)  NOT NULL COMMENT '指标类型（如 cdp_browser / ai_overall / fusion）',
    `metric_name`     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '指标名称（如 attach_count / pass_rate）',
    `field`           VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'fetcher 返回的字段名（如 attach_success_rate / pass_rate）',
    `warn_threshold`  DOUBLE       NULL COMMENT '警告阈值',
    `error_threshold` DOUBLE       NULL COMMENT '错误阈值',
    `critical_threshold` DOUBLE    NULL COMMENT '严重阈值',
    `compare_op`      VARCHAR(4)   NOT NULL DEFAULT '<' COMMENT '比较运算符: >= / <= / == / > / <',
    `enabled`         TINYINT      NOT NULL DEFAULT 1 COMMENT '是否启用',
    `description`     VARCHAR(255) NOT NULL DEFAULT '' COMMENT '阈值描述',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_threshold` (`metric_type`, `metric_name`, `field`),
    INDEX `idx_threshold_type` (`metric_type`, `enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指标阈值配置 — 通用告警体系';

-- 默认阈值种子数据（CDP 浏览器域）
INSERT IGNORE INTO `metric_thresholds` (`metric_type`, `metric_name`, `field`, `warn_threshold`, `error_threshold`, `critical_threshold`, `compare_op`, `description`) VALUES
    ('cdp_browser', 'attach', 'attach_success_rate', 90, 50, 0, '<', 'CDP 连接成功率(%)：≥90正常 / 50-90警告 / <50错误 / 0严重'),
    ('cdp_browser', 'case', 'pass_rate', 80, 50, 0, '<', 'CDP 用例通过率(%)：≥80正常 / 50-80警告 / <50错误 / 0严重'),
    ('cdp_browser', 'error', 'error_count', 1, 5, 10, '>', 'CDP 错误数：0正常 / 1-4警告 / 5-9错误 / ≥10严重'),
    ('cdp_browser', 'connect_duration', 'connect_duration_ms_avg', 1000, 3000, 5000, '>', 'CDP 平均连接耗时(ms)：≤1000正常 / 1000-3000警告 / 3000-5000错误 / >5000严重'),
    ('cdp_browser', 'exec_duration', 'exec_duration_ms_avg', 5000, 15000, 30000, '>', 'CDP 平均执行耗时(ms)：≤5000正常 / 5000-15000警告 / 15000-30000错误 / >30000严重');

-- ============================================================================
-- 第42域：P0-1 导航图 v2（nav_graph）— 真实爬取结构化导航图，替纸地图 system_nav_maps
-- 落点: PROJECT_CONTEXT.md §2.1 批 A；FK 引用 project_versions(project, version_label) ON DELETE CASCADE
-- ============================================================================

CREATE TABLE IF NOT EXISTS `nav_graph_nodes` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '节点自增ID',
    `project`       VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_label` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '版本标签 → project_versions.version_label',
    `page_url`      VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '完整页面URL（含 hash/query，Fix6 去重键）',
    `page_path`     VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '页面路径（path 部分，仅兜底去重）',
    `page_title`    VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '页面标题',
    `element_count` INT          NOT NULL DEFAULT 0 COMMENT '该页可交互元素数',
    `page_elements` JSON         NULL     COMMENT '★P0-3b: 结构化页面元素（json: {buttons,inputs,links,selects,forms}），替 system_nav_maps.nav_json.page_elements',
    `snapshot_id`   BIGINT       NULL     COMMENT '★P0-1 已废弃(2026-08-05): 原关联 page_dom_snapshots.id，该表已 DROP；写侧固定 NULL，勿再使用',
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

-- ============================================================================
-- 第43域：P0-1 批B 预言层缓存（op_cache_v2 + 校准样本 + 黄金集）
-- 落点: PROJECT_CONTEXT.md §2.1 批B；无 FK（version_id=0=legacy 允许跨版本复用）
-- ============================================================================

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

CREATE TABLE IF NOT EXISTS `golden_set` (
    `id`                  BIGINT       NOT NULL AUTO_INCREMENT COMMENT '条目ID',
    `project`             VARCHAR(128) NOT NULL COMMENT '项目名',
    `case_id`             VARCHAR(128) NOT NULL DEFAULT '' COMMENT '★P2-12接入: 关联用例ID → test_cases.case_id（空=按scenario匹配）',
    `scenario`            VARCHAR(512) NOT NULL DEFAULT '' COMMENT '场景描述',
    `expected_verdict`    VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '期望结论: PASS/FAIL',
    `expected_confidence` DECIMAL(5,4) NOT NULL DEFAULT 0 COMMENT '期望置信度 0~1',
    `notes`               TEXT         NULL     COMMENT '备注',
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_golden_project` (`project`),
    INDEX `idx_golden_case` (`project`, `case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='引擎自评估黄金集';

-- ============================================================================
-- 第44域：P0-1 批C 预言层产出（oracle/视觉复核/多模态RAG/三车道 + 纸地图废弃标记）
-- 落点: PROJECT_CONTEXT.md §2.1 批C + §2.2 ALTER；无 FK（version_id=0=legacy）
-- ============================================================================

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
    `case_id` varchar(64) NOT NULL DEFAULT '' COMMENT '关联 executions.case_id — D6M 归属键',
    `run_id` varchar(128) NOT NULL DEFAULT '' COMMENT '关联 executions.run_id — 同 run 聚合键',
    PRIMARY KEY (`id`),
    INDEX `idx_oracle_exec` (`execution_id`, `oracle_type`),
    INDEX `idx_oracle_project` (`project`, `version_id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每次执行的三种 oracle 结论';

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
    `version_id`  BIGINT       NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
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

-- ★ P0-3b: nav_graph_nodes 加结构化 page_elements 列（替 system_nav_maps.nav_json.page_elements）
--   根因: read_nav_graph_nodes 只返回 element_count(数量)，无 buttons/inputs/links 列表
--         → _rule_extractor 无法切换读 nav_graph → 仍读 system_nav_maps → P0-3b 读取侧未闭环
--   修复: 加 JSON 列存结构化元素，write_nav_graph 写入，read 返回，_rule_extractor 切换读取
--   幂等: db_schema.py 通过 error code 1060 跳过已存在列
ALTER TABLE `nav_graph_nodes` ADD COLUMN `page_elements` JSON NULL COMMENT '★P0-3b: 结构化页面元素（json: {buttons,inputs,links,selects,forms}）';
-- ★ P2-12 接入: golden_set 加 case_id 字段（关联 executions.case_id）
ALTER TABLE `golden_set` ADD COLUMN `case_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '★P2-12接入: 关联用例ID（空=按scenario匹配）';
ALTER TABLE `golden_set` ADD INDEX `idx_golden_case` (`project`, `case_id`);

-- ★ 全链路收尾: users 表 status 索引优化登录查询性能
ALTER TABLE `users` ADD INDEX `idx_users_status` (`status`);

-- ★ 登录自动恢复上次项目（2026-08-31）: 用户级记忆最近选择，避免每次登录弹回项目列表
ALTER TABLE `users` ADD COLUMN `last_project` VARCHAR(128) NOT NULL DEFAULT '' COMMENT '用户上次选择的项目（登录后自动恢复，空=未选过）' AFTER `status`;

-- ============================================================================
-- 第45域：流水线强化专项 T0 — case_upload_history（游离表入 schema，唯一事实源）
-- 落点: 流水线强化专项 design §3.2 / PRD §P0-1
-- 说明: 与 src/backend/case_upload_history.py 运行时兜底定义逐字段一致；
--       兜底建表仅作兼容层（IF NOT EXISTS，不覆盖本 schema 定义）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS `case_upload_history` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '上传记录ID',
    `filename`     VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传文件名',
    `target_key`   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '被测系统 key → target_cfgs 注册表',
    `version`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '被测系统版本',
    `uploader`     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '上传人（用户ID）',
    `uploaded_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    `matched`      TINYINT      NOT NULL DEFAULT 1 COMMENT 'target+version 匹配是否通过 1=通过/0=拒绝',
    `policy`       VARCHAR(16)  NOT NULL DEFAULT 'reject' COMMENT '不匹配处置策略 reject/warn/force',
    `message`      TEXT         NULL COMMENT '匹配校验消息',
    `exec_summary` TEXT         NULL COMMENT '执行结果摘要（all_pass/cases/probes）',
    PRIMARY KEY (`id`),
    INDEX `idx_cuh_target` (`target_key`),
    INDEX `idx_cuh_uploader` (`uploader`),
    INDEX `idx_cuh_time` (`uploaded_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用例上传历史表';

-- ============================================================================
-- 第46域：流水线强化专项 T3 — 交互式需求澄清（clarification_sessions + answers）
-- 落点: 流水线强化专项 design §3.2 / PRD §P1-2（Q5 拍板：独立表，避免污染需求主表）
-- 说明: session 状态机 open → answered → released；open → skipped（保持阻塞）。
--       backfill_json 存回答回填后的 operation/condition/expected_feedback。
-- ============================================================================

CREATE TABLE IF NOT EXISTS `clarification_sessions` (
    `id`                 BIGINT       NOT NULL AUTO_INCREMENT COMMENT '会话自增ID',
    `session_id`         VARCHAR(64)  NOT NULL COMMENT '会话ID（如 CLR-<run_id>-<seq>）',
    `project`            VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
    `version_id`         VARCHAR(128) NOT NULL DEFAULT '' COMMENT '项目版本ID（兼容字符串形态，对齐流水线上下文）',
    `run_id`             VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '流水线运行ID（pipeline_checkpoints.run_id）',
    `req_id`             VARCHAR(128) NOT NULL DEFAULT '' COMMENT '关联需求编号 → requirements.req_id',
    `ambiguity_item_id`  VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '歧义项ID（ambiguity_report items[].ambiguity_id）',
    `ambiguity_type`     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '歧义类型（六类规则枚举）',
    `severity`           VARCHAR(16)  NOT NULL DEFAULT 'medium' COMMENT '严重度 high/medium/low',
    `blocking`           TINYINT      NOT NULL DEFAULT 0 COMMENT '是否阻断生成（high=1）',
    `status`             VARCHAR(16)  NOT NULL DEFAULT 'open' COMMENT '状态: open/answered/released/skipped',
    `clarify_questions`  JSON         NOT NULL COMMENT '澄清问题列表（json数组）',
    `backfill_json`      JSON         NULL     COMMENT '回填结果（json: 更新后的 operation/condition/expected_feedback）',
    `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_clr_session_id` (`session_id`),
    INDEX `idx_clr_session` (`project`, `run_id`, `req_id`),
    INDEX `idx_clr_status` (`project`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='需求澄清会话表 — 每条高危歧义项一个会话，支持多轮问答与状态机';

CREATE TABLE IF NOT EXISTS `clarification_answers` (
    `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '回答自增ID',
    `session_id`     VARCHAR(64)  NOT NULL COMMENT '→ clarification_sessions.session_id',
    `question_index` INT          NOT NULL COMMENT '问题序号（从0开始）',
    `question`       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '问题原文快照',
    `answer`         VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '用户回答',
    `answered_by`    VARCHAR(128) NOT NULL DEFAULT '' COMMENT '回答人（当前登录用户ID）',
    `answered_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '回答时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_clr_ans` (`session_id`, `question_index`),
    INDEX `idx_clr_ans_session` (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='需求澄清回答表 — 一次回答一行，多轮可追溯';

-- ============================================================================
-- 第46域附：T2 方法论白名单校验 — case_meta 增量列（幂等，db_schema 按 1060 跳过）
-- 落点: 流水线强化专项 design §3.2 / PRD §P1-1（Q4 拍板：低危修正+记录、高危标记+阻断）
-- ============================================================================
ALTER TABLE `case_meta` ADD COLUMN `method_valid` TINYINT NOT NULL DEFAULT 1 COMMENT '★T2: 方法论标签校验是否通过(1=通过/0=非法已处置)' AFTER `strictness_score`;
ALTER TABLE `case_meta` ADD COLUMN `method_fix_note` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '★T2: 方法论标签修正/标记说明（如 白名单外test_method=XX 已就近映射为EP）' AFTER `method_valid`;


-- ============================================================
-- 第 N+1 域：审计补录（2026-09-22 schema.sql vs 线上库双向比对）
-- 以下 3 表由运行时模块自建，此前漏纳入 schema.sql 统一管理，现回填：
--   navigation_match_logs   ← src/pipeline/utils/nav_match_logger.py
--   plugin_interventions    ← src/pipeline/steps/step4/plugins/plugin_manager.py
--   user_preferences        ← src/backend/routes/pipeline_config/_auto_apply.py
-- 结构取自线上库 SHOW CREATE TABLE（去除 AUTO_INCREMENT=N 计数器）。
-- ============================================================


CREATE TABLE IF NOT EXISTS `navigation_match_logs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `project` varchar(64) NOT NULL DEFAULT '',
  `version_id` bigint NOT NULL DEFAULT '0' COMMENT '项目版本ID → project_versions.id，0=legacy',
  `case_id` varchar(128) NOT NULL DEFAULT '',
  `req_text` varchar(512) NOT NULL DEFAULT '',
  `match_level` varchar(16) NOT NULL DEFAULT 'none',
  `llm_used` tinyint NOT NULL DEFAULT '0',
  `llm_elapsed_ms` int NOT NULL DEFAULT '0',
  `llm_success` tinyint NOT NULL DEFAULT '0',
  `llm_raw_response` varchar(512) NOT NULL DEFAULT '',
  `llm_menu` varchar(128) NOT NULL DEFAULT '',
  `menu_hit` tinyint NOT NULL DEFAULT '0',
  `calibration_status` varchar(16) NOT NULL DEFAULT 'pending',
  `corrected_menu` varchar(128) NOT NULL DEFAULT '',
  `calibrated_by` varchar(64) NOT NULL DEFAULT '',
  `calibrated_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_project` (`project`),
  KEY `idx_calibration` (`calibration_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `plugin_interventions` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '记录ID',
  `project` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '项目名',
  `case_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '测试用例编号',
  `intent_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '意图ID',
  `plugin_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '插件名',
  `hook` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '钩子类型',
  `intervention_data` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '干预结果JSON',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
  PRIMARY KEY (`id`),
  KEY `idx_plugin_interv_case` (`project`,`case_id`),
  KEY `idx_plugin_interv_plugin` (`plugin_name`,`hook`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='插件干预记录 — 各钩子返回的实质性干预结果持久化';

CREATE TABLE IF NOT EXISTS `user_preferences` (
  `user_id` varchar(128) NOT NULL,
  `pref_key` varchar(128) NOT NULL,
  `pref_value` text NOT NULL,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`,`pref_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
