# -*- coding: utf-8 -*-
"""src.pipeline —— 流水线包（分层说明）

[B1.3 分层说明] 本文件原为 0 字节空文件。此处只补充**架构说明**，
不做任何 re-export，避免给 ``import src.pipeline`` 引入新的副作用与循环依赖风险。

分层约定
========

本包内部实际存在两层，职责必须区分开：

1. **编排层（orchestration）** —— ``src/pipeline/`` 顶层模块
   代表文件：``scheduler.py`` / ``scheduler_executor.py`` / ``streaming_scheduler.py`` /
   ``step1.py`` / ``step2.py`` / ``step3/`` / ``step4_executor.py`` / ``step5.py`` /
   ``review_gate.py`` / ``saga.py``
   职责：决定"**做哪些步骤、按什么顺序、在哪里暂停/回滚**"。
   它组织流程，不实现具体能力。

2. **能力层（capability）** —— ``src/pipeline/steps/`` 子包
   代表目录：``steps/step1/`` ~ ``steps/step5/``、``steps/step3_inverse/``
   职责：实现"**这一步具体怎么做**"（抓取、拆解、建模、执行、报告）。
   它提供可被复用的能力，不关心自己被谁、以何种顺序调用。

3. **公共设施** —— ``src/pipeline/utils/``、``src/pipeline/helpers*``、``src/pipeline/plugins/``
   两层都可依赖，自身不反向依赖任何一层。

依赖方向约定
============

    编排层  ──依赖──▶  能力层  ──依赖──▶  公共设施

**约定：能力层禁止反向 import 编排层。**
理由：反向依赖会让"某一步的能力"绑死在"某一条特定流水线"上，既无法单独测试，
也容易在 import 期形成环。

⚠️ 现状与约定不符（如实记录，勿据此误判为已达标）
------------------------------------------------
截至 2026-08-11 实测，``steps/`` 中仍有 **12 处 / 8 个文件**反向 import 编排层，
其中 10 处指向 ``src.pipeline.step3``（``_rule_extractor`` / ``_modeling_engine`` /
``value_generator``），另有 1 处 ``step2_coverage``、1 处 ``helpers``。

即：**上面的依赖方向是"目标态"，不是"现状"。**
这些反向依赖属于既有技术债，本轮整改（P0）**未做搬迁**——搬迁会大范围改动 import 链，
风险高于收益，需单独立项。新增代码请遵守约定，不要再新增反向依赖。
"""
