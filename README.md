Markdown
下载代码
复制代码
# 🌐 WeWorkIP-Docker (企业微信应用可信IP自动更新)

![
Docker Pulls](https://img.shields.io/docker/pulls/wenzhanquan/weworkip
)
![
Docker Image Size](https://img.shields.io/docker/image-size/wenzhanquan/weworkip/latest
)
![
GitHub License](https://img.shields.io/github/license/wenzhanquan/weworkip-docker
)

本仓库提供了一个
**独立、轻量、持久化**
的企业微信可信 IP 自动更新服务，专为内网穿透和动态公网 IP 环境设计。

> **💡 项目背景**
> 本项目核心逻辑脱胎于 MoviePilot 的同名插件。由于 MP 重启频率较高，经常导致内置插件的企微 Cookie 失效掉线。为了彻底解决这个问题，本项目将核心逻辑剥离，使用 Docker 独立部署，支持 Cookie 本地持久化存储，实现**一次扫码，长期稳定运行**。

---

## ✨ 核心特性

- **🐳 Docker 原生**
：完全解耦，不依赖任何第三方平台，静默运行。
- **💾 Cookie 持久化**
：登录状态保存在本地映射目录，容器重启、升级均不掉线。
- **📱 Web 扫码登录**
：内置轻量级 Web 面板，Cookie 失效时直接浏览器看图扫码，无需翻看控制台日志。
- **🔄 多应用支持**
：支持同时配置并更新多个企业微信应用的可信 IP。
- **⚙️ 纯净 Playwright**
：内置微软官方 Playwright 环境，高度模拟真实浏览器，降低风控概率。

---

## 🚀 快速部署

我们强烈推荐使用 
`docker-compose`
 进行部署。

### 1. 创建目录与配置文件
在你的服务器（如群晖、Linux NAS 等）上创建一个目录，例如 
`weworkip`，并在其中创建 `docker-compose.yml`
：

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
      # 必填：企业微信应用的可信IP配置页面地址（多个地址用英文逗号分隔）
      - WECHAT_URLS=[https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000](https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000)
      - CHECK_CRON=*/11 * * * * # 检测 IP 变动的频率，默认11分钟
      - OVERWRITE=True           # True为覆盖原有IP，False为追加IP
    volumes:
      # 【重要】将 Cookie 和二维码保存到本地，实现持久化
      - ./data:/app/data         
2. 启动容器
在 docker-compose.yml 所在目录执行以下命令：
Bash
下载代码
复制代码
docker-compose up -d
⚙️ 环境变量说明
变量名
必填
默认值
描述
WECHAT_URLS
是
无
企微应用管理地址。类似 https://work.weixin.../00000000000。多个应用请用 , 分隔。
CHECK_CRON
否
*/11 * * * *
IP 检测的 Cron 表达式。默认每 11 分钟检测一次外网 IP 是否变化。
OVERWRITE
否
True
True：新 IP 直接覆盖列表。

False：保留旧 IP，将新 IP 追加到末尾。
更多选项
🎮 使用指南 (扫码登录)
访问 Web 面板：容器启动后，在浏览器中访问 http://<你的服务器IP>:8888（端口取决于你的 compose 映射）。
扫描二维码：
如果是首次运行，或者检测到 Cookie 已经失效，页面上会显示一个企业微信登录二维码。
打开手机企业微信，扫描二维码并确认登录。
完成配置：扫码完成后，后台会自动接管流程，并将 Cookie 保存到映射的 ./data 目录中。Web 页面会显示“正常运行中”。
静默守护：此后你无需再进行任何操作。程序会按照你设定的频率在后台默默检查 IP 并自动更新到企业微信后台。
❓ 常见问题 (FAQ)
Q: 容器重启或者更新镜像后，需要重新扫码吗？ A: 不需要。只要你在 docker-compose.yml 中正确映射了 ./data:/app/data，Cookie 会被永久保存在本地宿主机，容器重建不会丢失登录状态。
Q: 为什么扫码后 Web 页面一直转圈或者报错？ A: 扫码后后台需要一点时间（通常 5-10 秒）跳转并抓取保存 Cookie。请耐心等待一会，刷新网页即可看到成功状态。如果持续失败，请检查服务器网络是否能正常访问企微官网。
📜 鸣谢与协议
核心交互逻辑灵感来源于 MoviePilot 社区插件 MoviePilot-Plugins。
本项目采用 GPL-3.0 License 开源协议。
***

### 这份 README 的亮点：
1. **徽章 (Badges)**：顶部预留了 Docker Hub 的拉取量和大小徽章，显得非常极客和专业。
2. **逻辑清晰**：按背景、特性、部署、配置、使用的顺序逐步引导。
3. **高亮强调**：用显眼的提示框和加粗字体，反复强调了 `./data` 挂载的重要性，降低小白用户的出错率。
4. **适配了纯净环境**：没有提到多余的 CookieCloud 配置，因为独立 Docker 版直接扫码持久化是最优雅、最直接的方案，抛
