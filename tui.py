import curses
import ftplib
import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

from core import (
    DEFAULT_DISCOVERY_RETRIES,
    DEFAULT_FTP_PORT,
    DEFAULT_NETLOADER_PORT,
    DEFAULT_TIMEOUT,
    DiscoveryError,
    FTPTransferError,
    NetloaderError,
    parse_host_port,
    resolve_host,
)
from ftp import (
    COLOR_ACTIVE,
    COLOR_DIM,
    COLOR_ERROR,
    COLOR_HEADER,
    draw_box,
    draw_explorer_pane,
    draw_shortcuts,
    fill_row,
    ftp_explorer_loop,
    init_explorer_colors,
    join_local_explorer_path,
    list_local_directory,
    restored_explorer_selection,
    truncate_text,
    ui_attr,
    validate_local_explorer_dir,
)
from netloader import discover_3ds_hosts, send_3dsx, validate_netloader_file


APP_OVERVIEW = [
    "Version 1.3",
    "Wirelessly launch .3dsx homebrew with NetLoader or browse files through a 3DS FTP server.",
    "Keep your computer and 3DS on the same network. Use arrow keys or j/k, Enter to select, q/Esc to go back.",
]
NETLOADER_OVERVIEW = [
    "Load one .3dsx through Homebrew Launcher NetLoader.",
    "On the 3DS, open Homebrew Launcher and press Y before loading.",
    "Scanning uses UDP discovery only. The TUI does not open a NetLoader TCP connection until a file is selected.",
]
FTP_OVERVIEW = [
    "Browse local files and a 3DS FTP server side by side.",
    "Start ftpd on the 3DS first, then enter its IP address or host:port here.",
    "Use the explorer to copy, move, delete, upload, download, and optionally unarchive files.",
]
NETLOADER_HELP = "Start NetLoader on the 3DS: Homebrew Launcher, then press Y. Check Wi-Fi/network connection too."


def is_netloader_file_candidate(path):
    if os.path.isdir(path):
        return False
    try:
        validate_netloader_file(path)
    except NetloaderError:
        return False
    return True


def parse_tui_host_port(value, default_port, label):
    host, port = parse_host_port(value.strip(), default_port, label)
    if not host:
        raise NetloaderError(f"{label} host is required")
    return resolve_host(host, port), port


def parse_tui_host_entry(value, default_port, label):
    value = value.strip()
    host, port = parse_host_port(value, default_port, label)
    if not host:
        raise NetloaderError(f"{label} host is required")
    return host, port, ":" in value


def validate_tui_port(value, label):
    try:
        port = int(value.strip())
    except ValueError as exc:
        raise NetloaderError(f"{label} port must be a number") from exc
    if not 1 <= port <= 65535:
        raise NetloaderError(f"{label} port must be between 1 and 65535")
    return port


def prompt_tui_target(stdscr, title, label, default_port):
    host_value = prompt_text(
        stdscr,
        f"{title} Host",
        f"Host or host:port (example: 172.20.10.12 or 172.20.10.12:{default_port})",
    )
    if not host_value:
        return None, ""
    try:
        host, parsed_port, has_port = parse_tui_host_entry(host_value, default_port, label)
        if has_port:
            return (resolve_host(host, parsed_port), parsed_port), ""

        port_value = prompt_text(
            stdscr,
            f"{title} Port",
            f"Port (example: {default_port})",
            str(default_port),
        )
        if port_value is None:
            return None, ""
        if not port_value:
            return None, f"{label} port is required"
        port = validate_tui_port(port_value, label)
        return (resolve_host(host, port), port), ""
    except NetloaderError as exc:
        return None, str(exc)


def draw_menu(stdscr, title, options, selected, message="", description=None):
    description = description or []
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    if height < 8 or width < 32:
        stdscr.addnstr(0, 0, "Terminal too small", max(0, width - 1))
        stdscr.refresh()
        return

    fill_row(stdscr, 0, 0, width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    stdscr.addnstr(0, 0, f" {title}", width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    inner_width = max(1, width - 5)
    row = 2
    for line in description:
        if row >= height - 6:
            break
        stdscr.addnstr(row, 2, truncate_text(line, inner_width), inner_width, ui_attr(COLOR_DIM))
        row += 1
    if description:
        row += 1

    box_top = row
    box_height = max(4, height - box_top - 3)
    draw_box(stdscr, box_top, 2, width - 5, box_height, "Select", active=True)
    option_width = max(1, width - 9)
    row = box_top + 2
    for index, option in enumerate(options):
        if row >= box_top + box_height - 1:
            break
        attr = ui_attr(COLOR_ACTIVE, curses.A_BOLD) if index == selected else 0
        fill_row(stdscr, row, 4, option_width, attr)
        stdscr.addnstr(row, 4, truncate_text(option, option_width), option_width, attr)
        row += 1

    footer = "Up/Down j/k move  Enter select  q/Esc back"
    fill_row(stdscr, height - 2, 0, width - 1, ui_attr(COLOR_DIM, curses.A_DIM))
    stdscr.addnstr(height - 2, 0, truncate_text(footer, width - 1), width - 1, ui_attr(COLOR_DIM, curses.A_DIM))
    if message:
        lower_message = message.lower()
        color = COLOR_ERROR if "failed" in lower_message or "error" in lower_message or "cannot" in lower_message else COLOR_DIM
        attr = ui_attr(color, curses.A_DIM)
        fill_row(stdscr, height - 1, 0, width - 1, attr)
        stdscr.addnstr(height - 1, 0, truncate_text(message, width - 1), width - 1, attr)
    stdscr.refresh()


def menu_loop(stdscr, title, options, message="", description=None):
    selected = 0
    while True:
        draw_menu(stdscr, title, options, selected, message, description=description)
        message = ""
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(len(options) - 1, selected + 1)
            continue
        if key in (curses.KEY_ENTER, 10, 13):
            return selected


def prompt_text(stdscr, title, prompt, default=""):
    height, width = stdscr.getmaxyx()
    value = default
    cursor = len(value)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            height, width = stdscr.getmaxyx()
            input_width = max(1, width - 9)
            stdscr.erase()
            fill_row(stdscr, 0, 0, width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
            stdscr.addnstr(0, 0, f" {title}", width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
            draw_box(stdscr, 2, 2, width - 5, 7, prompt, active=True)
            display_value = value[-input_width:]
            fill_row(stdscr, 4, 4, input_width)
            stdscr.addnstr(4, 4, display_value, input_width)
            hint = "Enter accept  Esc back"
            stdscr.addnstr(6, 4, truncate_text(hint, input_width), input_width, ui_attr(COLOR_DIM, curses.A_DIM))
            cursor_x = 4 + min(cursor, input_width - 1)
            stdscr.move(4, cursor_x)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (27,):
                return None
            if key in (curses.KEY_ENTER, 10, 13):
                return value.strip()
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if cursor > 0:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
                continue
            if key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
                continue
            if key == curses.KEY_RIGHT:
                cursor = min(len(value), cursor + 1)
                continue
            if 32 <= key <= 126:
                character = chr(key)
                value = value[:cursor] + character + value[cursor:]
                cursor += 1
    finally:
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def show_message(stdscr, title, message, description=None):
    menu_loop(stdscr, title, ["Back"], message=message, description=description)


def show_popup(stdscr, title, message, wait=True):
    height, width = stdscr.getmaxyx()
    while True:
        stdscr.erase()
        if height < 8 or width < 32:
            stdscr.addnstr(0, 0, truncate_text(message, max(0, width - 1)), max(0, width - 1))
            stdscr.refresh()
        else:
            fill_row(stdscr, 0, 0, width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
            stdscr.addnstr(0, 0, f" {title}", width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
            box_width = min(max(44, len(message) + 4), max(1, width - 4))
            box_height = 7
            top = max(1, (height - box_height) // 2)
            left = max(0, (width - box_width) // 2)
            draw_box(stdscr, top, left, box_width, box_height, title, active=True)
            inner_width = max(1, box_width - 4)
            stdscr.addnstr(top + 2, left + 2, truncate_text(message, inner_width), inner_width)
            footer = "Enter/c/q/Esc back to home"
            stdscr.addnstr(top + 4, left + 2, truncate_text(footer, inner_width), inner_width, ui_attr(COLOR_DIM, curses.A_DIM))
            stdscr.refresh()

        if not wait:
            return
        key = stdscr.getch()
        if key in (curses.KEY_ENTER, 10, 13, ord("c"), ord("C"), ord("q"), ord("Q"), 27):
            return


def scan_netloader_devices(stdscr):
    draw_menu(
        stdscr,
        "NetLoader",
        ["Scanning for NetLoader devices..."],
        0,
        "Open Homebrew Launcher and press Y.",
        description=NETLOADER_OVERVIEW,
    )
    try:
        return discover_3ds_hosts(DEFAULT_NETLOADER_PORT, DEFAULT_DISCOVERY_RETRIES, 1.0), ""
    except DiscoveryError as exc:
        return [], str(exc)


def choose_scanned_netloader_target(stdscr):
    hosts, message = scan_netloader_devices(stdscr)
    if not hosts:
        return None, message

    while True:
        options = [f"{host}:{DEFAULT_NETLOADER_PORT}" for host in hosts]
        options.append("Back")
        choice = menu_loop(stdscr, "NetLoader Device", options, message=message, description=NETLOADER_OVERVIEW)
        message = ""
        if choice is None or choice == len(options) - 1:
            return None, ""
        if choice < len(hosts):
            return (hosts[choice], DEFAULT_NETLOADER_PORT), ""


def prompt_netloader_custom_target(stdscr):
    return prompt_tui_target(stdscr, "NetLoader Custom", "NetLoader", DEFAULT_NETLOADER_PORT)


def choose_netloader_target(stdscr):
    message = ""
    while True:
        choice = menu_loop(
            stdscr,
            "NetLoader",
            [
                "Scan network for NetLoader",
                "Enter custom host and port",
                "Back",
            ],
            message=message,
            description=NETLOADER_OVERVIEW,
        )
        message = ""
        if choice is None or choice == 2:
            return None
        if choice == 0:
            target, message = choose_scanned_netloader_target(stdscr)
            if target is not None:
                return target
            continue
        try:
            target, message = prompt_netloader_custom_target(stdscr)
        except NetloaderError as exc:
            target, message = None, str(exc)
        if target is not None:
            return target


def draw_local_file_picker(stdscr, current_path, entries, selected, message=""):
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 20:
        stdscr.erase()
        stdscr.addnstr(0, 0, "Terminal too small", max(0, width - 1))
        stdscr.refresh()
        return

    stdscr.erase()
    fill_row(stdscr, 0, 0, width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    stdscr.addnstr(0, 0, " NetLoader File Picker", width - 1, ui_attr(COLOR_HEADER, curses.A_BOLD))
    draw_shortcuts(
        stdscr,
        1,
        0,
        width - 1,
        [
            ("Up/Down j/k", "move cursor"),
            ("Enter", "open/select"),
            ("Backspace", "go up"),
            ("q", "back"),
        ],
    )
    draw_explorer_pane(
        stdscr,
        3,
        0,
        width - 1,
        max(3, height - 5),
        "Local",
        "local",
        current_path,
        entries,
        selected,
        True,
        set(),
    )
    if message and height > 0:
        lower_message = message.lower()
        color = COLOR_ERROR if "failed" in lower_message or "error" in lower_message or "only supports" in lower_message else COLOR_DIM
        attr = ui_attr(color, curses.A_DIM)
        fill_row(stdscr, height - 1, 0, width - 1, attr)
        stdscr.addnstr(height - 1, 0, truncate_text(message, width - 1), width - 1, attr)
    stdscr.refresh()


def choose_netloader_file(stdscr, start_path="."):
    local_root = validate_local_explorer_dir(start_path)
    local_path = local_root
    selected = 0
    restore_name = None
    message = "Choose one .3dsx file to load."
    while True:
        try:
            entries = list_local_directory(local_path, local_root)
        except OSError as exc:
            entries = [{"name": "..", "type": "dir", "size": None, "modify": None}]
            message = f"Could not list directory: {exc}"
        selected = restored_explorer_selection(entries, restore_name, selected)
        restore_name = None
        draw_local_file_picker(stdscr, local_path, entries, selected, message)
        message = ""
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(len(entries) - 1, selected + 1)
            continue
        if key in (curses.KEY_BACKSPACE, 8, 127):
            if local_path == local_root:
                message = "Already at local start directory."
                continue
            previous = local_path
            local_path = join_local_explorer_path(local_path, "..", local_root)
            restore_name = os.path.basename(previous.rstrip(os.sep))
            continue
        if key not in (curses.KEY_ENTER, 10, 13):
            continue

        entry = entries[selected]
        path = join_local_explorer_path(local_path, entry["name"], local_root)
        if entry["type"] == "dir":
            previous = local_path
            local_path = path
            if entry["name"] == "..":
                restore_name = os.path.basename(previous.rstrip(os.sep))
            else:
                selected = 0
            continue
        if is_netloader_file_candidate(path):
            return path
        message = "NetLoader only supports .3dsx files."


def run_netloader_tui(stdscr):
    while True:
        target = choose_netloader_target(stdscr)
        if target is None:
            return
        host, port = target
        path = choose_netloader_file(stdscr, ".")
        if path is None:
            continue
        try:
            show_popup(stdscr, "NetLoader", f"Loading {os.path.basename(path)}. Processing...", wait=False)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                send_3dsx(host, port, path)
        except NetloaderError as exc:
            show_popup(stdscr, "NetLoader", f"Load failed: {exc}. {NETLOADER_HELP}")
            continue
        show_popup(stdscr, "NetLoader", f"Loaded {os.path.basename(path)} successfully.")
        return


def run_ftp_tui(stdscr):
    draw_menu(stdscr, "FTP", ["Enter FTP host..."], 0, description=FTP_OVERVIEW)
    target, message = prompt_tui_target(stdscr, "FTP", "FTP", DEFAULT_FTP_PORT)
    if target is None and not message:
        return
    if target is None:
        show_popup(stdscr, "FTP", f"FTP failed: {message}")
        return
    try:
        host, port = target
        local_start = validate_local_explorer_dir(".")
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=DEFAULT_TIMEOUT)
            ftp.set_pasv(True)
            ftp.login(user="anonymous", passwd="")
            ftp_explorer_loop(stdscr, ftp, local_start, "/")
        return
    except ftplib.all_errors + (NetloaderError, FTPTransferError, OSError) as exc:
        show_popup(stdscr, "FTP", f"FTP failed: {exc}")
        return


def run_update_tui(stdscr, args, update_runner):
    if update_runner is None:
        show_popup(stdscr, "Update", "Update is only available from the installed 3dsutil command.")
        return
    show_popup(stdscr, "Update", "Updating 3dsutil. Processing...", wait=False)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            update_runner(args.update_args)
    except (subprocess.CalledProcessError, NetloaderError) as exc:
        show_popup(stdscr, "Update", f"Update failed: {exc}")
        return
    show_popup(stdscr, "Update", "3dsutil updated successfully.")


def tui_loop(stdscr, args=None, update_runner=None):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        curses.set_escdelay(25)
    except curses.error:
        pass
    init_explorer_colors()

    while True:
        options = [
            "NetLoader - load .3dsx via NetLoader",
            "FTP - browse and transfer files",
        ]
        update_index = None
        if getattr(args, "show_update", False):
            update_index = len(options)
            options.append("Update 3dsutil")
        options.append("Quit")

        choice = menu_loop(
            stdscr,
            "3dsutil v1.3",
            options,
            description=APP_OVERVIEW,
        )
        if choice is None or choice == len(options) - 1:
            return
        if choice == 0:
            run_netloader_tui(stdscr)
        elif choice == 1:
            run_ftp_tui(stdscr)
        elif update_index is not None and choice == update_index:
            run_update_tui(stdscr, args, update_runner)


def run_tui(args=None, update_runner=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise NetloaderError("interactive TUI requires an interactive terminal")
    curses.wrapper(tui_loop, args, update_runner)
