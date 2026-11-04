# -*- coding: utf-8 -*-
"""
★ KEEP-34: 可恢复/不可恢复异常体系。

用于统一表达错误类别、是否可恢复、HTTP 状态码和用户可见消息。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """应用异常基类。"""

    category = "application"
    recoverable = False
    status_code = 500

    def __init__(self, message: str, *, details: Any = None, status_code: int = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "error": self.message,
            "category": self.category,
            "recoverable": self.recoverable,
        }
        if self.details is not None:
            payload["details"] = self.details
        return payload


class RecoverableError(AppError):
    """可恢复异常：重试、补充输入或等待依赖恢复后可继续。"""

    recoverable = True
    status_code = 400


class NonRecoverableError(AppError):
    """不可恢复异常：需要修改配置、代码或数据后才能继续。"""

    recoverable = False
    status_code = 500


class ValidationError(RecoverableError):
    """请求/业务输入校验失败。"""

    category = "validation"
    status_code = 400


class ConfigError(NonRecoverableError):
    """配置错误。"""

    category = "config"
    status_code = 500


class ExternalDependencyError(RecoverableError):
    """外部依赖失败。"""

    category = "external_dependency"
    status_code = 503


class DatabaseError(RecoverableError):
    """数据库临时错误。"""

    category = "database"
    status_code = 503
