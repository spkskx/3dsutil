import curses
import fnmatch
import ftplib
import os
import posixpath
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
import time

from core import (
    DEFAULT_FTP_PORT, DEFAULT_TIMEOUT, FTP_ARCHIVE_SKIP, FTP_ARCHIVE_UNARCHIVE, FTP_ARCHIVE_UPLOAD,
    FTP_CHUNK, DiscoveryError, FTPTransferError, NetloaderError,
    SEVEN_ZIP_COMMANDS, parse_host_port, resolve_host, validate_input_file,
)


def ask_yes_no(prompt, default=False, stdin=sys.stdin):
    if not stdin.isatty():
        return default

    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def command_exists(command):
    return shutil.which(command) is not None


def package_install_command(packages):
    if command_exists("apt-get"):
        manager = "apt-get"
    elif command_exists("dnf"):
        manager = "dnf"
    elif command_exists("pacman"):
        manager = "pacman"
    elif command_exists("brew"):
        manager = "brew"
    else:
        return None

    if isinstance(packages, dict):
        packages = packages.get(manager)
        if not packages:
            return None

    package_text = " ".join(packages)
    if manager == "apt-get":
        return f"sudo apt-get update && sudo apt-get install -y {package_text}"
    if manager == "dnf":
        return f"sudo dnf install -y {package_text}"
    if manager == "pacman":
        return f"sudo pacman -S --needed {package_text}"
    if manager == "brew":
        return f"brew install {package_text}"
    return None


def prompt_install_command_dependency(label, commands, packages):
    if any(command_exists(command) for command in commands):
        return True

    install_command = package_install_command(packages)
    if install_command is None:
        raise FTPTransferError(f"{label} is required, but no supported package manager was found")

    if not ask_yes_no(f"{label} is required. Install it now?", default=True):
        return False

    try:
        subprocess.run(install_command, shell=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise FTPTransferError(f"failed to install {label}: {exc}") from exc

    return any(command_exists(command) for command in commands)


def resolve_ftp_host(host, port, stdin=sys.stdin):
    if host is not None:
        resolved_host, resolved_port = parse_host_port(host, port, "FTP host")
        return resolve_host(resolved_host, resolved_port), resolved_port

    if stdin.isatty():
        prompt = "Enter 3DS FTP host or host:port: "
        value = input(prompt).strip()
        if not value:
            raise DiscoveryError("FTP host is required")
        prompted_host, prompted_port = parse_host_port(value, port, "FTP host")
        return resolve_host(prompted_host, prompted_port), prompted_port

    raise DiscoveryError("FTP host is required. Pass --host, or run interactively to enter host or host:port")


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
            prompt_install_command_dependency(
                "7z or 7zz",
                SEVEN_ZIP_COMMANDS,
                {
                    "apt-get": ("p7zip-full",),
                    "dnf": ("p7zip",),
                    "pacman": ("p7zip",),
                    "brew": ("p7zip",),
                },
            )
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


def make_unique_local_path(destination):
    directory = os.path.dirname(destination)
    basename = os.path.basename(destination)
    stem, extension = os.path.splitext(basename)
    index = 1
    while index <= 999:
        candidate = os.path.join(directory, f"{stem}_{index}{extension}")
        if not os.path.exists(candidate):
            return candidate
        index += 1
    raise FTPTransferError(f"could not find an unused local filename for {destination}")


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


def upload_ftp_file(ftp, local_path, remote_path, progress=None, item_index=1, item_total=1):
    total = os.path.getsize(local_path)
    sent = 0
    last_reported_percent = -1

    if progress is None:
        print(f"Uploading {local_path} -> {remote_path}")

    def report(block):
        nonlocal sent, last_reported_percent
        sent += len(block)
        percent = 100 if not total else int(sent * 100 / total)
        if percent == 100 or percent >= last_reported_percent + 10:
            if progress is None:
                print_upload_progress(posixpath.basename(remote_path), sent, total)
            else:
                progress(posixpath.basename(remote_path), sent, total, item_index, item_total)
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


def normalize_local_explorer_path(path):
    return os.path.abspath(os.path.expanduser(path or "~"))


def validate_local_explorer_dir(path):
    normalized = normalize_local_explorer_path(path)
    if not os.path.isdir(normalized):
        raise FTPTransferError(f"explorer source is not a directory: {path}")
    return normalized


def is_within_local_root(path, root):
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    return path == root or path.startswith(root + os.sep)


def local_entry_sort_key(entry):
    return (entry["type"] != "dir", entry["name"].lower())


def list_local_directory(path, root=None):
    entries = []
    if root is None or os.path.abspath(path) != os.path.abspath(root):
        entries.append({"name": "..", "type": "dir", "size": None, "modify": None})
    with os.scandir(path) as iterator:
        for item in iterator:
            try:
                stat = item.stat()
            except OSError:
                stat = None
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir(follow_symlinks=False) else "file",
                    "size": None if stat is None or item.is_dir(follow_symlinks=False) else stat.st_size,
                    "modify": None if stat is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                }
            )
    return entries[:1] + sorted(entries[1:], key=local_entry_sort_key) if entries and entries[0]["name"] == ".." else sorted(entries, key=local_entry_sort_key)


def join_local_explorer_path(current, name, root=None):
    if name == "..":
        candidate = os.path.dirname(current.rstrip(os.sep)) or os.sep
    else:
        candidate = os.path.abspath(os.path.join(current, name))
    if root is not None and not is_within_local_root(candidate, root):
        return os.path.abspath(root)
    return candidate


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


def explorer_join_path(side, current_path, name):
    if side == "local":
        return join_local_explorer_path(current_path, name)
    return join_ftp_explorer_path(current_path, name)


def explorer_basename(side, path):
    return os.path.basename(path.rstrip(os.sep)) if side == "local" else posixpath.basename(path.rstrip("/"))


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


def add_explorer_entry_to_selection(side, current_path, entries, selected, selected_paths):
    entry = entries[selected]
    if entry["name"] == "..":
        return selected_paths
    selected_paths.add((side, explorer_join_path(side, current_path, entry["name"])))
    return selected_paths


def move_explorer_selection(side, current_path, entries, selected, selected_paths, direction):
    selected_paths = add_explorer_entry_to_selection(side, current_path, entries, selected, selected_paths)
    selected = max(0, min(len(entries) - 1, selected + direction))
    selected_paths = add_explorer_entry_to_selection(side, current_path, entries, selected, selected_paths)
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


def count_local_transfer_files(items, skip_archives=False):
    count = 0
    for source_path, _ in items:
        if os.path.isfile(source_path):
            if skip_archives and is_supported_archive(source_path):
                continue
            count += 1
            continue
        for root, _, files in os.walk(source_path):
            for filename in files:
                path = os.path.join(root, filename)
                if skip_archives and is_supported_archive(path):
                    continue
                count += 1
    return count


def count_remote_transfer_files(ftp, items):
    count = 0
    for source_path, entry in items:
        if entry["type"] != "dir":
            count += 1
            continue
        for child in list_ftp_directory(ftp, source_path):
            if child["name"] == "..":
                continue
            child_path = join_ftp_explorer_path(source_path, child["name"])
            count += count_remote_transfer_files(ftp, [(child_path, child)])
    return count


def upload_local_path_to_remote(ftp, local_path, destination_dir, skip_archives=False, progress=None, item_state=None):
    if skip_archives and is_supported_archive(local_path):
        return None
    destination = join_remote_path(destination_dir, os.path.basename(local_path.rstrip(os.sep)))
    if os.path.isdir(local_path):
        ensure_remote_dir(ftp, destination)
        for name in sorted(os.listdir(local_path)):
            upload_local_path_to_remote(
                ftp,
                os.path.join(local_path, name),
                destination,
                skip_archives=skip_archives,
                progress=progress,
                item_state=item_state,
            )
        return destination

    local_size = os.path.getsize(local_path)
    existing_size = remote_size(ftp, destination)
    if existing_size == local_size:
        return None
    if existing_size is not None and existing_size != local_size:
        destination = make_unique_remote_path(ftp, destination)

    ensure_remote_dir(ftp, remote_parent(destination))
    item_index = 1
    item_total = 1
    if item_state is not None:
        item_state["current"] += 1
        item_index = item_state["current"]
        item_total = item_state["total"]
    upload_ftp_file(ftp, local_path, destination, progress=progress, item_index=item_index, item_total=item_total)
    return destination


def download_remote_path_to_local(ftp, remote_path, entry_type, destination_dir, progress=None, item_state=None):
    destination = os.path.join(destination_dir, posixpath.basename(remote_path.rstrip("/")))
    if entry_type == "dir":
        os.makedirs(destination, exist_ok=True)
        for entry in list_ftp_directory(ftp, remote_path):
            if entry["name"] == "..":
                continue
            child_path = join_ftp_explorer_path(remote_path, entry["name"])
            download_remote_path_to_local(ftp, child_path, entry["type"], destination, progress=progress, item_state=item_state)
        return destination

    os.makedirs(destination_dir, exist_ok=True)
    remote_file_size = remote_size(ftp, remote_path)
    if os.path.exists(destination):
        if remote_file_size is not None and os.path.getsize(destination) == remote_file_size:
            return None
        destination = make_unique_local_path(destination)

    item_index = 1
    item_total = 1
    if item_state is not None:
        item_state["current"] += 1
        item_index = item_state["current"]
        item_total = item_state["total"]

    received = 0

    def report(block):
        nonlocal received
        received += len(block)
        file_obj.write(block)
        if progress is not None:
            progress(posixpath.basename(remote_path), received, remote_file_size or 0, item_index, item_total)

    with open(destination, "wb") as file_obj:
        ftp.retrbinary(f"RETR {remote_path}", report, blocksize=FTP_CHUNK)
    return destination


def delete_local_path(path, entry_type):
    if entry_type == "dir":
        shutil.rmtree(path)
        return
    os.remove(path)


def copy_local_paths_to_remote(ftp, items, destination_dir, skip_archives=False, progress=None):
    copied = []
    item_state = {"current": 0, "total": count_local_transfer_files(items, skip_archives=skip_archives)}
    for source_path, entry in items:
        destination = upload_local_path_to_remote(
            ftp,
            source_path,
            destination_dir,
            skip_archives=skip_archives,
            progress=progress,
            item_state=item_state,
        )
        if destination is not None:
            copied.append((source_path, destination))
    return copied


def copy_remote_paths_to_local(ftp, items, destination_dir, progress=None):
    copied = []
    item_state = {"current": 0, "total": count_remote_transfer_files(ftp, items)}
    for source_path, entry in items:
        destination = download_remote_path_to_local(
            ftp,
            source_path,
            entry["type"],
            destination_dir,
            progress=progress,
            item_state=item_state,
        )
        if destination is not None:
            copied.append((source_path, destination))
    return copied


def move_local_paths(items, destination_dir):
    moved = []
    for source_path, entry in items:
        destination = os.path.join(destination_dir, os.path.basename(source_path.rstrip(os.sep)))
        if os.path.abspath(destination) == os.path.abspath(source_path):
            continue
        if entry["type"] == "dir" and os.path.abspath(destination).startswith(os.path.abspath(source_path) + os.sep):
            raise FTPTransferError(f"cannot move a directory into itself: {source_path}")
        shutil.move(source_path, destination)
        moved.append((source_path, destination))
    return moved


def copy_or_move_explorer_items(ftp, move_buffer, destination_side, destination_dir, skip_archives=False, progress=None):
    if not move_buffer:
        return []
    source_side = move_buffer[0][0]
    items = [(path, entry) for _, path, entry in move_buffer]
    if source_side == "remote" and destination_side == "remote":
        return move_ftp_paths(ftp, items, destination_dir)
    if source_side == "local" and destination_side == "local":
        return move_local_paths(items, destination_dir)
    if source_side == "local" and destination_side == "remote":
        return copy_local_paths_to_remote(ftp, items, destination_dir, skip_archives=skip_archives, progress=progress)
    return copy_remote_paths_to_local(ftp, items, destination_dir, progress=progress)


def explorer_items_have_archives(items):
    return any(side == "local" and has_archive_sources(path) for side, path, _ in items)


def truncate_text(text, width):
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 3] + "..." if width > 3 else text[:width]


def draw_explorer_pane(stdscr, y, x, width, height, title, side, current_path, entries, selected, active, selected_paths):
    attr = curses.A_BOLD | (curses.A_REVERSE if active else curses.A_NORMAL)
    stdscr.addnstr(y, x, truncate_text(title, width - 1), width - 1, attr)
    stdscr.addnstr(y + 1, x, truncate_text(current_path, width - 1), width - 1, curses.A_DIM)
    visible_rows = max(1, height - 2)
    offset = max(0, selected - visible_rows + 1)
    for row, entry in enumerate(entries[offset: offset + visible_rows], start=y + 2):
        path = explorer_join_path(side, current_path, entry["name"])
        selected_marker = "*" if (side, path) in selected_paths else " "
        prefix = "[Dir]" if entry["type"] == "dir" else "     "
        label = f"{selected_marker} {prefix} {ftp_entry_display_name(entry)}"
        row_attr = curses.A_REVERSE if active and offset + row - y - 2 == selected else curses.A_NORMAL
        stdscr.addnstr(row, x, truncate_text(label, width - 1), width - 1, row_attr)


def draw_ftp_explorer(
    stdscr,
    local_path,
    local_entries,
    local_selected,
    remote_path,
    remote_entries,
    remote_selected,
    active_side,
    message="",
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
    stdscr.addnstr(0, 0, "FTP Explorer: Local <-> 3DS", width - 1, curses.A_BOLD)
    stdscr.addnstr(1, 0, "Tab/Left/Right/h/l switch  Up/Down/j/k move  Space select  Enter open  Backspace up  m stage  p paste  P paste into dir  d delete  c cancel  q quit", width - 1)

    pane_gap = 2
    pane_width = max(10, (width - pane_gap) // 2)
    pane_height = max(1, height - 5)
    draw_explorer_pane(stdscr, 3, 0, pane_width, pane_height, "Local", "local", local_path, local_entries, local_selected, active_side == "local", selected_paths)
    draw_explorer_pane(stdscr, 3, pane_width + pane_gap, max(10, width - pane_width - pane_gap - 1), pane_height, "3DS", "remote", remote_path, remote_entries, remote_selected, active_side == "remote", selected_paths)

    source_text = ""
    if move_buffer:
        source_text = f" from {move_buffer[0][0]}"
    move_text = f"Move: {len(move_buffer)} item(s){source_text} ready. p pastes here, P pastes into selected dir, c/Esc cancels." if move_buffer else "Move: none. Press m to stage selected item(s)."
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


def prompt_archive_transfer_action(stdscr):
    height, width = stdscr.getmaxyx()
    if height <= 0 or width <= 0:
        return FTP_ARCHIVE_SKIP

    lines = [
        "Archive files selected.",
        "u: unarchive all before upload",
        "s: skip archives",
        "c/Esc: cancel",
    ]
    box_width = min(max(38, max(len(line) for line in lines) + 4), max(1, width - 2))
    box_height = len(lines) + 2
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
        if key in (ord("u"), ord("U")):
            return FTP_ARCHIVE_UNARCHIVE
        if key in (ord("s"), ord("S")):
            return FTP_ARCHIVE_SKIP
        if key in (ord("c"), ord("C"), 27):
            return None


def draw_transfer_progress(stdscr, label, sent, total, item_index, item_total):
    height, width = stdscr.getmaxyx()
    if height <= 0 or width <= 0:
        return

    percent = 100 if not total else min(100, int(sent * 100 / total))
    title = f"Transfer {item_index}/{max(1, item_total)}"
    size_text = f"{format_size(sent)} / {format_size(total)}" if total else format_size(sent)
    lines = [
        title,
        truncate_text(label, 60),
        size_text,
    ]
    box_width = min(max(44, max(len(line) for line in lines) + 4), max(1, width - 2))
    box_height = 7
    top = max(0, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)
    inner_width = max(1, box_width - 4)
    bar_width = max(1, inner_width - 7)
    filled = min(bar_width, int(bar_width * percent / 100))
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + f"] {percent:3d}%"

    for row in range(box_height):
        stdscr.addnstr(top + row, left, " " * box_width, box_width, curses.A_REVERSE)

    stdscr.addnstr(top, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + 1, left, f"| {title.ljust(inner_width)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + 2, left, f"| {truncate_text(label, inner_width).ljust(inner_width)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + 3, left, f"| {bar[:inner_width].ljust(inner_width)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + 4, left, f"| {size_text.ljust(inner_width)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + 5, left, f"| {'Please wait...'.ljust(inner_width)} |", box_width, curses.A_REVERSE)
    stdscr.addnstr(top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
    stdscr.refresh()


def active_explorer_state(active_side, local_path, local_entries, local_selected, remote_path, remote_entries, remote_selected):
    if active_side == "local":
        return local_path, local_entries, local_selected
    return remote_path, remote_entries, remote_selected


def selected_explorer_items(side, current_path, entries, selected, selected_paths):
    selected_items = []
    for entry in entries:
        if entry["name"] == "..":
            continue
        path = explorer_join_path(side, current_path, entry["name"])
        if (side, path) in selected_paths:
            selected_items.append((side, path, entry))
    if selected_items:
        return selected_items
    entry = entries[selected]
    if entry["name"] == "..":
        return []
    return [(side, explorer_join_path(side, current_path, entry["name"]), entry)]


def prepare_explorer_transfer_items(stdscr, move_buffer, destination_side):
    if not move_buffer or move_buffer[0][0] != "local" or destination_side != "remote":
        return move_buffer, False, None
    if not explorer_items_have_archives(move_buffer):
        return move_buffer, False, None

    action = prompt_archive_transfer_action(stdscr)
    if action is None:
        return None, False, None
    if action == FTP_ARCHIVE_SKIP:
        return move_buffer, True, None

    temp_dir = tempfile.mkdtemp(prefix="3dsutil-explorer-")
    archive_paths = [path for _, path, _ in move_buffer if has_archive_sources(path)]
    unarchive_ftp_sources(archive_paths, temp_dir)
    prepared = [
        ("local", temp_dir, {"name": os.path.basename(temp_dir), "type": "dir"})
    ]
    prepared.extend(item for item in move_buffer if not is_supported_archive(item[1]))
    return prepared, False, temp_dir


def ftp_explorer_loop(stdscr, ftp, local_start_path=None, remote_start_path="/"):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    local_root = validate_local_explorer_dir(local_start_path or ".")
    local_path = local_root
    remote_path = normalize_remote_path(remote_start_path)
    local_selected = 0
    remote_selected = 0
    active_side = "local"
    message = ""
    local_entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    remote_entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    selected_paths = set()
    shift_selected_paths = set()
    shift_selecting = False
    move_buffer = []

    while True:
        try:
            local_entries = list_local_directory(local_path, local_root)
            remote_entries = list_ftp_directory(ftp, remote_path)
            message = ""
        except ftplib.all_errors + (OSError,) as exc:
            message = f"Could not list directory: {exc}"
        local_selected = restored_ftp_selection(local_entries, None, local_selected)
        remote_selected = restored_ftp_selection(remote_entries, None, remote_selected)
        while True:
            draw_ftp_explorer(
                stdscr,
                local_path,
                local_entries,
                local_selected,
                remote_path,
                remote_entries,
                remote_selected,
                active_side,
                message,
                selected_paths=selected_paths | shift_selected_paths,
                move_buffer=move_buffer,
            )
            key = stdscr.getch()
            message = ""
            current_path, entries, selected = active_explorer_state(
                active_side, local_path, local_entries, local_selected, remote_path, remote_entries, remote_selected
            )

            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("\t"), curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")):
                active_side = "remote" if active_side == "local" else "local"
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                selected = min(len(entries) - 1, selected + 1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (getattr(curses, "KEY_SR", -1), ord("K")):
                if not shift_selecting:
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = True
                selected, shift_selected_paths = move_explorer_selection(active_side, current_path, entries, selected, shift_selected_paths, -1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key in (getattr(curses, "KEY_SF", -1), ord("J")):
                if not shift_selecting:
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = True
                selected, shift_selected_paths = move_explorer_selection(active_side, current_path, entries, selected, shift_selected_paths, 1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key == ord(" "):
                entry = entries[selected]
                if entry["name"] == "..":
                    message = "Cannot select parent entry."
                    continue
                path = explorer_join_path(active_side, current_path, entry["name"])
                selection_key = (active_side, path)
                if selection_key in selected_paths:
                    selected_paths.remove(selection_key)
                else:
                    selected_paths.add(selection_key)
                shift_selected_paths = set()
                shift_selecting = False
                continue
            if key in (ord("c"), ord("C"), 27):
                move_buffer = []
                message = "Move canceled."
                continue
            if key == ord("m"):
                items = selected_explorer_items(active_side, current_path, entries, selected, selected_paths | shift_selected_paths)
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
                        destination_dir = explorer_join_path(active_side, current_path, entry["name"])
                cross_pane = move_buffer and move_buffer[0][0] != active_side
                verb = "Copy" if cross_pane else "Move"
                description = f"{verb} {len(move_buffer)} item(s) to {destination_dir}?"
                if not prompt_ftp_confirmation(stdscr, description):
                    message = "Move canceled."
                    continue
                cleanup_dir = None
                try:
                    prepared_buffer, skip_archives, cleanup_dir = prepare_explorer_transfer_items(stdscr, move_buffer, active_side)
                    if prepared_buffer is None:
                        message = "Transfer canceled."
                        continue
                    draw_ftp_explorer(
                        stdscr,
                        local_path,
                        local_entries,
                        local_selected,
                        remote_path,
                        remote_entries,
                        remote_selected,
                        active_side,
                        message,
                        selected_paths=selected_paths | shift_selected_paths,
                        move_buffer=move_buffer,
                    )
                    progress = None
                    if prepared_buffer and prepared_buffer[0][0] != active_side:
                        progress = lambda label, sent, total, item_index, item_total: draw_transfer_progress(
                            stdscr,
                            label,
                            sent,
                            total,
                            item_index,
                            item_total,
                        )
                    moved = copy_or_move_explorer_items(
                        ftp,
                        prepared_buffer,
                        active_side,
                        destination_dir,
                        skip_archives=skip_archives,
                        progress=progress,
                    )
                    action = "Copied" if prepared_buffer and prepared_buffer[0][0] != active_side else "Moved"
                    message = f"{action} {len(moved)} item(s)."
                    move_buffer = []
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    break
                except ftplib.all_errors + (OSError, FTPTransferError) as exc:
                    message = f"Move failed: {exc}"
                    continue
                finally:
                    if cleanup_dir is not None:
                        shutil.rmtree(cleanup_dir, ignore_errors=True)
            if key == ord("d"):
                items = selected_explorer_items(active_side, current_path, entries, selected, selected_paths | shift_selected_paths)
                if not items:
                    message = "Select a file or directory to delete."
                    continue
                names = ", ".join(path for _, path, _ in items[:3])
                suffix = "..." if len(items) > 3 else ""
                description = f"Delete {len(items)} item(s): {names}{suffix}?"
                if not prompt_ftp_confirmation(stdscr, description):
                    message = "Delete canceled."
                    continue
                try:
                    for side, path, entry in items:
                        if side == "local":
                            delete_local_path(path, entry["type"])
                        else:
                            delete_ftp_path(ftp, path, entry["type"])
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    deleted_keys = {(side, path) for side, path, _ in items}
                    move_buffer = [(side, path, entry) for side, path, entry in move_buffer if (side, path) not in deleted_keys]
                    message = f"Deleted {len(items)} item(s)."
                    break
                except ftplib.all_errors + (OSError,) as exc:
                    message = f"Delete failed: {exc}"
                    continue
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if current_path == "/":
                    message = "Already at root."
                    continue
                if active_side == "local":
                    if local_path == local_root:
                        message = "Already at local start directory."
                        continue
                    local_path = join_local_explorer_path(local_path, "..", local_root)
                    local_selected = 0
                else:
                    remote_path = join_ftp_explorer_path(remote_path, "..")
                    remote_selected = 0
                selected_paths = set()
                shift_selected_paths = set()
                shift_selecting = False
                break
            if key in (curses.KEY_ENTER, 10, 13):
                entry = entries[selected]
                if entry["type"] == "dir":
                    if active_side == "local":
                        local_path = join_local_explorer_path(local_path, entry["name"], local_root)
                        local_selected = 0
                    else:
                        remote_path = join_ftp_explorer_path(remote_path, entry["name"])
                        remote_selected = 0
                    selected_paths = set()
                    shift_selected_paths = set()
                    shift_selecting = False
                    break
                message = "Selected file. Press m to stage it for moving."


def run_ftp_explorer(args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise FTPTransferError("FTP explorer requires an interactive terminal")

    local_start_path = validate_local_explorer_dir(getattr(args, "source", "."))
    remote_start_path = normalize_remote_path(getattr(args, "dest", "/"))
    host, port = resolve_ftp_host(args.host, args.port)
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user=args.user, passwd=args.password)
            curses.wrapper(ftp_explorer_loop, ftp, local_start_path, remote_start_path)
    except ftplib.all_errors + (OSError,) as exc:
        raise FTPTransferError(f"FTP explorer failed for {host}:{port}: {exc}") from exc
