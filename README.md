# IA Batch Downloader GUI

A desktop GUI for large Internet Archive batch downloads using Python and the `internetarchive` package.

GitHub repository: https://github.com/redditwhiteoak/InternetArchive-Downloader

## What it does

- Downloads large Internet Archive item/file lists in batches.
- Starts downloading as soon as the first downloadable files are found while later URLs continue scanning.
- Keeps queue priority in the same order as the URL list and the files discovered for each URL.
- Supports multiple downloads at once with a configurable limit.
- Shows live active downloads, full queue status, total discovered size, remaining download size, speed, and ETA.
- Checks available disk space and can stop before the destination drive runs out of room.
- Detects existing files and optional `.part` resume files.
- Handles `.part` prompts without blocking the rest of the queue.
- Saves and loads portable queue/state files.
- Can retry failed downloads.

## Current behavior notes

### Queue order

The downloader processes files in the order they are discovered from the URL list:

```text
URL 1 files → URL 2 files → URL 3 files → ...
```

When a download slot opens, the next file is pulled from the earliest URL that still has ready files. Later URLs should not jump ahead of earlier unfinished URLs.

### Streaming queue build

You do not need to wait for every URL to finish scanning before downloads begin. Once files are discovered, downloads can start while the remaining URLs continue building the queue.

### Active Downloads table

The Active Downloads table is intended to show only files currently downloading. Completed, failed, skipped, or queued files are shown in the separate Downloads Table window.

### Queue size display

The total queue size updates while scanning is still running. When scanning finishes, the status should stop saying “still scanning.”

### `.part` files

If a partial `.part` file is found, the app can ask whether to resume or start over. That prompt is non-blocking, so unrelated queue processing can continue while you respond.

## Main controls

### Start

Starts or resumes the queue. If a refresh/scan has not already been completed, Start will begin queue discovery and start downloads as files become available.

### Pause

Temporarily pauses active downloads/checks without clearing queue state.

### Stop

Stops current operations while preserving resumable state.

### Refresh Queue + Disk

Refreshes the queue display, discovered/remaining queue size, and free disk-space information. This can be used while downloads are running.

### Reset Inputs

Clears the visible input fields/settings back to defaults. This is for resetting the form; it is not meant to delete downloaded files.

### Clear Downloads view

Clears displayed download information in the view only. It should not delete actual downloaded files or remove underlying queue/history data.

## Downloads Table window

The Downloads Table shows all known rows and supports filtering by:

- All
- Active
- Queued
- Done
- Skipped
- Failed

When the table has many rows, the active-download view should avoid jumping down the list just because later rows update.

## Installation from source

Python 3.10+ is recommended.

```bash
pip install -r requirements.txt
```

Then run:

```bash
python main.py
```

On Windows, this usually also works:

```bat
py main.py
```

## Building a Windows `.exe`

A helper script is included:

```bat
build_windows_exe.bat
```

Run it from the project folder on a Windows PC. It installs/uses PyInstaller and builds:

> Note: the PyInstaller spec uses `Path.cwd()` because PyInstaller does not define `__file__` while executing spec files. Always run `build_windows_exe.bat` from the project folder.

```text
dist/IA_Batch_Downloader.exe
```

The `.exe` should be tested on the Windows machine where you plan to use it, especially if you rely on a configured Internet Archive login file.

### Logo/icon in the compiled `.exe`

The PyInstaller spec bundles both files below into the one-file executable:

```text
assets/internet_archive_downloader_logo.png
assets/internet_archive_downloader_logo.ico
```

Build from the project folder with `build_windows_exe.bat`. The app now loads bundled assets from PyInstaller's temporary resource folder, so the header logo and window icon should appear even when only `dist/IA_Batch_Downloader.exe` is copied to another folder.


## Project files

```text
main.py                         App entry point
gui.py                          Tkinter GUI and UI state logic
downloader.py                   File download/resume helpers
scheduler.py                    Queue scheduling helpers
models.py                       Shared data models
app_paths.py                    Settings/log/autosave path helpers
assets/                         Logo/icon files
examples/sample_queue.json      Example portable queue file
docs/                           Extra documentation folder
build_windows_exe.bat           Windows PyInstaller build helper
IA_Batch_Downloader.spec        PyInstaller build configuration
```

## Local files that should not be committed

The app may create local files such as:

```text
ia_downloader_settings.json
ia_downloader_history.json
ia_downloader_autosave_state.json
ia_downloader.log
*.part
__pycache__/
build/
dist/
```

These should stay out of GitHub.

## Notes

- Pause temporarily freezes activity.
- Stop preserves resume state.
- Closing the application autosaves current state.
- Use the Help menu for the GitHub repository link.
