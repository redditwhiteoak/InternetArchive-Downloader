# Contributing

Bug reports and pull requests are welcome.

Repository: https://github.com/redditwhiteoak/InternetArchive-Downloader

## Please do not commit

- Internet Archive credentials or `ia.ini`
- Personal paths
- Downloaded content
- `.part` files
- Generated settings/history/autosave/log files
- `__pycache__`, `build`, or `dist` folders
- Large compiled binaries in normal source commits

## Recommended workflow

1. Create a branch for the change.
2. Test with a small Internet Archive item first.
3. Verify Start, Pause, Stop, Refresh Queue + Disk, Reset Inputs, Downloads Table, and retry behavior.
4. Run a syntax check before committing:

```bash
python -m compileall .
```

## Building releases

Build Windows executables on Windows using:

```bat
build_windows_exe.bat
```

Upload the generated `dist/IA_Batch_Downloader.exe` to GitHub Releases rather than committing it to the source tree.
