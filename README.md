# 企业微信 IP 助手

<img src="static/favicon.svg" width="48" height="48" alt="企业微信 IP 助手图标">

独立运行的企业微信应用可信 IP 更新服务。定时检测公网 IPv4，实际访问企业微信后台验证 Cookie，读取可信 IP；配置不一致时自动更新，并重新读取确认结果。

## 面板功能

- **实际状态**：Cookie 是否有效、最近验证时间、当前公网 IP、后台实际可信 IP、最近读取时间和下次检查时间。
- **主要运行日志**：带北京时间，最近 200 条记录持久化保存，容器重启后仍可查看。不记录 Cookie 或推送 Token。
- **三种折叠登录方式**：远程浏览器、主面板二维码、手动注入 Cookie。
- **安全替换登录态**：获取二维码、取消登录和登录失败均保留现有 Cookie；新登录或新 Cookie 验证成功后才替换。
- **远程浏览器**：手机可操作完整登录页面及滑块，免去手工导出 Cookie。登录窗口最多等待 10 分钟。
- **多应用与推送**：支持多个应用地址，以及可选的 PushPlus 通知。
- **网页图标**：浏览器标签与面板标题使用统一图标，提供 SVG 和多尺寸 ICO。

面板按当前设计不要求访问密码，适合通过内网或 VPN 使用。远程桌面复用面板端口，无需额外映射 VNC 端口。

## Docker Compose 部署

当前构建为 Linux amd64。下载本仓库的 `docker-compose.yml` 和 `.env.example`，将 `.env.example` 复制为 `.env`，填写：

```dotenv
WECHAT_URLS=https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/你的应用ID
PUSHPLUS_TOKEN=
```

多个应用地址用英文逗号分隔。PushPlus 不使用时留空。

```sh
docker compose pull
docker compose up -d --no-build
```

打开 `http://服务器地址:9001`。数据保存在当前目录的 `./data`，升级时应保留该目录。

### 从源码构建

克隆完整仓库并配置 `.env` 后执行：

```sh
docker compose build weworkip
docker compose up -d --no-build --pull never weworkip
```

Dockerfile 从微软官方 Playwright 镜像独立构建，不依赖旧版 WeWorkIP 镜像。Python 依赖固定为已验证版本。

## 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `WECHAT_URLS` | 空，必填 | 企业微信应用详情地址，多个地址用英文逗号分隔 |
| `CHECK_CRON` | `*/11 * * * *` | 按小时内第 0、11、22、33、44、55 分钟检查 |
| `OVERWRITE` | `True` | 为 `True` 时可信 IP 只保留当前公网 IP；为 `False` 时保留原配置并追加 |
| `PUSHPLUS_TOKEN` | 空 | 可选通知 Token，也兼容 `PUSH_PLUS_TOKEN` |
| `TZ` | `Asia/Shanghai` | 容器时区；面板事件时间使用北京时间 |

示例映射为 `9001:8080`，可修改左侧端口。`shm_size: 256m` 用于浏览器共享内存。

## 登录与验证

1. **远程浏览器登录**：展开方式 1，点击按钮，按企业微信页面提示扫码，必要时直接操作滑块。手机建议横屏。
2. **二维码登录**：展开方式 2 后点击获取二维码；单纯展开不会触发重新登录。二维码过期可结束本次登录后重新获取。
3. **手动 Cookie**：展开方式 3，粘贴浏览器请求中的完整 Cookie 字符串，或 Playwright Cookie JSON 数组。新 Cookie 先验证，失败时不会覆盖原文件。

同一部手机能否通过相册识码取决于微信实际支持；不支持时使用另一块屏幕展示二维码。额外验证仍需用户本人完成。

Cookie 文件存在不代表当前有效；首页显示最近一次实际访问后台的结果。网络超时会显示未能确认，不会直接删除 Cookie。可信 IP 保存后会重新访问后台读取，只有值匹配才报告成功。

首次检测到 Cookie 失效时会自动打开扫码窗口；后续定时检查若仍处于同一失效状态，不会反复打开窗口。关闭或超时后可在面板手动重新获取二维码；成功登录后再次失效仍会触发提醒。

## 更新与回退

更新前备份 `data` 和 Compose 配置。拉取镜像后重建容器不会主动清除已保存的登录态：

```sh
docker compose pull
docker compose up -d --no-build
```

GitHub Actions 在 `main` 更新后先构建并运行回归测试，通过后发布 Docker Hub 的 `latest`、`edge` 和对应提交的 `sha-*` 标签。需要固定版本时，在 Compose 中指定相应提交标签。服务本身不执行自动拉取或自动升级。

## 测试

测试包含真实 Chromium 的登录生命周期、无效 Cookie 保留、可信 IP 保存后回读、任务繁忙恢复和日志持久化。测试使用本地模拟后台，不触碰真实企业微信配置：

```sh
docker build -t weworkip:test .
docker run --rm --network none --entrypoint bash \
  -v "$PWD:/tests:ro" -e PYTHONPATH=/app \
  -e WECHAT_URLS=https://fixture.invalid/app \
  weworkip:test -c 'Xvfb :98 -screen 0 1100x820x24 -nolisten tcp >/tmp/xvfb.log 2>&1 & sleep 1; DISPLAY=:98 python -m unittest discover -s /tests -p "test_*.py"'
```

本项目核心交互逻辑源自 MoviePilot 同名插件，延续原仓库的 GPL-3.0 协议说明。
