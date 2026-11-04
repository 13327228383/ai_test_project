# -*- coding: utf-8 -*-
"""
DbCoreBaseMixin: __init__, reload_config, read_schema_file
"""

from pathlib import Path  # 面向对象路径
import logging

_log = logging.getLogger(__name__)

from src.core.config_loader import config  # 全局配置单例


class _DbCoreBaseMixin:
    """基础方法：配置初始化、重载、表结构文档读取。"""

    def __init__(self) -> None:
        """初始化数据库执行器，从全局配置读取数据库连接参数。"""
        self.reload_config()

    def reload_config(self) -> None:
        """重新加载配置（项目切换后调用），确保使用最新的数据库连接参数。"""
        self._db_type = str(config.db_type).strip()
        self._db_host = config.db_host
        self._db_port = config.db_port
        self._db_user = config.db_user
        self._db_pass = config.db_password
        self._db_name = config.db_name
        self._db_file = config.db_file
        self._auto_mode = config.db_auto_mode

    def read_schema_file(self, schema_path: str = "db_schema.md") -> str:
        """
        读取数据库表结构文档。

        参数：
            schema_path: 文档路径（相对于项目根目录）

        返回：
            文档内容字符串
        """
        root = Path(__file__).resolve().parent.parent.parent.parent  # 项目根目录
        full_path = root / schema_path

        if not full_path.is_file():
            raise FileNotFoundError(
                "数据库表结构文档不存在: %s\n"
                "请先创建 db_schema.md 并填入表结构信息" % full_path
            )

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
