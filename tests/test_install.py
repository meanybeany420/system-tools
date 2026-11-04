"""Installer integration tests. FAKE sudo/pacman are logging test doubles only.
All installs use a temporary HOME; no privileged/package operations are run.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get('TMPDIR', Path.home() / '.cache'))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='system-tools-test-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home with spaces'
        self.home.mkdir()
        self.repo = self.base / 'checkout'
        self.repo.mkdir()
        for name in ('install.sh', 'install.py', 'packages'):
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, self.repo / name)
            elif source.exists():
                shutil.copy2(source, self.repo / name)
        for directory in ('bin', 'config/rofi', 'config/hypr'):
            (self.repo / directory).mkdir(parents=True, exist_ok=True)
        for name in ('quick-audio', 'quick-brightness', 'display-menu', 'clipboard-stack', 'focus-mode', 'clipboard-watch', 'system-tools-doctor'):
            f = self.repo / 'bin' / name
            f.write_text('#!/bin/bash\nprintf "test-tool\\n"\n')
            f.chmod(0o755)
        for path in ('rofi/quick-audio.rasi', 'rofi/system-tools.rasi', 'hypr/system-tools-monitors.lua'):
            (self.repo / 'config' / path).write_text('# test fixture\n')
        self.fake = self.base / 'FAKE-test-commands'
        self.fake.mkdir()
        self.calls = self.base / 'FAKE-calls'
        for command, body in {
            'sudo': 'printf "sudo\\n" >> "$FAKE_CALLS"\nexec "$@"\n',
            'pacman': 'printf "pacman" >> "$FAKE_CALLS"\nprintf " <%s>" "$@" >> "$FAKE_CALLS"\nprintf "\\n" >> "$FAKE_CALLS"\nexit "${FAKE_PACMAN_EXIT:-0}"\n',
        }.items():
            p = self.fake / command
            p.write_text('#!/bin/bash\n' + body)
            p.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('XDG_') and k not in ('BASH_ENV', 'ENV')}
        self.env.update(HOME=str(self.home), PATH=str(self.fake) + ':' + os.environ['PATH'], FAKE_CALLS=str(self.calls), PYTHONDONTWRITEBYTECODE='1')

    def run_install(self, *args, ok=True):
        result = subprocess.run(['/bin/bash', str(self.repo / 'install.sh'), *args], env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_backups_symlinks_and_idempotency(self):
        rc = self.home / '.bashrc'
        original = b'# my bashrc\nexport KEEP_ME=yes\n'
        rc.write_bytes(original)
        foreign = self.base / 'foreign-file'
        foreign.write_text('do not overwrite me')
        binary = self.home / '.local/bin/quick-audio'
        binary.parent.mkdir(parents=True)
        binary.symlink_to(foreign)
        self.run_install('--skip-packages')
        self.assertEqual(foreign.read_text(), 'do not overwrite me')
        self.assertFalse(binary.is_symlink())
        self.assertTrue(rc.read_bytes().startswith(original))
        state = self.home / '.local/state/system-tools'
        backups = list((state / 'backups').rglob('*'))
        regular = [p for p in backups if p.is_file() and not p.is_symlink()]
        self.assertTrue(any(p.read_bytes() == original for p in regular))
        self.assertTrue(any(p.read_bytes() == foreign.read_bytes() for p in regular))
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / 'install.log').stat().st_mode & 0o777, 0o600)
        before = {str(p): p.lstat().st_mtime_ns for p in self.home.rglob('*')}
        self.run_install('--skip-packages')
        after = {str(p): p.lstat().st_mtime_ns for p in self.home.rglob('*')}
        self.assertEqual(before, after)
        self.assertEqual(rc.read_text().count('# >>> system-tools managed PATH >>>'), 1)

    def test_offline_deployment_and_new_bash_path(self):
        self.run_install('--skip-packages')
        self.assertFalse(self.calls.exists())
        for source in (self.repo / 'bin').iterdir():
            target = self.home / '.local/bin' / source.name
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertFalse(target.is_symlink())
            self.assertTrue(os.access(target, os.X_OK))
        self.assertTrue((self.home / '.config/rofi/quick-audio.rasi').exists())
        self.assertTrue((self.home / '.config/hypr/system-tools-monitors.lua').exists())
        desktops = list((self.home / '.local/share/applications').glob('system-tools-*.desktop'))
        self.assertEqual(len(desktops), 5)
        self.assertFalse(any('clipboard-watch' in p.read_text() for p in desktops))
        self.assertNotIn(str(self.repo), (self.home / '.bashrc').read_text())
        # Installed commands and PATH setup must survive removal of the checkout.
        self.repo.rename(self.base / 'checkout-no-longer-at-original-path')
        result = subprocess.run(['/bin/bash', '--noprofile', '--rcfile', str(self.home / '.bashrc'), '-ic', 'command -v quick-audio'], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.home / '.local/bin/quick-audio'))

    def test_package_phase_uses_only_explicit_fake_commands(self):
        result = self.run_install()
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls[0], 'sudo')
        packages = [line for line in (self.repo / 'packages/arch.txt').read_text().splitlines() if line and not line.startswith('#')]
        self.assertEqual(calls[1], 'pacman <-Syu> <--needed> <--> ' + ' '.join(f'<{p}>' for p in packages))
        self.assertIn('FULL SYSTEM UPGRADE', result.stdout)
        self.assertIn('conflict', result.stdout)
        self.assertIn('native pacman confirmation', result.stdout)

    def test_unknown_argument_validated_before_packages(self):
        result = self.run_install('--help', '--typo', ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_failed_fake_pacman_stops_deployment(self):
        self.env['FAKE_PACMAN_EXIT'] = '23'
        result = self.run_install(ok=False)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_xdg_and_desktop_escaping_global_configs_untouched(self):
        special = self.base / 'paths with spaces \' quote " double % dollar $ backtick ` slash \\'
        special.mkdir()
        self.home = special / 'home'
        self.home.mkdir()
        self.env['HOME'] = str(self.home)
        for key, leaf in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')]:
            self.env[key] = str(special / leaf)
        config = Path(self.env['XDG_CONFIG_HOME'])
        for name in ('rofi/config.rasi', 'hypr/hyprland.conf', 'hypr/hyprland.lua'):
            path = config / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('MY GLOBAL CONFIG')
        self.run_install('--skip-packages')
        for name in ('rofi/config.rasi', 'hypr/hyprland.conf', 'hypr/hyprland.lua'):
            self.assertEqual((config / name).read_text(), 'MY GLOBAL CONFIG')
        desktops = list((Path(self.env['XDG_DATA_HOME']) / 'applications').glob('*.desktop'))
        self.assertEqual(len(desktops), 5)
        for desktop in desktops:
            self.assertIn('%%', desktop.read_text())
            if shutil.which('desktop-file-validate'):
                check = subprocess.run(['desktop-file-validate', str(desktop)], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        result = subprocess.run(['/bin/bash', '--noprofile', '--rcfile', str(self.home / '.bashrc'), '-ic', 'quick-audio'], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'test-tool')

    def test_relative_xdg_rejected_before_package_calls(self):
        self.env['XDG_CONFIG_HOME'] = 'relative-config'
        result = self.run_install(ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_changed_xdg_updates_managed_bash_block(self):
        self.run_install('--skip-packages')
        old = (self.home / '.bashrc').read_text()
        new_config = self.base / 'new-config'
        self.env['XDG_CONFIG_HOME'] = str(new_config)
        self.run_install('--skip-packages')
        rc = (self.home / '.bashrc').read_text()
        self.assertIn(str(new_config / 'system-tools/path.bash'), rc)
        self.assertEqual(rc.count('# >>> system-tools managed PATH >>>'), 1)
        self.assertNotEqual(rc, old)

    def test_unsafe_state_symlink_rejected_before_deployment(self):
        foreign = self.base / 'foreign-state'
        foreign.mkdir()
        state = self.home / '.local/state'
        state.mkdir(parents=True)
        (state / 'system-tools').symlink_to(foreign, target_is_directory=True)
        result = self.run_install('--skip-packages', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertFalse((self.home / '.local/bin').exists())

    def test_symlinked_destination_parent_refused(self):
        foreign = self.base / 'foreign-bin'
        foreign.mkdir()
        (self.home / '.local').mkdir()
        (self.home / '.local/bin').symlink_to(foreign, target_is_directory=True)
        result = self.run_install('--skip-packages', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(foreign.iterdir()), [])

    def test_changed_user_file_gets_unique_backups(self):
        self.run_install('--skip-packages')
        tool = self.home / '.local/bin/quick-audio'
        for content in ('# user change one', '# user change two'):
            tool.write_text(content)
            self.run_install('--skip-packages')
        backups = self.home / '.local/state/system-tools/backups'
        content = [p.read_text() for p in backups.rglob('*') if p.is_file() and not p.is_symlink()]
        self.assertIn('# user change one', content)
        self.assertIn('# user change two', content)
        self.assertEqual(len(list(backups.iterdir())), 2)

    def test_dry_run_does_not_need_python(self):
        self.env['PATH'] = str(self.fake)
        self.run_install('--dry-run')
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_help_does_not_write_or_call_packages(self):
        result = self.run_install('--help')
        self.assertIn('--skip-packages', result.stdout)
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_asset_directory_does_not_partially_deploy(self):
        shutil.rmtree(self.repo / 'config/hypr')
        result = self.run_install('--skip-packages', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_malformed_bash_block_does_not_partially_deploy(self):
        rc = self.home / '.bashrc'
        rc.write_text('# >>> system-tools managed PATH >>>\n# incomplete user edit\n')
        original = rc.read_bytes()
        result = self.run_install('--skip-packages', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(rc.read_bytes(), original)
        self.assertEqual(list(self.home.iterdir()), [rc])

    def test_dry_run_no_writes_or_package_calls(self):
        before = list(self.home.rglob('*'))
        result = self.run_install('--dry-run')
        self.assertEqual(list(self.home.rglob('*')), before)
        self.assertFalse(self.calls.exists())
        self.assertIn('pacman -Syu --needed --', result.stdout)
        self.assertIn('dry', result.stdout.lower())


if __name__ == '__main__':
    unittest.main()
