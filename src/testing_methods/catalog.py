# -*- coding: utf-8 -*-
"""测试方法论单一权威目录（18 种，覆盖 Web 应用对标高级 SDET 的用例设计全集）。

★ 根因修复：此前方法论口径分散在四处且相互出入——
  - src/testing_methods/__init__.py 注册 12 类（含 语句/分支/条件覆盖，无安全/Web 新方法）
  - _test_method_rules.py METHOD_CODES 12 码（EP..SI），_test_method_router_sdet 只路由 DT/BV/EP/FD
  - _test_methods.py 清单页 METHOD_NAMES 12 码但语义标签与引擎类不一致
  - integration_test.py 沦为孤儿类，未被任何目录引用
  本模块收敛为**单一权威来源**：_test_method_rules.py 与 _test_methods.py 均从此处读取
  METHOD_CODES / 中文名 / 分类，保证「写库编码 == 规则模板 == 清单展示」三者一致。

编码设计：
  黑盒·经典设计技术 (8)  EP 等价类 · BV 边界值 · CE 因果图 · DT 判定表 · ST 状态迁移 ·
                        OT 正交实验 · EG 错误推测 · FD 场景法
  黑盒·规格/标准 (1)     SD 规范导出
  白盒·结构覆盖 (1)      PC 路径覆盖
  安全 (4)               SX XSS 注入 · SI SQL 注入 · CS CSRF 跨站请求伪造 · AU 越权与权限
  黑盒·Web 专属 (4)      SS 会话管理 · CT 兼容与响应式 · PF 性能 · ET 探索式
合计 18。所有编码均为自由文本 VARCHAR(32)，无数据库迁移。
"""

from __future__ import annotations

# (code, 中文名, 分类, 描述)
# 分类仅允许：black_box / white_box / security（与清单页 CATEGORY_LABELS 对应）
_METHOD_ENTRIES_FULL: tuple[tuple, ...] = (
    ("EP", "等价类划分", "black_box", "将输入域划分为有效/无效等价类，每类取一例覆盖，降低冗余用例"),
    ("BV", "边界值分析", "black_box", "聚焦输入/输出边界值，捕获常见的边界取值错误"),
    ("CE", "因果图", "black_box", "分析输入条件与输出结果的因果逻辑，推导出系统性测试组合"),
    ("DT", "判定表", "black_box", "以条件/动作矩阵覆盖复杂业务规则的全部逻辑组合"),
    ("ST", "状态迁移", "black_box", "基于状态、事件与迁移关系验证状态机流转的正确性"),
    ("OT", "正交实验", "black_box", "用正交表以少量代表性组合覆盖多因素多水平输入"),
    ("EG", "错误推测", "black_box", "依据测试经验与软件直觉推测最易出错的特殊场景"),
    ("FD", "场景法", "black_box", "模拟用户端到端操作，验证主流程与备选流程的正确性"),
    ('SD', '规范导出', 'black_box', '从需求/规格说明逐条导出可执行验收用例'),
    ("PC", "路径覆盖", "white_box", "覆盖程序全部独立执行路径，检查各分支路径结果"),
    ("SX", "XSS注入", "security", "提交恶意脚本载荷，检测跨站脚本(XSS)漏洞"),
    ("SI", "SQL注入", "security", "注入SQL片段，检测SQL注入类安全漏洞"),
    ("CS", "CSRF跨站请求伪造", "security", "校验请求防伪token/同源校验，无token或伪造token的请求被拒绝"),
    ("AU", "越权与权限", "security", "校验角色权限矩阵与越权访问(IDOR/水平/垂直越权)被拦截"),
    ("SS", "会话管理", "black_box", "登录/登出/超时/Token/Cookie 会话生命周期正确（Web 专属）"),
    ("CT", "兼容与响应式", "black_box", "跨浏览器/分辨率/设备视图下页面正常渲染、关键元素可见（对应兼容类用例）"),
    ("PF", "性能", "black_box", "页面加载后核心功能在阈值内可交互、接口响应正常（对应性能类用例）"),
    ("ET", "探索式", "black_box", "基于经验与非预期输入主动探索异常路径，页面不崩溃、无未捕获错误"),
)

# 展示顺序：经典黑盒 → 规 格 → 白盒 → 安全 → Web 专属
METHOD_ORDER: tuple[str, ...] = (
    "EP", "BV", "CE", "DT", "ST", "OT", "EG", "FD", "SD",
    "PC", "SX", "SI", "CS", "AU", "SS", "CT", "PF", "ET",
)

# code -> (name, category, description)
METHOD_CATALOG: dict[str, tuple[str, str, str]] = {
    _code: (_name, _cat, _desc) for _code, _name, _cat, _desc in _METHOD_ENTRIES_FULL
}

METHOD_CODES: tuple[str, ...] = tuple(
    _code for _code in METHOD_ORDER if _code in METHOD_CATALOG
)

CATEGORY_LABELS: dict[str, str] = {
    "black_box": "黑盒",
    "white_box": "白盒",
    "security": "安全",
}

# 「未决定」标记码唯一事实源：这些字符串不是真实方法论编码，一律就近归一为 FD。
# 制造层（case_generator/_test_method_rules.normalize_method_code）与校验层
# （step3/_methodology_validator）共用本集合，禁止各自字面定义（曾漂移：2 项 vs 6 项）。
UNDECIDED_METHOD_MARKERS: frozenset[str] = frozenset(
    {"PLAYWRIGHT", "DEFAULT", "UNDECIDED", "NONE", "UNKNOWN", "AUTO"}
)


__all__ = ["METHOD_CATALOG", "METHOD_CODES", "METHOD_ORDER", "CATEGORY_LABELS",
           "UNDECIDED_METHOD_MARKERS"]