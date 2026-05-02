# Repository Guidelines

## Project Structure & Module Organization

This repository is intentionally small. The main utility lives in `3dslink.py` and contains the CLI, UDP discovery, input validation, compression, and NetLoader transfer logic. Tests live in `tests/test_3dslink.py` and import the script as the `3dslink` module. GitHub Actions configuration is in `.github/workflows/ci.yml`. User-facing usage and troubleshooting details belong in `README.md`.

## Build, Test, and Development Commands

- `python3 3dslink.py --help`: show CLI arguments and verify the script starts.
- `python3 3dslink.py netloader load --host 172.20.10.12 sample-app.3dsx`: send a `.3dsx` file to a known 3DS NetLoader address.
- `python3 3dslink.py netloader status --host 172.20.10.12`: check whether NetLoader is reachable.
- `python3 3dslink.py ftp upload --host 172.20.10.12 sample-app.3dsx`: upload a `.3dsx` file to a 3DS FTP server such as ftpd.
- `python3 3dslink.py ftp status --host 172.20.10.12`: check whether ftpd is reachable and accepts login.
- `python3 -m unittest discover -s tests -v`: run the full local test suite.

There is no build step and no runtime dependency installation. Keep the project usable with the Python standard library unless a dependency is clearly justified.

## Coding Style & Naming Conventions

Use standard Python 3 style with 4-space indentation. Prefer small, testable functions over adding behavior directly in `main()`. Use `snake_case` for functions and variables, `UPPER_CASE` for constants, and descriptive exception classes such as `DiscoveryError`. Keep CLI-facing errors concise and actionable because they are printed directly to users.

When editing protocol code, preserve explicit byte order and encoding choices, for example `struct.pack('<I', value)` and UTF-8 path encoding.

## Testing Guidelines

Tests use the standard `unittest` framework with `unittest.mock`; do not introduce pytest-only patterns unless the project explicitly adopts pytest. Name test classes after the behavior under test, such as `ParseArgsTests`, and name methods with `test_...` plus the expected behavior. Add tests for argument parsing, validation failures, socket error handling, and protocol buffer changes. Avoid tests that require a real 3DS or live network access; mock sockets and file objects instead.

## Commit & Pull Request Guidelines

The current history uses short, imperative commit subjects, for example `Add initial implementation...` and `Refactor README.md...`. Follow that style: start with a verb and describe the changed behavior or documentation.

Pull requests should include a brief summary, test results such as `python3 -m unittest discover -s tests -v`, and any user-visible CLI or README changes. Link related issues when available. Screenshots are not needed for normal code changes.

## Security & Configuration Tips

Do not commit sample `.3dsx` binaries, local IP addresses as defaults, or machine-specific configuration. Keep network timeouts bounded and report socket errors without dumping sensitive local environment details.
