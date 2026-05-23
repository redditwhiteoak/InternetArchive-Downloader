"""Entry point for IA Batch Downloader GUI."""


def _set_windows_app_user_model_id():
    """Make Windows use this app's own icon for taskbar/Start grouping.

    Without an explicit AppUserModelID, Tkinter apps can inherit the generic
    Python/Tk icon, which shows up as a feather in the taskbar even when the
    executable itself has the correct icon resource.
    """
    try:
        import sys
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "RedditWhiteOak.InternetArchiveDownloader.GUI"
            )
    except Exception:
        # Icon setup should never prevent the app from starting.
        pass


_set_windows_app_user_model_id()

from gui import main


if __name__ == "__main__":
    main()
