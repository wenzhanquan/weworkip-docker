FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx-light x11vnc novnc websockify openbox x11-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py security.py remote_login.py sync_engine.py monitoring.py nginx.conf start.sh /app/
COPY templates /app/templates
COPY static /app/static
RUN chmod +x /app/start.sh
ENV DISPLAY=:99 TZ=Asia/Shanghai
EXPOSE 8080
CMD ["/bin/bash", "/app/start.sh"]
