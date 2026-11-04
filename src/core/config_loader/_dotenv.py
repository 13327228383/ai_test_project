# -*- coding: utf-8 -*-
"""最小 .env 加载器（fail-open，无第三方依赖）

背景（易用性收尾）:
    config_loader 原先只读 os.environ。裸跑 `pytest tests/unit`（不 export
    STORAGE_MYSQL_* 等 6 个变量）时，MySQL 配置为 None → 连接挂死 30min+。
    本模块在 config_loader 初始化最早处自动加载项目根 .env，裸跑也能读到配置。

设计约束:
    - 无新增依赖：实测 python-dotenv 未安装，故手写最小解析器（逐行 KEY=VALUE）。
    - fail-open：.env 缺失 / 解析失败 / 权限异常一律静默跳过，不影响启动。
    - 显式 export 优先：仅 os.environ.setdefault()，已存在的环境变量不被覆盖。
    - 幂等：模块级标志保证整个进程只读取一次文件，重复调用开销为 0。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

_loaded = False


def _project_root() -> Path:
    """项目根目录：本文件位于 src/core/config_loader/ 下，上溯 4 级。"""
    return Path(__file__).resolve().parent.parent.parent.parent


def _default_env_path() -> Path:
    """默认 .env 位置。

    源码态: 项目根 ``<root>/.env``（上溯 4 级定位源码树）。
    frozen 态: ``os.path.dirname(sys.executable)/.env`` —— exe 所在目录。
      原因: PyInstaller 下 ``__file__`` 指向解包目录 ``_MEIPASS`` 内部，
      上溯 4 级会越界得到不存在路径，导致 .env 读不到、配置全为 None、
      ``STORAGE_MYSQL_PORT`` 等 int() 转换崩。通用性: 绿色自包含分发时
      .env 放在 exe 同级目录(即分发根)，用户可直接改/可写，不绑定引擎代码。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return _project_root() / ".env"


def load_dotenv_fail_open(
    env_path: Optional[Union[str, os.PathLike]] = None,
) -> int:
    """加载 .env 到 os.environ（setdefault 语义，不覆盖已有值）。

    Args:
        env_path: .env 文件路径；为 None 时默认取项目根目录 .env。

    Returns:
        实际写入 os.environ 的变量个数。幂等：进程内只解析一次，
        第二次调用返回 0；.env 缺失或解析异常也返回 0（fail-open）。
    """
    global _loaded
    if _loaded:
        return 0
    if env_path is None:
        env_path = _default_env_path()
    try:
        path = Path(env_path)
        if not path.is_file():
            _loaded = True
            return 0
        count = 0
        with open(str(path), "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                # 去掉可选的成对引号（.env 惯例支持 'xxx' / "xxx"）
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                # setdefault：显式 export 的环境变量优先于 .env
                os.environ.setdefault(key, value)
                count += 1
    except Exception:
        # fail-open：任何异常（权限、编码等）都不影响启动
        return 0
    _loaded = True
    return count
