"""Apply only the reviewed stuck-frame functions, preserving other router edits."""
import ast
import argparse
from datetime import datetime
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('path', nargs='?', default=r'\\192.168.7.10\radiorouter\app\main.py')
parser.add_argument('--music', action='store_true', help='Upgrade an already installed audio-upload fix')
args = parser.parse_args()
target = Path(args.path)
before = (root / ('stuck-frame-before-music.py' if args.music else 'stuck-frame-original-functions.py')).read_text(encoding='utf-8')
after = (root / 'stuck-frame-api-functions.py').read_text(encoding='utf-8')
marker = '@app.post("/api/tools/stuck-frame-effect")'
before_helpers, before_api = before.split(marker, 1)
after_helpers, after_api = after.split(marker, 1)
source = target.read_text(encoding='utf-8')
if source.count(before_helpers) != 1 or source.count(marker + before_api) != 1:
    raise SystemExit('Stuck-frame functions changed since review. No files modified.')
updated = source.replace(before_helpers, after_helpers, 1).replace(marker + before_api, marker + after_api, 1)
ast.parse(updated)
backup = target.with_name(target.name + '.before-stuck-frame-audio-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
shutil.copy2(target, backup)
if backup.read_bytes() != target.read_bytes():
    raise SystemExit('Backup verification failed. No source modified.')
target.write_text(updated, encoding='utf-8')
assert target.read_text(encoding='utf-8') == updated
print(f'Applied and verified. Backup: {backup}')
