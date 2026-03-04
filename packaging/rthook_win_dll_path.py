"""PyInstaller runtime hook: prepend _MEIPASS to PATH on Windows.

TensorFlow's self_check.py verifies its required DLLs (msvcp140_1.dll etc.)
via ctypes.WinDLL(), which searches %PATH% — not sys.path. With --onefile,
the DLLs are extracted to _MEIPASS but that directory isn't in PATH, causing
the check to fail even though the DLLs are present in the bundle.
"""
import os
import sys

if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")
