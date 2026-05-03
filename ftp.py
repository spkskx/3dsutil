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


class TransferCancelled(Exception):
    pass


COLOR_HEADER = 1
COLOR_ACTIVE = 2
COLOR_DIM = 3
COLOR_MARK = 4
COLOR_BORDER = 5
COLOR_ERROR = 6


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


def toggle_explorer_entry_selection(side, current_path, entries, selected, selected_paths):
    entry = entries[selected]
    if entry["name"] == "..":
        return selected_paths
    selection_key = (side, explorer_join_path(side, current_path, entry["name"]))
    if selection_key in selected_paths:
        selected_paths.remove(selection_key)
    else:
        selected_paths.add(selection_key)
    return selected_paths


def keep_explorer_marks_for_side(selected_paths, side):
    return {selection for selection in selected_paths if selection[0] == side}


def move_explorer_selection(side, current_path, entries, selected, selected_paths, direction):
    selected_paths = add_explorer_entry_to_selection(side, current_path, entries, selected, selected_paths)
    selected = max(0, min(len(entries) - 1, selected + direction))
    selected_paths = add_explorer_entry_to_selection(side, current_path, entries, selected, selected_paths)
    return selected, selected_paths


def move_explorer_selection_toggle(side, current_path, entries, selected, selected_paths, direction, toggle_start=True):
    previous_selected = selected
    if toggle_start:
        selected_paths = toggle_explorer_entry_selection(side, current_path, entries, selected, selected_paths)
    selected = max(0, min(len(entries) - 1, selected + direction))
    if selected != previous_selected:
        selected_paths = toggle_explorer_entry_selection(side, current_path, entries, selected, selected_paths)
    return selected, selected_paths


def restored_explorer_selection(entries, name, fallback):
    return restored_ftp_selection(entries, name, fallback)


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
        if entry["type"] == "dir" and (
            destination_path == source_path or destination_path.startswith(source_path.rstrip("/") + "/")
        ):
            raise FTPTransferError(f"cannot move a directory into itself: {source_path}")
        if destination_path == source_path:
            continue
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
        source_abs = os.path.abspath(source_path)
        destination_abs = os.path.abspath(destination)
        if entry["type"] == "dir" and (
            destination_abs == source_abs or destination_abs.startswith(source_abs + os.sep)
        ):
            raise FTPTransferError(f"cannot move a directory into itself: {source_path}")
        if destination_abs == source_abs:
            continue
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


def init_explorer_colors():
    if not curses.has_colors():
        return
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(COLOR_HEADER, curses.COLOR_CYAN, -1)
        curses.init_pair(COLOR_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(COLOR_DIM, curses.COLOR_BLUE, -1)
        curses.init_pair(COLOR_MARK, curses.COLOR_YELLOW, -1)
        curses.init_pair(COLOR_BORDER, curses.COLOR_BLUE, -1)
        curses.init_pair(COLOR_ERROR, curses.COLOR_RED, -1)
    except curses.error:
        pass


def ui_attr(color_pair=0, extra=0):
    attr = extra
    if color_pair:
        try:
            attr |= curses.color_pair(color_pair)
        except curses.error:
            pass
    return attr


def draw_box(stdscr, y, x, width, height, title="", active=False):
    if width < 2 or height < 2:
        return
    attr = ui_attr(COLOR_ACTIVE if active else COLOR_BORDER, curses.A_BOLD if active else curses.A_NORMAL)
    horizontal = "-" * max(0, width - 2)
    stdscr.addnstr(y, x, "+" + horizontal + "+", width, attr)
    for row in range(y + 1, y + height - 1):
        stdscr.addnstr(row, x, "|", 1, attr)
        stdscr.addnstr(row, x + width - 1, "|", 1, attr)
    stdscr.addnstr(y + height - 1, x, "+" + horizontal + "+", width, attr)
    if title:
        label = f" {title} "
        stdscr.addnstr(y, x + 2, truncate_text(label, max(1, width - 4)), max(1, width - 4), attr)


def fill_row(stdscr, y, x, width, attr=0):
    if width > 0:
        stdscr.addnstr(y, x, " " * width, width, attr)


def draw_shortcuts(stdscr, y, x, width, shortcuts):
    fill_row(stdscr, y, x, width, ui_attr(COLOR_DIM))
    cursor_x = x
    for keys, action in shortcuts:
        segment_width = len(keys) + len(action) + 3
        if cursor_x > x and cursor_x + segment_width >= x + width:
            break
        if cursor_x > x:
            stdscr.addnstr(y, cursor_x, "  ", max(0, min(2, x + width - cursor_x)), ui_attr(COLOR_DIM))
            cursor_x += 2
        if cursor_x >= x + width:
            break
        stdscr.addnstr(y, cursor_x, truncate_text(keys, x + width - cursor_x), x + width - cursor_x, ui_attr(COLOR_MARK, curses.A_BOLD))
        cursor_x += len(keys)
        if cursor_x >= x + width:
            break
        stdscr.addnstr(y, cursor_x, " ", 1, ui_attr(COLOR_DIM))
        cursor_x += 1
        if cursor_x >= x + width:
            break
        stdscr.addnstr(y, cursor_x, truncate_text(action, x + width - cursor_x), x + width - cursor_x, ui_attr(COLOR_DIM))
        cursor_x += len(action)


def draw_explorer_pane(stdscr, y, x, width, height, title, side, current_path, entries, selected, active, selected_paths):
    draw_box(stdscr, y, x, width, height, title, active=active)
    inner_x = x + 1
    inner_width = max(1, width - 2)
    stdscr.addnstr(y + 1, inner_x, truncate_text(current_path, inner_width), inner_width, ui_attr(COLOR_DIM, curses.A_DIM))
    visible_rows = max(1, height - 3)
    offset = max(0, selected - visible_rows + 1)
    for row, entry in enumerate(entries[offset: offset + visible_rows], start=y + 2):
        path = explorer_join_path(side, current_path, entry["name"])
        is_marked = (side, path) in selected_paths
        mark = "*" if is_marked else " "
        prefix = "[Dir]" if entry["type"] == "dir" else "     "
        label = f"{mark} {prefix} {ftp_entry_display_name(entry)}"
        is_cursor = active and offset + row - y - 2 == selected
        row_attr = ui_attr(COLOR_ACTIVE, curses.A_BOLD) if is_cursor else ui_attr(COLOR_MARK if is_marked else 0)
        fill_row(stdscr, row, inner_x, inner_width, row_attr)
        stdscr.addnstr(row, inner_x, truncate_text(label, inner_width), inner_width, row_attr)


def marked_explorer_summary(selected_paths, local_path, local_entries, remote_path, remote_entries):
    visible_types = {}
    for side, current_path, entries in (
        ("local", local_path, local_entries),
        ("remote", remote_path, remote_entries),
    ):
        for entry in entries:
            if entry["name"] == "..":
                continue
            visible_types[(side, explorer_join_path(side, current_path, entry["name"]))] = entry["type"]

    file_count = 0
    dir_count = 0
    unknown_count = 0
    for key in selected_paths:
        entry_type = visible_types.get(key)
        if entry_type == "file":
            file_count += 1
        elif entry_type == "dir":
            dir_count += 1
        else:
            unknown_count += 1

    parts = [f"{len(selected_paths)} item(s)"]
    if file_count or dir_count:
        parts.append(f"{file_count} file(s)")
        parts.append(f"{dir_count} dir(s)")
    if unknown_count:
        parts.append(f"{unknown_count} outside view")
    return ", ".join(parts)


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
):
    selected_paths = selected_paths or set()
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 20:
        stdscr.erase()
        stdscr.addnstr(0, 0, "Terminal too small", max(0, width - 1))
        stdscr.refresh()
        return

    stdscr.erase()
    fill_row(stdscr, 0, 0, width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    stdscr.addnstr(0, 0, " FTP Explorer: Local <-> 3DS", width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    draw_shortcuts(
        stdscr,
        1,
        0,
        width - 1,
        [
            ("Left/Right h/l", "switch pane"),
            ("Up/Down j/k", "move cursor"),
            ("Shift+Up/Down J/K", "range mark"),
            ("Space", "toggle mark"),
        ],
    )
    draw_shortcuts(
        stdscr,
        2,
        0,
        width - 1,
        [
            ("Enter", "open"),
            ("Backspace", "go up"),
            ("p", "paste"),
            ("d", "delete"),
            ("u", "unmark all"),
            ("q", "quit"),
        ],
    )

    pane_gap = 2
    pane_width = max(10, (width - pane_gap) // 2)
    pane_height = max(3, height - 6)
    draw_explorer_pane(stdscr, 4, 0, pane_width, pane_height, "Local", "local", local_path, local_entries, local_selected, active_side == "local", selected_paths)
    draw_explorer_pane(stdscr, 4, pane_width + pane_gap, max(10, width - pane_width - pane_gap - 1), pane_height, "3DS", "remote", remote_path, remote_entries, remote_selected, active_side == "remote", selected_paths)

    current_path, entries, selected = active_explorer_state(
        active_side, local_path, local_entries, local_selected, remote_path, remote_entries, remote_selected
    )
    cursor_entry = entries[selected] if entries else None
    cursor_name = ftp_entry_display_name(cursor_entry) if cursor_entry else "n/a"
    cursor_type = cursor_entry["type"] if cursor_entry else "n/a"
    marked_text = marked_explorer_summary(selected_paths, local_path, local_entries, remote_path, remote_entries)
    footer_text = (
        f"Cursor: {active_side} {cursor_type} {cursor_name} | "
        f"Marked: {marked_text} | p moves within a side, copies across sides"
    )
    if height > 1:
        fill_row(stdscr, height - 2, 0, width - 1, ui_attr(COLOR_DIM, curses.A_DIM))
        stdscr.addnstr(height - 2, 0, truncate_text(footer_text, width - 1), width - 1, ui_attr(COLOR_DIM, curses.A_DIM))
    if message and height > 0:
        message_attr = ui_attr(COLOR_ERROR if "failed" in message.lower() or "cannot" in message.lower() else COLOR_DIM, curses.A_DIM)
        fill_row(stdscr, height - 1, 0, width - 1, message_attr)
        stdscr.addnstr(height - 1, 0, truncate_text(message, width - 1), width - 1, message_attr)
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


def format_delete_target(side, path, entry):
    prefix = "local" if side == "local" else "3DS"
    entry_type = "dir" if entry["type"] == "dir" else "file"
    return f"{prefix} {entry_type}: {path}"


def prompt_delete_confirmation(stdscr, items):
    height, width = stdscr.getmaxyx()
    if height <= 0 or width <= 0:
        return False
    if height < 7 or width < 24:
        return prompt_ftp_confirmation(stdscr, f"Delete {len(items)} item(s)?")

    targets = [format_delete_target(side, path, entry) for side, path, entry in items]
    box_width = min(max(48, min(90, max([len(line) for line in targets] + [38]) + 4)), max(1, width - 2))
    box_height = min(max(8, min(height - 2, len(targets) + 5)), max(1, height - 2))
    top = max(0, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)
    inner_width = max(1, box_width - 4)
    list_height = max(1, box_height - 5)
    offset = 0

    while True:
        for row in range(box_height):
            stdscr.addnstr(top + row, left, " " * box_width, box_width, curses.A_REVERSE)

        stdscr.addnstr(top, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
        title = f"Delete {len(items)} item(s)?"
        stdscr.addnstr(top + 1, left, f"| {truncate_text(title, inner_width).ljust(inner_width)} |", box_width, curses.A_REVERSE)
        for index in range(list_height):
            target_index = offset + index
            if target_index < len(targets):
                line = targets[target_index]
            else:
                line = ""
            stdscr.addnstr(top + 2 + index, left, f"| {truncate_text(line, inner_width).ljust(inner_width)} |", box_width, curses.A_REVERSE)

        if len(targets) > list_height:
            footer = f"Up/Down scroll {offset + 1}-{min(offset + list_height, len(targets))}/{len(targets)}  y delete  n/Esc cancel"
        else:
            footer = "y delete  n/Esc cancel"
        stdscr.addnstr(top + box_height - 2, left, f"| {truncate_text(footer, inner_width).ljust(inner_width)} |", box_width, curses.A_REVERSE)
        stdscr.addnstr(top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+", box_width, curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), 27, 10, 13):
            return False
        if key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        if key in (curses.KEY_DOWN, ord("j")):
            offset = min(max(0, len(targets) - list_height), offset + 1)


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


def prompt_paste_destination(stdscr, current_dir, hover_dir=None):
    if hover_dir is None:
        return current_dir

    height, width = stdscr.getmaxyx()
    if height <= 0 or width <= 0:
        return None

    lines = [
        "Paste destination",
        "c: current directory",
        "h: cursor directory",
        "Esc: cancel",
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
        if key in (ord("c"), ord("C")):
            return current_dir
        if key in (ord("h"), ord("H")):
            return hover_dir
        if key in (27, ord("q"), ord("Q")):
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
    stdscr.addnstr(top + 5, left, f"| {'c/Esc/q cancels'.ljust(inner_width)} |", box_width, curses.A_REVERSE)
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


def explorer_items_from_marked_paths(ftp, marked_paths):
    items = []
    for side, path in sorted(marked_paths):
        if side == "local":
            entry_type = "dir" if os.path.isdir(path) else "file"
            name = os.path.basename(path.rstrip(os.sep))
        else:
            entry_type = "dir" if remote_is_dir(ftp, path) else "file"
            name = posixpath.basename(path.rstrip("/"))
        items.append((side, path, {"name": name, "type": entry_type}))
    return items


def explorer_operation_items(ftp, side, current_path, entries, selected, marked_paths):
    if marked_paths:
        return explorer_items_from_marked_paths(ftp, marked_paths)
    return selected_explorer_items(side, current_path, entries, selected, set())


def hover_explorer_directory(side, current_path, entries, selected):
    if not entries:
        return None
    entry = entries[selected]
    if entry["name"] == ".." or entry["type"] != "dir":
        return None
    return explorer_join_path(side, current_path, entry["name"])


def explorer_items_for_local_directory_contents(directory):
    items = []
    for entry in list_local_directory(directory, directory):
        if entry["name"] == "..":
            continue
        path = join_local_explorer_path(directory, entry["name"], directory)
        items.append(("local", path, entry))
    return items


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
    prepared = explorer_items_for_local_directory_contents(temp_dir)
    prepared.extend(item for item in move_buffer if not has_archive_sources(item[1]))
    return prepared, False, temp_dir


def ftp_explorer_loop(stdscr, ftp, local_start_path=None, remote_start_path="/"):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    init_explorer_colors()
    local_root = validate_local_explorer_dir(local_start_path or ".")
    local_path = local_root
    remote_path = normalize_remote_path(remote_start_path)
    local_selected = 0
    remote_selected = 0
    active_side = "local"
    message = ""
    local_entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    remote_entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
    marked_paths = set()
    shift_marking_active = False
    restore_local_selection_name = None
    restore_remote_selection_name = None

    while True:
        try:
            local_entries = list_local_directory(local_path, local_root)
            remote_entries = list_ftp_directory(ftp, remote_path)
            message = ""
        except ftplib.all_errors + (OSError,) as exc:
            message = f"Could not list directory: {exc}"
        local_selected = restored_explorer_selection(local_entries, restore_local_selection_name, local_selected)
        remote_selected = restored_explorer_selection(remote_entries, restore_remote_selection_name, remote_selected)
        restore_local_selection_name = None
        restore_remote_selection_name = None
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
                selected_paths=marked_paths,
            )
            key = stdscr.getch()
            message = ""
            current_path, entries, selected = active_explorer_state(
                active_side, local_path, local_entries, local_selected, remote_path, remote_entries, remote_selected
            )

            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")):
                shift_marking_active = False
                active_side = "remote" if active_side == "local" else "local"
                continue
            if key in (curses.KEY_UP, ord("k")):
                shift_marking_active = False
                selected = max(0, selected - 1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                shift_marking_active = False
                selected = min(len(entries) - 1, selected + 1)
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key in (getattr(curses, "KEY_SR", -1), ord("K")):
                marked_paths = keep_explorer_marks_for_side(marked_paths, active_side)
                selected, marked_paths = move_explorer_selection_toggle(
                    active_side,
                    current_path,
                    entries,
                    selected,
                    marked_paths,
                    -1,
                    toggle_start=not shift_marking_active,
                )
                shift_marking_active = True
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key in (getattr(curses, "KEY_SF", -1), ord("J")):
                marked_paths = keep_explorer_marks_for_side(marked_paths, active_side)
                selected, marked_paths = move_explorer_selection_toggle(
                    active_side,
                    current_path,
                    entries,
                    selected,
                    marked_paths,
                    1,
                    toggle_start=not shift_marking_active,
                )
                shift_marking_active = True
                if active_side == "local":
                    local_selected = selected
                else:
                    remote_selected = selected
                continue
            if key == ord(" "):
                shift_marking_active = False
                entry = entries[selected]
                if entry["name"] == "..":
                    message = "Cannot select parent entry."
                    continue
                marked_paths = keep_explorer_marks_for_side(marked_paths, active_side)
                path = explorer_join_path(active_side, current_path, entry["name"])
                selection_key = (active_side, path)
                if selection_key in marked_paths:
                    marked_paths.remove(selection_key)
                else:
                    marked_paths.add(selection_key)
                continue
            if key in (ord("u"), ord("U"), ord("c"), ord("C"), 27):
                shift_marking_active = False
                marked_paths = set()
                message = "Marks cleared."
                continue
            if key == ord("p"):
                shift_marking_active = False
                operation_items = explorer_operation_items(ftp, active_side, current_path, entries, selected, marked_paths)
                if not operation_items:
                    message = "Move the cursor to a file or directory, or mark item(s) first."
                    continue
                operation_sides = {side for side, _, _ in operation_items}
                if len(operation_sides) > 1:
                    message = "Marked items must all be on the same side."
                    continue
                destination_dir = current_path
                hover_dir = hover_explorer_directory(active_side, current_path, entries, selected)
                destination_dir = prompt_paste_destination(stdscr, current_path, hover_dir)
                if destination_dir is None:
                    message = "Paste canceled."
                    continue
                cross_pane = operation_items and operation_items[0][0] != active_side
                verb = "Copy" if cross_pane else "Move"
                description = f"{verb} {len(operation_items)} item(s) to {destination_dir}?"
                if not prompt_ftp_confirmation(stdscr, description):
                    message = "Paste canceled."
                    continue
                cleanup_dir = None
                previous_nodelay = False
                try:
                    prepared_buffer, skip_archives, cleanup_dir = prepare_explorer_transfer_items(stdscr, operation_items, active_side)
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
                        selected_paths=marked_paths,
                    )
                    progress = None
                    if prepared_buffer and prepared_buffer[0][0] != active_side:
                        try:
                            stdscr.nodelay(True)
                            previous_nodelay = True
                        except curses.error:
                            pass

                        last_progress_drawn = [0.0]

                        def progress(label, sent, total, item_index, item_total):
                            now = time.monotonic()
                            key = stdscr.getch()
                            if key in (ord("c"), ord("C"), ord("q"), ord("Q"), 27):
                                raise TransferCancelled()
                            if sent == total or now - last_progress_drawn[0] >= 0.2:
                                draw_transfer_progress(stdscr, label, sent, total, item_index, item_total)
                                last_progress_drawn[0] = now
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
                    marked_paths = set()
                    break
                except TransferCancelled:
                    message = "Transfer canceled."
                    break
                except ftplib.all_errors + (OSError, FTPTransferError) as exc:
                    message = f"Paste failed: {exc}"
                    continue
                finally:
                    if previous_nodelay:
                        try:
                            stdscr.nodelay(False)
                        except curses.error:
                            pass
                    if cleanup_dir is not None:
                        shutil.rmtree(cleanup_dir, ignore_errors=True)
            if key == ord("d"):
                shift_marking_active = False
                items = explorer_operation_items(ftp, active_side, current_path, entries, selected, marked_paths)
                if not items:
                    message = "Cannot delete go up entry. Move the cursor to a file or directory, or mark item(s) first."
                    continue
                if not prompt_delete_confirmation(stdscr, items):
                    message = "Delete canceled."
                    continue
                try:
                    for side, path, entry in items:
                        if side == "local":
                            delete_local_path(path, entry["type"])
                        else:
                            delete_ftp_path(ftp, path, entry["type"])
                    marked_paths = set()
                    message = f"Deleted {len(items)} item(s)."
                    break
                except ftplib.all_errors + (OSError,) as exc:
                    message = f"Delete failed: {exc}"
                    continue
            if key in (curses.KEY_BACKSPACE, 8, 127):
                shift_marking_active = False
                if current_path == "/":
                    message = "Already at root."
                    continue
                if active_side == "local":
                    if local_path == local_root:
                        message = "Already at local start directory."
                        continue
                    previous_path = local_path
                    local_path = join_local_explorer_path(local_path, "..", local_root)
                    restore_local_selection_name = os.path.basename(previous_path.rstrip(os.sep))
                else:
                    previous_path = remote_path
                    remote_path = join_ftp_explorer_path(remote_path, "..")
                    restore_remote_selection_name = posixpath.basename(previous_path.rstrip("/"))
                break
            if key in (curses.KEY_ENTER, 10, 13):
                shift_marking_active = False
                entry = entries[selected]
                if entry["type"] == "dir":
                    if active_side == "local":
                        previous_path = local_path
                        local_path = join_local_explorer_path(local_path, entry["name"], local_root)
                        if entry["name"] == "..":
                            restore_local_selection_name = os.path.basename(previous_path.rstrip(os.sep))
                        else:
                            local_selected = 0
                    else:
                        previous_path = remote_path
                        remote_path = join_ftp_explorer_path(remote_path, entry["name"])
                        if entry["name"] == "..":
                            restore_remote_selection_name = posixpath.basename(previous_path.rstrip("/"))
                        else:
                            remote_selected = 0
                    break
                message = "Cursor is on a file. Press Space to mark it, or p/d to operate on it."


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
