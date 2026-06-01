# 🌐 WeWorkIP-Docker (企业微信应用可信IP自动更新)

![Docker Pulls](https://img.shields.io/docker/pulls/wenzhanquan/weworkip)
![Docker Image Size](https://img.shields.io/docker/image-size/wenzhanquan/weworkip/latest)
![GitHub License](https://img.shields.io/github/license/wenzhanquan/weworkip-docker)

本仓库提供了一个**独立、轻量、持久化**的企业微信可信 IP 自动更新服务，专为内网穿透和动态公网 IP 环境设计。

> **💡 项目背景**
> 本项目核心逻辑脱胎于 MoviePilot 的同名插件。由于 MP 重启频率较高，经常导致内置插件的企微 Cookie 失效掉线。为了彻底解决这个问题，本项目将核心逻辑剥离，使用 Docker 独立部署，支持 Cookie 本地持久化存储，实现**一次扫码，长期稳定运行**。

---

## ✨ 核心特性

- **🐳 Docker 原生**：完全解耦，不依赖任何第三方平台，静默运行。
- **💾 Cookie 持久化**：登录状态保存在本地映射目录，容器重启、升级均不掉线。
- **📱 Web 扫码登录**：内置轻量级 Web 面板，Cookie 失效时直接浏览器看图扫码，无需翻看控制台日志。
- **🔄 多应用支持**：支持同时配置并更新多个企业微信应用的可信 IP。
- **⚙️ 纯净 Playwright**：内置微软官方 Playwright 环境，高度模拟真实浏览器，降低风控概率。

---

## 🚀 快速部署

我们强烈推荐使用 `docker-compose` 进行部署。

### 1. 创建目录与配置文件
在你的服务器（如群晖、Linux NAS 等）上创建一个目录，例如 `weworkip`，并在其中创建 `docker-compose.yml`：

```yaml
version: '3.8'
services:
  weworkip:
    image: wenzhanquan/weworkip:latest
    container_name: weworkip
    restart: unless-stopped
    ports:
      - "8888:8080"  # 8888 可修改为你喜欢的本地端口
    environment:
      - TZ=Asia/Shanghai  # 设置时区
      # 必填：企业微信应用的可信IP配置页面地址（多个地址用英文逗号分隔）
      - WECHAT_URLS=https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000
      - CHECK_CRON=*/11 * * * * # 检测 IP 变动的频率，默认11分钟
      - OVERWRITE=True           # True为覆盖原有IP，False为追加IP
    volumes:
      # 【重要】将 Cookie 和二维码保存到本地，实现持久化
      - ./data:/app/data
