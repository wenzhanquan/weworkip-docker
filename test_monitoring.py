import tempfile,unittest
from pathlib import Path
from monitoring import EventLog

class EventTests(unittest.TestCase):
 def test_log_survives_restart_and_redacts_credentials(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events.jsonl'
   log=EventLog(p,secrets=['private-push-token'])
   log.append('INFO','检查 private-push-token wwrtx.sid=private-session')
   item=EventLog(p).snapshot()[0]
   self.assertNotIn('private-push-token',item['message'])
   self.assertNotIn('private-session',item['message'])
   self.assertRegex(item['time'],r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
 def test_history_is_bounded(self):
  with tempfile.TemporaryDirectory() as d:
   log=EventLog(Path(d)/'events.jsonl')
   for i in range(205):log.append('INFO','event '+str(i))
   self.assertEqual(len(log.snapshot()),200)
   self.assertEqual(log.snapshot()[0]['message'],'event 5')

if __name__=='__main__':unittest.main()
