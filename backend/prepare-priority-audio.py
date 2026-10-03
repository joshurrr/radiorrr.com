from pathlib import Path
import ast
import difflib

root=Path(r'\\192.168.7.10\radiorouter\app\main.py')
original=root.read_text(encoding='utf-8');s=original
assert 'def request_priority_audio_scan(' not in s
helper=Path('backend/priority-audio-api.py').read_text()
anchor='@app.post("/api/admin/djs/add")'
s=s.replace(anchor,helper+'\n\n'+anchor,1)
needle="            print(f'[New DJ] LIVE: {platform} @{username} added to the live list')"
assert needle in s
s=s.replace(needle,"""            try:
                request_priority_audio_scan(username, platform)
            except Exception as error:
                print(f'[Priority Audio] Could not queue newly added DJ: {error}')
"""+needle,1)
ast.parse(s)
Path('backend/priority-audio-router.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='a/app/main.py',tofile='b/app/main.py')),encoding='utf-8')
helper=Path('backend/priority-audio-scout.py').read_text()
for location,label in [(Path(r'\\192.168.7.10\rrr-genredetect\detector-candidate-scout.py'),'root'),(Path(r'\\192.168.7.10\rrr-genredetect\current\detector-candidate-scout.py'),'current')]:
 original=location.read_text(encoding='utf-8');s=original
 assert 'def scan_priority_djs(' not in s
 s=s.replace('def main():',helper+'\n\ndef main():',1)
 s=s.replace('            live_djs = get_live_djs()', '            priority_scanned = scan_priority_djs()\n            live_djs = get_live_djs()',1)
 needle='            for index, dj in enumerate(scan_djs, start=1):'
 assert needle in s
 s=s.replace(needle,needle+'''\n                priority_scanned.update(scan_priority_djs())
                if (str(dj.get("platform") or "TikTok").casefold(), dj["username"].casefold()) in priority_scanned:
                    continue
''',1)
 ast.parse(s)
 main=next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='main')
 Path('backend/priority-scout-main-'+label+'-fixture.py').write_text(ast.get_source_segment(s,main),encoding='utf-8')
 Path('backend/priority-audio-scout-'+label+'.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='a/'+label+'/detector-candidate-scout.py',tofile='b/'+label+'/detector-candidate-scout.py')),encoding='utf-8')
print('Prepared syntax-checked priority queue and both scout patches.')
