# -*- coding: utf-8 -*-
"""
AiBrain 业务方法挂载 Mixin
==========================
通过 __getattr__ 懒加载 step3 的业务函数，避免循环导入。
同时提供模块级辅助函数用于单例的惰性挂载。

★ 2026-07-04：breakdown_requirements（文档驱动需求拆解）已废弃删除，
  Step2 改用 feature_driven_breakdown（功能清单驱动）。本文件只保留 model_test_cases 懒挂载。
"""


class _BusinessMixin:
    """业务方法懒加载与挂载。"""

    def __getattr__(self, name):
        """访问 model_test_cases 等业务方法时，自动懒加载。"""
        business_method_names = {
            "model_test_cases",
        }
        if name in business_method_names and not self.__dict__.get(
            "_business_methods_mounted"
        ):
            from src.pipeline.steps.step3.case_generator import model_test_cases

            setattr(self, "model_test_cases", model_test_cases)
            self._business_methods_mounted = True
            return getattr(self, name)
        raise AttributeError(f"'AiBrain' object has no attribute '{name}'")


# ── 模块级辅助：用于 ai_brain 单例的惰性挂载 ──
def _lazy_mount(ai_brain_instance):
    """惰性挂载 model_test_cases 到 ai_brain 单例。"""
    if not hasattr(ai_brain_instance, "model_test_cases"):
        from src.pipeline.steps.step3.case_generator import model_test_cases

        ai_brain_instance.model_test_cases = model_test_cases
