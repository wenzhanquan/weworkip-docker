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
    "login_active": False,
    "login_requested": False,
    "login_status": "idle",
    "check_active": False,
    "candidate_pending": False,
    "cookie_status": "unknown",
    "cookie_checked_at": None,
    "cookie_saved_at": None,
    "public_ip": None,
    "ip_checked_at": None,
    "sync_status": "unknown",
    "sync_checked_at": None,
    "last_sync_success": None,
    "apps": [],
    "last_error": "",
    "need_login": True,
    "current_ip": load_last_ip(),
    "last_check": "尚未检测"
}

STATE_LOCK = threading.RLock()
from monitoring import EventLog, timestamp
EVENTS = EventLog(os.path.join(DATA_DIR, 'events.jsonl'), secrets=[PUSH_PLUS_TOKEN])

def update_state(**changes):
    with STATE_LOCK:
        GLOBAL_STATE.update(changes)

def emit_event(level, message):
    EVENTS.append(level, message)
    logger.log(getattr(logging, level), message)

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone=TZ)
from security import install_security
install_security(app, DATA_DIR)

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
        temporary = COOKIE_FILE + '.new'
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, COOKIE_FILE)
        return True
    except Exception as e:
        logger.error(f"保存 Cookie 失败: {e}")
        return False

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
def schedule_check(job_id, **kwargs):
    scheduler.add_job(func=check_task, kwargs=kwargs, trigger='date',
        run_date=datetime.now(TZ)+timedelta(seconds=2), id=job_id, replace_existing=True)

def do_login_and_save_cookie():
    global ABORT_QR_WAIT
    ABORT_QR_WAIT = False
    from remote_login import run_remote_login
    run_remote_login(sys.modules[__name__])


def sync_or_keepalive(ip_address, only_keep_alive=False, candidate_cookies=None, read_only=False):
    from sync_engine import sync_apps
    return sync_apps(sys.modules[__name__], ip_address, candidate_cookies, read_only)


def check_task(force_update=False, force_login=False, candidate_cookies=None, read_only=False):
    if not WECHAT_URLS:
        update_state(status="未配置企业微信应用地址", last_error="WECHAT_URLS 缺失")
        emit_event('ERROR', '未配置企业微信应用地址，无法检查')
        return
    if not TASK_LOCK.acquire(timeout=1):
        if force_login:
            update_state(login_requested=False,login_status='error',login_message='其他检查任务正在运行，本次登录窗口未打开，请稍后重新获取')
            emit_event('WARNING','登录窗口未打开：后台任务繁忙，已解除等待状态，可稍后重试；Cookie 保留')
        elif candidate_cookies is not None:
            update_state(candidate_pending=False,status='后台任务繁忙，新 Cookie 尚未验证，请稍后重新提交')
            emit_event('WARNING','新 Cookie 尚未验证：后台任务繁忙，请稍后重新提交；原 Cookie 保留')
        else:
            emit_event('INFO', '已有检查或登录任务运行，本次检查跳过')
        return
    update_state(check_active=True)
    try:
        if force_login:
            update_state(login_requested=False)
            do_login_and_save_cookie()
            return
        update_state(last_check=timestamp(), status='正在检测公网 IP 并验证企业微信后台')
        emit_event('INFO', '开始检查公网 IP、Cookie 和企业微信可信 IP')
        current_ip = get_public_ip()
        if not current_ip:
            update_state(status='获取公网 IP 失败，本次未修改企业微信配置', sync_status='unconfirmed', last_error='公网 IP 检测失败')
            emit_event('ERROR', '获取公网 IP 失败，保留 Cookie 和可信 IP，等待下次重试')
            return
        update_state(public_ip=current_ip, ip_checked_at=timestamp())
        emit_event('INFO', '检测到当前公网 IP：'+current_ip)
        sync_or_keepalive(current_ip, candidate_cookies=candidate_cookies, read_only=read_only)
    finally:
        update_state(check_active=False)
        if candidate_cookies is not None:update_state(candidate_pending=False)
        TASK_LOCK.release()

# ================= Web 路由服务 =================
@app.get('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon', max_age=86400)

@app.route('/')
def index():
    return render_template(
        'index.html',
        status=GLOBAL_STATE["status"],
        need_login=GLOBAL_STATE["need_login"],
        current_ip=GLOBAL_STATE["current_ip"],
        last_check=GLOBAL_STATE["last_check"],
        qr_exists=os.path.exists(QR_PATH),
        is_fetching=GLOBAL_STATE.get("login_active", False),
        time=int(time.time())
    )

@app.route('/qr.png')
def serve_qr():
    if os.path.exists(QR_PATH):
        response=send_from_directory(DATA_DIR, 'qr.png', max_age=0)
        response.headers['Cache-Control']='no-store'
        return response
    return "QR not found", 404

@app.route('/update_cookie', methods=['POST'])
def update_cookie():
    with STATE_LOCK:
        if GLOBAL_STATE['login_active'] or GLOBAL_STATE['login_requested'] or GLOBAL_STATE['check_active'] or GLOBAL_STATE['candidate_pending']:
            return jsonify(error='当前任务尚未结束，请稍后提交 Cookie'), 409
        payload=request.get_json(silent=True) if request.is_json else request.form
        cookie_str=''
        for key in ['cookie','cookie_str','cookie_data','content','text','data','cookieContent']:
            value=(payload or {}).get(key)
            if isinstance(value,str) and value.strip():
                cookie_str=value.strip()
                break
        cookies=parse_cookie_string(cookie_str)
        if not cookies:return jsonify(error='Cookie 为空或格式不正确'),400
        update_state(candidate_pending=True,status='新 Cookie 正在等待验证，原 Cookie 保持不变')
        schedule_check('Manual_Cookie_Check',candidate_cookies=cookies)
    emit_event('INFO','收到新 Cookie；将在验证成功后替换保存，当前文件保持不变')
    return jsonify(status='ok',msg='已提交验证；验证成功后才替换原 Cookie'),202


@app.route('/check_ip_api', methods=['POST'])
def check_ip_api():
    if GLOBAL_STATE['check_active'] or GLOBAL_STATE['login_active'] or GLOBAL_STATE['login_requested'] or GLOBAL_STATE['candidate_pending']:
        return jsonify(error='当前已有任务运行，请等待完成或先结束登录窗口'),409
    schedule_check('Manual_IP_Check',force_update=True)
    emit_event('INFO','已收到立即检查并同步的请求')
    return jsonify(status='ok',msg='已安排检查 Cookie 和可信 IP；结果会写入运行日志')


@app.route('/refresh_qr_api', methods=['POST'])
@app.route('/refresh_qr', methods=['POST'])
@app.route('/relogin', methods=['POST'])
@app.route('/refresh_qrcode_api', methods=['POST'])
def refresh_qr():
    with STATE_LOCK:
        if GLOBAL_STATE['login_active'] or GLOBAL_STATE['login_requested']:
            return jsonify(status='ok',active=True,msg='登录窗口已打开；二维码过期时可结束后重新获取')
        if GLOBAL_STATE['check_active'] or GLOBAL_STATE['candidate_pending']:
            return jsonify(error='正在检查后台，请检查结束后再获取二维码'),409
        remove_qr_file()
        update_state(login_requested=True,login_status='queued')
        schedule_check('Manual_Relogin',force_login=True)
    emit_event('INFO','已请求新的登录窗口；现有 Cookie 保留，成功登录后才替换')
    return jsonify(status='ok',msg='正在准备登录二维码；现有 Cookie 不会被清除')


@app.get('/status_api')
def status_api():
    with STATE_LOCK:data=dict(GLOBAL_STATE)
    data['qr_exists']=bool(GLOBAL_STATE['login_active'] and os.path.exists(QR_PATH))
    data['cookie_file_exists']=os.path.exists(COOKIE_FILE)
    data['check_cron']=CHECK_CRON
    job=scheduler.get_job('IP_Checker')
    next_run=getattr(job,'next_run_time',None)
    data['next_check']=next_run.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S') if next_run else None
    data['server_time']=timestamp()
    return jsonify(data)


@app.get('/logs_api')
def logs_api():
    return jsonify(logs=EVENTS.snapshot())


@app.post('/cancel_login_api')
def cancel_login():
    global ABORT_QR_WAIT
    ABORT_QR_WAIT=True
    if GLOBAL_STATE['login_requested']:
        try:scheduler.remove_job('Manual_Relogin')
        except Exception:pass
        update_state(login_requested=False,login_status='idle')
    emit_event('INFO','已请求结束登录窗口；原 Cookie 保留')
    return jsonify(status='ok',msg='正在结束登录窗口，原 Cookie 保留')


@app.get('/remote-login')
def remote_login_page():
    return render_template('remote.html')

if __name__ == "__main__":
    emit_event("INFO", "服务启动，将使用已保存的 Cookie 验证后台并读取可信 IP")
    if load_cookies():
        GLOBAL_STATE["need_login"] = False
        GLOBAL_STATE["status"] = "已找到 Cookie，等待实际验证（文件存在不代表有效）"
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

    app.run(host='127.0.0.1', port=8081, use_reloader=False, threaded=True)
