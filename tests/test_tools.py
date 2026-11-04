"""Isolated regression tests: never contact the running desktop."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock, call

REPO = Path(__file__).resolve().parents[1]
TOOLS = ('quick-audio', 'quick-brightness', 'display-menu', 'focus-mode',
         'clipboard-stack', 'clipboard-watch')


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        self.home = Path(self.tmp.name)
        self.env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / 'config'),
                    'XDG_CACHE_HOME': str(self.home / 'cache'),
                    'XDG_STATE_HOME': str(self.home / 'state'),
                    'XDG_RUNTIME_DIR': str(self.home / 'run')}
        (self.home / 'run').mkdir()
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def load(self, name):
        loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(REPO / 'bin' / name))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_help_is_side_effect_free(self):
        fakebin = self.home / 'fakebin'
        fakebin.mkdir()
        log = self.home / 'calls'
        for name in ('rofi', 'wpctl', 'brightnessctl', 'hyprctl', 'makoctl', 'notify-send', 'wl-paste', 'wl-copy'):
            p = fakebin / name
            p.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$HOME/calls"\nexit 1\n')
            p.chmod(0o755)
        env = dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH'])
        for name in TOOLS:
            with self.subTest(tool=name):
                result = subprocess.run([str(REPO / 'bin' / name), '--help'], env=env,
                                        capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Usage:', result.stdout)
        self.assertFalse(log.exists(), log.read_text() if log.exists() else '')
        self.assertEqual(list((self.home / 'run').iterdir()), [])

    def test_menus_ignore_global_rofi_config_and_use_xdg_themes(self):
        for name in ('quick-audio', 'quick-brightness', 'focus-mode', 'display-menu', 'clipboard-stack'):
            with self.subTest(tool=name):
                module = self.load(name)
                if name == 'clipboard-stack':
                    module.store(b'isolated test', 'text')
                with patch.object(module.subprocess, 'run', return_value=Mock(returncode=1, stdout='', stderr='')) as run:
                    with patch.object(module.sys, 'argv', [name]):
                        module.main()
                args = next(c.args[0] for c in run.call_args_list if c.args[0][0] == 'rofi')
                self.assertIn('-no-config', args)
                theme = args[args.index('-theme') + 1]
                expected = 'quick-audio.rasi' if name.startswith('quick-') else 'system-tools.rasi'
                self.assertEqual(theme, str(self.home / 'config' / 'rofi' / expected))
                source = (REPO / 'config/rofi' / expected).read_text()
                self.assertNotIn('Montserrat', source)
                self.assertNotIn('@import', source)
                self.assertNotIn('/home/', source)

    def test_focus_start_launches_bounded_detached_completion_worker(self):
        focus = self.load('focus-mode')
        clock = [1000.0]
        def sleep(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, 1)
            clock[0] += seconds
        with patch.object(focus, 'command', return_value=True) as command, \
             patch.object(focus.subprocess, 'Popen') as spawn, \
             patch.object(focus.time, 'time', side_effect=lambda: clock[0]), \
             patch.object(focus.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(focus.time, 'sleep', side_effect=sleep):
            focus.start(20)
            spawn.assert_called_once()
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][0], sys.executable)
            self.assertEqual(args[0][2], '--worker')
            self.assertTrue(kwargs['start_new_session'])
            session = focus.read_session()
            self.assertEqual(args[0][3], session['token'])
            focus.worker(session['token'])
            self.assertIsNone(focus.read_session())
            self.assertEqual(clock[0], 2200)
            self.assertEqual(sum(c.args[0] == 'notify-send' for c in command.call_args_list), 1)
            focus.status()
            focus.worker(session['token'])
            self.assertEqual(sum(c.args[0] == 'notify-send' for c in command.call_args_list), 1)
            self.assertIn(call('makoctl', 'mode', '-r', focus.QUIET), command.call_args_list)

    def test_focus_stop_cleans_quiet_mode_and_worker_exits(self):
        focus = self.load('focus-mode')
        with patch.object(focus, 'command', return_value=True) as command, \
             patch.object(focus.subprocess, 'Popen'), \
             patch.object(focus.time, 'sleep') as sleep:
            focus.start(20)
            token = focus.read_session()['token']
            with patch.object(focus.sys, 'argv', ['focus-mode', 'stop']):
                focus.main()
            focus.worker(token)
            sleep.assert_not_called()
            self.assertIsNone(focus.read_session())
            self.assertEqual(sum(c.args[0] == 'notify-send' for c in command.call_args_list), 1)
            command.reset_mock()
            with patch.object(focus.sys, 'argv', ['focus-mode', 'stop']):
                focus.main()
            command.assert_called_once_with('makoctl', 'mode', '-r', focus.QUIET)

    def test_focus_replacement_and_spawn_failure(self):
        focus = self.load('focus-mode')
        with patch.object(focus, 'command', return_value=True) as command, \
             patch.object(focus.subprocess, 'Popen') as spawn, \
             patch.object(focus.time, 'sleep') as sleep:
            focus.start(20)
            old = focus.read_session()['token']
            focus.start(30)
            new = focus.read_session()['token']
            self.assertNotEqual(old, new)
            focus.worker(old)
            sleep.assert_not_called()
            self.assertEqual(focus.read_session()['token'], new)
            self.assertFalse(any(c.args[0] == 'notify-send' for c in command.call_args_list))
            spawn.side_effect = OSError('mock spawn failure')
            with self.assertRaises(OSError):
                focus.start(45)
            self.assertIsNone(focus.read_session())
            self.assertEqual(command.call_args.args, ('makoctl', 'mode', '-r', focus.QUIET))

    def test_focus_write_failure_restores_quiet_mode(self):
        focus = self.load('focus-mode')
        with patch.object(focus, 'command', return_value=True) as command, \
             patch.object(focus.os, 'replace', side_effect=OSError('mock disk failure')), \
             patch.object(focus.subprocess, 'Popen') as spawn:
            with self.assertRaises(OSError):
                focus.start(20)
            spawn.assert_not_called()
            self.assertIsNone(focus.read_session())
            self.assertFalse(focus.STATE.with_suffix('.tmp').exists())
            self.assertEqual(command.call_args.args, ('makoctl', 'mode', '-r', focus.QUIET))

    def test_focus_real_terminal_worker_completes_without_waybar(self):
        fakebin = self.home / 'fakebin'
        fakebin.mkdir()
        for name in ('makoctl', 'notify-send'):
            p = fakebin / name
            p.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$HOME/calls"\n')
            p.chmod(0o755)
        env = dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH'])
        tool = str(REPO / 'bin/focus-mode')
        state = self.home / 'run/focus-mode/session.json'
        def stop():
            subprocess.run([tool, 'stop'], env=env, capture_output=True, timeout=4)
            time.sleep(1.1)  # allow the bounded child to observe removal before teardown
        self.addCleanup(stop)
        result = subprocess.run([tool, '20'], env=env, capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        session = json.loads(state.read_text())
        self.assertIn('token', session)
        session['end'] = 0  # advance the mocked session deadline, not real time
        temporary = state.with_suffix('.test')
        temporary.write_text(json.dumps(session))
        temporary.replace(state)
        deadline = time.monotonic() + 4
        while state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(state.exists(), 'detached worker did not finish the session')
        calls = (self.home / 'calls').read_text()
        self.assertEqual(calls.count('Session complete.'), 1)
        self.assertIn('mode -r focus-timer', calls)

    @staticmethod
    def snapshot():
        return [dict(name='eDP-7', id=3, width=1600, height=1000, scale=1.25,
                     x=0, y=0, transform=0, mirrorOf='none', disabled=False),
                dict(name='DP-4', id=9, width=1920, height=1080, scale=1,
                     x=1280, y=0, transform=0, mirrorOf='none', disabled=False)]

    def test_display_discovers_names_scales_and_xdg_settings(self):
        display = self.load('display-menu')
        self.assertEqual(display.PATH, self.home / 'config/hypr/monitor-mode')
        settings = self.home / 'config/hypr/system-tools-displays.json'
        outputs = display.resolve_outputs(self.snapshot())
        self.assertEqual(outputs, {'laptop': 'eDP-7', 'external': 'DP-4',
                                  'laptop_scale': 1.25, 'external_scale': 1, 'connected': True})
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'laptop': 'eDP-7', 'external': 'DP-4',
                                       'laptop_scale': 2, 'external_scale': 1.25}))
        outputs = display.resolve_outputs(self.snapshot())
        self.assertEqual(outputs['laptop_scale'], 2)
        self.assertEqual(outputs['external_scale'], 1.25)
        for invalid in ({'external_scale': 0}, {'laptop_scale': 'shell code'}, {'typo': 1},
                        {'laptop': 'missing'}, {'laptop': 'eDP-7', 'external': 'eDP-7'}):
            settings.write_text(json.dumps(invalid))
            with self.subTest(settings=invalid), self.assertRaises(ValueError):
                display.resolve_outputs(self.snapshot())

    def test_display_unknown_reported_scale_uses_auto_and_desktop_roles_are_explicit(self):
        display = self.load('display-menu')
        snapshot = self.snapshot()
        snapshot[0].update(scale=0, disabled=True)
        self.assertEqual(display.resolve_outputs(snapshot)['laptop_scale'], 'auto')
        snapshot[0]['name'] = 'DP-6'
        with self.assertRaises(ValueError):
            display.resolve_outputs(snapshot)
        display.SETTINGS.parent.mkdir(parents=True)
        display.SETTINGS.write_text(json.dumps({'laptop': 'DP-6', 'external': 'DP-4'}))
        self.assertEqual(display.resolve_outputs(snapshot)['laptop'], 'DP-6')

    def test_display_refuses_ambiguous_or_unsupported_outputs_without_writes(self):
        display = self.load('display-menu')
        cases = [self.snapshot() + [dict(self.snapshot()[1], name='DP-5')],
                 [self.snapshot()[1]],
                 [dict(self.snapshot()[0], transform=1), self.snapshot()[1]],
                 [self.snapshot()[0], dict(self.snapshot()[1], name='HEADLESS-1')]]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), \
                 patch.object(display, 'monitors', return_value=snapshot), \
                 patch.object(display, 'hypr') as hypr, patch.object(display, 'notify'):
                with self.assertRaises(ValueError):
                    display.switch('right')
                hypr.assert_not_called()
                self.assertFalse(display.PATH.exists())

    def test_display_verifies_selected_connectors_in_each_layout(self):
        display = self.load('display-menu')
        a, b = self.snapshot()
        outputs = display.resolve_outputs([a, b])
        snapshots = {
            'right': [a, b],
            'left': [dict(a, x=1920), dict(b, x=0)],
            'mirror': [a, dict(b, x=0, mirrorOf=a['id'])],
            'laptop': [a, dict(b, disabled=True)],
            'external': [dict(a, disabled=True), dict(b, x=0)],
            'auto': [dict(a, disabled=True), dict(b, x=0)],
        }
        for mode, snapshot in snapshots.items():
            with self.subTest(mode=mode):
                self.assertTrue(display.layout_matches(mode, snapshot, outputs))
                self.assertFalse(display.layout_matches(mode, snapshot + [dict(b, name='DP-8')], outputs))
        self.assertFalse(display.layout_matches('right', [a, dict(b, x=500)], outputs))
        self.assertFalse(display.layout_matches('mirror', [a, dict(b, mirrorOf='other')], outputs))
        self.assertTrue(display.layout_matches('auto', [a], outputs))

    def test_display_apply_uses_supplied_lua_module_and_requires_stable_state(self):
        display = self.load('display-menu')
        display.MODULE.parent.mkdir(parents=True)
        shutil.copyfile(REPO / 'config/hypr/system-tools-monitors.lua', display.MODULE)
        snapshot = self.snapshot()
        outputs = display.resolve_outputs(snapshot)
        phases = iter(['preparing:right', 'settling:right', 'ready:right', 'ready:right', 'ready:right'])
        def hypr(*args):
            return 'ok' if args[0] == 'eval' else next(phases)
        with patch.object(display, 'hypr', side_effect=hypr) as call, \
             patch.object(display, 'monitors', return_value=snapshot) as monitors, \
             patch.object(display.time, 'sleep'):
            self.assertEqual(display.apply('right', outputs), snapshot)
            self.assertEqual(monitors.call_count, 5)
            self.assertIn('system-tools-monitors', call.call_args_list[0].args[1])
            self.assertNotIn('require("monitors")', call.call_args_list[0].args[1])
        with patch.object(display, 'hypr', side_effect=['ok', 'failed:right:mock failure']), \
             patch.object(display, 'monitors', return_value=snapshot):
            with self.assertRaisesRegex(RuntimeError, 'mock failure'):
                display.apply('right', outputs)

    def test_display_failed_transition_rolls_back_and_missing_module_is_safe(self):
        display = self.load('display-menu')
        display.PATH.parent.mkdir(parents=True)
        display.PATH.write_text('left\n')
        with patch.object(display, 'monitors', return_value=self.snapshot()), \
             patch.object(display, 'hypr', return_value='true') as hypr, \
             patch.object(display, 'notify'), patch.object(display, 'apply') as apply, \
             patch.object(display.sys, 'stderr'):
            with self.assertRaisesRegex(RuntimeError, 'module'):
                display.switch('right')
            hypr.assert_not_called()
            apply.assert_not_called()
            self.assertEqual(display.PATH.read_text(), 'left\n')
            shutil.copyfile(REPO / 'config/hypr/system-tools-monitors.lua', display.MODULE)
            apply.side_effect = [RuntimeError('mock modeset failed'), self.snapshot()]
            self.assertEqual(display.switch('right'), 1)
            self.assertEqual([c.args[0] for c in apply.call_args_list], ['right', 'left'])
            self.assertEqual(display.PATH.read_text(), 'left\n')
            self.assertIn('Previous layout restored.', (self.home / 'state/display-menu/last-error.log').read_text())

    def test_display_first_run_recovers_observed_layout_not_automatic_external_only(self):
        display = self.load('display-menu')
        display.MODULE.parent.mkdir(parents=True)
        shutil.copyfile(REPO / 'config/hypr/system-tools-monitors.lua', display.MODULE)
        with patch.object(display, 'monitors', return_value=self.snapshot()), \
             patch.object(display, 'hypr', return_value='true'), patch.object(display, 'notify'), \
             patch.object(display, 'apply', side_effect=[RuntimeError('mock failure'), self.snapshot()]) as apply, \
             patch.object(display.sys, 'stderr'):
            self.assertEqual(display.switch('left'), 1)
            self.assertEqual([c.args[0] for c in apply.call_args_list], ['left', 'right'])
            self.assertFalse(display.PATH.exists())

    @unittest.skipUnless(shutil.which('lua'), 'Lua interpreter is optional')
    def test_lua_helper_stages_and_verifies_layouts_without_real_hyprland(self):
        module = REPO / 'config/hypr/system-tools-monitors.lua'
        self.assertTrue(module.is_file(), 'supporting Lua module must be packaged')
        stub = r'''
local pending, mons, tick, timer = {}, {}, nil, nil
local function reset()
    mons = {
        ["eDP-7"] = {name="eDP-7", id=3, width=1600, height=1000, scale=1.25, x=0, y=0, enabled=true, is_mirror=false, mirrors={}},
        ["DP-4"] = {name="DP-4", id=9, width=1920, height=1080, scale=1, x=1280, y=0, enabled=true, is_mirror=false, mirrors={}}
    }
end
hl = {
    get_monitors = function(opts)
        assert(opts.all == true)
        local list = {}
        for _, mon in pairs(mons) do list[#list+1] = mon end
        return list
    end,
    monitor = function(rule) pending[#pending+1] = rule end,
    timer = function(callback, opts)
        assert(opts.timeout == 250 and opts.type == "repeat")
        tick = callback
        timer = {enabled=true, set_enabled=function(self, value) self.enabled=value end}
        return timer
    end
}
local function flush()
    for _, rule in ipairs(pending) do
        local mon = assert(mons[rule.output], "must not configure absent outputs")
        mon.enabled = not rule.disabled
        mon.is_mirror = rule.mirror ~= ""
        local x,y = rule.position:match("^(%d+)x(%d+)$")
        if x then mon.x, mon.y = tonumber(x), tonumber(y) end
        if type(rule.scale) == "number" then mon.scale = rule.scale end
    end
    pending = {}
end
reset()
local helper = dofile(arg[1])
assert(#pending == 0, "loading module must not change monitor config")
for _, mode in ipairs({"right", "left", "mirror", "external", "laptop", "auto"}) do
    helper.apply({mode=mode,laptop="eDP-7",external="DP-4",laptop_scale=1.25,external_scale=1,connected=true})
    assert(#pending == 2)
    for _, rule in ipairs(pending) do
        assert(rule.disabled == false and rule.mirror == "", "prepare independent outputs before disabling/mirroring")
    end
    flush()
    tick()
    assert(helper.status() == "settling:" .. mode)
    flush()
    tick()
    assert(helper.status() == "ready:" .. mode, helper.status())
    assert(timer.enabled == false, "no perpetual monitor enforcement")
end
reset()
helper.apply({mode="external",laptop="eDP-7",external="DP-4",laptop_scale=1.25,external_scale=1,connected=true})
-- Never flush: emulate a compositor that cannot finish a modeset.
for i=1,25 do tick() end
assert(helper.status():match("^failed:"), helper.status())
assert(timer.enabled == false)
print("Lua staged transitions and bounded failure passed")
'''
        result = subprocess.run(['lua', '-', str(module)], input=stub,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bounded failure passed', result.stdout)

    def test_clipboard_history_is_isolated_bounded_and_lossless(self):
        clipboard = self.load('clipboard-stack')
        for i in range(12):
            clipboard.store(f'item {i}'.encode(), 'text')
        self.assertEqual(len(clipboard.entries()), 10)
        self.assertEqual(clipboard.entries()[-1][2], b'item 2')
        content = b'multiline\nwith\ttabs\x00and bytes'
        clipboard.store(content, 'text')
        clipboard.store(b'item 11', 'text')
        clipboard.store(content, 'text')
        self.assertEqual(clipboard.entries()[0][2], content)
        self.assertEqual(sum(row[2] == content for row in clipboard.entries()), 1)
        clipboard.store(b'x' * (5 * 1024 * 1024 + 1), 'image')
        self.assertEqual(clipboard.entries()[0][2], content)
        with patch.object(clipboard.subprocess, 'run', side_effect=[Mock(returncode=0, stdout='0\n'), Mock(returncode=0)]) as run:
            clipboard.menu()
            self.assertEqual(run.call_args_list[1].kwargs['input'], content)
            self.assertEqual(run.call_args_list[1].args[0][0], 'wl-copy')
        for state in ('sensitive', 'nil', 'clear'):
            with patch.dict(os.environ, CLIPBOARD_STATE=state), \
                 patch.object(clipboard.sys, 'argv', ['clipboard-stack', 'store', 'text']), \
                 patch.object(clipboard, 'store') as store:
                clipboard.main()
                store.assert_not_called()
        self.assertTrue(str(clipboard.DB).startswith(str(self.home)))

    def check_clipboard_watcher(self):
        fakebin = self.home / 'fakebin'
        fakebin.mkdir()
        paste = fakebin / 'wl-paste'
        paste.write_text('#!/usr/bin/env python3\nimport json, os, sys, time\n'
                         'from pathlib import Path\n'
                         'Path(os.environ["HOME"], "watch-" + sys.argv[sys.argv.index("--type")+1]).write_text(json.dumps(sys.argv[1:]))\n'
                         'time.sleep(0.3)\n')
        paste.chmod(0o755)
        result = subprocess.run([str(REPO / 'bin/clipboard-watch')],
                                env=dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH']),
                                capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        for kind in ('text', 'image'):
            args = json.loads((self.home / ('watch-' + kind)).read_text())
            index = args.index('--watch')
            self.assertEqual(args[index+1:], [str(REPO / 'bin/clipboard-stack'), 'store', kind])

    def test_clipboard_watch_uses_sibling_binary_and_reaps_watchers(self):
        self.check_clipboard_watcher()

    def test_clipboard_watch_without_runtime_uses_private_home_fallback(self):
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': ''}):
            self.check_clipboard_watcher()
        private = self.home / 'state/system-tools/runtime'
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertTrue((private / 'clipboard-stack.lock').exists())

    def test_private_data_permissions_do_not_change_shared_directories(self):
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        cache = self.home / 'cache'
        cache.mkdir(mode=0o755)
        clipboard = self.load('clipboard-stack')
        clipboard.store(b'private test fixture', 'text')
        self.assertEqual(clipboard.DB.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(clipboard.DB.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cache.stat().st_mode & 0o777, 0o755)
        focus = self.load('focus-mode')
        with patch.object(focus, 'command', return_value=True), patch.object(focus.subprocess, 'Popen'):
            focus.start(20)
        self.assertEqual(focus.ROOT.stat().st_mode & 0o777, 0o700)
        self.assertEqual(focus.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual((focus.ROOT / 'lock').stat().st_mode & 0o777, 0o600)
        display = self.load('display-menu')
        display.MODULE.parent.mkdir(parents=True)
        shutil.copyfile(REPO / 'config/hypr/system-tools-monitors.lua', display.MODULE)
        with patch.object(display, 'monitors', return_value=self.snapshot()), \
             patch.object(display, 'hypr', return_value='true'), patch.object(display, 'notify'), \
             patch.object(display, 'apply', side_effect=[RuntimeError('mock failure'), self.snapshot()]), \
             patch.object(display.sys, 'stderr'):
            display.switch('left')
        log = self.home / 'state/display-menu/last-error.log'
        self.assertEqual(log.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_empty_xdg_variables_use_home_defaults(self):
        empty = {name: '' for name in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR')}
        with patch.dict(os.environ, empty):
            for name in ('quick-audio', 'quick-brightness', 'focus-mode', 'display-menu', 'clipboard-stack'):
                module = self.load(name)
                expected = 'quick-audio.rasi' if name.startswith('quick-') else 'system-tools.rasi'
                self.assertEqual(module.THEME, self.home / '.config/rofi' / expected, name)
            clipboard = self.load('clipboard-stack')
            self.assertEqual(clipboard.DB, self.home / '.cache/clipboard-stack/history.sqlite3')
            focus = self.load('focus-mode')
            self.assertEqual(focus.ROOT, self.home / '.local/state/system-tools/runtime/focus-mode')
            with focus.locked():
                self.assertEqual(focus.RUNTIME.stat().st_mode & 0o777, 0o700)
            display = self.load('display-menu')
            self.assertEqual(display.RUNTIME, self.home / '.local/state/system-tools/runtime')
            with patch.object(display.sys, 'argv', ['display-menu', 'laptop']), patch.object(display, 'switch', return_value=0) as switch:
                self.assertEqual(display.main(), 0)
                switch.assert_called_once_with('laptop')

    def test_numeric_quick_modes_do_not_open_rofi(self):
        for name, command in [('quick-audio', 'wpctl'), ('quick-brightness', 'brightnessctl')]:
            module = self.load(name)
            with patch.object(module.subprocess, 'run', return_value=Mock(returncode=0, stdout='', stderr='')) as run:
                with patch.object(module.sys, 'argv', [name, '42']):
                    module.main()
            self.assertTrue(all(c.args[0][0] == command for c in run.call_args_list))
            self.assertIn('42%', run.call_args_list[0].args[0])
            for invalid in ('101', '-1', '1.5', 'hello'):
                with self.subTest(tool=name, value=invalid), patch.object(module.subprocess, 'run') as run:
                    with patch.object(module.sys, 'argv', [name, invalid]):
                        with self.assertRaises(ValueError):
                            module.main()
                    run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
