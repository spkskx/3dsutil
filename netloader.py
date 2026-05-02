import os
import socket
import struct
import sys
import zlib

from core import (
    DEFAULT_DISCOVERY_RETRIES, DEFAULT_TIMEOUT, DISCOVERY_REQUEST, DISCOVERY_RESPONSE_PREFIX,
    NETLOADER_ERRORS, THREEDSX_MAGIC, ZLIB_CHUNK, DiscoveryError, NetloaderError,
    parse_host_port, resolve_host, validate_input_file,
)

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


def file_has_3dsx_magic(path):
    with open(path, 'rb') as file_obj:
        return file_obj.read(len(THREEDSX_MAGIC)) == THREEDSX_MAGIC


def validate_netloader_file(path):
    validate_input_file(path)

    if path.lower().endswith(".3dsx") or file_has_3dsx_magic(path):
        return

    raise NetloaderError("NetLoader only supports .3dsx files")


def send_3dsx(host, port, path):
    validate_netloader_file(path)

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


def resolve_netloader_host(args, stdin=None):
    if stdin is None:
        stdin = sys.stdin

    host = args.host
    if host is None:
        print(f"Discovering a 3DS on the local network via UDP broadcast on port {args.port}...")
        try:
            host = discover_3ds(args.port, DEFAULT_DISCOVERY_RETRIES, 1.0)
            print(f"Discovered 3DS at {host}")
            return host, args.port
        except DiscoveryError:
            if not stdin.isatty():
                raise

            value = input("Enter 3DS NetLoader host or host:port: ").strip()
            if not value:
                raise DiscoveryError("NetLoader host is required")
            prompted_host, prompted_port = parse_host_port(value, args.port, "NetLoader host")
            return resolve_host(prompted_host, prompted_port), prompted_port

    resolved_host, resolved_port = parse_host_port(host, args.port, "NetLoader host")
    return resolve_host(resolved_host, resolved_port), resolved_port

