import argparse
import os
import socket
import struct
import sys
import zlib

DEFAULT_PORT = 17491
DEFAULT_TIMEOUT = 30.0
DEFAULT_DISCOVERY_RETRIES = 10
DISCOVERY_REQUEST = b"3dsboot"
DISCOVERY_RESPONSE_PREFIX = b"boot3ds"
ZLIB_CHUNK = 16 * 1024

NETLOADER_ERRORS = {
    -1: "failed to create file on the 3DS",
    -2: "insufficient free space on the 3DS",
    -3: "insufficient memory on the 3DS",
}


class NetloaderError(Exception):
    pass


class DiscoveryError(NetloaderError):
    pass


def send_int32_le(sock, value):
    sock.sendall(struct.pack('<I', value))


def recv_exact(sock, size):
    chunks = []
    remaining = size

    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise NetloaderError("the 3DS closed the connection before the transfer finished")
        chunks.append(chunk)
        remaining -= len(chunk)

    return b''.join(chunks)


def recv_int32_le(sock):
    return struct.unpack('<i', recv_exact(sock, 4))[0]


def describe_response(response):
    return NETLOADER_ERRORS.get(response, f"unexpected NetLoader response {response}")


def yield_chunked(data, chunk_size):
    for start in range(0, len(data), chunk_size):
        yield data[start:start + chunk_size]


def iter_compressed_chunks(file_obj):
    compressor = zlib.compressobj()

    while True:
        block = file_obj.read(ZLIB_CHUNK)
        if not block:
            flushed = compressor.flush()
            if flushed:
                yield from yield_chunked(flushed, ZLIB_CHUNK)
            return

        compressed = compressor.compress(block)
        if compressed:
            yield from yield_chunked(compressed, ZLIB_CHUNK)


def build_command_buffer(remote_path):
    return remote_path.encode('utf-8') + b'\0'


def discover_3ds(port, retries, attempt_interval):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recv_sock:
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            recv_sock.bind(('', port))
            recv_sock.settimeout(attempt_interval)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as send_sock:
                send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                send_sock.settimeout(attempt_interval)

                for _ in range(retries):
                    send_sock.sendto(DISCOVERY_REQUEST, ('255.255.255.255', port))

                    while True:
                        try:
                            payload, remote = recv_sock.recvfrom(256)
                        except socket.timeout:
                            break

                        if payload.startswith(DISCOVERY_RESPONSE_PREFIX):
                            return remote[0]
    except OSError as exc:
        raise DiscoveryError(f"failed to use UDP discovery on port {port}: {exc}") from exc

    raise DiscoveryError(
        "no 3DS replied to discovery. Make sure NetLoader is open, both devices are on the same network, and the port is allowed"
    )


def resolve_host(host, port):
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetloaderError(f"could not resolve '{host}': {exc}") from exc

    return info[0][4][0]


def validate_input_file(path):
    if not os.path.exists(path):
        raise NetloaderError(f"file not found: {path}")
    if not os.path.isfile(path):
        raise NetloaderError(f"path is not a file: {path}")
    if os.path.getsize(path) == 0:
        raise NetloaderError(f"file is empty: {path}")


def send_3dsx(host, port, path):
    validate_input_file(path)

    basename = os.path.basename(path)
    filename = basename.encode('utf-8')
    file_size = os.path.getsize(path)
    remote_path = f"sdmc:/3ds/{basename}"
    command_buffer = build_command_buffer(remote_path)

    try:
        with socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT) as sock:
            sock.settimeout(DEFAULT_TIMEOUT)

            print(f"Connected to {host}:{port}")
            print(f"Sending metadata for {basename} (name={len(filename)} bytes, file={file_size} bytes)...")
            send_int32_le(sock, len(filename))
            sock.sendall(filename)
            send_int32_le(sock, file_size)

            response = recv_int32_le(sock)
            if response != 0:
                raise NetloaderError(f"the 3DS rejected the upload: {describe_response(response)}")

            print("Uploading compressed file data...")
            blocks_sent = 0
            bytes_sent = 0

            with open(path, 'rb') as file_obj:
                for chunk in iter_compressed_chunks(file_obj):
                    send_int32_le(sock, len(chunk))
                    sock.sendall(chunk)
                    blocks_sent += 1
                    bytes_sent += len(chunk)

            print(f"Compressed transfer sent {bytes_sent} bytes in {blocks_sent} blocks")

            response = recv_int32_le(sock)
            if response != 0:
                raise NetloaderError(f"the 3DS reported an upload failure: {describe_response(response)}")

            print(f"Sending launch command for {remote_path} ({len(command_buffer)} bytes)...")
            send_int32_le(sock, len(command_buffer))
            sock.sendall(command_buffer)

            sock.shutdown(socket.SHUT_WR)
    except socket.timeout as exc:
        raise NetloaderError(
            f"timed out while talking to {host}:{port}. Make sure NetLoader is open and the 3DS stays on the same Wi-Fi"
        ) from exc
    except OSError as exc:
        raise NetloaderError(f"network error while talking to {host}:{port}: {exc}") from exc


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Transfer a .3dsx file to a modded 3DS over 3dslink NetLoader.",
    )
    parser.add_argument("file", help="Path to the .3dsx file to upload")
    parser.add_argument(
        "--host",
        help="3DS hostname or IPv4 address. If omitted, the utility tries UDP discovery on the local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"NetLoader port for both discovery and transfer. Default: {DEFAULT_PORT}",
    )

    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    return args


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    try:
        host = args.host
        if host is None:
            print(f"Discovering a 3DS on the local network via UDP broadcast on port {args.port}...")
            host = discover_3ds(args.port, DEFAULT_DISCOVERY_RETRIES, 1.0)
            print(f"Discovered 3DS at {host}")
        else:
            host = resolve_host(host, args.port)

        send_3dsx(
            host=host,
            port=args.port,
            path=args.file,
        )
        print("Transfer complete. Check your 3DS screen.")
        return 0
    except NetloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: transfer cancelled by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())