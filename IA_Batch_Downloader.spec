# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None
project_dir = Path.cwd()
assets_dir = project_dir / "assets"

a = Analysis(
    ["main.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        (str(assets_dir / "internet_archive_downloader_logo.png"), "assets"),
        (str(assets_dir / "internet_archive_downloader_logo.ico"), "assets"),
        (str(assets_dir / "internet_archive_downloader_icon_256.png"), "assets"),
    ],
    hiddenimports=[
        "internetarchive",
        "requests",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="IA_Batch_Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(assets_dir / "internet_archive_downloader_logo.ico") if (assets_dir / "internet_archive_downloader_logo.ico").exists() else None,
)
