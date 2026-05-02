import argparse
import curses
import fnmatch
import ftplib
import os
import posixpath
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

NETLOADER_COMMAND = "netloader"
FTP_COMMAND = "ftp"
STATUS_ACTION = "status"
LOAD_ACTION = "load"
UPLOAD_ACTION = "upload"
EXPLORER_ACTION = "explorer"
DEFAULT_NETLOADER_PORT = 17491
DEFAULT_FTP_PORT = 5000
DEFAULT_PORT = DEFAULT_NETLOADER_PORT
DEFAULT_TIMEOUT = 30.0
DEFAULT_DISCOVERY_RETRIES = 10
DISCOVERY_REQUEST = b"3dsboot"
DISCOVERY_RESPONSE_PREFIX = b"boot3ds"
ZLIB_CHUNK = 16 * 1024
FTP_CHUNK = 16 * 1024
FTP_ARCHIVE_UNARCHIVE = "unarchive"
FTP_ARCHIVE_UPLOAD = "upload"
FTP_ARCHIVE_SKIP = "skip"
SEVEN_ZIP_COMMANDS = ("7zz", "7z")
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


def parse_host_port(value, default_port, label="host"):
    host, separator, port_text = value.rpartition(':')
    if not separator:
        return value, default_port

    try:
        port = int(port_text)
    except ValueError as exc:
        raise NetloaderError(f"{label} prompt must be a host or host:port") from exc
    if not 1 <= port <= 65535:
        raise NetloaderError(f"{label} port must be between 1 and 65535")

    return host, port


def resolve_ftp_host(host, port, stdin=sys.stdin):
    if host is not None:
        resolved_host, resolved_port = parse_host_port(host, port, "FTP host")
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
        prompted_host, prompted_port = parse_host_port(value, port, "FTP host")
        return resolve_host(prompted_host, prompted_port), prompted_port

    raise DiscoveryError("could not resolve a 3DS FTP host. Pass --host, or run interactively to enter host or host:port")


def validate_input_file(path):
    if not os.path.exists(path):
        raise NetloaderError(f"file not found: {path}")
    if not os.path.isfile(path):
        raise NetloaderError(f"path is not a file: {path}")
    if os.path.getsize(path) == 0:
        raise NetloaderError(f"file is empty: {path}")


def normalize_ftp_sources(sources):
    if isinstance(sources, (list, tuple)):
        return list(sources)
    return [sources]


def validate_ftp_source(path):
    if not os.path.exists(path):
        raise FTPTransferError(f"source not found: {path}")
    if not os.path.isfile(path) and not os.path.isdir(path):
        raise FTPTransferError(f"source is not a file or directory: {path}")


def normalize_patterns(patterns):
    return [pattern for pattern in (patterns or []) if pattern]


def path_matches_filters(relative_path, patterns):
    if not patterns:
        return True

    basename = posixpath.basename(relative_path)
    return any(
        fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(basename, pattern)
        for pattern in patterns
    )


def safe_extract_zip(archive_path, destination):
    destination_abs = os.path.abspath(destination)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = os.path.abspath(os.path.join(destination, member.filename))
            if target != destination_abs and not target.startswith(destination_abs + os.sep):
                raise FTPTransferError(f"archive member escapes destination: {member.filename}")
        archive.extractall(destination)


def find_7z_command():
    for command in SEVEN_ZIP_COMMANDS:
        resolved = shutil.which(command)
        if resolved:
            return resolved
    return None


def unarchive_ftp_source(source, destination):
    validate_input_file(source)
    lower_source = source.lower()

    if lower_source.endswith(".zip"):
        safe_extract_zip(source, destination)
        return destination

    if lower_source.endswith(".7z"):
        command = find_7z_command()
        if command is None:
            raise FTPTransferError("extracting .7z files requires a 7z or 7zz command in PATH")

        try:
            subprocess.run(
                [command, "x", source, f"-o{destination}", "-y"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise FTPTransferError(f"failed to extract {source}: {message}") from exc
        return destination

    raise FTPTransferError("--unarchive only supports .zip and .7z sources")


def is_supported_archive(path):
    lower_path = path.lower()
    return lower_path.endswith(".zip") or lower_path.endswith(".7z")


def iter_archive_sources(sources):
    for source in normalize_ftp_sources(sources):
        if os.path.isfile(source):
            if is_supported_archive(source):
                yield source, os.path.basename(source)
                continue
            raise FTPTransferError("--unarchive only supports .zip and .7z file sources")

        if not os.path.isdir(source):
            raise FTPTransferError(f"source is not a file or directory: {source}")

        for root, dirs, files in os.walk(source):
            dirs.sort()
            files.sort()
            for filename in files:
                local_path = os.path.join(root, filename)
                if is_supported_archive(local_path):
                    relative_path = os.path.relpath(local_path, source).replace(os.sep, "/")
                    yield local_path, relative_path


def unarchive_ftp_sources(sources, destination):
    for source in normalize_ftp_sources(sources):
        validate_ftp_source(source)

    archive_count = 0

    for archive_count, (archive_path, _) in enumerate(iter_archive_sources(sources), start=1):
        unarchive_ftp_source(archive_path, destination)

    if archive_count == 0:
        raise FTPTransferError("no .zip or .7z archives found to unarchive")

    return destination


def has_archive_sources(sources):
    for source in normalize_ftp_sources(sources):
        if os.path.isfile(source):
            if is_supported_archive(source):
                return True
            continue

        if not os.path.isdir(source):
            continue

        for _, _, files in os.walk(source):
            if any(is_supported_archive(filename) for filename in files):
                return True

    return False


def should_unarchive_ftp(args, stdin=None):
    return get_ftp_archive_action(args, stdin=stdin) == FTP_ARCHIVE_UNARCHIVE


def get_ftp_archive_action(args, stdin=None):
    if args.unarchive:
        return FTP_ARCHIVE_UNARCHIVE
    if stdin is None:
        stdin = sys.stdin
    if not stdin.isatty():
        return FTP_ARCHIVE_UPLOAD
    if not has_archive_sources(args.source):
        return FTP_ARCHIVE_UPLOAD

    answer = input("Archive files found. Extract archives before upload? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return FTP_ARCHIVE_UNARCHIVE
    return FTP_ARCHIVE_SKIP


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


def normalize_remote_path(path):
    if not path:
        return "/"
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return posixpath.normpath(normalized)


def remote_parent(path):
    parent = posixpath.dirname(path)
    return parent if parent else "/"


def join_remote_path(directory, name):
    return posixpath.normpath(posixpath.join(normalize_remote_path(directory), name))


def split_remote_name(path):
    directory = remote_parent(path)
    basename = posixpath.basename(path)
    stem, extension = posixpath.splitext(basename)
    return directory, stem, extension


def remote_size(ftp, path):
    try:
        return ftp.size(path)
    except ftplib.all_errors:
        return None


def remote_is_dir(ftp, path):
    current = None
    try:
        current = ftp.pwd()
    except ftplib.all_errors:
        pass

    try:
        ftp.cwd(path)
        return True
    except ftplib.all_errors:
        return False
    finally:
        if current is not None:
            try:
                ftp.cwd(current)
            except ftplib.all_errors:
                pass


def ensure_remote_dir(ftp, path):
    normalized = normalize_remote_path(path)
    if normalized == "/":
        return

    current = ""
    for part in normalized.strip("/").split("/"):
        current = current + "/" + part
        try:
            ftp.mkd(current)
        except ftplib.all_errors:
            pass


def iter_ftp_sources(source, patterns=None, skip_archives=False):
    patterns = normalize_patterns(patterns)

    if os.path.isfile(source):
        relative_path = os.path.basename(source)
        if skip_archives and is_supported_archive(source):
            return
        if path_matches_filters(relative_path, patterns):
            yield source, relative_path
        return

    for root, dirs, files in os.walk(source):
        dirs.sort()
        files.sort()
        for filename in files:
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, source).replace(os.sep, "/")
            if skip_archives and is_supported_archive(local_path):
                continue
            if path_matches_filters(relative_path, patterns):
                yield local_path, relative_path


def make_unique_remote_path(ftp, destination):
    directory, stem, extension = split_remote_name(destination)
    index = 1
    while index <= 999:
        candidate = posixpath.join(directory, f"{stem}_{index}{extension}")
        if remote_size(ftp, candidate) is None:
            return candidate
        index += 1
    raise FTPTransferError(f"could not find an unused remote filename for {destination}")


def resolve_ftp_destination(ftp, source, dest):
    destination = normalize_remote_path(dest)
    source_is_dir = os.path.isdir(source)

    if source_is_dir:
        ensure_remote_dir(ftp, destination)
        return destination, True

    if dest.endswith("/") or remote_is_dir(ftp, destination):
        ensure_remote_dir(ftp, destination)
        return join_remote_path(destination, os.path.basename(source)), False

    ensure_remote_dir(ftp, remote_parent(destination))
    return destination, False


def print_upload_progress(label, sent, total):
    if total:
        percent = min(100, int(sent * 100 / total))
        print(f"Uploading {label}: {sent}/{total} bytes ({percent}%)")
    else:
        print(f"Uploading {label}: {sent} bytes")


def upload_ftp_file(ftp, local_path, remote_path):
    total = os.path.getsize(local_path)
    sent = 0
    last_reported_percent = -1

    print(f"Uploading {local_path} -> {remote_path}")

    def report(block):
        nonlocal sent, last_reported_percent
        sent += len(block)
        percent = 100 if not total else int(sent * 100 / total)
        if percent == 100 or percent >= last_reported_percent + 10:
            print_upload_progress(posixpath.basename(remote_path), sent, total)
            last_reported_percent = percent

    with open(local_path, 'rb') as file_obj:
        ftp.storbinary(f"STOR {remote_path}", file_obj, blocksize=FTP_CHUNK, callback=report)


def print_ftp_summary(uploaded, skipped, renamed):
    print("FTP upload summary:")
    print(f"Uploaded: {len(uploaded)}")
    for local_path, remote_path in uploaded:
        print(f"  {local_path} -> {remote_path}")

    print(f"Skipped: {len(skipped)}")
    for local_path, remote_path in skipped:
        print(f"  {local_path} -> {remote_path} (same size)")

    print(f"Uploaded with different name: {len(renamed)}")
    for local_path, original_path, renamed_path in renamed:
        print(f"  {local_path} -> {renamed_path} (existing {original_path} has different size)")


def send_ftp(host, port, source, dest, user, password, patterns=None, skip_archives=False):
    sources = normalize_ftp_sources(source)
    for source_path in sources:
        validate_ftp_source(source_path)

    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=user, passwd=password)

            print(f"Connected to FTP at {host}:{port}")
            multiple_sources = len(sources) > 1
            if multiple_sources:
                base_destination = normalize_remote_path(dest)
                ensure_remote_dir(ftp, base_destination)
            else:
                base_destination, source_is_dir = resolve_ftp_destination(ftp, sources[0], dest)

            uploaded = []
            skipped = []
            renamed = []

            for source_path in sources:
                source_is_dir = os.path.isdir(source_path)
                for local_path, relative_path in iter_ftp_sources(source_path, patterns, skip_archives=skip_archives):
                    local_size = os.path.getsize(local_path)
                    if multiple_sources or source_is_dir:
                        destination = join_remote_path(base_destination, relative_path)
                        ensure_remote_dir(ftp, remote_parent(destination))
                    else:
                        destination = base_destination

                    existing_size = remote_size(ftp, destination)
                    if existing_size == local_size:
                        print(f"Skipping {local_path}; {destination} already exists with the same size")
                        skipped.append((local_path, destination))
                        continue
                    if existing_size is not None and existing_size != local_size:
                        original_destination = destination
                        destination = make_unique_remote_path(ftp, destination)
                        print(f"Remote file size differs; uploading {local_path} as {destination}")
                        upload_ftp_file(ftp, local_path, destination)
                        renamed.append((local_path, original_destination, destination))
                        continue

                    upload_ftp_file(ftp, local_path, destination)
                    uploaded.append((local_path, destination))

            print_ftp_summary(uploaded, skipped, renamed)
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
    if label == "NetLoader":
        print(
            "NetLoader may stop accepting transfers after a status check. "
            "Restart it before loading a .3dsx file: press B to go back, then press Y in the Homebrew Launcher."
        )


def check_ftp_status(host, port, user, password):
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=user, passwd=password)
    except ftplib.all_errors + (OSError,) as exc:
        raise FTPTransferError(f"FTP status check failed for {host}:{port}: {exc}") from exc

    print(f"FTP is reachable at {host}:{port}")


def parse_ftp_modify_time(value):
    if not value:
        return None
    if len(value) < 14 or not value[:14].isdigit():
        return value
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:14]} UTC"


def format_size(size):
    if size is None:
        return ""
    try:
        value = int(size)
    except (TypeError, ValueError):
        return str(size)

    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{value} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def ftp_entry_sort_key(entry):
    return (entry["type"] != "dir", entry["name"].lower())


def list_ftp_directory(ftp, path):
    entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    original = ftp.pwd()
    try:
        ftp.cwd(path)
        try:
            raw_entries = list(ftp.mlsd())
        except ftplib.all_errors:
            raw_entries = [(name, {}) for name in ftp.nlst()]

        for name, facts in raw_entries:
            if name in ("", ".", ".."):
                continue
            entry_type = facts.get("type", "file")
            if entry_type == "cdir":
                continue
            if entry_type == "pdir":
                name = ".."
                entry_type = "dir"
            entries.append(
                {
                    "name": name,
                    "type": "dir" if entry_type == "dir" else "file",
                    "size": facts.get("size"),
                    "modify": facts.get("modify"),
                }
            )
    finally:
        ftp.cwd(original)

    return [entries[0]] + sorted(entries[1:], key=ftp_entry_sort_key)


def join_ftp_explorer_path(current, name):
    if name == "..":
        parent = posixpath.dirname(current.rstrip("/"))
        return parent or "/"
    return join_remote_path(current, name)


def ftp_entry_display_name(entry):
    if entry["name"] == "..":
        return ".. (go up)"
    return entry["name"]


def selectable_ftp_entries(current_path, entries, selected, selected_paths):
    selected_items = []
    by_path = {}
    for entry in entries:
        if entry["name"] == "..":
            continue
        path = join_ftp_explorer_path(current_path, entry["name"])
        by_path[path] = entry
        if path in selected_paths:
            selected_items.append((path, entry))

    if selected_items:
        return selected_items

    entry = entries[selected]
    if entry["name"] == "..":
        return []
    path = join_ftp_explorer_path(current_path, entry["name"])
    return [(path, entry)]


def add_entry_to_selection(current_path, entries, selected, selected_paths):
    entry = entries[selected]
    if entry["name"] == "..":
        return selected_paths
    selected_paths.add(join_ftp_explorer_path(current_path, entry["name"]))
    return selected_paths


def move_ftp_selection(current_path, entries, selected, selected_paths, direction):
    selected_paths = add_entry_to_selection(current_path, entries, selected, selected_paths)
    selected = max(0, min(len(entries) - 1, selected + direction))
    selected_paths = add_entry_to_selection(current_path, entries, selected, selected_paths)
    return selected, selected_paths


def restored_ftp_selection(entries, name, fallback):
    if name is None:
        return min(fallback, len(entries) - 1)
    return next(
        (index for index, entry in enumerate(entries) if entry["name"] == name),
        min(fallback, len(entries) - 1),
    )


def delete_ftp_path(ftp, path, entry_type):
    if entry_type != "dir":
        ftp.delete(path)
        return

    for entry in list_ftp_directory(ftp, path):
        if entry["name"] == "..":
            continue
        child_path = join_ftp_explorer_path(path, entry["name"])
        delete_ftp_path(ftp, child_path, entry["type"])
    ftp.rmd(path)


def move_ftp_paths(ftp, items, destination_dir):
    moved = []
    destination_dir = normalize_remote_path(destination_dir)
    for source_path, entry in items:
        destination_path = join_remote_path(destination_dir, posixpath.basename(source_path))
        if destination_path == source_path:
            continue
        if entry["type"] == "dir" and destination_path.startswith(source_path.rstrip("/") + "/"):
            raise FTPTransferError(f"cannot move a directory into itself: {source_path}")
        ftp.rename(source_path, destination_path)
        moved.append((source_path, destination_path))
    return moved


def truncate_text(text, width):
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 3] + "..." if width > 3 else text[:width]


def draw_ftp_explorer(
    stdscr,
    current_path,
    entries,
    selected,
    message="",
    metadata_loading=False,
    selected_paths=None,
    move_buffer=None,
):
    selected_paths = selected_paths or set()
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 20:
        stdscr.erase()
        stdscr.addnstr(0, 0, "Terminal too small", max(0, width - 1))
        stdscr.refresh()
        return

    stdscr.erase()
    stdscr.addnstr(0, 0, f"FTP Explorer: {current_path}", width - 1, curses.A_BOLD)
    stdscr.addnstr(1, 0, "Up/Down/j/k move  Shift+Up/Down/J/K multi-select  Enter open/go up  Backspace up  m move  p/P paste  d delete  c/Esc cancel  q quit", width - 1)

    split = max(24, width // 2)
    list_width = min(split, width - 1)
    detail_x = min(list_width + 2, width - 1)
    detail_width = max(0, width - detail_x - 1)
    visible_rows = max(1, height - 5)
    offset = max(0, selected - visible_rows + 1)

    for row, entry in enumerate(entries[offset: offset + visible_rows], start=3):
        path = join_ftp_explorer_path(current_path, entry["name"])
        selected_marker = "*" if path in selected_paths else " "
        prefix = "[Dir]" if entry["type"] == "dir" else "     "
        label = f"{selected_marker} {prefix} {ftp_entry_display_name(entry)}"
        attr = curses.A_REVERSE if offset + row - 3 == selected else curses.A_NORMAL
        stdscr.addnstr(row, 0, truncate_text(label, list_width - 1), list_width - 1, attr)

    if entries and detail_width > 0:
        entry = entries[selected]
        selected_path = join_ftp_explorer_path(current_path, entry["name"])
        size = "n/a" if entry["type"] == "dir" else format_size(entry["size"]) or "n/a"
        details = [
            ("Name", ftp_entry_display_name(entry)),
            ("Type", "Directory" if entry["type"] == "dir" else "File"),
            ("Path", selected_path),
            ("Size", size),
            ("Modified", parse_ftp_modify_time(entry["modify"]) or "n/a"),
        ]
        stdscr.addnstr(3, detail_x, "Metadata", detail_width, curses.A_BOLD)
        if metadata_loading:
            stdscr.addnstr(5, detail_x, "Loading...", detail_width)
        else:
            for index, (label, value) in enumerate(details, start=5):
                if index >= height - 1:
                    break
                stdscr.addnstr(index, detail_x, truncate_text(f"{label}: {value}", detail_width), detail_width)

    move_text = f"Move: {len(move_buffer)} item(s) ready. p pastes here, P pastes into selected dir, c/Esc cancels." if move_buffer else "Move: none. Press m to stage selected item(s) for moving."
    if height > 1:
        stdscr.addnstr(height - 2, 0, truncate_text(move_text, width - 1), width - 1, curses.A_DIM)
    if message and height > 0:
        stdscr.addnstr(height - 1, 0, truncate_text(message, width - 1), width - 1, curses.A_DIM)
    stdscr.refresh()


def prompt_ftp_confirmation(stdscr, prompt):
    height, width = stdscr.getmaxyx()
    if height <= 0 or width <= 0:
        return False

    lines = [prompt, "Confirm? [y/N]"]
    box_width = min(max(32, max(len(line) for line in lines) + 4), max(1, width - 2))
    box_height = 5
    top = max(0, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)

    for row in range(box_height):
        stdscr.addnstr(top + row, left, " " * box_width, box_width, curses.A_REVERSE)

    stdscr.addnstr(top, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
    for index, line in enumerate(lines, start=1):
        text = truncate_text(line, box_width - 4)
        stdscr.addnstr(top + index, left, f"| {text.ljust(box_width - 4)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), 27, 10, 13):
            return False


def ftp_explorer_loop(stdscr, ftp, start_path="/"):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    current_path = normalize_remote_path(start_path)
    selected = 0
    message = ""
    entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    selected_paths = set()
    shift_selected_paths = set()
    shift_selecting = False
    move_buffer = []
    restore_selection_name = None

    while True:
        draw_ftp_explorer(
            stdscr,
            current_path,
            entries,
            selected,
            "Loading directory...",
            metadata_loading=True,
            selected_paths=selected_paths | shift_selected_paths,
            move_buffer=move_buffer,
        )
        try:
            entries = list_ftp_directory(ftp, current_path)
            message = ""
        except ftplib.all_errors as exc:
            message = f"Could not list {current_path}: {exc}"
            entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]

        if restore_selection_name is not None:
            selected = restored_ftp_selection(entries, restore_selection_name, selected)
            restore_selection_name = None
        else:
            selected = restored_ftp_selection(entries, None, selected)
        while True:
            draw_ftp_explorer(
                stdscr,
                current_path,
                entries,
                selected,
                message,
                selected_paths=selected_paths | shift_selected_paths,
                move_buffer=move_buffer,
            )
            key = stdscr.getch()
            message = ""

            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                selected = min(len(entries) - 1, selected + 1)
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (getattr(curses, "KEY_SR", -1), ord("K")):
                if not shift_selecting:
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = True
                selected, shift_selected_paths = move_ftp_selection(current_path, entries, selected, shift_selected_paths, -1)
                continue
            if key in (getattr(curses, "KEY_SF", -1), ord("J")):
                if not shift_selecting:
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = True
                selected, shift_selected_paths = move_ftp_selection(current_path, entries, selected, shift_selected_paths, 1)
                continue
            if key == ord(" "):
                entry = entries[selected]
                if entry["name"] == "..":
                    message = "Cannot select parent entry."
                    continue
                path = join_ftp_explorer_path(current_path, entry["name"])
                if path in selected_paths:
                    selected_paths.remove(path)
                else:
                    selected_paths.add(path)
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (ord("c"), ord("C"), 27):
                move_buffer = []
                message = "Move canceled."
                continue
            if key == ord("m"):
                items = selectable_ftp_entries(current_path, entries, selected, selected_paths | shift_selected_paths)
                if not items:
                    message = "Select a file or directory to move."
                    continue
                move_buffer = items
                selected_paths = set()
                shift_selected_paths = set()
                shift_selecting = False
                message = f"Staged {len(move_buffer)} item(s) to move."
                continue
            if key in (ord("p"), ord("P")):
                if not move_buffer:
                    message = "No staged move. Press m first."
                    continue
                destination_dir = current_path
                if key == ord("P"):
                    entry = entries[selected]
                    if entry["type"] == "dir" and entry["name"] != "..":
                        destination_dir = join_ftp_explorer_path(current_path, entry["name"])
                description = f"Move {len(move_buffer)} item(s) to {destination_dir}?"
                if not prompt_ftp_confirmation(stdscr, description):
                    message = "Move canceled."
                    continue
                try:
                    moved = move_ftp_paths(ftp, move_buffer, destination_dir)
                    message = f"Moved {len(moved)} item(s)."
                    move_buffer = []
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    break
                except ftplib.all_errors + (OSError, FTPTransferError) as exc:
                    message = f"Move failed: {exc}"
                    continue
            if key == ord("d"):
                items = selectable_ftp_entries(current_path, entries, selected, selected_paths | shift_selected_paths)
                if not items:
                    message = "Select a file or directory to delete."
                    continue
                names = ", ".join(path for path, _ in items[:3])
                suffix = "..." if len(items) > 3 else ""
                description = f"Delete {len(items)} item(s): {names}{suffix}?"
                if not prompt_ftp_confirmation(stdscr, description):
                    message = "Delete canceled."
                    continue
                try:
                    for path, entry in items:
                        delete_ftp_path(ftp, path, entry["type"])
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    move_buffer = [(path, entry) for path, entry in move_buffer if path not in {item_path for item_path, _ in items}]
                    message = f"Deleted {len(items)} item(s)."
                    break
                except ftplib.all_errors + (OSError,) as exc:
                    message = f"Delete failed: {exc}"
                    continue
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if current_path == "/":
                    message = "Already at root."
                    continue
                previous_path = current_path
                current_path = join_ftp_explorer_path(current_path, "..")
                restore_selection_name = posixpath.basename(previous_path.rstrip("/"))
                selected_paths = set()
                shift_selected_paths = set()
                shift_selecting = False
                break
            if key in (curses.KEY_ENTER, 10, 13):
                entry = entries[selected]
                if entry["type"] == "dir":
                    previous_path = current_path
                    current_path = join_ftp_explorer_path(current_path, entry["name"])
                    if entry["name"] == "..":
                        restore_selection_name = posixpath.basename(previous_path.rstrip("/"))
                    else:
                        selected = 0
                        restore_selection_name = None
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    break
                message = "Selected file. Use FTP upload for transfers."


def run_ftp_explorer(args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise FTPTransferError("FTP explorer requires an interactive terminal")

    host, port = resolve_ftp_host(args.host, args.port)
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=args.user, passwd=args.password)
            curses.wrapper(ftp_explorer_loop, ftp, "/")
    except ftplib.all_errors + (OSError,) as exc:
        raise FTPTransferError(f"FTP explorer failed for {host}:{port}: {exc}") from exc


def add_ftp_connection_arguments(parser):
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
    parser.add_argument("--source", action="append", required=True, help="Local file or directory to upload. Repeat for multiple sources")
    parser.add_argument("--dest", required=True, help="Remote destination file or directory path")
    parser.add_argument(
        "--unarchive",
        action="store_true",
        help="Extract .zip or .7z archives before uploading. If omitted in an interactive terminal, archives trigger a yes/no prompt.",
    )
    parser.add_argument(
        "--patterns",
        action="append",
        help="Upload only files matching a shell-style pattern, such as '*.nds' or '*.gba'. Repeat for multiple patterns.",
    )
    add_ftp_connection_arguments(parser)


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
    add_ftp_connection_arguments(parser)


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

    ftp_parser = subparsers.add_parser(FTP_COMMAND, help="Browse or upload through a 3DS FTP server such as ftpd")
    add_ftp_connection_arguments(ftp_parser)

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
            "Upload a file or directory through FTP.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == EXPLORER_ACTION:
        return parse_action_args(
            argv[2:],
            FTP_COMMAND,
            EXPLORER_ACTION,
            add_ftp_connection_arguments,
            "Browse files and directories through FTP.",
        )

    if argv and argv[0] in (NETLOADER_COMMAND, FTP_COMMAND):
        args = parser.parse_args(argv)
        validate_port(parser, args.port)
        if args.command == NETLOADER_COMMAND:
            args.action = LOAD_ACTION
        else:
            args.action = EXPLORER_ACTION
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


def run_netloader(args):
    host, port = resolve_netloader_host(args)

    send_3dsx(
        host=host,
        port=port,
        path=args.file,
    )
    print("Transfer complete. Check your 3DS screen.")


def run_ftp(args):
    host, port = resolve_ftp_host(args.host, args.port)
    archive_action = get_ftp_archive_action(args)
    if archive_action == FTP_ARCHIVE_UNARCHIVE:
        with tempfile.TemporaryDirectory(prefix="3dslink-ftp-") as temp_dir:
            source = unarchive_ftp_sources(args.source, temp_dir)
            send_ftp(
                host=host,
                port=port,
                source=source,
                dest=args.dest,
                user=args.user,
                password=args.password,
                patterns=args.patterns,
            )
        return

    send_ftp(
        host=host,
        port=port,
        source=args.source,
        dest=args.dest,
        user=args.user,
        password=args.password,
        patterns=args.patterns,
        skip_archives=archive_action == FTP_ARCHIVE_SKIP,
    )


def run_status(args):
    if args.command == FTP_COMMAND:
        host, port = resolve_ftp_host(args.host, args.port)
        check_ftp_status(host, port, args.user, args.password)
        return

    host, port = resolve_netloader_host(args)
    check_tcp_status(host, port, "NetLoader")


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
        elif args.command == FTP_COMMAND and args.action == EXPLORER_ACTION:
            run_ftp_explorer(args)
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
