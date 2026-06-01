# 使用官方 Playwright 镜像作为基础
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 设置工作目录
WORKDIR /app

# 设置时区为亚洲/上海
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制全部代码
COPY . .

# 创建数据存储目录 (用于持久化 cookie 和 二维码)
RUN mkdir -p /app/data

# 暴露 Web 端口
EXPOSE 8080

# 启动程序
CMD ["python", "-u", "main.py"]
