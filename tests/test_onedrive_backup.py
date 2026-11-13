import base64
import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import pathlib
import stat
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import onedrive_backup as backup

FUTURE_EXPIRY = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()


def fixture(refresh='fake-refresh-for-offline-test'):
    return ('[dota_onedrive]\ntype = onedrive\ndrive_type = personal\ndrive_id = fake-drive\ntoken = '
            + json.dumps({'access_token': 'fake-access', 'refresh_token': refresh,
                          'expiry': FUTURE_EXPIRY}) + '\n').encode()


@contextlib.contextmanager
def memory_remote():
    remote, writes, reads = {}, [], []

    def metadata(config, path, method):
        if path not in remote:
            raise backup.RemoteMissing('offline fixture: missing')
        return {'size': len(remote[path])}

    def read(config, path, output=None, read_method='standard', limit=None):
        payload = remote[path]
        reads.append(path)
        if limit is not None and len(payload) > limit:
            raise backup.BackupError('offline fixture: oversized')
        if output is not None:
            output.write(payload)
        return {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}

    def copyto(config, operation, source, target, *flags):
        if operation != 'copyto' or '--immutable' not in flags:
            raise AssertionError('All writes must be immutable copyto operations.')
        if '--onedrive-upload-cutoff' not in flags or flags[flags.index('--onedrive-upload-cutoff') + 1] != '201Mi':
            raise AssertionError('Small files must avoid preauthenticated upload sessions.')
        payload = pathlib.Path(source).read_bytes()
        if target in remote and remote[target] != payload:
            raise backup.BackupError('offline fixture: immutable conflict')
        remote[target] = payload
        writes.append(target)
        return b''

    with patch.object(backup, 'remote_metadata', side_effect=metadata), patch.object(
            backup, 'read_remote', side_effect=read), patch.object(backup, 'run_rclone', side_effect=copyto):
        yield remote, writes, reads


def local_batch(root):
    config = root / 'private/rclone.conf'
    backup.restore_config(config, {backup.SECRET_KEY: base64.b64encode(fixture()).decode()})
    archive = root / 'raw.tar.zst'
    archive.write_bytes(b'archive-fixture')
    training = root / 'training'
    training.mkdir()
    for name in ('matches.jsonl', 'provenance.json', 'report.json'):
        (training / name).write_bytes(('fixture-' + name).encode())
    return config, archive, training


class OneDriveTests(unittest.TestCase):
    def test_vault_seed_restores_private_config_without_overwriting_renewed_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'private/rclone.conf'
            env = {backup.SECRET_KEY: base64.b64encode(fixture()).decode()}
            backup.restore_config(path, env)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertFalse(backup.vault_update_required(path))
            renewed = fixture('renewed-fake-token')
            path.write_bytes(renewed)
            backup.restore_config(path, env)
            self.assertEqual(path.read_bytes(), renewed)
            self.assertTrue(backup.vault_update_required(path))
            env[backup.SECRET_KEY] = base64.b64encode(fixture('new-login')).decode()
            backup.restore_config(path, env)
            self.assertEqual(path.read_bytes(), fixture('new-login'))

    def test_rejects_missing_invalid_business_and_unrelated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'private/rclone.conf'
            with self.assertRaises(backup.BackupError):
                backup.restore_config(path, {})
            for payload in (b'not ini', fixture().replace(b'personal', b'business'),
                            fixture() + b'\n[unrelated]\ntype=drive\n'):
                with self.subTest(payload_type=len(payload)), self.assertRaises(backup.BackupError):
                    backup.restore_config(path, {backup.SECRET_KEY: base64.b64encode(payload).decode()})
            self.assertFalse(path.exists())

    def test_invalid_config_error_does_not_echo_secret_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = b'[dota_onedrive]\nPRIVATE-SECRET-SENTINEL\n'
            buffer = io.StringIO()
            with patch.dict(backup.os.environ, {backup.SECRET_KEY: base64.b64encode(bad).decode()}), contextlib.redirect_stdout(buffer):
                status = backup.main(['--config', str(pathlib.Path(directory) / 'private/rclone.conf'), 'bootstrap'])
            self.assertEqual(status, 1)
            self.assertNotIn('PRIVATE-SECRET-SENTINEL', buffer.getvalue())

    def test_credentials_cannot_be_written_inside_repo(self):
        with self.assertRaises(backup.BackupError):
            backup.restore_config(backup.REPOSITORY / 'rclone.conf', {})
        with self.assertRaises(backup.BackupError):
            backup.private_write(backup.REPOSITORY / 'personal-vault-value.txt', b'fake')

    def test_private_export_does_not_change_existing_parent_directory_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            parent.chmod(0o755)
            output = parent / 'value.txt'
            backup.private_write(output, b'fake-credential')
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)

    def test_installed_helper_also_refuses_credentials_in_a_separate_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = pathlib.Path(directory) / 'checkout'
            (checkout / '.git').mkdir(parents=True)
            with patch.object(backup, 'REPOSITORY', pathlib.Path(directory) / 'installed-tools'):
                with self.assertRaises(backup.BackupError):
                    backup.restore_config(checkout / 'private/rclone.conf', {})
                with self.assertRaises(backup.BackupError):
                    backup.private_write(checkout / 'private/value.txt', b'fake')

    def test_manifest_only_published_after_all_remote_files_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = root / 'private/rclone.conf'
            backup.restore_config(config, {backup.SECRET_KEY: base64.b64encode(fixture()).decode()})
            archive = root / 'raw.tar.zst'; archive.write_bytes(b'fake-archive')
            training = root / 'training'; training.mkdir()
            for name in ('matches.jsonl', 'provenance.json', 'report.json'):
                (training / name).write_bytes(b'fake-' + name.encode())
            remote = {}
            def run(config, operation, local, destination, *flags):
                self.assertEqual(operation, 'copyto')
                self.assertTrue(destination.startswith('dota_onedrive:DOTA2_hero_selector/opendota/batch-01/'))
                remote[destination] = pathlib.Path(local).read_bytes()
                return b''
            with patch.object(backup, 'load_manifest', return_value=None), patch.object(
                    backup, 'run_rclone', side_effect=run), patch.object(
                    backup, 'remote_sha256', side_effect=lambda config, path, method: hashlib.sha256(remote[path]).hexdigest()):
                report = backup.upload_batch(config, archive, training, 'batch-01', root / 'result.json')
            self.assertTrue(report['passed'])
            self.assertEqual(len(report['files']), 4)
            manifest = 'dota_onedrive:DOTA2_hero_selector/opendota/batch-01/manifest.json'
            self.assertEqual(list(remote)[-1], manifest)
            self.assertTrue(json.loads(remote[manifest])['passed'])
            remote.clear()
            with patch.object(backup, 'load_manifest', return_value=None), patch.object(
                    backup, 'run_rclone', side_effect=run), patch.object(backup, 'remote_sha256', return_value='bad'):
                with self.assertRaises(backup.BackupError):
                    backup.upload_batch(config, archive, training, 'batch-01', root / 'failed.json')
            self.assertNotIn(manifest, remote)
            self.assertFalse((root / 'failed.json').exists())

    def test_graph_read_uses_current_token_and_never_follows_download_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / 'private/rclone.conf'
            backup.restore_config(config, {backup.SECRET_KEY: base64.b64encode(fixture()).decode()})
            payload = b'complete-file-body'
            metadata = io.BytesIO(json.dumps({'id': 'fake-item', 'size': len(payload)}).encode())
            metadata.status = 200
            body = io.BytesIO(payload)
            body.status = 200
            opener = Mock()
            opener.open.side_effect = [metadata, body]
            target = 'dota_onedrive:DOTA2_hero_selector/opendota/batch-01/training/matches.jsonl'
            with patch.object(backup.urllib.request, 'build_opener', return_value=opener) as builder:
                result = backup.read_remote(config, target, read_method='graph-contentstream')
            self.assertEqual(result, {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            requests = [call.args[0] for call in opener.open.call_args_list]
            self.assertTrue(all(request.full_url.startswith('https://graph.microsoft.com/') for request in requests))
            self.assertTrue(requests[1].full_url.endswith('/beta/drives/fake-drive/items/fake-item/contentStream'))
            self.assertTrue(all(request.get_header('Authorization') == 'Bearer fake-access' for request in requests))
            self.assertTrue(all(isinstance(call.args[0], backup.NoRedirect) for call in builder.call_args_list))

            redirect = urllib.error.HTTPError('https://graph.microsoft.com/unused', 302, 'redirect',
                                             {'Location': 'https://download.invalid/?signature=PRIVATE-SENTINEL'}, io.BytesIO())
            opener.open.side_effect = redirect
            with patch.object(backup.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(backup.BackupError) as error:
                    backup.graph_open(config, '/beta/drives/{drive}/items/fake-item/contentStream')
            self.assertNotIn('PRIVATE-SENTINEL', str(error.exception))
            self.assertIn('302', str(error.exception))
            self.assertEqual(opener.open.call_count, 3)

    def test_graph_renews_expired_credentials_once_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / 'private/rclone.conf'
            backup.restore_config(config, {backup.SECRET_KEY: base64.b64encode(fixture()).decode()})
            expired = config.read_bytes().replace(b'fake-access', b'expired-access')
            parsed = backup.configparser.ConfigParser(interpolation=None)
            parsed.read_string(expired.decode())
            token = json.loads(parsed[backup.REMOTE]['token'])
            token['expiry'] = '2000-01-01T00:00:00+00:00'
            parsed[backup.REMOTE]['token'] = json.dumps(token)
            with config.open('w') as handle:
                parsed.write(handle)
            def renew(*args):
                backup.private_write(config, fixture('renewed-fake-token'))
                return b'{}'
            with patch.object(backup, 'run_rclone', side_effect=renew) as refresh:
                drive, access = backup.graph_credentials(config)
                self.assertEqual(access, 'fake-access')
                backup.graph_credentials(config)
            refresh.assert_called_once_with(config, 'about', 'dota_onedrive:', '--json')
            self.assertIn(b'renewed-fake-token', config.read_bytes())

    def test_graph_read_rejects_truncated_or_oversized_body(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / 'private/rclone.conf'
            backup.restore_config(config, {backup.SECRET_KEY: base64.b64encode(fixture()).decode()})
            target = 'dota_onedrive:DOTA2_hero_selector/opendota/batch-01/training/matches.jsonl'
            for declared, payload, limit in ((5, b'short', 4), (10, b'short', 10), (4, b'longer', 4)):
                with self.subTest(declared=declared, limit=limit):
                    metadata = io.BytesIO(json.dumps({'id': 'fake-item', 'size': declared}).encode())
                    metadata.status = 200
                    body = io.BytesIO(payload)
                    body.status = 200
                    opener = Mock()
                    opener.open.side_effect = [metadata, body]
                    with patch.object(backup.urllib.request, 'build_opener', return_value=opener):
                        with self.assertRaises(backup.BackupError):
                            backup.read_remote(config, target, read_method='graph-contentstream', limit=limit)

    def test_completed_batch_verifies_restores_and_reuploads_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory, memory_remote() as (remote, writes, reads):
            root = pathlib.Path(directory)
            config, archive, training = local_batch(root)
            report = backup.upload_batch(config, archive, training, 'batch-01', root / 'upload.json', 'graph-contentstream')
            self.assertTrue(report['experimental_beta'])
            manifest_path = backup.batch_prefix('batch-01') + '/manifest.json'
            self.assertEqual(writes[-1], manifest_path)
            original_manifest = remote[manifest_path]
            verified = backup.verify_batch(config, 'batch-01', root / 'verified.json', 'graph-contentstream')
            self.assertTrue(verified['passed'])
            self.assertEqual(len(verified['files']), 4)
            restored = backup.restore_batch(config, 'batch-01', root / 'restored', 'graph-contentstream')
            self.assertTrue(restored['passed'])
            self.assertEqual((root / 'restored/raw/raw.tar.zst').read_bytes(), archive.read_bytes())
            self.assertEqual((root / 'restored/training/matches.jsonl').read_bytes(), (training / 'matches.jsonl').read_bytes())
            self.assertEqual((root / 'restored/manifest.json').read_bytes(), original_manifest)
            self.assertTrue(json.loads((root / 'restored/restore-report.json').read_text())['passed'])
            self.assertFalse(list(root.glob('.onedrive-restore-*')))
            original_writes = list(writes)
            repeated = backup.upload_batch(config, archive, training, 'batch-01', root / 'repeat.json', 'graph-contentstream')
            self.assertTrue(repeated['already_complete'])
            self.assertEqual(writes, original_writes)
            self.assertEqual(remote[manifest_path], original_manifest)
            (training / 'matches.jsonl').write_bytes(b'different')
            with self.assertRaises(backup.BackupError):
                backup.upload_batch(config, archive, training, 'batch-01', root / 'conflict.json', 'graph-contentstream')
            self.assertEqual(writes, original_writes)
            self.assertFalse((root / 'conflict.json').exists())

    def test_corrupt_download_does_not_publish_a_restore_directory_or_success_report(self):
        with tempfile.TemporaryDirectory() as directory, memory_remote() as (remote, writes, reads):
            root = pathlib.Path(directory)
            config, archive, training = local_batch(root)
            backup.upload_batch(config, archive, training, 'batch-01', root / 'upload.json')
            remote[backup.batch_prefix('batch-01') + '/training/matches.jsonl'] = b'corrupt'
            with self.assertRaises(backup.BackupError):
                backup.verify_batch(config, 'batch-01', root / 'failed.json')
            with self.assertRaises(backup.BackupError):
                backup.restore_batch(config, 'batch-01', root / 'restored')
            self.assertFalse((root / 'restored').exists())
            self.assertFalse((root / 'failed.json').exists())
            self.assertFalse(list(root.glob('.onedrive-restore-*')))

    def test_malformed_manifest_is_rejected_before_artifact_download_or_local_write(self):
        with tempfile.TemporaryDirectory() as directory, memory_remote() as (remote, writes, reads):
            root = pathlib.Path(directory)
            config, archive, training = local_batch(root)
            backup.upload_batch(config, archive, training, 'batch-01', root / 'upload.json')
            manifest_path = backup.batch_prefix('batch-01') + '/manifest.json'
            original = json.loads(remote[manifest_path])
            cases = []
            for bad_name in ('../outside', 'training/../../outside', '/absolute', 'raw/../outside.tar.zst', 'training\\matches.jsonl'):
                candidate = copy.deepcopy(original)
                candidate['files'][0]['path'] = bad_name
                cases.append(candidate)
            for field, value in (('bytes', True), ('bytes', -1), ('sha256', 'not-a-hash')):
                candidate = copy.deepcopy(original)
                candidate['files'][0][field] = value
                cases.append(candidate)
            candidate = copy.deepcopy(original)
            candidate['files'][0] = copy.deepcopy(candidate['files'][1])
            cases.append(candidate)
            candidate = copy.deepcopy(original)
            candidate['destination'] = backup.batch_prefix('another-batch')
            cases.append(candidate)
            for index, candidate in enumerate(cases):
                with self.subTest(case=index):
                    remote[manifest_path] = json.dumps(candidate).encode()
                    start = len(reads)
                    target = root / ('restore-' + str(index))
                    with self.assertRaises(backup.BackupError):
                        backup.restore_batch(config, 'batch-01', target)
                    self.assertEqual(reads[start:], [manifest_path])
                    self.assertFalse(target.exists())
            self.assertFalse((root / 'outside').exists())

    def test_restore_refuses_existing_directories_and_broken_symlinks_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'existing'
            target.mkdir()
            (target / 'keep.txt').write_bytes(b'keep')
            link = root / 'broken'
            link.symlink_to(root / 'missing')
            with patch.object(backup, 'load_manifest') as network:
                for output in (target, link):
                    with self.assertRaises(backup.BackupError):
                        backup.restore_batch(root / 'unused.conf', 'batch-01', output)
            network.assert_not_called()
            self.assertEqual((target / 'keep.txt').read_bytes(), b'keep')
            self.assertTrue(link.is_symlink())

    def test_graph_auth_errors_are_not_treated_as_a_missing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            config, archive, training = local_batch(pathlib.Path(directory))
            for status in (401, 403, 429):
                with self.subTest(status=status):
                    error = urllib.error.HTTPError('https://graph.microsoft.com/unused', status,
                                                  'PRIVATE-SENTINEL', {}, io.BytesIO(b'PRIVATE-SENTINEL'))
                    opener = Mock()
                    opener.open.side_effect = error
                    with patch.object(backup.urllib.request, 'build_opener', return_value=opener):
                        with self.assertRaises(backup.BackupError) as raised:
                            backup.load_manifest(config, 'batch-01', 'graph-contentstream', missing_ok=True)
                    self.assertNotIsInstance(raised.exception, backup.RemoteMissing)
                    self.assertNotIn('PRIVATE-SENTINEL', str(raised.exception))

    def test_standard_mode_does_not_silently_fall_back_to_beta(self):
        process = Mock()
        process.stdout = io.BytesIO(b'')
        process.wait.return_value = 5
        process.poll.return_value = 5
        with patch.object(backup, 'rclone_command', return_value=['offline-rclone']), patch.object(
                backup.subprocess, 'Popen', return_value=process), patch.object(backup, 'graph_open') as graph:
            with self.assertRaises(backup.BackupError):
                backup.read_remote(pathlib.Path('/unused'), 'dota_onedrive:unused', read_method='standard')
        graph.assert_not_called()

    def test_temporary_profile_rejects_large_files_before_any_network_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config, archive, training = local_batch(root)
            with patch.object(backup, 'SINGLE_UPLOAD_LIMIT', 10), patch.object(backup, 'load_manifest') as network:
                with self.assertRaises(backup.BackupError):
                    backup.upload_batch(config, archive, training, 'batch-01', root / 'result.json', 'graph-contentstream')
            network.assert_not_called()
            self.assertFalse((root / 'result.json').exists())

    def test_upload_error_reports_http_code_without_disclosing_raw_stderr(self):
        result = Mock(returncode=1, stderr=b'HTTP error 401 https://private.invalid/?signature=PRIVATE-SENTINEL')
        with patch.object(backup, 'rclone_command', return_value=['offline-rclone']), patch.object(
                backup.subprocess, 'run', return_value=result):
            with self.assertRaises(backup.BackupError) as raised:
                backup.run_rclone(pathlib.Path('/unused'), 'copyto', '/unused', 'dota_onedrive:unused')
        self.assertIn('HTTP 401', str(raised.exception))
        self.assertNotIn('PRIVATE-SENTINEL', str(raised.exception))
        self.assertNotIn('private.invalid', str(raised.exception))

    def test_graph_retries_transient_errors_but_honors_long_retry_after(self):
        with tempfile.TemporaryDirectory() as directory:
            config, archive, training = local_batch(pathlib.Path(directory))
            error = urllib.error.HTTPError('https://graph.microsoft.com/unused', 504, 'timeout', {}, io.BytesIO())
            response = io.BytesIO(b'complete')
            response.status = 200
            opener = Mock()
            opener.open.side_effect = [error, response]
            with patch.object(backup.urllib.request, 'build_opener', return_value=opener), patch.object(backup.time, 'sleep') as wait:
                with backup.graph_open(config, '/beta/drives/{drive}/items/item/contentStream') as stream:
                    self.assertEqual(stream.read(), b'complete')
            self.assertEqual(opener.open.call_count, 2)
            wait.assert_called_once_with(2)
            opener.open.reset_mock()
            opener.open.side_effect = urllib.error.HTTPError('https://graph.microsoft.com/unused', 429,
                                                           'limited', {'Retry-After': '120'}, io.BytesIO())
            with patch.object(backup.urllib.request, 'build_opener', return_value=opener), patch.object(backup.time, 'sleep') as wait:
                with self.assertRaises(backup.BackupError):
                    backup.graph_open(config, '/beta/drives/{drive}/items/item/contentStream')
            self.assertEqual(opener.open.call_count, 1)
            wait.assert_not_called()

    def test_interrupted_http_body_is_reported_without_raw_exception_text(self):
        stream = Mock()
        stream.read.side_effect = backup.http.client.IncompleteRead(b'PRIVATE-SENTINEL', 5)
        @contextlib.contextmanager
        def broken_stream(*args):
            yield stream, 20
        with patch.object(backup, 'remote_stream', broken_stream):
            with self.assertRaises(backup.BackupError) as raised:
                backup.read_remote(pathlib.Path('/unused'), 'dota_onedrive:unused', read_method='graph-contentstream')
        self.assertNotIn('PRIVATE-SENTINEL', str(raised.exception))

    def test_unsafe_batch_name_is_rejected_before_network_access(self):
        with patch.object(backup, 'run_rclone') as network:
            with self.assertRaises(backup.BackupError):
                backup.upload_batch(pathlib.Path('/unused'), '/unused', '/unused', '../outside', '/unused')
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
