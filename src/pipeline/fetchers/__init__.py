# -*- coding: utf-8 -*-
"""
pipeline.fetchers — Step1 数据源拉取 mixin 包

- base.py      — 前置检查：健康检查、配置校验、版本管理
- confluence.py — Confluence 拉取：缓存、API、MySQL 写入
- figma.py     — Figma 拉取：缓存检查、智能拉取、增量模式
- database.py  — 数据库查询：测试账号与 Schema
- yapi.py      — YApi 拉取：缓存、API、MySQL 写入
- persist.py   — 需求稿与数据源状态持久化

Step1DataFetcher 继承全部 mixin，对外接口保持稳定。
"""
