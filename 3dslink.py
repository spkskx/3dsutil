import importlib

_module = importlib.import_module("3dsutil")
globals().update({name: value for name, value in vars(_module).items() if not name.startswith("__")})


if __name__ == "__main__":
    raise SystemExit(main())
