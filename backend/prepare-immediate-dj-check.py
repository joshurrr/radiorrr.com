from pathlib import Path
import ast
import difflib

remote=Path(r'\\192.168.7.10\radiorouter\app\main.py')
s=remote.read_text(encoding='utf-8');original=s
assert 'async def scan_added_dj_immediately(' not in s
helper=Path('backend/immediate-dj-check.py').read_text(encoding='utf-8')
anchor='@app.post("/api/admin/djs/add")'
s=s.replace(anchor,helper+'\n\n'+anchor,1)
# Transform only the add endpoints, leaving routine profile refresh unchanged.
tree=ast.parse(s)
for name in ['admin_add_dj','add_favourite','import_tiktok_favourite']:
 node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
 before=ast.get_source_segment(s,node);after=before
 if name=='admin_add_dj':
  after=after.replace('"""Add or re-enable a TikTok or Twitch DJ without doing a network lookup."""','"""Save/re-enable a DJ, then check live status immediately."""')
  after=after.replace('    return {\n        "ok": True,','    live_check = await scan_added_dj_immediately(actual_username, platform)\n    message += " " + added_dj_live_message(live_check)\n\n    return {\n        "ok": True,\n        "live_check": live_check,',1)
 elif name=='add_favourite':
  after=after.replace('    return {\n        "status": "ok",','    live_check = await scan_added_dj_immediately(username, platform)\n\n    return {\n        "status": "ok",\n        "live_check": live_check,',1)
 else:
  after=after.replace('        return {\n            "status": "ok",','        live_check = await scan_added_dj_immediately(profile.get("username") or str(username).lstrip("@"), "TikTok")\n\n        return {\n            "status": "ok",\n            "live_check": live_check,',1)
 assert after!=before,name
 s=s.replace(before,after,1)
 # Function offsets change after each transformation.
 tree=ast.parse(s)
s=s.replace("'Updating the Radio RRR DJ database.'","'Saving the DJ and checking live status immediately…'")
ast.parse(s)
Path('backend/immediate-dj-check.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='a/app/main.py',tofile='b/app/main.py')),encoding='utf-8')
# Keep test fixtures in the local workspace, without copying the entire backend.
for name in ['admin_add_dj','add_favourite','import_tiktok_favourite']:
 node=next(n for n in ast.parse(s).body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
 Path('backend/'+name+'-fixture.py').write_text(ast.get_source_segment(s,node)+'\n',encoding='utf-8')
print('Prepared and syntax-checked immediate DJ scan patch for all three add endpoints.')
