#!/usr/bin/env python3
"""Per-user deployment; use install.sh for the optional Arch package phase."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import stat
import tempfile
import json
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
START = '# >>> system-tools managed PATH >>>'
END = '# <<< system-tools managed PATH <<<'
TOOLS = {'quick-audio': 'Quick Audio', 'quick-brightness': 'Quick Brightness',
         'display-menu': 'Display Menu', 'clipboard-stack': 'Clipboard Stack',
         'focus-mode': 'Focus Mode'}


def xdg(name, default):
    value = Path(os.environ.get(name) or default)
    if not value.is_absolute():
        raise ValueError(f'{name} must be an absolute path')
    return value


def desktop_value(value):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def desktop_exec(path):
    # Exec quoting is interpreted after Desktop Entry string unescaping.
    value = str(path).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    # Keep field codes out of the executable token: GIO rejects %% there.
    return '/usr/bin/env ' + desktop_value('"' + value + '"')


def safe_parents(path):
    """Refuse symlinked parents rather than writing into someone else's tree."""
    for parent in reversed(path.parents):
        if parent.is_symlink():
            raise ValueError(f'Refusing symlinked parent directory: {parent}')
        if parent.exists() and not parent.is_dir():
            raise ValueError(f'Parent is not a directory: {parent}')


class Deployer:
    def __init__(self, state):
        self.state = state / 'system-tools'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.backups = self.state / 'backups' / f'{stamp}-{uuid.uuid4().hex}'
        safe_parents(self.backups / 'placeholder')
        log = self.state / 'install.log'
        if log.is_symlink() or (log.exists() and not log.is_file()):
            raise ValueError(f'Unsafe log destination: {log}')

    def private_directory(self, path):
        safe_parents(path / 'placeholder')
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)

    def log(self, path, backup):
        self.private_directory(self.state)
        log = self.state / 'install.log'
        if log.is_symlink() or (log.exists() and not log.is_file()):
            raise ValueError(f'Unsafe log destination: {log}')
        fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps({'installed': str(path), 'backup': str(backup) if backup else None}) + '\n')

    def backup(self, path):
        self.private_directory(self.state)
        self.private_directory(self.state / 'backups')
        self.private_directory(self.backups)
        # Every conflict gets an independent private directory, including symlink content.
        directory = self.backups / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        target = directory / path.name
        if path.is_symlink():
            # Preserve the link itself AND a content snapshot where it is a regular file.
            if path.is_file():
                content = directory / 'symlink-target-content'
                content.write_bytes(path.read_bytes())
                content.chmod(0o600)
            target.symlink_to(os.readlink(path))
        elif path.is_file():
            shutil.copy2(path, target)
            target.chmod(0o600)
        else:
            raise ValueError(f'Refusing to replace non-file destination: {path}')
        print(f'Backup: {path} -> {target}')
        return target

    def write(self, path, content, mode=0o644):
        safe_parents(path)
        if path.is_file() and not path.is_symlink() and path.read_bytes() == content and stat.S_IMODE(path.stat().st_mode) == mode:
            return False
        backup = self.backup(path) if os.path.lexists(path) else None
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.system-tools-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        self.log(path, backup)
        print(f'Installed: {path}')
        return True


def install():
    home = xdg('HOME', str(Path.home()))
    config = xdg('XDG_CONFIG_HOME', str(home / '.config'))
    data = xdg('XDG_DATA_HOME', str(home / '.local/share'))
    deploy = Deployer(xdg('XDG_STATE_HOME', str(home / '.local/state')))
    print(f'Private install log: {deploy.state / "install.log"}')
    print(f'Conflict backups (only when needed): {deploy.backups}')
    planned = []
    for source in sorted((ROOT / 'bin').iterdir()):
        if source.is_file():
            planned.append((home / '.local/bin' / source.name, source.read_bytes(), (stat.S_IMODE(source.stat().st_mode) & 0o777) | 0o111))
    for directory in ('rofi', 'hypr'):
        for source in sorted((ROOT / 'config' / directory).iterdir()):
            if source.is_file() and source.name not in ('config.rasi', 'hyprland.conf', 'hyprland.lua'):
                planned.append((config / directory / source.name, source.read_bytes(), 0o644))
    for command, name in TOOLS.items():
        text = ('[Desktop Entry]\nType=Application\nVersion=1.0\n' + f'Name={name}\n'
                + f'Exec={desktop_exec(home / ".local/bin" / command)}\n'
                + 'Terminal=false\nCategories=Utility;\n')
        planned.append((data / 'applications' / f'system-tools-{command}.desktop', text.encode(), 0o644))
    snippet = config / 'system-tools/path.bash'
    local_bin = shlex.quote(str(home / '.local/bin'))
    text = f'# Managed by system-tools; no shell or service changes.\ncase ":$PATH:" in\n  *:{local_bin}:*) ;;\n  *) export PATH={local_bin}:"$PATH" ;;\nesac\n'
    planned.append((snippet, text.encode(), 0o644))
    rc = home / '.bashrc'
    existing = rc.read_bytes() if rc.exists() else b''
    block = f'{START}\nif [ -r {shlex.quote(str(snippet))} ]; then\n    . {shlex.quote(str(snippet))}\nfi\n{END}\n'.encode()
    markers = {START.encode(): [], END.encode(): []}
    offset = 0
    for line in existing.splitlines(keepends=True):
        marker = line.rstrip(b'\r\n')
        if marker in markers:
            markers[marker].append((offset, offset + len(line)))
        offset += len(line)
    starts, ends = markers[START.encode()], markers[END.encode()]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError('Ambiguous managed PATH markers in .bashrc; repair them manually.')
        start = starts[0][0]
        end = ends[0][1]
        if start >= ends[0][0]:
            raise ValueError('Reversed managed PATH markers in .bashrc.')
        updated = existing[:start] + block + existing[end:]
    else:
        updated = existing + (b'\n' if existing and not existing.endswith(b'\n') else b'') + block
    mode = stat.S_IMODE(rc.stat().st_mode) if rc.exists() else 0o644
    planned.append((rc, updated, mode))
    # Validate all sources and destinations before changing any user files.
    for path, _, _ in planned:
        safe_parents(path)
        if os.path.lexists(path) and not path.is_symlink() and not path.is_file():
            raise ValueError(f'Refusing to replace non-file destination: {path}')
    for path, content, mode in planned:
        deploy.write(path, content, mode)
    print('Installed. Open a new Bash terminal or source ~/.bashrc for PATH.')
    print('No services, clipboard watcher, compositor reload, or hardware changes were performed.')
    print('Optional startup configuration is separate; consult README.md before enabling it.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.geteuid() == 0:
        parser.exit(1, 'Refusing root execution; run as your normal user.\n')
    try:
        install()
    except (OSError, ValueError) as error:
        parser.exit(1, f'Installation failed: {error}\n')


if __name__ == '__main__':
    main()
