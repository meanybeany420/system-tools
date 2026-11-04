"""Exercise the real installer and real installed commands in an isolated home.

Only dependency-discovery/desktop-command executables are explicit test doubles.
No packages, desktop settings, clipboard contents or real notifications are touched.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstalledToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='system-tools-real-artifacts-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'new home with spaces'
        self.home.mkdir()
        self.checkout = self.base / 'checkout'
        self.checkout.mkdir()
        for name in ('bin', 'config', 'packages'):
            shutil.copytree(ROOT / name, self.checkout / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('install.sh', 'install.py'):
            shutil.copy2(ROOT / name, self.checkout / name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('XDG_') and k not in ('BASH_ENV', 'ENV')}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'),
                        XDG_DATA_HOME=str(self.home / 'data'), XDG_STATE_HOME=str(self.home / 'state'),
                        XDG_RUNTIME_DIR=str(self.home / 'runtime'), PYTHONDONTWRITEBYTECODE='1')
        Path(self.env['XDG_RUNTIME_DIR']).mkdir(mode=0o700)
        result = subprocess.run([str(self.checkout / 'install.sh'), '--skip-packages'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.checkout.rename(self.base / 'checkout-moved-after-install')
        self.fake = self.base / 'FAKE-desktop-commands'
        self.fake.mkdir()
        for name in ('rofi', 'wpctl', 'brightnessctl', 'notify-send', 'wl-copy', 'wl-paste',
                     'xdg-mime', 'flock', 'readlink', 'hyprctl', 'makoctl'):
            path = self.fake / name
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$HOME/FAKE-command-calls"\n')
            path.chmod(0o755)
        self.env['PATH'] = str(self.fake) + ':' + self.env['PATH']

    def tool(self, name, *args, timeout=5):
        return subprocess.run([str(self.home / '.local/bin' / name), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def test_real_installed_help_assets_and_new_bash_work_without_checkout(self):
        for source in (ROOT / 'bin').iterdir():
            if source.is_file():
                installed = self.home / '.local/bin' / source.name
                self.assertEqual(source.read_bytes(), installed.read_bytes())
                result = self.tool(source.name, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout.lower())
        result = self.tool('system-tools-doctor', '--json')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checks = json.loads(result.stdout)
        self.assertTrue(all(c['status'] == 'PASS' for c in checks), checks)
        self.assertFalse((self.home / 'FAKE-command-calls').exists(), 'Help/diagnostics unexpectedly executed a desktop command')
        shell = subprocess.run(['/bin/bash', '--noprofile', '--rcfile', str(self.home / '.bashrc'), '-ic',
                                'command -v quick-audio; quick-brightness --help'], env=self.env,
                               capture_output=True, text=True, timeout=5)
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertIn(str(self.home / '.local/bin/quick-audio'), shell.stdout)
        self.assertFalse((self.home / 'FAKE-command-calls').exists())
        self.assertEqual(len(list((self.home / 'data/applications').glob('*.desktop'))), 5)

    def test_real_installed_focus_worker_finishes_after_checkout_moves(self):
        def stop_worker():
            self.tool('focus-mode', 'stop')
            time.sleep(1.1)
        self.addCleanup(stop_worker)
        result = self.tool('focus-mode', '20')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.home / 'runtime/focus-mode/session.json'
        session = json.loads(state.read_text())
        session['end'] = 0  # Test-fixture deadline, not a change to the system clock.
        replacement = state.with_suffix('.fixture')
        replacement.write_text(json.dumps(session))
        replacement.replace(state)
        deadline = time.monotonic() + 4
        while state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(state.exists(), 'Installed worker failed to complete')
        # Completion removes state before invoking the test notification command.
        log = self.home / 'FAKE-command-calls'
        while time.monotonic() < deadline:
            if log.exists() and 'Session complete.' in log.read_text():
                break
            time.sleep(0.05)
        calls = log.read_text()
        self.assertEqual(calls.count('Session complete.'), 1)
        self.assertIn('mode -r focus-timer', calls)


if __name__ == '__main__':
    unittest.main()
