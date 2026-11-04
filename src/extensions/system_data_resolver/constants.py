# -*- coding: utf-8 -*-
"""SystemDataResolver — 常量与匹配模式定义。"""


class ResolverConstants:
    """列名匹配模式、排除规则、枚举值等常量定义（均不区分大小写）。"""

    CACHE_FILE_NAME = "system_data_catalog.json"
    CACHE_MAX_AGE_HOURS = 2160  # 90天，需要刷新时用户手动强制拉取即可

    # ★ 通用枚举值：默认空，各项目通过 config.custom_enum_fields 注入
    # 格式: {"表.字段": "值1, 值2, ..."}
    # 示例: config.custom_enum_fields = {"ORDER.STATUS": "待支付,已支付,已完成,已取消"}
    DEFAULT_ENUM_FIELDS: dict[str, str] = {}

    # ═══════════════════════════════════════════════════════════════
    # 列名匹配模式（均不区分大小写）
    # ═══════════════════════════════════════════════════════════════

    # 用户表：需同时命中"用户名类"和"密码类"列
    USER_NAME_PATS = [
        "user_name",
        "username",
        "account",
        "login_name",
        "nickname",
        "login",
        "user",
        "name",
    ]
    USER_PWD_PATS = ["password", "pwd", "passwd", "secret", "pass", "user_pwd"]

    # 角色表：需有角色名称列
    ROLE_NAME_PATS = ["role_name", "rolename", "name", "role"]
    ROLE_TABLE_PATS = ["role", "permission", "auth", "group"]

    # 用户→角色外键
    ROLE_FK_PATS = ["role_id", "roleid", "role_code", "group_id"]

    # 业务实体表：有 *_CODE 类字段的表
    ENTITY_CODE_PATS = ["*_code", "*_no", "*_id", "code", "no"]
    ENTITY_NAME_PATS = ["*_name", "name"]

    # 排除的表（日志/审计/历史/临时/中间表）
    EXCLUDE_TABLE_KW = [
        "log_",
        "_log",
        "audit_",
        "_audit",
        "_history",
        "_backup",
        "tmp_",
        "_tmp",
        "temp_",
        "_temp",
        "_temporary",
        "qrtz_",
        "scheduler_",
        "flyway_",
        "_bak",
        "_archive",
        "undo_",
        "_copy",
        "_old",
        "_deprecated",
    ]

    # 系统表前缀（不归入业务实体）
    SYS_TABLE_PREFIXES = [
        "sys_",
        "t_sys",
        "information_schema",
        "pg_",
        "sqlite_",
    ]
