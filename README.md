# 3dslink.py

`3dslink.py` is a small, dependency-free Python utility for moving homebrew files from your computer to a modded 3DS over Wi-Fi.

It supports:

- NetLoader uploads for launching one `.3dsx` from the Homebrew Launcher.
- FTP browsing and uploads for a 3DS FTP server such as ftpd.

## Requirements

- Python 3
- a modded 3DS on the same network as the computer
- Homebrew Launcher NetLoader for `netloader`
- ftpd or another 3DS FTP server for `ftp`

## Usage

Start with the built-in help. It is the source of truth for commands and options:

```bash
python3 3dslink.py --help
python3 3dslink.py netloader --help
python3 3dslink.py netloader load --help
python3 3dslink.py netloader status --help
python3 3dslink.py ftp --help
python3 3dslink.py ftp explorer --help
python3 3dslink.py ftp upload --help
python3 3dslink.py ftp status --help
```

Common commands:

```bash
python3 3dslink.py netloader sample-app.3dsx
python3 3dslink.py netloader --host 172.20.10.12 sample-app.3dsx
python3 3dslink.py netloader status --host 172.20.10.12

python3 3dslink.py ftp --host 172.20.10.12
python3 3dslink.py ftp upload --host 172.20.10.12 --source sample-app.3dsx --dest /3ds/
python3 3dslink.py ftp upload --host 172.20.10.12 --source first.nds --source second.gba --dest /roms/
python3 3dslink.py ftp upload --host 172.20.10.12 --source roms.zip --dest /roms/ --unarchive --patterns "*.nds"
python3 3dslink.py ftp status --host 172.20.10.12
```

If `netloader status` succeeds, restart NetLoader before loading a file: press `B`, then press `Y` in the Homebrew Launcher.

## Troubleshooting

- If discovery fails, pass `--host <3DS_IP>`.
- If NetLoader rejects a file, confirm it is a `.3dsx` file.
- If FTP upload paths look wrong, check `python3 3dslink.py ftp upload --help`.
- `.7z` extraction requires a `7z` or `7zz` command in `PATH`.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## License

MIT. See `LICENSE`.
