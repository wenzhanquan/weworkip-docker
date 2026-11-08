"""Bounded, timestamped operational events. Never store browser cookies."""
import json,os,re,threading
from datetime import datetime,timezone,timedelta
from pathlib import Path

def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

class EventLog:
    def __init__(self,path,secrets=()):
        self.path=Path(path)
        self.secrets=[s for s in secrets if s]
        self.lock=threading.RLock()
        self.items=[]
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines()[-200:]:
                try:self.items.append(json.loads(line))
                except (ValueError,TypeError):pass

    def append(self,level,message):
        message=str(message)
        for secret in self.secrets:message=message.replace(secret,'[已隐藏]')
        message=re.sub(r'(?i)(wwrtx\.[\w.]+\s*[=:]\s*)[^;\s,]+',r'\1[已隐藏]',message)
        item={'time':timestamp(),'level':level,'message':message[:800]}
        with self.lock:
            self.items=(self.items+[item])[-200:]
            temporary=self.path.with_suffix('.new')
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                with temporary.open('w',encoding='utf-8') as f:
                    for entry in self.items:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
                os.chmod(temporary,0o600)
                os.replace(temporary,self.path)
            except OSError:pass
        return item

    def snapshot(self):
        with self.lock:return list(self.items)
