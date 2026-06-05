import os
import json
import time
import re
import logging
from datetime import datetime
import pytz  # 用于解决容器默认 UTC 时区导致的定时任务报错
from urllib.parse import urljoin
import requests
from flask import Flask, render_template, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from playwright.sync_api import sync_playwright

# ================= 配置与初始化 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 从环境变量读取配置 (带默认值)
WECHAT_URLS = os.environ.get("WECHAT_URLS", "").split(",")
CHECK_CRON = os.environ.get("CHECK_CRON", "*/11 * * * *")
OVERWRITE = os.environ.get("OVERWRITE", "True").lower() == "true"

DATA_DIR = "/app/data"
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
QR_PATH = os.path.join(DATA_DIR, "qr.png")

# 全局状态
GLOBAL_STATE = {
    "status": "初始化中",
    "need_login": True,
    "current_ip": "192.168.1.1",
    "is_fetching": False  # 防止频繁点击获取二维码导致浏览器多开
}

app = Flask(__name__)

# 将调度器实例提升到全局
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# ================= 核心工具函数 =================
def load_cookies():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_cookies(cookies):
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f)

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
        logger.info("已有获取二维码任务正在运行，跳过...")
        return

    GLOBAL_STATE["is_fetching"] = True
    GLOBAL_STATE["status"] = "正在获取登录二维码"
    GLOBAL_STATE["need_login"] = True
    
    if os.path.exists(QR_PATH):
        os.remove(QR_PATH)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            logger.info("打开企业微信后台登录页...")
            page.goto(WECHAT_URLS[0], timeout=60000)
            
            # 获取二维码图片
            iframe_element = page.frame_locator('iframe[src*="login_qrcode"]')
            qr_img_element = iframe_element.locator('.qrcode_login_img')
            qr_img_element.wait_for(state="visible", timeout=10000)
            
            qr_url = urljoin(page.url, qr_img_element.get_attribute('src'))
            resp = requests.get(qr_url)
            if resp.status_code == 200:
                with open(QR_PATH, "wb") as f:
                    f.write(resp.content)
                logger.info("二维码已保存，请前往Web页面扫码")
                GLOBAL_STATE["status"] = "请扫码登录"
            
            # 等待扫码完成跳转
            try:
                page.wait_for_url("**/frame**", timeout=120000) # 等待最多2分钟供用户扫码
                logger.info("登录成功！")
                save_cookies(context.cookies())
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"
            except Exception as e:
                logger.error(f"扫码超时或失败: {e}")
                GLOBAL_STATE["status"] = "登录超时，请刷新重试"
            
            browser.close()
    except Exception as e:
        logger.error(f"登录流程出错: {e}")
        GLOBAL_STATE["status"] = f"启动浏览器失败: {e}"
    finally:
        # 无论成功或失败，都解除获取状态，释放锁
        GLOBAL_STATE["is_fetching"] = False

def update_wechat_ip(ip_address):
    cookies = load_cookies()
    if not cookies:
        logger.error("没有Cookie，准备重新登录")
        do_login_and_save_cookie()
        return

    need_relogin = False  # 用标志位记录是否需要重新登录，避免嵌套 Playwright 崩溃

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            
            # 校验Cookie是否有效
            page.goto(WECHAT_URLS[0])
            time.sleep(2)
            if page.locator('.login_stage_title_text').is_visible():
                logger.info("检测到 Cookie 失效...")
                need_relogin = True  # 标记为需要重新登录
            else:
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"

                # 遍历应用更新IP
                for url in WECHAT_URLS:
                    if not url.strip(): continue
                    logger.info(f"正在配置应用IP...")
                    page.goto(url)
                    
                    # 这里根据MP原版插件逻辑，点击修改并输入IP
                    page.wait_for_selector('div.app_card_operate.js_show_ipConfig_dialog')
                    page.locator('div.app_card_operate.js_show_ipConfig_dialog').click()
                    page.wait_for_selector('textarea.js_ipConfig_textarea')
                    
                    input_area = page.locator('textarea.js_ipConfig_textarea')
                    confirm_btn = page.locator('.js_ipConfig_confirmBtn')
                    
                    existing_ip = input_area.input_value()
                    if OVERWRITE:
                        input_area.fill(ip_address)
                    else:
                        # 去重并添加
                        ips = set(existing_ip.split(';')) if existing_ip else set()
                        ips.add(ip_address)
                        input_area.fill(';'.join(filter(None, ips)))
                    
                    confirm_btn.click()
                    time.sleep(1)
                    logger.info(f"应用可信IP配置成功: {ip_address}")
            
            browser.close()
    except Exception as e:
        logger.error(f"更新IP失败: {e}")

    # 将重新登录的动作移到 with sync_playwright() 外部执行，避免嵌套冲突
    if need_relogin:
        logger.info("触发重新登录流程...")
        do_login_and_save_cookie()

def check_task():
    if not WECHAT_URLS or WECHAT_URLS[0] == "":
        logger.error("未配置 WECHAT_URLS 环境变量！")
        GLOBAL_STATE["status"] = "配置缺失 (WECHAT_URLS)"
        return

    if GLOBAL_STATE["need_login"]:
        do_login_and_save_cookie()
        return

    logger.info("开始检测公网IP...")
    current_ip = get_public_ip()
    if current_ip:
        logger.info(f"当前公网IP: {current_ip}")
        if current_ip != GLOBAL_STATE["current_ip"]:
            logger.info("检测到IP变化，准备同步到企业微信...")
            GLOBAL_STATE["current_ip"] = current_ip
            update_wechat_ip(current_ip)
        else:
            # 即使IP没变，也可以定期验证一下Cookie保活
            pass
    else:
        logger.error("获取公网IP失败")

# ================= Web 服务 =================
@app.route('/')
def index():
    return render_template(
        'index.html',
        status=GLOBAL_STATE["status"],
        need_login=GLOBAL_STATE["need_login"],
        current_ip=GLOBAL_STATE["current_ip"],
        qr_exists=os.path.exists(QR_PATH),
        is_fetching=GLOBAL_STATE.get("is_fetching", False),
        time=int(time.time())
    )

@app.route('/qr.png')
def serve_qr():
    if os.path.exists(QR_PATH):
        return send_from_directory(DATA_DIR, 'qr.png')
    return "QR not found", 404

# 新增手动强制刷新接口
@app.route('/refresh_qr_api')
def refresh_qr_api():
    if not GLOBAL_STATE["need_login"]:
        return {"status": "success", "msg": "当前Cookie有效，无需刷新！"}
    
    if GLOBAL_STATE.get("is_fetching"):
        return {"status": "info", "msg": "后台正在努力获取中..."}

    if os.path.exists(QR_PATH):
        os.remove(QR_PATH)
        
    # 明确指定上海时区，避免 8 小时 UTC 偏差导致任务被抛弃
    scheduler.add_job(
        func=do_login_and_save_cookie, 
        trigger='date', 
        run_date=datetime.now(pytz.timezone("Asia/Shanghai"))
    )
    return {"status": "success", "msg": "已触发重新获取"}


if __name__ == "__main__":
    # 启动时先做一次初步校验
    if not load_cookies():
        GLOBAL_STATE["need_login"] = True
    else:
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "正常运行中"

    # 启动定时任务 (这里的 scheduler 已经在顶部初始化了)
    scheduler.add_job(func=check_task, trigger=CronTrigger.from_crontab(CHECK_CRON), name="IP_Checker")
    scheduler.start()

    # 明确指定上海时区，马上异步触发一次检查
    scheduler.add_job(
        func=check_task, 
        trigger='date', 
        run_date=datetime.now(pytz.timezone("Asia/Shanghai"))
    )

    # 启动Web服务器
    app.run(host='0.0.0.0', port=8080)
