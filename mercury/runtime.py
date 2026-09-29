"""Use system Tk first; optionally use matching locally extracted test bindings."""

import ctypes
import importlib
import os
from pathlib import Path
import sys


def ensure_tk() -> None:
    try:
        importlib.import_module("tkinter")
        return
    except ImportError as original:
        runtime = Path(__file__).resolve().parent / ".runtime" / "usr"
        python_lib = runtime / "lib64" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        libraries = sorted((runtime / "lib64").glob("libtcl*tk*.so"))
        if not python_lib.is_dir() or not libraries:
            raise RuntimeError(
                "Tkinter is unavailable in this Python interpreter. Install matching OS Tk bindings "
                "(Fedora: sudo dnf install python3-tkinter; Debian/Ubuntu: sudo apt install python3-tk), "
                "then retry with .venv/bin/python -m mercury. Tkinter is not installed through pip."
            ) from original
        ctypes.CDLL(str(libraries[0]), mode=ctypes.RTLD_GLOBAL)
        sys.path[:0] = [str(python_lib), str(python_lib / "lib-dynload")]
        tk_dirs = sorted((runtime / "share").glob("tk[0-9]*"))
        if tk_dirs:
            os.environ.setdefault("TK_LIBRARY", str(tk_dirs[0]))
        importlib.invalidate_caches()
        importlib.import_module("tkinter")
