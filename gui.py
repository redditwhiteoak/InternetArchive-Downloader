"""
IA Batch Downloader GUI

A desktop GUI for downloading one or more Internet Archive items with:
- multiple URLs/identifiers
- resumable queue save/load
- existing-file size checks
- simultaneous downloads
- live speed and ETA
- disk-space checks

This project uses the official internetarchive Python package for metadata/auth
and requests-style streaming through its configured session for file downloads.
"""

import os
import sys
import json
import time
import shutil
import re
import logging
import hashlib
import platform
import subprocess
import threading
import queue
import webbrowser
from urllib.parse import urlparse, unquote

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

from internetarchive import get_item

from downloader import DownloaderMixin
from scheduler import SchedulerMixin


APP_TITLE = "Internet Archive Downloader"
QUEUE_FILE_VERSION = 5


from app_paths import get_settings_path, get_history_path, get_log_path, get_autosave_path, get_app_base_dir, get_autosave_path


class IADownloaderGUI(DownloaderMixin, SchedulerMixin):
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1440x900")
        self.worker_thread = None
        self.queue_check_thread = None
        self.stop_requested = False
        self.ui_queue = queue.Queue()

        self.row_to_source_url = {}
        self.failed_urls = set()
        self.failed_download_jobs = []
        self.resume_pending_jobs = []  # memory-only; cleared when app exits
        self.stop_resume_counter = 0
        self.detached_download_rows = {}
        self.download_rows = {}
        self.downloads_window = None
        self.downloads_tree = None
        self.queue_window = None
        self.queue_tree = None
        self.queue_files_window = None
        self.queue_files_tree = None
        self.log_window = None
        self.log_text = None
        self.live_log_lines = []

        self.loaded_queue_path = ""
        self.last_check_signature = None
        self.last_check_remaining_bytes = None
        self.last_check_total_bytes = None
        self.last_check_file_count = None
        self.autosave_loaded = False
        self.autosave_interval_ms = 60000
        self.autosave_timer_active = False
        self.autosave_after_first_download_start_done = False
        self.part_resume_choices = {}

        self.status_filter_var = tk.StringVar(value="All")
        self.filter_count_var = tk.StringVar(value="Showing 0 of 0 rows")
        self.status_filter_combo = None

        self.show_urls_var = tk.BooleanVar(value=True)
        self.show_options_var = tk.BooleanVar(value=True)

        self.urls_section_widgets = []
        self.options_section_widgets = []

        self.download_history = self.load_history()

        self.pause_requested = False

        logging.basicConfig(
            filename=get_log_path(),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )

        self.progress_lock = threading.Lock()
        self.queue_total_files = 0
        self.queue_completed_files = 0
        self.estimated_total_bytes = 0
        self.estimated_remaining_bytes = 0

        # The top current-file progress bar should represent the active download
        # that started first. The table still shows all active downloads.
        self.current_display_lock = threading.Lock()
        self.active_start_order = {}
        self.active_start_counter = 0
        self.current_display_row_id = None
        self.scheduler_event = threading.Event()

        self.logo_image = None

        self.active_count_var = tk.StringVar(value="0")
        self.remaining_count_var = tk.StringVar(value="0")
        self.completed_count_var = tk.StringVar(value="0")
        self.failed_count_var = tk.StringVar(value="0")

        self.configure_modern_style()
        self.build_menu()
        self._build_ui()
        self.update_main_visibility()
        self.load_last_settings()
        self.load_autosave_state()
        self.log("Ready. Enter Internet Archive URLs/identifiers, choose a destination, then click Start Downloads.")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_ui_queue)

    # -------------------------
    # UI
    # -------------------------
    def configure_modern_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.colors = {
            "bg": "#06101d",
            "panel": "#0b1829",
            "panel2": "#0e2136",
            "panel3": "#071522",
            "border": "#294761",
            "text": "#f4f8ff",
            "muted": "#a9bfd4",
            "blue": "#35aaff",
            "green": "#73d51b",
            "green2": "#4d980d",
            "red": "#ff5555",
            "yellow": "#f6c85f",
        }

        c = self.colors
        self.root.configure(bg=c["bg"])

        style.configure(".", font=("Segoe UI", 10), background=c["bg"], foreground=c["text"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Header.TFrame", background=c["panel3"])
        style.configure("Panel.TFrame", background=c["panel"])
        style.configure("Card.TFrame", background=c["panel"])

        style.configure("TLabelframe", background=c["bg"], foreground=c["text"], bordercolor=c["border"], relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["green"], font=("Segoe UI", 11, "bold"))

        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Panel.TLabel", background=c["panel"], foreground=c["text"])
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        style.configure("PanelMuted.TLabel", background=c["panel"], foreground="#d6e6f5")
        style.configure("Green.TLabel", background=c["panel"], foreground=c["green"], font=("Segoe UI", 11, "bold"))
        style.configure("HeaderTitle.TLabel", background=c["panel3"], foreground=c["text"], font=("Segoe UI", 30, "bold"))
        style.configure("HeaderGreen.TLabel", background=c["panel3"], foreground=c["green"], font=("Segoe UI", 28, "bold"))
        style.configure("HeaderSubtitle.TLabel", background=c["panel3"], foreground=c["muted"], font=("Segoe UI", 10))

        style.configure("StatTitle.TLabel", background=c["panel"], foreground=c["text"], font=("Segoe UI", 10, "bold"))
        style.configure("StatGreen.TLabel", background=c["panel"], foreground=c["green"], font=("Segoe UI", 22, "bold"))
        style.configure("StatRed.TLabel", background=c["panel"], foreground=c["red"], font=("Segoe UI", 22, "bold"))

        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 10, "bold"), background="#14283d", foreground=c["text"], bordercolor=c["border"])
        style.map("TButton", background=[("active", "#1c3b5c"), ("disabled", "#283646")])
        style.configure("Accent.TButton", padding=(10, 8), font=("Segoe UI", 10, "bold"), foreground="#06101d", background=c["green"], bordercolor=c["green2"])
        style.map("Accent.TButton", background=[("active", "#91f235"), ("disabled", "#56702d")])
        style.configure("Stop.TButton", padding=(10, 8), font=("Segoe UI", 10, "bold"), foreground=c["text"], background="#253140", bordercolor=c["border"])
        style.map("Stop.TButton", background=[("active", "#38495c"), ("disabled", "#283646")])

        style.configure("TEntry", fieldbackground="#07101a", foreground=c["text"], insertcolor=c["text"], bordercolor=c["border"])
        style.configure("TCheckbutton", background=c["panel"], foreground=c["text"])
        style.configure("TCombobox", fieldbackground="#07101a", foreground="#111827")
        style.configure("TSpinbox", fieldbackground="#f8fbff", foreground="#111827", arrowsize=14)

        style.configure("Treeview", background="#07101a", fieldbackground="#07101a", foreground=c["text"], rowheight=28, borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#10273e", foreground=c["text"])
        style.map("Treeview", background=[("selected", "#1d4f75")], foreground=[("selected", c["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor="#182a3d", background=c["green"], bordercolor=c["border"])

    def build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save State", command=self.save_state)
        file_menu.add_command(label="Load State", command=self.load_state)
        file_menu.add_separator()
        file_menu.add_command(label="Open Destination Folder", command=self.open_destination_folder)
        file_menu.add_command(label="Open Log File", command=self.open_log_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        downloads_menu = tk.Menu(menubar, tearoff=0)
        downloads_menu.add_command(label="Refresh Queue + Disk Space", command=self.check_queue_only)
        downloads_menu.add_command(label="Start Downloads", command=self.start_downloads)
        downloads_menu.add_command(label="Pause / Resume Starting Files", command=self.toggle_pause)
        downloads_menu.add_command(label="Stop After Active Downloads", command=self.request_stop)
        downloads_menu.add_separator()
        downloads_menu.add_command(label="Retry Failed Only", command=self.retry_failed_downloads)
        downloads_menu.add_command(label="Save Portable State As...", command=self.save_autosave_state_as)
        downloads_menu.add_command(label="Load Portable State", command=self.load_autosave_state_dialog)
        downloads_menu.add_separator()
        menubar.add_cascade(label="Downloads", menu=downloads_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Remove Selected Bad URL", command=self.remove_selected_bad_url)
        edit_menu.add_command(label="Remove All Bad URLs", command=self.remove_all_bad_urls)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Log", command=self.clear_log)
        edit_menu.add_command(label="Clear Download View", command=self.clear_downloads)
        edit_menu.add_command(label="Reset Inputs", command=self.reset_inputs)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Open Downloads Table", command=self.open_downloads_window)
        view_menu.add_command(label="Show Queue", command=self.open_queue_window)
        view_menu.add_command(label="Show Queue Files", command=self.open_queue_files_window)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        help_menu.add_command(label="GitHub Repository", command=self.open_github_repo)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.update_idletasks()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.BOTH, expand=True)

        # -------------------------
        # Mockup-style branded header
        # -------------------------
        header = ttk.Frame(top, style="Header.TFrame", padding=10)
        header.pack(fill=tk.X, pady=(0, 8))

        logo_path = os.path.join(get_app_base_dir(), "assets", "internet_archive_downloader_logo.png")
        if os.path.exists(logo_path):
            try:
                self.logo_image = tk.PhotoImage(file=logo_path)
                self.root.iconphoto(True, self.logo_image)
                try:
                    ico_path = os.path.join(get_app_base_dir(), "assets", "internet_archive_downloader_logo.ico")
                    if os.path.exists(ico_path):
                        self.root.iconbitmap(ico_path)
                except Exception:
                    pass

                max_w = 230
                max_h = 145
                scale = max(1, int(max(self.logo_image.width() / max_w, self.logo_image.height() / max_h)))
                self.header_logo_image = self.logo_image.subsample(scale, scale) if scale > 1 else self.logo_image
                ttk.Label(header, image=self.header_logo_image, background=self.colors["panel3"]).pack(side=tk.LEFT, padx=(0, 18))
            except Exception as e:
                self.log(f"Could not load logo image: {e}")

        header_text = ttk.Frame(header, style="Header.TFrame")
        header_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(header_text, text="INTERNET ARCHIVE", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header_text, text="DOWNLOADER", style="HeaderGreen.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(
            header_text,
            text="Batch downloads • queue management • live progress • disk-space checks",
            style="HeaderSubtitle.TLabel"
        ).pack(anchor="w")

        stats_frame = ttk.Frame(header, style="Header.TFrame")
        stats_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        def stat_card(parent, title, variable, style_name):
            card = ttk.Frame(parent, style="Panel.TFrame", padding=(18, 10), width=150)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            card.pack_propagate(False)
            ttk.Label(card, text=title, style="StatTitle.TLabel", anchor="center").pack(fill=tk.X)
            ttk.Label(card, textvariable=variable, style=style_name, anchor="center").pack(fill=tk.X, pady=(4, 0))
            return card

        stat_card(stats_frame, "Active Downloads", self.active_count_var, "StatGreen.TLabel")
        stat_card(stats_frame, "Remaining Files", self.remaining_count_var, "StatGreen.TLabel")
        stat_card(stats_frame, "Total Completed", self.completed_count_var, "StatGreen.TLabel")
        stat_card(stats_frame, "Total Failed", self.failed_count_var, "StatRed.TLabel")

        quick_actions = ttk.Frame(top)
        quick_actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(quick_actions, text="Open Downloads Folder", command=self.open_destination_folder).pack(side=tk.RIGHT, padx=4)
        ttk.Button(quick_actions, text="Live Log", command=self.open_log_file).pack(side=tk.RIGHT, padx=4)
        ttk.Button(quick_actions, text="Downloads Table", command=self.open_downloads_window).pack(side=tk.RIGHT, padx=4)
        ttk.Button(quick_actions, text="Retry Failed", command=self.retry_failed_downloads).pack(side=tk.RIGHT, padx=4)

        # -------------------------
        # Three-panel main controls
        # -------------------------
        panels = ttk.Frame(top)
        panels.pack(fill=tk.X, pady=(0, 8))
        panels.columnconfigure(0, weight=3)
        panels.columnconfigure(1, weight=2)
        panels.columnconfigure(2, weight=3)

        self.loaded_queue_var = tk.StringVar(value="")
        self.loaded_queue_label = ttk.Label(
            self.urls_panel if False else panels,
            textvariable=self.loaded_queue_var,
            style="PanelMuted.TLabel"
        )
        self.loaded_queue_label.grid(row=0, column=0, columnspan=3, sticky="w", padx=(0, 6), pady=(0, 4))

        self.urls_panel = ttk.LabelFrame(panels, text="1. ADD URL(S)", padding=10)
        self.urls_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        self.urls_label = ttk.Label(self.urls_panel, text="Internet Archive URLs or identifiers, one per line:", style="Panel.TLabel")
        self.urls_label.pack(anchor="w")

        self.urls_frame = ttk.Frame(self.urls_panel, style="Panel.TFrame")
        self.urls_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 4))

        self.urls_text = tk.Text(
            self.urls_frame,
            height=4,
            wrap="none",
            bg="#07101a",
            fg="#f4f8ff",
            insertbackground="#f4f8ff",
            relief="solid",
            bd=1
        )

        urls_scroll_y = ttk.Scrollbar(self.urls_frame, orient="vertical", command=self.urls_text.yview)
        self.urls_scroll_x = ttk.Scrollbar(self.urls_panel, orient="horizontal", command=self.urls_text.xview)

        self.urls_text.configure(
            yscrollcommand=urls_scroll_y.set,
            xscrollcommand=self.urls_scroll_x.set
        )

        self.urls_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        urls_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.urls_scroll_x.pack(fill=tk.X)

        self.url_count_var = tk.StringVar(value="0 identifiers recognized")
        self.url_count_label = ttk.Label(self.urls_panel, textvariable=self.url_count_var, style="PanelMuted.TLabel")
        self.url_count_label.pack(anchor="w", pady=(4, 0))

        self.urls_section_widgets = [self.urls_panel]
        self.urls_text.bind("<KeyRelease>", lambda event: (self.update_url_count(), self.rebuild_queue_table(), self.invalidate_check_cache()))
        self.urls_text.bind("<FocusOut>", lambda event: (self.update_url_count(), self.rebuild_queue_table(), self.invalidate_check_cache()))

        self.options_frame = ttk.LabelFrame(panels, text="2. DOWNLOAD SETTINGS", padding=10)
        self.options_frame.grid(row=1, column=1, sticky="nsew", padx=6)
        options = self.options_frame
        self.options_section_widgets = [self.options_frame]

        ttk.Label(options, text="Destination:", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.dest_var = tk.StringVar(value="")
        self.dest_var.trace_add("write", lambda *_: self.invalidate_check_cache())
        self.dest_entry = ttk.Entry(options, textvariable=self.dest_var)
        self.dest_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(options, text="Browse", command=self.browse_dest).grid(row=0, column=2, pady=(0, 6))

        ttk.Label(options, text="Downloads at once:", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.concurrent_var = tk.IntVar(value=1)
        self.concurrent_var.trace_add("write", lambda *_: self.on_concurrent_limit_changed())
        self.concurrent_spin = ttk.Spinbox(options, from_=1, to=10, textvariable=self.concurrent_var, width=7)
        self.concurrent_spin.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        try:
            self.concurrent_spin.configure(foreground="black")
        except Exception:
            pass

        ttk.Label(options, text="File extensions (optional):", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.extensions_var = tk.StringVar(value="")
        self.extensions_var.trace_add("write", lambda *_: self.invalidate_check_cache())
        self.extensions_entry = ttk.Entry(options, textvariable=self.extensions_var)
        self.extensions_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=4)

        ttk.Label(
            options,
            text="Optional. Leave blank for all file types. Examples: zip, 7z, iso",
            style="PanelMuted.TLabel"
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 8))

        ttk.Label(options, text=".part files:", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        self.part_action_var = tk.StringVar(value="Ask, auto-start over after 5 minutes")
        self.part_action_combo = ttk.Combobox(
            options,
            textvariable=self.part_action_var,
            values=[
                "Ask, auto-start over after 5 minutes",
                "Always resume .part files",
                "Always start over"
            ],
            state="readonly",
            width=34
        )
        self.part_action_combo.grid(row=4, column=1, columnspan=2, sticky="ew", padx=6, pady=4)

        self.size_check_var = tk.BooleanVar(value=True)
        self.size_check_var.trace_add("write", lambda *_: self.invalidate_check_cache())
        ttk.Checkbutton(options, text="Verify existing file sizes before skipping", variable=self.size_check_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.stop_if_no_space_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="Stop if destination drive does not have enough free space", variable=self.stop_if_no_space_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.use_config_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Use IA config/login file", variable=self.use_config_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.config_var = tk.StringVar(value="")
        self.config_entry = ttk.Entry(options, textvariable=self.config_var)
        self.config_entry.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(options, text="Browse Config", command=self.browse_config).grid(row=8, column=2, padx=(6, 0), pady=(6, 0))

        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Show extra detail in log", variable=self.verbose_var).grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))

        options.columnconfigure(1, weight=1)

        control_frame = ttk.LabelFrame(panels, text="3. CONTROL", padding=10)
        control_frame.grid(row=1, column=2, sticky="nsew", padx=(6, 0))
        control_frame.configure(width=210)

        self.check_btn = ttk.Button(control_frame, text="↻ REFRESH QUEUE + DISK", command=self.check_queue_only)
        self.check_btn.pack(fill=tk.X, pady=(0, 8), ipady=2)

        self.reset_btn = ttk.Button(control_frame, text="⟲ RESET INPUTS", command=self.reset_inputs)
        self.reset_btn.pack(fill=tk.X, pady=(0, 8), ipady=2)

        self.start_btn = ttk.Button(control_frame, text="▶ START", command=self.start_downloads, style="Accent.TButton")
        self.start_btn.pack(fill=tk.X, pady=(0, 8), ipady=2)

        self.pause_btn = ttk.Button(control_frame, text="Ⅱ PAUSE", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(fill=tk.X, pady=(0, 8), ipady=2)

        self.stop_btn = ttk.Button(control_frame, text="■ STOP", command=self.request_stop, state=tk.DISABLED, style="Stop.TButton")
        self.stop_btn.pack(fill=tk.X, ipady=2)

        # -------------------------
        # Status strip
        # -------------------------
        status_frame = ttk.Frame(top, style="Panel.TFrame", padding=8)
        status_frame.pack(fill=tk.X, pady=(0, 8))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_frame, textvariable=self.status_var, style="Panel.TLabel").pack(side=tk.LEFT, padx=(0, 18))

        self.total_size_var = tk.StringVar(value="Total queue size: not checked")
        ttk.Label(status_frame, textvariable=self.total_size_var, style="PanelMuted.TLabel").pack(side=tk.LEFT, padx=(0, 18))

        self.disk_space_var = tk.StringVar(value="Disk space: not checked")
        ttk.Label(status_frame, textvariable=self.disk_space_var, style="PanelMuted.TLabel").pack(side=tk.LEFT)

        progress_frame = ttk.Frame(top)
        progress_frame.pack(fill=tk.X, pady=(0, 8))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 4))

        self.current_file_var = tk.StringVar(value="Current file: idle")
        ttk.Label(progress_frame, textvariable=self.current_file_var, style="PanelMuted.TLabel").pack(anchor="w")

        self.file_progress_var = tk.DoubleVar(value=0)
        self.file_progress = ttk.Progressbar(progress_frame, variable=self.file_progress_var, maximum=100)
        self.file_progress.pack(fill=tk.X, pady=(4, 0))

        # -------------------------
        # Active downloads table
        # -------------------------
        self.active_frame = ttk.LabelFrame(top, text="ACTIVE DOWNLOADS", padding=8)
        self.active_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        active_tree_frame = ttk.Frame(self.active_frame)
        active_tree_frame.pack(fill=tk.BOTH, expand=True)

        active_columns = ("percent", "downloaded", "speed", "eta", "size", "status")
        self.active_tree = ttk.Treeview(
            active_tree_frame,
            columns=active_columns,
            show="tree headings",
            height=7
        )

        active_scroll_y = ttk.Scrollbar(active_tree_frame, orient="vertical", command=self.active_tree.yview)
        active_scroll_x = ttk.Scrollbar(self.active_frame, orient="horizontal", command=self.active_tree.xview)

        self.active_tree.configure(
            yscrollcommand=active_scroll_y.set,
            xscrollcommand=active_scroll_x.set
        )

        self.active_tree.heading("#0", text="Internet Archive Item / File")
        self.active_tree.heading("percent", text="Progress")
        self.active_tree.heading("downloaded", text="Downloaded")
        self.active_tree.heading("speed", text="Speed")
        self.active_tree.heading("eta", text="ETA")
        self.active_tree.heading("size", text="Size")
        self.active_tree.heading("status", text="Status")

        self.active_tree.column("#0", width=520, minwidth=260, stretch=True)
        self.active_tree.column("percent", width=100, anchor="center", stretch=False)
        self.active_tree.column("downloaded", width=120, anchor="e", stretch=False)
        self.active_tree.column("speed", width=100, anchor="e", stretch=False)
        self.active_tree.column("eta", width=100, anchor="e", stretch=False)
        self.active_tree.column("size", width=110, anchor="e", stretch=False)
        self.active_tree.column("status", width=150, stretch=False)

        self.active_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        active_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        active_scroll_x.pack(fill=tk.X, pady=(4, 0))

        self.active_tree.insert(
            "",
            tk.END,
            iid="__no_active__",
            text="No active downloads",
            values=("", "", "", "", "", "Waiting")
        )

    # -------------------------
    # Settings
    # -------------------------
    def collect_settings(self):
        return {
            "version": QUEUE_FILE_VERSION,
            "urls": self.get_urls(),
            "destination": self.dest_var.get(),
            "use_config": self.use_config_var.get(),
            "config_file": self.config_var.get(),
            "verify_sizes": self.size_check_var.get(),
                        "verbose": self.verbose_var.get(),
            "concurrent_downloads": self.get_concurrent_count(),
            "stop_if_no_space": self.stop_if_no_space_var.get(),
            "extensions": self.extensions_var.get(),
            "part_action": self.part_action_var.get() if hasattr(self, "part_action_var") else "Ask, auto-start over after 5 minutes",
            "window_geometry": self.root.geometry(),
        }

    def migrate_settings(self, data):
        """Apply simple defaults for older settings/state files."""
        if not isinstance(data, dict):
            return {}
        data.setdefault("version", QUEUE_FILE_VERSION)
        data.setdefault("extensions", "")
        data.setdefault("stop_if_no_space", True)
        data.setdefault("concurrent_downloads", 1)
        data.setdefault("part_action", "Ask, auto-start over after 5 minutes")
        return data

    def apply_settings(self, data):
        data = self.migrate_settings(data)
        urls = data.get("urls", [])
        self.urls_text.delete("1.0", tk.END)
        self.urls_text.insert("1.0", "\n".join(urls))
        self.dest_var.set(data.get("destination", ""))
        self.use_config_var.set(bool(data.get("use_config", False)))
        self.config_var.set(data.get("config_file", ""))
        self.size_check_var.set(bool(data.get("verify_sizes", True)))
        self.verbose_var.set(bool(data.get("verbose", False)))
        try:
            self.concurrent_var.set(max(1, min(10, int(data.get("concurrent_downloads", 1)))))
        except Exception:
            self.concurrent_var.set(1)
        self.stop_if_no_space_var.set(bool(data.get("stop_if_no_space", True)))
        self.extensions_var.set(data.get("extensions", ""))
        self.update_url_count()
    def save_last_settings(self):
        try:
            with open(get_settings_path(), "w", encoding="utf-8") as f:
                json.dump(self.collect_settings(), f, indent=2)
        except Exception:
            pass

    def load_last_settings(self):
        path = get_settings_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.apply_settings(json.load(f))
            self.log(f"Loaded previous settings from: {path}")
        except Exception as e:
            self.log(f"Could not load previous settings: {e}")

    def on_close(self):
        self.autosave_timer_active = False
        self.save_last_settings()
        self.save_history()
        self.save_autosave_state()
        self.root.destroy()

    # -------------------------
    # Persistent history
    # -------------------------
    def load_history(self):
        path = get_history_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_history(self):
        try:
            with open(get_history_path(), "w", encoding="utf-8") as f:
                json.dump(self.download_history, f, indent=2)
        except Exception:
            pass

    def record_history(self, identifier, file_name, local_path, expected_size, status, source_url):
        key = f"{identifier}/{file_name}"
        self.download_history[key] = {
            "identifier": identifier,
            "file_name": file_name,
            "local_path": local_path,
            "expected_size": expected_size,
            "status": status,
            "source_url": source_url,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # -------------------------
    # Dialogs / state files
    # -------------------------
    def browse_dest(self):
        folder = filedialog.askdirectory()
        if folder:
            self.dest_var.set(folder)

    def browse_config(self):
        path = filedialog.askopenfilename(title="Select ia.ini", filetypes=[("INI files", "*.ini"), ("All files", "*.*")])
        if path:
            self.config_var.set(path)
            self.use_config_var.set(True)

    def save_state(self):
        path = filedialog.asksaveasfilename(title="Save queue", defaultextension=".json",
                                            filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.collect_settings(), f, indent=2)
            self.log(f"Saved queue: {path}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def invalidate_check_cache(self, *_args):
        self.last_check_signature = None
        self.last_check_remaining_bytes = None
        self.last_check_total_bytes = None
        self.last_check_file_count = None
        try:
            self.status_var.set("Queue changed - size check needed")
        except Exception:
            pass

    def get_check_signature(self):
        payload = {
            "urls": self.get_urls(),
            "destination": self.dest_var.get().strip(),
            "extensions": self.extensions_var.get().strip(),
            "verify_sizes": bool(self.size_check_var.get()),
            "use_config": bool(self.use_config_var.get()),
            "config_file": self.config_var.get().strip() if self.use_config_var.get() else "",
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def store_check_cache(self, total_bytes, remaining_bytes, file_count):
        self.last_check_signature = self.get_check_signature()
        self.last_check_total_bytes = total_bytes
        self.last_check_remaining_bytes = remaining_bytes
        self.last_check_file_count = file_count

    def has_valid_check_cache(self):
        return (
            self.last_check_signature is not None
            and self.last_check_signature == self.get_check_signature()
            and self.last_check_remaining_bytes is not None
        )

    def update_loaded_queue_display(self, path=""):
        self.loaded_queue_path = path or ""
        try:
            if self.loaded_queue_path:
                self.loaded_queue_var.set(f"Loaded state file: {os.path.basename(self.loaded_queue_path)}")
            else:
                self.loaded_queue_var.set("")
        except Exception:
            pass

    def load_state(self):
        path = filedialog.askopenfilename(title="Load queue",
                                          filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            try:
                self.apply_settings(json.loads(raw))
            except json.JSONDecodeError:
                urls = [line.strip() for line in raw.splitlines() if line.strip()]
                self.urls_text.delete("1.0", tk.END)
                self.urls_text.insert("1.0", "\n".join(urls))
            self.update_url_count()
            self.rebuild_queue_table()
            self.update_loaded_queue_display(path)
            self.invalidate_check_cache()
            self.log(f"Loaded state: {path}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    # -------------------------
    # Bad URL removal
    # -------------------------
    def remove_selected_bad_url(self):
        if not self.downloads_tree or not self.downloads_window or not self.downloads_window.winfo_exists():
            self.open_downloads_window()

        selected = self.downloads_tree.selection() if self.downloads_tree else []
        if not selected:
            messagebox.showinfo("No Selection", "Select a bad URL/error row from the Downloads Table first.")
            return

        removed_any = False
        for row_id in selected:
            source_url = self.row_to_source_url.get(row_id)
            if not source_url or source_url not in self.failed_urls:
                continue
            self.remove_url_from_textbox(source_url)
            self.failed_urls.discard(source_url)
            removed_any = True
            if row_id in self.download_rows:
                self.download_rows[row_id]["status"] = "Removed from URL list"

        self.apply_status_filter()

        if removed_any:
            self.log("Removed selected bad URL(s) from the URL list.")
        else:
            messagebox.showinfo("Not a Bad URL", "The selected row is not marked as a bad URL/error item.")

    def remove_all_bad_urls(self):
        if not self.failed_urls:
            messagebox.showinfo("No Bad URLs", "There are no bad URLs to remove.")
            return
        count = len(self.failed_urls)
        for source_url in list(self.failed_urls):
            self.remove_url_from_textbox(source_url)
        self.failed_urls.clear()
        self.log(f"Removed {count} bad URL(s) from the URL list.")

    def remove_url_from_textbox(self, url_to_remove):
        kept = [line for line in self.get_urls() if line.strip() != url_to_remove.strip()]
        self.urls_text.delete("1.0", tk.END)
        self.urls_text.insert("1.0", "\n".join(kept))

    # -------------------------
    # UI queue helpers
    # -------------------------
    def normalize_path_text(self, value):
        if value is None:
            return ""
        text = str(value)
        # Normalize Windows-looking paths so logs/tables do not mix / and \.
        if re.search(r"[A-Za-z]:[\\/]", text):
            def repl(match):
                return os.path.normpath(match.group(0))
            # Match a Windows path-like run until a likely separator in log prose.
            return re.sub(r"[A-Za-z]:[\\/][^\n\r\t]+", repl, text)
        return os.path.normpath(text) if ("/" in text or "\\" in text) and not text.startswith("http") else text

    def log(self, msg):
        try:
            text = self.normalize_path_text(msg)
            logging.info(text)
            self.append_live_log(text)
        except Exception:
            pass

    def ui_log(self, msg):
        self.ui_queue.put(("log", self.normalize_path_text(msg)))

    def ui_detail_log(self, msg):
        if self.verbose_var.get():
            self.ui_queue.put(("log", msg))

    def ui_status(self, msg):
        self.ui_queue.put(("status", msg))

    def ui_total_size(self, msg):
        self.ui_queue.put(("total_size", msg))

    def ui_disk_space(self, msg):
        self.ui_queue.put(("disk_space", msg))

    def ui_progress(self, percent):
        self.ui_queue.put(("progress", percent))

    def ui_file_progress(self, percent, text):
        self.ui_queue.put(("file_progress", percent, text))

    def ui_active_download(self, row_id, file_name, percent, downloaded_text, size_text, status, speed_text="", eta_text=""):
        self.ui_queue.put(("active_download", row_id, file_name, percent, downloaded_text, speed_text, eta_text, size_text, status))

    def ui_active_clear(self):
        self.ui_queue.put(("active_clear",))

    def ui_add_or_update_file(self, row_id, item_name, file_name, status, percent, size_text, path, source_url=None):
        self.ui_queue.put(("file_update", row_id, item_name, file_name, status, percent, size_text, self.normalize_path_text(path), source_url))

    def ui_add_error(self, row_id, source_url, identifier, error_text):
        self.ui_queue.put(("error_row", row_id, source_url, identifier, error_text))

    def get_status_priority(self, status):
        status = (status or "").lower()
        if "downloading" in status or "starting" in status or "paused" in status or "retrying" in status:
            return 0
        if "queued" in status or "redownloading" in status or "not started" in status or "waiting" in status or "limit reduced" in status:
            return 1
        if "done" in status or "completed" in status or "skipped" in status:
            return 2
        if "error" in status or "mismatch" in status or "missing" in status or "bad url" in status or "failed" in status:
            return 3
        return 4

    def set_status_filter(self, value):
        self.status_filter_var.set(value)
        if self.status_filter_combo is not None:
            try:
                self.status_filter_combo.set(value)
            except Exception:
                pass
        if not self.downloads_window or not self.downloads_window.winfo_exists():
            self.open_downloads_window()
        else:
            self.apply_status_filter()

    def status_matches_filter(self, status, filter_value):
        status = (status or "").lower()
        if filter_value == "All":
            return True
        if filter_value == "Active":
            return any(term in status for term in ("downloading", "starting", "paused", "retrying"))
        if filter_value == "Queued":
            return any(term in status for term in ("queued", "redownloading", "not started", "waiting", "limit reduced"))
        if filter_value == "Done":
            return "done" in status or "completed" in status
        if filter_value == "Skipped":
            return "skipped" in status
        if filter_value == "Failed":
            return any(term in status for term in ("error", "mismatch", "missing", "bad url", "failed", "forbidden", "unauthorized"))
        return True

    def rebuild_download_table(self):
        if not self.downloads_tree or not self.downloads_window or not self.downloads_window.winfo_exists():
            try:
                self.filter_count_var.set(f"{len(self.download_rows)} total row(s)")
            except Exception:
                pass
            return

        selected_filter = self.status_filter_var.get() or "All"

        for row_id in self.downloads_tree.get_children():
            self.downloads_tree.delete(row_id)

        rows = []
        total_rows = len(self.download_rows)

        for row_id, row in self.download_rows.items():
            status = row.get("status", "")
            if self.status_matches_filter(status, selected_filter):
                rows.append((self.get_status_priority(status), row.get("order", 0), row_id, row))

        rows.sort(key=lambda x: (x[0], x[1]))

        for _, _, row_id, row in rows:
            self.downloads_tree.insert(
                "",
                tk.END,
                iid=row_id,
                values=(
                    row.get("display_name", ""),
                    row.get("status", ""),
                    row.get("percent", ""),
                    row.get("size_text", ""),
                    self.normalize_path_text(row.get("path", "")),
                )
            )

        self.filter_count_var.set(f"Showing {len(rows)} of {total_rows} rows ({selected_filter})")

    def reorder_download_table(self):
        self.rebuild_download_table()

    def apply_status_filter(self):
        self.rebuild_download_table()

    def set_widget_visible(self, widget, visible):
        try:
            if visible:
                if not widget.winfo_ismapped():
                    widget.pack()
            else:
                if widget.winfo_ismapped():
                    widget.pack_forget()
        except Exception:
            pass

    def update_main_visibility(self):
        # URL and Options sections are always visible in this layout.
        return

    def update_stats_cards(self):
        try:
            active = 0
            if hasattr(self, "active_tree"):
                for row in self.active_tree.get_children():
                    if row == "__no_active__":
                        continue
                    values = self.active_tree.item(row, "values")
                    status = str(values[-1]).lower() if values else ""
                    if any(term in status for term in ("downloading", "starting", "paused", "queued", "retrying")):
                        active += 1

            completed = 0
            failed = 0
            remaining = 0
            for row in getattr(self, "download_rows", {}).values():
                status = str(row.get("status", "")).lower()
                if "done" in status or "skipped" in status:
                    completed += 1
                elif any(term in status for term in ("error", "failed", "mismatch", "bad url", "unauthorized", "forbidden")):
                    failed += 1
                else:
                    remaining += 1

            # Include same-session stopped jobs even if their row has not updated yet.
            for job in self.dedupe_jobs(getattr(self, "resume_pending_jobs", [])):
                row_id = job.get("row_id")
                row_status = str(getattr(self, "download_rows", {}).get(row_id, {}).get("status", "")).lower()
                if row_id and ("done" in row_status or "skipped" in row_status or "error" in row_status):
                    continue
                if row_id and row_id in getattr(self, "download_rows", {}):
                    continue
                remaining += 1

            self.active_count_var.set(str(active))
            self.remaining_count_var.set(str(remaining))
            self.completed_count_var.set(str(completed))
            self.failed_count_var.set(str(failed))
        except Exception:
            pass

    def bind_tree_mousewheel(self, tree):
        try:
            tree.bind("<MouseWheel>", lambda e: tree.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            tree.bind("<Shift-MouseWheel>", lambda e: tree.xview_scroll(int(-1 * (e.delta / 120)), "units"))
        except Exception:
            pass

    def process_ui_queue(self):
        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]

                if kind == "log":
                    self.log(event[1])

                elif kind == "status":
                    self.status_var.set(event[1])

                elif kind == "total_size":
                    self.total_size_var.set(event[1])

                elif kind == "disk_space":
                    self.disk_space_var.set(event[1])

                elif kind == "progress":
                    self.progress_var.set(event[1])

                elif kind == "file_progress":
                    _, percent, text = event
                    self.file_progress_var.set(percent)
                    self.current_file_var.set(text)

                elif kind == "active_download":
                    # Supports both event formats:
                    # old: ("active_download", row_id, file_name, percent, downloaded, speed, eta, size, status)
                    # new: ("active_download", row_id, item_name, file_name, percent, downloaded, speed, eta, size, status)
                    if len(event) == 10:
                        _, row_id, item_name, file_name, percent, downloaded_text, speed_text, eta_text, size_text, status = event
                    else:
                        _, row_id, file_name, percent, downloaded_text, speed_text, eta_text, size_text, status = event
                        item_name = ""
                        try:
                            item_name = self.download_rows.get(row_id, {}).get("item_name", "")
                        except Exception:
                            item_name = ""

                    combined_name = f"{item_name}  |  {file_name}" if item_name else file_name
                    values = (f"{percent}%", downloaded_text, speed_text, eta_text, size_text, status)

                    if self.active_tree.exists("__no_active__"):
                        self.active_tree.delete("__no_active__")

                    if self.active_tree.exists(row_id):
                        self.active_tree.item(row_id, text=combined_name, values=values)
                    else:
                        self.active_tree.insert("", tk.END, iid=row_id, text=combined_name, values=values)

                    # Keep the active-downloads table anchored to the oldest active row.
                    # Previously every progress update called see(row_id), so parallel
                    # downloads made the table jump to whichever row updated last.
                    anchor_row = None
                    try:
                        anchor_row = self.current_display_row_id
                    except Exception:
                        anchor_row = None
                    if anchor_row and self.active_tree.exists(anchor_row):
                        self.active_tree.see(anchor_row)
                    elif self.active_tree.get_children():
                        self.active_tree.see(self.active_tree.get_children()[0])
                    self.active_tree.update_idletasks()
                    self.update_stats_cards()

                elif kind == "active_clear":
                    for row in self.active_tree.get_children():
                        self.active_tree.delete(row)
                    self.active_tree.insert(
                        "",
                        tk.END,
                        iid="__no_active__",
                        text="No active downloads",
                        values=("", "", "", "", "", "Waiting")
                    )

                elif kind == "file_update":
                    _, row_id, item_name, file_name, status, percent, size_text, path, source_url = event
                    display_name = f"{item_name} / {file_name}" if file_name else item_name
                    if source_url:
                        self.row_to_source_url[row_id] = source_url

                    if row_id not in self.download_rows:
                        self.download_rows[row_id] = {"order": len(self.download_rows)}

                    self.download_rows[row_id].update({
                        "display_name": display_name,
                        "item_name": item_name,
                        "file_name": file_name,
                        "status": status,
                        "percent": percent,
                        "size_text": size_text,
                        "path": path,
                        "source_url": source_url,
                    })

                    self.apply_status_filter()
                    self.rebuild_queue_table()
                    self.rebuild_queue_files_table()
                    self.update_stats_cards()
                    self.update_stats_cards()

                elif kind == "error_row":
                    _, row_id, source_url, identifier, error_text = event
                    self.failed_urls.add(source_url)
                    self.row_to_source_url[row_id] = source_url

                    if row_id not in self.download_rows:
                        self.download_rows[row_id] = {"order": len(self.download_rows)}

                    self.download_rows[row_id].update({
                        "display_name": identifier,
                        "item_name": identifier,
                        "file_name": "",
                        "status": "Bad URL / Error",
                        "percent": "",
                        "size_text": "",
                        "path": error_text,
                        "source_url": source_url,
                    })

                    self.apply_status_filter()
                    self.rebuild_queue_table()
                    self.rebuild_queue_files_table()
                    self.update_stats_cards()

                elif kind == "buttons":
                    _, running = event
                    self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
                    # Refresh Queue remains available while downloads are running.
                    self.check_btn.config(state=tk.NORMAL)
                    self.pause_btn.config(state=tk.NORMAL if running else tk.DISABLED)
                    self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)

        except queue.Empty:
            pass
        self.root.after(100, self.process_ui_queue)

    # -------------------------
    # General helpers
    # -------------------------
    def should_skip_metadata_file(self, base_name):
        skip_suffixes = (
            "_meta.xml", "_files.xml", "_reviews.xml", "_meta.sqlite",
            "_thumb.jpg", "_thumb.png", "__ia_thumb.jpg", "__ia_thumb.png",
            "_itemimage.jpg", "_itemimage.png"
        )
        return base_name.lower().endswith(skip_suffixes)

    def get_urls(self):
        return [line.strip() for line in self.urls_text.get("1.0", tk.END).splitlines() if line.strip()]

    def get_extension_filter(self):
        raw = self.extensions_var.get().strip()
        if not raw:
            return []
        exts = []
        for part in raw.replace(";", ",").split(","):
            part = part.strip().lower()
            if not part:
                continue
            if not part.startswith("."):
                part = "." + part
            exts.append(part)
        return exts

    def file_allowed_by_extension(self, file_name):
        exts = self.get_extension_filter()
        if not exts:
            return True
        return os.path.basename(file_name).lower().endswith(tuple(exts))

    def adjust_concurrent_count(self, delta):
        try:
            current = int(self.concurrent_var.get())
        except Exception:
            current = 1
        self.concurrent_var.set(max(1, min(10, current + delta)))
        self.on_concurrent_limit_changed()

    def get_concurrent_count(self):
        try:
            return max(1, min(10, int(self.concurrent_var.get())))
        except Exception:
            return 1

    def on_concurrent_limit_changed(self):
        # Wake the scheduler/download workers so they can react to a changed limit.
        try:
            self.scheduler_event.set()
        except Exception:
            pass

    def should_throttle_download(self, row_id):
        """
        If the user lowers 'Downloads at once' below the number already active,
        pause active downloads beyond the new limit. The oldest active downloads
        keep running; newer extra ones wait between chunks.
        """
        limit = self.get_concurrent_count()

        with self.current_display_lock:
            if row_id not in self.active_start_order:
                return False

            ordered = sorted(self.active_start_order.items(), key=lambda item: item[1])
            allowed = {active_row_id for active_row_id, _ in ordered[:limit]}
            return row_id not in allowed

    def wait_while_throttled(self, row_id, file_name):
        while (
            not self.stop_requested
            and not self.pause_requested
            and self.should_throttle_download(row_id)
        ):
            current_status = f"Queued - limit reduced to {self.get_concurrent_count()}"
            self.ui_active_download(row_id, file_name, 0, "", "", current_status)
            self.ui_add_or_update_file(row_id, "", file_name, current_status, "", "", "", "")
            if self.should_update_current_bar(row_id):
                self.ui_file_progress(self.file_progress_var.get(), f"{current_status}: {file_name}")
            self.scheduler_event.wait(0.25)


    def extract_identifier(self, url_or_id):
        text = url_or_id.strip()
        if not text:
            return ""

        parsed = urlparse(text if "://" in text else f"https://archive.org/{text}")

        if parsed.netloc and "archive.org" in parsed.netloc.lower():
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("download", "details"):
                return parts[1].strip()
            if len(parts) == 1:
                return parts[0].strip()

        # Plain identifier fallback. Strip accidental query/fragment.
        text = text.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
        return unquote(text)

    def update_url_count(self):
        urls = self.get_urls()
        identifiers = [self.extract_identifier(u) for u in urls if self.extract_identifier(u)]
        self.url_count_var.set(f"{len(identifiers)} identifier(s) recognized")

    def clear_log(self):
        from app_paths import get_log_path

        try:
            self.live_log_lines.clear()
            with open(get_log_path(), "w", encoding="utf-8") as f:
                f.write("IA Batch Downloader log cleared.\n")
            self.status_var.set("Log cleared")
            self.refresh_live_log_window()
        except Exception as e:
            messagebox.showerror("Clear Log Error", str(e))

    def clear_downloads(self):
        """Clear the visible download information without deleting table rows or saved row data."""
        if self.downloads_tree:
            for row in self.downloads_tree.get_children():
                try:
                    self.downloads_tree.item(row, values=("", "", "", "", ""))
                except Exception:
                    pass

        if self.active_tree:
            for row in self.active_tree.get_children():
                try:
                    self.active_tree.item(row, text="", values=("", "", "", "", "", ""))
                except Exception:
                    pass
            if not self.active_tree.get_children():
                self.active_tree.insert(
                    "",
                    tk.END,
                    iid="__no_active__",
                    text="No active downloads",
                    values=("", "", "", "", "", "Waiting")
                )

        self.progress_var.set(0)
        self.file_progress_var.set(0)
        self.current_file_var.set("Current file: idle")
        self.status_var.set("Download view cleared")
        self.log("Download view cleared. Table rows, queue data, log, and history were preserved.")

    def reset_inputs(self):
        """Reset all user-entered inputs/settings to their default empty state."""
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Downloads Running", "Stop the current downloads before resetting inputs.")
            return

        self.urls_text.delete("1.0", tk.END)
        self.dest_var.set("")
        self.extensions_var.set("")
        self.use_config_var.set(False)
        self.config_var.set("")
        self.size_check_var.set(True)
        self.stop_if_no_space_var.set(True)
        self.verbose_var.set(False)
        self.part_action_var.set("Ask, auto-start over after 5 minutes")
        try:
            self.concurrent_var.set(1)
        except Exception:
            pass

        self.update_loaded_queue_display("")
        self.update_url_count()
        self.invalidate_check_cache()
        self.rebuild_queue_table()
        self.rebuild_queue_files_table()
        self.total_size_var.set("Total queue size: not checked")
        self.disk_space_var.set("Disk space: not checked")
        self.status_var.set("Inputs reset")
        self.log("Inputs reset.")

    def open_destination_folder(self):
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showerror("Missing Destination", "Choose a destination folder first.")
            return
        try:
            os.makedirs(dest, exist_ok=True)
            system = platform.system().lower()
            if system == "windows":
                os.startfile(dest)
            elif system == "darwin":
                subprocess.Popen(["open", dest])
            else:
                subprocess.Popen(["xdg-open", dest])
        except Exception as e:
            messagebox.showerror("Open Folder Error", str(e))

    def open_log_file(self):
        self.open_live_log_window()

    def open_live_log_window(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            self.log_window.focus_force()
            self.refresh_live_log_window()
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("Live Log")
        self.log_window.geometry("1100x650")
        self.log_window.protocol("WM_DELETE_WINDOW", self.close_live_log_window)

        frame = ttk.Frame(self.log_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        topbar = ttk.Frame(frame)
        topbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(topbar, text="Live Log", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(topbar, text="Clear", command=self.clear_log).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(topbar, text="Refresh", command=self.refresh_live_log_window).pack(side=tk.RIGHT)

        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            body,
            wrap="none",
            bg="#07101a",
            fg="#f4f8ff",
            insertbackground="#f4f8ff",
            relief="solid",
            bd=1
        )
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self.refresh_live_log_window()

    def close_live_log_window(self):
        try:
            self.log_window.destroy()
        except Exception:
            pass
        self.log_window = None
        self.log_text = None

    def refresh_live_log_window(self):
        if not self.log_text or not self.log_window or not self.log_window.winfo_exists():
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert("1.0", "\n".join(self.live_log_lines[-5000:]))
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def append_live_log(self, msg):
        try:
            text = self.normalize_path_text(msg)
            self.live_log_lines.append(text)
            if len(self.live_log_lines) > 5000:
                self.live_log_lines = self.live_log_lines[-5000:]

            if self.log_text and self.log_window and self.log_window.winfo_exists():
                self.log_text.configure(state="normal")
                self.log_text.insert(tk.END, text + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state="disabled")
        except Exception:
            pass

    def open_queue_window(self):
        if self.queue_window and self.queue_window.winfo_exists():
            self.queue_window.lift()
            self.queue_window.focus_force()
            self.rebuild_queue_table()
            return

        self.queue_window = tk.Toplevel(self.root)
        self.queue_window.title("Queue")
        self.queue_window.geometry("1000x520")

        frame = ttk.Frame(self.queue_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        topbar = ttk.Frame(frame)
        topbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(topbar, text="Current Queue", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(topbar, text="Refresh", command=self.rebuild_queue_table).pack(side=tk.RIGHT)

        columns = ("number", "identifier", "status", "source")
        self.queue_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=18
        )

        self.queue_tree.heading("number", text="#")
        self.queue_tree.heading("identifier", text="Parsed Identifier")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("source", text="Original URL / Input")

        self.queue_tree.column("number", width=60, anchor="center")
        self.queue_tree.column("identifier", width=280)
        self.queue_tree.column("status", width=180)
        self.queue_tree.column("source", width=520)

        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.queue_tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.queue_tree.xview)

        self.queue_tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set
        )

        self.queue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        xscroll.pack(fill=tk.X, pady=(4, 0))

        self.bind_tree_mousewheel(self.queue_tree)
        self.rebuild_queue_table()

    def rebuild_queue_table(self):
        if not self.queue_tree or not self.queue_window or not self.queue_window.winfo_exists():
            return

        for row in self.queue_tree.get_children():
            self.queue_tree.delete(row)

        urls = self.get_urls()

        for index, source in enumerate(urls, start=1):
            identifier = self.extract_identifier(source)
            if not identifier:
                status = "Invalid / blank"
            elif " " in identifier:
                status = "Invalid identifier"
            else:
                # If any download rows are already known for this identifier,
                # summarize the most important current status.
                related = [
                    row.get("status", "")
                    for row in self.download_rows.values()
                    if row.get("item_name") == identifier or str(row.get("display_name", "")).startswith(identifier + " /")
                ]

                if not related:
                    status = "Waiting"
                elif any("downloading" in str(s).lower() or "starting" in str(s).lower() for s in related):
                    status = "Active"
                elif any("error" in str(s).lower() or "mismatch" in str(s).lower() for s in related):
                    status = "Has errors"
                elif all("done" in str(s).lower() or "skipped" in str(s).lower() for s in related):
                    status = "Complete"
                else:
                    status = "In progress"

            self.queue_tree.insert(
                "",
                tk.END,
                values=(index, identifier, status, source)
            )

    def open_queue_files_window(self):
        if self.queue_files_window and self.queue_files_window.winfo_exists():
            self.queue_files_window.lift()
            self.queue_files_window.focus_force()
            self.rebuild_queue_files_table()
            return

        self.queue_files_window = tk.Toplevel(self.root)
        self.queue_files_window.title("Queue Files")
        self.queue_files_window.geometry("1200x650")

        frame = ttk.Frame(self.queue_files_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        topbar = ttk.Frame(frame)
        topbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(topbar, text="Files in queue to download", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(topbar, text="Refresh", command=self.rebuild_queue_files_table).pack(side=tk.RIGHT)

        columns = ("item", "file", "size", "status", "local_path")
        self.queue_files_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=22
        )

        self.queue_files_tree.heading("item", text="Item")
        self.queue_files_tree.heading("file", text="File")
        self.queue_files_tree.heading("size", text="Size")
        self.queue_files_tree.heading("status", text="Status")
        self.queue_files_tree.heading("local_path", text="Local Path / Error")

        self.queue_files_tree.column("item", width=220)
        self.queue_files_tree.column("file", width=330)
        self.queue_files_tree.column("size", width=110, anchor="e")
        self.queue_files_tree.column("status", width=130)
        self.queue_files_tree.column("local_path", width=420)

        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.queue_files_tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.queue_files_tree.xview)

        self.queue_files_tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set
        )

        self.queue_files_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        xscroll.pack(fill=tk.X, pady=(4, 0))

        self.bind_tree_mousewheel(self.queue_files_tree)
        self.rebuild_queue_files_table()

    def rebuild_queue_files_table(self):
        if not self.queue_files_tree or not self.queue_files_window or not self.queue_files_window.winfo_exists():
            return

        for row in self.queue_files_tree.get_children():
            self.queue_files_tree.delete(row)

        if not self.download_rows:
            self.queue_files_tree.insert(
                "",
                tk.END,
                values=(
                    "",
                    "No file list loaded yet. Click Refresh Queue or Start to read item metadata.",
                    "",
                    "Waiting",
                    ""
                )
            )
            return

        rows = sorted(
            self.download_rows.values(),
            key=lambda row: (row.get("item_name", ""), row.get("order", 0))
        )

        for row in rows:
            self.queue_files_tree.insert(
                "",
                tk.END,
                values=(
                    row.get("item_name", ""),
                    row.get("file_name", "") or row.get("display_name", ""),
                    row.get("size_text", ""),
                    row.get("status", ""),
                    row.get("path", ""),
                )
            )

    def open_downloads_window(self):
        if self.downloads_window and self.downloads_window.winfo_exists():
            self.downloads_window.lift()
            self.downloads_window.focus_force()
            self.rebuild_download_table()
            return

        self.downloads_window = tk.Toplevel(self.root)
        self.downloads_window.title("Downloads Table")
        self.downloads_window.geometry("1400x760")
        self.downloads_window.minsize(900, 500)
        self.downloads_window.protocol("WM_DELETE_WINDOW", self.close_downloads_window)

        outer = ttk.Frame(self.downloads_window, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        topbar = ttk.Frame(outer)
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(topbar, text="Downloads Table", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(topbar, text="Show:").pack(side=tk.LEFT)

        self.status_filter_combo = ttk.Combobox(
            topbar,
            textvariable=self.status_filter_var,
            values=["All", "Active", "Queued", "Done", "Skipped", "Failed"],
            width=12,
            state="readonly"
        )
        self.status_filter_combo.pack(side=tk.LEFT, padx=(6, 8))
        self.status_filter_combo.bind("<<ComboboxSelected>>", lambda event: self.apply_status_filter())

        for label in ["All", "Active", "Queued", "Done", "Skipped", "Failed"]:
            ttk.Button(topbar, text=label, command=lambda value=label: self.set_status_filter(value)).pack(side=tk.LEFT, padx=2)

        ttk.Button(topbar, text="Refresh", command=self.apply_status_filter).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(topbar, textvariable=self.filter_count_var, style="PanelMuted.TLabel").pack(side=tk.LEFT, padx=(12, 0))

        table_frame = ttk.Frame(outer)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = ("item_file", "status", "percent", "size", "path")
        self.downloads_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=24
        )

        headings = {
            "item_file": "Item / File",
            "status": "Status",
            "percent": "Progress",
            "size": "Size",
            "path": "Path / Error",
        }
        for col, label in headings.items():
            self.downloads_tree.heading(col, text=label)

        self.downloads_tree.column("item_file", width=430, minwidth=220, stretch=True)
        self.downloads_tree.column("status", width=150, minwidth=110, stretch=False)
        self.downloads_tree.column("percent", width=95, minwidth=80, anchor="center", stretch=False)
        self.downloads_tree.column("size", width=120, minwidth=100, anchor="e", stretch=False)
        self.downloads_tree.column("path", width=650, minwidth=300, stretch=True)

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.downloads_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.downloads_tree.xview)

        self.downloads_tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set
        )

        self.downloads_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self.bind_tree_mousewheel(self.downloads_tree)
        self.rebuild_download_table()

    def close_downloads_window(self):
        try:
            self.downloads_window.destroy()
        except Exception:
            pass
        self.downloads_window = None
        self.downloads_tree = None
        self.status_filter_combo = None

    def serialize_job(self, job):
        return {
            "identifier": job.get("identifier", ""),
            "file_name": job.get("file_name", ""),
            "expected_size": job.get("expected_size"),
            "local_path": self.normalize_path_text(job.get("local_path", "")),
            "row_id": job.get("row_id", ""),
            "size_text": job.get("size_text", ""),
            "source_url": job.get("source_url", ""),
            "config_file": job.get("config_file"),
            "dest": self.normalize_path_text(job.get("dest", "")),
            "file_index": job.get("file_index", 0),
            "stop_order": job.get("stop_order", 0),
            "was_active_when_stopped": bool(job.get("was_active_when_stopped", False)),
            "part_path": self.normalize_path_text(job.get("part_path", "")),
            "resume_from_part": bool(job.get("resume_from_part", False)),
        }

    def deserialize_job(self, data):
        if not isinstance(data, dict):
            return None
        identifier = data.get("identifier", "")
        file_name = data.get("file_name", "")
        if not identifier or not file_name:
            return None
        return {
            "identifier": identifier,
            "file_name": file_name,
            "expected_size": data.get("expected_size"),
            "local_path": self.normalize_path_text(data.get("local_path", "")),
            "row_id": data.get("row_id") or self.safe_row_id(identifier, file_name),
            "size_text": data.get("size_text") or self.format_bytes(data.get("expected_size")),
            "source_url": data.get("source_url", identifier),
            "config_file": data.get("config_file"),
            "dest": self.normalize_path_text(data.get("dest", "")),
            "file_index": data.get("file_index", 0),
            "stop_order": data.get("stop_order", 0),
            "was_active_when_stopped": bool(data.get("was_active_when_stopped", False)),
            "part_path": self.normalize_path_text(data.get("part_path", "")),
            "resume_from_part": bool(data.get("resume_from_part", False)),
        }

    def periodic_autosave_state(self):
        # Compatibility no-op. Autosave is event-driven now so it does not
        # interrupt idle/checking work or write repeatedly while no file has started.
        return

    def autosave_after_first_download_start(self):
        if self.autosave_after_first_download_start_done:
            return
        self.autosave_after_first_download_start_done = True
        self.save_autosave_state()
        self.ui_log("Autosaved state after first download started.")

    def collect_autosave_state(self):
        failed_jobs = [self.serialize_job(job) for job in self.failed_download_jobs]
        resume_jobs = [self.serialize_job(job) for job in self.resume_pending_jobs]

        return {
            "version": QUEUE_FILE_VERSION,
            "settings": self.collect_settings(),
            "loaded_queue_path": self.loaded_queue_path,
            "resume_pending_jobs": resume_jobs,
            "failed_download_jobs": failed_jobs,
            "download_rows": self.download_rows,
            "last_check_signature": self.last_check_signature,
            "last_check_remaining_bytes": self.last_check_remaining_bytes,
            "last_check_total_bytes": self.last_check_total_bytes,
            "last_check_file_count": self.last_check_file_count,
        }

    def save_autosave_state(self, path=None):
        path = path or get_autosave_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.collect_autosave_state(), f, indent=2)
            self.ui_status(f"Autosaved resume state: {os.path.basename(path)}")
            self.log(f"Autosaved portable resume state: {path}")
        except Exception as e:
            self.log(f"Could not save autosave state: {e}")

    def save_autosave_state_as(self):
        path = filedialog.asksaveasfilename(
            title="Save Portable State",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.save_autosave_state(path)

    def load_autosave_state_dialog(self):
        path = filedialog.askopenfilename(
            title="Load Portable State",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.load_autosave_state(path)

    def load_autosave_state(self, path=None):
        path = path or get_autosave_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            settings = data.get("settings", {})
            if settings:
                self.apply_settings(settings)

            self.update_loaded_queue_display(data.get("loaded_queue_path", ""))

            self.resume_pending_jobs = []
            for item in data.get("resume_pending_jobs", []):
                job = self.deserialize_job(item)
                if job:
                    self.resume_pending_jobs.append(job)

            self.failed_download_jobs = []
            for item in data.get("failed_download_jobs", []):
                job = self.deserialize_job(item)
                if job:
                    self.failed_download_jobs.append(job)

            rows = data.get("download_rows", {})
            if isinstance(rows, dict):
                self.download_rows.update(rows)

            self.last_check_signature = data.get("last_check_signature")
            self.last_check_remaining_bytes = data.get("last_check_remaining_bytes")
            self.last_check_total_bytes = data.get("last_check_total_bytes")
            self.last_check_file_count = data.get("last_check_file_count")

            self.resume_pending_jobs = self.dedupe_jobs(self.resume_pending_jobs)
            self.failed_download_jobs = self.dedupe_jobs(self.failed_download_jobs)
            self.autosave_loaded = True
            self.apply_status_filter()
            self.rebuild_queue_files_table()
            self.update_stats_cards()
            self.ui_status(f"Loaded portable resume state: {os.path.basename(path)}")
            self.log(f"Loaded portable resume state: {path}")
            return True
        except Exception as e:
            messagebox.showerror("Load Resume State Error", str(e))
            return False

    def retry_failed_downloads(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Busy", "A download/check operation is already running.")
            return

        jobs = self.dedupe_jobs(list(self.failed_download_jobs))
        if not jobs:
            # Also build retry list from visible/download row state if failed jobs list is empty.
            for row_id, row in self.download_rows.items():
                status = str(row.get("status", "")).lower()
                if any(term in status for term in ("error", "failed", "mismatch", "bad url", "unauthorized", "forbidden")):
                    identifier = row.get("item_name", "")
                    file_name = row.get("file_name", "")
                    if identifier and file_name:
                        jobs.append({
                            "identifier": identifier,
                            "file_name": file_name,
                            "expected_size": None,
                            "local_path": self.normalize_path_text(row.get("path", "")),
                            "row_id": row_id,
                            "size_text": row.get("size_text", "Unknown"),
                            "source_url": row.get("source_url", identifier),
                            "config_file": self.config_var.get().strip() if self.use_config_var.get() else None,
                            "dest": self.dest_var.get().strip(),
                            "file_index": row.get("order", 0),
                        })

        jobs = self.dedupe_jobs(jobs)
        if not jobs:
            messagebox.showinfo("Retry Failed", "There are no failed downloads to retry.")
            return

        self.stop_requested = False
        self.pause_requested = False
        self.pause_btn.config(text="Ⅱ PAUSE")
        self.worker_thread = threading.Thread(target=self.run_failed_retry_downloads, args=(jobs,), daemon=True)
        self.worker_thread.start()

    def run_failed_retry_downloads(self, jobs):
        self.ui_queue.put(("buttons", True))
        try:
            self.failed_download_jobs = []
            with self.progress_lock:
                self.queue_total_files = len(jobs)
                self.queue_completed_files = 0

            self.ui_progress(0)
            self.ui_log(f"Retrying {len(jobs)} failed download(s) only.")
            self.ui_status(f"Retrying {len(jobs)} failed download(s)")

            self.run_dynamic_scheduler("Retry failed only", len(jobs), 0, jobs)

            self.ui_status("Retry failed finished" if not self.stop_requested else "Retry failed stopped")
            self.save_history()
            self.save_autosave_state()
            self.update_stats_cards()

        finally:
            self.ui_queue.put(("buttons", False))
            self.save_last_settings()

    def view_completed_downloads(self):
        completed = []

        # Saved history from previous/current runs.
        for item in self.download_history.values():
            status = str(item.get("status", "")).lower()
            if status in ("done", "skipped"):
                completed.append({
                    "file_name": item.get("file_name", ""),
                    "status": item.get("status", ""),
                    "expected_size": item.get("expected_size"),
                    "local_path": item.get("local_path", ""),
                    "updated": item.get("updated", ""),
                })

        # Current visible/in-memory table rows, useful before history has been saved.
        known_paths = {item.get("local_path") for item in completed}
        for row in self.download_rows.values():
            status = str(row.get("status", "")).lower()
            if ("done" in status or "skipped" in status) and row.get("path") not in known_paths:
                completed.append({
                    "file_name": row.get("file_name") or row.get("display_name", ""),
                    "status": row.get("status", ""),
                    "expected_size": None,
                    "local_path": row.get("path", ""),
                    "updated": "Current session",
                })

        win = tk.Toplevel(self.root)
        win.title("Completed Downloads")
        win.geometry("1000x560")

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text=f"Completed / skipped downloads: {len(completed)}",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 4))

        columns = ("status", "size", "path", "updated")
        tree = ttk.Treeview(frame, columns=columns, show="tree headings")
        tree.heading("#0", text="File")
        tree.heading("status", text="Status")
        tree.heading("size", text="Expected Size")
        tree.heading("path", text="Local Path")
        tree.heading("updated", text="Updated")

        tree.column("#0", width=300)
        tree.column("status", width=100)
        tree.column("size", width=120, anchor="e")
        tree.column("path", width=420)
        tree.column("updated", width=150)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True)

        tree.pack(in_=table_frame, side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(in_=table_frame, side=tk.RIGHT, fill=tk.Y)
        xscroll.pack(fill=tk.X, pady=(4, 0))

        for item in sorted(completed, key=lambda x: str(x.get("updated", "")), reverse=True):
            expected_size = item.get("expected_size")
            size_text = self.format_bytes(expected_size) if expected_size else "Unknown"
            tree.insert(
                "",
                tk.END,
                text=item.get("file_name", ""),
                values=(
                    item.get("status", ""),
                    size_text,
                    item.get("local_path", ""),
                    item.get("updated", ""),
                )
            )

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side=tk.RIGHT)

    def open_github_repo(self):
        webbrowser.open("https://github.com/redditwhiteoak/InternetArchive-Downloader")

    def show_about(self):
        messagebox.showinfo(
            "About IA Batch Downloader GUI",
            "IA Batch Downloader GUI\n\n"
            "Batch download Internet Archive items with queue management, "
            "resume-friendly size checks, parallel downloads, and logging.\n\n"
            "GitHub: https://github.com/redditwhiteoak/InternetArchive-Downloader"
        )

    def request_stop(self):
        self.stop_requested = True
        self.pause_requested = False
        try:
            self.pause_btn.config(text="Ⅱ PAUSE")
        except Exception:
            pass
        self.scheduler_event.set()
        self.ui_log("Stop requested. Checking/downloading will stop between metadata records or chunks.")
        self.ui_status("Stopping... Start will re-enable when current work has stopped.")

    def toggle_pause(self):
        self.pause_requested = not self.pause_requested
        if self.pause_requested:
            self.pause_btn.config(text="▶ RESUME")
            self.ui_log("Paused. Queue checks pause between records; active downloads pause between chunks; no new files will start.")
            self.ui_status("Paused - downloads waiting")
            self.ui_file_progress(self.file_progress_var.get(), "Paused - downloads waiting")
        else:
            self.pause_btn.config(text="Ⅱ PAUSE")
            self.ui_log("Resumed. Active downloads and new starts may continue.")
            self.ui_status("Resumed")
        self.scheduler_event.set()

    def wait_while_paused(self, row_id=None, file_name=""):
        while self.pause_requested and not self.stop_requested:
            if row_id and file_name:
                self.ui_active_download(row_id, file_name, 0, "", "", "Paused")
            self.scheduler_event.wait(0.25)

    @staticmethod
    def percent_from_sizes(current, total):
        if not total:
            return 0
        return max(0, min(100, int((current / total) * 100)))

    @staticmethod
    def format_bytes(num):
        if num is None:
            return "Unknown"
        num = float(num)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num < 1024:
                return f"{num:.1f} {unit}"
            num /= 1024
        return f"{num:.1f} PB"

    @staticmethod
    def format_speed(bytes_per_second):
        if bytes_per_second is None:
            return ""
        return IADownloaderGUI.format_bytes(bytes_per_second) + "/s"

    @staticmethod
    def format_eta(seconds):
        if seconds is None or seconds < 0:
            return ""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {sec}s"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h {minutes}m"
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"

    @staticmethod
    def safe_row_id(*parts):
        raw = "_".join(str(p) for p in parts)
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
        return f"{cleaned[:220]}_{digest}"

    def get_local_path(self, dest, identifier, file_name):
        r"""
        Save structure:
        destination/
            identifier/
                <original IA internal folders/files>

        Examples:
        <destination>/<archive_item>/Game.zip
        <destination>/<archive_item>/Disc 1/Game.iso

        Only one top-level folder is created per IA site/item.
        Existing internal IA folders are preserved naturally.
        """
        return os.path.join(dest, identifier, file_name.replace("/", os.sep))

    def get_legacy_direct_path(self, dest, file_name):
        # Older versions of this GUI saved directly under the selected destination.
        return os.path.join(dest, file_name.replace("/", os.sep))

    def find_existing_local_file(self, dest, identifier, file_name):
        """
        Check both layouts:
        1. New preferred layout: destination/item_identifier/file
        2. Legacy layout: destination/file

        New downloads always save into destination/item_identifier/file.
        Existing legacy files are still detected and skipped if their size matches.
        """
        item_folder_path = self.get_local_path(dest, identifier, file_name)
        legacy_direct_path = self.get_legacy_direct_path(dest, file_name)

        if os.path.exists(item_folder_path):
            return item_folder_path, True

        if os.path.exists(legacy_direct_path):
            return legacy_direct_path, True

        return item_folder_path, False




    def wait_while_check_paused(self, context="Checking"):
        while self.pause_requested and not self.stop_requested:
            self.ui_status(f"Paused - {context}")
            self.ui_file_progress(self.file_progress_var.get(), f"Paused - {context}")
            self.scheduler_event.wait(0.25)

    def estimate_queue_sizes(self, queue_items, dest, config_file):
        """
        Estimate total queue size, remaining download size, and eligible file count.

        This pre-check uses size-only checks for speed. Full verification still
        happens during the actual skip/download decision in process_item().
        """
        grand_total = 0
        remaining_total = 0
        eligible_file_count = 0
        total_items = len(queue_items)

        self.ui_log(f"Queue size check started for {total_items} item(s).")
        self.ui_total_size("Total queue size: checking metadata...")
        self.ui_disk_space("Disk space: waiting for queue estimate...")

        for item_index, (source_url, identifier) in enumerate(queue_items, start=1):
            self.wait_while_check_paused("queue size check")

            if self.stop_requested:
                self.ui_status("Stopped during queue size check")
                self.ui_log("Queue size check stopped by user.")
                break

            try:
                self.ui_status(f"Checking item {item_index}/{total_items}: {identifier}")
                self.ui_file_progress(0, f"Checking metadata: {identifier}")
                self.ui_log(f"Reading metadata for {identifier} ({item_index}/{total_items})...")

                item = get_item(identifier, config_file=config_file) if config_file else get_item(identifier)

                item_total = 0
                item_remaining = 0
                item_count = 0
                scanned_count = 0

                for f in item.files:
                    self.wait_while_check_paused(f"checking {identifier}")

                    if self.stop_requested:
                        self.ui_status("Stopped during queue size check")
                        self.ui_log(f"Queue size check stopped while scanning {identifier}.")
                        break

                    scanned_count += 1
                    name = f.get("name")
                    if not name:
                        continue

                    if scanned_count == 1 or scanned_count % 100 == 0:
                        self.ui_status(
                            f"Checking {identifier}: scanned {scanned_count} metadata record(s), "
                            f"kept {item_count} file(s)"
                        )
                        self.ui_file_progress(
                            0,
                            f"Checking {identifier}: scanned {scanned_count}, kept {item_count}"
                        )

                    base = os.path.basename(name).lower()
                    if self.should_skip_metadata_file(base):
                        continue

                    if not self.file_allowed_by_extension(name):
                        continue

                    item_count += 1
                    eligible_file_count += 1

                    try:
                        size = int(f.get("size")) if f.get("size") is not None else 0
                    except Exception:
                        size = 0

                    item_total += size
                    grand_total += size

                    local_path, exists_locally = self.find_existing_local_file(dest, identifier, name)

                    should_download = True

                    if exists_locally:
                        if not self.size_check_var.get():
                            should_download = False
                        else:
                            try:
                                local_size = os.path.getsize(local_path)
                                if local_size == size:
                                    should_download = False
                            except Exception:
                                pass

                    row_id = self.safe_row_id(identifier, name)
                    local_path, _ = self.find_existing_local_file(dest, identifier, name)
                    part_path, part_size, can_resume_part = self.get_part_file_info(local_path, size, name)
                    status_text = "Queued - resume .part" if should_download and can_resume_part else ("Queued" if should_download else "Skipped")
                    if row_id not in self.download_rows:
                        self.download_rows[row_id] = {"order": len(self.download_rows)}
                    self.download_rows[row_id].update({
                        "display_name": f"{identifier} / {name}",
                        "item_name": identifier,
                        "file_name": name,
                        "status": status_text,
                        "percent": (f"{self.percent_from_sizes(part_size, size)}%" if should_download and can_resume_part else ("0%" if should_download else "100%")),
                        "size_text": self.format_bytes(size),
                        "path": part_path if should_download and can_resume_part else local_path,
                        "source_url": source_url,
                    })

                    if should_download:
                        item_remaining += size
                        remaining_total += size

                    if item_count % 50 == 0:
                        self.ui_total_size(
                            f"Partial queue size: {self.format_bytes(grand_total)} total "
                            f"({self.format_bytes(remaining_total)} remaining, "
                            f"{eligible_file_count} file(s))"
                        )

                self.ui_log(
                    f"Size estimate for {identifier}: "
                    f"{self.format_bytes(item_total)} total, "
                    f"{self.format_bytes(item_remaining)} remaining, "
                    f"{item_count} file(s), {scanned_count} metadata record(s) scanned"
                )

                if total_items:
                    self.ui_progress((item_index / total_items) * 100)

                self.ui_total_size(
                    f"Partial queue size: {self.format_bytes(grand_total)} total "
                    f"({self.format_bytes(remaining_total)} remaining, "
                    f"{eligible_file_count} file(s))"
                )

            except Exception as e:
                self.ui_log(f"Could not estimate size for {identifier}: {e}")

        self.estimated_total_bytes = grand_total
        self.estimated_remaining_bytes = remaining_total

        with self.progress_lock:
            self.queue_total_files = eligible_file_count
            self.queue_completed_files = 0

        self.ui_total_size(
            f"Total queue size: {self.format_bytes(grand_total)} "
            f"(remaining download: {self.format_bytes(remaining_total)}, "
            f"{eligible_file_count} file(s))"
        )

        if self.stop_requested:
            self.ui_file_progress(0, "Queue size check stopped")
            self.ui_status("Queue size check stopped")
        else:
            self.ui_file_progress(100, "Queue size check complete")
            self.ui_status("Queue size check complete")

        return grand_total, remaining_total, eligible_file_count

    def check_destination_space(self, dest, remaining_bytes):
        """
        Check whether the destination drive has enough free space for the remaining queue.
        Returns True if OK to continue, False if downloads should stop.
        """
        try:
            total, used, free = shutil.disk_usage(dest)
        except Exception as e:
            self.ui_disk_space(f"Disk space: could not check ({e})")
            self.ui_log(f"Could not check destination free space: {e}")
            return True

        if remaining_bytes <= 0:
            self.ui_disk_space(
                f"Disk space: {self.format_bytes(free)} free; no remaining download size estimated"
            )
            return True

        short_by = remaining_bytes - free

        if short_by <= 0:
            self.ui_disk_space(
                f"Disk space: {self.format_bytes(free)} free; "
                f"{self.format_bytes(remaining_bytes)} remaining; OK"
            )
            self.ui_log(
                f"Disk space OK: {self.format_bytes(free)} free, "
                f"{self.format_bytes(remaining_bytes)} remaining to download."
            )
            return True

        warning = (
            f"Not enough free space. Free: {self.format_bytes(free)}. "
            f"Remaining download: {self.format_bytes(remaining_bytes)}. "
            f"Short by: {self.format_bytes(short_by)}."
        )

        self.ui_disk_space(f"Disk space: NOT ENOUGH - short by {self.format_bytes(short_by)}")
        self.ui_log(warning)

        if self.stop_if_no_space_var.get():
            return False

        return True

    def check_space_before_starting_next_file(self, dest):
        """
        During a run, check free space before starting another file.
        Uses estimated remaining bytes minus completed progress when possible.
        """
        try:
            _, _, free = shutil.disk_usage(dest)
        except Exception:
            return True

        # Keep a small safety buffer so a drive is not filled completely.
        safety_buffer = 1 * 1024 * 1024 * 1024  # 1 GB

        if free <= safety_buffer:
            self.ui_disk_space(
                f"Disk space: LOW - only {self.format_bytes(free)} free; stopping new downloads"
            )
            self.ui_log(
                f"Stopping new downloads because free space is low: {self.format_bytes(free)} free."
            )
            return False

        self.ui_disk_space(f"Disk space: {self.format_bytes(free)} free")
        return True

    # -------------------------
    # Download orchestration
    # -------------------------
    def check_queue_only(self):
        """
        Refresh the parsed queue display, total queue estimate, and destination disk space
        without starting downloads. This can run while downloads are active, but only
        one refresh can run at a time.
        """
        if self.queue_check_thread and self.queue_check_thread.is_alive():
            messagebox.showinfo("Busy", "A queue refresh is already running.")
            return

        self.update_url_count()
        self.rebuild_queue_table()
        self.rebuild_queue_files_table()

        urls = self.get_urls()
        dest = self.dest_var.get().strip()

        if not urls:
            messagebox.showerror("Missing URLs", "Enter at least one Internet Archive URL or identifier.")
            return

        if not dest:
            messagebox.showerror("Missing Destination", "Choose a destination folder.")
            return

        self.queue_check_thread = threading.Thread(target=self.run_queue_check_only, daemon=True)
        self.queue_check_thread.start()

    def run_queue_check_only(self):
        downloads_running = bool(self.worker_thread and self.worker_thread.is_alive())
        if not downloads_running:
            self.ui_queue.put(("buttons", True))

        try:
            urls = self.get_urls()
            dest = self.dest_var.get().strip()
            config_file = self.config_var.get().strip() if self.use_config_var.get() else None

            queue_items = [(u, self.extract_identifier(u)) for u in urls]

            self.ui_log("Refreshing queue size and disk space...")
            self.ui_status("Checking queue size and free space...")

            total_bytes, remaining_bytes, file_count = self.estimate_queue_sizes(queue_items, dest, config_file)

            if not self.stop_requested:
                self.store_check_cache(total_bytes, remaining_bytes, file_count)

            enough_space = self.check_destination_space(dest, remaining_bytes)

            if enough_space:
                self.ui_status("Queue refresh complete - enough free space available")
                self.ui_log("Queue refresh complete: enough free space available.")
            else:
                self.ui_status("Queue refresh complete - NOT enough free space")
                self.ui_log("Queue refresh complete: NOT enough free space.")

        except Exception as e:
            self.ui_log(f"Queue refresh failed: {e}")
            self.ui_status("Queue refresh failed")

        finally:
            downloads_running = bool(self.worker_thread and self.worker_thread.is_alive())
            if not downloads_running:
                self.ui_queue.put(("buttons", False))
            else:
                self.ui_queue.put(("buttons", True))

    def queue_stopped_job_for_resume(self, job, was_active=False):
        try:
            self.stop_resume_counter += 1
            job["stop_order"] = self.stop_resume_counter
            job["was_active_when_stopped"] = bool(was_active)

            row_id = job.get("row_id")
            if row_id and row_id in self.download_rows:
                self.download_rows[row_id]["status"] = "Queued - stopped"
                self.download_rows[row_id]["percent"] = self.download_rows[row_id].get("percent", "")
                local_path_for_resume = self.normalize_path_text(job.get("local_path", self.download_rows[row_id].get("path", "")))
                part_path_for_resume = local_path_for_resume + ".part"
                try:
                    if os.path.exists(part_path_for_resume):
                        self.download_rows[row_id]["path"] = part_path_for_resume
                    else:
                        self.download_rows[row_id]["path"] = local_path_for_resume
                except Exception:
                    self.download_rows[row_id]["path"] = local_path_for_resume

            # A stopped active download should be first when Start is pressed again.
            if was_active:
                self.resume_pending_jobs.insert(0, job)
            else:
                self.resume_pending_jobs.append(job)

            self.resume_pending_jobs = self.dedupe_jobs(self.resume_pending_jobs)
            self.update_stats_cards()
            self.save_autosave_state()
        except Exception:
            if was_active:
                self.resume_pending_jobs.insert(0, job)
            else:
                self.resume_pending_jobs.append(job)

    def dedupe_jobs(self, jobs):
        seen = set()
        unique = []
        for job in jobs:
            key = job.get("row_id") or job.get("local_path")
            if key in seen:
                continue
            seen.add(key)
            unique.append(job)

        def priority(job):
            local_path = job.get("local_path", "")
            part_path = local_path + ".part" if local_path else ""
            try:
                # Stopped mid-file downloads have a .part file and should resume first.
                if part_path and os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                    return (0, -int(job.get("stop_order", 0) or 0), job.get("file_index", 0))
            except Exception:
                pass

            # Explicitly marked stopped-active jobs come before never-started jobs.
            if job.get("was_active_when_stopped"):
                # Sort active stopped jobs ahead of never-started jobs.
                # Negative stop_order keeps the most recently stopped active file first.
                return (1, -int(job.get("stop_order", 0) or 0), job.get("file_index", 0))

            return (2, job.get("file_index", 0), job.get("row_id", ""))

        return sorted(unique, key=priority)

    def build_resume_jobs_from_rows(self):
        """
        Fallback resume builder.

        If Stop happened while threads were still winding down, or if a state file
        was restored without resume_pending_jobs being populated correctly, rebuild
        resumable jobs from rows marked stopped/queued/error and from .part files.
        """
        jobs = []

        dest = self.dest_var.get().strip()
        config_file = self.config_var.get().strip() if self.use_config_var.get() else None

        for row_id, row in getattr(self, "download_rows", {}).items():
            status = str(row.get("status", "")).lower()
            item_name = row.get("item_name", "")
            file_name = row.get("file_name", "")

            if not item_name or not file_name:
                continue

            path = self.normalize_path_text(row.get("path", ""))
            local_path = path[:-5] if path.endswith(".part") else path
            part_path = local_path + ".part"

            has_part = False
            try:
                has_part = os.path.exists(part_path) and os.path.getsize(part_path) > 0
            except Exception:
                has_part = False

            should_resume = (
                "queued - stopped" in status
                or "stopped" in status
                or "resume .part" in status
                or has_part
            )

            if not should_resume:
                continue

            expected_size = None
            size_text = row.get("size_text", "Unknown")
            # Keep expected_size unknown if not available; downloader can still resume
            # and validate by server/content length when possible.

            jobs.append({
                "identifier": item_name,
                "source_url": row.get("source_url") or item_name,
                "file_index": row.get("order", 0),
                "file_name": file_name,
                "expected_size": expected_size,
                "local_path": local_path,
                "row_id": row_id,
                "size_text": size_text,
                "config_file": config_file,
                "dest": dest,
                "part_path": part_path,
                "resume_from_part": has_part,
                "was_active_when_stopped": has_part or "stopped" in status,
                "stop_order": row.get("order", 0),
            })

        return self.dedupe_jobs(jobs)

    def start_downloads(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Busy", "A download/check operation is already running.")
            return

        # Resume stopped/partial jobs first. This includes the normal
        # same-session queue plus a fallback rebuilt from stopped rows/.part files.
        resume_jobs = self.dedupe_jobs(list(self.resume_pending_jobs) + self.build_resume_jobs_from_rows())
        if resume_jobs:
            self.resume_pending_jobs = resume_jobs
            self.stop_requested = False
            self.pause_requested = False
            self.pause_btn.config(text="Ⅱ PAUSE")
            self.worker_thread = threading.Thread(target=self.run_resume_downloads, daemon=True)
            self.worker_thread.start()
            return

        urls = self.get_urls()
        dest = self.dest_var.get().strip()

        if not urls:
            messagebox.showerror("Missing URLs", "Enter at least one Internet Archive URL or identifier.")
            return

        if not dest:
            messagebox.showerror("Missing Destination", "Choose a destination folder.")
            return

        self.stop_requested = False
        self.pause_requested = False
        self.pause_btn.config(text="Ⅱ PAUSE")
        self.worker_thread = threading.Thread(target=self.run_downloads, daemon=True)
        self.worker_thread.start()

    def run_resume_downloads(self):
        self.ui_queue.put(("buttons", True))
        try:
            jobs = self.dedupe_jobs(list(self.resume_pending_jobs))
            self.resume_pending_jobs.clear()
            self.autosave_after_first_download_start_done = False

            if not jobs:
                self.ui_status("Nothing to resume")
                return

            with self.progress_lock:
                self.queue_total_files = len(jobs)
                self.queue_completed_files = 0

            self.ui_progress(0)
            self.update_stats_cards()
            first_name = jobs[0].get("file_name", "") if jobs else ""
            self.ui_log(f"Resuming {len(jobs)} stopped/partial file(s) without rechecking the full Internet Archive list.")
            if first_name:
                self.ui_log(f"First resume file: {first_name}")
            self.ui_status(f"Resuming {len(jobs)} stopped/partial file(s)")

            self.run_dynamic_scheduler("Resumed queue", len(jobs), 0, jobs)

            if self.stop_requested and self.resume_pending_jobs:
                self.ui_status(f"Stopped - {len(self.resume_pending_jobs)} file(s) can still be resumed")
            else:
                self.ui_status("Resume finished")

            self.save_history()
            self.save_autosave_state()
            self.update_stats_cards()

        finally:
            self.ui_queue.put(("buttons", False))
            self.save_last_settings()

    def run_downloads(self):
        self.ui_queue.put(("buttons", True))
        urls = self.get_urls()
        dest = self.dest_var.get().strip()
        config_file = self.config_var.get().strip() if self.use_config_var.get() else None

        try:
            os.makedirs(dest, exist_ok=True)

            # Full Start begins a fresh queue. Resume is only used if
            # resume_pending_jobs existed before entering this method.
            self.resume_pending_jobs.clear()
            self.autosave_after_first_download_start_done = False

            with self.progress_lock:
                self.queue_total_files = 0
                self.queue_completed_files = 0

            self.ui_progress(0)
            self.ui_file_progress(0, "Current file: none")
            self.ui_active_clear()
            with self.current_display_lock:
                self.active_start_order.clear()
                self.active_start_counter = 0
                self.current_display_row_id = None

            queue_items = [(u, self.extract_identifier(u)) for u in urls]
            self.ui_log(f"Start clicked for queue with {len(queue_items)} item(s).")

            if self.has_valid_check_cache():
                remaining_bytes = self.last_check_remaining_bytes
                self.ui_log("Using previous completed Refresh Queue result; URL/destination/extensions unchanged.")
                self.ui_status("Using previous Refresh Queue result")
            else:
                self.ui_log("Checking queue size now before downloads begin...")
                total_bytes, remaining_bytes, file_count = self.estimate_queue_sizes(queue_items, dest, config_file)
                if not self.stop_requested:
                    self.store_check_cache(total_bytes, remaining_bytes, file_count)

            if self.stop_requested:
                self.ui_status("Stopped during queue check")
                self.ui_log("Start cancelled during queue check. Press Start again to begin from the current in-memory stopped queue if available.")
                return

            if not self.check_destination_space(dest, remaining_bytes):
                self.ui_status("Stopped - not enough disk space")
                self.ui_log("Downloads were not started because the destination drive does not have enough free space.")
                return

            self.ui_log("You can change 'Downloads at once' during the run; the new value is used before each next file starts.")
            self.ui_log("Existing files count as completed when skipped.")
            self.ui_log("Each new Internet Archive site/item downloads into one top-level subfolder.")
            self.ui_log("Internal archive folder structure is preserved automatically.")
            self.ui_log("Skip check still detects both destination/item_identifier/file and older destination/file layouts.\n")

            for item_index, (source_url, identifier) in enumerate(queue_items, start=1):
                if self.stop_requested:
                    break

                if not identifier or " " in identifier:
                    error = "Invalid Internet Archive identifier or URL."
                    self.ui_log(f"ERROR for {source_url}: {error}")
                    self.ui_add_error(self.safe_row_id("ERROR", source_url), source_url, identifier or source_url, error)
                    continue

                self.ui_status(f"Item {item_index}/{len(queue_items)}: reading metadata for {identifier}")
                self.ui_log(f"Starting item {item_index}/{len(queue_items)}: {identifier}")

                try:
                    self.process_item(source_url, identifier, dest, config_file)
                except Exception as e:
                    self.ui_log(f"ERROR for {source_url}: {e}")
                    self.ui_add_error(self.safe_row_id("ERROR", source_url), source_url, identifier, str(e))

            self.ui_status("Finished" if not self.stop_requested else "Stopped")
            if not self.stop_requested:
                self.ui_file_progress(100, "Current file: queue complete")
            self.ui_log("\nQueue finished." if not self.stop_requested else "\nQueue stopped.")

        finally:
            self.ui_queue.put(("buttons", False))
            self.save_last_settings()
            self.save_history()

    def format_file_age(self, path):
        try:
            age_seconds = max(0, time.time() - os.path.getmtime(path))
        except Exception:
            return "unknown age"

        if age_seconds < 60:
            return f"{int(age_seconds)} seconds old"
        minutes = age_seconds / 60
        if minutes < 60:
            return f"{int(minutes)} minutes old"
        hours = minutes / 60
        if hours < 48:
            return f"{int(hours)} hours old"
        days = hours / 24
        if days < 60:
            return f"{int(days)} days old"
        months = days / 30
        if months < 24:
            return f"{int(months)} months old"
        years = days / 365
        return f"{years:.1f} years old"

    def ask_resume_part_file(self, part_path, part_size, expected_size, file_name):
        setting = self.part_action_var.get() if hasattr(self, "part_action_var") else "Ask, auto-start over after 5 minutes"

        if setting == "Always resume .part files":
            self.ui_log(f".part handling setting: automatically resume {part_path}")
            return True

        if setting == "Always start over":
            self.ui_log(f".part handling setting: automatically start over {part_path}")
            return False

        age_text = self.format_file_age(part_path)
        expected_text = self.format_bytes(expected_size) if expected_size else "unknown total size"

        # Tk must create dialogs on the UI thread. This method is called from
        # worker threads, so use root.after + Event to safely ask the user.
        result = {"value": False}
        done = threading.Event()

        def show_dialog():
            win = tk.Toplevel(self.root)
            win.title("Partial download found")
            win.geometry("720x380")
            win.transient(self.root)
            win.grab_set()

            frame = ttk.Frame(win, padding=14)
            frame.pack(fill=tk.BOTH, expand=True)

            ttk.Label(frame, text="Partial .part file found", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 8))

            details = (
                f"File: {file_name}\n"
                f"Partial size: {self.format_bytes(part_size)}\n"
                f"Expected size: {expected_text}\n"
                f"Partial file age: {age_text}\n\n"
                f"{part_path}"
            )

            text = tk.Text(frame, height=8, wrap="word", bg="#07101a", fg="#f4f8ff", relief="solid", bd=1)
            text.insert("1.0", details)
            text.configure(state="disabled")
            text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

            countdown_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=countdown_var, style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 10))

            button_frame = ttk.Frame(frame)
            button_frame.pack(fill=tk.X)

            seconds_left = {"value": 300}
            closed = {"done": False}

            def finish(value):
                if closed["done"]:
                    return
                closed["done"] = True
                result["value"] = bool(value)
                try:
                    win.grab_release()
                except Exception:
                    pass
                try:
                    win.destroy()
                except Exception:
                    pass
                done.set()

            def tick():
                if closed["done"]:
                    return
                remaining = seconds_left["value"]
                mins, secs = divmod(remaining, 60)
                countdown_var.set(
                    f"Choose Resume or Start Over. If no choice is made, this will automatically START OVER in {mins}:{secs:02d}."
                )
                if remaining <= 0:
                    finish(False)
                    return
                seconds_left["value"] -= 1
                win.after(1000, tick)

            ttk.Button(button_frame, text="Resume .part", command=lambda: finish(True)).pack(side=tk.LEFT)
            ttk.Button(button_frame, text="Start Over", command=lambda: finish(False)).pack(side=tk.LEFT, padx=(8, 0))

            win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
            tick()

        try:
            self.root.after(0, show_dialog)
            done.wait()
            return bool(result["value"])
        except Exception as e:
            self.ui_log(f"Could not show .part prompt; starting over by default: {e}")
            return False

    def get_part_file_info(self, local_path, expected_size=None, file_name=""):
        part_path = local_path + ".part"
        try:
            if os.path.exists(part_path):
                part_size = os.path.getsize(part_path)

                if expected_size is not None and part_size >= expected_size:
                    # Treat oversized/equal .part as suspicious and restart cleanly.
                    return part_path, part_size, False

                if part_size <= 0:
                    return part_path, part_size, False

                # Ask the user once per .part file per session.
                if part_path not in self.part_resume_choices:
                    resume_part = self.ask_resume_part_file(part_path, part_size, expected_size, file_name or os.path.basename(local_path))
                    self.part_resume_choices[part_path] = resume_part

                    if not resume_part:
                        try:
                            os.remove(part_path)
                            self.ui_log(f"Deleted partial .part file and will start over: {part_path}")
                        except Exception as e:
                            self.ui_log(f"Could not delete .part file {part_path}: {e}")
                        return part_path, 0, False

                    self.ui_log(
                        f"User chose to resume .part file: {part_path} "
                        f"({self.format_bytes(part_size)}, {self.format_file_age(part_path)})"
                    )

                return part_path, part_size, bool(self.part_resume_choices.get(part_path, False))
        except Exception as e:
            self.ui_log(f"Could not inspect .part file {part_path}: {e}")
        return part_path, 0, False

    def process_item(self, source_url, identifier, dest, config_file):
        item = get_item(identifier, config_file=config_file) if config_file else get_item(identifier)

        files = []
        for f in item.files:
            self.wait_while_check_paused(f"reading file list for {identifier}")

            if self.stop_requested:
                self.ui_log(f"Stopped while reading file list for {identifier}.")
                break

            name = f.get("name")
            size_raw = f.get("size")

            if not name:
                continue

            base = os.path.basename(name).lower()
            if self.should_skip_metadata_file(base):
                continue

            if not self.file_allowed_by_extension(name):
                continue

            try:
                expected_size = int(size_raw) if size_raw is not None else None
            except Exception:
                expected_size = None

            files.append((name, expected_size))

        if self.stop_requested and not files:
            self.ui_status("Stopped before downloads started")
            return

        if not files:
            raise RuntimeError("No downloadable files found, or item metadata could not be read.")

        total = len(files)
        completed_for_item = 0
        pending_jobs = []

        # queue_total_files is pre-counted during queue estimation for stable progress.
        self.ui_log(f"{identifier}: found {total} downloadable file(s).")

        for file_index, (file_name, expected_size) in enumerate(files, start=1):
            row_id = self.safe_row_id(identifier, file_name)
            local_path, exists_locally = self.find_existing_local_file(dest, identifier, file_name)
            size_text = self.format_bytes(expected_size)

            part_path, part_size, can_resume_part = self.get_part_file_info(local_path, expected_size, file_name)

            if exists_locally:
                local_size = os.path.getsize(local_path)

                if not self.size_check_var.get():
                    completed_for_item += 1
                    self.mark_completed()
                    self.record_history(identifier, file_name, local_path, expected_size, "Skipped", source_url)
                    self.ui_add_or_update_file(row_id, identifier, file_name, "Skipped", "100%", size_text, local_path, source_url)
                    self.ui_file_progress(100, f"Skipped existing file: {file_name}")
                    self.ui_detail_log(f"Skipped existing file without size check: {local_path}")
                    self.ui_status(f"{identifier}: {completed_for_item}/{total} complete, {total - completed_for_item} left")
                    continue

                if expected_size is not None and local_size == expected_size:
                    completed_for_item += 1
                    self.mark_completed()
                    self.record_history(identifier, file_name, local_path, expected_size, "Skipped", source_url)
                    self.ui_add_or_update_file(row_id, identifier, file_name, "Skipped", "100%", size_text, local_path, source_url)
                    self.ui_file_progress(100, f"Skipped complete file: {file_name}")
                    self.ui_detail_log(f"Skipped size-matched file: {local_path}")
                    self.ui_status(f"{identifier}: {completed_for_item}/{total} complete, {total - completed_for_item} left")
                    continue

                mismatch_pct = self.percent_from_sizes(local_size, expected_size) if expected_size else 0
                self.ui_add_or_update_file(row_id, identifier, file_name, "Redownloading", f"{mismatch_pct}%", size_text, local_path, source_url)
                self.ui_detail_log(
                    f"Redownloading size mismatch: {local_path} "
                    f"local={self.format_bytes(local_size)}, expected={size_text}"
                )
                # Keep the old file until a new .part download succeeds.
            else:
                if can_resume_part:
                    resume_pct = self.percent_from_sizes(part_size, expected_size) if expected_size else 0
                    self.ui_add_or_update_file(
                        row_id,
                        identifier,
                        file_name,
                        "Queued - resume .part",
                        f"{resume_pct}%",
                        size_text,
                        part_path,
                        source_url
                    )
                    self.ui_detail_log(
                        f"Found partial download to resume: {part_path} "
                        f"({self.format_bytes(part_size)} of {size_text})"
                    )
                else:
                    self.ui_add_or_update_file(row_id, identifier, file_name, "Queued", "0%", size_text, local_path, source_url)

            pending_jobs.append({
                "identifier": identifier,
                "source_url": source_url,
                "file_index": file_index,
                "file_name": file_name,
                "expected_size": expected_size,
                "local_path": local_path,
                "row_id": row_id,
                "size_text": size_text,
                "config_file": config_file,
                "dest": dest,
                "part_path": part_path,
                "resume_from_part": can_resume_part,
            })

        completed_for_item += self.run_dynamic_scheduler(identifier, total, completed_for_item, pending_jobs)

        self.ui_status(f"{identifier}: {completed_for_item}/{total} complete, {total - completed_for_item} left")
        self.ui_log(f"Finished item: {identifier} ({completed_for_item}/{total} complete, including skipped files).")



    def mark_completed(self):
        with self.progress_lock:
            self.queue_completed_files += 1
            if self.queue_total_files:
                percent = min(100, (self.queue_completed_files / self.queue_total_files) * 100)
            else:
                percent = 0
        self.ui_progress(percent)

    def register_active_download(self, row_id):
        with self.current_display_lock:
            self.active_start_counter += 1
            self.active_start_order[row_id] = self.active_start_counter

            if self.current_display_row_id is None:
                self.current_display_row_id = row_id

    def unregister_active_download(self, row_id):
        with self.current_display_lock:
            self.active_start_order.pop(row_id, None)

            if self.current_display_row_id == row_id:
                if self.active_start_order:
                    self.current_display_row_id = min(
                        self.active_start_order,
                        key=self.active_start_order.get
                    )
                else:
                    self.current_display_row_id = None

        try:
            self.scheduler_event.set()
        except Exception:
            pass

    def should_update_current_bar(self, row_id):
        with self.current_display_lock:
            return self.current_display_row_id == row_id



def main():
    root = tk.Tk()
    app = IADownloaderGUI(root)
    root.mainloop()
