FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/swufe/.cache/huggingface \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 libopenblas0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 swufe \
    && useradd --uid 10001 --gid swufe --create-home --shell /usr/sbin/nologin swufe

WORKDIR /app

COPY requirements.txt requirements-web.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        torch==2.10.0+cpu \
    && python -m pip install -r requirements.txt -r requirements-web.txt

COPY --chown=swufe:swufe . .
RUN chmod 0555 /app/docker-entrypoint.sh
RUN mkdir -p /home/swufe/.cache/huggingface \
    && chown -R swufe:swufe /home/swufe/.cache /app

USER swufe

ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 8000

# 存活探针走 /healthz(不触碰模型,毫秒级)。slim 镜像没装 curl,
# 因此用 stdlib urllib;start-period 给足冷启动加载模型索引的时间,
# 否则首次探测会在模型还没加载完时误判失败并触发重启循环。
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.server.application:app", "--host", "0.0.0.0", "--port", "8000"]
