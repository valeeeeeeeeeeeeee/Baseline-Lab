"""Desktop interface (Tkinter + Matplotlib).

Simple flow: Import .txt -> the baseline is calculated automatically -> Export all (CSV).
Columns, method and parameters live under "Advanced options" (hidden by default).
All texts come from i18n.tr(); switching the language rebuilds the interface keeping the state.
"""
from __future__ import annotations

import pickle
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import baselines as bl
from . import i18n
from . import peaks as pk
from .derivative import dtg
from .i18n import tr
from .io_txt import DataFile, read_file

COMPARE_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b",
                  "#e377c2", "#7f7f7f", "#bcbd22"]
DEFAULT_METHOD = "Derivada 1ª + 2ª"
PEAK_PARAMS = [
    bl.Param("k", "Limiar k (× ruído σ)", 3.0, 1.0, 10.0,
             help="Múltiplo do ruído do ambiente (σ, estimado sozinho). A baseline dentro de "
                  "k·σ de 0 vira 0, e só é pico o que passa de k·σ. Maior = mais rigoroso "
                  "(menos picos pequenos); menor = aceita picos fracos, mas também ruído."),
    bl.Param("min_height_pct", "Altura mínima (% do maior pico)", 5.0, 0.5, 50.0, log=True,
             help="Descarta picos com altura menor que essa % da altura do maior pico. Ajuda a "
                  "ignorar ondulações que passam do limiar de ruído."),
    bl.Param("min_width", "Largura mínima (unidades de X)", 0.0, 0.0, 50.0,
             help="Trechos mais estreitos que isso, de pé a pé, são ruído e não picos (no modo "
                  "'somente picos' viram 0). Eles também deixam de ser o 'maior pico' da altura "
                  "mínima. Use quando uma espícula de ruído é mais alta que um pico real. "
                  "0 = desligado."),
    bl.Param("skip_edges", "Ignorar picos cortados nas bordas", "sim", choices=("sim", "não"),
             help="Sim: um pico que não volta ao nível do ruído antes do início ou do fim dos "
                  "dados é ignorado (em geral é artefato de borda). Não: ele é mantido, mesmo "
                  "incompleto."),
]


class Tooltip:
    """Help balloon that appears when the cursor rests on a widget."""

    def __init__(self, widget, text: str, delay_ms: int = 300):
        self.widget, self.text, self.delay = widget, text, delay_ms
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None):
        self.hide()
        self._job = self.widget.after(self.delay, self.show)

    def show(self):
        self._job = None
        if self.tip or not self.widget.winfo_ismapped():
            return
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self.text, justify="left", wraplength=300, background="#fffbe6",
                 foreground="#222", relief="solid", borderwidth=1, padx=8, pady=6,
                 font=("Segoe UI", 9)).pack()
        tw.update_idletasks()
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        y = self.widget.winfo_rooty() - 4
        if x + w > self.widget.winfo_screenwidth():  # does not fit on the right: open on the left
            x = self.widget.winfo_rootx() - w - 6
        y = max(0, min(y, self.widget.winfo_screenheight() - h - 40))
        tw.wm_geometry(f"+{x}+{y}")

    def hide(self, _e=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def info_icon(parent, text: str):
    """(i) icon with a help balloon."""
    lbl = ttk.Label(parent, text="ⓘ", foreground="#1a6fb5", cursor="question_arrow",
                    font=("Segoe UI Symbol", 12))
    Tooltip(lbl, text)
    return lbl


def with_info(widget, text: str, **pack):
    """Packs `widget` (child of a new Frame) with the (i) icon right next to it."""
    widget.pack(side="left")
    info_icon(widget.master, text).pack(side="left", padx=(4, 0))
    widget.master.pack(**pack)
    return widget


def guess_columns(columns: list[str]) -> tuple[int, int, bool]:
    """Guess of (X, Y, calculate DTG) from the column names.

    Thermal analysis: X = temperature; Y = derivative (DTG) if it exists. If there is only the
    mass, uses the mass and turns on the DTG calculation (a baseline on the TGA curve makes no sense).
    """
    low = [c.lower() for c in columns]
    xi = next((i for i, c in enumerate(low) if c.startswith("temp") and "differ" not in c), 0)
    yi = next((i for i, c in enumerate(low) if "deriv" in c), None)
    if yi is not None:
        return xi, yi, False
    yi = 1 if xi == 0 else 0
    yi = min(yi, len(columns) - 1)
    is_mass = bool(re.search(r"mass|weight|peso", low[yi]))
    return xi, yi, is_mass and low[xi].startswith("temp")


def wheel_zoom(canvas, factor: float = 1.25):
    """Mouse-wheel zoom, centered on the cursor (separate windows)."""
    def on_scroll(event):
        ax = event.inaxes
        if ax is None or event.xdata is None:
            return
        scale = 1 / factor if event.button == "up" else factor
        for get, set_, c in ((ax.get_xlim, ax.set_xlim, event.xdata),
                             (ax.get_ylim, ax.set_ylim, event.ydata)):
            lo, hi = get()
            set_(c - (c - lo) * scale, c + (hi - c) * scale)
        canvas.draw_idle()
    canvas.mpl_connect("scroll_event", on_scroll)


class ScrollFrame(ttk.Frame):
    """Panel with a scrollbar (many controls)."""

    def __init__(self, parent, width=300):
        super().__init__(parent)
        canvas = tk.Canvas(self, width=width, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas, padding=8)
        self.inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._canvas = canvas
        # Enter/Leave on the frame fails when moving over the child controls; decide by the widget under the mouse
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_wheel(self, ev):
        try:
            w = self.winfo_containing(ev.x_root, ev.y_root)
        except (KeyError, tk.TclError):  # open list of a Combobox (internal Tk window)
            return
        if w is None or "popdown" in str(w) or isinstance(w, (tk.Listbox, ttk.Treeview)):
            return  # these scroll their own content
        if str(w) == str(self) or str(w).startswith(str(self) + "."):
            self._canvas.yview_scroll(-1 if ev.delta > 0 else 1, "units")


class HomeToolbar(NavigationToolbar2Tk):
    """Plot toolbar with only "Home" (original view); zoom and pan are on the mouse."""
    toolitems = [t for t in NavigationToolbar2Tk.toolitems if t[0] == "Home"]

    def __init__(self, canvas, window, on_home):
        self._on_home = on_home
        super().__init__(canvas, window)

    def home(self, *_args):
        self._on_home()


class App(tk.Tk):
    def __init__(self, initial_files: list[str] | None = None, lang: str | None = None):
        super().__init__()
        i18n.set_lang(lang or i18n.load_lang())
        self.title("Baseline Lab")
        self.geometry("1280x780")
        self.minsize(900, 560)
        self.files: dict[str, DataFile] = {}
        # each file is independent: choices, adjustments and calculation log kept per file
        self._file_states: dict[str, dict] = {}
        self._cur_key: str | None = None    # file whose state is in the controls
        # anchors adjusted by hand on top of the current method: [x, y]; y None = on the smoothed signal
        self.edits: list[list] | None = None
        self._edit_sig = None               # file/method/parameters the adjustments apply to
        self.noise_marks: list[tuple[float, float]] = []  # peaks marked as noise (X)
        self._noise_sig = None              # file/columns the marks apply to
        self.peak_feet: list[float] | None = None  # dragged peak feet ('peaks only')
        self.auto_anchors = None            # anchors shown (from the method or adjusted)
        self.events: list[dict] = []
        self.param_vars: dict[str, tk.Variable] = {}
        self.peak_vars: dict[str, tk.Variable] = {}
        self.regions: list[tuple[int, int]] = []
        self.method_keys = list(bl.METHODS)  # internal names; the list shows the translated ones
        self._after_id = None
        self._drag = None                   # index of the anchor being dragged
        self._pan = None                    # plot drag in progress
        self._home_view = None              # limits of the full view ("Home" button)
        self._settle_after = None           # turns the automatic layout back on when the mouse stops
        self._view_snap = None              # stored image for pan/zoom without redrawing
        self._view_after = None             # real draw when the mouse pauses
        self._drag_after = None
        self._curve = None                  # (x, baseline) drawn, to find anchors
        self._corr = None                   # (x, corrected) drawn, to mark peaks
        self._hl: list = []                 # artists of the red marking of the selected peak
        # the mouse wheel does not change options: by default Tk switches the value of the
        # Combobox under the cursor when scrolling; without this binding the wheel only scrolls the panel
        for cls in ("TCombobox", "TSpinbox"):
            self.unbind_class(cls, "<MouseWheel>")
        self._build_ui()
        if initial_files:
            self.after(50, lambda: self.load_files(initial_files))

    # ------------------------------------------------------------------ UI
    def _build_ui(self, state: dict | None = None):
        style = ttk.Style(self)
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 6))
        style.configure("Title.TLabel", font=("Segoe UI", 10, "bold"))

        # top bar: the only everyday actions
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text=tr("import_btn"), style="Big.TButton",
                   command=self.open_files).pack(side="left")
        self.adv_btn = ttk.Button(bar, text=tr("adv_closed"), command=self.toggle_advanced)
        self.adv_btn.pack(side="right")
        codes = list(i18n.LANGS)
        self.lang_cb = ttk.Combobox(bar, state="readonly", width=15,
                                    values=[i18n.LANGS[c] for c in codes])
        self.lang_cb.current(codes.index(i18n.get_lang()))
        self.lang_cb.bind("<<ComboboxSelected>>",
                          lambda _e: self.set_language(codes[self.lang_cb.current()]))
        self.lang_cb.pack(side="right", padx=(0, 12))
        ttk.Label(bar, text=tr("language")).pack(side="right", padx=(0, 4))
        ttk.Separator(self).pack(side="top", fill="x")

        # left column: files and results
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text=tr("files"), style="Title.TLabel").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=8, width=36, exportselection=False,
                                  activestyle="none")
        self.listbox.pack(fill="x", pady=(2, 2))
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.on_file_change())
        self.listbox.bind("<Delete>", lambda _e: self.remove_file())
        ttk.Button(left, text=tr("remove"), command=self.remove_file).pack(anchor="e")

        ttk.Label(left, text=tr("result"), style="Title.TLabel").pack(anchor="w", pady=(12, 2))
        self.summary = ttk.Label(left, wraplength=280, justify="left")
        self.summary.pack(anchor="w")
        self.stats = ttk.Label(left, wraplength=280, justify="left", foreground="#555")
        self.stats.pack(anchor="w", pady=(4, 0))
        # space under the table: adjusted so it ends at the base of the corrected plot
        self._left = left
        self._tree_pad = ttk.Frame(left, height=0)
        self._tree_pad.pack(side="bottom", fill="x")
        self._plot_menu = tk.Menu(self, tearoff=0)
        self._plot_menu.add_command(label=tr("open_window"),
                                    command=lambda: self.open_in_window(self._menu_ax))
        self._plot_menu.add_command(label=tr("save_image"),
                                    command=lambda: self.save_image(self._menu_ax))
        self._menu_ax = None
        self._area_menu = tk.Menu(self, tearoff=0)
        self._area_menu.add_command(label=tr("calc_area"), command=self.calc_area)
        self._area_menu.add_command(label=tr("mark_noise"), command=self.mark_noise)
        self._area_row = None
        box = ttk.Frame(left)
        box.pack(fill="x", pady=(6, 0))
        self.tree = ttk.Treeview(box, columns=("ini", "pico", "fim", "area", "tot"),
                                 show="headings", height=6)
        for c, t in (("ini", "col_start"), ("pico", "col_peak"), ("fim", "col_end"),
                     ("area", "col_event"), ("tot", "col_total")):
            self.tree.heading(c, text=tr(t))
            self.tree.column(c, anchor="e")
        self._fit_columns()  # width from the content, redone on every result
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.highlight_selected())
        self.tree.bind("<Escape>", lambda _e: self.tree.selection_set(()))
        self.tree.bind("<ButtonPress-1>", self._toggle_row)
        self.tree.bind("<Button-3>", self._tree_menu)

        # log of the area calculations (right click on the table): takes the rest of the column
        calc_head = ttk.Frame(left)
        calc_head.pack(fill="x", pady=(12, 2))
        ttk.Label(calc_head, text=tr("calcs"), style="Title.TLabel").pack(side="left")
        ttk.Button(calc_head, text=tr("calcs_clear"),
                   command=self.clear_calc_log).pack(side="right")
        box = ttk.Frame(left)
        box.pack(fill="both", expand=True)
        self.calc_log = tk.Text(box, width=1, height=4, wrap="word", font=("Segoe UI", 9),
                                relief="solid", borderwidth=1, padx=0, pady=0, cursor="arrow")
        self.calc_log.tag_configure("head", font=("Segoe UI", 9, "bold"))
        self.calc_log.tag_configure("dim", foreground="#666")
        # each calculation is a block; alternating gray background to separate one from the next
        for tag, bg in (("even", "white"), ("odd", "#ececec")):
            self.calc_log.tag_configure(tag, background=bg, lmargin1=6, lmargin2=6, rmargin=6)
        self.calc_log.tag_configure("first", spacing1=4)  # spacing at the top of each block
        self.calc_log.tag_configure("last", spacing3=4)   # and at the bottom
        self.calc_log.tag_configure("sel_entry", background="#cfe2ff", lmargin1=6, lmargin2=6,
                                    rmargin=6)  # marked calculation: its area in red
        self.calc_log.tag_lower("even")
        self.calc_log.tag_lower("odd")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.calc_log.yview)
        self.calc_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.calc_log.pack(side="left", fill="both", expand=True)
        self.calc_log.bind("<Button-1>", self._log_select)
        self.calc_log.bind("<Button-3>", self._log_menu)
        self._log_menu_pop = tk.Menu(self, tearoff=0)
        self._log_menu_pop.add_command(label=tr("calcs_remove"), command=self.remove_log_entry)
        self._log_clicked = None  # calculation under the right click
        # calculations done: {"lines": [(text, tags)], "lo", "hi", "pico"}; switching the language
        # keeps them, and also which one is marked
        self._log_entries = [dict(e) for e in (state or {}).get("calc_log", [])]
        self._log_sel = (state or {}).get("calc_sel")
        self._log_render()

        # advanced panel (hidden)
        self.adv = ScrollFrame(self)
        self._build_advanced(self.adv.inner, (state or {}).get("peak_vals"))

        # plot
        self.plot_frame = ttk.Frame(self)
        self.plot_frame.pack(side="left", fill="both", expand=True)
        self.fig = Figure(figsize=(8, 6), constrained_layout=True)
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212, sharex=self.ax1)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        HomeToolbar(self.canvas, self.plot_frame, self.reset_view).update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self.on_click)
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.canvas.mpl_connect("button_release_event", self.on_release)
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("draw_event", self._on_draw)

        # start screen: a big button in the middle of the plot
        self.empty = ttk.Frame(self.plot_frame, padding=24, relief="groove")
        ttk.Label(self.empty, text=tr("empty_title"), font=("Segoe UI", 12)).pack(pady=(0, 12))
        ttk.Button(self.empty, text=tr("import_btn"), style="Big.TButton",
                   command=self.open_files).pack()
        ttk.Label(self.empty, foreground="#666", justify="center",
                  text=tr("empty_hint")).pack(pady=(12, 0))
        self.empty.place(relx=0.5, rely=0.45, anchor="center")

        self.bind_all("<Control-o>", lambda _e: self.open_files())
        if state:
            self._restore(state)
        else:
            self.on_method_change()

    def _section(self, box, title: str):
        """Section title; returns the Frame where the section content goes."""
        ttk.Label(box, text=title, style="Title.TLabel").pack(anchor="w")
        body = ttk.Frame(box)
        body.pack(fill="x")
        return body

    def _build_advanced(self, box, peak_vals: dict | None = None):
        sec = self._section(box, tr("data"))
        cols = ttk.Frame(sec)
        cols.pack(fill="x")
        self.x_cb = self._combo(cols, tr("col_x"), 0, tr("help_x"))
        self.y_cb = self._combo(cols, tr("col_y"), 1, tr("help_y"))
        self.x_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        self.y_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        self.deriv_var = tk.BooleanVar(value=False)
        with_info(ttk.Checkbutton(ttk.Frame(sec), text=tr("dtg_chk"),
                                  variable=self.deriv_var, command=self.on_columns_change),
                  tr("help_dtg"), anchor="w", pady=(4, 0))
        self.invert_var = tk.BooleanVar(value=False)
        with_info(ttk.Checkbutton(ttk.Frame(sec), text=tr("invert_chk"),
                                  variable=self.invert_var, command=self.on_columns_change),
                  tr("help_invert"), anchor="w")

        ttk.Separator(box).pack(fill="x", pady=8)
        sec = self._section(box, tr("method"))
        self.method_var = tk.StringVar(value=DEFAULT_METHOD)  # internal method name
        self.mcb = ttk.Combobox(sec, state="readonly",
                                values=[i18n.method_name(k) for k in self.method_keys])
        self.mcb.pack(fill="x")
        self.mcb.bind("<<ComboboxSelected>>", lambda _e: self._method_picked())
        self.desc = ttk.Label(sec, wraplength=270, foreground="#555")
        self.desc.pack(anchor="w", pady=(4, 6))

        self.params_frame = ttk.Frame(sec)
        self.params_frame.pack(fill="x")

        self.anchor_frame = ttk.LabelFrame(sec, text=tr("anchors_frame"), padding=6)
        self.anchor_frame.pack(fill="x", pady=6)
        ttk.Label(self.anchor_frame, wraplength=260, text=tr("anchors_help")).pack(anchor="w")
        self.anchor_lbl = ttk.Label(self.anchor_frame, text="")
        self.anchor_lbl.pack(anchor="w", pady=(4, 0))
        self.undo_btn = ttk.Button(self.anchor_frame, text=tr("undo_edits"),
                                   command=self.undo_edits, state="disabled")
        self.undo_btn.pack(fill="x", pady=(4, 0))
        self.clear_btn = ttk.Button(self.anchor_frame, text=tr("clear_anchors"),
                                    command=self.clear_anchors, state="disabled")
        self.clear_btn.pack(fill="x", pady=(4, 0))

        self.restore_btn = ttk.Button(sec, text=tr("restore"), command=self.restore_default)
        self.restore_btn.pack(fill="x", pady=(8, 0))

        ttk.Separator(box).pack(fill="x", pady=8)
        sec = self._section(box, tr("peaks_title"))
        self.peaks_var = tk.BooleanVar(value=False)
        with_info(ttk.Checkbutton(ttk.Frame(sec), text=tr("peaks_chk"),
                                  variable=self.peaks_var, command=self.refresh),
                  tr("help_peaks"), anchor="w", pady=(2, 4))
        peak_box = ttk.Frame(sec)
        peak_box.pack(fill="x")
        for p in PEAK_PARAMS:
            self._add_control(p, peak_box, self.peak_vars, (peak_vals or {}).get(p.key))
        self.noise_lbl = ttk.Label(sec, foreground="#555")
        self.noise_lbl.pack(anchor="w", pady=(6, 0))
        self.noise_btn = ttk.Button(sec, text=tr("restore_noise"), command=self.restore_noise,
                                    state="disabled")
        self.noise_btn.pack(fill="x", pady=(2, 0))

        ttk.Separator(box).pack(fill="x", pady=8)
        self.compare_var = tk.BooleanVar(value=False)
        with_info(ttk.Checkbutton(ttk.Frame(box), text=tr("compare_chk"),
                                  variable=self.compare_var, command=self.refresh),
                  tr("help_compare"), anchor="w")

    def _combo(self, parent, label, row, help_text):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        cb = ttk.Combobox(parent, state="readonly", width=22)
        cb.grid(row=row, column=1, sticky="ew", padx=(6, 0))
        info_icon(parent, help_text).grid(row=row, column=2, padx=(4, 0))
        parent.columnconfigure(1, weight=1)
        return cb

    def _on_draw(self, _event):
        self._view_snap = None  # any new draw invalidates the stored image
        self.after_idle(self._align_tree)

    def _align_tree(self):
        """Makes the results table end at the same height as the base of the corrected plot."""
        if not self.ax2.get_visible():
            return
        try:
            widget = self.canvas.get_tk_widget()
            # figure pixels -> screen pixels (the figure may be at another DPI scale)
            scale = widget.winfo_height() / self.fig.bbox.height
            base_y = widget.winfo_rooty() + widget.winfo_height() - self.ax2.bbox.y0 * scale
            bottom = self._left.winfo_rooty() + self._left.winfo_height() - 8  # panel padding
        except tk.TclError:  # window being rebuilt (language switch)
            return
        pad = max(0, int(round(bottom - base_y)))
        if abs(pad - self._tree_pad.winfo_reqheight()) > 1:
            self._tree_pad.configure(height=pad)

    def toggle_advanced(self):
        if self.adv.winfo_ismapped():
            self.adv.pack_forget()
            self.adv_btn.config(text=tr("adv_closed"))
        else:
            self.adv.pack(side="right", fill="y", before=self.plot_frame)
            self.adv_btn.config(text=tr("adv_open"))

    # -------------------------------------------------------------- language
    def _snapshot(self) -> dict:
        """Everything the user chose, to rebuild the interface in another language."""
        return {
            "file_sel": self.listbox.curselection(),
            "x": self.x_cb.get(), "y": self.y_cb.get(),
            "deriv": self.deriv_var.get(), "invert": self.invert_var.get(),
            "method": self.method_var.get(),
            "param_vals": {k: v.get() for k, v in self.param_vars.items()},
            "peak_vals": {k: v.get() for k, v in self.peak_vars.items()},
            "peaks": self.peaks_var.get(), "compare": self.compare_var.get(),
            "edits": self.edits, "edit_sig": self._edit_sig,
            "noise_marks": list(self.noise_marks), "noise_sig": self._noise_sig,
            "peak_feet": self.peak_feet,
            "adv": bool(self.adv.winfo_ismapped()), "adv_scroll": self.adv._canvas.yview()[0],
            "tree_sel": self.tree.selection(),
            "calc_log": list(self._log_entries), "calc_sel": self._log_sel,
        }

    def _restore(self, st: dict):
        for df in self.files.values():
            self.listbox.insert("end", df.name)
        df = None
        if st["file_sel"] and st["file_sel"][0] < len(self.files):
            self.listbox.selection_set(st["file_sel"][0])
            self.listbox.see(st["file_sel"][0])
            df = self.current()
        if df:
            self.empty.place_forget()
            for cb in (self.x_cb, self.y_cb):
                cb["values"] = df.columns
            self.x_cb.set(st["x"])
            self.y_cb.set(st["y"])
        for var, key in ((self.deriv_var, "deriv"), (self.invert_var, "invert"),
                         (self.peaks_var, "peaks"), (self.compare_var, "compare")):
            var.set(st[key])
        self.method_var.set(st["method"])
        if st["adv"]:
            self.toggle_advanced()
        self._edit_sig, self.edits = st["edit_sig"], st["edits"]  # same method: they stay
        self._noise_sig, self.noise_marks = st["noise_sig"], list(st["noise_marks"])
        self.on_method_change(st["param_vals"])
        if st["peak_feet"] is not None:  # the redraw above discards the adjusted feet
            self.peak_feet = list(st["peak_feet"])
            self.refresh(keep_view=True)
        if st["tree_sel"]:
            self.tree.selection_set([i for i in st["tree_sel"] if self.tree.exists(i)])
        if st["adv"]:
            self.update_idletasks()
            self.adv._canvas.yview_moveto(st["adv_scroll"])

    def set_language(self, code: str):
        if code == i18n.get_lang():
            return
        state = self._snapshot()
        i18n.set_lang(code)
        i18n.save_lang(code)
        self.unbind_all("<MouseWheel>")  # the new panel binds the wheel again
        for w in self.winfo_children():
            w.destroy()
        self._build_ui(state)

    # ------------------------------------------------------------ files
    def open_files(self):
        paths = filedialog.askopenfilenames(
            title=tr("open_title"),
            filetypes=[(tr("ft_text"), "*.txt *.dat *.csv *.xlsx"), (tr("ft_all"), "*.*")])
        if paths:
            self.load_files(paths)

    def load_files(self, paths):
        errors, first_new = [], None
        for p in paths:
            key = str(Path(p).resolve())
            if key in self.files:  # reopening does not duplicate (the list and the dict would get out of sync)
                continue
            try:
                df = read_file(p)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{Path(p).name}: {exc}")
                continue
            self.files[key] = df
            self.listbox.insert("end", df.name)
            if first_new is None:
                first_new = self.listbox.size() - 1
        if errors:
            messagebox.showwarning(tr("read_fail"), "\n".join(errors))
        if first_new is not None:  # show the first newly imported file
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(first_new)
            self.listbox.see(first_new)
            self.on_file_change()

    def remove_file(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        key = list(self.files)[i]
        del self.files[key]
        self._file_states.pop(key, None)  # reopening the file starts from scratch
        if key == self._cur_key:
            self._cur_key = None
        self.listbox.delete(i)
        if self.files:
            self.listbox.selection_set(min(i, len(self.files) - 1))
        self.on_file_change()

    def current(self) -> DataFile | None:
        sel = self.listbox.curselection()
        return list(self.files.values())[sel[0]] if sel else None

    def _current_key(self) -> str | None:
        sel = self.listbox.curselection()
        return list(self.files)[sel[0]] if sel else None

    def on_file_change(self):
        """File switch: stores the previous file's state and puts the chosen one's in the controls."""
        key = self._current_key()
        if key is not None and key == self._cur_key:
            return  # clicked on the file that is already open
        if self._cur_key in self.files:
            self._file_states[self._cur_key] = self._file_state()
        self._cur_key = key
        df = self.current()
        if not df:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
            self.edits, self.peak_feet, self.noise_marks = None, None, []
            self._log_entries, self._log_sel = [], None
            self._log_render()
            self.refresh()
            return
        self.empty.place_forget()
        self._apply_file_state(df, self._file_states.get(key) or self._default_state(df))

    def _file_state(self) -> dict:
        """Everything that applies only to the open file: columns, method, parameters, peaks,
        adjusted anchors and feet, marked noise and the calculation log."""
        return {
            "x": self.x_cb.get(), "y": self.y_cb.get(),
            "deriv": self.deriv_var.get(), "invert": self.invert_var.get(),
            "method": self.method_var.get(),
            "param_vals": {k: v.get() for k, v in self.param_vars.items()},
            "peak_vals": {k: v.get() for k, v in self.peak_vars.items()},
            "peaks": self.peaks_var.get(), "compare": self.compare_var.get(),
            "edits": None if self.edits is None else [list(e) for e in self.edits],
            "edit_sig": self._edit_sig,
            "noise_marks": list(self.noise_marks), "noise_sig": self._noise_sig,
            "peak_feet": None if self.peak_feet is None else list(self.peak_feet),
            "log": list(self._log_entries), "log_sel": self._log_sel,
        }

    @staticmethod
    def _default_state(df: DataFile) -> dict:
        """File opened for the first time: columns from the guess, default method and parameters."""
        xi, yi, calc = guess_columns(df.columns)
        return {
            "x": df.columns[xi], "y": df.columns[yi], "deriv": calc, "invert": False,
            "method": DEFAULT_METHOD, "param_vals": {},
            "peak_vals": {p.key: np.log10(p.default) if p.log else p.default
                          for p in PEAK_PARAMS},
            "peaks": False, "compare": False, "edits": None, "edit_sig": None,
            "noise_marks": [], "noise_sig": None, "peak_feet": None, "log": [], "log_sel": None,
        }

    def _apply_file_state(self, df: DataFile, st: dict):
        for cb in (self.x_cb, self.y_cb):
            cb["values"] = df.columns
        self.x_cb.set(st["x"])
        self.y_cb.set(st["y"])
        for var, k in ((self.deriv_var, "deriv"), (self.invert_var, "invert"),
                       (self.peaks_var, "peaks"), (self.compare_var, "compare")):
            var.set(st[k])
        for k, v in st["peak_vals"].items():
            if k in self.peak_vars:
                self.peak_vars[k].set(v)
        self.method_var.set(st["method"])
        # the signatures match the restored controls: the adjustments still apply
        self._edit_sig, self.edits = st["edit_sig"], st["edits"]
        self._noise_sig, self.noise_marks = st["noise_sig"], list(st["noise_marks"])
        self._log_entries, self._log_sel = list(st["log"]), st["log_sel"]
        self._log_render()
        self.on_method_change(st["param_vals"])  # builds the parameters and redraws
        if st["peak_feet"] is not None:  # the redraw above discards the adjusted feet
            self.peak_feet = list(st["peak_feet"])
            self.refresh(keep_view=True)

    def on_columns_change(self):
        self.refresh()

    def _columns_for(self, df: DataFile) -> tuple[str, str, bool]:
        """(X, Y, calculate DTG): the chosen columns, or a guess if the file does not have them."""
        if self.x_cb.get() in df.columns and self.y_cb.get() in df.columns:
            return self.x_cb.get(), self.y_cb.get(), self.deriv_var.get()
        xi, yi, calc = guess_columns(df.columns)
        return df.columns[xi], df.columns[yi], calc

    def _xy_of(self, df: DataFile):
        # by name (in a batch, the same quantity may be in another position)
        xname, yname, calc = self._columns_for(df)
        x, y = df.xy(df.columns.index(xname), df.columns.index(yname))
        if calc:
            y = dtg(x, y)
        if self.invert_var.get():
            y = -y
        return x, y

    def xy(self):
        df = self.current()
        return self._xy_of(df) if df else None

    def y_label(self, df: DataFile | None = None):
        xname, label, calc = (self._columns_for(df) if df else
                              (self.x_cb.get(), self.y_cb.get(), self.deriv_var.get()))
        if calc:
            m = re.search(r"\(([^)]*)\)\s*$", xname)
            label = tr("dtg_label", unit=m.group(1) if m else "X")
        return f"−[{label}]" if self.invert_var.get() else label

    # -------------------------------------------------------------- method
    def _method_picked(self):
        self.method_var.set(self.method_keys[self.mcb.current()])
        self.on_method_change()

    def on_method_change(self, values: dict | None = None):
        """Builds the method controls; `values` restores already chosen values."""
        key = self.method_var.get()
        m = bl.METHODS[key]
        self.mcb.current(self.method_keys.index(key))
        self.desc.config(text=i18n.method_desc(key, m.description))
        for w in self.params_frame.winfo_children():
            w.destroy()
        self.params_frame.configure(height=1)  # an empty frame does not shrink on its own in Tk
        self.param_vars.clear()
        for p in m.params:
            self._add_control(p, self.params_frame, self.param_vars, (values or {}).get(p.key))
        self.refresh()

    def restore_default(self):
        self.method_var.set(DEFAULT_METHOD)
        self.compare_var.set(False)
        self.edits = None
        self.on_method_change()

    def _add_control(self, p: bl.Param, parent, store: dict, value=None):
        """Creates the control for `p`; `value` is the variable's raw value (restoration)."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        head = ttk.Frame(row)
        head.pack(anchor="w")
        label = i18n.param_label(p)
        lbl = ttk.Label(head, text=label)
        lbl.pack(side="left")
        if p.help:
            info_icon(head, i18n.param_help(p)).pack(side="left", padx=(4, 0))
        if p.choices:
            var = tk.StringVar(value=p.default if value is None else value)  # internal value
            store[p.key] = var
            cb = ttk.Combobox(row, values=[i18n.choice(c) for c in p.choices], state="readonly")
            cb.current(p.choices.index(var.get()))
            cb.pack(fill="x")
            # value changed by code (another file): the list follows
            var.trace_add("write", lambda *_: cb.current(p.choices.index(var.get())))

            def picked(_e):
                var.set(p.choices[cb.current()])
                self.refresh()

            cb.bind("<<ComboboxSelected>>", picked)
            return
        if p.log:
            var = tk.DoubleVar(value=np.log10(p.default) if value is None else value)
            lo, hi = np.log10(p.minimum), np.log10(p.maximum)
        else:
            var = tk.DoubleVar(value=p.default if value is None else value)
            lo, hi = p.minimum, p.maximum
        store[p.key] = var

        def show(*_):
            lbl.config(text=f"{label}: {self.param_value(p, store):.4g}")

        def changed(_v=None):
            show()
            self._debounced_refresh()

        ttk.Scale(row, from_=lo, to=hi, variable=var, command=changed).pack(fill="x")
        var.trace_add("write", show)  # value changed by code (another file): the label follows
        show()

    def param_value(self, p: bl.Param, store: dict | None = None):
        v = (self.param_vars if store is None else store)[p.key].get()
        if p.choices:
            return v
        v = 10 ** v if p.log else v
        return int(round(v)) if p.integer else v

    def current_params(self) -> dict:
        m = bl.METHODS[self.method_var.get()]
        return {p.key: self.param_value(p) for p in m.params}

    def peak_detection(self) -> dict:
        """Peak rule (k·σ threshold, minimum height, edges): applies to the events table
        and to 'peaks only' mode."""
        v = {p.key: self.param_value(p, self.peak_vars) for p in PEAK_PARAMS}
        v["skip_edges"] = v["skip_edges"] == "sim"
        return v

    def peak_params(self) -> dict | None:
        """Parameters of 'peaks only' mode, or None if off."""
        return self.peak_detection() if self.peaks_var.get() else None

    def _compute(self, name, x, y, params, feet=None, edits=None, noise=()):
        """Baseline + corrected + events, applying 'peaks only' mode if on.
        `edits`/`feet`: method anchors / peak feet adjusted by hand (they only apply to the
        current file, method and parameters). `noise`: peaks marked as noise.

        Returns (baseline, corrected, anchors, events, peak_regions)."""
        base, corr, anchors = bl.compute_full(name, x, y, edits=edits, **params)
        pp = self.peak_params()
        if pp is not None:
            # the method's anchors go away; only the peak feet remain (one on each side)
            base, corr, regions, _, anchors = pk.peaks_only(x, y, base, **pp, feet=feet,
                                                             exclude=noise)
            return base, corr, anchors, pk.region_events(x, corr, regions, signal=y), regions
        # as in Origin: peaks are searched in the corrected signal; noise does not become an event
        events, regions = pk.find_events(x, corr, signal=y, **self.peak_detection(),
                                         exclude=noise)
        return base, corr, anchors, events, regions

    # ------------------------------------------------------------- anchors
    def _anchor_mode(self) -> str | None:
        """'anchors': the method's anchors (add, drag, delete); 'feet': peak feet in
        'peaks only' mode (drag only); None: nothing editable (comparison)."""
        if self.compare_var.get() or self._curve is None:
            return None
        if self.peaks_var.get():
            return "feet" if self.auto_anchors is not None and len(self.auto_anchors) else None
        return "anchors"

    def _anchor_xs(self) -> list[float]:
        """X of the anchors the mouse edits in the current mode (in the order of the editable lists)."""
        mode = self._anchor_mode()
        if mode == "feet" and self.peak_feet is not None:
            return self.peak_feet
        if mode == "anchors" and self.edits is not None:
            return [e[0] for e in self.edits]
        return [] if self.auto_anchors is None else [float(a) for a in self.auto_anchors]

    def _begin_edit(self):
        """First edit: starts from the anchors the method showed (same baseline)."""
        mode = self._anchor_mode()
        if mode == "feet" and self.peak_feet is None:
            self.peak_feet = [float(a) for a in self.auto_anchors]
        elif mode == "anchors" and self.edits is None:
            cx, cbase = self._curve  # the baseline passes through the anchors: keep their height
            auto = [] if self.auto_anchors is None else self.auto_anchors
            self.edits = [[float(a), float(np.interp(a, cx, cbase))] for a in auto]

    def _move_anchor(self, i: int, xv: float):
        cx = self._curve[0]
        x0, x1 = cx[0], cx[-1]
        if self._anchor_mode() == "feet":  # a foot cannot pass its neighbor: each peak keeps 2 feet
            feet = self.peak_feet
            gap = 3 * (x1 - x0) / max(len(cx) - 1, 1)
            if i > 0:
                x0 = feet[i - 1] + gap
            if i < len(feet) - 1:
                x1 = feet[i + 1] - gap
            feet[i] = float(np.clip(xv, x0, max(x0, x1)))
        else:  # a moved anchor sits on the smoothed signal at the new position
            self.edits[i] = [float(np.clip(xv, x0, x1)), None]

    def undo_edits(self):
        """Goes back to the anchors found by the method."""
        self.edits = None
        self.refresh(keep_view=True)

    def clear_anchors(self):
        """Removes all anchors; with no anchors the baseline is 0 until new ones are placed on the plot."""
        self.edits = []
        self.refresh(keep_view=True)

    def _editing_anchors(self, event) -> bool:
        return (self._anchor_mode() is not None and event.inaxes in (self.ax1, self.ax2)
                and event.xdata is not None)

    def _anchor_at(self, event, tol_px: float = 8.0):
        """Index of the editable anchor under the mouse (within tol_px pixels), or None."""
        anchors = self._anchor_xs()
        if not anchors or self._curve is None:
            return None
        ax_ = np.asarray(anchors, float)
        cx, cbase = self._curve
        if event.inaxes is self.ax1:  # on the top plot: the anchor sits on the baseline
            ay = np.interp(ax_, cx, cbase)
        elif event.inaxes is self.ax2 and self._corr is not None:  # on the bottom one: on the corrected curve
            ay = np.interp(ax_, *self._corr)
        else:
            return None
        # distance in x and y: only grabs the anchor when clicking near it, not at any height
        pts = event.inaxes.transData.transform(np.column_stack([ax_, ay]))
        dist = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
        i = int(np.argmin(dist))
        return i if dist[i] <= tol_px else None

    def _on_line(self, event, tol_px: float = 6.0) -> bool:
        """Did the click land on a curve (within tol_px pixels)? Top: signal or baseline;
        bottom: the corrected curve."""
        if self._curve is None:
            return False
        cx, cbase = self._curve
        if event.inaxes is self.ax1:
            curves = [(cx, self._signal), (cx, cbase)]
        elif event.inaxes is self.ax2 and self._corr is not None:
            curves = [self._corr]
        else:
            return False
        p = np.array([event.x, event.y], float)
        for xs, ys in curves:
            pts = event.inaxes.transData.transform(np.column_stack([xs, ys]))
            a, b = pts[:-1], pts[1:]  # distance from the click to each segment of the curve
            ab = b - a
            t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
            if np.min(np.hypot(*(a + t[:, None] * ab - p).T)) <= tol_px:
                return True
        return False

    def on_click(self, event):
        editing = self._editing_anchors(event)
        if editing and event.button == 1:
            i = self._anchor_at(event)
            if i is not None:  # clicked on an anchor: start dragging
                self._begin_edit()
                self._drag = i
                return
        editing = editing and self._anchor_mode() == "anchors"  # feet: no adding or deleting
        if event.button in (1, 2) and event.inaxes in (self.ax1, self.ax2):
            # dragging pans the plot; a click without dragging on the curve adds an anchor on release
            ax = event.inaxes
            add = editing and event.button == 1 and self._on_line(event)
            self._pan = dict(ax=ax, x=event.x, y=event.y, xlim=ax.get_xlim(), ylim=ax.get_ylim(),
                             xdata=event.xdata, add=add, moved=False)
            return
        if event.button != 3 or event.inaxes not in (self.ax1, self.ax2):
            return
        i = self._anchor_at(event) if editing else None
        if i is not None:  # right click on an anchor: remove it
            self._begin_edit()
            self.edits.pop(i)
            self.refresh(keep_view=True)
            return
        self._menu_ax = event.inaxes
        widget = self.canvas.get_tk_widget()
        # the matplotlib event has no screen position: use the pointer's
        x_root, y_root = widget.winfo_pointerxy()
        try:
            self._plot_menu.tk_popup(x_root, y_root)
        finally:
            self._plot_menu.grab_release()

    def _single_figure(self, ax) -> Figure:
        """New figure with only the `ax` plot, a faithful copy (curves, anchors, highlights)."""
        fig = pickle.loads(pickle.dumps(self.fig))
        keep = fig.axes[self.fig.axes.index(ax)]
        for other in list(fig.axes):
            if other is not keep:
                fig.delaxes(other)
        keep.set_subplotspec(fig.add_gridspec(1, 1)[0])  # fills the whole window
        if not keep.get_xlabel():  # the top one shares the X axis with the bottom one, without a label
            keep.set_xlabel(self.ax2.get_xlabel())
        fig.set_layout_engine("constrained")
        fig.set_size_inches(8, 5)
        return fig

    def open_in_window(self, ax):
        """Copy of the clicked plot in its own window, with the full toolbar (zoom, save)."""
        if ax is None or not ax.has_data():
            return
        fig = self._single_figure(ax)
        top = tk.Toplevel(self)
        df = self.current()
        name = tr("signal") if ax is self.ax1 else tr("corrected_axis")
        top.title(f"{df.name} – {name}" if df else name)
        canvas = FigureCanvasTkAgg(fig, master=top)
        NavigationToolbar2Tk(canvas, top).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        wheel_zoom(canvas)
        canvas.draw()

    def save_image(self, ax):
        """Saves the clicked plot as PNG, as it appears on screen (with the current zoom)."""
        if ax is None or not ax.has_data():
            return
        df = self.current()
        part = "sinal" if ax is self.ax1 else "corrigido"
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile=f"{df.path.stem if df else 'grafico'}_{part}.png",
            initialdir=str(df.path.parent) if df else None)
        if not path:
            return
        self._single_figure(ax).savefig(path, dpi=200)
        messagebox.showinfo(tr("save_image"), tr("image_saved", path=path))

    def on_motion(self, event):
        if self._pan is not None:
            self._pan_to(event)
            return
        if self._drag is None:  # only changes the cursor over a draggable anchor
            over = self._editing_anchors(event) and self._anchor_at(event) is not None
            self.canvas.get_tk_widget().configure(cursor="sb_h_double_arrow" if over else "")
            return
        if event.xdata is None or event.inaxes not in (self.ax1, self.ax2) or self._curve is None:
            return
        self._move_anchor(self._drag, event.xdata)
        if self._drag_after is None:  # redraws at most ~30 times per second
            self._drag_after = self.after(30, self._drag_redraw)

    def _drag_redraw(self):
        self._drag_after = None
        self.refresh(keep_view=True)

    def on_release(self, _event):
        if self._pan is not None:
            pan, self._pan = self._pan, None
            self.canvas.get_tk_widget().configure(cursor="")
            if pan["add"] and not pan["moved"]:
                self._begin_edit()
                self.edits.append([float(pan["xdata"]), None])
                self.refresh(keep_view=True)
            elif pan["moved"]:
                self._view_redraw()  # axis numbers at the final position
            return
        if self._drag is None:
            return
        self._drag = None
        if self._drag_after is not None:
            self.after_cancel(self._drag_after)
            self._drag_after = None
        self.refresh(keep_view=True)

    def _pan_to(self, event, min_px: float = 3.0):
        """Moves the plot under the mouse along with the cursor (X applies to both plots)."""
        pan = self._pan
        dx, dy = event.x - pan["x"], event.y - pan["y"]
        if not pan["moved"]:
            if max(abs(dx), abs(dy)) < min_px:  # click jitter does not count as a drag
                return
            pan["moved"] = True
            self.canvas.get_tk_widget().configure(cursor="fleur")
        self._view_begin()
        ax, bbox = pan["ax"], pan["ax"].bbox
        for lim, d, size, set_ in ((pan["xlim"], dx, bbox.width, ax.set_xlim),
                                   (pan["ylim"], dy, bbox.height, ax.set_ylim)):
            shift = -d * (lim[1] - lim[0]) / size
            set_(lim[0] + shift, lim[1] + shift)
        self._view_preview()

    # Redrawing the figure takes ~0.1 s. While the user pans or zooms, the already drawn
    # image is shifted/stretched to the new limits (instant) and the real draw (axis numbers,
    # crisp lines) is only done when the mouse pauses.
    def _view_begin(self):
        self._fast_mode()
        if self._view_snap is None:
            if getattr(self.canvas, "_idle_draw_id", None):  # pending draw: stale image
                self.canvas.draw()
            self._view_grab()

    def _view_grab(self):
        """Stores the current image and, for each plot, the data area and the limits."""
        buf = np.asarray(self.canvas.get_renderer().buffer_rgba())
        h, w = buf.shape[:2]
        boxes = {}
        for ax in (self.ax1, self.ax2):
            b = ax.bbox  # 2 px inwards: the borders (spines) stay still
            c0, c1 = max(int(np.ceil(b.x0)) + 2, 0), min(int(b.x1) - 2, w)
            r0, r1 = max(h - int(b.y1) + 2, 0), min(h - int(np.ceil(b.y0)) - 2, h)
            face = (np.array(ax.patch.get_facecolor()) * 255).round().astype(np.uint8)
            boxes[ax] = (r0, r1, c0, c1, face, b.frozen(), ax.get_xlim(), ax.get_ylim())
        self._view_snap = (buf.copy(), boxes)

    def _view_preview(self, pause_ms: int = 120):
        """Shows the stored image fitted to the current limits, without redrawing."""
        bg, boxes = self._view_snap
        out = np.asarray(self.canvas.get_renderer().buffer_rgba())
        if out.shape != bg.shape:  # window changed size: only the normal draw will do
            self.canvas.draw_idle()
            return
        out[...] = bg
        h = bg.shape[0]
        for ax, (r0, r1, c0, c1, face, b, xlim0, ylim0) in boxes.items():
            # new pixel -> value at the current limits -> pixel of the stored image
            px = np.arange(c0, c1) + 0.5
            xlim, ylim = ax.get_xlim(), ax.get_ylim()
            vx = xlim[0] + (px - b.x0) / b.width * (xlim[1] - xlim[0])
            cols = np.floor(b.x0 + (vx - xlim0[0]) / (xlim0[1] - xlim0[0]) * b.width).astype(int)
            py = h - (np.arange(r0, r1) + 0.5)
            vy = ylim[0] + (py - b.y0) / b.height * (ylim[1] - ylim[0])
            rows = h - 1 - np.floor(b.y0 + (vy - ylim0[0]) / (ylim0[1] - ylim0[0]) * b.height).astype(int)
            region = out[r0:r1, c0:c1]
            region[...] = face
            # increasing mapping: the pixels that exist in the stored image form a single band
            i = np.flatnonzero((rows >= r0) & (rows < r1))
            j = np.flatnonzero((cols >= c0) & (cols < c1))
            if not (i.size and j.size):
                continue
            (i0, i1), (j0, j1) = (i[0], i[-1] + 1), (j[0], j[-1] + 1)
            rs, cs = rows[i0:i1], cols[j0:j1]
            if rs[-1] - rs[0] == i1 - i0 - 1 and cs[-1] - cs[0] == j1 - j0 - 1:  # only panned
                region[i0:i1, j0:j1] = bg[rs[0]:rs[-1] + 1, cs[0]:cs[-1] + 1]
            else:  # zoom
                region[i0:i1, j0:j1] = bg.take(rs, 0).take(cs, 1)
        self.canvas.blit()
        if self._view_after is not None:
            self.after_cancel(self._view_after)
        self._view_after = self.after(pause_ms, self._view_redraw)

    def _view_redraw(self):
        """Mouse stopped: does the real draw and continues from that image."""
        if self._view_after is not None:
            self.after_cancel(self._view_after)
            self._view_after = None
        self.canvas.draw()
        self._view_grab()

    def _fast_mode(self, settle_ms: int = 250):
        """While the user pans/zooms, draws without recalculating the layout (most of each
        frame's time) and with the legend fixed; when the mouse stops, turns the layout back on."""
        if self._settle_after is None:
            self.fig.set_layout_engine("none")  # axes stay where the layout left them
            for ax in (self.ax1, self.ax2):
                leg = ax.get_legend()
                if leg is not None and leg._loc == 0:  # "best": recalculated every frame
                    box = leg.get_window_extent().transformed(ax.transAxes.inverted())
                    leg.set_loc((box.x0, box.y0))
        else:
            self.after_cancel(self._settle_after)
        self._settle_after = self.after(settle_ms, self._settle)

    def _settle(self):
        self._settle_after = None
        self.fig.set_layout_engine("constrained")
        self.canvas.draw_idle()

    def reset_view(self):
        """The "Home" button: goes back to the full view of the last draw."""
        if self._home_view is None:
            return
        xlim, y1, y2 = self._home_view
        self.ax1.set_xlim(xlim)
        self.ax1.set_ylim(y1)
        self.ax2.set_ylim(y2)
        self.canvas.draw_idle()

    def on_scroll(self, event, factor: float = 1.25):
        """Mouse-wheel zoom, centered on the cursor: forward zooms in, backward zooms out.
        X is shared by the two plots; Y changes only on the plot under the mouse."""
        ax = event.inaxes
        if ax not in (self.ax1, self.ax2) or event.xdata is None or not ax.has_data():
            return
        scale = 1 / factor if event.button == "up" else factor
        self._view_begin()
        for get, set_, c in ((ax.get_xlim, ax.set_xlim, event.xdata),
                             (ax.get_ylim, ax.set_ylim, event.ydata)):
            lo, hi = get()
            set_(c - (c - lo) * scale, c + (hi - c) * scale)
        self._view_preview()

    def _edit_signature(self):
        """What defines the method's anchors: if it changes, the manual adjustments stop applying.
        The interpolation is left out: changing it only changes how the same anchors are joined."""
        df = self.current()
        params = {k: v for k, v in self.current_params().items() if k != "interp"}
        return (df.name if df else None, self.x_cb.get(), self.y_cb.get(),
                self.deriv_var.get(), self.invert_var.get(), self.method_var.get(),
                tuple(sorted(params.items())))

    def _data_signature(self):
        """The data itself (file and columns): noise marks apply as long as it does not change."""
        df = self.current()
        return (df.name if df else None, self.x_cb.get(), self.y_cb.get(),
                self.deriv_var.get(), self.invert_var.get())

    # ------------------------------------------------------------- plot
    def _debounced_refresh(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(150, self.refresh)

    def refresh(self, keep_view: bool = False):
        # editing anchors must not undo the zoom; switching file/method/columns should
        view = (self.ax1.get_xlim(), self.ax1.get_ylim(), self.ax2.get_ylim()) if keep_view else None
        if not keep_view:  # file, method or parameters changed: the peaks are different
            self.peak_feet = None
        sig = self._edit_signature()
        if sig != self._edit_sig:  # the method's anchors are different: manual adjustments go away
            self.edits, self._edit_sig = None, sig
        sig = self._data_signature()
        if sig != self._noise_sig:  # different data: the marked noise belonged to the old one
            self.noise_marks, self._noise_sig = [], sig
        n = len(self.noise_marks)
        self.noise_lbl.config(text=tr("noise_marked", n=n) if n else "")
        self.noise_btn.config(state="normal" if n else "disabled")
        # peaks selected in the table stay marked after editing anchors
        keep_sel = [self.events[int(i)]["pico"] for i in self.tree.selection()] if keep_view else []
        self._curve = self._corr = self._home_view = None
        self.hide_area()  # the calculated area belongs to the previous result
        self._hl = []  # ax.clear() below already removes the artists
        self.anchor_lbl.config(text="")
        self.undo_btn.config(state="normal" if self.edits is not None else "disabled")
        self.clear_btn.config(state="disabled")
        self.auto_anchors, self.events = None, []
        self.tree.delete(*self.tree.get_children())
        self.summary.config(text=tr("no_file"))
        self.stats.config(text="")
        self.ax1.clear()
        self.ax2.clear()
        for ax in (self.ax1, self.ax2):  # no file: only the start screen, no empty axes
            ax.set_visible(bool(self.files))
        try:
            data = self.xy()
        except Exception as exc:  # noqa: BLE001
            self.summary.config(text=tr("bad_cols", exc=exc))
            self.canvas.draw_idle()
            return
        if data is None:
            self.canvas.draw_idle()
            return
        x, y = data
        self.ax1.plot(x, y, color="#d62728", lw=1, label=tr("signal"))

        if self.compare_var.get():
            self._draw_compare(x, y)
        else:
            self._draw_single(x, y, self.method_var.get())

        self.ax1.set_title(self.current().name, fontsize=10)
        self.ax1.set_ylabel(self.y_label())
        self.ax1.legend(loc="best", fontsize=8)
        self.ax2.axhline(0, color="#999", lw=0.6)
        self.ax2.set_xlabel(self.x_cb.get())
        self.ax2.set_ylabel(tr("corrected_axis"))
        for ax in (self.ax1, self.ax2):  # translucent grid, behind the curves
            ax.set_axisbelow(True)
            ax.grid(True, color="#888", alpha=0.25, lw=0.6)
        self._home_view = (self.ax1.get_xlim(), self.ax1.get_ylim(), self.ax2.get_ylim())
        if view is not None:
            self.ax1.set_xlim(view[0])
            self.ax1.set_ylim(view[1])
            self.ax2.set_ylim(view[2])
        if keep_sel:  # reselect the event that still contains each marked peak
            ids = [str(k) for k, e in enumerate(self.events)
                   if any(e["inicio"] <= p <= e["fim"] for p in keep_sel)]
            self.tree.selection_set(ids)
        if keep_sel or self._log_sel is not None:  # and the marked calculation in the log
            self.highlight_selected(draw=False)
        self.canvas.draw_idle()

    def _describe(self, name):
        return tr("describe", y=self.y_label(), x=self.x_cb.get(), m=i18n.method_name(name))

    def _draw_single(self, x, y, name):
        try:
            base, corr, anchors, self.events, self.regions = self._compute(
                name, x, y, self.current_params(), self.peak_feet, self.edits, self.noise_marks)
        except Exception as exc:  # noqa: BLE001
            self.summary.config(text=f"{self._describe(name)}\n\n⚠ {exc}")
            return
        only_peaks = self.peaks_var.get()
        self._curve, self._signal = (x, base), y
        self.ax1.plot(x, base, color="black", lw=1.6,
                      label=tr("baseline_zero") if only_peaks else tr("baseline"))
        self.ax2.plot(x, corr, color="black", lw=1)
        self.ax2.margins(y=0.12)  # headroom for the label of the selected peak
        self._corr = (x, corr)
        info = [self._describe(name), tr("total_area", a=f"{np.trapezoid(corr, x):.4g}")]
        self.auto_anchors = anchors
        if not only_peaks:
            n = 0 if anchors is None else len(anchors)
            self.anchor_lbl.config(text=tr("n_anchors_edited" if self.edits is not None
                                           else "n_anchors", n=n))
            self.clear_btn.config(state="normal" if n else "disabled")
        if anchors is not None and len(anchors):
            self.ax1.plot(anchors, np.interp(anchors, x, base), "o", color="#ff7f0e",
                          ms=3.5, mec="k", mew=0.4,
                          label=tr("anchors_feet") if only_peaks else tr("anchors"))
        if only_peaks:
            info.append(tr("peaks_info", n=len(self.events)))
        elif anchors is not None and len(anchors):
            info.append(tr("anchors_info", a=len(anchors), e=len(self.events)))
        for k, e in enumerate(self.events):
            self.tree.insert("", "end", iid=str(k), values=(f"{e['inicio']:.1f}", f"{e['pico']:.1f}",
                                                f"{e['fim']:.1f}", f"{e['area']:.4g}",
                                                f"{e['area_total']:.4g}"))
        self._fit_columns()
        if self.events and "%/" in self.y_label():
            self.stats.config(text=tr("dtg_stats"))
        self.summary.config(text="\n".join(info))

    def _fit_columns(self):
        """Width of each table column = the longest text (title or value); the user does not
        resize. With spare space the columns stretch; when short, the table widens."""
        body, head = tkfont.nametofont("TkDefaultFont"), tkfont.nametofont("TkHeadingFont")
        rows = [self.tree.item(i, "values") for i in self.tree.get_children()]
        for j, c in enumerate(self.tree["columns"]):
            w = max([head.measure(self.tree.heading(c, "text")) + 14]
                    + [body.measure(str(r[j])) + 12 for r in rows])
            self.tree.column(c, width=w, minwidth=w, stretch=True)
        # the Treeview only recomputes the requested width when the displayed columns change
        self.tree.configure(displaycolumns=self.tree["displaycolumns"])

    def _toggle_row(self, event):
        """Clicking an already marked row unmarks the peak (the other marked ones stay)."""
        if self.tree.identify_region(event.x, event.y) == "separator":
            return "break"  # columns have automatic width: they cannot be dragged
        row = self.tree.identify_row(event.y)
        if event.state & 0x1:  # Shift+click: range selection, default behavior
            return None
        if not row or self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if row in self.tree.selection():
            self.tree.focus_set()
            self.tree.selection_remove(row)
            return "break"  # stops the Treeview from marking it again
        return None

    def _tree_menu(self, event):
        """Right click on a peak in the table: "Calculate area" and "Mark as noise"."""
        row = self.tree.identify_row(event.y)
        if not row or self._corr is None or self._curve is None:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)  # highlights on the plot the peak that will be calculated
        self._area_row = row
        try:
            self._area_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._area_menu.grab_release()

    def mark_noise(self):
        """The clicked peak is noise: it leaves the table and stops being the reference for the
        minimum height; in 'peaks only' mode it becomes 0."""
        if self._area_row is None or not self.tree.exists(self._area_row):
            return
        e = self.events[int(self._area_row)]
        self.noise_marks.append((e["inicio"], e["fim"]))
        if self.peak_feet is not None:  # the dragged feet of that peak go with it
            f = self.peak_feet
            pairs = [(f[i], f[i + 1]) for i in range(0, len(f) - 1, 2)]
            self.peak_feet = [v for a, b in pairs if not a <= e["pico"] <= b for v in (a, b)]
        self.tree.selection_set(())
        self.refresh(keep_view=True)

    def restore_noise(self):
        """Undoes all noise marks of the current file."""
        self.noise_marks = []
        self.refresh(keep_view=True)

    def calc_area(self):
        """Calculates the areas of the peak clicked in the table and appends them to the calculation log."""
        if self._area_row is None or not self.tree.exists(self._area_row):
            return
        e = self.events[int(self._area_row)]
        x, corr = self._corr
        a = pk.peak_areas(x, self._signal, self._curve[1], corr, e["inicio"], e["fim"])
        df = self.current()
        source = f"{df.name} · " if df else ""
        edited = f" · {tr('calcs_edited')}" if self.edits is not None else ""
        lines = [(f"{time.strftime('%H:%M:%S')}  " + tr("area_title", p=f"{e['pico']:.1f}"),
                  ("head",)),
                 (f"{source}{i18n.method_name(self.method_var.get())}{edited}", ("dim",)),
                 (tr("area_range", a=f"{e['inicio']:.1f}", b=f"{e['fim']:.1f}"), ("dim",))]
        for key, label in (("peak", "area_peak"), ("signal", "area_signal"),
                           ("baseline", "area_base")):
            lines.append((f"  {tr(label)}: {a[key]:.5g}", ("head",) if key == "peak" else ()))
        entry = {"lines": lines, "lo": e["inicio"], "hi": e["fim"], "pico": e["pico"]}
        old = [self._log_key(o) for o in self._log_entries]
        if self._log_key(entry) in old:  # repeated calculation: just shows what is already in the log
            self.calc_log.see(f"{self._log_line(old.index(self._log_key(entry)))}.0")
            return
        self._log_entries.append(entry)
        self._log_render()
        self.calc_log.see("end")

    @staticmethod
    def _log_key(entry) -> tuple:
        """What identifies a calculation: everything except the time (start of the 1st line)."""
        lines = entry["lines"]
        return (lines[0][0].split("  ", 1)[-1],) + tuple(text for text, _tags in lines[1:])

    def _log_line(self, k: int) -> int:
        """Log line (1 = first) where calculation k starts."""
        return 1 + sum(len(e["lines"]) for e in self._log_entries[:k])

    def _log_entry_at(self, event) -> int | None:
        """Index of the calculation under the mouse, or None (empty log)."""
        if not self._log_entries:
            return None
        line = int(self.calc_log.index(f"@{event.x},{event.y}").split(".")[0])
        starts = [self._log_line(k) for k in range(len(self._log_entries))]
        return max(k for k, s in enumerate(starts) if s <= line)

    def _log_select(self, event):
        """Left click on a calculation: marks its area on the corrected plot; clicking the same
        one again unmarks it."""
        k = self._log_entry_at(event)
        if k is None:
            return
        self._log_sel = None if k == self._log_sel else k
        view = self.calc_log.yview()[0]
        self._log_render()
        self.calc_log.yview_moveto(view)
        self.highlight_selected()

    def _log_render(self):
        """Redraws the log (read-only for the user): one block per calculation, with alternating
        background (the marked one in blue); with no calculations, shows how to make one."""
        log = self.calc_log
        log.configure(state="normal")
        log.delete("1.0", "end")
        entries = [e["lines"] for e in self._log_entries] or [[(tr("calcs_empty"), ("dim",))]]
        for k, lines in enumerate(entries):
            stripe = "odd" if k % 2 else "even"
            if k == self._log_sel and self._log_entries:
                stripe = "sel_entry"
            for j, (text, tags) in enumerate(lines):
                edge = (("first",) if j == 0 else ()) + (("last",) if j == len(lines) - 1 else ())
                # the line break goes inside the tag: the background reaches the right edge
                log.insert("end", text + "\n", tuple(tags) + (stripe,) + edge)
        log.delete("end-2c")  # no blank line after the last block
        log.configure(state="disabled")

    def _log_menu(self, event):
        """Right click on a log calculation: "Remove entry"."""
        self._log_clicked = self._log_entry_at(event)
        if self._log_clicked is None:
            return
        try:
            self._log_menu_pop.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu_pop.grab_release()

    def remove_log_entry(self):
        """Removes a calculation from the log; if done again, it shows up again."""
        if self._log_clicked is None or self._log_clicked >= len(self._log_entries):
            return
        view = self.calc_log.yview()[0]
        k, self._log_clicked = self._log_clicked, None
        self._log_entries.pop(k)
        if self._log_sel is not None:  # the marked one goes with it, or changes position
            self._log_sel = None if self._log_sel == k else self._log_sel - (self._log_sel > k)
        self._log_render()
        self.calc_log.yview_moveto(view)  # does not jump to the top
        self.highlight_selected()

    def clear_calc_log(self):
        self._log_entries = []
        self._log_sel = None
        self._log_render()
        self.highlight_selected()

    def hide_area(self):
        self._area_row = None

    def highlight_selected(self, draw: bool = True):
        """Marks in red, on the corrected plot, the peaks selected in the events table
        and the calculation marked in the log (over the interval where the area was calculated)."""
        for art in self._hl:
            try:
                art.remove()
            except ValueError:  # already gone along with ax.clear()
                pass
        self._hl = []
        if self._corr is not None:
            x, corr = self._corr
            spans = [(e["inicio"], e["fim"], e["pico"], e["altura"])
                     for e in (self.events[int(iid)] for iid in self.tree.selection())]
            if self._log_sel is not None and self._log_sel < len(self._log_entries):
                c = self._log_entries[self._log_sel]
                spans.append((c["lo"], c["hi"], c["pico"], float(np.interp(c["pico"], x, corr))))
            for lo, hi, peak, height in spans:
                sel = (x >= lo) & (x <= hi)
                if not sel.any():
                    continue
                xs, cs = x[sel], corr[sel]
                self._hl += [  # red area under the line (which stays black)
                    self.ax2.fill_between(xs, cs, color="#d62728", alpha=0.35, lw=0, zorder=1.5),
                    self.ax2.annotate(f"{peak:.1f}", (peak, height),
                                      xytext=(0, 4), textcoords="offset points", ha="center",
                                      va="bottom", color="#d62728", fontsize=8, zorder=5),
                ]
        if draw:
            self.canvas.draw_idle()

    def _draw_compare(self, x, y):
        lines = []
        for i, (name, m) in enumerate(bl.METHODS.items()):
            params, edits = {p.key: p.default for p in m.params}, None
            if name == self.method_var.get():  # the selected method uses the controls and adjustments
                params, edits = self.current_params(), self.edits
            try:
                base, corr = self._compute(name, x, y, params, edits=edits,
                                           noise=self.noise_marks)[:2]
            except Exception:  # noqa: BLE001
                continue
            c = COMPARE_COLORS[i % len(COMPARE_COLORS)]
            shown = i18n.method_name(name)
            self.ax1.plot(x, base, color=c, lw=1.3, label=shown)
            self.ax2.plot(x, corr, color=c, lw=0.9, label=shown)
            lines.append(tr("compare_area", m=shown, a=f"{np.trapezoid(corr, x):.4g}"))
        self.ax2.legend(loc="best", fontsize=7)
        self.summary.config(text=tr("compare_summary"))
        self.stats.config(text="\n".join(lines) or tr("compare_need"))


def main(argv: list[str] | None = None):
    """Files passed on the command line (or dragged onto the shortcut) open already loaded."""
    import sys
    files = [a for a in (sys.argv[1:] if argv is None else argv) if Path(a).is_file()]
    App(files).mainloop()
