import os
import json
import time
import re
import logging
from datetime import datetime
import pytz
from urllib.parse import urljoin
import requests
from flask import Flask, render_template, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from playwright.sync_api import sync_playwright

# ================= 配置与初始化 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WECHAT_URLS = os.environ.get("WECHAT_URLS", "").split(",")
CHECK_CRON = os.environ.get("CHECK_CRON", "*/11 * * * *")
OVERWRITE = os.environ.get("OVERWRITE", "True").lower() == "true"
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()

DATA_DIR = "/app/data"
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
QR_PATH = os.path.join(DATA_DIR, "qr.png")

GLOBAL_STATE = {
    "status": "初始化中",
    "need_login": True,
    "current_ip": "192.168.1.1",
    "is_fetching": False
}

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# ================= PushPlus 高级 Markdown 推送模块 =================
def send_pushplus(title, content):
    if not PUSHPLUS_TOKEN:
        return
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "markdown"  # 核心：使用 Markdown 模板渲染，支持更好看的排版
    }
    try:
        res = requests.post(url, json=data, timeout=10)
        if res.status_code == 200:
            logger.info("✅ PushPlus 消息推送成功！")
        else:
            logger.error(f"❌ PushPlus 推送失败: {res.text}")
    except Exception as e:
        logger.error(f"❌ PushPlus 推送异常: {e}")

# ================= 智能 Cookie 处理模块 =================
def load_cookies():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                
                # 1. 尝试解析为标准 JSON (插件导出格式)
                if content.startswith("[") and content.endswith("]"):
                    return json.loads(content)
                
                # 2. 智能解析: 浏览器 F12 网络抓包直接复制的 Raw 字符串格式
                logger.info("检测到非 JSON 格式的 Cookie，启动智能原生字符串解析...")
                parsed_cookies = []
                for item in content.split(';'):
                    if '=' in item:
                        name, value = item.strip().split('=', 1)
                        parsed_cookies.append({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": ".work.weixin.qq.com",
                            "path": "/"
                        })
                
                if parsed_cookies:
                    logger.info(f"成功解析 {len(parsed_cookies)} 个原生 Cookie 字段，并已自动格式化！")
                    save_cookies(parsed_cookies) # 顺手保存为标准JSON格式，覆盖原文本
                    return parsed_cookies
                    
        except Exception as e:
            logger.error(f"解析 Cookie 文件失败: {e}")
    return []

def save_cookies(cookies):
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=4)

def get_public_ip():
    urls = ["https://myip.ipip.net", "https://ddns.oray.com/checkip", "https://ip.3322.net", "https://4.ipw.cn"]
    pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    for url in urls:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                match = re.search(pattern, resp.text)
                if match:
                    return match.group()
        except Exception:
            continue
    return None

# ================= 自动化控制逻辑 =================
def do_login_and_save_cookie():
    if GLOBAL_STATE.get("is_fetching"):
        return

    GLOBAL_STATE["is_fetching"] = True
    GLOBAL_STATE["status"] = "正在获取登录二维码"
    GLOBAL_STATE["need_login"] = True
    
    if os.path.exists(QR_PATH):
        try: os.remove(QR_PATH)
        except: pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            logger.info("打开企业微信后台登录页...")
            page.goto(WECHAT_URLS[0], timeout=60000)
            
            iframe_element = page.frame_locator('iframe[src*="login_qrcode"]')
            qr_img_element = iframe_element.locator('.qrcode_login_img')
            qr_img_element.wait_for(state="visible", timeout=10000)
            
            qr_url = urljoin(page.url, qr_img_element.get_attribute('src'))
            resp = requests.get(qr_url)
            if resp.status_code == 200:
                with open(QR_PATH, "wb") as f:
                    f.write(resp.content)
                logger.info("二维码已保存，等待扫码")
                GLOBAL_STATE["status"] = "请扫码登录"
                
                # ----- 触发二维码推送 (Markdown格式) -----
                now_time = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                qr_content = f"""### 🚨 企微助手状态异常

**当前状态**：等待扫码 ⏳
**触发时间**：{now_time}

---
**Cookie 已失效**，系统已在后台为您提取最新的登录二维码。
👉 **请尽快前往 Web 面板进行扫码！**

💡 **防拦截提示**：
若遇到【滑块验证】拦截，请直接在电脑端提取原生 Cookie 注入 `cookies.json` 文件，并点击网页「强制刷新」即可无感接管。"""
                send_pushplus("⚠️ 企微助手：请扫码登录", qr_content)
            
            try:
                page.wait_for_url("**/frame**", timeout=120000)
                logger.info("扫码登录成功！")
                save_cookies(context.cookies())
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"
                
                # ----- 触发接管成功推送 (Markdown格式) -----
                now_time = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                success_content = f"""### 🎉 企微接管成功

**当前状态**：正常运行中 🟢
**触发时间**：{now_time}

---
程序已成功验证 Cookie 并接管企业微信！🚀
后续系统将在后台默默为您同步动态公网 IP，无需人工干预。"""
                send_pushplus("✅ 企微助手：接管成功", success_content)
                
            except Exception:
                logger.error("用户未在 2 分钟内扫码，流程结束。")
                GLOBAL_STATE["status"] = "登录超时，请手动刷新或注入Cookie"
            
            browser.close()
    except Exception as e:
        logger.error(f"登录流程出错: {e}")
        GLOBAL_STATE["status"] = f"启动浏览器失败: {e}"
    finally:
        GLOBAL_STATE["is_fetching"] = False

def update_wechat_ip(ip_address):
    cookies = load_cookies()
    if not cookies:
        logger.error("没有 Cookie，准备触发登录流程")
        do_login_and_save_cookie()
        return

    need_relogin = False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            
            page.goto(WECHAT_URLS[0])
            time.sleep(2)
            if page.locator('.login_stage_title_text').is_visible():
                logger.info("检测到 Cookie 失效 (或验证码拦截)...")
                need_relogin = True
            else:
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"

                for url in WECHAT_URLS:
                    if not url.strip(): continue
                    logger.info("正在配置应用IP...")
                    page.goto(url)
                    
                    page.wait_for_selector('div.app_card_operate.js_show_
