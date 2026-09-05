FROM docker.m.daocloud.io/library/python:3.12-slim

WORKDIR /app

# 配置 pip 使用阿里云镜像
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com

# 系统依赖（psycopg2 需要 pg_config）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖声明，利用 Docker 缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# 复制项目代码
COPY . .

# 重新安装以注册项目代码
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# 启动命令：uvicorn 多 worker
CMD ["sh", "-c", "uvicorn kbquant.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-8} --http httptools --timeout-keep-alive 65 --timeout-graceful-shutdown 30 --backlog 16384 --limit-max-requests 10000"]
