# 使用官方精简版Python镜像
FROM python:3.9-slim

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制项目文件到容器中
COPY . .

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 暴露容器端口
EXPOSE 5001

# 启动命令，使用 gunicorn 启动 app:app
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5001", "app:app"]
