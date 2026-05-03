# 3dsutil

`3dsutil` is a small, dependency-free Python utility for wirelessly moving homebrew files from your computer to a modded 3DS on the same network.

It is especially useful when a newly modded 3DS does not yet have core apps such as ftpd, Universal-Updater, or FBI, and you cannot easily copy files with an SD card reader. Connect the 3DS to your network, wirelessly upload and launch Universal-Updater with NetLoader, then finish the rest of the setup directly on the console.

It supports:

- NetLoader uploads for launching one `.3dsx` from the Homebrew Launcher.
- Side-by-side local and 3DS FTP browsing, uploads, downloads, copies, and moves for a 3DS FTP server such as ftpd.
- FTP uploads can extract `.zip` and `.7z` archives before transfer, which is useful because ROMs are often distributed inside archives.

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
curl -fsSL https://raw.githubusercontent.com/spkskx/3dsutil/main/install.sh | INSTALL_REF=1.2 sh
```

The installer checks for Python 3 and git. If either is missing, it asks before running a package-manager install command for `apt-get`, `dnf`, `pacman`, or Homebrew.

By default it installs the source into `~/.local/lib/3dsutil.py` and writes a launcher to `~/.local/bin/3dsutil`. Make sure `~/.local/bin` is in your `PATH`.

After installation:

```bash
3dsutil --help
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
3dsutil install --ref 1.2
3dsutil update
3dsutil update --ref 1.2
3dsutil uninstall
```

## Usage

Start with the built-in help. It is the source of truth for commands and options:

```bash
3dsutil --help
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

## Testing

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## License

MIT. See `LICENSE`.
