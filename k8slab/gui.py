"""The desktop GUI: live topology canvas + terminal + handbook, in pure Tkinter.

Layout
------
    +--------------------------------------------------------------+
    | toolbar: stats - namespace - view - tick - docker - export    |
    +----------+---------------------------------------------------+
    | handbook |                topology canvas                    |
    |  + labs  +---------------------------------------------------+
    |          | terminal | yaml editor | handbook page | details   |
    +----------+---------------------------------------------------+
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Dict, List, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:                                  # pragma: no cover
    raise SystemExit(
        "This build of Python has no tkinter.\n"
        "  Debian/Ubuntu : sudo apt install python3-tk\n"
        "  Fedora        : sudo dnf install python3-tkinter\n"
        "  macOS (brew)  : brew install python-tk\n"
        f"  (original error: {exc})\n"
        "Or run the browser UI instead:  python k8s_lab.py --web")

from . import export, handbook, labs as lab_index, topology
from .controllers import reconcile
from .kubectl import Shell
from .model import deep_get
from .topology import PALETTE, STATUS_COLORS

# --------------------------------------------------------------------------
BG = "#0b1220"
PANEL = "#111c31"
PANEL2 = "#16233c"
STROKE = "#25344f"
TEXT = "#e2e8f0"
MUTED = "#8b9bb4"
ACCENT = "#38bdf8"
OK = "#22c55e"
WARN = "#f59e0b"
ERR = "#ef4444"


class LabApp:
    def __init__(self, root: tk.Tk, base_dir: str = ".", live: bool = False,
                 web_port: int = 8899):
        self.root = root
        self.shell = Shell(base_dir=base_dir, live=live)
        self.base_dir = os.path.abspath(base_dir)
        self.web_port = web_port
        self.view = tk.StringVar(value="logical")
        self.namespace = tk.StringVar(value="default")
        self.autotick = tk.BooleanVar(value=True)
        self.show_system = tk.BooleanVar(value=False)
        self.zoom = 1.0
        self.graph: Optional[topology.Graph] = None
        self.canvas_items: Dict[int, str] = {}
        self.selected: Optional[str] = None
        self.images: Dict[str, tk.PhotoImage] = {}
        self.page_index = 0
        self.current_topic = None
        self.history: List[str] = []
        self.history_pos = 0
        self.lab_queue: List[str] = []
        self.lab_running = False
        self.editor_memory: Dict[str, str] = {}
        self.editor_pristine = ""
        self.progress_path = os.path.join(self.base_dir, "lab-progress.json")
        self.progress = self._load_progress()
        self.progress_var = tk.BooleanVar(value=False)

        root.title("k8s-practice-lab  -  Kubernetes Handbook, hands on")
        root.geometry("1560x950")
        root.minsize(1100, 700)
        root.configure(bg=BG)
        self._style()
        self._build()
        self._bind_keys()
        self.refresh(initial=True)
        self._tick_loop()
        self.show_topic("introduction")
        self.update_progress_bar()
        self.log("k8s-practice-lab ready. Type `help`, pick a lab on the left, or run:\n"
                 "  kubectl create deployment web --image=nginx:1.25 --replicas=3\n",
                 "muted")

    # ------------------------------------------------------------------
    # chrome
    # ------------------------------------------------------------------
    def _style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL,
                        bordercolor=STROKE, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Chip.TLabel", background=PANEL, foreground=TEXT, padding=(8, 3))
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI", 12, "bold"))
        style.configure("TButton", background=PANEL2, foreground=TEXT, borderwidth=0,
                        padding=(10, 5), focuscolor=PANEL2)
        style.map("TButton", background=[("active", "#22344f")])
        style.configure("Accent.TButton", background="#0e7490", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#0891b2")])
        style.configure("Docker.TButton", background="#1d63ed", foreground="#ffffff")
        style.map("Docker.TButton", background=[("active", "#2f74ff")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=(14, 6), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL2)],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, borderwidth=0, rowheight=22)
        style.map("Treeview", background=[("selected", "#1d3557")])
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=TEXT, arrowcolor=TEXT)
        style.configure("TCheckbutton", background=BG, foreground=MUTED)
        style.configure("Horizontal.TProgressbar", background=ACCENT,
                        troughcolor=PANEL)

    def _build(self) -> None:
        self._build_toolbar()
        outer = ttk.PanedWindow(self.root, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self._build_sidebar(outer)
        right = ttk.PanedWindow(outer, orient="vertical")
        outer.add(right, weight=4)
        self._build_canvas(right)
        self._build_bottom(right)
        self._build_statusbar()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(8, 6))

        ttk.Label(bar, text="k8s-practice-lab", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text="  simulated cluster  ", style="Muted.TLabel").pack(side="left")

        self.chips: Dict[str, ttk.Label] = {}
        for key, text in (("nodes", "nodes 0"), ("pods", "pods 0"),
                          ("running", "running 0"), ("problems", "problems 0"),
                          ("tick", "tick 0")):
            chip = ttk.Label(bar, text=text, style="Chip.TLabel")
            chip.pack(side="left", padx=3)
            self.chips[key] = chip

        right = ttk.Frame(bar)
        right.pack(side="right")

        ttk.Button(right, text="Deploy to Docker", style="Docker.TButton",
                   command=self.open_docker_panel).pack(side="right", padx=(6, 0))
        ttk.Button(right, text="Web UI", command=self.open_web_ui).pack(side="right",
                                                                       padx=3)
        ttk.Button(right, text="Export SVG", command=self.export_svg).pack(side="right",
                                                                          padx=3)
        ttk.Button(right, text="Reset", command=self.reset_lab).pack(side="right",
                                                                     padx=3)
        ttk.Button(right, text="Tick +5", command=lambda: self.tick(5)).pack(
            side="right", padx=3)
        ttk.Checkbutton(right, text="auto", variable=self.autotick).pack(side="right",
                                                                        padx=3)
        ttk.Separator(right, orient="vertical").pack(side="right", fill="y", padx=8)

        ttk.Label(right, text="view", style="Muted.TLabel").pack(side="right")
        view_box = ttk.Combobox(right, textvariable=self.view, width=9,
                                state="readonly", values=("logical", "physical"))
        view_box.pack(side="right", padx=4)
        view_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Label(right, text="namespace", style="Muted.TLabel").pack(side="right",
                                                                     padx=(10, 0))
        self.ns_box = ttk.Combobox(right, textvariable=self.namespace, width=14,
                                   state="readonly")
        self.ns_box.pack(side="right", padx=4)
        self.ns_box.bind("<<ComboboxSelected>>", lambda e: self.on_namespace_change())

    def _build_sidebar(self, parent: ttk.PanedWindow) -> None:
        side = ttk.Frame(parent, width=280)
        parent.add(side, weight=1)
        book = ttk.Notebook(side)
        book.pack(fill="both", expand=True)

        # --- handbook tree -------------------------------------------
        topics_tab = ttk.Frame(book)
        book.add(topics_tab, text="Handbook")
        search_row = ttk.Frame(topics_tab)
        search_row.pack(fill="x", pady=(6, 4))
        self.search_var = tk.StringVar()
        entry = tk.Entry(search_row, textvariable=self.search_var, bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=1,
                         highlightbackground=STROKE, highlightcolor=ACCENT)
        entry.pack(fill="x", padx=6, ipady=4)
        entry.insert(0, "")
        self.search_var.trace_add("write", lambda *a: self.fill_topics())

        progress_row = ttk.Frame(topics_tab)
        progress_row.pack(fill="x", padx=6, pady=(6, 0))
        self.progress_label = ttk.Label(progress_row, text="progress  0 / 0",
                                        style="Muted.TLabel")
        self.progress_label.pack(side="left")
        ttk.Button(progress_row, text="Reset lab",
                   command=self.reset_lab).pack(side="right")
        self.progress_bar = ttk.Progressbar(topics_tab, style="Horizontal.TProgressbar",
                                            mode="determinate")
        self.progress_bar.pack(fill="x", padx=6, pady=(3, 6))
        self.progress_check = ttk.Checkbutton(
            topics_tab, text="I have covered this topic",
            variable=self.progress_var, command=self.toggle_progress)
        self.progress_check.pack(anchor="w", padx=6, pady=(0, 4))

        self.tree = ttk.Treeview(topics_tab, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self.on_topic_select)
        self.fill_topics()

        # --- labs ------------------------------------------------------
        labs_tab = ttk.Frame(book)
        book.add(labs_tab, text="Labs")
        self.lab_list = tk.Listbox(labs_tab, bg=PANEL, fg=TEXT, relief="flat",
                                   highlightthickness=0, selectbackground="#1d3557",
                                   activestyle="none")
        self.lab_list.pack(fill="both", expand=True, padx=6, pady=6)
        for lab in lab_index.LABS:
            self.lab_list.insert("end", lab.title)
        self.lab_list.bind("<<ListboxSelect>>", self.on_lab_select)

        buttons = ttk.Frame(labs_tab)
        buttons.pack(fill="x", padx=6, pady=(0, 8))
        ttk.Button(buttons, text="Load", command=self.load_lab).pack(side="left")
        ttk.Button(buttons, text="Run all", style="Accent.TButton",
                   command=self.run_lab).pack(side="left", padx=4)
        ttk.Button(buttons, text="Step", command=self.step_lab).pack(side="left")

        self.lab_note = tk.Text(labs_tab, height=8, bg=PANEL, fg=MUTED, relief="flat",
                                wrap="word", padx=8, pady=6,
                                font=("Segoe UI", 9))
        self.lab_note.pack(fill="x", padx=6, pady=(0, 8))
        self.lab_note.configure(state="disabled")

    def _build_canvas(self, parent: ttk.PanedWindow) -> None:
        wrap = ttk.Frame(parent)
        parent.add(wrap, weight=3)

        header = ttk.Frame(wrap)
        header.pack(fill="x")
        ttk.Label(header, text="Cluster topology", style="Title.TLabel").pack(
            side="left", padx=4, pady=(0, 4))
        self.topology_hint = ttk.Label(header, text="", style="Muted.TLabel")
        self.topology_hint.pack(side="left", padx=10)
        ttk.Checkbutton(header, text="system namespaces", variable=self.show_system,
                        command=self.refresh).pack(side="right")
        ttk.Button(header, text="fit", command=self.fit_zoom).pack(side="right", padx=3)
        ttk.Button(header, text="+", width=3,
                   command=lambda: self.set_zoom(self.zoom * 1.15)).pack(side="right")
        ttk.Button(header, text="-", width=3,
                   command=lambda: self.set_zoom(self.zoom / 1.15)).pack(side="right")

        holder = tk.Frame(wrap, bg=STROKE)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        vbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(holder, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        self.canvas.bind("<ButtonPress-2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>",
                         lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))

    def _build_bottom(self, parent: ttk.PanedWindow) -> None:
        book = ttk.Notebook(parent)
        parent.add(book, weight=2)
        self.bottom_book = book

        # --- terminal --------------------------------------------------
        term = ttk.Frame(book)
        book.add(term, text="Terminal")
        mono = ("Consolas" if sys.platform.startswith("win") else "Menlo"
                if sys.platform == "darwin" else "DejaVu Sans Mono")
        self.output = tk.Text(term, bg="#08101d", fg=TEXT, relief="flat", wrap="none",
                              insertbackground=TEXT, font=(mono, 10), padx=10, pady=8)
        out_bar = ttk.Scrollbar(term, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=out_bar.set, state="disabled")
        self.output.pack(side="top", fill="both", expand=True)
        out_bar.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self.output.tag_configure("cmd", foreground="#7dd3fc")
        self.output.tag_configure("err", foreground="#fca5a5")
        self.output.tag_configure("ok", foreground="#86efac")
        self.output.tag_configure("muted", foreground=MUTED)

        prompt_row = tk.Frame(term, bg=BG)
        prompt_row.pack(fill="x", side="bottom")
        self.prompt_label = tk.Label(prompt_row, text="default $", bg=BG, fg=ACCENT,
                                     font=(mono, 10))
        self.prompt_label.pack(side="left", padx=(10, 4), pady=6)
        self.entry = tk.Entry(prompt_row, bg="#08101d", fg=TEXT, relief="flat",
                              insertbackground=TEXT, font=(mono, 10),
                              highlightthickness=1, highlightbackground=STROKE,
                              highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, pady=6)
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Up>", self.on_history_up)
        self.entry.bind("<Down>", self.on_history_down)
        self.entry.bind("<Tab>", self.on_complete)
        ttk.Button(prompt_row, text="Run", style="Accent.TButton",
                   command=self.on_enter).pack(side="left", padx=6)
        ttk.Button(prompt_row, text="Clear",
                   command=self.clear_output).pack(side="left", padx=(0, 10))

        # --- script / yaml editor --------------------------------------
        editor = ttk.Frame(book)
        book.add(editor, text="Script / YAML")
        tools = ttk.Frame(editor)
        tools.pack(fill="x", pady=4)
        ttk.Button(tools, text="Open...", command=self.open_file).pack(side="left",
                                                                      padx=4)
        ttk.Button(tools, text="Save as...", command=self.save_file).pack(side="left")
        ttk.Button(tools, text="Run as commands", style="Accent.TButton",
                   command=self.run_editor_commands).pack(side="left", padx=8)
        ttk.Button(tools, text="Apply as YAML",
                   command=self.apply_editor_yaml).pack(side="left")
        ttk.Label(tools, text="  (one kubectl command per line, or a full manifest)",
                  style="Muted.TLabel").pack(side="left")
        self.editor = tk.Text(editor, bg="#08101d", fg=TEXT, relief="flat",
                              insertbackground=TEXT, font=(mono, 10), padx=10, pady=8,
                              wrap="none", undo=True)
        self.editor.pack(fill="both", expand=True)

        # --- handbook page ---------------------------------------------
        page = ttk.Frame(book)
        book.add(page, text="Handbook page")
        page_bar = ttk.Frame(page)
        page_bar.pack(fill="x", pady=4)
        self.page_label = ttk.Label(page_bar, text="", style="Muted.TLabel")
        self.page_label.pack(side="left", padx=8)
        ttk.Button(page_bar, text="<", width=3,
                   command=lambda: self.step_page(-1)).pack(side="right", padx=2)
        ttk.Button(page_bar, text=">", width=3,
                   command=lambda: self.step_page(1)).pack(side="right", padx=2)
        ttk.Button(page_bar, text="open full size",
                   command=self.open_page_external).pack(side="right", padx=6)
        self.page_canvas = tk.Canvas(page, bg=BG, highlightthickness=0)
        self.page_canvas.pack(fill="both", expand=True)
        self.page_canvas.bind("<Configure>", lambda e: self.render_page())

        # --- notes ------------------------------------------------------
        notes = ttk.Frame(book)
        book.add(notes, text="Notes")
        self.notes = tk.Text(notes, bg=PANEL, fg=TEXT, relief="flat", wrap="word",
                             padx=14, pady=10, font=("Segoe UI", 10))
        self.notes.pack(fill="both", expand=True)
        self.notes.tag_configure("h1", font=("Segoe UI", 13, "bold"),
                                 foreground="#7dd3fc", spacing3=6)
        self.notes.tag_configure("h2", font=("Segoe UI", 10, "bold"),
                                 foreground=ACCENT, spacing1=8, spacing3=4)
        self.notes.tag_configure("body", foreground=TEXT, spacing3=3)
        self.notes.tag_configure("bullet", foreground="#cbd5e1", lmargin1=12,
                                 lmargin2=24)
        self.notes.tag_configure("cmd", foreground="#86efac",
                                 font=(mono, 9), lmargin1=12)
        self.notes.tag_configure("warn", foreground=WARN, lmargin1=12, lmargin2=24)
        self.notes.tag_configure("q", foreground="#e9d5ff", lmargin1=12, lmargin2=24)
        self.notes.tag_configure("muted", foreground=MUTED)
        self.notes.tag_configure("why", foreground="#94a3b8", lmargin1=26, lmargin2=26,
                                 spacing3=6, font=("Segoe UI", 9))
        self.notes.configure(state="disabled")

        # --- object inspector ------------------------------------------
        details = ttk.Frame(book)
        book.add(details, text="Selected object")
        self.details = tk.Text(details, bg="#08101d", fg=TEXT, relief="flat",
                               wrap="none", font=(mono, 9), padx=10, pady=8)
        self.details.pack(fill="both", expand=True)
        self.details.configure(state="disabled")

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=PANEL)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="ready", bg=PANEL, fg=MUTED, anchor="w",
                               padx=10, pady=3)
        self.status.pack(side="left")
        self.mode_label = tk.Label(
            bar, text="simulator", bg=PANEL,
            fg=OK if not self.shell.live else WARN, padx=10)
        self.mode_label.pack(side="right")

    def _bind_keys(self) -> None:
        self.root.bind("<Control-l>", lambda e: self.clear_output())
        self.root.bind("<Control-r>", lambda e: self.refresh())
        self.root.bind("<F5>", lambda e: self.tick(5))
        self.root.bind("<Control-q>", lambda e: self.root.destroy())

    # ------------------------------------------------------------------
    # progress (7)
    # ------------------------------------------------------------------
    def _load_progress(self) -> set:
        try:
            import json
            with open(self.progress_path, encoding="utf-8") as handle:
                return set(json.load(handle).get("done", []))
        except (OSError, ValueError):
            return set()

    def _save_progress(self) -> None:
        try:
            import json
            with open(self.progress_path, "w", encoding="utf-8") as handle:
                json.dump({"done": sorted(self.progress)}, handle, indent=2)
        except OSError:
            pass

    def toggle_progress(self) -> None:
        if self.current_topic is None:
            return
        key = self.current_topic.key
        if self.progress_var.get():
            self.progress.add(key)
        else:
            self.progress.discard(key)
        self._save_progress()
        self.fill_topics()
        self.update_progress_bar()

    def update_progress_bar(self) -> None:
        total = len(handbook.ORDER)
        done = len(self.progress)
        self.progress_label.configure(text=f"progress  {done} / {total}")
        self.progress_bar["maximum"] = total
        self.progress_bar["value"] = done

    def reset_lab(self) -> None:
        choice = messagebox.askyesnocancel(
            "Reset",
            "Yes  -- reset the cluster AND your topic progress\n"
            "No   -- reset the cluster only\n"
            "Cancel -- do nothing")
        if choice is None:
            return
        self.shell.run("sim reset")
        self.clear_output()
        if choice:
            self.progress.clear()
            self._save_progress()
            self.fill_topics()
        self.update_progress_bar()
        self.log("lab reset", "muted")
        self.refresh()

    # ------------------------------------------------------------------
    # handbook sidebar
    # ------------------------------------------------------------------
    def fill_topics(self) -> None:
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        matches = {t.key for t in handbook.search(query)} if query else None
        for section, keys in handbook.SECTIONS:
            visible = [k for k in keys if matches is None or k in matches]
            if not visible:
                continue
            parent = self.tree.insert("", "end", text=f"  {section}", open=True,
                                      tags=("section",))
            for key in visible:
                topic = handbook.TOPICS[key]
                mark = "[x]" if key in self.progress else "[  ]"
                self.tree.insert(parent, "end", iid=key,
                                 text=f" {mark} {topic.title}  ·  p{topic.pages[0]}",
                                 tags=("done",) if key in self.progress else ())
        self.tree.tag_configure("section", foreground=ACCENT)
        self.tree.tag_configure("done", foreground="#6ee7a8")

    def on_topic_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        key = selection[0]
        if key in handbook.TOPICS:
            self.show_topic(key)

    def show_topic(self, key: str) -> None:
        topic = handbook.TOPICS.get(key)
        if topic is None:
            return
        if self.current_topic is not None:
            self.editor_memory[self.current_topic.key] = self.editor.get("1.0", "end-1c")
        self.current_topic = topic
        self.page_index = 0
        self.render_notes(topic)
        self.render_page()
        # the script/YAML tab always follows the selection, whichever tab is on top
        related = lab_index.by_topic(topic.key)
        if topic.lab:
            direct = lab_index.by_file(topic.lab)
            if direct is not None and direct not in related:
                related = [direct] + related
        body = ""
        if related and related[0].exists():
            body = related[0].read()
        elif topic.yaml:
            body = topic.yaml
        else:
            body = f"# {topic.title}\n# no starter script for this topic yet\n"
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.editor_memory.get(topic.key, body))
        self.editor_pristine = body
        self.progress_var.set(topic.key in self.progress)
        self.progress_check.configure(text=f"I have covered: {topic.title[:28]}")

    def render_notes(self, topic: handbook.Topic) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("end", f"{topic.title}\n", "h1")
        self.notes.insert("end", f"Handbook page(s): "
                                 f"{', '.join(str(p) for p in topic.pages)}\n\n",
                          "muted")
        self.notes.insert("end", topic.summary + "\n", "body")
        if topic.bullets:
            self.notes.insert("end", "\nKey points\n", "h2")
            for point in topic.bullets:
                self.notes.insert("end", f"  •  {point}\n", "bullet")
        if topic.commands:
            self.notes.insert("end", "\nCommands (double-click a line to run it)\n",
                              "h2")
            for command in topic.commands:
                self.notes.insert("end", f"  $ {command}\n", "cmd")
                why = handbook.describe_command(command)
                if why:
                    self.notes.insert("end", f"      {why}\n", "why")
        if topic.gotchas:
            self.notes.insert("end", "\nCommon mistakes\n", "h2")
            for gotcha in topic.gotchas:
                self.notes.insert("end", f"  !  {gotcha}\n", "warn")
        if topic.interview:
            self.notes.insert("end", "\nInterview questions\n", "h2")
            for question in topic.interview:
                self.notes.insert("end", f"  ?  {question}\n", "q")
        matching = lab_index.by_topic(topic.key)
        if matching:
            self.notes.insert("end", "\nLab\n", "h2")
            for lab in matching:
                self.notes.insert("end", f"  →  {lab.title}  ({lab.file})\n", "bullet")
        self.notes.configure(state="disabled")
        self.notes.bind("<Double-Button-1>", self._run_clicked_command)

    def _run_clicked_command(self, event) -> None:
        index = self.notes.index(f"@{event.x},{event.y} linestart")
        line = self.notes.get(index, f"{index} lineend").strip()
        if line.startswith("$ "):
            self.execute(line[2:].strip())
            self.bottom_book.select(0)

    # ------------------------------------------------------------------
    # handbook page images
    # ------------------------------------------------------------------
    def render_page(self) -> None:
        canvas = self.page_canvas
        canvas.delete("all")
        pages = self.current_topic.page_files()
        if not pages:
            canvas.create_text(20, 20, anchor="nw", fill=MUTED,
                               text="Handbook page images are not bundled in this copy.\n"
                                    "Put the extracted PNGs in handbook/pages/ to see "
                                    "the original diagrams here.")
            self.page_label.configure(text="")
            return
        self.page_index = max(0, min(self.page_index, len(pages) - 1))
        path = pages[self.page_index]
        self.page_label.configure(
            text=f"{self.current_topic.title}  -  page "
                 f"{self.current_topic.pages[self.page_index]}  "
                 f"({self.page_index + 1}/{len(pages)})")
        try:
            image = self._load_image(path, canvas.winfo_width() or 900,
                                     canvas.winfo_height() or 500)
        except Exception as exc:
            canvas.create_text(20, 20, anchor="nw", fill=ERR,
                               text=f"could not load {os.path.basename(path)}: {exc}")
            return
        cx = max((canvas.winfo_width() - image.width()) // 2, 0)
        canvas.create_image(cx, 6, anchor="nw", image=image)
        canvas.image = image                       # keep a reference
        canvas.configure(scrollregion=(0, 0, image.width(), image.height() + 12))
        canvas.create_text(10, 8, anchor="nw", fill=MUTED,
                           text="click to enlarge", font=("Segoe UI", 8))
        canvas.bind("<Button-1>", lambda e: PageViewer(self.root, path,
                                                       self.current_topic.title))

    def _load_image(self, path: str, max_w: int, max_h: int) -> "tk.PhotoImage":
        key = f"{path}:{max_w}x{max_h}"
        if key in self.images:
            return self.images[key]
        try:                                       # smooth scaling when Pillow is here
            from PIL import Image, ImageTk         # type: ignore
            image = Image.open(path)
            ratio = min(max_w / image.width, max_h / image.height, 1.0)
            if ratio < 1.0:
                image = image.resize((int(image.width * ratio),
                                      int(image.height * ratio)), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:                          # stdlib fallback: integer subsample
            photo = tk.PhotoImage(file=path)
            factor = 1
            while (photo.width() // factor > max_w or
                   photo.height() // factor > max_h) and factor < 8:
                factor += 1
            if factor > 1:
                photo = photo.subsample(factor, factor)
        if len(self.images) > 12:
            self.images.clear()
        self.images[key] = photo
        return photo

    def step_page(self, delta: int) -> None:
        self.page_index += delta
        self.render_page()

    def open_page_external(self) -> None:
        pages = self.current_topic.page_files()
        if pages:
            webbrowser.open("file://" + os.path.abspath(
                pages[min(self.page_index, len(pages) - 1)]))

    # ------------------------------------------------------------------
    # terminal
    # ------------------------------------------------------------------
    def log(self, text: str, tag: str = "") -> None:
        if not text:
            return
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip("\n") + "\n", tag)
        self.output.see("end")
        self.output.configure(state="disabled")

    def clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def on_enter(self, _event=None) -> str:
        command = self.entry.get().strip()
        self.entry.delete(0, "end")
        if command:
            self.execute(command)
        return "break"

    def execute(self, command: str) -> None:
        self.history.append(command)
        self.history_pos = len(self.history)
        self.log(f"{self.shell.cluster.current_namespace} $ {command}", "cmd")
        result = self.shell.run(command)
        if result.out == "\x1bCLEAR":
            self.clear_output()
        elif result.ok:
            self.log(result.out, "ok" if len(result.out) < 60 else "")
        else:
            self.log(result.err, "err")
        if result.topic and result.topic in handbook.TOPICS:
            self.show_topic(result.topic)
            try:
                self.tree.selection_set(result.topic)
                self.tree.see(result.topic)
            except tk.TclError:
                pass
        self.refresh()
        self.status.configure(text=command[:120])

    def on_history_up(self, _event=None) -> str:
        if self.history:
            self.history_pos = max(0, self.history_pos - 1)
            self.entry.delete(0, "end")
            self.entry.insert(0, self.history[self.history_pos])
        return "break"

    def on_history_down(self, _event=None) -> str:
        if self.history:
            self.history_pos = min(len(self.history), self.history_pos + 1)
            self.entry.delete(0, "end")
            if self.history_pos < len(self.history):
                self.entry.insert(0, self.history[self.history_pos])
        return "break"

    def on_complete(self, _event=None) -> str:
        text = self.entry.get()
        words = text.split()
        options = ["kubectl get ", "kubectl describe ", "kubectl apply -f ",
                   "kubectl delete ", "kubectl scale ", "kubectl logs ",
                   "kubectl rollout status ", "kubectl top ", "helm install ",
                   "kustomize build ", "sim tick ", "sim load ", "sim chaos ",
                   "sim node ", "help"]
        if len(words) <= 2:
            hits = [o for o in options if o.startswith(text)]
            if len(hits) == 1:
                self.entry.delete(0, "end")
                self.entry.insert(0, hits[0])
            elif hits:
                self.log("  ".join(h.strip() for h in hits), "muted")
        return "break"

    # ------------------------------------------------------------------
    # labs
    # ------------------------------------------------------------------
    def _selected_lab(self):
        selection = self.lab_list.curselection()
        if not selection:
            return None
        return lab_index.LABS[selection[0]]

    def on_lab_select(self, _event=None) -> None:
        lab = self._selected_lab()
        if lab is None:
            return
        self.lab_note.configure(state="normal")
        self.lab_note.delete("1.0", "end")
        self.lab_note.insert("end", f"{lab.title}\n\n{lab.goal}\n\nYou should see:\n")
        for check in lab.checks:
            self.lab_note.insert("end", f"  • {check}\n")
        if not lab.exists():
            self.lab_note.insert("end", f"\n(missing file: labs/{lab.file})")
        self.lab_note.configure(state="disabled")
        if lab.topic in handbook.TOPICS:
            self.show_topic(lab.topic)

    def load_lab(self) -> None:
        lab = self._selected_lab()
        if lab is None or not lab.exists():
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", lab.read())
        self.bottom_book.select(1)
        self.status.configure(text=f"loaded labs/{lab.file} into the editor")

    def run_lab(self) -> None:
        lab = self._selected_lab()
        if lab is None or not lab.exists():
            return
        self.lab_queue = lab.commands()
        self.bottom_book.select(0)
        self.log(f"\n─── running {lab.title} "
                 f"({len(self.lab_queue)} commands) ───", "muted")
        self.lab_running = True
        self._drain_lab()

    def step_lab(self) -> None:
        if not self.lab_queue:
            lab = self._selected_lab()
            if lab is None or not lab.exists():
                return
            self.lab_queue = lab.commands()
            self.bottom_book.select(0)
        self.lab_running = False
        command = self.lab_queue.pop(0)
        self.execute(command)

    def _drain_lab(self) -> None:
        if not self.lab_running or not self.lab_queue:
            self.lab_running = False
            return
        self.execute(self.lab_queue.pop(0))
        self.root.after(420, self._drain_lab)

    # ------------------------------------------------------------------
    # editor actions
    # ------------------------------------------------------------------
    def run_editor_commands(self) -> None:
        text = self.editor.get("1.0", "end")
        self.lab_queue = [ln.strip() for ln in text.splitlines()
                          if ln.strip() and not ln.strip().startswith("#")]
        self.bottom_book.select(0)
        self.lab_running = True
        self._drain_lab()

    def apply_editor_yaml(self) -> None:
        text = self.editor.get("1.0", "end")
        path = os.path.join(self.base_dir, ".editor-buffer.yaml")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.execute("kubectl apply -f .editor-buffer.yaml")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=self.base_dir,
            filetypes=[("YAML / scripts", "*.yaml *.yml *.kubectl *.txt"),
                       ("All files", "*.*")])
        if path:
            with open(path, encoding="utf-8") as handle:
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", handle.read())
            self.status.configure(text=f"opened {path}")

    def save_file(self) -> None:
        path = filedialog.asksaveasfilename(initialdir=self.base_dir,
                                            defaultextension=".yaml")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.editor.get("1.0", "end"))
            self.status.configure(text=f"saved {path}")

    # ------------------------------------------------------------------
    # cluster actions
    # ------------------------------------------------------------------
    def tick(self, count: int = 1) -> None:
        reconcile(self.shell.cluster, count)
        self.refresh()

    def _tick_loop(self) -> None:
        if self.autotick.get():
            reconcile(self.shell.cluster, 1)
            self.refresh()
        self.root.after(1000, self._tick_loop)

    def reset_cluster(self) -> None:
        if messagebox.askyesno("Reset cluster",
                               "Throw away every object and start from a fresh "
                               "4-node cluster?"):
            self.shell.run("sim reset")
            self.clear_output()
            self.log("cluster reset", "muted")
            self.refresh()

    def on_namespace_change(self) -> None:
        namespace = self.namespace.get()
        if namespace not in ("<all>",):
            self.shell.cluster.current_namespace = namespace
        self.refresh()

    def export_svg(self) -> None:
        if self.graph is None:
            return
        path = filedialog.asksaveasfilename(initialdir=self.base_dir,
                                            defaultextension=".svg",
                                            initialfile="topology.svg")
        if path:
            export.write_svg(self.graph, path,
                             f"{self.shell.cluster.name} - {self.view.get()} view")
            self.status.configure(text=f"topology exported to {path}")

    # ------------------------------------------------------------------
    # docker
    # ------------------------------------------------------------------
    def open_web_ui(self) -> None:
        from .webui import ensure_server
        url = ensure_server(self.shell, self.web_port)
        webbrowser.open(url)
        self.status.configure(text=f"web UI at {url}")

    def open_docker_panel(self) -> None:
        DockerPanel(self.root, self.base_dir, self.web_port)

    # ------------------------------------------------------------------
    # topology rendering
    # ------------------------------------------------------------------
    def refresh(self, initial: bool = False) -> None:
        cluster = self.shell.cluster
        namespaces = ["<all>"] + cluster.namespaces()
        if list(self.ns_box["values"]) != namespaces:
            self.ns_box["values"] = namespaces
        if self.namespace.get() not in namespaces:
            self.namespace.set(cluster.current_namespace)
        selected_ns = None if self.namespace.get() == "<all>" else self.namespace.get()

        self.graph = topology.build(cluster, selected_ns, self.view.get(),
                                    include_system=self.show_system.get())
        self.draw_graph()

        stats = self.graph.stats
        self.chips["nodes"].configure(text=f"nodes {stats['nodes']}")
        self.chips["pods"].configure(text=f"pods {stats['pods']}")
        self.chips["running"].configure(text=f"running {stats['running']}")
        self.chips["problems"].configure(text=f"problems {stats['problems']}")
        self.chips["tick"].configure(text=f"tick {stats['tick']}")
        self.prompt_label.configure(text=f"{cluster.current_namespace} $")
        self.topology_hint.configure(
            text=f"{len(self.graph.nodes)} objects · click a card to inspect it"
                 + ("  ·  RBAC on" if cluster.rbac_enforced else "")
                 + (f"  ·  acting as {cluster.current_user}"
                    if cluster.current_user != "admin" else ""))
        if self.selected:
            self.show_details(self.selected)

    def set_zoom(self, value: float) -> None:
        self.zoom = max(0.35, min(2.2, value))
        self.draw_graph()

    def fit_zoom(self) -> None:
        if not self.graph:
            return
        width = self.canvas.winfo_width() or 900
        height = self.canvas.winfo_height() or 500
        self.zoom = max(0.35, min(1.6, min(width / max(self.graph.width, 1),
                                           height / max(self.graph.height, 1))))
        self.draw_graph()

    def _on_wheel(self, event) -> None:
        if event.state & 0x0004:                     # ctrl held -> zoom
            self.set_zoom(self.zoom * (1.1 if event.delta > 0 else 0.9))
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def draw_graph(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self.canvas_items.clear()
        graph = self.graph
        if graph is None:
            return
        z = self.zoom
        offset = 34 if graph.groups else 6

        def sx(value):
            return value * z

        # node group boxes (physical view)
        for group in graph.groups:
            colour = STATUS_COLORS.get(group.get("status", "ok"), MUTED)
            x, y = sx(group["x"]), sx(group["y"] + offset)
            w, h = sx(group["w"]), sx(group["h"])
            self._round_rect(x, y, x + w, y + h, 14 * z, fill=PANEL, outline=colour,
                             width=1.4)
            canvas.create_text(x + 16 * z, y + 20 * z, anchor="nw", fill=TEXT,
                               text=group["title"],
                               font=("Segoe UI", max(8, int(11 * z)), "bold"))
            canvas.create_text(x + 16 * z, y + 40 * z, anchor="nw", fill=MUTED,
                               text=group["subtitle"],
                               font=("Segoe UI", max(7, int(8 * z))))
            if group.get("kind") == "Node":
                for index, key in enumerate(("cpu_pct", "mem_pct")):
                    pct = max(0, min(100, int(group.get(key, 0))))
                    bx = x + w - 96 * z
                    by = y + (18 + index * 14) * z
                    canvas.create_rectangle(bx, by, bx + 80 * z, by + 7 * z,
                                            fill="#1e2b45", outline="")
                    fill = OK if pct < 70 else (WARN if pct < 90 else ERR)
                    canvas.create_rectangle(bx, by, bx + 80 * z * pct / 100,
                                            by + 7 * z, fill=fill, outline="")
                    canvas.create_text(bx - 6 * z, by + 3 * z, anchor="e", fill=MUTED,
                                       text=key[:3].upper(),
                                       font=("Segoe UI", max(6, int(7 * z))))

        by_id = {n.id: n for n in graph.nodes}
        for edge in graph.edges:
            src, dst = by_id.get(edge.src), by_id.get(edge.dst)
            if src is None or dst is None:
                continue
            style = export.EDGE_STYLE.get(edge.kind, export.EDGE_STYLE["owns"])
            x1, y1 = sx(src.x + src.w / 2), sx(src.y + src.h + offset)
            x2, y2 = sx(dst.x + dst.w / 2), sx(dst.y + offset)
            if abs(src.y - dst.y) < 10:
                x1, y1 = sx(src.x + src.w), sx(src.y + src.h / 2 + offset)
                x2, y2 = sx(dst.x), sx(dst.y + dst.h / 2 + offset)
            elif y2 < y1:
                x1, y1 = sx(src.x + src.w / 2), sx(src.y + offset)
                x2, y2 = sx(dst.x + dst.w / 2), sx(dst.y + dst.h + offset)
            mid = (y1 + y2) / 2
            dash = tuple(int(v) for v in style["dash"].split()) if style["dash"] else ()
            canvas.create_line(x1, y1, x1, mid, x2, mid, x2, y2, smooth=True,
                               fill=style["color"], width=float(style["width"]) * z,
                               dash=dash or None, splinesteps=24)

        for node in graph.nodes:
            x, y = sx(node.x), sx(node.y + offset)
            w, h = sx(node.w), sx(node.h)
            status_colour = STATUS_COLORS.get(node.status, MUTED)
            outline = ACCENT if node.id == self.selected else node.color
            width = 2.6 if node.id == self.selected else 1.5
            if node.fresh:
                self._round_rect(x - 3, y - 3, x + w + 3, y + h + 3, 12 * z,
                                 fill="", outline=node.color, width=1)
            shape = self._round_rect(x, y, x + w, y + h, 10 * z, fill=PANEL2,
                                     outline=outline, width=width)
            self.canvas_items[shape] = node.id
            bar = canvas.create_rectangle(x, y + 3, x + 4 * z, y + h - 3,
                                          fill=node.color, outline="")
            self.canvas_items[bar] = node.id
            dot = canvas.create_oval(x + w - 18 * z, y + 8 * z, x + w - 8 * z,
                                     y + 18 * z, fill=status_colour, outline="")
            self.canvas_items[dot] = node.id
            title = canvas.create_text(
                x + 12 * z, y + 8 * z, anchor="nw", fill=TEXT, text=node.label,
                font=("Segoe UI", max(7, int(9 * z)), "bold"))
            self.canvas_items[title] = node.id
            kind = canvas.create_text(
                x + 12 * z, y + 24 * z, anchor="nw", fill=MUTED, text=node.kind,
                font=("Segoe UI", max(6, int(7 * z))))
            self.canvas_items[kind] = node.id
            if node.sublabel:
                sub = canvas.create_text(
                    x + 12 * z, y + 36 * z, anchor="nw", fill=status_colour,
                    text=node.sublabel[:26],
                    font=("Segoe UI", max(6, int(7 * z))))
                self.canvas_items[sub] = node.id
            if node.badge and z > 0.6:
                badge = canvas.create_text(
                    x + w - 10 * z, y + h - 8 * z, anchor="se", fill=MUTED,
                    text=node.badge[:14],
                    font=("Segoe UI", max(6, int(7 * z))))
                self.canvas_items[badge] = node.id

        if not graph.nodes and not graph.groups:
            canvas.create_text(40, 40, anchor="nw", fill=MUTED,
                               font=("Segoe UI", 11),
                               text="Nothing here yet.\n\nRun a command in the terminal "
                                    "below, or pick a lab on the left and press "
                                    "'Run all'.\n\n"
                                    "  kubectl create deployment web --image=nginx:1.25 "
                                    "--replicas=3\n"
                                    "  kubectl expose deploy/web --port=80")
        canvas.configure(scrollregion=(0, 0, sx(graph.width) + 20,
                                       sx(graph.height + offset) + 20))
        self._draw_legend()

    def _draw_legend(self) -> None:
        canvas = self.canvas
        x = 12
        y = self.canvas.winfo_height() - 22 if self.canvas.winfo_height() > 60 else 10
        for kind, colour in topology.legend():
            item = canvas.create_rectangle(x, y, x + 9, y + 9, fill=colour, outline="")
            canvas.create_text(x + 13, y + 4, anchor="w", fill=MUTED, text=kind,
                               font=("Segoe UI", 7))
            x += 20 + 6 * len(kind)

    def _round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        radius = max(2, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    # ------------------------------------------------------------------
    def on_canvas_click(self, event) -> None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x - 2, y - 2, x + 2, y + 2)
        for item in reversed(items):
            node_id = self.canvas_items.get(item)
            if node_id:
                self.selected = node_id
                self.draw_graph()
                self.show_details(node_id)
                self.bottom_book.select(4)
                node = self.graph.by_id(node_id) if self.graph else None
                if node and node.topic in handbook.TOPICS:
                    self.show_topic(node.topic)
                return
        self.selected = None
        self.draw_graph()

    def show_details(self, node_id: str) -> None:
        parts = node_id.split("/")
        if len(parts) != 3:
            return
        kind, namespace, name = parts
        namespace = "" if namespace == "-" else namespace
        obj = self.shell.cluster.get(kind, namespace, name)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if obj is None:
            self.details.insert("end", f"{node_id} no longer exists.")
        else:
            from . import printers
            self.details.insert("end", printers.describe(self.shell.cluster, obj))
            self.details.insert("end", "\n\n" + "-" * 60 + "\nYAML\n" + "-" * 60 + "\n")
            self.details.insert("end", printers.to_output(obj, "yaml"))
        self.details.configure(state="disabled")


class PageViewer(tk.Toplevel):
    """Full-size handbook page with zoom (2)."""

    def __init__(self, master, path: str, title: str):
        super().__init__(master)
        self.title(f"{title}  -  {os.path.basename(path)}")
        self.configure(bg=BG)
        self.geometry("1150x900")
        self.path = path
        self.scale = 1.0
        self._photo = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text=title, style="Title.TLabel").pack(side="left")
        ttk.Button(bar, text="close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="fit", command=lambda: self.zoom(0)).pack(side="right",
                                                                      padx=3)
        ttk.Button(bar, text="+", width=3,
                   command=lambda: self.zoom(1.25)).pack(side="right")
        ttk.Button(bar, text="-", width=3,
                   command=lambda: self.zoom(1 / 1.25)).pack(side="right")

        holder = tk.Frame(self, bg=BG)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        vbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(holder, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", lambda e: self.zoom(1.25))
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(60, lambda: self.zoom(0))

    def zoom(self, factor: float) -> None:
        if factor == 0:
            self.scale = 1.0
        else:
            self.scale = max(0.4, min(4.0, self.scale * factor))
        width = int((self.canvas.winfo_width() or 1000) * self.scale)
        height = int((self.canvas.winfo_height() or 800) * self.scale)
        try:
            from PIL import Image, ImageTk           # type: ignore
            image = Image.open(self.path)
            ratio = min(width / image.width, 4.0) if self.scale != 1.0 else \
                min(width / image.width, height / image.height)
            size = (max(200, int(image.width * ratio)), max(200, int(image.height * ratio)))
            self._photo = ImageTk.PhotoImage(image.resize(size, Image.LANCZOS))
        except Exception:
            photo = tk.PhotoImage(file=self.path)
            step = 1
            while photo.width() // step > width and step < 8:
                step += 1
            self._photo = photo.subsample(step, step) if step > 1 else photo
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, self._photo.width(),
                                            self._photo.height()))


# --------------------------------------------------------------------------
class DockerPanel(tk.Toplevel):
    """One-click container build/run, with the commands shown for transparency."""

    def __init__(self, master, base_dir: str, port: int):
        super().__init__(master)
        self.title("Deploy k8s-practice-lab to Docker")
        self.configure(bg=BG)
        self.geometry("760x520")
        self.base_dir = base_dir
        self.port = port
        self.queue: "queue.Queue[str]" = queue.Queue()

        tk.Label(self, text="Run the lab inside a container",
                 bg=BG, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(anchor="w",
                                                                     padx=16, pady=(14, 2))
        tk.Label(self, text="The container serves the browser UI on "
                            f"http://localhost:{port} - the desktop window keeps "
                            "running locally.",
                 bg=BG, fg=MUTED, wraplength=700, justify="left").pack(anchor="w",
                                                                      padx=16)

        commands = ttk.Frame(self)
        commands.pack(fill="x", padx=16, pady=12)
        ttk.Button(commands, text="Deploy", style="Docker.TButton",
                   command=self.deploy).pack(side="left")
        ttk.Button(commands, text="Status", command=self.status).pack(side="left",
                                                                     padx=6)
        ttk.Button(commands, text="Stop + remove", command=self.stop).pack(side="left")
        ttk.Button(commands, text="Open in browser",
                   command=lambda: webbrowser.open(
                       f"http://localhost:{self.port}")).pack(side="right")

        mono = ("Consolas" if sys.platform.startswith("win") else "DejaVu Sans Mono")
        self.log_view = tk.Text(self, bg="#08101d", fg=TEXT, relief="flat",
                                font=(mono, 9), padx=10, pady=8, wrap="word")
        self.log_view.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.write("One button does all of it:\n\n"
                   "  docker build -t k8s-practice-lab .\n"
                   "  docker rm -f k8s-lab\n"
                   f"  docker run -d --name k8s-lab -p {port}:8899 k8s-practice-lab\n\n"
                   "Press Deploy to run them for you, or copy them into your own "
                   "terminal.\n"
                   "docker compose up --build   also works (see docker-compose.yml).\n\n"
                   "If Docker Desktop is installed but not started you will get "
                   "'failed to connect to the docker API' -- start Docker Desktop "
                   "first.\n")
        self.after(250, self._drain)

    def write(self, text: str) -> None:
        self.log_view.insert("end", text)
        self.log_view.see("end")

    def _drain(self) -> None:
        try:
            while True:
                self.write(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.after(250, self._drain)

    def _run(self, argv: List[str]) -> None:
        def worker():
            self.queue.put(f"\n$ {' '.join(argv)}\n")
            try:
                proc = subprocess.Popen(argv, cwd=self.base_dir, text=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
            except FileNotFoundError:
                self.queue.put("docker was not found on your PATH. Install Docker "
                               "Desktop (or the docker CLI) and try again.\n")
                return
            for line in proc.stdout:                       # type: ignore[union-attr]
                self.queue.put(line)
            proc.wait()
            self.queue.put(f"[exit {proc.returncode}]\n")
            if proc.returncode == 0 and argv[:2] == ["docker", "run"]:
                self.queue.put(f"\nReady: http://localhost:{self.port}\n")
                webbrowser.open(f"http://localhost:{self.port}")
        threading.Thread(target=worker, daemon=True).start()

    def deploy(self) -> None:
        """One button: check the daemon, build, replace, run."""
        def chain():
            from .webui import _docker_ready
            try:
                problem = _docker_ready()
            except Exception as exc:
                problem = f"Could not talk to Docker: {exc}"
            if problem:
                self.queue.put("\n" + problem + "\n")
                return
            for argv in (["docker", "build", "-t", "k8s-practice-lab", "."],
                         ["docker", "rm", "-f", "k8s-lab"],
                         ["docker", "run", "-d", "--name", "k8s-lab", "-p",
                          f"{self.port}:8899", "k8s-practice-lab"]):
                self._run_sync(argv)
        threading.Thread(target=chain, daemon=True).start()

    def status(self) -> None:
        self._run(["docker", "ps", "-a", "--filter", "name=k8s-lab"])

    def _run_sync(self, argv: List[str]) -> None:
        self.queue.put(f"\n$ {' '.join(argv)}\n")
        try:
            proc = subprocess.run(argv, cwd=self.base_dir, text=True,
                                  capture_output=True)
        except FileNotFoundError:
            self.queue.put("docker was not found on your PATH.\n")
            return
        self.queue.put((proc.stdout or "") + (proc.stderr or ""))
        if proc.returncode == 0 and argv[1] == "run":
            self.queue.put(f"\nReady: http://localhost:{self.port}\n")
            webbrowser.open(f"http://localhost:{self.port}")

    def stop(self) -> None:
        self._run(["docker", "rm", "-f", "k8s-lab"])


# --------------------------------------------------------------------------
def launch(base_dir: str = ".", live: bool = False, web_port: int = 8899,
           script: Optional[str] = None) -> None:
    root = tk.Tk()
    app = LabApp(root, base_dir=base_dir, live=live, web_port=web_port)
    if script and os.path.isfile(script):
        with open(script, encoding="utf-8") as handle:
            app.lab_queue = [ln.strip() for ln in handle.read().splitlines()
                             if ln.strip() and not ln.strip().startswith("#")]
        app.lab_running = True
        root.after(600, app._drain_lab)
    root.mainloop()
