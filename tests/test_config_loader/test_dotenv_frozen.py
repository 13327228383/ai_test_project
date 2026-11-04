# -*- coding: utf-8 -*-
"""回归测试：.env 默认路径在 frozen(打包exe) 与源码态下正确选择。

背景（2026-09-03）：PyInstaller 打包后 __file__ 指向 _MEIPASS 内部，
原 _project_root() 上溯 4 级越界 → 找不到 .env → STORAGE_MYSQL_PORT 为
None 导致 exe 启动 int(None) 崩。修复引入 _default_env_path()：frozen 时
回落 exe 所在目录/.env（绿色分发根，可写可改）。
"""
import sys
from pathlib import Path

from src.core.config_loader import _dotenv as d


def test_source_mode_defaults_to_project_root(monkeypatch):
    """源码态：.env 定位到项目根（上溯 4 级）。"""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert d._default_env_path() == d._project_root() / ".env"


def test_frozen_mode_defaults_to_exe_dir(monkeypatch):
    """frozen 态：.env 定位到 exe 所在目录（绿色分发根）。"""
    fake_exe = Path("C:/pkg/SmartShopEngine/SmartShopEngine.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert d._default_env_path() == Path("C:/pkg/SmartShopEngine") / ".env"


def test_load_dotenv_reads_explicit_path(tmp_path):
    """显式传 env_path 时仍能正确加载到 os.environ（setdefault）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("STORAGE_MYSQL_PORT=3306\nFLASK_SECRET_KEY='secret'\n", encoding="utf-8")
    d._loaded = False
    n = d.load_dotenv_fail_open(env_file)
    assert n == 2
    assert __import__("os").environ.get("STORAGE_MYSQL_PORT") == "3306"
    d._loaded = False