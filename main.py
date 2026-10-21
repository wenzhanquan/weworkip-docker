import os
import sys
import json
import time
import re
import logging
import threading
from datetime import datetime, timedelta
import pytz
import requests
from flask import Flask, render_template, send_from_directory, request, redirect, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ================= 配置与日志初始化 =================
# 强制实时输出日志，防止 Docker 容器缓存导致看不到 log
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# 开启 Flask 路由访问日志，方便观察按钮点击是否生效
log = logging.getLogger('werkzeug')
log.setLevel(logging.INFO)

TZ = pytz.timezone("Asia/Shanghai")

WECHAT_URLS = [u.strip() for u in os.environ.get("WECHAT_URLS", "").split(",") if u.strip()]
CHECK_CRON = os.environ.get("CHECK_CRON", "*/11 * * * *")
OVERWRITE = os.environ.get("OVERWRITE", "True").lower() == "true"
PUSH_PLUS_TOKEN = (os.environ.get("PUSH_PLUS_TOKEN") or os.environ.get("PUSHPLUS_TOKEN") or "").strip()

DATA_DIR = "/app/data"
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
QR_PATH = os.path.join(DATA_DIR, "qr.png")
IP_FILE = os.path.join(DATA_DIR, "last_ip.txt")

os.makedirs(DATA_DIR, exist_ok=True)

TASK_LOCK = threading.Lock()
ABORT_QR_WAIT = False

# 移除会导致容器内 close() 死锁的 --no-zygote 参数，仅保留核心安全参数
BROWSER_ARGS = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu'
]

def load_last_ip():
    if os.path.exists(IP_FILE):
        try:
            with open(IP_FILE, "r", encoding="utf-8") as f:
                ip = f.read().strip()
                if ip:
                    return ip
        except Exception:
            pass
    return "未记录"

def save_last_ip(ip):
    try:
        with open(IP_FILE, "w", encoding="utf-8") as f:
            f.write(ip.strip())
    except Exception as e:
        logger.error(f"保存IP记录失败: {e}")

def remove_qr_file():
    if os.path.exists(QR_PATH):
        try:
            os.remove(QR_PATH)
        except Exception:
            pass

GLOBAL_STATE = {
    "status": "初始化中",
    "need_login": True,
    "current_ip": load_last_ip(),
    "last_check": "尚未检测"
}

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone=TZ)

# ================= 核心工具函数 =================
def send_pushplus(title, content):
    if not PUSH_PLUS_TOKEN:
        return
    try:
        url = "http://www.pushplus.plus/send"
        data = {
            "token": PUSH_PLUS_TOKEN,
            "title": title,
            "content": content,
            "template": "html"
        }
        resp = requests.post(url, json=data, timeout=10)
        if resp.status_code == 200:
            logger.info("✅ PushPlus 推送成功")
        else:
            logger.warning(f"PushPlus 推送返回异常状态码: {resp.status_code}")
    except Exception as e:
        logger.error(f"PushPlus 推送失败: {e}")

def parse_cookie_string(cookie_str):
    """兼容 JSON 数组格式与浏览器 Header 原始键值对格式"""
    cookie_str = cookie_str.strip()
    if not cookie_str:
        return []
    if cookie_str.startswith("[") and cookie_str.endswith("]"):
        try:
            return json.loads(cookie_str)
        except Exception:
            pass
            
    logger.info("检测到非 JSON 格式 Cookie，启动智能解析...")
    cookies = []
    # 兼容换行符或分号分隔
    raw_items = re.split(r'[;\n\r]+', cookie_str)
    for item in raw_items:
        item = item.strip()
        if not item or '=' not in item:
            continue
        k, v = item.split('=', 1)
        k, v = k.strip(), v.strip()
        if k.lower() in ['expires', 'max-age', 'path', 'domain', 'secure', 'httponly', 'samesite', 'cookie']:
            continue
        cookies.append({
            "name": k,
            "value": v,
            "domain": ".work.weixin.qq.com",
            "path": "/"
        })
    logger.info(f"成功解析 {len(cookies)} 个 Cookie 字段并格式化！")
    return cookies

def load_cookies():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return parse_cookie_string(content)
        except Exception as e:
            logger.error(f"读取 Cookie 文件失败: {e}")
    return []

def save_cookies(cookies):
    try:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 Cookie 失败: {e}")

def get_public_ip():
    urls = [
        "https://4.ipw.cn",
        "https://myip.ipip.net",
        "https://ddns.oray.com/checkip",
        "https://ip.3322.net"
    ]
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
    global ABORT_QR_WAIT
    ABORT_QR_WAIT = False
    GLOBAL_STATE["status"] = "正在获取登录二维码..."
    GLOBAL_STATE["need_login"] = True
    remove_qr_file()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            logger.info("打开企业微信后台获取二维码...")
            page.goto(WECHAT_URLS[0], wait_until="domcontentloaded", timeout=60000)
            
            iframe_element = page.frame_locator('iframe[src*="login_qrcode"], iframe#wx_reg')
            qr_img_element = iframe_element.locator('.qrcode_login_img')
            qr_img_element.wait_for(state="visible", timeout=25000)
            
            # 等待二维码图片完全渲染出来
            time.sleep(1)
            
            # 【核心修复】直接对浏览器内的二维码元素进行截图！
            # 严禁使用 requests.get 二次下载，否则会导致服务端刷新二维码造成前后端不一致
            qr_img_element.screenshot(path=QR_PATH)
            logger.info("✅ 二维码已通过元素截图保存，等待扫码...")
            GLOBAL_STATE["status"] = "请扫码登录"
            
            send_pushplus(
                "企微IP助手：需要扫码登录",
                f"Cookie 已失效，请前往 Web 面板扫码或手动注入 Cookie。<br>当前公网IP: {GLOBAL_STATE['current_ip']}"
            )
            
            # 循环等待扫码（共等待 120 秒）
            for _ in range(240): # 240 * 0.5s = 120秒
                if ABORT_QR_WAIT or not GLOBAL_STATE["need_login"]:
                    logger.info("检测到登录状态被手动接管或强制刷新，立即中止当前二维码等待！")
                    browser.close()
                    return
                
                # 通过检查 cookies 中是否已经生成了企微后台的核心登录凭证 (wwrtx.sid) 或 URL 跳转来判定登录成功
                current_cookies = context.cookies()
                cookie_names = [c.get("name") for c in current_cookies]
                is_logged_in = ("wwrtx.sid" in cookie_names or "wwrtx.ltype" in cookie_names) and ("frame" in page.url)
                
                if is_logged_in:
                    try:
                        if not page.locator('.login_stage_title_text').is_visible():
                            logger.info("✅ 扫码登录成功！正在保存持久化 Cookie...")
                            time.sleep(2) # 等待所有子域 Cookie 写入完毕
                            save_cookies(context.cookies())
                            remove_qr_file()
                            GLOBAL_STATE["need_login"] = False
                            GLOBAL_STATE["status"] = "正常运行中"
                            browser.close()
                            
                            # 登录成功后立即安排一次 IP 同步
                            scheduler.add_job(
                                func=check_task,
                                kwargs={"force_update": True},
                                trigger='date',
                                run_date=datetime.now(TZ) + timedelta(seconds=2),
                                id="Post_Login_Check",
                                replace_existing=True
                            )
                            return
                    except Exception:
                        pass
                time.sleep(0.5)
                
            logger.warning("用户未在 2 分钟内扫码，本次等待流程结束。")
            GLOBAL_STATE["status"] = "二维码已过期，请点击刷新二维码或手动注入"
            browser.close()
    except Exception as e:
        logger.error(f"二维码获取流程异常: {e}")
        GLOBAL_STATE["status"] = f"获取二维码失败: 请点击刷新重试"

def sync_or_keepalive(ip_address, only_keep_alive=False):
    """
    only_keep_alive=True: 仅访问应用详情页刷新 Cookie，不点击配置弹窗
    only_keep_alive=False: 走完整的修改可信 IP 流程
    """
    cookies = load_cookies()
    if not cookies:
        logger.warning("本地无可用 Cookie，转入登录流程...")
        do_login_and_save_cookie()
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            context.add_cookies(cookies)
            page = context.new_page()
            
            # 1. 访问第一个应用管理地址
            page.goto(WECHAT_URLS[0], wait_until="domcontentloaded", timeout=45000)
            
            # 等待后台核心元素或登录框出现（二选一，最多等 25 秒）
            page.wait_for_selector(
                'div.app_card_operate.js_show_ipConfig_dialog, .login_stage_title_text, iframe[src*="login_qrcode"]',
                timeout=25000
            )

            # 2. 检查是否掉线跳回了登录页
            if page.locator('.login_stage_title_text').is_visible() or page.locator('iframe[src*="login_qrcode"]').is_visible() or "loginpage" in page.url.lower():
                logger.warning("⚠️ 检测到企微登录框，Cookie 确已失效！")
                browser.close()
                do_login_and_save_cookie()
                return False
            
            # 3. 确认在线！等待 2 秒后将最新 Cookie 覆写回本地实现续期
            time.sleep(2)
            GLOBAL_STATE["need_login"] = False
            GLOBAL_STATE["status"] = "正常运行中"
            GLOBAL_STATE["last_check"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            save_cookies(context.cookies())
            remove_qr_file()
            
            # 如果只是例行保活，到此直接结束！
            if only_keep_alive:
                logger.info(f"💚 Cookie 保活与续期成功 (当前公网IP: {ip_address} 未变动)")
                browser.close()
                return True

            # 4. 当 IP 发生变化或强制触发时，遍历所有应用修改可信 IP
            for idx, url in enumerate(WECHAT_URLS):
                logger.info(f"正在为第 {idx+1} 个应用配置可信 IP: {ip_address} ...")
                if idx > 0:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(2)
                
                operate_btn = page.locator('div.app_card_operate.js_show_ipConfig_dialog')
                operate_btn.wait_for(state="visible", timeout=30000)
                operate_btn.click()
                
                input_area = page.locator('textarea.js_ipConfig_textarea')
                input_area.wait_for(state="visible", timeout=15000)
                confirm_btn = page.locator('.js_ipConfig_confirmBtn')
                
                existing_ip = input_area.input_value()
                if OVERWRITE:
                    input_area.fill(ip_address)
                else:
                    ips = [i.strip() for i in existing_ip.split(';') if i.strip()]
                    if ip_address not in ips:
                        ips.append(ip_address)
                    input_area.fill(';'.join(ips))
                
                confirm_btn.click()
                time.sleep(2)
                logger.info(f"✅ 应用 {idx+1} 可信 IP 配置成功: {ip_address}")
            
            save_cookies(context.cookies())
            old_ip = GLOBAL_STATE["current_ip"]
            GLOBAL_STATE["current_ip"] = ip_address
            save_last_ip(ip_address)
            
            if old_ip != ip_address:
                send_pushplus("企微IP助手：公网IP已更新", f"可信 IP 已自动同步为：<b>{ip_address}</b><br>(原 IP: {old_ip})")
                
            browser.close()
            return True

    except PlaywrightTimeoutError as te:
        logger.warning(f"⏳ 访问企微后台网络超时，保留现有 Cookie 等待下次重试: {te}")
        return False
    except Exception as e:
        logger.error(f"❌ 执行过程发生异常，保留现有 Cookie: {e}")
        return False

def check_task(force_update=False, force_login=False):
    if not WECHAT_URLS:
        logger.error("未配置 WECHAT_URLS 环境变量！")
        GLOBAL_STATE["status"] = "配置缺失 (WECHAT_URLS)"
        return

    # 尝试获取锁，最多等待 5 秒（确保手动中断旧任务后，新任务能顺利接管锁）
    if not TASK_LOCK.acquire(timeout=5):
        logger.info("当前已有任务正在运行且未释放锁，跳过本次触发...")
        return

    try:
        if force_login or (GLOBAL_STATE["need_login"] and not load_cookies()):
            do_login_and_save_cookie()
            return

        logger.info("开始检测公网IP...")
        current_ip = get_public_ip()
        if not current_ip:
            logger.error("获取公网IP失败，跳过本次检测")
            return

        logger.info(f"当前公网IP: {current_ip}")
        ip_changed = (current_ip != GLOBAL_STATE["current_ip"]) or (GLOBAL_STATE["current_ip"] in ["192.168.1.1", "未记录", "获取中..."])
        
        if force_update or ip_changed:
            logger.info(f"🔄 准备同步配置或验证最新 Cookie (当前IP: {current_ip}, 历史记录: {GLOBAL_STATE['current_ip']})...")
            sync_or_keepalive(current_ip, only_keep_alive=False)
        else:
            sync_or_keepalive(current_ip, only_keep_alive=True)
    finally:
        TASK_LOCK.release()

# ================= Web 路由服务 =================
@app.route('/')
def index():
    return render_template(
        'index.html',
        status=GLOBAL_STATE["status"],
        need_login=GLOBAL_STATE["need_login"],
        current_ip=GLOBAL_STATE["current_ip"],
        last_check=GLOBAL_STATE["last_check"],
        qr_exists=os.path.exists(QR_PATH),
        time=int(time.time())
    )

@app.route('/qr.png')
def serve_qr():
    if os.path.exists(QR_PATH):
        return send_from_directory(DATA_DIR, 'qr.png')
    return "QR not found", 404

@app.route('/update_cookie', methods=['POST'])
def update_cookie():
    global ABORT_QR_WAIT
    # 兼容不同表单字段名或 JSON 提交
    cookie_str = (request.form.get('cookie') or request.form.get('cookie_str') or '').strip()
    if not cookie_str and request.is_json:
        cookie_str = (request.json.get('cookie') or '').strip()
        
    logger.info(f"📥 收到网页提交的手动 Cookie 请求 (长度: {len(cookie_str)})")
    if cookie_str:
        cookies = parse_cookie_string(cookie_str)
        if cookies:
            save_cookies(cookies)
            remove_qr_file()
            GLOBAL_STATE["need_login"] = False
            GLOBAL_STATE["status"] = "已注入Cookie，正在后台验证并同步IP..."
            ABORT_QR_WAIT = True # 通知扫码线程立刻退出
            
            scheduler.add_job(
                func=check_task,
                kwargs={"force_update": True},
                trigger='date',
                run_date=datetime.now(TZ) + timedelta(seconds=1),
                id="Manual_Cookie_Check",
                replace_existing=True
            )
    return redirect(f'/?t={int(time.time())}')

@app.route('/check_ip_api', methods=['GET', 'POST'])
def check_ip_api():
    logger.info("🔍 收到网页端发起的立即检测 IP 请求...")
    scheduler.add_job(
        func=check_task,
        kwargs={"force_update": True},
        trigger='date',
        run_date=datetime.now(TZ) + timedelta(seconds=1),
        id="Manual_IP_Check",
        replace_existing=True
    )
    msg = "检测与同步任务已在后台触发，请稍后刷新页面查看结果！"
    return jsonify({"status": "ok", "msg": msg, "message": msg})

# 同时绑定 /refresh_qr, /relogin, /refresh_qrcode_api，彻底杜绝前端按钮路由不匹配或卡住
@app.route('/refresh_qr', methods=['GET', 'POST'])
@app.route('/relogin', methods=['GET', 'POST'])
@app.route('/refresh_qrcode_api', methods=['GET', 'POST'])
def refresh_qr():
    global ABORT_QR_WAIT
    logger.info("🔄 收到网页端发起的强制刷新二维码请求...")
    ABORT_QR_WAIT = True
    remove_qr_file()
    if os.path.exists(COOKIE_FILE):
        try:
            os.remove(COOKIE_FILE)
        except Exception:
            pass
    GLOBAL_STATE["need_login"] = True
    GLOBAL_STATE["status"] = "正在重新获取二维码，请稍候刷新页面..."
    
    scheduler.add_job(
        func=check_task,
        kwargs={"force_login": True},
        trigger='date',
        run_date=datetime.now(TZ) + timedelta(seconds=1),
        id="Manual_Relogin",
        replace_existing=True
    )
    
    # 如果前端是通过 fetch/ajax 异步请求的，返回 JSON 防止前端卡死；如果是普通链接跳转，则重定向回首页
    if request.path.endswith('_api') or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        msg = "正在后台重新抓取二维码，请等待约 5 秒后刷新页面！"
        return jsonify({"status": "ok", "msg": msg, "message": msg})
    return redirect(f'/?t={int(time.time())}')

if __name__ == "__main__":
    logger.info("🚀 系统启动，优先启动 Web 服务，首次检测将延迟 5 秒执行...")
    if load_cookies():
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "正常运行中"
    else:
        GLOBAL_STATE["need_login"] = True

    scheduler.add_job(
        func=check_task,
        trigger=CronTrigger.from_crontab(CHECK_CRON, timezone=TZ),
        id="IP_Checker",
        replace_existing=True
    )
    scheduler.add_job(
        func=check_task,
        trigger='date',
        run_date=datetime.now(TZ) + timedelta(seconds=5),
        id="Init_Checker",
        replace_existing=True
    )
    scheduler.start()

    app.run(host='0.0.0.0', port=8080, use_reloader=False)
