"""Check local documentation links, JSON and skill entrypoints; no network."""
import json
import re
from pathlib import Path
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
errors = []
docs = [p for p in ROOT.rglob('*.md') if '.git' not in p.parts and 'backups' not in p.parts]
for p in docs:
    text = p.read_text(encoding='utf-8-sig')
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
        if '://' in target or target.startswith('#'):
            continue
        target = unquote(target.split('#')[0].strip('<>'))
        if target and not (p.parent / target).exists():
            errors.append(f'{p.relative_to(ROOT)} -> {target}')
    if p.name == 'SKILL.md':
        if not re.match(r'---\s*\nname: [a-z0-9-]+\ndescription: .+\n---', text):
            errors.append(f'Invalid skill header: {p}')
for p in (ROOT / '04_本地进度网页').glob('*.json'):
    json.loads(p.read_text(encoding='utf-8-sig'))
print(json.dumps(dict(markdown_files=len(docs), skills=len(list((ROOT/'.agents/skills').glob('*/SKILL.md'))), broken_links=errors), ensure_ascii=False, indent=2))
sys.exit(bool(errors))
