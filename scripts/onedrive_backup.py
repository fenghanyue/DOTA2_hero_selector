#!/usr/bin/env python3
"""Restore personal OneDrive authorization and upload verified batch artifacts."""
from __future__ import annotations

import argparse
import base64
import binascii
import configparser
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

SECRET_KEY = 'ONEDRIVE_RCLONE_CONFIG_B64'
REMOTE = 'dota_onedrive'
REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORY = pathlib.Path.home() / '.config/dota2-hero-selector'
CONFIG_PATH = PRIVATE_DIRECTORY / 'rclone.conf'
PINNED_RCLONE = pathlib.Path.home() / '.local/share/dota2-hero-selector/bin/rclone'
TRAINING_FILES = ('matches.jsonl', 'provenance.json', 'report.json', 'collection-report.json',
                  'collection-summary.json', 'audit.json', 'test-report.json',
                  'backup.json', 'restore-verification.json', 'README.md')


class BackupError(Exception):
    pass


def validate_config(payload):
    try:
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(payload.decode('utf-8'))
        if config.sections() != [REMOTE] or config.defaults():
            raise ValueError()
        section = config[REMOTE]
        token = json.loads(section['token'])
        if (section['type'] != 'onedrive' or section['drive_type'] != 'personal'
                or not section['drive_id'] or not isinstance(token, dict)
                or not isinstance(token.get('refresh_token'), str) or not token['refresh_token']):
            raise ValueError()
    except (ValueError, KeyError, UnicodeError, configparser.Error):
        # Parser exceptions can include a credential-bearing input line.
        raise BackupError('授权配置无效；请只导出 dota_onedrive 这个个人 OneDrive 连接。') from None


def private_write(path, payload):
    path = pathlib.Path(path).resolve()
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise BackupError('授权文件必须保存在 Git 仓库之外。')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.onedrive-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            os.fchmod(handle.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


def restore_config(path=CONFIG_PATH, environ=None):
    environ = os.environ if environ is None else environ
    path = pathlib.Path(path).resolve()
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise BackupError('授权文件必须保存在 Git 仓库之外。')
    encoded = environ.get(SECRET_KEY)
    marker = path.with_suffix('.seed-sha256')
    if encoded:
        try:
            payload = base64.b64decode(encoded.strip(), validate=True)
        except (ValueError, binascii.Error):
            raise BackupError(f'{SECRET_KEY} 不是有效的 Base64 授权配置。') from None
        validate_config(payload)
        seed = hashlib.sha256(payload).hexdigest()
        previous = marker.read_text().strip() if marker.exists() else None
        if not path.exists() or previous != seed:
            private_write(path, payload)
            private_write(marker, (seed + '\n').encode())
        # An unchanged vault seed must not overwrite a newer runtime refresh token.
    elif not path.exists():
        raise BackupError(f'缺少个人保管库变量 {SECRET_KEY}；尚未连接 OneDrive。')
    validate_config(path.read_bytes())
    os.chmod(path, 0o600)
    return path


def vault_update_required(path):
    marker = path.with_suffix('.seed-sha256')
    return not marker.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != marker.read_text().strip()


def rclone_command(config, *arguments):
    executable = str(PINNED_RCLONE) if PINNED_RCLONE.is_file() else shutil.which('rclone')
    if not executable:
        raise BackupError('请先运行 python scripts/install_rclone.py。')
    return [executable, '--config', str(config), '--log-level', 'ERROR', '--retries', '3',
            '--low-level-retries', '3', '--contimeout', '20s', '--timeout', '60s', *arguments]


def child_environment():
    # The rclone child needs the private file, not a second copy of the vault value.
    return {k: v for k, v in os.environ.items() if k != SECRET_KEY and not k.startswith('RCLONE_CONFIG_')}


def run_rclone(config, *arguments):
    result = subprocess.run(rclone_command(config, *arguments), env=child_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise BackupError(f'OneDrive 操作失败（rclone 退出码 {result.returncode}）；请核对授权、网络和目标文件。')
    return result.stdout


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def remote_sha256(config, remote_path):
    digest = hashlib.sha256()
    with subprocess.Popen(rclone_command(config, 'cat', remote_path), env=child_environment(),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        for block in iter(lambda: process.stdout.read(1024 * 1024), b''):
            digest.update(block)
        if process.wait():
            raise BackupError('OneDrive 回读失败，尚不能确认备份有效。')
    return digest.hexdigest()


def upload_batch(config, archive, training, batch, report_path):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', batch):
        raise BackupError('批次名只能使用字母、数字、点、横线和下划线。')
    archive, training = pathlib.Path(archive).resolve(), pathlib.Path(training).resolve()
    if not archive.is_file() or not archive.name.endswith('.tar.zst'):
        raise BackupError('需要已生成的 .tar.zst 原始归档。')
    if not all((training / name).is_file() for name in ('matches.jsonl', 'provenance.json', 'report.json')):
        raise BackupError('训练目录缺少数据、来源或报告。')
    targets = [(archive, 'raw/' + archive.name)]
    targets.extend((training / name, 'training/' + name) for name in TRAINING_FILES if (training / name).is_file())
    prefix = f'{REMOTE}:DOTA2_hero_selector/opendota/{batch}'
    verified = []
    for local, name in targets:
        remote = prefix + '/' + name
        expected = sha256_file(local)
        run_rclone(config, 'copyto', str(local), remote, '--immutable', '--checksum',
                   '--onedrive-chunk-size', '5Mi')
        actual = remote_sha256(config, remote)
        if actual != expected:
            raise BackupError(f'回读 SHA-256 不一致：{name}')
        verified.append({'path': name, 'bytes': local.stat().st_size, 'sha256': actual})
        print(json.dumps({'verified_file': name, 'bytes': local.stat().st_size}, ensure_ascii=False), flush=True)
    report = {'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'passed': True,
              'destination': prefix, 'files': verified, 'verification': 'downloaded all files and checked SHA-256',
              'vault_update_required': vault_update_required(config)}
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode()
    # Publish the completion manifest only after every artifact has been read back.
    with tempfile.TemporaryDirectory(prefix='onedrive-manifest-') as directory:
        manifest = pathlib.Path(directory) / 'manifest.json'
        manifest.write_bytes(payload)
        run_rclone(config, 'copyto', str(manifest), prefix + '/manifest.json')
        if remote_sha256(config, prefix + '/manifest.json') != hashlib.sha256(payload).hexdigest():
            raise BackupError('备份清单回读校验失败。')
    report_path = pathlib.Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(payload)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=pathlib.Path, default=CONFIG_PATH)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('bootstrap', help='restore authorization without a network request')
    commands.add_parser('status', help='check the OneDrive quota; do not list personal files')
    upload = commands.add_parser('upload')
    upload.add_argument('--archive', required=True)
    upload.add_argument('--training', required=True)
    upload.add_argument('--batch', required=True)
    upload.add_argument('--report', required=True)
    export = commands.add_parser('export-vault', help='write renewed config to a private file; never stdout')
    export.add_argument('--output', type=pathlib.Path, default=PRIVATE_DIRECTORY / 'personal-vault-value.txt')
    args = parser.parse_args(argv)
    try:
        config = restore_config(args.config)
        if args.command == 'bootstrap':
            print(json.dumps({'authorization_configured': True, 'vault_update_required': vault_update_required(config)}))
        elif args.command == 'status':
            quota = json.loads(run_rclone(config, 'about', REMOTE + ':', '--json'))
            print(json.dumps({'connected': True, 'quota_bytes': {k: v for k, v in quota.items() if type(v) is int},
                              'vault_update_required': vault_update_required(config)}, ensure_ascii=False))
        elif args.command == 'export-vault':
            private_write(args.output, base64.b64encode(config.read_bytes()))
            print(f'更新后的授权已写入受限权限文件：{args.output}；请通过个人保管库保存，不要发到聊天中。')
        else:
            report = upload_batch(config, args.archive, args.training, args.batch, args.report)
            print(json.dumps({'passed': report['passed'], 'files_verified': len(report['files']),
                              'vault_update_required': report['vault_update_required']}, ensure_ascii=False))
        return 0
    except BackupError as exc:
        print(str(exc))
        return 1
    except (OSError, ValueError):
        # No raw subprocess stderr, token JSON, or parser excerpts in chat/logs.
        print('OneDrive 接入或备份未完成。请检查个人保管库配置、rclone、网络和文件；授权失效时重新登录。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
