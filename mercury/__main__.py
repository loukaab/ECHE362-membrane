"""Launch with python -m mercury from the repository root."""

import sys


def main() -> int:
    try:
        from .runtime import ensure_tk
        ensure_tk()
        from .gui.main_window import launch
        launch()
        return 0
    except (ImportError, RuntimeError, OSError, ValueError) as error:
        print(f"Mercury could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
