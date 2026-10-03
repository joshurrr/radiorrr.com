from pathlib import Path
import ast
import os
import hashlib
import re
from datetime import datetime


def apply_patch(original, patch):
    lines = original.splitlines(keepends=True)
    output = []
    cursor = 0
    hunks = patch.splitlines(keepends=True)
    i = 0
    while i < len(hunks):
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', hunks[i])
        if not match:
            i += 1
            continue
        start = int(match.group(1)) - 1
        assert start >= cursor, 'Overlapping patch hunks'
        output.extend(lines[cursor:start])
        cursor = start
        i += 1
        while i < len(hunks) and not hunks[i].startswith('@@ '):
            line = hunks[i]
            if line.startswith((' ', '-')):
                assert cursor < len(lines) and lines[cursor] == line[1:], 'Shared source changed; patch no longer matches'
                if line.startswith(' '): output.append(lines[cursor])
                cursor += 1
            elif line.startswith('+'):
                output.append(line[1:])
            elif line.startswith('\\'):
                pass
            else:
                raise AssertionError('Invalid patch')
            i += 1
    output.extend(lines[cursor:])
    return ''.join(output)


changes=[]
for path,patch in [
    (Path(r'\\192.168.7.10\radiorouter\app\main.py'),'backend/priority-audio-router.patch'),
    (Path(r'\\192.168.7.10\rrr-genredetect\detector-candidate-scout.py'),'backend/priority-audio-scout-root.patch'),
    (Path(r'\\192.168.7.10\rrr-genredetect\current\detector-candidate-scout.py'),'backend/priority-audio-scout-current.patch')]:
    original=path.read_bytes()
    text=original.decode('utf-8').replace('\r\n','\n')
    updated=apply_patch(text,Path(patch).read_text(encoding='utf-8'))
    ast.parse(updated)
    changes.append((path,original,updated.encode('utf-8')))
backup_tag=datetime.now().strftime('%Y%m%d-%H%M%S')
for path,original,updated in changes:
    assert path.read_bytes()==original, 'Shared source changed during preparation'
    backup=path.with_name(path.name+'.before-priority-audio-'+backup_tag)
    backup.write_bytes(original)
    staging=path.with_name(path.name+".priority-audio-staged")
    staging.write_bytes(updated)
    assert staging.read_bytes()==updated
    os.replace(staging,path)
    assert path.read_bytes()==updated
    print(f'Updated {path}; backup {backup.name}')
