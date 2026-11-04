# -*- coding: utf-8 -*-
"""★ P1-5: VLM Oracle 模块 — 视觉真值校验层。

职责：封装视觉大模型（VLM）调用，为 Step4 提供"视觉上是否正确"的独立真值源。
设计：
  - vlm_adapter.py: VLM Provider 适配层（Ollama 本地优先，预留扩展）
  - correctness_vlm.py: VLMOracle.check() 视觉校验入口
通用性：输入为 layout_spec(JSON) + screenshot(bytes/path) + dom_summary(str)，
        不绑定任何业务术语/行业/页面类型。
"""
