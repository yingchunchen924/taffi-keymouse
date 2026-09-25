from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyautogui
import tkinter as tk
from PIL import Image, ImageTk
from pynput import keyboard as pynput_keyboard
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import APP_DISPLAY_NAME, APP_VERSION
from .database import Database
from .models import Macro, MacroFolder, ReferenceImage
from .paths import DB_PATH, EXPORT_DIR, ICON_ICO, ICON_PNG, LOG_DIR, ensure_dirs
from .player import Player
from .recorder import Recorder
from .repository import MacroRepository
from .utils import event_summary, normalize_event, screen_size
from .vision import VisionEngine


STATE_IDLE = "空闲"
STATE_RECORDING = "录制中"
STATE_PLAYING = "回放中"
STATE_PAUSED = "已暂停"


class Taffi3App:
    def __init__(self) -> None:
        ensure_dirs()
        logging.basicConfig(
            filename=str(LOG_DIR / "taffi3.log"),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )
        self.db = Database(DB_PATH)
        self.repo = MacroRepository(self.db)
        self.vision = VisionEngine()
        stored_folder_state = self.db.get_setting("folder_open_state", {})
        self.folder_open_state: Dict[str, bool] = {
            str(folder_id): bool(is_open)
            for folder_id, is_open in stored_folder_state.items()
            if isinstance(stored_folder_state, dict)
        } if isinstance(stored_folder_state, dict) else {}
        self.current_macro: Optional[Macro] = None
        self.events: List[Dict[str, Any]] = []
        self.references: List[ReferenceImage] = []
        self.state = STATE_IDLE
        self.state_started_at: Optional[float] = None
        self.pulse_on = False
        self.pending_refresh = False
        self.drag_origin: Optional[tuple[int, int, int, int]] = None

        self.colors = {
            "bg": "#f3f6fb",
            "panel": "#ffffff",
            "soft": "#f8fafc",
            "soft2": "#edf4ff",
            "line": "#d9e2ee",
            "ink": "#172033",
            "muted": "#64748b",
            "blue": "#2563eb",
            "green": "#0f9f6e",
            "red": "#e11d48",
            "gold": "#f5b942",
            "purple": "#6d5bd0",
            "cyan": "#0891b2",
            "slate": "#334155",
            "orange": "#f97316",
            "disabled": "#cbd5e1",
        }

        self.root = tk.Tk()
        self.root.title(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        self.root.configure(bg=self.colors["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_startup_geometry()
        if ICON_ICO.exists():
            try:
                self.root.iconbitmap(str(ICON_ICO))
            except Exception:
                pass

        self.status_var = tk.StringVar(value=STATE_IDLE)
        self.main_title_var = tk.StringVar(value="准备就绪")
        self.main_hint_var = tk.StringVar(value="选择脚本后，点击“开始录制”或“开始回放”。")
        self.timer_var = tk.StringVar(value="")
        self.current_var = tk.StringVar(value="未选择脚本")
        self.message_var = tk.StringVar(value="欢迎使用塔菲键鼠 3.2")
        self.event_count_var = tk.StringVar(value="0 个步骤")
        self.mouse_pos_var = tk.StringVar(value="等待录制点击坐标")
        self.search_var = tk.StringVar(value="")
        self.playback_mode_var = tk.StringVar(value=str(self.setting("playback_mode", "single")))
        self.playback_count_var = tk.IntVar(value=int(self.setting("playback_count", 3)))
        self.mode_summary_var = tk.StringVar(value="回放：单次")
        self.anchor_guard_var = tk.BooleanVar(value=bool(self.setting("anchor_guard", True)))
        self.topmost_var = tk.BooleanVar(value=bool(self.setting("topmost", False)))
        self.safe_confirm_var = tk.BooleanVar(value=bool(self.setting("safety_ack", False)))
        self.speed_var = tk.StringVar(value=str(self.setting("speed_label", "1.0x")))
        self.countdown_var = tk.IntVar(value=int(self.setting("countdown", 2)))
        self.loop_interval_var = tk.DoubleVar(value=float(self.setting("loop_interval", 0.4)))
        self.threshold_var = tk.DoubleVar(value=float(self.setting("threshold", 0.82)))
        self.anchor_radius_var = tk.IntVar(value=int(self.setting("anchor_radius", 240)))
        self.move_interval_var = tk.DoubleVar(value=float(self.setting("move_interval", 0.055)))
        self.anchor_point: Optional[tuple[int, int]] = None

        self.recorder = Recorder(
            on_event=self.on_recorded_event,
            should_ignore_point=self.is_point_inside_app,
            move_interval=lambda: float(self.move_interval_var.get()),
        )
        self.player = Player(
            db=self.db,
            status_callback=self.thread_status,
            progress_callback=self.thread_progress,
            ref_path_lookup=self.lookup_ref_path,
            settings_provider=self.player_settings,
        )
        self.hotkeys: Optional[pynput_keyboard.GlobalHotKeys] = None

        self.configure_style()
        self.build_ui()
        self.apply_topmost()
        self.install_hotkeys()
        self.ensure_welcome_macro()
        self.refresh_all()
        self.root.after(250, self.tick)
        if not self.safe_confirm_var.get():
            self.root.after(350, self.show_safety_dialog)

    def configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 9), background="#ffffff", fieldbackground="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#eef2f7", relief="flat")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", self.colors["ink"])])
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 9), padding=(16, 8), background="#e8eef6", foreground=self.colors["muted"])
        style.map("TNotebook.Tab", background=[("selected", self.colors["panel"])], foreground=[("selected", self.colors["ink"])])
        style.configure("TCombobox", padding=(5, 3), arrowsize=13)

    def apply_startup_geometry(self) -> None:
        screen_w = max(self.root.winfo_screenwidth(), 1280)
        screen_h = max(self.root.winfo_screenheight(), 720)
        width = min(max(1180, int(screen_w * 0.66)), screen_w - 120)
        height = min(max(760, int(screen_h * 0.76)), screen_h - 120)
        x = max(20, screen_w - width - 40)
        y = max(20, (screen_h - height) // 2)
        self.root.resizable(True, True)
        self.root.minsize(1, 1)
        self.root.wm_minsize(1, 1)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def enable_free_window_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self.start_free_window_drag, add="+")
        widget.bind("<B1-Motion>", self.free_window_drag, add="+")
        widget.bind("<ButtonRelease-1>", self.stop_free_window_drag, add="+")
        for child in widget.winfo_children():
            self.enable_free_window_drag(child)

    def start_free_window_drag(self, event: tk.Event) -> None:
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def free_window_drag(self, event: tk.Event) -> None:
        if not self.drag_origin:
            return
        start_x, start_y, window_x, window_y = self.drag_origin
        next_x = window_x + event.x_root - start_x
        next_y = window_y + event.y_root - start_y
        self.root.geometry(f"+{next_x}+{next_y}")

    def stop_free_window_drag(self, _event: tk.Event) -> None:
        self.drag_origin = None

    def build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self.build_header()
        self.build_sidebar()
        self.build_workspace()

    def build_header(self) -> None:
        header = tk.Frame(self.root, bg=self.colors["bg"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 8))
        header.grid_columnconfigure(2, weight=1)
        self.logo_img = None
        if ICON_PNG.exists():
            try:
                img = Image.open(ICON_PNG).resize((42, 42), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
                tk.Label(header, image=self.logo_img, bg=self.colors["bg"]).grid(row=0, column=0, rowspan=2, padx=(0, 10))
            except Exception:
                pass
        tk.Label(header, text=APP_DISPLAY_NAME, bg=self.colors["bg"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(header, text="给普通人用的本地键鼠自动化工作台", bg=self.colors["bg"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 9)).grid(row=1, column=1, sticky="w")
        self.status_badge = tk.Label(header, textvariable=self.status_var, bg="#eef4ff", fg=self.colors["blue"], padx=14, pady=6, font=("Microsoft YaHei UI", 9, "bold"))
        self.status_badge.grid(row=0, column=3, rowspan=2, sticky="e")
        self.enable_free_window_drag(header)

    def build_sidebar(self) -> None:
        side = tk.Frame(self.root, bg=self.colors["panel"], highlightbackground=self.colors["line"], highlightthickness=1)
        side.grid(row=1, column=0, sticky="nsw", padx=(16, 8), pady=(0, 14))
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(4, weight=1)
        tk.Label(side, text="脚本库", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        search = tk.Frame(side, bg=self.colors["panel"])
        search.grid(row=1, column=0, sticky="ew", padx=10)
        tk.Entry(search, textvariable=self.search_var, width=18, bg=self.colors["soft"], relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
        self.small_button(search, "搜索", self.refresh_macros, self.colors["blue"]).pack(side="left", padx=(6, 0))
        tools = tk.Frame(side, bg=self.colors["panel"])
        tools.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 8))
        self.small_button(tools, "新建", self.create_macro, self.colors["blue"]).pack(side="left", padx=(0, 4))
        self.small_button(tools, "文件夹", self.create_folder, self.colors["cyan"]).pack(side="left", padx=4)
        self.small_button(tools, "复制", self.duplicate_macro, self.colors["purple"]).pack(side="left", padx=4)
        self.small_button(tools, "导入", self.import_placeholder, self.colors["green"]).pack(side="left", padx=4)
        self.small_button(tools, "导出", self.export_macro, self.colors["green"]).pack(side="left", padx=4)
        hint = tk.Label(side, text="文件夹可分类管理脚本；双击脚本可加载。", bg=self.colors["panel"], fg=self.colors["muted"], wraplength=260, justify="left", font=("Microsoft YaHei UI", 8))
        hint.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.macro_tree = ttk.Treeview(side, columns=("events",), show="tree headings", height=18)
        self.macro_tree.heading("#0", text="分类 / 脚本")
        self.macro_tree.heading("events", text="步骤")
        self.macro_tree.column("#0", width=190, minwidth=90)
        self.macro_tree.column("events", width=52, anchor="center")
        self.macro_tree.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.macro_tree.bind("<<TreeviewSelect>>", self.select_macro)
        self.macro_tree.bind("<Double-Button-1>", self.select_macro)
        self.macro_tree.bind("<<TreeviewOpen>>", self.on_folder_tree_state_change)
        self.macro_tree.bind("<<TreeviewClose>>", self.on_folder_tree_state_change)
        bottom = tk.Frame(side, bg=self.colors["panel"])
        bottom.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.small_button(bottom, "删除", self.delete_macro, self.colors["red"]).pack(side="left")
        self.small_button(bottom, "改名", self.rename_macro, self.colors["cyan"]).pack(side="left", padx=(6, 0))
        self.small_button(bottom, "移动", self.move_macro_to_folder, self.colors["gold"], "#3b2a10").pack(side="left", padx=(6, 0))
        self.small_button(bottom, "目录", lambda: os.startfile(str(DB_PATH.parent)), self.colors["purple"]).pack(side="right")

    def build_workspace(self) -> None:
        main = tk.Frame(self.root, bg=self.colors["bg"])
        main.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(0, 14))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)
        self.build_action_panel(main)
        self.build_status_panel(main)
        self.build_settings_strip(main)
        self.build_tabs(main)
        footer = tk.Frame(main, bg=self.colors["bg"])
        footer.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        tk.Label(footer, textvariable=self.message_var, bg=self.colors["bg"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(footer, text="急停: Ctrl+Shift+F12 或把鼠标移到屏幕角落", bg=self.colors["bg"], fg=self.colors["red"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

    def build_action_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=self.colors["panel"], highlightbackground=self.colors["line"], highlightthickness=1)
        panel.grid(row=0, column=0, sticky="ew")
        panel.grid_columnconfigure(8, weight=1)
        self.btn_record = self.action_button(panel, "● 开始录制", self.start_recording, self.colors["red"], padx=18, pady=10, font_size=10)
        self.btn_record.grid(row=0, column=0, padx=(12, 5), pady=10)
        self.btn_stop = self.action_button(panel, "■ 停止", self.stop_action, "#475569", padx=16, pady=10, font_size=10)
        self.btn_stop.grid(row=0, column=1, padx=5, pady=10)
        self.btn_play = self.action_button(panel, "▶ 开始回放", self.start_playback, self.colors["green"], padx=18, pady=10, font_size=10)
        self.btn_play.grid(row=0, column=2, padx=5, pady=10)
        self.btn_pause = self.action_button(panel, "Ⅱ 暂停", self.pause_playback, self.colors["gold"], "#3b2a10", padx=14, pady=10)
        self.btn_pause.grid(row=0, column=3, padx=5, pady=10)
        self.btn_save = self.action_button(panel, "保存脚本", self.save_events, self.colors["blue"], padx=13, pady=9)
        self.btn_save.grid(row=0, column=4, padx=5, pady=10)
        self.btn_check = self.action_button(panel, "检测脚本", self.check_current_macro, self.colors["cyan"], padx=13, pady=9)
        self.btn_check.grid(row=0, column=5, padx=5, pady=10)
        self.btn_help = self.action_button(panel, "安全说明", self.show_safety_dialog, self.colors["purple"], padx=13, pady=9)
        self.btn_help.grid(row=0, column=6, padx=5, pady=10)
        summary = tk.Frame(panel, bg=self.colors["soft"], highlightbackground=self.colors["line"], highlightthickness=1)
        summary.grid(row=0, column=8, sticky="e", padx=12, pady=10)
        tk.Label(summary, textvariable=self.current_var, bg=self.colors["soft"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="top", anchor="e", padx=10, pady=(6, 0))
        tk.Label(summary, textvariable=self.mode_summary_var, bg=self.colors["soft"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 8)).pack(side="top", anchor="e", padx=10, pady=(0, 6))

    def build_status_panel(self, parent: tk.Frame) -> None:
        self.status_panel = tk.Frame(parent, bg="#edf4ff", highlightbackground="#c7d7fe", highlightthickness=1)
        self.status_panel.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.status_panel.grid_columnconfigure(1, weight=1)
        self.status_icon = tk.Label(self.status_panel, text="●", bg="#edf4ff", fg=self.colors["blue"], font=("Microsoft YaHei UI", 24, "bold"), width=3)
        self.status_icon.grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=10)
        tk.Label(self.status_panel, textvariable=self.main_title_var, bg="#edf4ff", fg=self.colors["ink"], font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=1, sticky="w", pady=(10, 1))
        self.status_hint = tk.Label(self.status_panel, textvariable=self.main_hint_var, bg="#edf4ff", fg=self.colors["muted"], font=("Microsoft YaHei UI", 9), justify="left")
        self.status_hint.grid(row=1, column=1, sticky="w", pady=(1, 10))
        self.timer_label = tk.Label(self.status_panel, textvariable=self.timer_var, bg="#edf4ff", fg=self.colors["blue"], font=("Consolas", 19, "bold"), padx=14)
        self.timer_label.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 14))

    def build_settings_strip(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=self.colors["panel"], highlightbackground=self.colors["line"], highlightthickness=1)
        panel.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        top = tk.Frame(panel, bg=self.colors["panel"])
        top.pack(fill="x", padx=12, pady=(9, 4))
        tk.Label(top, text="常用设置", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(0, 10))
        mode_box = tk.Frame(top, bg="#edf4ff", highlightbackground="#bfdbfe", highlightthickness=1)
        mode_box.pack(side="left", padx=(0, 10))
        tk.Label(mode_box, text="回放", bg="#edf4ff", fg=self.colors["blue"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(8, 4))
        for text, value in [("单次", "single"), ("指定次数", "count"), ("一直循环", "loop")]:
            tk.Radiobutton(
                mode_box,
                text=text,
                value=value,
                variable=self.playback_mode_var,
                command=self.update_playback_mode_ui,
                bg="#edf4ff",
                activebackground="#edf4ff",
                selectcolor="#ffffff",
                fg=self.colors["ink"],
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left", padx=(0, 3))
        self.playback_count_label = tk.Label(mode_box, text="次数", bg="#edf4ff", fg=self.colors["muted"], font=("Microsoft YaHei UI", 9))
        self.playback_count_label.pack(side="left", padx=(4, 3))
        self.playback_count_spin = tk.Spinbox(
            mode_box,
            textvariable=self.playback_count_var,
            from_=1,
            to=999,
            increment=1,
            width=4,
            justify="center",
            bg="#ffffff",
            relief="solid",
            bd=1,
        )
        self.playback_count_spin.pack(side="left", padx=(0, 8))
        tk.Label(top, text="单次适合测试；指定次数适合重复任务；一直循环需要手动停止。", bg=self.colors["panel"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(2, 0))

        bottom = tk.Frame(panel, bg=self.colors["panel"])
        bottom.pack(fill="x", padx=12, pady=(0, 9))
        tk.Label(bottom, text="速度", bg=self.colors["panel"], fg=self.colors["muted"]).pack(side="left", padx=(0, 4))
        ttk.Combobox(bottom, textvariable=self.speed_var, values=("0.25x", "0.5x", "0.75x", "1.0x", "1.5x", "2.0x", "3.0x"), width=7, state="readonly").pack(side="left", padx=(0, 10))
        tk.Label(bottom, text="开始前倒计时", bg=self.colors["panel"], fg=self.colors["muted"]).pack(side="left", padx=(0, 3))
        tk.Spinbox(bottom, textvariable=self.countdown_var, from_=0, to=10, increment=1, width=4, justify="center", bg=self.colors["soft"], relief="solid", bd=1).pack(side="left", padx=(0, 8))
        self.loop_interval_label = tk.Label(bottom, text="每次间隔", bg=self.colors["panel"], fg=self.colors["muted"])
        self.loop_interval_label.pack(side="left", padx=(0, 3))
        self.loop_interval_spin = tk.Spinbox(bottom, textvariable=self.loop_interval_var, from_=0.1, to=10.0, increment=0.1, width=5, justify="center", bg=self.colors["soft"], relief="solid", bd=1)
        self.loop_interval_spin.pack(side="left", padx=(0, 12))
        tk.Checkbutton(bottom, text="坐标保险", variable=self.anchor_guard_var, bg=self.colors["panel"], activebackground=self.colors["panel"], selectcolor="#edf4ff").pack(side="left", padx=(0, 8))
        tk.Checkbutton(bottom, text="窗口置顶", variable=self.topmost_var, command=self.apply_topmost, bg=self.colors["panel"], activebackground=self.colors["panel"], selectcolor="#edf4ff").pack(side="left", padx=(0, 8))
        self.update_playback_mode_ui()

    def build_tabs(self, parent: tk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=3, column=0, sticky="nsew")
        self.build_guide_tab()
        self.build_steps_tab()
        self.build_vision_tab()
        self.build_runs_tab()
        self.build_advanced_tab()

    def build_guide_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=self.colors["panel"])
        self.notebook.add(tab, text="上手指导")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tk.Label(tab, text="三步完成一次自动化", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 8))
        cards = [
            ("1. 新建脚本", "左侧点击“新建”，给脚本起一个容易看懂的名字。"),
            ("2. 开始录制", "点击顶部“开始录制”，切到目标窗口，正常操作鼠标和键盘。"),
            ("3. 停止并回放", "回到塔菲键鼠点击“停止”，检查步骤后点击“开始回放”。"),
            ("安全提醒", "不要录制密码、验证码、支付信息。跑偏时按 Ctrl+Shift+F12 急停。"),
        ]
        for index, (title, text) in enumerate(cards):
            card = self.info_card(tab, title, text)
            card.grid(row=1 + index // 2, column=index % 2, sticky="nsew", padx=(18 if index % 2 == 0 else 8, 18 if index % 2 else 8), pady=8)
        current = self.info_card(tab, "当前坐标", "这里只显示录制或回放真正使用到的坐标点，不会一直跟随鼠标乱跳。")
        current.grid(row=3, column=0, sticky="ew", padx=18, pady=8)
        tk.Label(current, textvariable=self.mouse_pos_var, bg=self.colors["soft"], fg=self.colors["blue"], font=("Consolas", 16, "bold")).pack(anchor="w", padx=12, pady=(0, 12))
        state = self.info_card(tab, "当前状态", "这里会同步显示录制、回放、暂停和失败状态。")
        state.grid(row=3, column=1, sticky="ew", padx=18, pady=8)
        tk.Label(state, textvariable=self.main_title_var, bg=self.colors["soft"], fg=self.colors["green"], font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=12, pady=(0, 12))

    def build_steps_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=self.colors["panel"])
        self.notebook.add(tab, text="步骤编辑")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        head = tk.Frame(tab, bg=self.colors["panel"])
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        tk.Label(head, text="脚本步骤", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(head, textvariable=self.event_count_var, bg=self.colors["panel"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)
        self.small_button(head, "下移", self.move_event_down, self.colors["gold"], "#3b2a10").pack(side="right", padx=3)
        self.small_button(head, "上移", self.move_event_up, self.colors["gold"], "#3b2a10").pack(side="right", padx=3)
        self.small_button(head, "删除", self.delete_event, self.colors["red"]).pack(side="right", padx=3)
        self.small_button(head, "编辑", self.edit_event, self.colors["blue"]).pack(side="right", padx=3)
        self.small_button(head, "识图校验", self.add_verify_event, self.colors["cyan"]).pack(side="right", padx=3)
        self.small_button(head, "识图点击", self.add_image_click_event, self.colors["cyan"]).pack(side="right", padx=3)
        self.small_button(head, "添加等待", self.add_wait_event, self.colors["purple"]).pack(side="right", padx=3)
        self.small_button(head, "添加文本", self.add_text_event, self.colors["purple"]).pack(side="right", padx=3)
        body = tk.Frame(tab, bg=self.colors["panel"])
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.event_list = tk.Listbox(body, bg="#ffffff", fg=self.colors["ink"], selectbackground="#dbeafe", font=("Consolas", 10), activestyle="none", relief="solid", bd=1)
        self.event_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.event_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.event_list.configure(yscrollcommand=scroll.set)
        self.event_list.bind("<Double-Button-1>", lambda _event: self.edit_event())

    def build_vision_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=self.colors["panel"])
        self.notebook.add(tab, text="识图图库")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        tk.Label(tab, text="参考图", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))
        tk.Label(tab, text="先导入按钮或图标截图，再把它添加为识图点击或识图校验。", bg=self.colors["panel"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))
        head = tk.Frame(tab, bg=self.colors["panel"])
        head.grid(row=0, column=0, sticky="e", padx=18, pady=(16, 4))
        self.small_button(head, "导入图片", self.import_reference_image, self.colors["green"]).pack(side="left", padx=3)
        self.small_button(head, "识图点击", self.add_image_click_event, self.colors["cyan"]).pack(side="left", padx=3)
        self.small_button(head, "识图校验", self.add_verify_event, self.colors["cyan"]).pack(side="left", padx=3)
        self.small_button(head, "试找一次", self.test_reference_find, self.colors["blue"]).pack(side="left", padx=3)
        self.small_button(head, "删除图片", self.delete_reference_image, self.colors["red"]).pack(side="left", padx=3)
        body = tk.Frame(tab, bg=self.colors["panel"])
        body.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=0)
        body.grid_rowconfigure(0, weight=1)
        self.ref_tree = ttk.Treeview(body, columns=("name", "size", "status"), show="headings")
        for col, text, width in [("name", "图片名称", 260), ("size", "尺寸", 90), ("status", "状态", 120)]:
            self.ref_tree.heading(col, text=text)
            self.ref_tree.column(col, width=width, anchor="center" if col != "name" else "w")
        self.ref_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.ref_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.ref_tree.configure(yscrollcommand=scroll.set)
        self.ref_tree.bind("<<TreeviewSelect>>", lambda _event: self.update_reference_preview())

        detail = tk.Frame(body, bg=self.colors["soft"], highlightbackground=self.colors["line"], highlightthickness=1, width=270)
        detail.grid(row=0, column=2, sticky="ns", padx=(12, 0))
        detail.grid_propagate(False)
        tk.Label(detail, text="图片预览", bg=self.colors["soft"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        self.ref_preview_holder = tk.Frame(detail, bg="#ffffff", highlightbackground=self.colors["line"], highlightthickness=1, width=230, height=135)
        self.ref_preview_holder.pack(anchor="w", padx=12)
        self.ref_preview_holder.pack_propagate(False)
        self.ref_preview_label = tk.Label(self.ref_preview_holder, text="未选择图片", bg="#ffffff", fg=self.colors["muted"], font=("Microsoft YaHei UI", 9), wraplength=200, justify="center")
        self.ref_preview_label.pack(fill="both", expand=True)
        self.ref_detail_var = tk.StringVar(value="选择一张参考图后，这里会显示尺寸和状态。")
        tk.Label(detail, textvariable=self.ref_detail_var, bg=self.colors["soft"], fg=self.colors["muted"], justify="left", wraplength=230, font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12, pady=(8, 12))
        guide = (
            "推荐流程:\n"
            "1. 导入目标按钮截图\n"
            "2. 点“试找一次”确认能找到\n"
            "3. 在步骤列表选中位置\n"
            "4. 添加识图点击或识图校验\n\n"
            "怎么运行:\n"
            "- 回放到那一行时才会找图\n"
            "- 识图点击: 找到后点击图片中心\n"
            "- 识图校验: 只检查画面是否出现\n\n"
            "截图建议:\n"
            "- 只截稳定的按钮或图标\n"
            "- 避开动态数字和闪烁区域\n"
            "- 太小或太大的图都不稳定"
        )
        tk.Label(detail, text=guide, bg=self.colors["soft"], fg=self.colors["ink"], justify="left", wraplength=230, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=(0, 12))
        self.ref_preview_image = None

    def build_runs_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=self.colors["panel"])
        self.notebook.add(tab, text="运行记录")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        head = tk.Frame(tab, bg=self.colors["panel"])
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        tk.Label(head, text="最近运行", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self.small_button(head, "刷新", self.refresh_runs, self.colors["blue"]).pack(side="right")
        self.run_tree = ttk.Treeview(tab, columns=("started", "ended", "status", "loops", "msg"), show="headings")
        for col, text, width in [("started", "开始", 150), ("ended", "结束", 150), ("status", "状态", 90), ("loops", "次数", 70), ("msg", "消息", 360)]:
            self.run_tree.heading(col, text=text)
            self.run_tree.column(col, width=width, anchor="center" if col != "msg" else "w")
        self.run_tree.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    def build_advanced_tab(self) -> None:
        tab = tk.Frame(self.notebook, bg=self.colors["panel"])
        self.notebook.add(tab, text="高级能力")
        tab.grid_columnconfigure(0, weight=1)
        tk.Label(tab, text="高级设置", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 6))
        tk.Label(tab, text="普通用户保持默认即可。这里主要给需要更细控制的人使用。", bg=self.colors["panel"], fg=self.colors["muted"], font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 12))
        grid = tk.Frame(tab, bg=self.colors["panel"])
        grid.grid(row=2, column=0, sticky="ew", padx=18)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        self.setting_card(grid, "识图灵敏度", "数值越高越严格，默认 0.82。", self.threshold_var, 0.60, 0.99, 0.01).grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=8)
        self.setting_card(grid, "坐标修正半径", "回放点击前，在附近寻找参考图修正位置。", self.anchor_radius_var, 80, 700, 20).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=8)
        self.setting_card(grid, "录制移动精度", "数值越小，记录鼠标移动越细。", self.move_interval_var, 0.02, 0.20, 0.005).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=8)
        notes = self.info_card(
            tab,
            "后续扩展方向",
            "视觉点击、流程条件、定时触发、脚本打包发行会继续完善；当前版本先保证录制、编辑、回放这条主线稳定清楚。",
        )
        notes.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 18))

    def info_card(self, parent: tk.Widget, title: str, body: str) -> tk.Frame:
        card = tk.Frame(parent, bg=self.colors["soft"], highlightbackground=self.colors["line"], highlightthickness=1)
        tk.Label(card, text=title, bg=self.colors["soft"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(card, text=body, bg=self.colors["soft"], fg=self.colors["muted"], wraplength=430, justify="left", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=(0, 12))
        return card

    def setting_card(self, parent: tk.Widget, title: str, hint: str, var: tk.Variable, start: float, end: float, inc: float) -> tk.Frame:
        card = tk.Frame(parent, bg=self.colors["soft"], highlightbackground=self.colors["line"], highlightthickness=1)
        tk.Label(card, text=title, bg=self.colors["soft"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        tk.Label(card, text=hint, bg=self.colors["soft"], fg=self.colors["muted"], wraplength=390, justify="left", font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12, pady=(0, 8))
        tk.Spinbox(card, textvariable=var, from_=start, to=end, increment=inc, width=9, justify="center", bg="#ffffff", relief="solid", bd=1).pack(anchor="w", padx=12, pady=(0, 12))
        return card

    def action_button(self, parent: tk.Widget, text: str, command: Any, color: str, fg: str = "white", padx: int = 14, pady: int = 8, font_size: int = 9) -> tk.Button:
        btn = tk.Button(parent, text=text, command=command, bg=color, fg=fg, activebackground=color, activeforeground=fg, bd=0, relief="flat", padx=padx, pady=pady, font=("Microsoft YaHei UI", font_size, "bold"), cursor="hand2")
        btn._taffi_bg = color
        btn._taffi_fg = fg
        return btn

    def small_button(self, parent: tk.Widget, text: str, command: Any, color: str, fg: str = "white") -> tk.Button:
        btn = tk.Button(parent, text=text, command=command, bg=color, fg=fg, activebackground=color, activeforeground=fg, bd=0, relief="flat", padx=9, pady=5, font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2")
        btn._taffi_bg = color
        btn._taffi_fg = fg
        return btn

    def setting(self, key: str, default: Any) -> Any:
        return self.db.get_setting(key, default)

    def update_playback_mode_ui(self) -> None:
        mode = self.playback_mode_var.get()
        try:
            count = max(1, int(self.playback_count_var.get()))
        except Exception:
            count = 1
            self.playback_count_var.set(count)
        if mode == "count":
            self.playback_count_spin.configure(state="normal", bg="#ffffff", fg=self.colors["ink"])
            self.playback_count_label.configure(fg=self.colors["ink"])
            self.loop_interval_spin.configure(state="normal", bg=self.colors["soft"], fg=self.colors["ink"])
            self.loop_interval_label.configure(fg=self.colors["muted"])
            self.mode_summary_var.set(f"回放：指定 {count} 次")
        elif mode == "loop":
            self.playback_count_spin.configure(state="disabled", disabledforeground=self.colors["muted"])
            self.playback_count_label.configure(fg=self.colors["muted"])
            self.loop_interval_spin.configure(state="normal", bg=self.colors["soft"], fg=self.colors["ink"])
            self.loop_interval_label.configure(fg=self.colors["muted"])
            self.mode_summary_var.set("回放：一直循环")
        else:
            self.playback_count_spin.configure(state="disabled", disabledforeground=self.colors["muted"])
            self.playback_count_label.configure(fg=self.colors["muted"])
            self.loop_interval_spin.configure(state="disabled", disabledforeground=self.colors["muted"])
            self.loop_interval_label.configure(fg=self.colors["muted"])
            self.mode_summary_var.set("回放：单次")

    def save_settings(self) -> None:
        mode = self.playback_mode_var.get()
        if mode not in ("single", "count", "loop"):
            mode = "single"
        try:
            playback_count = max(1, int(self.playback_count_var.get()))
        except Exception:
            playback_count = 1
            self.playback_count_var.set(playback_count)
        self.db.set_setting("playback_mode", mode)
        self.db.set_setting("playback_count", playback_count)
        self.db.set_setting("loop", mode == "loop")
        self.db.set_setting("anchor_guard", bool(self.anchor_guard_var.get()))
        self.db.set_setting("topmost", bool(self.topmost_var.get()))
        self.db.set_setting("safety_ack", bool(self.safe_confirm_var.get()))
        self.db.set_setting("speed_label", self.speed_var.get())
        self.db.set_setting("countdown", int(self.countdown_var.get()))
        self.db.set_setting("loop_interval", float(self.loop_interval_var.get()))
        self.db.set_setting("threshold", float(self.threshold_var.get()))
        self.db.set_setting("anchor_radius", int(self.anchor_radius_var.get()))
        self.db.set_setting("move_interval", float(self.move_interval_var.get()))
        self.capture_folder_open_state(persist=False)
        self.db.set_setting("folder_open_state", self.folder_open_state)

    def refresh_all(self) -> None:
        self.refresh_macros()
        self.refresh_events()
        self.refresh_refs()
        self.refresh_runs()
        self.update_buttons()

    def macro_tree_macro_iid(self, macro_id: int) -> str:
        return f"macro:{macro_id}"

    def macro_tree_folder_iid(self, folder_id: int) -> str:
        return f"folder:{folder_id}"

    def on_folder_tree_state_change(self, _event: Optional[tk.Event] = None) -> None:
        self.root.after_idle(self.capture_folder_open_state)

    def capture_folder_open_state(self, persist: bool = True) -> None:
        if not hasattr(self, "macro_tree"):
            return
        for folder_iid in self.macro_tree.get_children(""):
            item_type, folder_id = self.parse_macro_tree_iid(folder_iid)
            if item_type == "folder" and folder_id is not None:
                self.folder_open_state[str(folder_id)] = bool(self.macro_tree.item(folder_iid, "open"))
        if persist:
            self.db.set_setting("folder_open_state", self.folder_open_state)

    def parse_macro_tree_iid(self, iid: str) -> tuple[str, Optional[int]]:
        if iid.startswith("macro:"):
            try:
                return "macro", int(iid.split(":", 1)[1])
            except ValueError:
                return "", None
        if iid.startswith("folder:"):
            try:
                return "folder", int(iid.split(":", 1)[1])
            except ValueError:
                return "", None
        if iid.isdigit():
            return "macro", int(iid)
        return "", None

    def refresh_macros(self) -> None:
        self.capture_folder_open_state(persist=False)
        for item in self.macro_tree.get_children():
            self.macro_tree.delete(item)
        query = self.search_var.get().strip()
        for folder in self.db.list_folders():
            macros = self.db.list_macros_by_folder(folder.id, query)
            if query and not macros:
                continue
            folder_iid = self.macro_tree_folder_iid(folder.id)
            folder_text = f"[文件夹] {folder.name}"
            self.macro_tree.insert(
                "",
                "end",
                iid=folder_iid,
                text=folder_text,
                values=(f"{len(macros)}个",),
                open=self.folder_open_state.get(str(folder.id), True),
            )
            for macro in macros:
                star = "★ " if macro.favorite else ""
                macro_iid = self.macro_tree_macro_iid(macro.id)
                self.macro_tree.insert(folder_iid, "end", iid=macro_iid, text=f"  {star}{macro.name}", values=(macro.event_count,))
        if self.current_macro:
            macro_iid = self.macro_tree_macro_iid(self.current_macro.id)
            if self.macro_tree.exists(macro_iid):
                self.macro_tree.selection_set(macro_iid)
                self.macro_tree.focus(macro_iid)

    def refresh_events(self) -> None:
        self.event_list.delete(0, "end")
        for index, event in enumerate(self.events):
            self.event_list.insert("end", event_summary(index, event))
        self.event_count_var.set(f"{len(self.events)} 个步骤")
        self.sync_anchor_point_from_events()

    def refresh_refs(self) -> None:
        self.references = self.db.list_references(self.current_macro.id) if self.current_macro else []
        if not hasattr(self, "ref_tree"):
            return
        for item in self.ref_tree.get_children():
            self.ref_tree.delete(item)
        cur_w, cur_h = screen_size()
        for ref in self.references:
            status = self.reference_status(ref, cur_w, cur_h)
            self.ref_tree.insert("", "end", iid=str(ref.id), values=(ref.name, f"{ref.width}x{ref.height}", status))
        self.update_reference_preview()

    def reference_status(self, ref: ReferenceImage, screen_w: Optional[int] = None, screen_h: Optional[int] = None) -> str:
        path = Path(ref.file_path)
        if not path.exists():
            return "文件丢失"
        screen_w = screen_w or screen_size()[0]
        screen_h = screen_h or screen_size()[1]
        if ref.width < 8 or ref.height < 8:
            return "太小"
        if ref.width > screen_w or ref.height > screen_h:
            return "大于屏幕"
        if ref.width < 16 or ref.height < 16:
            return "偏小"
        if ref.width > screen_w * 0.5 or ref.height > screen_h * 0.5:
            return "偏大"
        return "可用"

    def update_reference_preview(self) -> None:
        if not hasattr(self, "ref_preview_label") or not hasattr(self, "ref_detail_var"):
            return
        ref = self.selected_reference()
        if not ref:
            self.ref_preview_image = None
            self.ref_preview_label.configure(image="", text="未选择图片")
            self.ref_detail_var.set("选择一张参考图后，这里会显示尺寸和状态。")
            return

        path = Path(ref.file_path)
        status = self.reference_status(ref)
        if not path.exists():
            self.ref_preview_image = None
            self.ref_preview_label.configure(image="", text="图片文件丢失\n请重新导入")
            self.ref_detail_var.set(f"名称: {ref.name}\n尺寸: {ref.width}x{ref.height}\n状态: 文件丢失")
            return

        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail((218, 123), Image.Resampling.LANCZOS)
            self.ref_preview_image = ImageTk.PhotoImage(image)
            self.ref_preview_label.configure(image=self.ref_preview_image, text="")
            advice = self.reference_advice(ref, status)
            self.ref_detail_var.set(f"名称: {ref.name}\n尺寸: {ref.width}x{ref.height}\n状态: {status}\n{advice}")
        except Exception as exc:
            logging.warning("参考图预览失败: %s", exc)
            self.ref_preview_image = None
            self.ref_preview_label.configure(image="", text="无法预览\n请重新导入")
            self.ref_detail_var.set(f"名称: {ref.name}\n尺寸: {ref.width}x{ref.height}\n状态: 无法预览")

    def reference_advice(self, ref: ReferenceImage, status: str) -> str:
        if status == "可用":
            return "建议: 先点“试找一次”，成功后再加入步骤。"
        if status == "太小":
            return "建议: 图片太小，识图无法稳定工作，请重新截大一点。"
        if status == "偏小":
            return "建议: 可以使用，但容易误差，建议截完整按钮或图标。"
        if status == "偏大":
            return "建议: 图片偏大，最好只截按钮、图标或固定文字。"
        if status == "大于屏幕":
            return "建议: 图片比当前屏幕还大，回放时无法匹配。"
        return "建议: 请重新导入这张图片。"

    def refresh_runs(self) -> None:
        for item in self.run_tree.get_children():
            self.run_tree.delete(item)
        macro_id = self.current_macro.id if self.current_macro else None
        for run in self.db.list_runs(macro_id=macro_id, limit=80):
            self.run_tree.insert("", "end", values=(run.started_at, run.ended_at or "", run.status, run.loops, run.message))

    def ensure_welcome_macro(self) -> None:
        if self.current_macro:
            return
        macros = self.db.list_macros()
        if macros:
            self.current_macro = macros[0]
            self.events = self.repo.load_events(macros[0].id)
            self.current_var.set(f"当前: {macros[0].name}")
            return
            macro = self.repo.create_macro("欢迎上手", "3.2 默认示例脚本")
        demo_events = [
            {"type": "note", "text": "这是一个示例脚本，帮助你理解步骤结构。", "time": 0.0},
            {"type": "wait", "duration": 1.0, "time": 0.3},
            {"type": "text", "text": "你好，我是塔菲键鼠 3.2", "time": 1.6},
        ]
        self.repo.save_events(macro.id, demo_events)
        self.current_macro = self.db.get_macro(macro.id)
        self.events = self.repo.load_events(macro.id)
        if self.current_macro:
            self.current_var.set(f"当前: {self.current_macro.name}")

    def selected_tree_item(self) -> str:
        sel = self.macro_tree.selection()
        return sel[0] if sel else ""

    def selected_folder_from_tree(self) -> Optional[MacroFolder]:
        kind, item_id = self.parse_macro_tree_iid(self.selected_tree_item())
        if kind != "folder" or item_id is None:
            return None
        return self.db.get_folder(item_id)

    def selected_macro_from_tree(self) -> Optional[Macro]:
        kind, item_id = self.parse_macro_tree_iid(self.selected_tree_item())
        if kind != "macro" or item_id is None:
            return None
        return self.db.get_macro(item_id)

    def selected_folder_id(self, default_current: bool = True) -> int:
        kind, item_id = self.parse_macro_tree_iid(self.selected_tree_item())
        if kind == "folder" and item_id is not None:
            return item_id
        if kind == "macro" and item_id is not None:
            macro = self.db.get_macro(item_id)
            if macro:
                return macro.folder_id
        if default_current and self.current_macro:
            return self.current_macro.folder_id
        return 1

    def choose_folder_dialog(self, title: str, initial_folder_id: int = 1) -> Optional[MacroFolder]:
        folders = self.db.list_folders()
        if not folders:
            return None
        initial = next((folder for folder in folders if folder.id == initial_folder_id), folders[0])
        result: Dict[str, Optional[MacroFolder]] = {"folder": None}

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=self.colors["panel"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.geometry(f"360x150+{self.root.winfo_x() + 80}+{self.root.winfo_y() + 80}")

        tk.Label(dlg, text="选择目标文件夹", bg=self.colors["panel"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        folder_var = tk.StringVar(value=initial.name)
        combo = ttk.Combobox(dlg, textvariable=folder_var, values=[folder.name for folder in folders], state="readonly", width=28)
        combo.pack(fill="x", padx=18)
        combo.focus_set()

        actions = tk.Frame(dlg, bg=self.colors["panel"])
        actions.pack(fill="x", padx=18, pady=16)

        def confirm() -> None:
            chosen = next((folder for folder in folders if folder.name == folder_var.get()), None)
            result["folder"] = chosen
            dlg.destroy()

        def cancel() -> None:
            dlg.destroy()

        self.small_button(actions, "取消", cancel, "#64748b").pack(side="right")
        self.small_button(actions, "确定", confirm, self.colors["blue"]).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda _event: confirm())
        dlg.bind("<Escape>", lambda _event: cancel())
        self.root.wait_window(dlg)
        return result["folder"]

    def create_macro(self) -> None:
        if self.state != STATE_IDLE:
            return
        name = simpledialog.askstring("新建脚本", "脚本名称:", initialvalue=time.strftime("新脚本_%m%d_%H%M"))
        if not name:
            return
        folder_id = self.selected_folder_id(default_current=True)
        macro = self.repo.create_macro(name, "", folder_id=folder_id)
        self.current_macro = macro
        self.events = []
        self.anchor_point = None
        self.current_var.set(f"当前: {macro.name}")
        self.refresh_all()
        self.message(f"已创建脚本: {macro.name}")

    def create_folder(self) -> None:
        if self.state != STATE_IDLE:
            return
        name = simpledialog.askstring("新建文件夹", "文件夹名称:", initialvalue="新文件夹")
        if not name:
            return
        name = name.strip()
        if not name:
            self.message("文件夹名称不能为空")
            return
        if self.db.get_folder_by_name(name):
            messagebox.showwarning("新建失败", "已经有同名文件夹了，请换一个名字。")
            return
        try:
            folder_id = self.db.create_folder(name)
            self.folder_open_state[str(folder_id)] = True
            self.db.set_setting("folder_open_state", self.folder_open_state)
        except Exception as exc:
            logging.exception("新建文件夹失败")
            messagebox.showerror("新建失败", str(exc))
            return
        self.refresh_macros()
        folder_iid = self.macro_tree_folder_iid(folder_id)
        if self.macro_tree.exists(folder_iid):
            self.macro_tree.selection_set(folder_iid)
            self.macro_tree.focus(folder_iid)
            self.macro_tree.see(folder_iid)
        self.message(f"已创建文件夹: {name}")

    def rename_macro(self) -> None:
        if self.state != STATE_IDLE:
            return
        folder = self.selected_folder_from_tree()
        if folder:
            if folder.id == 1:
                messagebox.showinfo("不能重命名", "“未分类”是默认文件夹，不能重命名。")
                return
            new_name = simpledialog.askstring("重命名文件夹", "新的文件夹名称:", initialvalue=folder.name)
            if not new_name:
                return
            new_name = new_name.strip()
            if not new_name:
                self.message("文件夹名称不能为空")
                return
            same_folder = self.db.get_folder_by_name(new_name)
            if same_folder and same_folder.id != folder.id:
                messagebox.showwarning("重命名失败", "已经有同名文件夹了，请换一个名字。")
                return
            self.db.update_folder(folder.id, new_name)
            self.refresh_macros()
            folder_iid = self.macro_tree_folder_iid(folder.id)
            if self.macro_tree.exists(folder_iid):
                self.macro_tree.selection_set(folder_iid)
                self.macro_tree.focus(folder_iid)
            self.message(f"文件夹已重命名为: {new_name}")
            return

        macro = self.selected_macro_from_tree() or self.current_macro
        if not macro:
            self.message("请先选择要重命名的脚本")
            return
        new_name = simpledialog.askstring("重命名脚本", "新的脚本名称:", initialvalue=macro.name)
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            self.message("脚本名称不能为空")
            return
        same_name = self.db.get_macro_by_name(new_name)
        if same_name and same_name.id != macro.id:
            messagebox.showwarning("重命名失败", "已经有同名脚本了，请换一个名字。")
            return
        self.db.update_macro_meta(macro.id, new_name, macro.description, macro.tags, macro.favorite)
        updated = self.db.get_macro(macro.id)
        if updated:
            self.current_macro = updated
            self.current_var.set(f"当前: {updated.name}")
        self.refresh_macros()
        self.update_buttons()
        self.message(f"已重命名为: {new_name}")

    def select_macro(self, _event: Any = None) -> None:
        if self.state != STATE_IDLE:
            return
        folder = self.selected_folder_from_tree()
        if folder:
            self.message(f"已选择文件夹: {folder.name}")
            return
        macro = self.selected_macro_from_tree()
        if not macro:
            return
        self.current_macro = macro
        self.events = self.repo.load_events(macro.id)
        self.current_var.set(f"当前: {macro.name}")
        self.refresh_events()
        self.refresh_refs()
        self.refresh_runs()
        self.update_buttons()
        self.message(f"已加载: {macro.name}")

    def duplicate_macro(self) -> None:
        macro = self.selected_macro_from_tree()
        if not macro:
            self.message("请先选择要复制的脚本")
            return
        copy = self.repo.duplicate_macro(macro.id)
        if copy:
            self.refresh_macros()
            self.message(f"已复制: {copy.name}")

    def delete_macro(self) -> None:
        folder = self.selected_folder_from_tree()
        if folder:
            if folder.id == 1:
                messagebox.showinfo("不能删除", "“未分类”是默认文件夹，不能删除。")
                return
            count = len(self.db.list_macros_by_folder(folder.id))
            text = f"确定删除文件夹“{folder.name}”吗？\n里面的 {count} 个脚本不会删除，会移动到“未分类”。"
            if not messagebox.askyesno("删除文件夹", text):
                return
            self.db.delete_folder(folder.id)
            if self.current_macro:
                self.current_macro = self.db.get_macro(self.current_macro.id)
            self.refresh_all()
            self.message(f"文件夹已删除，脚本已移到未分类: {folder.name}")
            return

        macro = self.selected_macro_from_tree()
        if not macro:
            self.message("请先选择要删除的脚本或文件夹")
            return
        if not messagebox.askyesno("删除脚本", f"确定删除“{macro.name}”吗？"):
            return
        self.repo.delete_macro(macro.id)
        if self.current_macro and self.current_macro.id == macro.id:
            self.current_macro = None
            self.events = []
            self.anchor_point = None
            self.current_var.set("未选择脚本")
        self.refresh_all()
        self.message("脚本已删除")

    def move_macro_to_folder(self) -> None:
        if self.state != STATE_IDLE:
            return
        macro = self.selected_macro_from_tree()
        if not macro:
            self.message("请先选择要移动的脚本")
            return
        folder = self.choose_folder_dialog("移动脚本", macro.folder_id)
        if not folder:
            return
        if folder.id == macro.folder_id:
            self.message("脚本已经在这个文件夹里")
            return
        self.db.move_macro_to_folder(macro.id, folder.id)
        updated = self.db.get_macro(macro.id)
        if self.current_macro and self.current_macro.id == macro.id and updated:
            self.current_macro = updated
        self.refresh_all()
        macro_iid = self.macro_tree_macro_iid(macro.id)
        if self.macro_tree.exists(macro_iid):
            self.macro_tree.selection_set(macro_iid)
            self.macro_tree.focus(macro_iid)
            self.macro_tree.see(macro_iid)
        self.message(f"已移动到文件夹: {folder.name}")

    def export_macro(self) -> None:
        if not self.current_macro:
            self.message("请先选择脚本")
            return
        out = self.repo.export_macro_zip(self.current_macro.id, EXPORT_DIR)
        self.message(f"已导出: {out}")

    def import_placeholder(self) -> None:
        path = filedialog.askopenfilename(
            title="导入塔菲脚本",
            filetypes=[("塔菲脚本", "*.zip *.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            folder_id = self.selected_folder_id(default_current=True)
            macro = self.repo.import_macro_file(Path(path))
            if not macro:
                messagebox.showwarning("导入失败", "没有从这个文件里识别出可导入的脚本。")
                return
            self.db.move_macro_to_folder(macro.id, folder_id)
            macro = self.db.get_macro(macro.id) or macro
            self.current_macro = macro
            self.events = self.repo.load_events(macro.id)
            self.current_var.set(f"当前: {macro.name}")
            self.refresh_all()
            self.message(f"已导入: {macro.name}")
        except Exception as exc:
            logging.exception("导入失败")
            messagebox.showerror("导入失败", str(exc))

    def check_current_macro(self) -> None:
        if not self.current_macro:
            self.message("请先选择要检测的脚本")
            return
        result = self.analyze_current_macro()
        title = result["title"]
        body = "\n".join(result["lines"])
        if result["errors"]:
            messagebox.showerror(title, body)
        elif result["warnings"]:
            messagebox.showwarning(title, body)
        else:
            messagebox.showinfo(title, body)
        self.message(result["summary"])

    def analyze_current_macro(self) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        ok: List[str] = []
        macro = self.current_macro
        events = [normalize_event(event) for event in self.events]
        assert macro is not None
        cur_w, cur_h = screen_size()
        allowed = {"mouse", "move", "scroll", "key", "text", "image_click", "verify", "wait", "note"}
        mouse_down: Dict[str, int] = {}
        key_down: Dict[str, int] = {}
        times: List[float] = []
        ref_by_name = {ref.name: ref for ref in self.references}
        image_names = {ref.name for ref in self.references if Path(ref.file_path).exists()}
        all_ref_names = {ref.name for ref in self.references}

        if not events:
            errors.append("脚本没有任何步骤，请先录制或添加步骤。")
        else:
            ok.append(f"已读取 {len(events)} 个步骤。")

        if macro.screen_width and macro.screen_height and (macro.screen_width != cur_w or macro.screen_height != cur_h):
            warnings.append(f"录制时屏幕是 {macro.screen_width}x{macro.screen_height}，当前屏幕是 {cur_w}x{cur_h}，回放坐标会自动缩放，但窗口位置变化仍可能跑偏。")
        else:
            ok.append("当前屏幕尺寸与脚本记录匹配。")

        for index, event in enumerate(events, start=1):
            kind = str(event.get("type", ""))
            if kind not in allowed:
                errors.append(f"第 {index} 步类型“{kind}”暂不支持，回放时会被跳过。")
            try:
                t = float(event.get("time", 0))
                times.append(t)
                if t < 0:
                    warnings.append(f"第 {index} 步时间小于 0，建议重新保存脚本。")
            except Exception:
                errors.append(f"第 {index} 步时间格式异常。")

            x = event.get("x")
            y = event.get("y")
            if kind in {"mouse", "move"}:
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    errors.append(f"第 {index} 步缺少可用坐标。")
                elif x < 0 or y < 0 or x >= cur_w or y >= cur_h:
                    warnings.append(f"第 {index} 步坐标 ({int(x)}, {int(y)}) 超出当前屏幕 {cur_w}x{cur_h}，可能点不到目标。")

            if kind == "mouse":
                button = str(event.get("button", "left"))
                action = str(event.get("action", ""))
                if action == "down":
                    mouse_down[button] = index
                elif action == "up":
                    mouse_down.pop(button, None)
                else:
                    warnings.append(f"第 {index} 步鼠标动作不明确。")

            if kind == "key":
                key = str(event.get("key", ""))
                action = str(event.get("action", ""))
                if not key:
                    errors.append(f"第 {index} 步缺少按键名称。")
                elif action == "down":
                    key_down[key] = index
                elif action == "up":
                    key_down.pop(key, None)
                else:
                    warnings.append(f"第 {index} 步按键动作不明确。")

            if kind in {"image_click", "verify"}:
                name = str(event.get("image_name") or event.get("image") or "")
                if not name:
                    errors.append(f"第 {index} 步需要参考图片，但没有图片名称。")
                elif name not in all_ref_names:
                    errors.append(f"第 {index} 步使用的参考图“{name}”没有登记，请重新导入图片。")
                elif name not in image_names:
                    errors.append(f"第 {index} 步使用的参考图“{name}”文件丢失，请重新导入图片。")
                else:
                    ref = ref_by_name.get(name)
                    if ref:
                        if ref.width < 8 or ref.height < 8:
                            errors.append(f"第 {index} 步使用的参考图“{name}”太小，至少需要 8x8 像素。")
                        elif ref.width < 16 or ref.height < 16:
                            warnings.append(f"第 {index} 步使用的参考图“{name}”偏小，建议截完整按钮或图标。")
                        if ref.width > cur_w or ref.height > cur_h:
                            errors.append(f"第 {index} 步使用的参考图“{name}”比当前屏幕还大，无法匹配。")
                        elif ref.width > cur_w * 0.5 or ref.height > cur_h * 0.5:
                            warnings.append(f"第 {index} 步使用的参考图“{name}”偏大，建议只截目标按钮、图标或固定文字。")
                        ok.append(f"第 {index} 步参考图“{name}”文件可读取。")

                if kind == "verify":
                    try:
                        retries = int(event.get("retries", 3))
                        if retries < 0:
                            errors.append(f"第 {index} 步校验重试次数小于 0。")
                        elif retries > 30:
                            warnings.append(f"第 {index} 步校验重试次数较多，运行时可能等待较久。")
                    except Exception:
                        errors.append(f"第 {index} 步校验重试次数格式异常。")
                if kind == "image_click":
                    try:
                        retries = int(event.get("retries", 5))
                        if retries < 0:
                            errors.append(f"第 {index} 步识图点击重试次数小于 0。")
                        elif retries > 30:
                            warnings.append(f"第 {index} 步识图点击重试次数较多，运行时可能等待较久。")
                    except Exception:
                        errors.append(f"第 {index} 步识图点击重试次数格式异常。")

            if kind == "wait":
                try:
                    duration = float(event.get("duration", 0))
                    if duration < 0:
                        errors.append(f"第 {index} 步等待时间小于 0。")
                    elif duration > 60:
                        warnings.append(f"第 {index} 步等待 {duration:g} 秒，时间较长。")
                except Exception:
                    errors.append(f"第 {index} 步等待时间格式异常。")

        if mouse_down:
            for button, index in mouse_down.items():
                errors.append(f"第 {index} 步按下了鼠标 {button}，但后面没有松开。")
        if key_down:
            for key, index in key_down.items():
                errors.append(f"第 {index} 步按下了 {key}，但后面没有松开。")
        if len(times) >= 2 and any(times[i] < times[i - 1] for i in range(1, len(times))):
            warnings.append("脚本步骤时间不是递增的，建议保存一次或重新录制。")

        if not errors and not warnings:
            ok.append("没有发现明显风险，可以先小范围试跑。")
        lines: List[str] = []
        if errors:
            lines.append("需要先处理的问题：")
            lines.extend(f"- {item}" for item in errors[:8])
            if len(errors) > 8:
                lines.append(f"- 还有 {len(errors) - 8} 个问题未显示。")
        if warnings:
            if lines:
                lines.append("")
            lines.append("建议注意：")
            lines.extend(f"- {item}" for item in warnings[:8])
            if len(warnings) > 8:
                lines.append(f"- 还有 {len(warnings) - 8} 条提醒未显示。")
        if ok:
            if lines:
                lines.append("")
            lines.append("已通过：")
            lines.extend(f"- {item}" for item in ok[:4])
        if errors:
            title = "脚本检测：需要修复"
            summary = f"检测发现 {len(errors)} 个问题，建议先修复"
        elif warnings:
            title = "脚本检测：有风险提醒"
            summary = f"检测发现 {len(warnings)} 条提醒，可以谨慎试跑"
        else:
            title = "脚本检测：可以试跑"
            summary = "检测通过，可以先小范围试跑"
        return {"title": title, "summary": summary, "lines": lines, "errors": errors, "warnings": warnings}

    def start_recording(self) -> None:
        if not self.current_macro:
            self.create_macro()
            if not self.current_macro:
                return
        if self.events and not messagebox.askyesno("重新录制", "当前脚本已有步骤。继续会清空旧步骤并重新录制，确定吗？"):
            return
        self.events = []
        self.anchor_point = None
        self.refresh_events()
        self.recorder.start()
        self.set_state(STATE_RECORDING)
        self.message("录制中，请切到目标窗口操作")

    def stop_action(self) -> None:
        if self.state == STATE_RECORDING:
            self.recorder.stop()
            self.set_state(STATE_IDLE)
            self.save_events()
            self.message(f"录制完成，已保存 {len(self.events)} 个步骤")
        elif self.state in (STATE_PLAYING, STATE_PAUSED):
            self.player.stop()
            self.set_state(STATE_IDLE)
            self.refresh_runs()
            self.message("已停止回放")

    def start_playback(self) -> None:
        if not self.current_macro:
            self.message("请先选择或新建脚本")
            return
        if not self.events:
            self.message("当前脚本还没有步骤，请先录制")
            return
        check = self.analyze_current_macro()
        if check["errors"]:
            messagebox.showerror("脚本检测未通过", "\n".join(check["lines"]))
            self.message("检测未通过，已阻止回放")
            return
        self.save_settings()
        mode = self.playback_mode_var.get()
        if mode == "count":
            self.message(f"准备回放 {max(1, int(self.playback_count_var.get()))} 次，请切到目标窗口")
        elif mode == "loop":
            self.message("准备一直循环回放，需要停止时点击“停止回放”")
        else:
            self.message("准备单次回放，请切到目标窗口")
        self.set_state(STATE_PLAYING)
        self.player.start(self.current_macro, self.events, self.player_settings())

    def pause_playback(self) -> None:
        if self.state not in (STATE_PLAYING, STATE_PAUSED):
            return
        paused = self.player.toggle_pause()
        self.set_state(STATE_PAUSED if paused else STATE_PLAYING)

    def save_events(self) -> None:
        if not self.current_macro:
            return
        self.repo.save_events(self.current_macro.id, self.events)
        self.current_macro = self.db.get_macro(self.current_macro.id)
        self.refresh_macros()
        self.refresh_events()
        self.message("脚本已保存")

    def add_wait_event(self) -> None:
        if not self.current_macro:
            self.create_macro()
            if not self.current_macro:
                return
        seconds = simpledialog.askfloat("添加等待", "等待秒数:", initialvalue=1.0, minvalue=0.05, maxvalue=3600)
        if seconds is None:
            return
        self.insert_event({"type": "wait", "duration": round(float(seconds), 3)})

    def add_text_event(self) -> None:
        if not self.current_macro:
            self.create_macro()
            if not self.current_macro:
                return
        text = simpledialog.askstring("添加文本", "要输入的文本:", initialvalue="")
        if text is None:
            return
        self.insert_event({"type": "text", "text": text})
        self.refresh_events()

    def selected_reference(self) -> Optional[ReferenceImage]:
        if not hasattr(self, "ref_tree"):
            return None
        sel = self.ref_tree.selection()
        if sel:
            ref_id = int(sel[0])
            for ref in self.references:
                if ref.id == ref_id:
                    return ref
        if len(self.references) == 1:
            return self.references[0]
        return None

    def ensure_macro_for_reference(self) -> bool:
        if self.current_macro:
            return True
        self.create_macro()
        return self.current_macro is not None

    def import_reference_image(self) -> None:
        if not self.ensure_macro_for_reference():
            return
        paths = filedialog.askopenfilenames(
            title="导入参考图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        imported = 0
        last_ref_id: Optional[int] = None
        for item in paths:
            try:
                ref = self.repo.import_reference(self.current_macro.id, Path(item))
                if ref:
                    imported += 1
                    last_ref_id = ref.id
            except Exception as exc:
                logging.warning("导入参考图失败: %s", exc)
        self.refresh_refs()
        if last_ref_id and hasattr(self, "ref_tree") and self.ref_tree.exists(str(last_ref_id)):
            self.ref_tree.selection_set(str(last_ref_id))
            self.ref_tree.see(str(last_ref_id))
            self.update_reference_preview()
        self.message(f"已导入 {imported} 张参考图" if imported else "没有导入参考图")

    def delete_reference_image(self) -> None:
        ref = self.selected_reference()
        if not ref:
            self.message("请先选择参考图")
            return
        if not messagebox.askyesno("删除参考图", f"确定删除“{ref.name}”吗？"):
            return
        self.repo.delete_reference(ref)
        self.refresh_refs()
        self.message("参考图已删除")

    def add_image_click_event(self) -> None:
        ref = self.selected_reference()
        if not ref:
            self.message("请先在“识图图库”选择参考图")
            try:
                self.notebook.select(self.notebook.tabs()[2])
            except Exception:
                pass
            return
        pos = self.insert_event({"type": "image_click", "image_name": ref.name, "retries": 5})
        self.message(f"已添加到第 {pos + 1} 步：回放到这里时查找“{ref.name}”，找到后点击图片中心")

    def add_verify_event(self) -> None:
        ref = self.selected_reference()
        if not ref:
            self.message("请先在“识图图库”选择参考图")
            try:
                self.notebook.select(self.notebook.tabs()[2])
            except Exception:
                pass
            return
        pos = self.insert_event({"type": "verify", "image_name": ref.name, "on_fail": "stop", "retries": 3})
        self.message(f"已添加到第 {pos + 1} 步：回放到这里时校验“{ref.name}”，找不到就停止")

    def test_reference_find(self) -> None:
        ref = self.selected_reference()
        if not ref:
            self.message("请先选择参考图")
            return
        path = Path(ref.file_path)
        if not path.exists():
            messagebox.showerror("试找失败", "参考图文件丢失，请重新导入。")
            return
        found = self.vision.find_file(path, float(self.threshold_var.get()))
        if found:
            messagebox.showinfo("试找成功", f"找到了“{ref.name}”。\n位置: X {found[0]} / Y {found[1]}\n相似度: {found[2]:.0%}")
            self.message(f"试找成功: {ref.name} {found[2]:.0%}")
        else:
            messagebox.showwarning("试找失败", f"当前屏幕没有找到“{ref.name}”。\n可以尝试把目标窗口切到前台，或降低高级设置里的识图灵敏度。")
            self.message(f"试找失败: {ref.name}")

    def insert_event(self, event: Dict[str, Any]) -> int:
        index = self.selected_event_index()
        pos = len(self.events) if index is None else index + 1
        if "time" not in event:
            event["time"] = self.time_for_insert_position(pos)
        self.events.insert(pos, normalize_event(event))
        self.refresh_events()
        self.select_event_index(pos)
        return pos

    def selected_event_index(self) -> Optional[int]:
        sel = self.event_list.curselection()
        return int(sel[0]) if sel else None

    def edit_event(self) -> None:
        index = self.selected_event_index()
        if index is None:
            self.message("请先选择要编辑的步骤")
            return
        event = self.events[index]
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑步骤")
        dlg.geometry("520x420")
        dlg.configure(bg=self.colors["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(
            dlg,
            text=event_summary(index, event),
            bg=self.colors["bg"],
            fg=self.colors["ink"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill="x", padx=16, pady=(16, 8))
        form = tk.Frame(dlg, bg=self.colors["bg"])
        form.pack(fill="both", expand=True, padx=16)
        vars_: Dict[str, tk.StringVar] = {}
        readonly_keys = {"type"}
        for row, (key, value) in enumerate(event.items()):
            tk.Label(form, text=key, bg=self.colors["bg"], fg=self.colors["muted"]).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=str(value))
            entry = tk.Entry(form, textvariable=var, relief="solid", bd=1)
            if key in readonly_keys:
                entry.configure(state="readonly", readonlybackground="#f1f5f9")
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
            vars_[key] = var
        form.grid_columnconfigure(1, weight=1)

        def save() -> None:
            new_event: Dict[str, Any] = {}
            for key, var in vars_.items():
                value = var.get()
                if key in ("x", "y", "dx", "dy", "retries"):
                    try:
                        new_event[key] = int(value)
                    except ValueError:
                        new_event[key] = value
                elif key in ("time", "duration"):
                    try:
                        new_event[key] = float(value)
                    except ValueError:
                        new_event[key] = value
                else:
                    new_event[key] = value
            self.events[index] = normalize_event(new_event)
            self.refresh_events()
            self.select_event_index(index)
            dlg.destroy()
            self.message("步骤已更新")

        self.action_button(dlg, "保存修改", save, self.colors["blue"]).pack(pady=12)

    def delete_event(self) -> None:
        index = self.selected_event_index()
        if index is None:
            self.message("请先选择要删除的步骤")
            return
        del self.events[index]
        self.refresh_events()
        if self.events:
            self.select_event_index(min(index, len(self.events) - 1))
        self.message("步骤已删除")

    def move_event_up(self) -> None:
        index = self.selected_event_index()
        if index is None:
            self.message("请先选择要移动的步骤")
            return
        if index <= 0:
            return
        self.events[index - 1], self.events[index] = self.events[index], self.events[index - 1]
        self.refresh_events()
        self.select_event_index(index - 1)
        self.message("步骤已上移")

    def move_event_down(self) -> None:
        index = self.selected_event_index()
        if index is None:
            self.message("请先选择要移动的步骤")
            return
        if index >= len(self.events) - 1:
            return
        self.events[index + 1], self.events[index] = self.events[index], self.events[index + 1]
        self.refresh_events()
        self.select_event_index(index + 1)
        self.message("步骤已下移")

    def on_recorded_event(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        self.capture_anchor_point(event)
        if not self.pending_refresh:
            self.pending_refresh = True
            self.root.after(160, self.flush_record_refresh)

    def flush_record_refresh(self) -> None:
        self.pending_refresh = False
        self.refresh_events()

    def next_time(self) -> float:
        if not self.events:
            return 0.0
        return round(float(self.events[-1].get("time", 0)) + 0.3, 3)

    def time_for_insert_position(self, pos: int) -> float:
        if not self.events:
            return 0.0
        if pos <= 0:
            first = float(self.events[0].get("time", 0))
            return round(max(0.0, first - 0.05), 3)
        if pos >= len(self.events):
            return self.next_time()

        prev_time = float(self.events[pos - 1].get("time", 0))
        next_time = float(self.events[pos].get("time", prev_time + 0.1))
        if next_time > prev_time:
            return round(prev_time + (next_time - prev_time) / 2, 3)
        return round(prev_time + 0.03, 3)

    def set_state(self, state: str) -> None:
        self.state = state
        self.status_var.set(state)
        if state == STATE_IDLE:
            self.state_started_at = None
        elif state in (STATE_RECORDING, STATE_PLAYING) and self.state_started_at is None:
            self.state_started_at = time.time()
        self.apply_activity_state()
        self.update_buttons()

    def apply_activity_state(self) -> None:
        data = {
            STATE_IDLE: ("#eef4ff", "#c7d7fe", self.colors["blue"], "●", "准备就绪", "选择脚本后，点击“开始录制”或“开始回放”。"),
            STATE_RECORDING: ("#fff1f2", "#fecdd3", self.colors["red"], "●", "正在录制键鼠动作", "请切到目标窗口操作。完成后回到塔菲键鼠，点击“停止录制”。"),
            STATE_PLAYING: ("#ecfdf3", "#bbf7d0", self.colors["green"], "▶", "正在回放脚本", "请不要移动目标窗口。需要中断时点击“停止回放”或使用急停。"),
            STATE_PAUSED: ("#fffbeb", "#fde68a", "#9a6700", "Ⅱ", "回放已暂停", "点击“继续”恢复，或点击“停止回放”结束。"),
        }[self.state]
        bg, line, accent, icon, title, hint = data
        if self.state in (STATE_RECORDING, STATE_PLAYING) and self.pulse_on:
            bg = "#ffffff"
        self.status_panel.configure(bg=bg, highlightbackground=line)
        for widget in (self.status_icon, self.status_hint, self.timer_label):
            widget.configure(bg=bg)
        for child in self.status_panel.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=bg)
        self.status_icon.configure(text=icon, fg=accent)
        self.timer_label.configure(fg=accent)
        self.status_badge.configure(bg=bg, fg=accent)
        self.main_title_var.set(title)
        self.main_hint_var.set(hint)

    def update_buttons(self) -> None:
        has_macro = self.current_macro is not None
        has_events = bool(self.events)
        self.btn_record.configure(text="● 正在录制" if self.state == STATE_RECORDING else "● 开始录制")
        self.btn_stop.configure(text="■ 停止录制" if self.state == STATE_RECORDING else ("■ 停止回放" if self.state in (STATE_PLAYING, STATE_PAUSED) else "■ 停止"))
        self.btn_play.configure(text="▶ 正在回放" if self.state == STATE_PLAYING else "▶ 开始回放")
        self.btn_pause.configure(text="▶ 继续" if self.state == STATE_PAUSED else "Ⅱ 暂停")
        self.button_state(self.btn_record, has_macro and self.state == STATE_IDLE)
        self.button_state(self.btn_stop, self.state in (STATE_RECORDING, STATE_PLAYING, STATE_PAUSED))
        self.button_state(self.btn_play, has_macro and has_events and self.state == STATE_IDLE)
        self.button_state(self.btn_pause, self.state in (STATE_PLAYING, STATE_PAUSED))
        self.button_state(self.btn_save, has_macro and self.state == STATE_IDLE)
        self.button_state(self.btn_check, has_macro and self.state == STATE_IDLE)
        if self.state == STATE_RECORDING:
            self.btn_record.configure(bg="#fff1f2", fg=self.colors["red"], disabledforeground=self.colors["red"])
            self.btn_stop.configure(bg=self.colors["red"], fg="white")
        elif self.state == STATE_PLAYING:
            self.btn_play.configure(bg="#ecfdf3", fg=self.colors["green"], disabledforeground=self.colors["green"])
            self.btn_stop.configure(bg=self.colors["red"], fg="white")
        elif self.state == STATE_PAUSED:
            self.btn_pause.configure(bg="#fffbeb", fg="#9a6700")

    def button_state(self, button: tk.Button, enabled: bool) -> None:
        if enabled:
            bg = getattr(button, "_taffi_bg", self.colors["blue"])
            fg = getattr(button, "_taffi_fg", "white")
            button.configure(state="normal", cursor="hand2", bg=bg, fg=fg, activebackground=bg, activeforeground=fg, disabledforeground=fg)
        else:
            button.configure(state="disabled", cursor="arrow", bg=self.colors["disabled"], fg="#f8fafc", activebackground=self.colors["disabled"], activeforeground="#f8fafc", disabledforeground="#f8fafc")

    def message(self, text: str) -> None:
        self.message_var.set(text)
        logging.info(text)

    def thread_status(self, text: str) -> None:
        self.root.after(0, lambda: self.message(text))

    def thread_progress(self, index: int, total: int) -> None:
        self.root.after(0, lambda: self.apply_progress(index))

    def apply_progress(self, index: int) -> None:
        self.select_event_index(index)
        if 0 <= index < len(self.events):
            self.capture_anchor_point(self.events[index])

    def select_event_index(self, index: int) -> None:
        try:
            self.event_list.selection_clear(0, "end")
            self.event_list.selection_set(index)
            self.event_list.see(index)
        except Exception:
            pass

    def player_settings(self) -> Dict[str, Any]:
        try:
            speed = float(str(self.speed_var.get()).replace("x", ""))
        except ValueError:
            speed = 1.0
        mode = self.playback_mode_var.get()
        if mode not in ("single", "count", "loop"):
            mode = "single"
        try:
            playback_count = max(1, int(self.playback_count_var.get()))
        except Exception:
            playback_count = 1
        ref_map = {r.name: r.file_path for r in self.references if Path(r.file_path).exists()}
        return {
            "playback_mode": mode,
            "playback_count": playback_count,
            "loop": mode == "loop",
            "anchor_guard": bool(self.anchor_guard_var.get()),
            "speed": speed,
            "countdown": int(self.countdown_var.get()),
            "loop_interval": float(self.loop_interval_var.get()),
            "threshold": float(self.threshold_var.get()),
            "anchor_radius": int(self.anchor_radius_var.get()),
            "reference_files": list(ref_map.values()),
            "reference_map": ref_map,
        }

    def lookup_ref_path(self, image_name: str) -> Optional[Path]:
        for ref in self.references:
            if ref.name == image_name and Path(ref.file_path).exists():
                return Path(ref.file_path)
        return None

    def capture_anchor_point(self, event: Dict[str, Any]) -> None:
        x = event.get("x")
        y = event.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            self.anchor_point = (int(x), int(y))
            self.mouse_pos_var.set(f"X: {int(x)}   Y: {int(y)}")

    def sync_anchor_point_from_events(self) -> None:
        self.anchor_point = None
        for event in reversed(self.events):
            x = event.get("x")
            y = event.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                self.anchor_point = (int(x), int(y))
                break
        if self.anchor_point:
            self.mouse_pos_var.set(f"X: {self.anchor_point[0]}   Y: {self.anchor_point[1]}")
        elif self.state == STATE_PLAYING:
            self.mouse_pos_var.set("当前步骤没有坐标")
        else:
            self.mouse_pos_var.set("等待录制点击坐标")

    def is_point_inside_app(self, x: int, y: int) -> bool:
        try:
            self.root.update_idletasks()
            gx, gy = self.root.winfo_rootx(), self.root.winfo_rooty()
            gw, gh = self.root.winfo_width(), self.root.winfo_height()
            return gx <= x <= gx + gw and gy <= y <= gy + gh
        except Exception:
            return False

    def apply_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def emergency_stop(self) -> None:
        self.recorder.stop()
        self.player.stop()
        self.set_state(STATE_IDLE)
        self.refresh_runs()
        self.message("急停已触发")

    def install_hotkeys(self) -> None:
        try:
            self.hotkeys = pynput_keyboard.GlobalHotKeys(
                {
                    "<ctrl>+<shift>+<f12>": lambda: self.root.after(0, self.emergency_stop),
                    "<ctrl>+<shift>+r": lambda: self.root.after(0, self.start_recording),
                    "<ctrl>+<shift>+b": lambda: self.root.after(0, self.start_playback),
                    "<ctrl>+<shift>+p": lambda: self.root.after(0, self.pause_playback),
                }
            )
            self.hotkeys.daemon = True
            self.hotkeys.start()
        except Exception as exc:
            logging.warning("注册全局快捷键失败: %s", exc)

    def show_safety_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("安全启动确认")
        dlg.geometry("560x330")
        dlg.configure(bg=self.colors["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="塔菲键鼠安全说明", bg=self.colors["bg"], fg=self.colors["ink"], font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(22, 8))
        text = (
            "塔菲键鼠会在录制时监听键盘和鼠标，并在回放时模拟你的操作。\n\n"
            "软件本地运行，不隐藏、不自启动、不安装证书、不修改系统代理、不注入其他进程。\n\n"
            "请不要录制密码、验证码、支付信息。脚本跑偏时按 Ctrl+Shift+F12 或把鼠标移到屏幕角落急停。"
        )
        tk.Label(dlg, text=text, bg=self.colors["bg"], fg=self.colors["ink"], justify="left", wraplength=500, font=("Microsoft YaHei UI", 10)).pack(padx=24, pady=8)

        def accept() -> None:
            self.safe_confirm_var.set(True)
            self.save_settings()
            dlg.destroy()

        self.action_button(dlg, "我知道了", accept, self.colors["blue"]).pack(pady=14)

    def tick(self) -> None:
        if self.state in (STATE_RECORDING, STATE_PLAYING, STATE_PAUSED) and self.state_started_at:
            elapsed = max(0, int(time.time() - self.state_started_at))
            self.timer_var.set(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        else:
            self.timer_var.set("")
        if self.state in (STATE_RECORDING, STATE_PLAYING):
            self.pulse_on = not self.pulse_on
            self.apply_activity_state()
        elif self.pulse_on:
            self.pulse_on = False
            self.apply_activity_state()
        if self.state in (STATE_PLAYING, STATE_PAUSED) and not self.player.running:
            self.set_state(STATE_IDLE)
            self.refresh_runs()
        self.root.after(250, self.tick)

    def on_close(self) -> None:
        try:
            self.save_settings()
            self.recorder.stop()
            self.player.stop()
            if self.hotkeys:
                self.hotkeys.stop()
            self.db.close()
        except Exception:
            logging.debug("关闭清理异常", exc_info=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
