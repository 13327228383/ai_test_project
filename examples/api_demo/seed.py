# -*- coding: utf-8 -*-
"""api_demo — 种子数据（启动自动建库种子）
============================================
[未复核-需 GLM/DeepSeek 把关] 新靶子系统种子（2026-08-10 新增）。

提供：
  - 两个账号：admin（管理员，可删除）/ user（普通用户，越权验证用）
  - 3 本初始书籍（覆盖不同价格/库存边界）
"""

SEED_USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "user": {"password": "user123", "role": "user"},
}

SEED_BOOKS = [
    {"title": "E2E 智能测试引擎实战", "author": "Alice", "price": 99.00, "stock": 50},
    {"title": "跨系统复用测试方法论", "author": "Bob", "price": 59.90, "stock": 10},
    {"title": "API 自动化测试入门", "author": "Carol", "price": 39.50, "stock": 0},
]
