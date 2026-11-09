"""Synthetic tests for source-only, non-executing unit staging."""
import configparser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.morgan_monitor.staging import render_staged_service, StagingError


class StagingTests(unittest.TestCase):
    def test_literal_reviewed_paths_and_no_activation_commands(self):
        unit = render_staged_service('/approved/release', '/private/config.json', '/private/token.env')
        parsed = configparser.ConfigParser(interpolation=None)
        parsed.read_string(unit)
        self.assertEqual(parsed['Service']['WorkingDirectory'], '/approved/release')
        self.assertEqual(parsed['Service']['ExecStart'], '/usr/bin/python3 -m tools.morgan_monitor run --config /private/config.json')
        self.assertEqual(parsed['Service']['EnvironmentFile'], '/private/token.env')
        self.assertEqual(parsed['Service']['Restart'], 'on-failure')
        self.assertEqual(parsed['Service']['UMask'], '0077')
        self.assertEqual(parsed['Service']['NoNewPrivileges'], 'true')
        self.assertIn('STAGED ONLY', unit)
        for forbidden in ('systemctl', 'daemon-reload', 'ExecStartPre', 'ExecStartPost', 'Environment=', '/REPLACE/'):
            self.assertNotIn(forbidden, unit)

    def test_referenced_config_and_credentials_are_never_read_or_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, credential = root / 'config.json', root / 'token.env'
            config.write_text('synthetic-private-config')
            credential.write_text('SYNTHETIC_SECRET=do-not-copy')
            original_read = Path.read_text
            reads = []
            def template_read(path, *args, **kwargs):
                reads.append(path)
                self.assertEqual(path.name, 'morgan-monitor.service.example')
                return original_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', template_read):
                unit = render_staged_service(str(root / 'source'), str(config), str(credential))
            self.assertEqual(len(reads), 1)
            self.assertNotIn('do-not-copy', unit)
            self.assertNotIn('synthetic-private-config', unit)
            self.assertEqual(config.read_text(), 'synthetic-private-config')
            self.assertEqual(credential.read_text(), 'SYNTHETIC_SECRET=do-not-copy')
            self.assertFalse((root / 'source').exists())

    def test_injection_relative_paths_and_systemd_expansion_rejected(self):
        for invalid in ('relative', '/bad\nExecStart=/bin/evil', '/%h/source', '/$HOME/source',
                        '/space path', '/bad;command', '/a/../b', '/a/./b', '', None):
            for field in range(3):
                with self.subTest(invalid=invalid, field=field):
                    paths = ['/approved/source', '/private/config.json', '/private/token.env']
                    paths[field] = invalid
                    with self.assertRaisesRegex(StagingError, '^invalid_staging_path$'):
                        render_staged_service(*paths)

    def test_unexpected_template_shape_fails_closed(self):
        with patch.object(Path, 'read_text', return_value='unexpected template'):
            with self.assertRaisesRegex(StagingError, '^unit_template_drift$'):
                render_staged_service('/approved/source', '/private/config.json', '/private/token.env')
