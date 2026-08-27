import os
import json
import time
import re
import logging
from datetime import datetime
import pytz
from urllib.parse import urljoin
import requests
import threading
from flask import Flask, render_template, send_from_directory, request, redirect
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

# 💡 核心修复：启动时自动确保目录和文件存在，防止 Docker 挂载时误创文件夹
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(COOKIE_FILE) or os.path.isdir(COOKIE_FILE):
    try:
        # 如果是个误创的目录（Docker 挂载常见问题），可以尝试记录日志
        if os.path.isdir(COOKIE_FILE):
            logger.error(f"{COOKIE_FILE} 是一个目录！请检查 fnOS 或宿主机上的 volume 映射配置，建议直接映射整个 data 目录而不是单文件。")
        else:
            with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                f.write('')
    except Exception as e:
        logger.error(f"初始化 Cookie 文件失败: {e}")


GLOBAL_STATE = {
    "status": "初始化中",
    "need_login": True,
    "current_ip": "192.168.1.1",
    "is_fetching": False,   # 获取二维码的锁
    "is_validating": False  # 验证Cookie的锁
}

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# 引入线程锁，确保线程安全
validating_lock = threading.Lock()
fetching_lock = threading.Lock()

# ================= PushPlus 推送模块 =================
def send_pushplus(title, content):
    if not PUSHPLUS_TOKEN:
        return
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "markdown"
    }
    try:
        res = requests.post(url, json=data, timeout=10)
        if res.status_code == 200:
            logger.info("✅ PushPlus 推送成功")
        else:
            logger.error(f"❌ PushPlus 推送失败: {res.text}")
    except Exception as e:
        logger.error(f"❌ PushPlus 推送异常: {e}")

# ================= 智能 Cookie 模块 =================
def load_cookies():
    if os.path.exists(COOKIE_FILE) and os.path.isfile(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                
                if content.startswith("[") and content.endswith("]"):
                    return json.loads(content)
                
                logger.info("检测到非 JSON 格式 Cookie，启动智能解析...")
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
                    logger.info(f"成功解析 {len(parsed_cookies)} 个字段并格式化！")
                    save_cookies(parsed_cookies)
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
    if not fetching_lock.acquire(blocking=False):
        logger.info("已有获取二维码的任务在运行，跳过...")
        return

    GLOBAL_STATE["is_fetching"] = True
    GLOBAL_STATE["status"] = "正在获取登录二维码"
    GLOBAL_STATE["need_login"] = True
    
    if os.path.exists(QR_PATH) and os.path.isfile(QR_PATH):
        try: os.remove(QR_PATH)
        except: pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            logger.info("打开企业微信后台...")
            page.goto(WECHAT_URLS[0], timeout=60000)
            
            iframe_element = page.frame_locator('iframe[src*="login_qrcode"]')
            qr_img_element = iframe_element.locator('.qrcode_login_img')
            qr_img_element.wait_for(state="visible", timeout=15000)
            
            qr_url = urljoin(page.url, qr_img_element.get_attribute('src'))
            resp = requests.get(qr_url)
            if resp.status_code == 200:
                with open(QR_PATH, "wb") as f:
                    f.write(resp.content)
                logger.info("二维码已保存，等待扫码")
                GLOBAL_STATE["status"] = "请扫码登录"
                
                now_time = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                qr_content = f"### 🚨 企微助手状态异常\n\n**当前状态**：等待扫码 ⏳\n**触发时间**：{now_time}\n\n---\n**Cookie 已失效**，系统已提取最新二维码。\n👉 **请前往 Web 面板扫码！**\n\n💡 若遇滑块拦截，请手动注入原生 Cookie。"
                send_pushplus("⚠️ 企微助手：请扫码登录", qr_content)
            
            wait_time = 0
            success = False
            while wait_time < 120:
                if not GLOBAL_STATE["need_login"]:
                    logger.info("检测到登录状态已被接管，主动取消二维码等待！")
                    break
                
                # 💡 核心修复：防止重定向参数干扰，只要 URL 里彻底没有 login 且没有滑块验证码关键词 vcpage，才判定为登录跳转成功
                if "login" not in page.url and "vcpage" not in page.url:
                    success = True
                    break
                    
                page.wait_for_timeout(1000) 
                wait_time += 1
            
            if success:
                logger.info("扫码登录成功！")
                save_cookies(context.cookies())
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"
                send_pushplus("✅ 企微助手：接管成功", f"### 🎉 企微接管成功\n\n**状态**：正常运行中 🟢\n**时间**：{datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n系统已成功接管企业微信，继续监控动态 IP。")
            elif GLOBAL_STATE["need_login"]:
                logger.error("用户未在 2 分钟内扫码，流程结束。")
                GLOBAL_STATE["status"] = "登录超时，请手动刷新或注入Cookie"
                if os.path.exists(COOKIE_FILE) and os.path.isfile(COOKIE_FILE):
                    try: os.remove(COOKIE_FILE)
                    except: pass
            
            browser.close()
    except Exception as e:
        logger.error(f"登录流程出错: {e}")
        if GLOBAL_STATE["need_login"]:
            GLOBAL_STATE["status"] = f"获取二维码异常: {str(e)[:30]}..."
    finally:
        GLOBAL_STATE["is_fetching"] = False
        fetching_lock.release()

def update_wechat_ip(ip_address):
    if not validating_lock.acquire(blocking=False):
        logger.info("当前已有验证任务在运行，跳过...")
        return
        
    GLOBAL_STATE["is_validating"] = True
    need_relogin = False

    try:
        cookies = load_cookies()
        if not cookies:
            logger.error("没有 Cookie，准备触发登录流程")
            need_relogin = True
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            
            page.goto(WECHAT_URLS[0], timeout=60000)
            page.wait_for_timeout(2000) 
            
            # 💡 核心修复：增强对失效/验证码拦截页面的双重判定 (防止 vcpage 滑块页死锁)
            if page.locator('.login_stage_title_text').is_visible() or "login" in page.url or "vcpage" in page.url:
                logger.info("检测到 Cookie 失效 (或验证码拦截)...")
                need_relogin = True
                if os.path.exists(COOKIE_FILE) and os.path.isfile(COOKIE_FILE):
                    try: os.remove(COOKIE_FILE)
                    except: pass
            else:
                GLOBAL_STATE["need_login"] = False
                GLOBAL_STATE["status"] = "正常运行中"

                if not ip_address:
                    logger.info("Cookie 验证通过。暂未获取公网IP，本次跳过修改。")
                else:
                    for url in WECHAT_URLS:
                        if not url.strip(): continue
                        logger.info("正在配置应用IP...")
                        page.goto(url, timeout=60000)
                        
                        page.wait_for_selector('div.app_card_operate.js_show_ipConfig_dialog', timeout=15000)
                        page.locator('div.app_card_operate.js_show_ipConfig_dialog').first.click()
                        page.wait_for_selector('textarea.js_ipConfig_textarea', timeout=10000)
                        
                        input_area = page.locator('textarea.js_ipConfig_textarea').first
                        confirm_btn = page.locator('.js_ipConfig_confirmBtn').first
                        
                        existing_ip = input_area.input_value()
                        if OVERWRITE:
                            input_area.fill(ip_address)
                        else:
                            ips = list(filter(None, set(existing_ip.split(';')) if existing_ip else []))
                            if ip_address not in ips:
                                ips.append(ip_address)
                            # 💡 核心修复：追加模式下最多限制保留最新 10 个 IP，防止超出企微字符限制爆仓
                            ips = ips[-10:] 
                            input_area.fill(';'.join(ips))
                        
                        confirm_btn.click()
                        page.wait_for_timeout(1000)
                        logger.info(f"✅ 应用可信 IP 配置成功: {ip_address}")
            browser.close()
    except Exception as e:
        logger.error(f"更新/验证过程出错: {e}")
        GLOBAL_STATE["status"] = f"运行异常: {str(e)[:30]}..."
        # 如果报错且疑似由于登录失效引起，允许清空进入重新登录状态
        if "timeout" in str(e).lower() or "selector" in str(e).lower():
            need_relogin = True
    finally:
        GLOBAL_STATE["is_validating"] = False
        validating_lock.release()

    if need_relogin:
        GLOBAL_STATE["current_ip"] = "192.168.1.1" # 验证失败时还原初始 IP，防止状态不一致
        do_login_and_save_cookie()

def check_task():
    if not WECHAT_URLS or WECHAT_URLS[0] == "":
        return

    # 热重载检测：检测本地新注入的 Cookie
    if GLOBAL_STATE["need_login"] and load_cookies():
        logger.info("检测到本地被手动注入了 Cookie，进入验证流程...")
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "正在验证手动注入的 Cookie..."

    if GLOBAL_STATE["need_login"]:
        do_login_and_save_cookie()
        return

    logger.info("开始检测公网IP...")
    current_ip = get_public_ip()
    
    if current_ip:
        logger.info(f"当前公网IP: {current_ip}")
        # 💡 核心修复：即使 IP 没变化，只要状态健康也强制进入 update_wechat_ip 去刷新页面，达到主动保活 Cookie 的作用
        if current_ip != GLOBAL_STATE["current_ip"] or GLOBAL_STATE["status"] != "正常运行中":
            logger.info("准备同步配置或验证最新 Cookie...")
            GLOBAL_STATE["current_ip"] = current_ip
            update_wechat_ip(current_ip)
        else:
            logger.info("IP 未发生变化，强制执行一次企微后台访问以监控保活和检验 Cookie...")
            update_wechat_ip(current_ip)
    else:
        logger.error("获取公网IP失败")
        if GLOBAL_STATE["status"] != "正常运行中":
            logger.info("无IP但需要验证Cookie，强制启动校验...")
            update_wechat_ip(None)

# ================= Web 服务 =================
@app.route('/')
def index():
    return render_template(
        'index.html',
        status=GLOBAL_STATE["status"],
        need_login=GLOBAL_STATE["need_login"],
        current_ip=GLOBAL_STATE["current_ip"],
        qr_exists=os.path.exists(QR_PATH) and os.path.isfile(QR_PATH),
        is_fetching=GLOBAL_STATE.get("is_fetching", False),
        time=int(time.time())
    )

# 💡 新增功能：处理网页提交的手动 Cookie
@app.route('/update_cookie', methods=['POST'])
def update_cookie():
    cookie_data = request.form.get('cookie_data', '')
    if cookie_data.strip():
        try:
            # 写入原始字符串，依靠 load_cookies 函数强大的自动转换机制转成 Playwright 格式
            with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                f.write(cookie_data.strip())
            
            logger.info("收到来自网页提交的手动 Cookie。")
            
            # 主动改变全局状态并触发后台验证
            GLOBAL_STATE["need_login"] = False
            GLOBAL_STATE["status"] = "已接收注入的 Cookie，正在后台验证..."
            
            # 如果当前没有在验证中，可以考虑立刻触发一波检查
            scheduler.add_job(
                func=check_task, 
                trigger='date', 
                run_date=datetime.now(pytz.timezone("Asia/Shanghai"))
            )
            
            # 返回并重定向回首页，带上一个时间戳参数强制刷新
            return redirect(f'/?t={int(time.time())}')
        except Exception as e:
            logger.error(f"写入手动 Cookie 发生错误: {e}")
            return f"保存 Cookie 时发生错误: {str(e)} <br><a href='/'>点击返回</a>", 500
    else:
        return "提交的 Cookie 不能为空！<br><a href='/'>点击返回</a>", 400

@app.route('/qr.png')
def serve_qr():
    if os.path.exists(QR_PATH) and os.path.isfile(QR_PATH):
        return send_from_directory(DATA_DIR, 'qr.png')
    return "QR not found", 404

@app.route('/refresh_qr_api')
def refresh_qr_api():
    if load_cookies():
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "正在验证手动注入的 Cookie..."
        scheduler.add_job(func=check_task, trigger='date', run_date=datetime.now(pytz.timezone("Asia/Shanghai")))
        return {"status": "success", "msg": "已识别到 Cookie，正在后台验证..."}

    if not GLOBAL_STATE["need_login"]:
        return {"status": "success", "msg": "当前Cookie有效，无需刷新！"}
    
    if GLOBAL_STATE.get("is_fetching"):
        return {"status": "info", "msg": "后台正在努力获取二维码中..."}

    if os.path.exists(QR_PATH) and os.path.isfile(QR_PATH):
        try: os.remove(QR_PATH)
        except: pass
        
    scheduler.add_job(
        func=do_login_and_save_cookie, 
        trigger='date', 
        run_date=datetime.now(pytz.timezone("Asia/Shanghai"))
    )
    return {"status": "success", "msg": "已触发重新获取"}

if __name__ == "__main__":
    # 💡 核心修复：异常重启后，先给一个不确定状态，强制清除 current_ip，逼迫初始化时走一次真实验证
    if not load_cookies():
        GLOBAL_STATE["need_login"] = True
        GLOBAL_STATE["status"] = "未登录，请扫码"
    else:
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "设备重启，等待首次验证 Cookie..."
        GLOBAL_STATE["current_ip"] = None  

    # 💡 核心修复：先在主线程安全地同步跑完第一次检测判定，建立正确的 GLOBAL_STATE
    try:
        logger.info("系统启动，正在执行首次同步网络检测...")
        check_task()
    except Exception as init_err:
        logger.error(f"首次初始化检测失败（网络未完全就绪）: {init_err}")

    # 主线程跑完后，再稳妥地启动定时调度和 Flask
    scheduler.add_job(func=check_task, trigger=CronTrigger.from_crontab(CHECK_CRON), name="IP_Checker")
    scheduler.start()

    app.run(host='0.0.0.0', port=8080)
