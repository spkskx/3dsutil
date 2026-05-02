# 3dslink.py

`3dslink.py` is a small Python script for sending `.3dsx` homebrew apps to a modded 3DS over the 3dslink NetLoader protocol or an FTP server such as ftpd.

It is mainly for people who want a simple wireless workflow: no SD card reader, no need to copy files through the SD card manually, and no extra dependencies. It is especially useful for getting initial homebrew apps onto a modded 3DS when the console already has network access.

## Requirements

- Python 3
- a modded 3DS that can open the Homebrew Launcher and enter the 3dslink NetLoader screen with `Y`
- the computer and 3DS on the same network

## What It Does

- uploads a `.3dsx` file over 3dslink NetLoader
- uploads files over FTP
- auto-discovers the 3DS over UDP if `--host` is not provided
- supports custom NetLoader and FTP ports, default `17491` for NetLoader and `5000` for FTP
- keeps the interface small while making transfer mode explicit with `netloader` and `ftp`

## Usage

NetLoader with known 3DS IP:

```bash
python3 3dslink.py netloader load --host 172.20.10.12 sample-app.3dsx
```

NetLoader auto-discovery on the local network:

```bash
python3 3dslink.py netloader load sample-app.3dsx
```

NetLoader connection status:

```bash
python3 3dslink.py netloader status --host 172.20.10.12
```

FTP upload to ftpd:

```bash
python3 3dslink.py ftp upload --host 192.168.0.10 sample-app.3dsx
```

FTP connection status:

```bash
python3 3dslink.py ftp status --host 192.168.0.10
```

FTP upload with credentials, custom port, and remote path:

```bash
python3 3dslink.py ftp upload --host 192.168.0.10 --port 5000 --user user --password pass --remote /3ds/sample-app.3dsx sample-app.3dsx
```

The old flat NetLoader form still works for now, but prints a deprecation warning:

```bash
python3 3dslink.py sample-app.3dsx
```

The default status command checks NetLoader status:

```bash
python3 3dslink.py status --host 172.20.10.12
```

Show help:

```bash
python3 3dslink.py --help
```

## Command Summary

- `python3 3dslink.py netloader load [--host HOST] [--port PORT] FILE`: load and launch a `.3dsx` file through NetLoader.
- `python3 3dslink.py netloader [--host HOST] [--port PORT] FILE`: same as `netloader load`.
- `python3 3dslink.py netloader status [--host HOST] [--port PORT]`: check NetLoader connectivity.
- `python3 3dslink.py ftp upload [--host HOST] [--port PORT] [--user USER] [--password PASSWORD] [--remote PATH] FILE`: upload a file through FTP.
- `python3 3dslink.py ftp [--host HOST] [--port PORT] [--user USER] [--password PASSWORD] [--remote PATH] FILE`: same as `ftp upload`.
- `python3 3dslink.py ftp status [--host HOST] [--port PORT] [--user USER] [--password PASSWORD]`: check FTP connectivity and login.
- `python3 3dslink.py FILE`: legacy NetLoader load form, still supported with a deprecation warning.
- `python3 3dslink.py status [--host HOST] [--port PORT]`: default NetLoader status check.

## NetLoader Parameters

- `netloader`: upload and launch through 3dslink NetLoader.
- `load`: optional NetLoader action that uploads and launches the `.3dsx` file. Default: selected when omitted.
- `file`: required path to the `.3dsx` file to send. NetLoader only accepts files with a `.3dsx` extension or a valid `.3dsx` header.
- `--host`: optional 3DS IP address or hostname. Default: omitted, which makes the tool try UDP auto-discovery on the local network.
- `--port`: optional NetLoader port. Default: `17491`.
- `status`: optional NetLoader action that checks discovery, name resolution, and TCP connectivity without uploading.
- `-h`, `--help`: show the built-in help text and exit.

## FTP Parameters

- `ftp`: upload through FTP without launching the app.
- `upload`: optional FTP action that uploads a file. Default: selected when omitted.
- `file`: required path to the file to upload.
- `--host`: optional FTP IP address or hostname. Default: omitted, which makes the tool try mDNS FTP discovery and then prompt in an interactive terminal.
- `--port`: optional FTP port. Default: `5000`.
- `--user`: optional FTP username. Default: `anonymous`.
- `--password`: optional FTP password. Default: empty.
- `--remote`: optional remote destination file path. Default: `/<local basename>`.
- `status`: optional FTP action that checks discovery, name resolution, connection, and login without uploading.

## Workflow

1. On the 3DS, open the Homebrew Launcher and press `Y` to enter the 3dslink NetLoader screen.
2. Make sure the 3DS and computer are on the same network.
3. Check the 3dslink NetLoader screen for the displayed IP address and port.
4. Run `python3 3dslink.py netloader load <file>` for auto-discovery, or pass the displayed values directly with `--host <IP>` and `--port <PORT>`.
5. Wait for the transfer to finish, then check the 3DS screen.

For FTP, open ftpd on the 3DS, then run `python3 3dslink.py ftp upload --host <IP> <file>`. If `--host` is omitted, the tool tries mDNS discovery for FTP services and prompts for `host` or `host:port` when running interactively.

## Troubleshooting

- If discovery fails, make sure the 3DS is on the NetLoader screen and both devices are on the same subnet.
- If broadcast traffic is blocked, use `--host <3DS_IP>` directly.
- If the transfer times out, keep the 3DS awake and verify the IP and port.
- If NetLoader rejects the upload, confirm the file is a valid `.3dsx` and that the 3DS has enough space and memory.
- If the NetLoader command rejects the local file before connecting, rename it with a `.3dsx` extension or verify that it is actually a `.3dsx` binary.
- If FTP discovery fails in a script or CI job, pass `ftp --host <3DS_IP>` directly.
- If an FTP upload fails with a missing directory error, create the remote directory in ftpd first or choose an existing path with `--remote`.

## Testing

This project includes unit tests and a GitHub Actions CI workflow.

Run tests locally:

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## Changelog

### 2026-05-02

- Added explicit `netloader` and `ftp` command prefixes.
- Added explicit `netloader load` and `ftp upload` actions while keeping those actions as each mode's default.
- Added `status` checks for NetLoader, FTP, and the default NetLoader command path.
- Added FTP upload support through Python's standard-library `ftplib`, defaulting to port `5000`, passive mode, and anonymous login.
- Restricted NetLoader transfers to `.3dsx` files or files with a `.3dsx` header.
- Kept the old flat NetLoader CLI form temporarily with a deprecation warning.

### 2026-04-30

- Added the initial NetLoader transfer feature for sending `.3dsx` homebrew apps to a modded 3DS.
- Added UDP auto-discovery when `--host` is omitted.
- Added configurable NetLoader port support with default port `17491`.
- Added unit tests and GitHub Actions CI for Python 3.11, 3.12, and 3.13.

## License

MIT. See `LICENSE`.
