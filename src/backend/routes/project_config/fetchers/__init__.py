# -*- coding: utf-8 -*-
"""
project_config.fetchers — 项目配置路由各功能模块

将原 project_config.py 中的路由函数和常量拆分为独立模块：
- _constants.py — 所有常量和 _rebuild_valid_keys_cache, _coerce_config_value
- _read.py     — api_get_config (只读路由)
- _write.py    — api_save_config (写入路由)
- _history.py  — api_config_history, api_config_rollback (历史/回滚路由)
"""
