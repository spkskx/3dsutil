# 3dsutil

`3dsutil` is a dependency-free Python utility for wirelessly bootstrapping and managing a modded Nintendo 3DS from your computer.

The FTP workflow can download a direct HTTP(S) link, then automatically extract `.zip` and `.7z` archives while transferring files. Point `3dsutil` at a lawful direct download, local archive, or directory of archives; optionally filter for files such as `*.nds` or `*.gba`; and it uploads the matching files to the 3DS SD card. In an interactive terminal, archive transfers prompt with a default yes; in scripts, pass `--unarchive`.

It is useful both on a newly modded 3DS and as a daily file-management tool. Use NetLoader first when the console does not yet have base apps such as ftpd, FBI, or Universal-Updater, then use the FTP explorer and upload commands for regular transfers after ftpd is installed.

## Highlights

- Interactive TUI: run `3dsutil` with no arguments to choose NetLoader, FTP browsing, direct-link downloads, update, or quit from one terminal interface.
- NetLoader bootstrap: discover a 3DS running Homebrew Launcher NetLoader over UDP, upload one `.3dsx`, and launch setup apps without removing the SD card.
- FTP file explorer: browse local files and the 3DS SD card side by side through ftpd or another 3DS FTP server.
- FTP transfers: upload files, directories, direct HTTP(S) links, or multiple sources in one command, with progress output and a summary.
- Archive-assisted ROM copying: extract `.zip` and `.7z` archives before upload, including archives found inside directory sources.
- Pattern filtering: upload only matching extracted or source files, such as `*.nds`, `*.gba`, or `*.cia`.
- File operations in the explorer: copy across panes, move within a pane, delete selected files or directories, and cancel active transfers.
- Collision handling: skip remote files that already exist with the same size, and upload with a unique name when a remote file exists with a different size.
- Status checks: test NetLoader or FTP reachability before starting a transfer.
- Self-management: install, update, or uninstall the `3dsutil` launcher from the CLI.

## Requirements

- Python 3
- A modded 3DS on the same network as the computer
- Homebrew Launcher NetLoader for `netloader`
- ftpd or another 3DS FTP server for `ftp`
- `7z` or `7zz` in `PATH` for `.7z` extraction

`.zip` extraction uses the Python standard library. If `.7z` support is needed and `7z`/`7zz` is missing, `3dsutil` can prompt to install the package with a supported package manager when running interactively.

## Installation

Install the latest version of the `3dsutil` command with:

```bash
curl -fsSL https://raw.githubusercontent.com/spkskx/3dsutil/main/install.sh | sh
```

Install a specific tagged version with:

```bash
curl -fsSL https://raw.githubusercontent.com/spkskx/3dsutil/main/install.sh | INSTALL_REF=1.4 sh
```

The installer checks for Python 3 and git. If either is missing, it asks before running a package-manager install command for `apt-get`, `dnf`, `pacman`, or Homebrew.

By default it installs the source into `~/.local/lib/3dsutil.py` and writes a launcher to `~/.local/bin/3dsutil`. Make sure `~/.local/bin` is in your `PATH`.

After installation:

```bash
3dsutil --help
3dsutil
3dsutil netloader --help
3dsutil ftp --help
3dsutil install --help
3dsutil update --help
3dsutil uninstall --help
```

Once Python and git are available, the CLI can manage itself:

```bash
3dsutil install
3dsutil install --ref 1.4
3dsutil update
3dsutil update --ref 1.4
3dsutil uninstall
```

## Quick Start

Start the interactive TUI:

```bash
3dsutil
```

Load a `.3dsx` through Homebrew Launcher NetLoader:

```bash
3dsutil netloader sample-app.3dsx
3dsutil netloader --host 172.20.10.12 sample-app.3dsx
3dsutil netloader status --host 172.20.10.12
```

Open the FTP explorer:

```bash
3dsutil ftp --host 172.20.10.12
3dsutil ftp explorer --host 172.20.10.12 --source . --dest /3ds/
```

Upload files through FTP:

```bash
3dsutil ftp upload --host 172.20.10.12 --source sample-app.3dsx --dest /3ds/
3dsutil ftp upload --host 172.20.10.12 --source first.nds --source second.gba --dest /roms/
3dsutil ftp upload --host 172.20.10.12 --source roms.zip --dest /roms/ --unarchive --patterns "*.nds"
3dsutil ftp upload --host 172.20.10.12 --source ./archives --dest /roms/ --unarchive --patterns "*.nds" --patterns "*.gba"
3dsutil ftp fetch --host 172.20.10.12 --url https://example.org/homebrew.zip --dest /roms/nds/ --unarchive --patterns "*.nds"
3dsutil ftp status --host 172.20.10.12
```

If `netloader status` succeeds, restart NetLoader before loading a file: press `B`, then press `Y` in the Homebrew Launcher.

## Command Guide

The built-in help is the source of truth for options:

```bash
3dsutil --help
3dsutil netloader --help
3dsutil netloader load --help
3dsutil netloader status --help
3dsutil ftp --help
3dsutil ftp explorer --help
3dsutil ftp upload --help
3dsutil ftp fetch --help
3dsutil ftp status --help
```

### Interactive TUI

Run `3dsutil` with no arguments to open the terminal UI.

- Choose `NetLoader - load one .3dsx`, then scan the network or enter a custom address.
- For scanning, open Homebrew Launcher on the 3DS and press `Y` before starting the scan.
- Scanning uses UDP discovery only. The TUI does not open a NetLoader TCP connection until after you choose a `.3dsx` file to load.
- For a custom address, enter a host such as `172.20.10.12`, or `host:port` such as `172.20.10.12:17491`.
- After selecting a NetLoader target, browse the current directory and choose one `.3dsx` file to upload and launch.
- Choose `FTP - browse and transfer files`, start ftpd on the 3DS, then enter the FTP host. You can enter `host:port`, such as `172.20.10.12:5000`, or enter only the host and provide the port when prompted.
- Choose `FTP - download link to 3DS` to download a direct HTTP(S) URL and upload it to a 3DS directory. It defaults to `/roms/nds/` and remembers the last successful destination in `${XDG_CONFIG_HOME:-~/.config}/3dsutil/config.json`.
- Choose `Update 3dsutil` from an installed `3dsutil` command to update the local checkout and launcher.
- Choose `Quit`, or press `q`/`Esc`, to leave the TUI.

### NetLoader

NetLoader is best for the first wireless setup step. On the 3DS, open Homebrew Launcher and press `Y`, then run:

```bash
3dsutil netloader load Universal-Updater.3dsx
```

If discovery does not find the console, pass the address shown on the 3DS:

```bash
3dsutil netloader load --host 172.20.10.12 Universal-Updater.3dsx
```

For compatibility with older usage, `3dsutil netloader sample-app.3dsx` and `3dsutil sample-app.3dsx` also load a `.3dsx`.

### FTP Explorer

The FTP explorer opens local files on the left and the 3DS FTP server on the right. Use `--source` to choose the local starting directory and `--dest` to choose the initial 3DS directory. The local pane cannot browse above its starting directory, while the 3DS pane can still browse up to the console's FTP root.

- Move the cursor with `Up`/`Down` or `j`/`k`.
- Switch panes with `Left`/`Right` or `h`/`l`.
- Open a directory with `Enter`; go up with `Backspace`.
- Mark files or directories with `Space`. Hold Shift while moving with `Up`/`Down` or use `J`/`K` to range-mark as the cursor moves.
- Starting marks on one pane clears marks from the other pane, so copy, move, and delete targets always come from one side.
- Press `u` to unmark everything.
- Press `p` to paste. Pasting within the same pane moves; pasting across panes copies.
- Press `d` to delete. The confirmation dialog lists every file or directory that will be deleted.
- During a transfer, press `c`, `q`, or `Esc` to cancel.

When copying from local to 3DS, archives can be extracted before upload. When moving within one pane, the explorer prevents moving a directory into itself or into one of its children.

### FTP Download, Upload, And Archives

`ftp fetch` downloads one direct HTTP(S) URL to a temporary file, then uploads it through FTP. The URL must have a filename; pass `--name` if it does not. Use only content you are authorized to download.

```bash
3dsutil ftp fetch --host 172.20.10.12 --url https://example.org/homebrew.nds --dest /roms/nds/
3dsutil ftp fetch --host 172.20.10.12 --url https://example.org/homebrew.zip --dest /roms/nds/ --unarchive --patterns "*.nds"
```

`ftp upload` accepts one or more `--source` values. A source can be a file or directory. Directories are walked recursively.

Archive handling is designed for ROM sets and other bulk transfers:

- Pass `--unarchive` to extract `.zip` or `.7z` sources into a temporary directory before upload.
- If a directory source contains supported archives, `--unarchive` extracts all supported archives found inside that directory tree.
- In an interactive terminal, if archives are found and `--unarchive` is omitted, `3dsutil` asks whether to extract them before upload. The default answer is yes.
- If that interactive prompt is declined, archive files are skipped and the remaining non-archive files are uploaded.
- In non-interactive use, archives are uploaded as normal files unless `--unarchive` is provided.
- Use `--patterns` to upload only files matching shell-style patterns after extraction or during normal recursive upload.

Examples:

```bash
3dsutil ftp upload --host 172.20.10.12 --source roms.zip --dest /roms/ --unarchive
3dsutil ftp upload --host 172.20.10.12 --source roms.7z --dest /roms/ --unarchive --patterns "*.nds"
3dsutil ftp upload --host 172.20.10.12 --source ./incoming --dest /roms/ --unarchive --patterns "*.gba"
```

## Troubleshooting

- If discovery fails, pass `--host <3DS_IP>`.
- If NetLoader rejects a file, confirm it is a `.3dsx` file.
- If `netloader status` succeeds but a later load fails, restart NetLoader by pressing `B`, then `Y` in Homebrew Launcher.
- FTP commands prompt for a host in interactive terminals. In scripts, pass `--host <3DS_IP>`.
- If FTP upload paths look wrong, check `3dsutil ftp upload --help` or `3dsutil ftp fetch --help` and whether `--dest` points to a file path or a directory path ending in `/`.
- `.7z` extraction requires a `7z` or `7zz` command in `PATH`.
- If ftpd is unreachable, confirm the 3DS and computer are on the same network and that ftpd is currently open on the console.

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

### 1.4

- Added `ftp fetch` for downloading a direct HTTP(S) link and transferring it to the 3DS, including existing archive extraction and filtering options.
- Added a TUI flow for direct-link transfers, with archive choices and a remembered 3DS destination.

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
