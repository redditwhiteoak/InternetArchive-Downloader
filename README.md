# IA Batch Downloader GUI

A modern desktop GUI for downloading large batches of files from the Internet Archive using the `internetarchive` Python library.

This tool is designed for:
- Large unattended downloads
- ROM/archive collections
- Resume/recovery after stopping
- Queue management
- Disk space planning
- Partial `.part` file recovery

---

# Features

## Download Features

- Multi-threaded downloads
- Adjustable concurrent downloads
- Resume support using `.part` files
- Automatic retry support
- Retry failed downloads only
- Pause downloads
- Stop downloads while preserving resume state
- Download speed monitoring
- Estimated remaining time
- Queue size estimation
- Disk space checking

---

## Resume System

The downloader includes a full resume system.

### Pause
Temporarily freezes activity:
- Active downloads pause between chunks
- Queue refresh/check operations pause
- Press Pause again to continue immediately

### Stop
Stops the current session while preserving resumable state:
- Active downloads become resumable `.part` files
- Remaining queued files are preserved
- Pressing Start resumes stopped downloads first

### Resume Priority
When Start is pressed after a Stop:
1. Existing `.part` files resume first
2. Previously active downloads resume next
3. Remaining queued files continue afterward

---

## Portable State System

The application supports portable save/load state files.

Saved state includes:
- URLs
- Destination folder
- File extension filters
- Failed downloads
- Stopped downloads
- Resume queue
- Download table history
- Queue check cache

Menu options:
- Downloads → Save Portable State As...
- Downloads → Load Portable State

The application also autosaves state automatically after the first download starts and after major queue changes.

---

## Queue Refresh System

The Refresh Queue button:
- Checks queue size
- Estimates remaining download size
- Estimates required disk space
- Detects existing files
- Detects resumable `.part` files

If URLs, destination folder, or extension filters do not change:
- Start Downloads reuses the previous refresh result
- Queue analysis is not repeated unnecessarily

---

# Main Interface

## Active Downloads
Shows currently downloading files:
- Progress %
- Download speed
- ETA
- File size
- Status

## Downloads Table
A separate window showing all files:
- Queued
- Downloading
- Completed
- Failed
- Skipped
- Resumable

Includes filtering options.

## Queue Files Window
Shows all files currently planned for download.

## Live Log Window
Displays live application logs inside the GUI.

---

# Controls

## START
Starts downloads or resumes stopped downloads.

If resumable `.part` files exist:
- They resume first.

## PAUSE
Temporarily pauses downloads/checks.

Does not destroy queue state.

## STOP
Stops current operations while preserving resumable state.

## ↻ REFRESH
Refreshes:
- Queue size
- Remaining bytes
- Disk space requirements
- Existing file detection

Can be used while downloads are running.

---

# File Extension Filtering

The File Extensions field is optional.

Examples:
- zip
- 7z
- iso

Leave blank to download all file types.

---

# Installation

## Requirements

Python 3.10+ recommended.

Install dependencies:

```bash
pip install internetarchive requests
```

---

# Running

Open a terminal inside the project folder:

```bash
py main.py
```

---

# Folder Structure

```text
ia-batch-downloader-gui/
│
├── main.py
├── gui.py
├── downloader.py
├── scheduler.py
├── app_paths.py
├── history.py
├── utils.py
├── assets/
└── README.md
```

---

# Important Files

## main.py
Application entry point.

## gui.py
Main tkinter GUI and interface logic.

## downloader.py
Handles actual file downloading and resume logic.

## scheduler.py
Controls concurrent download scheduling.

## app_paths.py
Application paths/settings/log locations.

---

# Resume Files

Partial downloads are stored as:

```text
filename.ext.part
```

These files are automatically detected and resumed.

---

# Notes

- Stop preserves resume state
- Pause only temporarily freezes activity
- Closing the application autosaves current state
- Existing files can be skipped automatically
- Failed downloads can be retried separately

---

# Recommended Usage

For large archives:

1. Paste URLs
2. Choose destination
3. Click Refresh Queue
4. Verify required space
5. Click Start

If interrupted:
- Press Start again to resume automatically

---

# Troubleshooting

## Resume Not Working
Make sure:
- `.part` files still exist
- Destination folder has not changed

## Downloads Fail Immediately
Check:
- Internet connection
- Internet Archive availability
- Destination write permissions

## Queue Refresh Takes Time
Large Internet Archive collections may contain thousands of files.

---

# License

Personal/open-source use.

---

## Automatic Autosave

The application automatically saves portable resume state after the first download starts and after important queue/download events.

Autosave includes:
- URLs
- Destination folder
- Extension filter
- Stopped downloads
- Failed downloads
- Resume queue
- Download table state
- Queue refresh/check cache

Autosave also still happens immediately after important events such as:
- stopping downloads
- failed downloads
- completed downloads
- retry operations
- app close

The autosave file is stored in the application folder as:

```text
ia_downloader_autosave_state.json
```

If the app closes unexpectedly after downloads have started, reopening it will attempt to load the autosaved state.

---

## Partial `.part` File Prompt

When a `.part` file is found before downloading, the app can ask whether to resume it or start over. This behavior is controlled by the `.part files` setting.

The prompt shows:
- the `.part` file path
- the partial size
- the expected final file size when known
- how old the `.part` file is

Available `.part files` settings:
- **Ask, auto-start over after 5 minutes**: shows a prompt with a countdown. If no choice is made, the app deletes the `.part` file and starts over.
- **Always resume .part files**: resumes automatically.
- **Always start over**: deletes `.part` files and restarts those files automatically.
