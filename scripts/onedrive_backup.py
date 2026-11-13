#!/usr/bin/env python3
"""Upload, verify and restore project batches in a personal OneDrive."""
from __future__ import annotations

import argparse
import base64
import binascii
import configparser
import contextlib
import datetime as dt
import hashlib
import http.client
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SECRET_KEY = 'ONEDRIVE_RCLONE_CONFIG_B64'
REMOTE = 'dota_onedrive'
REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORY = pathlib.Path.home() / '.config/dota2-hero-selector'
CONFIG_PATH = PRIVATE_DIRECTORY / 'rclone.conf'
PINNED_RCLONE = pathlib.Path.home() / '.local/share/dota2-hero-selector/bin/rclone'
TRAINING_FILES = ('matches.jsonl', 'provenance.json', 'report.json', 'collection-report.json',
                  'collection-summary.json', 'audit.json', 'test-report.json',
                  'backup.json', 'restore-verification.json', 'README.md')
READ_METHODS = ('standard', 'graph-contentstream')
MANIFEST_LIMIT = 1024 * 1024
SINGLE_UPLOAD_LIMIT = 200 * 1024 * 1024
# rclone switches to sessions at size >= cutoff; keep 200 MiB files below it.
UPLOAD_FLAGS = ('--immutable', '--checksum', '--onedrive-upload-cutoff', '201Mi',
                '--onedrive-chunk-size', '5Mi')


class BackupError(Exception):
    pass


class RemoteMissing(BackupError):
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


def private_path(path):
    path = pathlib.Path(path).resolve()
    if (path == REPOSITORY or REPOSITORY in path.parents
            or any((parent / '.git').exists() for parent in path.parents)):
        raise BackupError('授权文件必须保存在 Git 仓库之外。')
    return path


def private_write(path, payload):
    path = private_path(path)
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
    path = private_path(path)
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
        codes = sorted(set(re.findall(r'(?:HTTP(?:/\d(?:\.\d)?)?(?: error)?|status(?: code)?)\s*[:=]?\s*([45]\d\d)',
                                     result.stderr.decode(errors='replace'), re.I)))
        status = '，HTTP ' + '/'.join(codes) if codes else ''
        raise BackupError(f'OneDrive 操作失败（rclone 退出码 {result.returncode}{status}）；请核对授权、网络和目标文件。')
    return result.stdout


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def batch_prefix(batch):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', batch):
        raise BackupError('批次名只能使用字母、数字、点、横线和下划线。')
    return f'{REMOTE}:DOTA2_hero_selector/opendota/{batch}'


def artifact_name_valid(name):
    if not isinstance(name, str) or '\\' in name or any(ord(c) < 32 for c in name):
        return False
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or str(path) != name or '..' in path.parts or len(path.parts) != 2:
        return False
    folder, filename = path.parts
    return ((folder == 'raw' and len(filename) > len('.tar.zst') and filename.endswith('.tar.zst'))
            or (folder == 'training' and filename in TRAINING_FILES))


def validate_manifest(manifest, prefix):
    if (not isinstance(manifest, dict) or manifest.get('passed') is not True
            or manifest.get('destination') != prefix):
        raise BackupError('备份清单无效或不属于指定批次。')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not 4 <= len(entries) <= len(TRAINING_FILES) + 1:
        raise BackupError('备份清单文件数量无效。')
    names = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not artifact_name_valid(entry.get('path'))
                or type(entry.get('bytes')) is not int or entry['bytes'] < 0
                or not isinstance(entry.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                or entry['path'] in names):
            raise BackupError('备份清单含无效路径、大小、哈希或重复文件。')
        names.add(entry['path'])
    required = {'training/' + name for name in ('matches.jsonl', 'provenance.json', 'report.json')}
    if not required <= names or sum(name.startswith('raw/') for name in names) != 1:
        raise BackupError('备份清单缺少原始归档或必要训练文件。')


def write_report(path, report):
    if path is not None:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Never forward a Graph bearer token to a download host.
        return None


def graph_credentials(config):
    def read():
        payload = pathlib.Path(config).read_bytes()
        validate_config(payload)
        parsed = configparser.ConfigParser(interpolation=None)
        parsed.read_string(payload.decode())
        section = parsed[REMOTE]
        if section.get('region', 'global') != 'global' or section.get('root_folder_id', '').strip():
            raise BackupError('contentStream 模式目前只支持 Global 个人盘的默认根目录。')
        return section['drive_id'], json.loads(section['token'])

    drive, token = read()
    try:
        expiry = dt.datetime.fromisoformat(token['expiry'].replace('Z', '+00:00'))
        fresh = (isinstance(token.get('access_token'), str) and bool(token['access_token'])
                 and expiry > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2))
    except (AttributeError, KeyError, TypeError, ValueError):
        fresh = False
    if not fresh:
        # Let the installed OAuth client renew and persist credentials privately.
        run_rclone(config, 'about', REMOTE + ':', '--json')
        drive, token = read()
    if not isinstance(token.get('access_token'), str) or not token['access_token']:
        raise BackupError('未能取得可用的 Graph 访问令牌；请检查私有授权配置。')
    return urllib.parse.quote(drive, safe=''), token['access_token']


def graph_open(config, path):
    for attempt in range(3):
        drive, token = graph_credentials(config)
        request = urllib.request.Request(
            'https://graph.microsoft.com' + path.format(drive=drive),
            headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'DOTA2-OneDrive/1.0'})
        try:
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=60)
            if response.status != 200:
                response.close()
                raise BackupError('Graph 返回了非预期状态，未将其作为文件正文。')
            return response
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                retry_after = max(0, int(exc.headers.get('Retry-After', '0')))
            except ValueError:
                retry_after = 0
            exc.close()
            if status == 404:
                raise RemoteMissing('OneDrive 目标文件或批次不存在。') from None
            if status in (408, 429, 500, 502, 503, 504) and attempt < 2:
                if retry_after > 30:
                    raise BackupError(f'Graph 要求等待 {retry_after} 秒；本次未提前重试。') from None
                time.sleep(max(retry_after, 2 * (attempt + 1)))
                continue
            raise BackupError(f'Graph 读取失败（HTTP {status}）；未跟随重定向，也未转发授权。') from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise BackupError('Graph 读取失败：网络连接或读取超时。') from None


def remote_metadata(config, remote_path, read_method):
    if read_method not in READ_METHODS:
        raise BackupError('未知的 OneDrive 读取模式。')
    if read_method == 'graph-contentstream':
        expected = REMOTE + ':DOTA2_hero_selector/opendota/'
        if not remote_path.startswith(expected):
            raise BackupError('只允许读取项目 OneDrive 目录。')
        relative = remote_path.split(':', 1)[1]
        path = '/v1.0/drives/{drive}/root:/' + urllib.parse.quote(relative, safe='/') + '?$select=id,size'
        with graph_open(config, path) as response:
            payload = response.read(65537)
        try:
            metadata = json.loads(payload)
            if (len(payload) > 65536 or not isinstance(metadata.get('id'), str)
                    or type(metadata.get('size')) is not int or metadata['size'] < 0):
                raise ValueError()
        except (ValueError, AttributeError):
            raise BackupError('Graph 文件元数据无效。') from None
        return metadata
    result = subprocess.run(rclone_command(config, 'lsjson', remote_path, '--stat'),
                            env=child_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode in (3, 4):
        raise RemoteMissing('OneDrive 目标文件或批次不存在。')
    if result.returncode:
        raise BackupError(f'OneDrive 元数据读取失败（rclone 退出码 {result.returncode}）。')
    try:
        metadata = json.loads(result.stdout)
        if metadata.get('IsDir') or type(metadata.get('Size')) is not int or metadata['Size'] < 0:
            raise ValueError()
        return {'size': metadata['Size']}
    except (ValueError, AttributeError):
        raise BackupError('OneDrive 文件元数据无效。') from None


@contextlib.contextmanager
def remote_stream(config, remote_path, read_method, limit):
    if read_method == 'graph-contentstream':
        metadata = remote_metadata(config, remote_path, read_method)
        if limit is not None and metadata['size'] > limit:
            raise BackupError('远端文件超过预期大小，已停止读取。')
        path = '/beta/drives/{drive}/items/' + urllib.parse.quote(metadata['id'], safe='') + '/contentStream'
        with graph_open(config, path) as response:
            yield response, metadata['size']
    elif read_method == 'standard':
        process = subprocess.Popen(rclone_command(config, 'cat', remote_path), env=child_environment(),
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            yield process.stdout, None
            if process.wait():
                raise BackupError('OneDrive 回读失败，尚不能确认备份有效。')
        finally:
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.wait()
    else:
        raise BackupError('未知的 OneDrive 读取模式。')


def read_remote(config, remote_path, output=None, read_method='standard', limit=None):
    digest, size = hashlib.sha256(), 0
    try:
        with remote_stream(config, remote_path, read_method, limit) as (stream, remote_size):
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(block)
                if limit is not None and size > limit:
                    raise BackupError('远端文件超过预期大小，已停止读取。')
                digest.update(block)
                if output is not None:
                    output.write(block)
            if remote_size is not None and size != remote_size:
                raise BackupError('远端文件正文不完整。')
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException):
        raise BackupError('OneDrive 文件读取中断或超时，尚未确认完整性。') from None
    return {'bytes': size, 'sha256': digest.hexdigest()}


def remote_sha256(config, remote_path, read_method='standard'):
    return read_remote(config, remote_path, read_method=read_method)['sha256']


def load_manifest(config, batch, read_method, missing_ok=False):
    prefix = batch_prefix(batch)
    remote = prefix + '/manifest.json'
    try:
        metadata = remote_metadata(config, remote, read_method)
    except RemoteMissing:
        if missing_ok:
            return None
        raise
    if metadata['size'] > MANIFEST_LIMIT:
        raise BackupError('备份清单超过大小限制。')
    buffer = io.BytesIO()
    actual = read_remote(config, remote, buffer, read_method, MANIFEST_LIMIT)
    if actual['bytes'] != metadata['size']:
        raise BackupError('备份清单大小发生变化。')
    payload = buffer.getvalue()
    try:
        manifest = json.loads(payload)
    except ValueError:
        raise BackupError('备份清单不是有效 JSON。') from None
    validate_manifest(manifest, prefix)
    return manifest, payload, actual['sha256']


def check_file(config, prefix, entry, read_method, output=None):
    actual = read_remote(config, prefix + '/' + entry['path'], output, read_method, entry['bytes'])
    if actual != {'bytes': entry['bytes'], 'sha256': entry['sha256']}:
        raise BackupError(f"文件大小或 SHA-256 不一致：{entry['path']}")
    print(json.dumps({'verified_file': entry['path'], **actual}, ensure_ascii=False), flush=True)
    return {'path': entry['path'], **actual}


def verification_report(config, batch, entries, manifest_sha, read_method, operation):
    return {'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'passed': True,
            'operation': operation, 'destination': batch_prefix(batch), 'files': entries,
            'manifest_sha256': manifest_sha, 'read_method': read_method,
            'experimental_beta': read_method == 'graph-contentstream',
            'vault_update_required': vault_update_required(config)}


def verify_batch(config, batch, report_path=None, read_method='standard'):
    manifest, payload, manifest_sha = load_manifest(config, batch, read_method)
    entries = [check_file(config, batch_prefix(batch), entry, read_method) for entry in manifest['files']]
    report = verification_report(config, batch, entries, manifest_sha, read_method, 'verify')
    write_report(report_path, report)
    return report


def restore_batch(config, batch, output, read_method='standard'):
    output = pathlib.Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise BackupError('恢复目标已存在；请选择新的目录，不覆盖已有文件。')
    manifest, payload, manifest_sha = load_manifest(config, batch, read_method)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.onedrive-restore-', dir=output.parent) as directory:
        staging = pathlib.Path(directory)
        entries = []
        for entry in manifest['files']:
            target = staging / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as handle:
                entries.append(check_file(config, batch_prefix(batch), entry, read_method, handle))
        report = verification_report(config, batch, entries, manifest_sha, read_method, 'restore')
        report['restored_to'] = str(output)
        (staging / 'manifest.json').write_bytes(payload)
        write_report(staging / 'restore-report.json', report)
        if output.exists() or output.is_symlink():
            raise BackupError('恢复目标已出现，已停止提交恢复目录。')
        staging.rename(output)
    return report


def upload_batch(config, archive, training, batch, report_path, read_method='standard'):
    prefix = batch_prefix(batch)
    archive, training = pathlib.Path(archive).resolve(), pathlib.Path(training).resolve()
    if not archive.is_file() or not archive.name.endswith('.tar.zst'):
        raise BackupError('需要已生成的 .tar.zst 原始归档。')
    if not all((training / name).is_file() for name in ('matches.jsonl', 'provenance.json', 'report.json')):
        raise BackupError('训练目录缺少数据、来源或报告。')
    targets = [(archive, 'raw/' + archive.name)]
    targets.extend((training / name, 'training/' + name) for name in TRAINING_FILES if (training / name).is_file())
    if read_method == 'graph-contentstream' and any(local.stat().st_size > SINGLE_UPLOAD_LIMIT for local, name in targets):
        raise BackupError('当前临时方案单文件上限为 200 MiB；请拆小采集批次后备份。')
    planned = [{'path': name, 'bytes': local.stat().st_size, 'sha256': sha256_file(local)}
               for local, name in targets]
    validate_manifest({'passed': True, 'destination': prefix, 'files': planned}, prefix)
    existing = load_manifest(config, batch, read_method, missing_ok=True)
    if existing is not None:
        by_path = lambda entries: {entry['path']: (entry['bytes'], entry['sha256']) for entry in entries}
        if by_path(existing[0]['files']) != by_path(planned):
            raise BackupError('该批次已完成且内容不同；请使用新的批次名，不覆盖已有备份。')
        report = verify_batch(config, batch, read_method=read_method)
        report['already_complete'] = True
        write_report(report_path, report)
        return report
    verified = []
    for (local, name), entry in zip(targets, planned):
        remote = prefix + '/' + name
        expected = entry['sha256']
        run_rclone(config, 'copyto', str(local), remote, *UPLOAD_FLAGS)
        actual = remote_sha256(config, remote, read_method)
        if actual != expected:
            raise BackupError(f'回读 SHA-256 不一致：{name}')
        verified.append(entry)
        print(json.dumps({'verified_file': name, 'bytes': entry['bytes']}, ensure_ascii=False), flush=True)
    report = {'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'passed': True,
              'destination': prefix, 'files': verified, 'verification': 'downloaded all files and checked SHA-256',
              'read_method': read_method, 'experimental_beta': read_method == 'graph-contentstream',
              'vault_update_required': vault_update_required(config)}
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode()
    # Publish the completion manifest only after every artifact has been read back.
    with tempfile.TemporaryDirectory(prefix='onedrive-manifest-') as directory:
        manifest = pathlib.Path(directory) / 'manifest.json'
        manifest.write_bytes(payload)
        run_rclone(config, 'copyto', str(manifest), prefix + '/manifest.json', *UPLOAD_FLAGS)
        if remote_sha256(config, prefix + '/manifest.json', read_method) != hashlib.sha256(payload).hexdigest():
            raise BackupError('备份清单回读校验失败。')
    write_report(report_path, report)
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
    verify = commands.add_parser('verify', help='read every file and verify the completed batch')
    verify.add_argument('--batch', required=True)
    verify.add_argument('--report', required=True)
    restore = commands.add_parser('restore', help='restore a verified batch to a new directory')
    restore.add_argument('--batch', required=True)
    restore.add_argument('--output', required=True)
    for command in (upload, verify, restore):
        command.add_argument('--read-method', choices=READ_METHODS, default='standard',
                             help='graph-contentstream explicitly opts into Microsoft Graph beta')
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
            if args.command == 'upload':
                report = upload_batch(config, args.archive, args.training, args.batch, args.report, args.read_method)
            elif args.command == 'verify':
                report = verify_batch(config, args.batch, args.report, args.read_method)
            else:
                report = restore_batch(config, args.batch, args.output, args.read_method)
            print(json.dumps({'passed': report['passed'], 'files_verified': len(report['files']),
                              'read_method': args.read_method,
                              'experimental_beta': args.read_method == 'graph-contentstream',
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
