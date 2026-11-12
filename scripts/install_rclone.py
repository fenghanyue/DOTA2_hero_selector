#!/usr/bin/env python3
"""Install a checksum-pinned rclone in the user's application directory."""
import hashlib
import io
import os
import pathlib
import platform
import subprocess
import tempfile
import urllib.request
import zipfile

VERSION = '1.76.0'
PACKAGES = {
    ('Linux', 'x86_64'): ('linux-amd64', 'b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7'),
    ('Darwin', 'x86_64'): ('osx-amd64', '4f5835733d52b818340f97b1fc0348a70461534d123363a183ca2b660219595a'),
    ('Darwin', 'arm64'): ('osx-arm64', 'e72dc90b70f4947a7b72b61bde4a4fbba5777b2874fb448951ffdde67ebd0ab7'),
}


def install():
    target = pathlib.Path.home() / '.local/share/dota2-hero-selector/bin/rclone'
    if target.is_file():
        result = subprocess.run([str(target), 'version'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.splitlines()[0] == f'rclone v{VERSION}':
            print(f'rclone v{VERSION} already installed: {target}')
            return
    package, expected = PACKAGES[(platform.system(), platform.machine())]
    name = f'rclone-v{VERSION}-{package}'
    request = urllib.request.Request(f'https://downloads.rclone.org/v{VERSION}/{name}.zip',
                                     headers={'User-Agent': 'DOTA2-hero-selector-setup/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError('rclone archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        executable = zipped.read(f'{name}/rclone')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.rclone-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(executable)
            os.fchmod(handle.fileno(), 0o755)
        os.replace(temporary, target)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    subprocess.run([str(target), 'version'], check=True)
    print(f'Archive SHA-256 verified; installed: {target}')


if __name__ == '__main__':
    install()
