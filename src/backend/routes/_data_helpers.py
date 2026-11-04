# -*- coding: utf-8 -*-
"""
data 路由辅助函数
=================
从 data.py 提取的公共辅助函数，供 data.py（facade）和 routes/ 子模块共同使用。
"""

import logging

_log = logging.getLogger(__name__)


def _check_login_and_project():
    """检查用户已登录且有项目权限。返回 (ok, error_message)。"""
    try:
        from src.core.config_loader import get_current_user
        from src.backend.helpers import _user_can_access_project
        from src.backend.state import _current_project

        uid = get_current_user()
        if not uid:
            return False, "请先登录"
        proj = _current_project()
        if proj and not _user_can_access_project(uid, proj):
            return False, "无权访问当前项目"
        return True, ""
    except Exception as e:
        _log.error("权限校验异常: %s", e, exc_info=True)
        return True, ""


def _current_project():
    """获取当前项目。

    ★ 修复（2026-06-23）：原读 session.get('project')，但实际 session key 为
    'project_name'（见 state._set_current_project），key 不匹配恒返回 None，
    导致 /api/plugin-metrics 和 /api/token-dashboard 返回 400。改用
    state._current_project() 统一来源（已处理 session 恢复 + 全局变量 + config 兜底）。"""
    try:
        from src.backend.state import _current_project as _get_project

        return _get_project() or None
    except Exception as _e:
        _log.debug("当前项目获取失败: %s", _e, exc_info=True)
        return None
