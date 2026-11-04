# System Tools

Small desktop utilities for Arch Linux and Hyprland, collected with their themes and an installer so they can be moved to another machine.

## Commands

| Command | Purpose |
| --- | --- |
| `quick-audio [0–100]` | Set the default audio output volume; opens a rofi prompt without a number. |
| `quick-brightness [0–100]` | Set a supported backlight's brightness; opens a rofi prompt without a number. |
| `display-menu` | Select and verify a Hyprland display layout. |
| `clipboard-stack` | Pick from a local clipboard history. |
| `clipboard-watch` | Watch for clipboard changes while this process is running. |
| `focus-mode` | Start or stop a focus timer. |
| `system-tools-doctor` | Check required commands and installed assets without changing settings. |

Use `--help` on a command for its options. These are Linux desktop tools, not cross-platform Windows/macOS replacements.

## Install

Run the installer as your normal user, not with `sudo`:

```sh
./install.sh --dry-run
./install.sh
```

The default install uses `sudo pacman -Syu --needed` to install the packages in `packages/arch.txt`. This includes a full system upgrade, not just installation of these tools. Review pacman's transaction and any package conflicts before approving it. The package list includes the PipeWire audio stack and Hyprland; installing packages does not select your desktop session or guarantee hardware support.

Arch does not support partial upgrades. The installer does not use `pacman -Sy` alone, bypass signatures or automatically accept package prompts. See the [Arch system maintenance guidance](https://wiki.archlinux.org/title/System_maintenance#Partial_upgrades_are_unsupported).

If the dependencies are already installed:

```sh
./install.sh --skip-packages
```

The installer places commands in `~/.local/bin`, the supplied rofi themes in the XDG configuration directory, and application launchers in the XDG data directory. It adds a managed Bash PATH setup without replacing your existing `.bashrc`. Open a new Bash terminal after installation, or reload your Bash configuration.

Existing files that need replacing are backed up under the XDG state directory. The installer prints the backup location. Running it again does not add duplicate Bash setup blocks. Your existing global rofi and Hyprland configuration files are not replaced, and no services, clipboard watchers or display changes are started automatically.

## Check the installation

```sh
system-tools-doctor
system-tools-doctor --session
quick-audio --help
quick-brightness --help
```

The optional `--session` check makes read-only queries to Hyprland and the default audio output. It does not set brightness, volume or display layouts.

The audio and brightness prompts include their own minimal theme. Other menus use the supplied general theme, so they do not depend on a theme in the original author's home directory. Desktop entries also make the graphical tools available from an application launcher such as `rofi -show drun`.

## Hardware and session setup

- Run the graphical tools inside a working Wayland/Hyprland session. Installing Hyprland does not switch your current session.
- Audio requires a running PipeWire/WirePlumber stack and a usable default output. Existing audio packages may conflict with the installer package list; do not accept removals without reviewing them.
- Brightness currently targets a supported backlight. External-monitor DDC/CI brightness is not implemented. Device permissions must permit brightness control; the scripts do not silently elevate privileges.
- Display layouts are hardware-dependent. Check `display-menu --help` on a new machine and resolve ambiguous display selection before applying a layout. The included monitor module is separate from your own `monitors.lua`.
- Clipboard recording is opt-in. Run `clipboard-watch` when you want recording; `clipboard-stack` opens the history menu. Do not start duplicate watchers through several startup mechanisms.
- Clipboard history is private local runtime data, not part of this repository. Treat it as sensitive even though it is stored locally.
- Focus mode can be used from Bash. The `--status` and `--watch` outputs are available for optional Waybar integration. Notification silencing requires a compatible notification-daemon configuration; installing a package is not the same as configuring your existing daemon.

## Repository layout

```text
bin/                 Commands
config/rofi/         Self-contained prompt/menu themes
config/hypr/         Supporting display module
packages/arch.txt    Pacman dependencies
install.sh           Pacman phase and installer entry point
install.py           Per-user installation
tests/               Unit and isolated installation tests
```

## Tests

```sh
for script in install.sh bin/clipboard-watch; do bash -n "$script"; done
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Installation tests use temporary homes. Pacman and sudo transaction tests use explicit test doubles; they do not upgrade the host. Hardware/session commands are mocked in logic tests. Passing those tests is not a claim that a different laptop's backlight or display firmware has been physically tested.

GitHub Actions is configured to run the shell checks and tests on Python 3.12 and 3.14 after this repository is published.

## Updating and keeping data private

After updating this checkout, rerun the installer to update installed copies. Keep machine-specific settings, clipboard databases, logs, credentials and generated state out of Git. Ignore rules are included, but inspect `git status` and the staged diff before committing.

## Licence

MIT. See `LICENSE`.
