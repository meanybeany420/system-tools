"""Read-only diagnostic checks; external tools and homes are test fixtures."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_doctor():
    loader = importlib.machinery.SourceFileLoader('toolkit_doctor', str(ROOT / 'bin/system-tools-doctor'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class DoctorTests(unittest.TestCase):
    def test_missing_dependencies_are_reported_without_running_commands(self):
        module = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), patch.object(module.shutil, 'which', return_value=None), patch.object(module.subprocess, 'run') as run:
                checks = module.collect_checks()
                self.assertTrue(any(c['status'] == 'FAIL' and c['name'] == 'python3' for c in checks))
                self.assertTrue(any(c['status'] == 'FAIL' and 'quick-audio.rasi' in c['name'] for c in checks))
                run.assert_not_called()

    def test_installed_dependencies_and_assets_pass_offline_checks(self):
        module = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            for relative in ('rofi/quick-audio.rasi', 'rofi/system-tools.rasi', 'hypr/system-tools-monitors.lua'):
                path = config / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test asset\n')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), patch.object(module.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), patch.object(module.subprocess, 'run') as run:
                checks = module.collect_checks()
                self.assertTrue(checks)
                self.assertTrue(all(c['status'] == 'PASS' for c in checks), checks)
                run.assert_not_called()

    def test_session_probe_is_opt_in_and_only_uses_read_commands(self):
        module = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), patch.object(module.shutil, 'which', return_value='/usr/bin/fixture'), patch.object(module.subprocess, 'run', return_value=module.subprocess.CompletedProcess([], 0, stdout='fixture', stderr='')) as run:
                checks = module.collect_checks(session=True)
                self.assertEqual([call.args[0] for call in run.call_args_list], [['hyprctl', '-j', 'monitors', 'all'], ['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@']])
                self.assertTrue(all(call.kwargs['timeout'] == 5 for call in run.call_args_list))
                self.assertTrue(any(c['name'] == 'Hyprland session' for c in checks))


if __name__ == '__main__':
    unittest.main()
