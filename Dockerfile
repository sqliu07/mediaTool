FROM python:3.9-slim

WORKDIR /app

# 创建非 root 用户和组
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# 创建必要的目录并设置权限
RUN mkdir -p /app/logs /app/configs && chown -R appuser:appgroup /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# 确保复制后的文件权限正确
RUN chown -R appuser:appgroup /app

# 切换到非 root 用户
USER appuser

EXPOSE 8082

CMD ["python", "app.py"]