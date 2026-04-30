# 3dslink.py

`3dslink.py` is a small Python script for sending `.3dsx` homebrew apps to a modded 3DS over the 3dslink NetLoader protocol.

It is mainly for people who want a simple wireless workflow: no SD card reader, no need to copy files through the SD card manually, and no extra dependencies. It is especially useful for getting initial homebrew apps onto a modded 3DS when the console already has network access.

## Requirements

- Python 3
- a modded 3DS that can open the Homebrew Launcher and enter the 3dslink NetLoader screen with `Y`
- the computer and 3DS on the same network

## What It Does

- uploads a `.3dsx` file over 3dslink NetLoader
- auto-discovers the 3DS over UDP if `--host` is not provided
- supports a custom NetLoader port, default `17491`
- keeps the interface small: `file`, `--host`, and `--port`

## Usage

Known 3DS IP:

```bash
python3 3dslink.py --host 172.20.10.12 sample-app.3dsx
```

Auto-discovery on the local network:

```bash
python3 3dslink.py sample-app.3dsx
```

Custom port:

```bash
python3 3dslink.py --host 172.20.10.12 --port 17491 sample-app.3dsx
```

Show help:

```bash
python3 3dslink.py --help
```

## Parameters

- `file`: required path to the `.3dsx` file to send. Default: none.
- `--host`: optional 3DS IP address or hostname. Default: omitted, which makes the tool try UDP auto-discovery on the local network.
- `--port`: optional NetLoader port. Default: `17491`.
- `-h`, `--help`: show the built-in help text and exit.

## Workflow

1. On the 3DS, open the Homebrew Launcher and press `Y` to enter the 3dslink NetLoader screen.
2. Make sure the 3DS and computer are on the same network.
3. Check the 3dslink NetLoader screen for the displayed IP address and port.
4. Run `python3 3dslink.py <file>` for auto-discovery, or pass the displayed values directly with `--host <IP>` and `--port <PORT>`.
5. Wait for the transfer to finish, then check the 3DS screen.

## Troubleshooting

- If discovery fails, make sure the 3DS is on the NetLoader screen and both devices are on the same subnet.
- If broadcast traffic is blocked, use `--host <3DS_IP>` directly.
- If the transfer times out, keep the 3DS awake and verify the IP and port.
- If NetLoader rejects the upload, confirm the file is a valid `.3dsx` and that the 3DS has enough space and memory.

## Testing

This project includes unit tests and a GitHub Actions CI workflow.

Run tests locally:

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same test suite on push and pull request for Python 3.11, 3.12, and 3.13.

## License

MIT. See `LICENSE`.
