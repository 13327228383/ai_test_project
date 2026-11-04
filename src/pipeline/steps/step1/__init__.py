# -*- coding: utf-8 -*-
# src/pipeline/steps/step1/__init__.py
from src.pipeline.steps.step1.confluence_client import confluence_client
from src.pipeline.steps.step1.figma_client import figma_client


def fetch_all():
    """拉取 Confluence + Figma（需求文档 + 设计稿）。

    ★ P0-refactor(2026-07-27): 取消 DOM 爬取。
       架构决策(§16.2): 静态 DOM 爬取永远爬不全动态页面，改为 AgentExplorer 实时探索。
       Step1 只负责拉取需求文档(Confluence)和设计稿(Figma)，
       Step4 AgentExplorer 在浏览器中实时感知→决策→执行，不依赖静态 DOM 快照。
    """
    cf_result = confluence_client.fetch_confluence_all()
    figma_result = figma_client.fetch_figma_file()

    return {
        "success": cf_result["success"] or figma_result["success"],
        "confluence": cf_result,
        "figma": figma_result,
        "dom_crawl": {
            "success": True,
            "skipped": True,
            "reason": "P0-refactor: DOM爬取已取消，Step4 AgentExplorer实时探索替代",
        },
    }
