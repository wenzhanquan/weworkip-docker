"""Verify live admin access and read back trusted IP values before reporting success."""
import re
from monitoring import timestamp

ADMIN='div.app_card_operate.js_show_ipConfig_dialog'
AREA='textarea.js_ipConfig_textarea'
LOGIN='.login_stage_title_text, iframe[src*="login_qrcode"], iframe#wx_reg'

def split_ips(value):
    return [s for s in re.split(r'[;；,，\s]+',value.strip()) if s]

def matches(values,ip,overwrite):
    return set(values)=={ip} if overwrite else ip in values

def visit(page,url):
    page.goto(url,wait_until='domcontentloaded',timeout=45000)
    page.wait_for_selector(ADMIN+', '+LOGIN,timeout=25000)
    if page.locator(ADMIN).is_visible():return True
    return False

def read_ips(page):
    page.locator(ADMIN).click()
    area=page.locator(AREA)
    area.wait_for(state='visible',timeout=15000)
    return split_ips(area.input_value())

def sync_apps(owner,ip_address,candidate_cookies=None,read_only=False):
    candidate=candidate_cookies is not None
    cookies=candidate_cookies if candidate else owner.load_cookies()
    if not cookies:
        owner.update_state(cookie_status='missing',need_login=True,sync_status='waiting',status='尚未保存 Cookie，请选择一种方式登录')
        owner.emit_event('WARNING','未保存 Cookie，暂不能读取企业微信可信 IP')
        return False
    verified=False
    wrote=False
    observed=[]
    owner.update_state(sync_status='checking',status='正在验证 Cookie 并读取企业微信可信 IP',last_error='')
    try:
        with owner.sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=owner.BROWSER_ARGS)
            try:
                context=browser.new_context(locale='zh-CN')
                context.add_cookies(cookies)
                page=context.new_page()
                for index,url in enumerate(owner.WECHAT_URLS):
                    if not visit(page,url):
                        if candidate:
                            owner.update_state(status='提交的 Cookie 未通过验证，原 Cookie 已保留',sync_status='unknown',last_error='新 Cookie 无法进入应用后台')
                            owner.emit_event('WARNING','提交的 Cookie 无效；未替换原 Cookie')
                        else:
                            owner.update_state(cookie_status='invalid',cookie_checked_at=timestamp(),need_login=True,sync_status='waiting',status='Cookie 已失效，请重新登录')
                            owner.emit_event('WARNING','Cookie 验证失败：企业微信返回登录页面；保留文件等待重新登录')
                            owner.schedule_check('Expired_Cookie_Login',force_login=True)
                        return False
                    if not verified:
                        if not owner.save_cookies(context.cookies()):
                            raise RuntimeError('Cookie 验证通过但保存失败，请检查数据目录')
                        verified=True
                        owner.update_state(cookie_status='valid',cookie_checked_at=timestamp(),cookie_saved_at=timestamp(),need_login=False)
                        owner.emit_event('INFO',('新 Cookie 验证成功并已替换保存' if candidate else 'Cookie 有效：已成功访问企业微信应用后台'))
                    values=read_ips(page)
                    owner.emit_event('INFO',f'应用 {index+1} 后台可信 IP：'+('；'.join(values) or '未配置'))
                    changed=False
                    if not matches(values,ip_address,owner.OVERWRITE) and not read_only:
                        desired=[ip_address] if owner.OVERWRITE else list(dict.fromkeys(values+[ip_address]))
                        owner.emit_event('INFO',f'应用 {index+1} 可信 IP 与当前公网 IP 不一致，正在保存')
                        page.locator(AREA).fill(';'.join(desired))
                        page.locator('.js_ipConfig_confirmBtn').click()
                        page.locator(AREA).wait_for(state='hidden',timeout=15000)
                        # A click is not evidence of persistence. Reload from the backend.
                        if not visit(page,url):raise RuntimeError('保存后重新访问后台失败')
                        values=read_ips(page)
                        changed=True
                        wrote=True
                    ok=matches(values,ip_address,owner.OVERWRITE)
                    observed.append({'app':index+1,'trusted_ips':values,'matches':ok,'checked_at':timestamp()})
                    owner.update_state(apps=list(observed),sync_checked_at=timestamp())
                    if not ok:
                        message=f'应用 {index+1} 可信 IP 未匹配当前公网 IP'+('，保存后重新读取仍不一致' if changed else '')
                        owner.update_state(sync_status='mismatch' if read_only else 'failed',last_error=message,status=message)
                        owner.emit_event('ERROR',message)
                        return False
                    owner.emit_event('INFO',f'应用 {index+1} '+('更新后已重新读取确认' if changed else '已核对一致')+f'：{ip_address}')
                if not observed:raise RuntimeError('尚未配置需要检查的应用')
                owner.update_state(sync_status='matched',sync_checked_at=timestamp(),last_check=timestamp(),status='Cookie 有效，所有应用可信 IP 已核对一致',last_error='')
                if not read_only:
                    old_ip=owner.GLOBAL_STATE['current_ip']
                    owner.save_last_ip(ip_address)
                    owner.update_state(current_ip=ip_address,last_sync_success=timestamp() if wrote else owner.GLOBAL_STATE.get('last_sync_success'))
                    if old_ip!=ip_address:owner.send_pushplus('企微IP助手：可信 IP 已确认',f'已重新读取并确认所有应用的可信 IP 为：{ip_address}')
                owner.emit_event('INFO','本次检查完成：Cookie 有效，所有应用可信 IP 与当前公网 IP 一致')
                return True
            finally:browser.close()
    except Exception as exc:
        # Do not publish Playwright traces, URLs or cookies to the public event log.
        error=str(exc) if isinstance(exc,RuntimeError) else ('访问企业微信后台超时' if isinstance(exc,owner.PlaywrightTimeoutError) else '企业微信后台检查异常：'+type(exc).__name__)
        changes={'sync_status':'failed','last_error':error,'status':error+'；保留已保存的 Cookie'}
        if not verified and not candidate:changes['cookie_status']='unconfirmed'
        owner.update_state(**changes)
        owner.emit_event('ERROR',error+'；未报告同步成功，原 Cookie 文件保留')
        return False
