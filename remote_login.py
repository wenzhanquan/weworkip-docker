"""An isolated interactive login never replaces the active Cookie until verified."""
import time,os
from monitoring import timestamp

ADMIN_SELECTOR='div.app_card_operate.js_show_ipConfig_dialog'

def run_remote_login(owner):
    def state(**changes):
        if hasattr(owner,'update_state'):owner.update_state(**changes)
        else:owner.GLOBAL_STATE.update(changes)
    def event(level,message):
        if hasattr(owner,'emit_event'):owner.emit_event(level,message)
        else:owner.logger.log(20,message)

    owner.GLOBAL_STATE.setdefault('need_login',True)
    state(login_active=True,login_requested=False,login_status='opening',login_message='正在打开独立登录窗口，原 Cookie 保留')
    owner.remove_qr_file()
    event('INFO','打开独立登录窗口；未清除现有 Cookie')
    try:
        with owner.sync_playwright() as p:
            browser=p.chromium.launch(headless=False,args=owner.BROWSER_ARGS+['--window-position=0,0','--window-size=1100,820','--start-maximized'])
            try:
                context=browser.new_context(no_viewport=True,locale='zh-CN')
                page=context.new_page()
                page.goto(owner.WECHAT_URLS[0],wait_until='domcontentloaded',timeout=60000)
                state(login_status='waiting',login_message='请扫码；如需滑块，可打开远程浏览器完成验证')
                owner.send_pushplus('企微IP助手：登录窗口已打开','请打开面板扫码，或进入远程浏览器完成验证。现有 Cookie 保留。')
                deadline=time.monotonic()+600
                last_qr=0
                qr_announced=False
                while time.monotonic()<deadline and not owner.ABORT_QR_WAIT:
                    if page.is_closed():
                        state(login_status='closed',login_message='登录窗口已关闭，现有 Cookie 保留')
                        return
                    if page.locator(ADMIN_SELECTOR).is_visible():
                        page.wait_for_timeout(1000)
                        if owner.save_cookies(context.cookies()) is False:
                            state(login_status='error',login_message='登录完成但 Cookie 保存失败，请检查数据目录')
                            event('ERROR','新登录的 Cookie 保存失败；未报告登录成功')
                            return
                        state(need_login=False,cookie_status='valid',cookie_checked_at=timestamp(),cookie_saved_at=timestamp(),login_status='success',login_message='登录成功，新的 Cookie 已验证并替换保存',status='新登录成功，正在等待读取并核对可信 IP')
                        event('INFO','新登录已确认进入应用后台，Cookie 已替换保存；准备核对可信 IP')
                        owner.schedule_check('Post_Login_Check',force_update=True)
                        return
                    if time.monotonic()-last_qr>3:
                        last_qr=time.monotonic()
                        for frame in page.frames:
                            qr=frame.locator('.qrcode_login_img')
                            if qr.count()==1 and qr.is_visible():
                                try:
                                    tmp=owner.QR_PATH+'.tmp.png'
                                    qr.screenshot(path=tmp,timeout=1500)
                                    os.replace(tmp,owner.QR_PATH)
                                    if not qr_announced:
                                        event('INFO','登录二维码已生成；等待手机扫码，原 Cookie 保留')
                                        qr_announced=True
                                except Exception:pass
                                break
                    page.wait_for_timeout(500)
                state(login_status='closed',login_message='登录已结束，未替换现有 Cookie')
                event('INFO','登录等待已结束；未替换现有 Cookie')
            finally:browser.close()
    except Exception as exc:
        state(login_status='error',login_message='登录页面打开失败，可重新获取二维码')
        event('ERROR','登录页面异常：'+type(exc).__name__+'；现有 Cookie 保留')
    finally:
        owner.remove_qr_file()
        state(login_active=False,login_requested=False)
