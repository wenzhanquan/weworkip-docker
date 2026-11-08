import os,tempfile,unittest
from pathlib import Path
import main as appmod
from unittest.mock import patch

class LoginTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.state=appmod.GLOBAL_STATE.copy()
  appmod.GLOBAL_STATE.update(login_active=False,login_requested=False,check_active=False,cookie_status='valid',need_login=False)
  self.original=appmod.COOKIE_FILE
  appmod.COOKIE_FILE=str(Path(self.tmp.name)/'cookies.json')
  Path(appmod.COOKIE_FILE).write_text('existing=preserve')
  self.client=appmod.app.test_client()
 def tearDown(self):
  appmod.COOKIE_FILE=self.original
  appmod.GLOBAL_STATE.clear();appmod.GLOBAL_STATE.update(self.state)
  appmod.scheduler.remove_all_jobs()
  self.tmp.cleanup()
 def test_refresh_button_returns_json_and_preserves_saved_cookie(self):
  r=self.client.post('/refresh_qr_api')
  self.assertEqual(r.status_code,200)
  self.assertEqual(r.json['status'],'ok')
  self.assertEqual(Path(appmod.COOKIE_FILE).read_text(),'existing=preserve')
  self.assertEqual(appmod.GLOBAL_STATE['cookie_status'],'valid')
  self.assertFalse(appmod.GLOBAL_STATE['need_login'])
 def test_panel_and_remote_entry_do_not_require_password(self):
  c=appmod.app.test_client()
  self.assertEqual(c.get('/').status_code,200)
  self.assertEqual(c.get('/remote-login').status_code,200)
  self.assertEqual(c.get('/auth-check').status_code,200)
 def test_cross_site_mutation_is_rejected(self):
  r=self.client.post('/refresh_qr_api',headers={'Origin':'https://attacker.invalid'})
  self.assertEqual(r.status_code,403)
 def test_remote_authorization_accepts_session(self):
  self.assertEqual(self.client.get('/auth-check').status_code,200)
 def test_refresh_during_active_login_does_not_abort_session(self):
  appmod.GLOBAL_STATE['login_active']=True
  try:
   r=self.client.post('/refresh_qr_api')
   self.assertEqual(r.status_code,200)
   self.assertTrue(r.json['active'])
  finally:appmod.GLOBAL_STATE['login_active']=False
 def test_status_exposes_cookie_and_actual_ip_observations_and_log_times(self):
  appmod.emit_event('INFO','测试检查完成')
  data=self.client.get('/status_api').json
  self.assertIn('cookie_checked_at',data)
  self.assertIn('public_ip',data)
  self.assertIn('apps',data)
  self.assertIn('sync_checked_at',data)
  log=self.client.get('/logs_api').json['logs'][-1]
  self.assertEqual(log['message'],'测试检查完成')
  self.assertRegex(log['time'],r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
 def test_busy_worker_releases_queued_login_instead_of_sticking(self):
  appmod.GLOBAL_STATE['login_requested']=True
  appmod.TASK_LOCK.acquire()
  try:
   with patch.object(appmod,'WECHAT_URLS',['https://fixture.invalid/app']):appmod.check_task(force_login=True)
  finally:appmod.TASK_LOCK.release()
  self.assertFalse(appmod.GLOBAL_STATE['login_requested'])
  self.assertEqual(appmod.GLOBAL_STATE['login_status'],'error')
 def test_busy_worker_releases_unvalidated_cookie_candidate(self):
  appmod.GLOBAL_STATE['candidate_pending']=True
  appmod.TASK_LOCK.acquire()
  try:
   with patch.object(appmod,'WECHAT_URLS',['https://fixture.invalid/app']):appmod.check_task(candidate_cookies=[{'name':'fixture','value':'candidate'}])
  finally:appmod.TASK_LOCK.release()
  self.assertFalse(appmod.GLOBAL_STATE['candidate_pending'])
  self.assertEqual(Path(appmod.COOKIE_FILE).read_text(),'existing=preserve')

if __name__=='__main__':unittest.main()
