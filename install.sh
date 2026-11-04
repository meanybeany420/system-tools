#!/usr/bin/env bash
# Bootstrap with Bash only: Python is installed in the package phase.
set -euo pipefail
script=${BASH_SOURCE[0]}
script_dir=${script%/*}
[[ $script_dir != "$script" ]] || script_dir=.
root=$(cd -- "$script_dir" && pwd)
dry_run=false
skip_packages=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true ;;
        --skip-packages) skip_packages=true ;;
        --help|-h) ;; # Handle after validating every argument.
        *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
for arg in "$@"; do
    if [[ $arg == --help || $arg == -h ]]; then
        printf '%s\n' 'Usage: ./install.sh [--dry-run] [--skip-packages] [--help]' \
          'Default: sudo pacman -Syu --needed -- <packages>, then per-user deployment.' \
          '--dry-run: Bash-only preview; no writes, sudo, or package calls.' \
          '--skip-packages: offline deployment with already-installed dependencies.' \
          'Never run this installer with sudo; only pacman is elevated.'
        exit 0
    fi
done
for key in HOME XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME; do
    value=${!key-}
    if [[ -n $value && $value != /* ]]; then
        printf '%s must be an absolute path.\n' "$key" >&2; exit 2
    fi
done
[[ -n ${HOME-} ]] || { printf 'HOME must be set.\n' >&2; exit 2; }
packages=()
while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] && continue
    [[ $line =~ ^[a-z0-9][a-z0-9@._+-]*$ ]] || { printf 'Invalid package name: %s\n' "$line" >&2; exit 2; }
    packages+=("$line")
done < "$root/packages/arch.txt"
if $dry_run; then
    printf '%s\n' 'DRY RUN: no files or package state will change.'
    if ! $skip_packages; then
        printf 'Would run: sudo pacman -Syu --needed --'; printf ' %s' "${packages[@]}"; printf '\n'
    fi
    printf 'Would copy bin/* to %s/.local/bin and managed assets under %s and %s.\n' "$HOME" "${XDG_CONFIG_HOME:-$HOME/.config}" "${XDG_DATA_HOME:-$HOME/.local/share}"
    printf 'Would manage Bash PATH and private backups/logs under %s/system-tools.\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
    exit 0
fi
if (( EUID == 0 )); then
    printf 'Refusing root execution: run as your normal user (sudo is only for pacman).\n' >&2
    exit 1
fi
if ! $skip_packages; then
    printf '%s\n' 'NOTICE: pacman will perform a FULL SYSTEM UPGRADE (-Syu).' \
      'PipeWire audio packages may conflict with/replace PulseAudio or another session manager.' \
      'Review the transaction and conflicts carefully; native pacman confirmation is retained.'
    sudo pacman -Syu --needed -- "${packages[@]}"
fi
command -v python3 >/dev/null || { printf 'python3 is required; install Arch python or omit --skip-packages.\n' >&2; exit 1; }
exec python3 "$root/install.py"
