# 3dslink.py

`3dslink.py` is a small Python utility for wirelessly sending a `.3dsx` homebrew app from your computer to a modded 3DS over the 3dslink NetLoader protocol.

It is especially useful if you already have a working wireless connection but do not have access to an SD card reader, or do not want to manage your setup by moving files through the SD card manually. In that case, you can use this tool to start getting homebrew apps onto a modded 3DS wirelessly during the initial setup stage.

It is useful when:

- your 3DS can enter the Homebrew Launcher and open the 3dslink NetLoader screen by pressing `Y`
- your computer and 3DS are on the same local network
- you want to launch a `.3dsx` without manually copying it to the SD card first

The script speaks the same NetLoader wire protocol expected by `3dslink`: it sends the filename and file size in the correct order, uploads compressed file chunks, and sends the launch command buffer so the app starts immediately on the 3DS.

## Requirements

- Python 3
- a modded 3DS that can open the Homebrew Launcher and enter the 3dslink NetLoader screen by pressing `Y`
- the computer and 3DS connected to the same network

No third-party Python packages are required.

## What It Does

- uploads a `.3dsx` file over TCP using the 3dslink NetLoader protocol
- can auto-discover the 3DS over UDP broadcast on the local network
- supports a custom NetLoader port with `17491` as the default
- prints clearer connection and transfer errors than the original one-off script

## Basic Usage

Transfer a file to a known 3DS IP:

```bash
python3 3dslink.py --host 172.20.10.12 sample-app.3dsx
```

Use a custom NetLoader port:

```bash
python3 3dslink.py --host 172.20.10.12 --port 17491 Universal-Updater.3dsx
```

Let the script discover the 3DS automatically on the same network:

```bash
python3 3dslink.py sample-app.3dsx
```

Show help:

```bash
python3 3dslink.py --help
```

## CLI Reference

- `file`: path to the `.3dsx` file to upload
- `--host`: 3DS hostname or IPv4 address. If omitted, UDP discovery is used.
- `--port`: NetLoader port for both discovery and transfer. Default: `17491`.

## Typical Workflow

1. On the 3DS, open the Homebrew Launcher and press `Y` to enter the 3dslink NetLoader screen.
2. Make sure the 3DS and computer are on the same Wi-Fi or hotspot.
3. Run `python3 3dslink.py <file>` for auto-discovery, or provide `--host` if you already know the IP.
4. Wait for `Transfer complete. Check your 3DS screen.`

## Troubleshooting

If discovery fails:

- confirm the 3DS is on the 3dslink NetLoader screen opened from the Homebrew Launcher with `Y`
- make sure both devices are on the same subnet
- try `--host <3DS_IP>` directly if broadcast traffic is blocked
- confirm the port matches the NetLoader port on the 3DS

If the transfer times out:

- keep the 3DS awake on the NetLoader screen during the upload
- verify the IP and port are correct

If NetLoader rejects the upload:

- check that the file is a valid `.3dsx`
- try a smaller known-good homebrew app such as `sample-app.3dsx`
- verify there is enough free space and memory on the 3DS side

## License

This project is licensed under the MIT License. See `LICENSE` for the full text.
