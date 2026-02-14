# ========================================
# ArtifactFlow Docker Image
# ========================================
# 基于 Python 3.11，包含 Playwright 浏览器（crawl4ai 依赖）
# 构建镜像约 2GB+，主要是浏览器占用

FROM python:3.11-slim

LABEL maintainer="1998neutrino@gmail.com"
LABEL description="ArtifactFlow - Multi-Agent System"

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
# Playwright 需要这些库来运行 Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright 依赖
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    # 其他工具
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件（单独复制以利用 Docker 缓存）
# 只要 requirements.txt 不变，下面的依赖安装层就会使用缓存
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install -r requirements.txt

# 初始化 crawl4ai（安装 Playwright 浏览器）
# 这一步会下载 Chromium，是镜像体积大的主要原因
RUN crawl4ai-setup

# 复制源代码
COPY . .

# 安装项目（editable mode）
# --no-deps: 依赖已在上面安装，跳过依赖检查
RUN pip install -e . --no-deps

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]
