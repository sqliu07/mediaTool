# MediaTool 镜像
FROM python:3.9-slim

# PYTHONUNBUFFERED: 日志实时输出；TZ: 时区（需要 tzdata 才生效）
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1

# 系统依赖：
#   tzdata  —— slim 镜像默认没有时区库，否则 TZ 不生效、日志会是 UTC
#   gosu    —— 入口脚本用 PUID/PGID 切换运行身份
# 依赖全是纯 Python 包，不需要 gcc / build-essential
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只复制依赖清单，代码改动不会导致重新安装依赖（利用层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 再复制源码（.dockerignore 已排除 .venv / testdata / configs / logs 等）
COPY . .

# 运行期目录与入口脚本
RUN mkdir -p /app/configs /app/logs \
    && chmod 755 /entrypoint.sh

EXPOSE 5001

# 健康检查用 python 自带的 urllib，避免为了 curl 额外装包
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/progress', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["/entrypoint.sh"]

# 进度状态保存在进程内存中，定时任务也应只跑一份，
# 因此固定为「单 worker + 多线程」；--timeout 0 避免长任务被 kill。
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "8", "--timeout", "0", \
     "-b", "0.0.0.0:5001", "app:app"]
