# -*- coding: utf-8 -*-
"""★ P1-6: 校准环模块 — 人改回流 + 主动学习样本管理。

职责：将人审修改的"预测 vs 人工"结论对回流到 calibration_samples 表，
     供 VLM oracle / 置信度校准 / golden_set 对比使用。

设计：
  - sampler.py: 采样人改用例 → 写入 calibration_samples（被动收集）
  - trainer.py: 读取样本 + 计算校准统计（假PASS率/假FAIL率/一致率）

通用性：
  - 不绑定特定 verdict 来源（human_edit / vlm_judgment / execution_result）
  - features 是通用 JSON（不绑定业务字段）
  - 统计计算通用（适用于任何 PASS/FAIL 二分类系统）
"""
