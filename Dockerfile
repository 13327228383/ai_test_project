# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────
# E2E 智能测试引擎 — 容器镜像
# 基于 python:3.11-slim，安装 Playwright (chromium) 与项目依赖
# 暴露 5000 端口，CMD 启动 Flask Web 控制面板（src.backend.app）
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# ── 系统依赖：Playwright/Chromium 运行时所需库 + 常规构建工具 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg \
        build-essential pkg-config \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        fonts-liberation fonts-noto-cjk libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# ── 项目依赖 ──
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt \
    && pip install playwright \
    && python -m playwright install --with-deps chromium

# ── 复制项目源码 ──
COPY . /app

EXPOSE 5000

# 默认入口：启动 Flask Web 控制面板。
# 若仅需要跑 CLI 流水线，可覆盖 CMD：docker run ... python main.py --project xxx
CMD ["python", "-m", "src.backend.app", "--host", "0.0.0.0", "--port", "5000"]
