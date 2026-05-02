import argparse
import ftplib
import os
import socket
import struct
import sys
import zlib

NETLOADER_COMMAND = "netloader"
FTP_COMMAND = "ftp"
STATUS_ACTION = "status"
LOAD_ACTION = "load"
UPLOAD_ACTION = "upload"
DEFAULT_NETLOADER_PORT = 17491
DEFAULT_FTP_PORT = 5000
DEFAULT_PORT = DEFAULT_NETLOADER_PORT
DEFAULT_TIMEOUT = 30.0
DEFAULT_DISCOVERY_RETRIES = 10
DISCOVERY_REQUEST = b"3dsboot"
DISCOVERY_RESPONSE_PREFIX = b"boot3ds"
ZLIB_CHUNK = 16 * 1024
THREEDSX_MAGIC = b"3DSX"
MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
MDNS_FTP_QUERIES = ("_ftp._tcp.local", "_ftpd._tcp.local")
MDNS_DISCOVERY_TIMEOUT = 2.0
MDNS_QTYPE_PTR = 12
MDNS_QTYPE_SRV = 33
MDNS_QTYPE_A = 1
MDNS_QCLASS_IN = 1

NETLOADER_ERRORS = {
    -1: "failed to create file on the 3DS",
    -2: "insufficient free space on the 3DS",
    -3: "insufficient memory on the 3DS",
}


class NetloaderError(Exception):
    pass


class DiscoveryError(NetloaderError):
    pass


class FTPTransferError(NetloaderError):
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


def encode_dns_name(name):
    parts = name.rstrip('.').split('.')
    return b''.join(bytes([len(part)]) + part.encode('utf-8') for part in parts) + b'\0'


def decode_dns_name(payload, offset):
    labels = []
    jumped = False
    next_offset = offset

    while True:
        length = payload[offset]
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | payload[offset + 1]
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break

        offset += 1
        labels.append(payload[offset:offset + length].decode('utf-8', errors='replace'))
        offset += length

    return '.'.join(labels), next_offset


def build_mdns_query(service_name):
    header = struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0)
    question = encode_dns_name(service_name) + struct.pack('!HH', MDNS_QTYPE_PTR, MDNS_QCLASS_IN)
    return header + question


def parse_mdns_response(payload):
    if len(payload) < 12:
        return []

    _, _, question_count, answer_count, authority_count, additional_count = struct.unpack('!HHHHHH', payload[:12])
    offset = 12
    records = []

    try:
        for _ in range(question_count):
            _, offset = decode_dns_name(payload, offset)
            offset += 4

        for _ in range(answer_count + authority_count + additional_count):
            name, offset = decode_dns_name(payload, offset)
            record_type, _, _, data_length = struct.unpack('!HHIH', payload[offset:offset + 10])
            offset += 10
            data_offset = offset
            offset += data_length

            if record_type == MDNS_QTYPE_SRV:
                _, _, port = struct.unpack('!HHH', payload[data_offset:data_offset + 6])
                target, _ = decode_dns_name(payload, data_offset + 6)
                records.append(("srv", name, target, port))
            elif record_type == MDNS_QTYPE_A:
                if data_length == 4:
                    records.append(("a", name, socket.inet_ntoa(payload[data_offset:data_offset + 4])))
    except (IndexError, struct.error):
        return []

    return records


def discover_ftp_mdns(timeout=MDNS_DISCOVERY_TIMEOUT):
    srv_records = []
    addresses = {}

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(timeout)

            for service_name in MDNS_FTP_QUERIES:
                sock.sendto(build_mdns_query(service_name), (MDNS_ADDR, MDNS_PORT))

            while True:
                try:
                    payload, _ = sock.recvfrom(4096)
                except socket.timeout:
                    break

                for record in parse_mdns_response(payload):
                    if record[0] == "srv":
                        srv_records.append(record)
                    elif record[0] == "a":
                        addresses[record[1].rstrip('.')] = record[2]
    except OSError as exc:
        raise DiscoveryError(f"failed to use mDNS FTP discovery: {exc}") from exc

    candidates = []
    seen = set()
    for _, _, target, port in srv_records:
        host = addresses.get(target.rstrip('.'), target.rstrip('.'))
        key = (host, port)
        if key not in seen:
            candidates.append(key)
            seen.add(key)

    return candidates


def parse_host_port(value, default_port):
    host, separator, port_text = value.rpartition(':')
    if not separator:
        return value, default_port

    try:
        port = int(port_text)
    except ValueError as exc:
        raise NetloaderError("FTP host prompt must be a host or host:port") from exc
    if not 1 <= port <= 65535:
        raise NetloaderError("FTP port must be between 1 and 65535")

    return host, port


def resolve_ftp_host(host, port, stdin=sys.stdin):
    if host is not None:
        resolved_host, resolved_port = parse_host_port(host, port)
        return resolve_host(resolved_host, resolved_port), resolved_port

    candidates = discover_ftp_mdns()
    if len(candidates) == 1:
        candidate_host, candidate_port = candidates[0]
        return resolve_host(candidate_host, candidate_port), candidate_port

    if stdin.isatty():
        prompt = "Enter 3DS FTP host or host:port: "
        value = input(prompt).strip()
        if not value:
            raise DiscoveryError("FTP host is required")
        prompted_host, prompted_port = parse_host_port(value, port)
        return resolve_host(prompted_host, prompted_port), prompted_port

    raise DiscoveryError("could not resolve a 3DS FTP host. Pass --host, or run interactively to enter host or host:port")


def validate_input_file(path):
    if not os.path.exists(path):
        raise NetloaderError(f"file not found: {path}")
    if not os.path.isfile(path):
        raise NetloaderError(f"path is not a file: {path}")
    if os.path.getsize(path) == 0:
        raise NetloaderError(f"file is empty: {path}")


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


def get_ftp_remote_path(path, remote_path):
    if remote_path:
        return remote_path
    return "/" + os.path.basename(path)


def send_ftp(host, port, path, user, password, remote_path):
    validate_input_file(path)
    destination = get_ftp_remote_path(path, remote_path)

    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=user, passwd=password)

            print(f"Connected to FTP at {host}:{port}")
            print(f"Uploading {os.path.basename(path)} to {destination}...")
            with open(path, 'rb') as file_obj:
                ftp.storbinary(f"STOR {destination}", file_obj)
    except ftplib.all_errors + (OSError,) as exc:
        raise FTPTransferError(f"FTP transfer to {host}:{port} failed: {exc}") from exc


def check_tcp_status(host, port, label):
    try:
        with socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT) as sock:
            sock.settimeout(DEFAULT_TIMEOUT)
    except socket.timeout as exc:
        raise NetloaderError(f"{label} status check timed out while connecting to {host}:{port}") from exc
    except OSError as exc:
        raise NetloaderError(f"{label} status check failed for {host}:{port}: {exc}") from exc

    print(f"{label} is reachable at {host}:{port}")


def check_ftp_status(host, port, user, password):
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=user, passwd=password)
    except ftplib.all_errors + (OSError,) as exc:
        raise FTPTransferError(f"FTP status check failed for {host}:{port}: {exc}") from exc

    print(f"FTP is reachable at {host}:{port}")


def add_netloader_arguments(parser):
    parser.add_argument("file", help="Path to the .3dsx file to upload")
    parser.add_argument(
        "--host",
        help="3DS hostname or IPv4 address. If omitted, the utility tries UDP discovery on the local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_NETLOADER_PORT,
        help=f"NetLoader port for both discovery and transfer. Default: {DEFAULT_NETLOADER_PORT}",
    )


def add_ftp_arguments(parser):
    parser.add_argument("file", help="Path to the .3dsx file to upload")
    parser.add_argument(
        "--host",
        help="3DS FTP hostname or IPv4 address. If omitted, the utility tries mDNS discovery or prompts interactively.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_FTP_PORT,
        help=f"FTP port. Default: {DEFAULT_FTP_PORT}",
    )
    parser.add_argument("--user", default="anonymous", help="FTP username. Default: anonymous")
    parser.add_argument("--password", default="", help="FTP password. Default: empty")
    parser.add_argument("--remote", help="Remote destination file path. Default: /<local basename>")


def add_netloader_status_arguments(parser):
    parser.add_argument(
        "--host",
        help="3DS hostname or IPv4 address. If omitted, the utility tries UDP discovery on the local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_NETLOADER_PORT,
        help=f"NetLoader port for both discovery and connection check. Default: {DEFAULT_NETLOADER_PORT}",
    )


def add_ftp_status_arguments(parser):
    parser.add_argument(
        "--host",
        help="3DS FTP hostname or IPv4 address. If omitted, the utility tries mDNS discovery or prompts interactively.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_FTP_PORT,
        help=f"FTP port. Default: {DEFAULT_FTP_PORT}",
    )
    parser.add_argument("--user", default="anonymous", help="FTP username. Default: anonymous")
    parser.add_argument("--password", default="", help="FTP password. Default: empty")


def validate_port(parser, port, option="--port"):
    if not 1 <= port <= 65535:
        parser.error(f"{option} must be between 1 and 65535")


def parse_status_args(argv, command, add_arguments, description):
    parser = argparse.ArgumentParser(description=description)
    add_arguments(parser)
    args = parser.parse_args(argv)
    validate_port(parser, args.port)
    args.command = command
    args.action = STATUS_ACTION
    args.legacy = False
    return args


def parse_action_args(argv, command, action, add_arguments, description):
    parser = argparse.ArgumentParser(description=description)
    add_arguments(parser)
    args = parser.parse_args(argv)
    validate_port(parser, args.port)
    args.command = command
    args.action = action
    args.legacy = False
    return args


def parse_args(argv):
    argv = list(argv)
    parser = argparse.ArgumentParser(
        description="Transfer a .3dsx file to a modded 3DS over NetLoader or FTP.",
    )
    subparsers = parser.add_subparsers(dest="command")

    netloader_parser = subparsers.add_parser(NETLOADER_COMMAND, help="Upload and launch through 3dslink NetLoader")
    add_netloader_arguments(netloader_parser)

    ftp_parser = subparsers.add_parser(FTP_COMMAND, help="Upload through a 3DS FTP server such as ftpd")
    add_ftp_arguments(ftp_parser)

    if argv and argv[0] in ("-h", "--help"):
        return parser.parse_args(argv)

    if argv and argv[0] == STATUS_ACTION:
        args = parse_status_args(
            argv[1:],
            NETLOADER_COMMAND,
            add_netloader_status_arguments,
            "Check NetLoader connection status.",
        )
        args.legacy = True
        return args

    if len(argv) >= 2 and argv[0] == NETLOADER_COMMAND and argv[1] == STATUS_ACTION:
        return parse_status_args(
            argv[2:],
            NETLOADER_COMMAND,
            add_netloader_status_arguments,
            "Check NetLoader connection status.",
        )

    if len(argv) >= 2 and argv[0] == NETLOADER_COMMAND and argv[1] == LOAD_ACTION:
        return parse_action_args(
            argv[2:],
            NETLOADER_COMMAND,
            LOAD_ACTION,
            add_netloader_arguments,
            "Load a .3dsx file through 3dslink NetLoader.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == STATUS_ACTION:
        return parse_status_args(
            argv[2:],
            FTP_COMMAND,
            add_ftp_status_arguments,
            "Check FTP connection status.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == UPLOAD_ACTION:
        return parse_action_args(
            argv[2:],
            FTP_COMMAND,
            UPLOAD_ACTION,
            add_ftp_arguments,
            "Upload a file through FTP.",
        )

    if argv and argv[0] in (NETLOADER_COMMAND, FTP_COMMAND):
        args = parser.parse_args(argv)
        validate_port(parser, args.port)
        if args.command == NETLOADER_COMMAND:
            args.action = LOAD_ACTION
        else:
            args.action = UPLOAD_ACTION
        args.legacy = False
        return args

    legacy_parser = argparse.ArgumentParser(
        description="Transfer a .3dsx file to a modded 3DS over 3dslink NetLoader.",
    )
    add_netloader_arguments(legacy_parser)
    args = legacy_parser.parse_args(argv)
    validate_port(legacy_parser, args.port)
    args.command = NETLOADER_COMMAND
    args.action = LOAD_ACTION
    args.legacy = True

    return args


def resolve_netloader_host(args):
    host = args.host
    if host is None:
        print(f"Discovering a 3DS on the local network via UDP broadcast on port {args.port}...")
        host = discover_3ds(args.port, DEFAULT_DISCOVERY_RETRIES, 1.0)
        print(f"Discovered 3DS at {host}")
    else:
        host = resolve_host(host, args.port)
    return host


def run_netloader(args):
    host = resolve_netloader_host(args)

    send_3dsx(
        host=host,
        port=args.port,
        path=args.file,
    )
    print("Transfer complete. Check your 3DS screen.")


def run_ftp(args):
    host, port = resolve_ftp_host(args.host, args.port)
    send_ftp(
        host=host,
        port=port,
        path=args.file,
        user=args.user,
        password=args.password,
        remote_path=args.remote,
    )
    print("FTP upload complete.")


def run_status(args):
    if args.command == FTP_COMMAND:
        host, port = resolve_ftp_host(args.host, args.port)
        check_ftp_status(host, port, args.user, args.password)
        return

    host = resolve_netloader_host(args)
    check_tcp_status(host, args.port, "NetLoader")


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    try:
        if getattr(args, "legacy", False) and getattr(args, "action", UPLOAD_ACTION) != STATUS_ACTION:
            print(
                "Warning: running NetLoader without the 'netloader' command is deprecated; use 'netloader' explicitly.",
                file=sys.stderr,
            )

        if getattr(args, "action", UPLOAD_ACTION) == STATUS_ACTION:
            run_status(args)
        elif args.command == FTP_COMMAND:
            run_ftp(args)
        else:
            run_netloader(args)
        return 0
    except NetloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: transfer cancelled by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
