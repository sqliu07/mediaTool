FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 将依赖文件拷贝到容器中
COPY requirements.txt .

# 安装依赖，包括 Flask、requests 和 APScheduler
RUN mkdir -p /app/logs pip install --no-cache-dir -r requirements.txt

# 拷贝项目代码
COPY . .

# 暴露端口（这里以5000端口为例）
EXPOSE 5000

# 启动 Flask 应用
CMD ["python", "app.py"]
