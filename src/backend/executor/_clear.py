# -*- coding: utf-8 -*-
"""清除中间产物 —— 薄委托层，清空范围由 ``src.core.project_purge`` 从 schema 派生。

为什么保留这层: ``/api/clear-files`` 与 Step1 强制覆盖都经此入口，
签名与返回值 (cleared, errors) 是既有契约；具体删哪些表不再在此维护。
"""

import logging

_log = logging.getLogger(__name__)


def _clear_files(targets=None, file_paths=None, all_derived=False):
    """清除当前项目的中间产物，返回 ``(删除行数, 错误数)``。

    - ``all_derived=True`` 或两者皆空 → 清空全部派生数据（输入态保留）
    - ``file_paths`` → 逐项 ``mysql://table[/sub]`` 精确清除
    - ``targets`` → 兼容旧调用方：每项按同一个 ``mysql://`` 解析规则解释
    """
    from src.core.config_loader import config as _cfg
    from src.core.project_purge import purge_project, purge_specs

    project = getattr(_cfg, "project_name", "") or ""
    if not project:
        _log.warning("[清理] 未选择项目，跳过")
        return 0, 1

    if not all_derived and (file_paths or targets):
        specs = list(file_paths or ()) + list(targets or ())
        result = purge_specs(project, specs)
    else:
        result = purge_project(project)

    _log.info(
        "\n清理完成: 删除 %d 行 / %d 张表, 失败 %d 个 (project=%s)",
        result["total"], len(result["tables"]), len(result["errors"]), project,
    )
    return result["total"], len(result["errors"])
