"""Application path helpers."""

import os
import sys


def get_app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_settings_path():
    return os.path.join(get_app_base_dir(), "ia_downloader_settings.json")


def get_history_path():
    return os.path.join(get_app_base_dir(), "ia_downloader_history.json")


def get_log_path():
    return os.path.join(get_app_base_dir(), "ia_downloader.log")


def get_autosave_path():
    return os.path.join(get_app_base_dir(), "ia_downloader_autosave_state.json")


def get_resource_path(*parts):
    """Return a path to bundled read-only resources.

    In normal Python runs, resources live next to the source files.
    In a PyInstaller one-file executable, resources are unpacked to
    sys._MEIPASS, so GUI assets like the logo must be loaded from there.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, *parts)
