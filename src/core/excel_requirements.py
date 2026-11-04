# -*- coding: utf-8 -*-
"""
需求拆解表 CRUD 工具函数
==========================
从 ExcelHandler 类中提取的需求拆解表操作方法，转为独立函数。
所有方法通过 requirement_store 操作 MySQL，不直接操作 Excel。
"""

import logging
import re
from src.core.guarded import guard

_log = logging.getLogger(__name__)

# 需求 ID 的权威编号格式（下游用例 ID 形如 TC-REQ-007-*，依赖 REQ-<数字> 连续）
_REQ_ID_RE = re.compile(r"^REQ-(\d+)$")


def _next_manual_req_id() -> str:
    """为「人工添加」的需求分配下一个 REQ-<数字>（项目内最大编号 +1）。

    根因：UI 的「添加遗漏需求」只提交 {description, module}，不带 req_id；
    而 upsert_requirements 对无 req_id 的行会静默跳过 —— 导致人工补充的需求
    丢失，且接口仍回 ok（假成功）。此处按既有编号规则补齐唯一 ID，使人工输入
    真正落库（MySQL 唯一事实源）。
    """
    from src.pipeline.utils.requirement_store import requirement_store

    max_n = 0
    try:
        for r in requirement_store.list_requirements() or []:
            m = _REQ_ID_RE.match(str(r.get("req_id", "") or "").strip())
            if m:
                max_n = max(max_n, int(m.group(1)))
    except Exception as e:  # noqa: BLE001 —— 读取失败则退回 REQ-000，绝不静默丢数据
        _log.warning("[ExcelHandler] 读取现有 req_id 失败，退回 REQ-000 起始: %s", str(e)[:80])
    return "REQ-%03d" % (max_n + 1)


def write_requirements(requirements):
    """写入需求列表到 MySQL requirements 表。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        if not requirement_store.upsert_requirements(requirements):
            raise RuntimeError("requirements 写入 MySQL 失败")
    except Exception as e:
        _log.info("[ExcelHandler] requirements 写入 MySQL 失败: %s", str(e)[:80])
        return 0
    count = len(requirements)
    _log.info("[MySQL] requirements 已写入: %d 条需求", count)
    return count


def read_requirements_from_excel(req_path, read_sheet_fn, req_columns):
    """【导入源专用】仅用于显式从 Excel 导入源读取，不作为运行时事实源。

    Args:
        req_path: Excel 文件路径
        read_sheet_fn: 读取 sheet 的回调函数（替代 self._read_sheet）
        req_columns: REQ_COLUMNS 列定义
    """
    from src.core.excel_handler import _check_file_size
    import openpyxl

    if not req_path.is_file():
        return []
    _check_file_size(req_path)
    wb = openpyxl.load_workbook(str(req_path))
    try:
        return read_sheet_fn(wb, "需求拆解", req_columns)
    finally:
        wb.close()


def read_requirements():
    """★ MySQL-only：从 requirements 表读取。Excel 仅作导入源导出物，不再参与主运行链路兜底。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        return requirement_store.list_requirements()
    except Exception as e:
        _log.info("[ExcelHandler] requirements 从 MySQL 读取失败: %s", str(e)[:80])
        return []


def get_ungenerated_requirements(read_requirements_fn):
    """获取未生成测试用例的需求列表。

    Args:
        read_requirements_fn: 读取需求的回调函数（替代 self.read_requirements()）
    """
    all_reqs = read_requirements_fn()
    result = []
    for r in all_reqs:
        val = r.get("is_generated", False)
        if isinstance(val, bool):
            is_gen = val
        elif isinstance(val, str):
            is_gen = val.strip().lower() in ("true", "1", "yes")
        elif isinstance(val, (int, float)):
            is_gen = bool(val) and val != 0
        else:
            is_gen = False
        if not is_gen:
            result.append(r)
    return result


def reset_all_is_generated():
    """★ MySQL-only：重置当前项目全部需求的 is_generated，不再操作 Excel。"""
    with guard("reset_all_is_generated: from src.pipeline.utils.requirement_", log=_log, level=logging.INFO):
        from src.pipeline.utils.requirement_store import requirement_store

        reqs = requirement_store.list_requirements()
        for r in reqs:
            r["is_generated"] = False
        if requirement_store.upsert_requirements(reqs):
            _log.info("[MySQL] requirements.is_generated 已重置: %d 条", len(reqs))


def update_is_generated(req_id, value=True):
    """★ MySQL-only：更新单条需求的 is_generated，不再操作 Excel。"""
    with guard("update_is_generated: from src.pipeline.utils.requirement_", log=_log, level=logging.INFO):
        from src.pipeline.utils.requirement_store import requirement_store

        if requirement_store.update_requirement(req_id, {"is_generated": value}):
            _log.info("[MySQL] requirement.is_generated 已更新: %s → %s", req_id, value)


def req_col_index(attr_name: str, req_columns) -> int:
    """根据属性名查找 REQ_COLUMNS 中的列索引。

    Args:
        attr_name: 属性名
        req_columns: REQ_COLUMNS 列定义列表
    """
    for i, (name, _, _) in enumerate(req_columns):
        if name == attr_name:
            return i
    return -1


def mark_requirement_intents_modeled(req_id):
    """★ MySQL-only：标记需求已生成测试用例，不再操作 Excel。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        ok = requirement_store.update_requirement(req_id, {"is_generated": True})
        if ok:
            _log.info("[MySQL] requirement.is_generated 已标记: %s", req_id)
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] requirements 状态写入 MySQL 失败: %s", str(e)[:80])
        return False


def update_review_status(req_id: str, status: str) -> bool:
    """★ MySQL-only：更新单条需求的审核状态，不再操作 Excel。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        ok = requirement_store.update_requirement(req_id, {"review_status": status})
        if ok:
            _log.info(
                "[MySQL] requirement.review_status 已更新: %s → %s", req_id, status
            )
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] 审核状态更新失败: %s", e)
        return False


def update_requirement(req_id: str, updates: dict) -> bool:
    """★ MySQL-only：修改需求条目的字段值，不再操作 Excel。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        ok = requirement_store.update_requirement(req_id, updates)
        if ok:
            _log.info(
                "[MySQL] requirement 已更新: %s fields=%s",
                req_id,
                ",".join(list(updates.keys())[:3]),
            )
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] 需求修改失败: %s", e)
        return False


def delete_requirement(req_id: str) -> bool:
    """★ MySQL-only：从 requirements 表删除一条需求，不再操作 Excel。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        ok = requirement_store.delete_requirement(req_id)
        if ok:
            _log.info("[MySQL] requirement 已删除: %s", req_id)
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] 需求删除失败: %s", e)
        return False


def add_requirement(data: dict) -> bool:
    """★ MySQL-only：向 requirements 表追加需求，不再操作 Excel。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        row_data = dict(data)
        row_data.setdefault("review_status", "approved")
        if not str(row_data.get("req_id", "") or "").strip():
            row_data["req_id"] = _next_manual_req_id()
        ok = requirement_store.upsert_requirements([row_data])
        if ok:
            _log.info("[MySQL] requirement 已新增: %s", row_data.get("req_id", ""))
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] 需求添加失败: %s", e)
        return False
