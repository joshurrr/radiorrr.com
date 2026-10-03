import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from uuid import uuid4

class HTTPException(Exception):
 def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail
class App:
 def post(self,path):return lambda fn:fn
class Request:pass

with tempfile.TemporaryDirectory() as folder:
 db=Path(folder)/'queue.db'
 def get_db():
  conn=sqlite3.connect(db);conn.row_factory=sqlite3.Row;return conn
 conn=get_db();conn.executescript('''CREATE TABLE favourite_djs(username TEXT,platform TEXT,enabled INTEGER);
 CREATE TABLE live_djs(username TEXT,platform TEXT,name TEXT);''')
 for username in ['first','clicked','regular','failure']:
  conn.execute('INSERT INTO favourite_djs VALUES (?,?,1)',(username,'TikTok'))
  conn.execute('INSERT INTO live_djs VALUES (?,?,?)',(username,'TikTok',username))
 conn.commit();conn.close()
 ns=dict(asyncio=asyncio,get_db=get_db,time=time,uuid4=uuid4,HTTPException=HTTPException,Request=Request,app=App())
 exec(Path('backend/priority-audio-api.py').read_text(),ns)
 request=ns['request_priority_audio_scan'];claim=ns['claim_priority_audio_scan'];complete=ns['complete_priority_audio_scan']
 first=request('@FIRST','tiktok');assert first['status']=='queued'
 assert request('first')['request_id']==first['request_id']
 second=request('clicked')
 job=claim();assert job['username']=='first'
 assert claim()['username']=='clicked'
 assert claim() is None
 complete(first['request_id'],True);assert request('first')['status']=='complete'
 complete(second['request_id'],False);assert request('clicked')['status']=='error'
 # An expired worker lease can be reclaimed; after three failed leases it stops.
 conn=get_db();conn.execute("UPDATE priority_audio_scans SET status='scanning',lease_until=0,attempts=1 WHERE username='first'");conn.commit();conn.close()
 assert claim()['username']=='first'
 conn=get_db();conn.execute("UPDATE priority_audio_scans SET lease_until=0,attempts=3 WHERE username='first'");conn.commit();conn.close()
 assert claim() is None
 request('regular');conn=get_db();conn.execute("UPDATE favourite_djs SET enabled=0 WHERE username='regular'");conn.commit();conn.close();assert claim() is None
 try:request('missing')
 except HTTPException as error:assert error.status_code==404
 else:raise AssertionError('Unknown DJ accepted')
 # Priority worker consumes jobs immediately and publishes all analysis fields.
 jobs=[{'username':'failure','platform':'TikTok','request_id':'bad'},{'username':'clicked','platform':'TikTok','request_id':'good'},None]
 events=[]
 def post(path,data):
  if path.endswith('/next'):return {'dj':jobs.pop(0)}
  events.append(('complete',path,data));return {'ok':True}
 def analyse(dj):
  events.append(('analyse',dj['username']))
  if dj['username']=='failure':raise RuntimeError('no audio')
  return [{'genre':'House'}],{'bpm':125},{'speech_ratio':.2}
 scout=dict(priority_post=post,analyse_dj=analyse,
  post_results=lambda *args:events.append(('publish',args)),
  print_results=lambda *args:None,release_relay=lambda dj:events.append(('release',dj['username'])))
 exec(Path('backend/priority-audio-scout.py').read_text(),scout)
 scout['priority_post']=post
 scanned=scout['scan_priority_djs']();assert scanned=={('tiktok','clicked')}
 assert [e[2]['success'] for e in events if e[0]=='complete']==[False,True]
 assert next(e for e in events if e[0]=='publish')[1][2]['bpm']==125
 assert len([e for e in events if e[0]=='release'])==2
 print('PASS: queue validation, duplicate suppression, FIFO priority, leases/recovery, failed-job cooldown, disabled-DJ exclusion, full analysis publication and worker cleanup.')

# Run both real patched main loops: a click arrives mid-round and must go next.
for label in ['root','current']:
    events=[]
    pending=[{'username':'firstclick','platform':'TikTok','request_id':'one'}]
    def post(path,data):
        if path.endswith('/next'):
            return {'dj':pending.pop(0) if pending else None}
        return {'ok':True}
    def analyse(dj):
        events.append(dj['username'])
        if dj['username']=='regular1':
            pending.append({'username':'lateclick','platform':'TikTok','request_id':'two'})
        return [],{'bpm':120},{'speech_ratio':0}
    def sleep(seconds):
        if 'regular2' in events:raise KeyboardInterrupt()
    scout=dict(priority_post=post,analyse_dj=analyse,post_results=lambda *args:None,
        print_results=lambda *args:None,release_relay=lambda *args:None,
        start_engine_heartbeat=lambda:None,RADIO_ROUTER_URL='http://test',SAMPLE_SECONDS=30,
        SKIP_CURRENT_RELAY=True,SCAN_IDLE_SECONDS=5,time=type('Clock',(),{'sleep':staticmethod(sleep)})(),
        get_live_djs=lambda:[{'username':'regular1','platform':'TikTok'},{'username':'regular2','platform':'TikTok'}],
        get_current_relay_identity=lambda:None)
    exec(Path('backend/priority-audio-scout.py').read_text(),scout)
    scout['priority_post']=post
    exec(Path('backend/priority-scout-main-'+label+'-fixture.py').read_text(),scout)
    scout['main']()
    assert events==['firstclick','regular1','lateclick','regular2'],events
print('PASS: both scout main loops service clicks mid-round before the next normal DJ.')
