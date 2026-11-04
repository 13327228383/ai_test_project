# -*- coding: utf-8 -*-
"""
置信度计算器 —— 元类与描述符
=====================================
提供 ``_DualMethod``（类/实例双态调用描述符）与 ``_ConfidenceMeta``
（``WEIGHTS`` 属性代理元类）。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不变。
"""


class _DualMethod:
    """同时支持类调用和实例调用的方法描述符。

    - ConfidenceCalculator.calculate(...)：走共享单例，兼容旧调用方
    - ConfidenceCalculator().calculate(...)：走当前实例，确保实例权重真正独立
    """

    def __init__(self, func):
        self.func = func
        self.__name__ = getattr(func, "__name__", "_dual_method")
        self.__doc__ = getattr(func, "__doc__", None)

    def __get__(self, obj, cls=None):
        target = obj if obj is not None else cls._get_instance()

        def _bound(*args, **kwargs):
            return self.func(target, *args, **kwargs)

        _bound.__name__ = self.__name__
        _bound.__doc__ = self.__doc__
        return _bound


class _ConfidenceMeta(type):
    @property
    def WEIGHTS(cls) -> dict[str, float]:
        """向后兼容旧入口：返回共享单例权重副本，避免暴露可变类变量。"""
        return cls._get_instance().get_weights()

    @WEIGHTS.setter
    def WEIGHTS(cls, weights: dict[str, float]) -> None:
        cls._get_instance().set_weights(weights)
