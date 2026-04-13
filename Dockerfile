FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY *.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# 数据持久化目录
VOLUME ["/app/data"]

# 暴露端口
EXPOSE 5000

# 启动命令
CMD ["python", "main.py"]
