import tempfile,threading,unittest,json,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import remote_login,main

class Handler(BaseHTTPRequestHandler):
 stored='192.0.2.10'
 def do_GET(self):
  body=(b'<div class="login_stage_title_text">login</div>' if self.path=='/login' else b'<div class="app_card_operate js_show_ipConfig_dialog">Application</div>')
  if self.path.startswith('/config'):
   target='/ignore-save' if self.path=='/config-fail' else '/save'
   body=f"""<div class="app_card_operate js_show_ipConfig_dialog" onclick="document.querySelector('#dialog').hidden=false">Configure</div><div id="dialog" hidden><textarea class="js_ipConfig_textarea">{self.stored}</textarea><button class="js_ipConfig_confirmBtn" onclick="fetch('{target}',{{method:'POST',body:document.querySelector('textarea').value}}).then(()=>document.querySelector('#dialog').hidden=true)">Save</button></div>""".encode()
  self.send_response(200);self.send_header('Set-Cookie','wwrtx.sid=test-fixture; HttpOnly; Path=/');self.end_headers();self.wfile.write(body)
 def do_POST(self):
  value=self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()
  if self.path=='/save':type(self).stored=value
  self.send_response(200);self.end_headers();self.wfile.write(b'OK')
 def log_message(self,*args):pass

class BrowserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  threading.Thread(target=cls.server.serve_forever,daemon=True).start()
  cls.url='http://127.0.0.1:'+str(cls.server.server_port)
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
 def owner(self,url,abort=False):
  self.saved=[];self.jobs=[]
  return SimpleNamespace(GLOBAL_STATE={},ABORT_QR_WAIT=abort,sync_playwright=sync_playwright,BROWSER_ARGS=['--no-sandbox','--disable-dev-shm-usage'],WECHAT_URLS=[url],QR_PATH='/tmp/fixture-qr.png',remove_qr_file=lambda:None,send_pushplus=lambda *a:None,save_cookies=lambda c:self.saved.append(c),schedule_check=lambda *a,**kw:self.jobs.append((a,kw)),logger=main.logger)
 def test_verified_admin_page_saves_httponly_cookie_and_schedules_sync(self):
  o=self.owner(self.url)
  remote_login.run_remote_login(o)
  self.assertEqual(self.saved[0][0]['name'],'wwrtx.sid')
  self.assertTrue(self.saved[0][0]['httpOnly'])
  self.assertFalse(o.GLOBAL_STATE['need_login'])
  self.assertFalse(o.GLOBAL_STATE['login_active'])
  self.assertEqual(self.jobs[0][1],{'force_update':True})
 def test_navigation_failure_releases_login_and_preserves_cookie(self):
  o=self.owner('http://127.0.0.1:1')
  remote_login.run_remote_login(o)
  self.assertFalse(o.GLOBAL_STATE['login_active'])
  self.assertEqual(self.saved,[])
  self.assertEqual(self.jobs,[])
 def test_cancel_releases_login_without_saving_unverified_cookie(self):
  o=self.owner(self.url+'/login',True)
  remote_login.run_remote_login(o)
  self.assertFalse(o.GLOBAL_STATE['login_active'])
  self.assertEqual(self.saved,[])
 def test_cookie_write_failure_does_not_report_login_saved(self):
  o=self.owner(self.url)
  o.save_cookies=lambda cookies:False
  remote_login.run_remote_login(o)
  self.assertTrue(o.GLOBAL_STATE['need_login'])
  self.assertFalse(o.GLOBAL_STATE['login_active'])
  self.assertEqual(self.jobs,[])
 def test_canceled_session_removes_last_qr_image(self):
  with tempfile.TemporaryDirectory() as tmp:
   qr=Path(tmp)/'qr.png'
   o=self.owner(self.url+'/login',True)
   o.QR_PATH=str(qr)
   o.remove_qr_file=lambda:qr.unlink(missing_ok=True)
   o.send_pushplus=lambda *a:qr.write_bytes(b'last-session-qr')
   remote_login.run_remote_login(o)
   self.assertFalse(qr.exists())
 def test_expired_cookie_schedules_login_after_playwright_context(self):
  with patch.object(main,'WECHAT_URLS',[self.url+'/login']),patch.object(main,'load_cookies',return_value=[{'name':'old','value':'old','domain':'127.0.0.1','path':'/'}]),patch.object(main,'schedule_check') as scheduled,patch.object(main,'do_login_and_save_cookie',side_effect=AssertionError('Nested login is forbidden')):
   self.assertFalse(main.sync_or_keepalive('192.0.2.1',True))
   scheduled.assert_called_once_with('Expired_Cookie_Login',force_login=True)
 def test_invalid_injected_cookie_does_not_replace_working_cookie(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(main,'WECHAT_URLS',[self.url+'/login']),patch.object(main,'COOKIE_FILE',str(Path(tmp)/'cookies.json')):
   original='[{"name":"old","value":"preserved","domain":"127.0.0.1","path":"/"}]'
   Path(main.COOKIE_FILE).write_text(original)
   self.assertFalse(main.sync_or_keepalive('192.0.2.20',candidate_cookies=[{'name':'bad','value':'invalid','domain':'127.0.0.1','path':'/'}]))
   self.assertEqual(Path(main.COOKIE_FILE).read_text(),original)
 def test_successful_ip_write_is_reloaded_and_verified(self):
  self.exercise_sync('/config',True)
 def test_rejected_ip_write_is_not_reported_as_synchronized(self):
  self.exercise_sync('/config-fail',False)
 def exercise_sync(self,path,expected):
  Handler.stored='192.0.2.10'
  with tempfile.TemporaryDirectory() as tmp,patch.object(main,'WECHAT_URLS',[self.url+path]),patch.object(main,'COOKIE_FILE',str(Path(tmp)/'cookies.json')),patch.object(main,'IP_FILE',str(Path(tmp)/'last_ip.txt')),patch.object(main,'send_pushplus'):
   Path(main.COOKIE_FILE).write_text('[{"name":"sid","value":"fixture","domain":"127.0.0.1","path":"/"}]')
   result=main.sync_or_keepalive('192.0.2.20')
   self.assertEqual(result,expected)
   if expected:
    self.assertEqual(main.GLOBAL_STATE['apps'][0]['trusted_ips'],['192.0.2.20'])
    self.assertEqual(main.GLOBAL_STATE['sync_status'],'matched')
    self.assertEqual(Path(main.IP_FILE).read_text(),'192.0.2.20')
   else:
    self.assertEqual(main.GLOBAL_STATE['sync_status'],'failed')
    self.assertFalse(Path(main.IP_FILE).exists())

if __name__=='__main__':unittest.main()
