# Contributing

Thanks for helping improve `3dsutil`. This project is intentionally small and dependency-free, so changes should stay focused and easy to review.

## Development

Use Python 3 and the standard library only unless a dependency is clearly justified.

Useful commands:

```bash
python3 3dslink.py --help
python3 -m unittest discover -s tests -v
```

## Before Opening a Pull Request

- Keep CLI-facing errors concise and actionable.
- Add or update tests for argument parsing, validation, socket handling, protocol buffers, and user-visible behavior.
- Avoid changes that require a real 3DS or live network access in tests.
- Update `README.md` when commands, options, or workflows change.
- Run `python3 -m unittest discover -s tests -v`.

## Pull Requests

Use a short, imperative title such as `Add FTP upload validation`. Include a summary, test results, and any user-visible CLI or README changes.
