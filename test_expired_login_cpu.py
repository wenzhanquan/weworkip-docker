import unittest
from unittest.mock import MagicMock,patch
import sync_engine
class ExpiredLoginTests(unittest.TestCase):
 def owner(self):
  o=MagicMock();o.GLOBAL_STATE={'cookie_status':'unknown'};o.WECHAT_URLS=['http://fixture/login'];o.BROWSER_ARGS=[]
  o.load_cookies.return_value=[{'name':'fixture'}]
  o.update_state.side_effect=lambda **kw:o.GLOBAL_STATE.update(kw)
  return o
 def test_repeated_invalid_cookie_does_not_reopen_browser(self):
  o=self.owner()
  with patch.object(sync_engine,'visit',return_value=False):
   sync_engine.sync_apps(o,'192.0.2.1');sync_engine.sync_apps(o,'192.0.2.1')
  o.schedule_check.assert_called_once_with('Expired_Cookie_Login',force_login=True)
 def test_new_expiration_after_valid_cookie_can_prompt_again(self):
  o=self.owner()
  with patch.object(sync_engine,'visit',return_value=False):
   sync_engine.sync_apps(o,'192.0.2.1');o.GLOBAL_STATE['cookie_status']='valid';sync_engine.sync_apps(o,'192.0.2.1')
  self.assertEqual(o.schedule_check.call_count,2)
 def test_bad_candidate_does_not_open_login_or_replace_cookie(self):
  o=self.owner();o.GLOBAL_STATE['cookie_status']='valid'
  with patch.object(sync_engine,'visit',return_value=False):sync_engine.sync_apps(o,'192.0.2.1',candidate_cookies=[{'name':'bad'}])
  o.schedule_check.assert_not_called();o.save_cookies.assert_not_called();self.assertEqual(o.GLOBAL_STATE['cookie_status'],'valid')
if __name__=='__main__':unittest.main()
