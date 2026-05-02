# 3dslink.py

`3dslink.py` is a small, dependency-free Python utility for moving homebrew files from your computer to a modded 3DS over Wi-Fi.

It supports two main workflows:

- **Load and launch `.3dsx` apps through 3dslink NetLoader.** Use this when the Homebrew Launcher is open and you want to run one `.3dsx` immediately.
- **Browse or transfer files through a 3DS FTP server such as ftpd.** Use `ftp` for an interactive remote file explorer, or `ftp upload` when you want to copy one file, rename it during upload, or sync the contents of a local directory to the SD card.

The tool can discover NetLoader with UDP broadcast, discover FTP services with best-effort mDNS, prompt for an address in interactive terminals, or connect directly when you pass `--host`.

## Requirements

- Python 3
- a modded 3DS on the same network as the computer
- Homebrew Launcher NetLoader for `netloader load`
- ftpd or another FTP server on the 3DS for `ftp upload`

## CLI Reference

| Task | Command | Description | Example |
| --- | --- | --- | --- |
| Load `.3dsx` with NetLoader | `netloader load [--host HOST] [--port PORT] FILE` | Uploads and launches one `.3dsx` file. `load` is the default NetLoader action. | `python3 3dslink.py netloader load --host 172.20.10.12 sample-app.3dsx` |
| Load `.3dsx` with NetLoader shorthand | `netloader [--host HOST] [--port PORT] FILE` | Same as `netloader load`. | `python3 3dslink.py netloader sample-app.3dsx` |
| Legacy NetLoader load | `FILE` | Old flat form. Still works, but prints a deprecation warning. | `python3 3dslink.py sample-app.3dsx` |
| Check NetLoader reachability | `netloader status [--host HOST] [--port PORT]` | Checks discovery, resolution, and TCP reachability. Restart NetLoader afterward before loading. | `python3 3dslink.py netloader status --host 172.20.10.12` |
| Default status check | `status [--host HOST] [--port PORT]` | Same as `netloader status`. | `python3 3dslink.py status --host 172.20.10.12` |
| Browse with FTP explorer | `ftp [--host HOST] [--port PORT] [--user USER] [--password PASSWORD]` | Opens a terminal file explorer for the FTP server. `explorer` is the default FTP action. | `python3 3dslink.py ftp --host 192.168.0.10` |
| Browse with explicit FTP explorer | `ftp explorer [--host HOST] [--port PORT] [--user USER] [--password PASSWORD]` | Same as `ftp`. | `python3 3dslink.py ftp explorer --host 192.168.0.10` |
| Upload with FTP | `ftp upload [--host HOST] [--port PORT] [--user USER] [--password PASSWORD] [--unarchive] [--patterns PATTERN] --source PATH [--source PATH ...] --dest PATH` | Uploads one or more files, directories, or archive sources. | `python3 3dslink.py ftp upload --host 192.168.0.10 --source ./3ds --dest /3ds/` |
| Check FTP reachability | `ftp status [--host HOST] [--port PORT] [--user USER] [--password PASSWORD]` | Connects to FTP and verifies login. | `python3 3dslink.py ftp status --host 192.168.0.10` |
| Show help | `--help` | Shows top-level CLI help. Use subcommand help for action-specific options. | `python3 3dslink.py ftp upload --help` |

## NetLoader

NetLoader is for loading `.3dsx` apps only. The tool accepts a NetLoader source when the path ends in `.3dsx` or the file starts with the `.3dsx` header magic.

| Option | Default | Description |
| --- | --- | --- |
| `FILE` | required | Local `.3dsx` file to load and launch. |
| `--host HOST` | auto-discovery or prompt | 3DS hostname or IP address. If omitted, the tool sends UDP NetLoader discovery packets. If discovery fails in an interactive terminal, it prompts for `host` or `host:port`. |
| `--port PORT` | `17491` | NetLoader discovery and transfer port. |

Typical workflow:

1. Open the Homebrew Launcher on the 3DS.
2. Press `Y` to enter the 3dslink NetLoader screen.
3. Run `python3 3dslink.py netloader load <file>`, or pass `--host <IP>` if broadcast discovery is blocked. In an interactive terminal, failed discovery prompts for `host` or `host:port`.

`netloader status` is intentionally limited to a TCP reachability check. NetLoader expects real `.3dsx` load traffic, so a status check can leave the 3DS unable to accept the next load. After running `netloader status`, restart NetLoader before loading a file: press `B` to go back, then press `Y` in the Homebrew Launcher.

## FTP

FTP mode is for browsing and transferring files on a 3DS FTP server such as ftpd. It does not launch apps.

| Option | Default | Description |
| --- | --- | --- |
| `--source PATH` | required | Local file or directory to upload. Repeat `--source` to upload multiple paths in one command. |
| `--dest PATH` | required | Remote destination file or directory path. |
| `--host HOST` | mDNS discovery or prompt | 3DS FTP hostname or IP address. |
| `--port PORT` | `5000` | FTP port. |
| `--user USER` | `anonymous` | FTP username. |
| `--password PASSWORD` | empty | FTP password. |
| `--unarchive` | prompt when interactive | Extract `.zip` or `.7z` archives into one unique temporary directory, then upload the extracted files into `--dest`. If omitted and archives are found in an interactive terminal, the prompt defaults to yes. Answering no ignores archive files and uploads the remaining files that match `--patterns`. `.zip` uses Python's standard library; `.7z` requires a `7z` or `7zz` command in `PATH`. |
| `--patterns PATTERN` | none | Upload only files matching a shell-style pattern, such as `*.nds` or `*.gba`. Repeat `--patterns` for multiple patterns. Filters apply after unarchiving. |

FTP explorer controls:

| Key | Action |
| --- | --- |
| `Up` / `Down` or `j` / `k` | Move the highlighted file or directory and update the metadata panel. Normal movement clears temporary multi-selection. |
| `Shift+Up` / `Shift+Down` or `J` / `K` | Extend multi-selection while moving. |
| `Space` | Toggle multi-select for the highlighted file or directory. |
| `Enter` on a directory | Open the selected directory. |
| `Enter` on `..` | Go up to the parent directory. |
| `Backspace` | Go up to the parent directory without highlighting `..`. |
| `m` | Stage the selected item, or all multi-selected items, for moving. |
| `p` | Paste staged move items into the current directory after a centered y/n confirmation popup. |
| `P` | Paste staged move items into the highlighted directory after a centered y/n confirmation popup, or into the current directory when a file is highlighted. |
| `c` / `Esc` | Cancel the staged move. |
| `d` | Delete the selected item, or all multi-selected items, after a centered y/n confirmation popup. Directories are deleted recursively. |
| `q` | Quit the explorer. |

The right side of the explorer shows metadata for the selected entry, including name, type, remote path, size, and modified time when the FTP server reports it. Directory size is shown as `n/a` because FTP servers do not provide a reliable whole-directory size without recursively scanning the tree. The footer always shows whether a move is staged and how to paste or cancel it.

Destination behavior:

| Source | Destination | Result |
| --- | --- | --- |
| file | remote directory, such as `/3ds/` | Uploads as `/3ds/<local basename>`. |
| file | remote file, such as `/3ds/app.3dsx` | Uploads to that exact path, allowing rename. |
| directory | remote directory, such as `/3ds/` | Uploads everything inside the local directory into the remote directory, preserving relative paths. |
| multiple `--source` values | remote directory, such as `/3ds/` | Uploads each source into that destination directory. |

FTP upload behavior:

| Condition | Behavior |
| --- | --- |
| Destination directory is missing | Creates it before uploading. |
| Remote file exists with the same size | Skips the upload. |
| Remote file exists with a different size | Uploads with `_1`, `_2`, etc. before the extension. |
| Upload is active | Prints progress during transfer. |
| Upload finishes | Prints a summary of uploaded, skipped, and renamed uploads. |
| `--unarchive` is set on an archive file | Extracts that archive into one unique temporary directory, then uploads the extracted contents into the destination root. |
| `--unarchive` is set on a directory | Recursively extracts every `.zip` and `.7z` in the directory into one unique temporary directory, then uploads the extracted contents into the destination root. |
| Archives are found and `--unarchive` is omitted in an interactive terminal | Prompts whether to extract archives. The default `y` extracts them; `n` ignores archive files and uploads the rest. |
| `--patterns` is set | Filters source files, or extracted files when combined with `--unarchive`. |

Examples:

```bash
python3 3dslink.py ftp upload --host 192.168.0.10 --source sample-app.3dsx --dest /3ds/
python3 3dslink.py ftp upload --host 192.168.0.10 --source sample-app.3dsx --dest /3ds/renamed.3dsx
python3 3dslink.py ftp upload --host 192.168.0.10 --source first.nds --source second.gba --dest /roms/
python3 3dslink.py ftp upload --host 192.168.0.10 --source ./3ds --dest /3ds/
python3 3dslink.py ftp upload --host 192.168.0.10 --source roms.zip --dest /roms/ --unarchive --patterns "*.nds" --patterns "*.gba"
python3 3dslink.py ftp upload --host 192.168.0.10 --source ./archives --dest /roms/ --unarchive --patterns "*.nds"
```

## Troubleshooting

- If NetLoader discovery fails, make sure the 3DS is on the NetLoader screen and both devices are on the same subnet. In an interactive terminal, enter the displayed `host` or `host:port` when prompted.
- If broadcast traffic is blocked, pass `--host <3DS_IP>` directly.
- If NetLoader rejects the local file, confirm it is a `.3dsx` file or has a valid `.3dsx` header.
- If you ran `netloader status`, restart NetLoader before loading a file by pressing `B`, then `Y` in the Homebrew Launcher.
- If FTP discovery fails in a script or CI job, pass `ftp --host <3DS_IP>` directly.
- If an FTP directory upload puts files somewhere unexpected, confirm that `--dest` is the target directory. Directory sources always copy their contents into the destination directory.
- If `.7z` extraction fails, install a `7z` or `7zz` command and make sure it is available in `PATH`.

## Testing

Run the full local test suite:

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## Changelog

### 2026-05-02

- Added explicit `netloader` and `ftp` command prefixes.
- Added explicit `netloader load` and `ftp upload` actions while keeping those actions as each mode's default.
- Added `status` checks for NetLoader, FTP, and the default NetLoader command path.
- Added a NetLoader status warning because checking the TCP port can require restarting NetLoader before the next load.
- Added an interactive NetLoader prompt for `host` or `host:port` when UDP discovery fails.
- Added FTP upload support through Python's standard-library `ftplib`, defaulting to port `5000`, passive mode, and anonymous login.
- Changed the default `ftp` action to an interactive terminal file explorer and added explicit `ftp explorer`.
- Added FTP explorer multi-select, move, paste, and recursive delete actions with confirmation prompts.
- Refined FTP explorer navigation with `j`/`k`, shifted range selection, Backspace parent navigation, centered confirmation popups, and restored parent-directory selection when going back up.
- Reworked FTP uploads to use `--source` and `--dest`, support file and directory sources, create missing destination directories, skip same-size files, rename different-size conflicts, show progress, and print a summary.
- Added FTP `--unarchive` for `.zip` and `.7z` sources, including directory sources containing archives, and `--patterns` for shell-style upload filtering.
- Added an interactive FTP archive prompt that defaults to extracting archives and can ignore archives while uploading the rest.
- Added repeatable FTP `--source` for uploading multiple paths in one command.
- Restricted NetLoader transfers to `.3dsx` files or files with a `.3dsx` header.
- Kept the old flat NetLoader CLI form temporarily with a deprecation warning.
- Reorganized the README around NetLoader and FTP workflows with CLI reference tables.

### 2026-04-30

- Added the initial NetLoader transfer feature for sending `.3dsx` homebrew apps to a modded 3DS.
- Added UDP auto-discovery when `--host` is omitted.
- Added configurable NetLoader port support with default port `17491`.
- Added unit tests and GitHub Actions CI for Python 3.11, 3.12, and 3.13.

## License

MIT. See `LICENSE`.
