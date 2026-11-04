# -*- coding: utf-8 -*-
"""
case_store.crud — CaseStore 的 CRUD 拆分包

将原 _store_core.py 中 1100+ 行的 CaseStore 按职责拆分为：
- _base.py    — 基础连接、表初始化、虚拟继承、执行状态补齐
- _write.py   — 写入类：upsert / delete / update / clear
- _read.py    — 读取类：get / list / list_with_meta / get_by_ids / get_by_files
- _filter.py  — 过滤类：get_unexecuted / get_failed
- _export.py  — 导出类：export_to_excel / health_stats

CaseStore 继承全部 mixin，对外接口 100% 兼容。
"""
