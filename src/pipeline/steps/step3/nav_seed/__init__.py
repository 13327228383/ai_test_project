# -*- coding: utf-8 -*-
"""nav_seed：按 target_cfg["nav_seed"] 动态加载该靶子自带的导航数据种子。

为什么: 种子内容是靶子专属知识，随靶子放在其自身目录里（file），引擎只负责
加载与调用；未声明 nav_seed 的靶子返回 {"seeded": False} 并给出 reason，
不静默通过、也不回落到任何靶子的种子。
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[5]


def _load_seeder(entry: dict):
    """按 {"file","fn"} 加载种子函数；file 相对项目根。"""
    file_rel = entry.get("file")
    fn = entry.get("fn")
    if not file_rel or not fn:
        raise ValueError(f"nav_seed 需声明 file+fn，实际={entry!r}")
    path = Path(file_rel)
    if not path.is_absolute():
        path = _ROOT / path
    spec = importlib.util.spec_from_file_location(
        f"_nav_seed_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"nav_seed 文件无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, fn)


def seed_nav_graph(target_key: str) -> dict:
    """按 target_cfg["nav_seed"] 注入导航数据。

    Returns:
        dict：target_key / seeded / reason（seeded=False 时）；
              真实注入时附带种子器返回值（如 write_result / has_nav_data）。
    """
    try:
        cfg = get_target_cfg(target_key)
    except KeyError:
        _log.warning("[seed_nav_graph] 未知靶子 target_key=%s", target_key)
        return {
            "target_key": target_key,
            "seeded": False,
            "reason": f"未知靶子 target_key={target_key!r}",
        }

    entry = cfg.get("nav_seed") or {}
    if not entry:
        return {
            "target_key": target_key,
            "seeded": False,
            "reason": f"no nav_seed for target_key={target_key!r}",
        }

    try:
        seed_fn = _load_seeder(entry)
        summary = seed_fn(target_key)
        summary["target_key"] = target_key
        summary["seeded"] = True
        return summary
    except Exception as _e:
        _log.error("[seed_nav_graph] %s nav_seed 执行失败: %s", target_key, _e)
        return {
            "target_key": target_key,
            "seeded": False,
            "reason": f"nav_seed 执行失败: {_e}",
        }
