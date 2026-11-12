import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import onedrive_backup as backup


def fixture(refresh='fake-refresh-for-offline-test'):
    return ('[dota_onedrive]\ntype = onedrive\ndrive_type = personal\ndrive_id = fake-drive\ntoken = '
            + json.dumps({'access_token': 'fake-access', 'refresh_token': refresh}) + '\n').encode()


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
            with patch.object(backup, 'run_rclone', side_effect=run), patch.object(
                    backup, 'remote_sha256', side_effect=lambda config, path: hashlib.sha256(remote[path]).hexdigest()):
                report = backup.upload_batch(config, archive, training, 'batch-01', root / 'result.json')
            self.assertTrue(report['passed'])
            self.assertEqual(len(report['files']), 4)
            manifest = 'dota_onedrive:DOTA2_hero_selector/opendota/batch-01/manifest.json'
            self.assertEqual(list(remote)[-1], manifest)
            self.assertTrue(json.loads(remote[manifest])['passed'])
            remote.clear()
            with patch.object(backup, 'run_rclone', side_effect=run), patch.object(backup, 'remote_sha256', return_value='bad'):
                with self.assertRaises(backup.BackupError):
                    backup.upload_batch(config, archive, training, 'batch-01', root / 'failed.json')
            self.assertNotIn(manifest, remote)
            self.assertFalse((root / 'failed.json').exists())

    def test_unsafe_batch_name_is_rejected_before_network_access(self):
        with patch.object(backup, 'run_rclone') as network:
            with self.assertRaises(backup.BackupError):
                backup.upload_batch(pathlib.Path('/unused'), '/unused', '/unused', '../outside', '/unused')
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
