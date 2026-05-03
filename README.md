# 3dsutil

`3dsutil` is a small, dependency-free Python utility for wirelessly moving homebrew files from your computer to a modded 3DS on the same network.

It is especially useful when a newly modded 3DS does not yet have base apps such as ftpd, FBI, or Universal-Updater, and you cannot easily copy files with an SD card reader. Use NetLoader to wirelessly launch a setup app first, then use FTP for everyday file management after the console has ftpd installed.

Main components:

- Interactive TUI: run `3dsutil` to choose NetLoader, FTP, update, or quit from one terminal interface.
- NetLoader: bootstrap a console by loading one `.3dsx` through Homebrew Launcher's 3dslink NetLoader. On the 3DS, open Homebrew Launcher and press `Y`, then use `3dsutil` to send Universal-Updater, FBI, or another setup app wirelessly.
- FTP: browse local files and the 3DS SD card side by side through ftpd or another 3DS FTP server. Use it for uploads, downloads, copies, moves, deletes, and archive extraction once FTP is available on the console.

## Requirements

- Python 3
- a modded 3DS on the same network as the computer
- Homebrew Launcher NetLoader for `netloader`
- ftpd or another 3DS FTP server for `ftp`

## Installation

Install the latest version of the `3dsutil` command with:

```bash
curl -fsSL https://raw.githubusercontent.com/spkskx/3dsutil/main/install.sh | sh
```

Install a specific tagged version with:

```bash
curl -fsSL https://raw.githubusercontent.com/spkskx/3dsutil/main/install.sh | INSTALL_REF=1.3 sh
```

The installer checks for Python 3 and git. If either is missing, it asks before running a package-manager install command for `apt-get`, `dnf`, `pacman`, or Homebrew.

By default it installs the source into `~/.local/lib/3dsutil.py` and writes a launcher to `~/.local/bin/3dsutil`. Make sure `~/.local/bin` is in your `PATH`.

After installation:

```bash
3dsutil --help
3dsutil
3dsutil install --help
3dsutil update --help
3dsutil uninstall --help
3dsutil netloader --help
3dsutil ftp --help
3dsutil ftp explorer --help
```

Once Python and git are available, the CLI can manage itself:

```bash
3dsutil install
3dsutil install --ref 1.3
3dsutil update
3dsutil update --ref 1.3
3dsutil uninstall
```

## Usage

Start with the built-in help. It is the source of truth for commands and options:

```bash
3dsutil --help
3dsutil
3dsutil install --help
3dsutil update --help
3dsutil uninstall --help
3dsutil netloader --help
3dsutil netloader load --help
3dsutil netloader status --help
3dsutil ftp --help
3dsutil ftp explorer --help
3dsutil ftp upload --help
3dsutil ftp status --help
```

Common commands:

```bash
3dsutil

3dsutil netloader sample-app.3dsx
3dsutil netloader --host 172.20.10.12 sample-app.3dsx
3dsutil netloader status --host 172.20.10.12

3dsutil ftp --host 172.20.10.12
3dsutil ftp explorer --host 172.20.10.12 --source . --dest /3ds/
3dsutil ftp upload --host 172.20.10.12 --source sample-app.3dsx --dest /3ds/
3dsutil ftp upload --host 172.20.10.12 --source first.nds --source second.gba --dest /roms/
3dsutil ftp upload --host 172.20.10.12 --source roms.zip --dest /roms/ --unarchive --patterns "*.nds"
3dsutil ftp status --host 172.20.10.12
```

If `netloader status` succeeds, restart NetLoader before loading a file: press `B`, then press `Y` in the Homebrew Launcher.

### Interactive TUI

Run `3dsutil` with no arguments to open the interactive terminal UI.

- Choose `NetLoader - load one .3dsx`, then choose whether to scan the network or enter a custom address.
- For scanning, open Homebrew Launcher on the 3DS and press `Y` before starting the scan. If scanning fails, the TUI returns to the NetLoader home screen.
- Scanning uses UDP discovery only. The TUI does not open a NetLoader TCP connection until after you choose a `.3dsx` file to load.
- For a custom address, enter a host such as `172.20.10.12`, or enter `host:port` such as `172.20.10.12:17491`. If you enter only a host, the TUI explicitly prompts for the port next.
- After selecting a NetLoader target, browse the current directory and choose one `.3dsx` file to upload and launch.
- After selecting a file, the TUI shows a processing popup while the load runs. If loading fails, it shows the error in a popup and asks you to start NetLoader again on the 3DS by pressing `Y` in Homebrew Launcher and check the network connection.
- A successful load shows a popup; press `Enter`, `c`, `q`, or `Esc` to return to the main TUI menu.
- Choose `FTP - browse and transfer files`, start ftpd on the 3DS, then enter the FTP host. You can enter `host:port`, such as `172.20.10.12:5000`, or enter only the host and provide the port when prompted.
- Choose `Update 3dsutil` from an installed `3dsutil` command to update the local checkout and launcher.
- Choose `Quit`, or press `q`/`Esc`, to leave the TUI.

### FTP explorer controls

The FTP explorer opens local files on the left and the 3DS FTP server on the right. Use `--source` to choose the local starting directory and `--dest` to choose the initial 3DS directory. The local pane cannot browse above its starting directory, while the 3DS pane can still browse up to the console's FTP root.

- Move the cursor with `Up`/`Down` or `j`/`k`.
- Switch panes with `Left`/`Right` or `h`/`l`.
- Open a directory with `Enter`; go up with `Backspace`.
- Mark files or directories with `Space`. Hold Shift while moving with `Up`/`Down` or use `J`/`K` to range-mark as the cursor moves.
- Starting marks on one pane clears marks from the other pane, so copy/move/delete targets always come from one side.
- Press `u` to unmark everything.
- Press `p` to paste. Pasting within the same pane moves; pasting across panes copies.
- Press `d` to delete. The confirmation dialog lists every file or directory that will be deleted.
- During a transfer, press `c`, `q`, or `Esc` to cancel.

When copying from local to 3DS, archives can be extracted before upload. When moving within one pane, the explorer prevents moving a directory into itself or into one of its children.

## Troubleshooting

- If discovery fails, pass `--host <3DS_IP>`.
- If NetLoader rejects a file, confirm it is a `.3dsx` file.
- FTP commands prompt for a host in interactive terminals. In scripts, pass `--host <3DS_IP>`.
- If FTP upload paths look wrong, check `3dsutil ftp upload --help`.
- `.7z` extraction requires a `7z` or `7zz` command in `PATH`.

## Development

Development checkouts can run the script directly:

```bash
python3 3dsutil.py --help
python3 3dsutil.py
```

Run the test suite with:

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## Changelog

### 1.3

- Added the interactive TUI as the default `3dsutil` experience.
- Added NetLoader scan/custom host flows, a `.3dsx` file picker, processing/success/error popups, and installed-command update access from the TUI.
- Added multi-device NetLoader discovery while keeping file loading as the first TCP connection.

### 1.2

- Expanded FTP support with the side-by-side explorer, local and remote copy/move/delete workflows, transfer progress, cancellation, and archive handling for `.zip` and `.7z`.

### 1.1

- Added command management flows for installing, updating, and uninstalling the `3dsutil` launcher.
- Kept direct NetLoader and FTP commands scriptable through explicit subcommands.

### 1.0

- Added the initial NetLoader upload path for sending and launching one `.3dsx` file over the local network.

## License

MIT. See `LICENSE`.
