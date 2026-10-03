import asyncio
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime,timezone

class Request:
 def __init__(self,data):self.data=data
 async def json(self):return self.data
class HTTPException(Exception):pass

with tempfile.TemporaryDirectory() as folder:
 db=Path(folder)/'test.db'
 def get_db():
  conn=sqlite3.connect(db);conn.row_factory=sqlite3.Row;return conn
 conn=get_db();conn.executescript('''
 CREATE TABLE favourite_djs(username TEXT,name TEXT,platform TEXT,profile_url TEXT,live_url TEXT,genre TEXT,enabled INTEGER,created_at TEXT,UNIQUE(platform,username));
 CREATE TABLE live_djs(username TEXT,name TEXT,platform TEXT,url TEXT,genre TEXT,viewers INTEGER,started_at TEXT,updated_at TEXT,UNIQUE(platform,username));
 CREATE TABLE tiktok_live_stats(username TEXT UNIQUE,auto_dormant INTEGER,next_check_at TEXT,last_result TEXT);
 ''');conn.close()
 observed=True;fail=False;disable=False
 def check(username):
  conn=get_db();assert conn.execute('SELECT 1 FROM favourite_djs WHERE username=?',(username,)).fetchone();conn.close()
  if fail:raise RuntimeError('upstream unavailable')
  if disable:
   conn=get_db();conn.execute('UPDATE favourite_djs SET enabled=0 WHERE username=?',(username,));conn.commit();conn.close()
  return observed
 async def twitch(names):return {names[0].lower():{'user_name':'Twitch DJ','viewer_count':42,'started_at':'2026-10-04T00:00:00Z'}} if observed else {}
 async def youtube(url):return {'channel':'YouTube DJ','webpage_url':'https://www.youtube.com/watch?v=123','concurrent_view_count':7} if observed else None
 def account(value):
  platform,username=value.split(':')
  return {'platform':platform,'username':username,'profile_url':'https://example.com/'+username,'live_url':'https://example.com/'+username+'/live'}
 def stats(conn,username,now):conn.execute('INSERT OR IGNORE INTO tiktok_live_stats(username) VALUES (?)',(username,))
 async def import_dj(username):
  conn=get_db();conn.execute('INSERT OR REPLACE INTO favourite_djs(username,name,platform,profile_url,live_url,enabled) VALUES (?,?,?,?,?,1)',(username,username,'TikTok','https://example.com','https://example.com/live'));conn.commit();conn.close();return {'username':username}
 namespace=dict(asyncio=asyncio,get_db=get_db,datetime=datetime,timezone=timezone,
  _run_tiktok_is_live_check=check,_record_tiktok_live_check=lambda *args:None,
  invalid_tiktok_account_counts={},offline_miss_counts={},fetch_twitch_live_streams=twitch,get_youtube_live_info=youtube,
  sqlite3=sqlite3,Request=Request,HTTPException=HTTPException,_admin_dj_account=account,
  _ensure_tiktok_stats_row=stats,import_tiktok_dj=import_dj)
 exec(Path('backend/immediate-dj-check.py').read_text(),namespace)
 for name in ['admin_add_dj','add_favourite','import_tiktok_favourite']:
  exec(Path('backend/'+name+'-fixture.py').read_text(),namespace)
 async def run():
  global observed,fail,disable
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:newdj'}))
  assert result['live_check']=={'status':'live','live':True};assert 'LIVE' in result['message']
  conn=get_db();first=conn.execute('SELECT started_at FROM live_djs WHERE username="newdj"').fetchone()[0];conn.close()
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:newdj'}));assert result['action']=='already_enabled'
  conn=get_db();assert conn.execute('SELECT started_at FROM live_djs WHERE username="newdj"').fetchone()[0]==first;conn.close()
  observed=False
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:offline'}));assert result['live_check']['status']=='offline'
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:newdj'}));assert result['live_check']['status']=='retained_live'
  fail=True
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:error'}));assert result['ok'];assert result['live_check']['live'] is None
  fail=False;observed=True;disable=True
  result=await namespace['admin_add_dj'](Request({'username':'TikTok:disabled'}));assert result['live_check']['status']=='skipped'
  disable=False
  result=await namespace['admin_add_dj'](Request({'username':'Twitch:twitchdj'}));assert result['live_check']['live'] is True
  result=await namespace['admin_add_dj'](Request({'username':'YouTube:youtubedj'}));assert result['live_check']['live'] is True
  result=await namespace['add_favourite'](Request({'username':'generic','name':'Generic DJ','profile_url':'https://example.com/generic','live_url':'https://example.com/generic/live'}));assert result['live_check']['live'] is True
  result=await namespace['import_tiktok_favourite'](Request({'username':'imported'}));assert result['live_check']['live'] is True
  conn=get_db();assert not conn.execute('SELECT 1 FROM live_djs WHERE username IN ("offline","error","disabled")').fetchone();assert conn.execute('SELECT viewers FROM live_djs WHERE username="twitchdj"').fetchone()[0]==42;conn.close()
  print('PASS: all add routes check after saving; live promotion, offline/error handling, session preservation, disabled-during-check guard, Twitch and YouTube metadata.')
 asyncio.run(run())
