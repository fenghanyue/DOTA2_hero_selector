#!/usr/bin/env bash
# Append this block to the existing environment install script; keep its current duties.
set -euo pipefail
python3 - <<'PY'
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.request

revision = 'b85058f4d76822fa614a3e9d0f6f2c053a54a4a2'
root = pathlib.Path('/workspace/.cloud-environment/onedrive-tools')
root.mkdir(parents=True, exist_ok=True)
sources = {
    'install_rclone.py': '5f0588eaccaf5055bc58e4095948f3a25d7649ae3d1eb6b2f464d6552647694f',
    'onedrive_backup.py': '86364ba2bfe0632b1f82fb595316e491e49b2f1bf1fa8e81927264bff8712b79',
}
for name, expected in sources.items():
    target = root / name
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        continue
    url = f'https://raw.githubusercontent.com/fenghanyue/DOTA2_hero_selector/{revision}/scripts/{name}'
    request = urllib.request.Request(url, headers={'User-Agent': 'DOTA2-hero-selector-setup/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RuntimeError(f'OneDrive helper checksum mismatch: {name}')
    fd, temporary = tempfile.mkstemp(prefix='.install-', dir=root)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(payload)
    os.replace(temporary, target)
(root / 'sources.json').write_text(json.dumps({'revision': revision, 'sha256': sources}, indent=2) + '\n')
subprocess.run(['python3', str(root / 'install_rclone.py')], check=True)
print('OneDrive tools installed; authorization remains in the personal vault.')
PY
