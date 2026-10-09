"""Desktop interface (Tkinter + Matplotlib).

The main window is a blank board. "Baseline" and "Rheology", on the top bar, ask for files and
open each one as a plot in a small window of its own on the board. The tools (columns, method
and parameters in "Adjustment", peaks, calculations...) are about the selected plot.
All texts come from i18n.tr(); switching the language rebuilds the interface keeping the state.
"""
from __future__ import annotations

import copy
import pickle
import re
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from . import baselines as bl
from . import i18n
from . import peaks as pk
from . import theme
from . import updates
from .derivative import dtg
from .i18n import tr
from .io_txt import DataFile, read_file
from .rheology_gui import RheologyPanel
from .palettes import CalcWindow, ColorWindow, Palette, open_palette
from .widgets import (CAN_COPY_IMAGE, ChartWindow, PlotCanvas, ToolWindow, block_at,
                      ZoomSlider, copy_figure, file_key, file_title, log_box, paint_icon,
                      render_log, show_image, still, title_bar)

COMPARE_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b",
                  "#e377c2", "#7f7f7f", "#bcbd22"]
DEFAULT_METHOD = "Derivada 1ª + 2ª"
ICON = Path(__file__).with_name("icon.ico")
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
    """Help balloon that appears when the cursor rests on a widget. `small`: a compact box
    with a smaller font, for a balloon that is just a name."""

    def __init__(self, widget, text: str, delay_ms: int = 500, small: bool = False):
        self.widget, self.text, self.delay, self.small = widget, text, delay_ms, small
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
        tk.Label(tw, text=self.text, justify="left", wraplength=300,
                 background=theme.color("paper"), foreground=theme.color("ink"), relief="solid", borderwidth=1,
                 padx=4 if self.small else 8, pady=1 if self.small else 6,
                 font=("Segoe UI", 8 if self.small else 9)).pack()
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


def with_help(widget, text: str, **pack):
    """Packs `widget` (child of a new Frame, so the balloon only shows over its own text, not
    over the rest of the row) with a help balloon."""
    widget.pack(side="left")
    Tooltip(widget, text)
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


TOOL_SIZE = 22  # side of a header icon, in pixels
# the tools that only one analysis has; the others are about the selected plot, whatever it is
TOOL_VIEW = {"baseline": "tga", "peaks": "tga", "models": "rheology"}
VIEWS = {"tga": "tga", "rheology": "rheo_btn"}  # {analysis: the text of its name}
HISTORY_MS = 250    # how often the history looks for a change ("Back" / "Forward")
HISTORY_STEPS = 50  # how many changes it can go back


def draw_tool_icon(c: tk.Canvas, kind: str, size: int):
    """Draws a small square header icon on `c`, `size` pixels wide. Analyses - "tga": a step
    of mass loss; "rheology": a flow curve through measured points. Tools - "baseline": a peak
    over its baseline; "calcs": a sum sign; "peaks": a filled peak; "models": a table; "files":
    a sheet of paper; "adjust": three sliders."""
    c.delete("all")
    ink, paper = theme.color("ink"), theme.color("paper")
    c.configure(width=size, height=size)
    t = np.linspace(0, 1, 40)
    lo, span = size / 6, size * 11 / 15  # drawing area, in pixels (y grows downwards)
    pen = max(size / 14, 1.3)

    def points(y, x=t):
        return [v for xx, yy in zip(x, y) for v in (lo + xx * span, lo + (1 - yy) * span)]

    if kind == "tga":
        c.create_line(points(0.88 - 0.76 / (1 + np.exp(-(t - 0.5) / 0.07))), fill="#d62728",
                      width=pen)
    elif kind == "baseline":
        base = 0.12 + 0.2 * t
        c.create_line(points(base + 0.65 * np.exp(-((t - 0.5) / 0.14) ** 2)), fill="#d62728",
                      width=pen)
        c.create_line(points(base), fill=ink, width=pen)
    elif kind == "rheology":
        c.create_line(points(0.08 + 0.85 * t ** 0.45), fill=ink, width=pen)
        r = size / 11
        for x in (0.12, 0.45, 0.85):
            px, py = lo + x * span, lo + (1 - (0.08 + 0.85 * x ** 0.45)) * span
            c.create_oval(px - r, py - r, px + r, py + r, fill="#d62728", outline=ink)
    elif kind == "calcs":
        c.create_text(size / 2 + 1, size / 2 + 1, text="Σ", fill=ink,
                      font=("Segoe UI", -round(size * 0.72), "bold"))
    elif kind == "peaks":
        peak = 0.1 + 0.85 * np.exp(-((t - 0.5) / 0.16) ** 2)
        c.create_polygon(points(peak) + points([0.1, 0.1], [1, 0]), fill="#d62728", outline="")
        c.create_line(points([0.1, 0.1], [0, 1]), fill=ink, width=pen)
    elif kind == "models":  # a table: its title row filled and the line between two rows
        c.create_polygon(points([0.9, 0.9, 0.1, 0.1], [0, 1, 1, 0]), fill=paper,
                         outline=ink, width=pen)
        c.create_polygon(points([0.9, 0.9, 0.64, 0.64], [0, 1, 1, 0]), fill="#d62728",
                         outline=ink, width=pen)
        c.create_line(points([0.37, 0.37], [0, 1]), fill=ink, width=pen)
    elif kind == "adjust":
        r = size / 9
        for y, x in ((0.82, 0.3), (0.5, 0.7), (0.18, 0.45)):  # each slider: its track and knob
            c.create_line(points([y, y], [0, 1]), fill=ink, width=pen)
            px, py = lo + x * span, lo + (1 - y) * span
            c.create_oval(px - r, py - r, px + r, py + r, fill="#d62728", outline=ink)
    else:  # "files": a sheet with its corner folded and two lines of text
        sheet = [(0.18, 0), (0.62, 0), (0.84, 0.24), (0.84, 1), (0.18, 1)]
        c.create_polygon(points([1 - y for _x, y in sheet], [x for x, _y in sheet]),
                         fill=paper, outline=ink, width=pen)
        c.create_line(points([1, 0.76, 0.76], [0.62, 0.62, 0.84]), fill=ink, width=pen)
        for y in (0.5, 0.26):
            c.create_line(points([y, y], [0.32, 0.7]), fill=ink, width=pen)


def set_icon(window):
    """Window and taskbar icon. Set on each window: `iconbitmap(default=...)` does not apply it."""
    try:
        window.iconbitmap(str(ICON))
    except tk.TclError:  # no .ico support (not Windows) or file missing: default icon
        pass


class Program:
    """The program's interface, on the window it is mixed into: the main one (`App`), with the
    top bar and the board where the plots of both analyses are opened.

    The TGA analysis lives here, with its baseline: one set of controls and results, about
    one file at a time (the others keep theirs in `_file_states`), drawn on that file's own
    figures."""

    def _init_state(self):
        set_icon(self)
        self.files: dict[str, DataFile] = {}
        # each file is independent: choices, adjustments and calculation log kept per file
        self._file_states: dict[str, dict] = {}
        self._cur_key: str | None = None    # file whose state is in the controls
        # anchors adjusted by hand on top of the current method: [x, y]; y None = on the smoothed signal
        self.edits: list[list] | None = None
        self._edit_sig = None               # file/method/parameters the adjustments apply to
        self.noise_marks: list[tuple[float, float]] = []  # peaks marked as noise (X)
        self._noise_sig = None              # file/columns the marks apply to
        # peaks painted, for as long as the marks apply: (an X inside the peak, its hex color)
        self.peak_colors: list[tuple[float, str]] = []
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
        self._settle_after = None           # turns the automatic layout back on when the mouse stops
        self._view_snap = None              # stored image for pan/zoom without redrawing
        self._view_after = None             # real draw when the mouse pauses
        self._drag_after = None
        self._curve = None                  # (x, baseline) drawn, to find anchors
        self._corr = None                   # (x, corrected) drawn, to mark peaks
        self._hl: list = []                 # artists of the red marking of the selected peak
        self._update = None                 # (version, page) of a newer release, once found
        self._mark_imgs = []                # pictures of the sliders' marks
        self._rebuilding = False            # the interface is being made again (`_rebuild`)
        # "Back" / "Forward": the states left behind, the ones gone back from and the one in
        # force (`_history_state`)
        self._hist_back: list[dict] = []
        self._hist_fwd: list[dict] = []
        self._hist_now: dict | None = None
        self._hist_seen = None              # a change seen once: taken if it is still there
        # the mouse wheel does not change options: by default Tk switches the value of the
        # Combobox under the cursor when scrolling; without this binding the wheel only scrolls the panel
        for cls in ("TCombobox", "TSpinbox"):
            self.unbind_class(cls, "<MouseWheel>")
        self.bind_all("<Control-MouseWheel>", self._board_wheel)
        self._build_ui()
        self._history_tick()

    # ------------------------------------------------------------------ UI
    def _build_ui(self, state: dict | None = None):
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Small.Toolbutton", font=("Segoe UI", 8), padding=(4, 0))  # flat, no outline
        style.configure("Small.TButton", font=("Segoe UI", 8), padding=(6, 0))
        # table without its own (gray) border: it gets the same black outline as the other boxes
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        # sliders of the advanced options: a small mark in place of the theme's thumb. Once
        # for each ttk theme (the light and the dark one): the interface is rebuilt on a switch
        if "Mark.Horizontal.Scale.slider" not in style.element_names():
            mark = tk.PhotoImage(width=3, height=13)
            mark.put(theme.color("ink"), to=(0, 0, 3, 13))
            self._mark_imgs.append(mark)  # kept: Tk does not hold it
            style.element_create("Mark.Horizontal.Scale.slider", "image", mark)
        style.layout("Mark.Horizontal.TScale", [("Horizontal.Scale.trough", {
            "sticky": "nswe", "children": [
                ("Horizontal.Scale.track", {"sticky": "we"}),
                ("Mark.Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])

        self.rheo_var = tk.BooleanVar(value=False)  # True: the selected plot is a rheology one
        self._build_bar()

        # {(analysis, file): its window}, from the one at the bottom to the one on top
        self._charts: dict[tuple[str, str], ChartWindow] = {}
        self._selected: tuple[str, str] | None = None  # the plot the tools are about
        # where the plots were before a language switch: {(analysis, file): (sheet, (x, y, w, h))}
        self._chart_boxes = dict((state or {}).get("charts", ()))
        # the sheets, one tab each at the bottom left corner, and "+", which adds one. A
        # sheet is a board of its own: the blank area that takes the window, where each file
        # opened is a plot in a small window, of one of the two analyses ("tga" /
        # "rheology"). Only the board of the open sheet is on screen (`self.board`)
        self._tabs = tk.Frame(self, background=theme.color("tab"))
        self._tabs.pack(side="bottom", fill="x")
        self._add_tab = tk.Label(self._tabs, text="+", background=theme.color("tab"),
                                 cursor="hand2", padx=8, pady=2,
                                 font=("Segoe UI", 10, "bold"))
        self._add_tab.pack(side="left")
        self._add_tab.bind("<Button-1>", lambda _e: self.add_sheet())
        Tooltip(self._add_tab, tr("sheet_new"), small=True)
        # at the right end of the strip: the zoom of the open sheet
        self._zoom = ZoomSlider(self._tabs, self.set_zoom)
        self._zoom.pack(side="right", fill="y")
        # {"n": its number, "name": the one the user gave it, "zoom", "board", "hint", "tab",
        # "selected"}
        self._sheets: list[dict] = []
        self.board = None
        self._zoom_at = (0, 0)  # the point of the board that a change of zoom leaves in place
        for n, name, zoom, origin in (state or {}).get("sheets", ((1, None, 1.0, (0.0, 0.0)),)):
            self._new_sheet(n, name, zoom, origin)
        self.show_sheet((state or {}).get("sheet", 0))
        # {file: its figures and canvases, as `_figure` gives them} of the TGA plots. With none
        # open, what is drawn goes to figures that are never shown
        self._plots: dict[str, tuple] = {}
        self._no_plot = self._figure()
        (self.fig, self.ax1, self.ax2, self.canvas, self.fig2, self.canvas2) = self._no_plot
        # the open file's baseline is shown ("Baseline" tool): drawn on its plot with the
        # anchors, and the corrected plot is on screen. Kept with the file, like the rest
        self.base_on = False
        # the window has no side columns: what they held is reached through the tools of the
        # top bar
        self._tool_wins: dict[str, tk.Toplevel] = {}   # {analysis: its "Adjustment" window}
        self._tool_placed: dict[str, bool] = {}
        self.rheo = RheologyPanel(self, self._tool_window("rheology", state).body,
                                  (state or {}).get("rheo"))

        # left column: files and results. It is never shown: it only holds the lists that the
        # "Files", "Peaks" and "Calculations" tools show
        left = ttk.Frame(self, padding=8)
        # the three boxes (files, results, calculations) have the same shape: a title and, below,
        # a box with its scrollbar, so the edges line up
        ttk.Label(left, text=tr("files"), style="Title.TLabel").pack(anchor="w", pady=(0, 2))
        box = ttk.Frame(left)
        box.pack(fill="x")
        self.listbox = tk.Listbox(box, height=8, width=36, exportselection=False,
                                  activestyle="none", relief="solid", borderwidth=1,
                                  highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.on_file_change())
        self.listbox.bind("<Delete>", lambda _e: self.remove_file())
        self.listbox.bind("<Button-3>", self._file_menu)
        self._file_menu_pop = tk.Menu(self, tearoff=0)
        self._file_menu_pop.add_command(label=tr("remove"),
                                        command=lambda: self.remove_file(self._file_clicked))
        self._file_clicked = None  # file under the right click

        ttk.Label(left, text=tr("result"), style="Title.TLabel").pack(anchor="w", pady=(12, 2))
        self.summary = ttk.Label(left, wraplength=280, justify="left")
        self.summary.pack(anchor="w")
        self.stats = ttk.Label(left, wraplength=280, justify="left", foreground=theme.color("dim"))
        self.stats.pack(anchor="w", pady=(4, 0))
        self._plot_menu = tk.Menu(self, tearoff=0)
        self._plot_menu.add_command(label=tr("open_image"),
                                    command=lambda: self.open_image(self._menu_ax))
        self._plot_menu.add_command(label=tr("save_image"),
                                    command=lambda: self.save_image(self._menu_ax))
        if CAN_COPY_IMAGE:
            self._plot_menu.add_command(label=tr("copy_image"),
                                        command=lambda: self.copy_image(self._menu_ax))
        self.add_tools(self._plot_menu, "tga")
        self._menu_ax = None
        self._area_menu = tk.Menu(self, tearoff=0)
        # "Color" has a picture (a paint bucket); the others, a blank one that keeps the texts
        # in line. Kept: Tk does not hold them
        self._menu_imgs = [paint_icon(self, None), paint_icon(self, theme.color("ink"))]
        for key, command, img in (("calc_area", self.calc_area, 0),
                                  ("mark_noise", self.mark_noise, 0),
                                  ("paint_peak", self.paint_peak, 1),
                                  ("paint_all", self.paint_all, 1)):
            self._area_menu.add_command(label=tr(key), command=command, compound="left",
                                        image=self._menu_imgs[img])
        self._area_row = None
        box = ttk.Frame(left)
        box.pack(fill="x", pady=(6, 0))
        frame = tk.Frame(box, borderwidth=1, relief="solid")
        self.tree = ttk.Treeview(frame, columns=("ini", "pico", "fim", "area", "tot"),
                                 show="headings", height=6, selectmode="extended")
        for c, t in (("ini", "col_start"), ("pico", "col_peak"), ("fim", "col_end"),
                     ("area", "col_event"), ("tot", "col_total")):
            self.tree.heading(c, text=tr(t))
            self.tree.column(c, anchor="e")
        self._fit_columns()  # width from the content, redone on every result
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        frame.pack(side="left", fill="both", expand=True)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._tree_select())
        self.tree.bind("<Escape>", lambda _e: self.tree.selection_set(()))
        self.tree.bind("<ButtonPress-1>", self._toggle_row)
        # the mouse does nothing on the titles and on the column edges: no highlight under the
        # cursor and no resize arrows (both come from the Treeview's own <Motion>)
        self.tree.bind("<Motion>", lambda _e: "break")
        self.tree.bind("<Button-3>", self._tree_menu)

        # log of the area calculations (right click on the table): takes the rest of the column
        calc_head = ttk.Frame(left)
        calc_head.pack(fill="x", pady=(12, 2))
        ttk.Label(calc_head, text=tr("calcs"), style="Title.TLabel").pack(side="left")
        clear_btn = ttk.Button(calc_head, text=tr("calcs_clear"), command=self.clear_calc_log,
                               style="Small.Toolbutton", takefocus=False)
        clear_btn.pack(side="right")
        box, self.calc_log, sb = log_box(left)
        box.pack(fill="both", expand=True)
        # the button ends at the edge of the box, not of its scrollbar
        clear_btn.pack_configure(padx=(0, sb.winfo_reqwidth()))
        self.calc_log.bind("<Button-3>", self._log_menu)
        self._log_menu_pop = tk.Menu(self, tearoff=0)
        self._log_menu_pop.add_command(label=tr("calcs_copy"), command=self.copy_log_entry)
        self._log_menu_pop.add_command(label=tr("calcs_remove"), command=self.remove_log_entry)
        self._log_clicked = None  # calculation under the right click
        # calculations done, as data (the text is written in the current language when shown):
        # {"source", "method", "edited", "lo", "hi", "pico", "areas"}; they are the open
        # file's, kept with it
        self._log_entries = []
        self._log_render()

        # the data of a TGA plot (its columns): its "Adjustment" tool, a floating window like
        # the other tools
        data = ttk.Frame(self._tool_window("tga", state).body, padding=8)
        data.pack(fill="both", expand=True)
        self._build_data(data)
        # the "Baseline" tool: on the left the corrected plot of the open file (each file has
        # its canvas there, only the open one's is packed) and on the right what the baseline
        # is made with
        win = self._tool_window("baseline", state, view="tga", title=tr("tool_baseline"),
                                on_close=self.close_baseline)
        win.of_base = True  # on screen only while the selected plot shows its baseline
        self.adv = ScrollFrame(win.body)
        self.adv.pack(side="right", fill="y")
        self._corr_box = tk.Frame(win.body, background="white")
        self._corr_box.pack(side="left", fill="both", expand=True)
        self._build_advanced(self.adv.inner, ((state or {}).get("file") or {}).get("peak_vals"))

        self.bind_all("<Control-o>", lambda _e: self.open_files())
        if state:
            self._restore(state)
            for key in state["charts"]:  # one over the other as they were
                if key in self._charts:
                    self._charts[key] = self._charts.pop(key)
                    self._charts[key].lift()
            for sheet, selected in zip(self._sheets, state["sheet_sel"]):
                sheet["selected"] = selected if selected in self._charts else None
            self.show_sheet(state["sheet"])
        else:
            self.on_method_change()
        self._fit_tools()

    def _build_bar(self):
        # top bar: the only everyday actions
        self._bar = bar = ttk.Frame(self, padding=(4, 1, 4, 1))
        bar.pack(side="top", fill="x")
        views = [(view, tr(key)) for view, key in VIEWS.items()]
        # "File", in the top left corner: small and without an outline, like a menu title.
        # Load: opens files as new plots of an analysis; Save: the selected plot, as a JPEG
        file_menu = tk.Menu(self, tearoff=0)
        load = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label=tr("file_load"), menu=load)
        file_menu.add_command(label=tr("file_save"), command=self.save_plot)
        # nothing to save while no plot is selected
        file_menu.configure(postcommand=lambda: file_menu.entryconfigure(
            1, state="normal" if self._shown_plot()[1] else "disabled"))
        ttk.Menubutton(bar, text=tr("file_btn"), style="Toolbutton", takefocus=False,
                       menu=file_menu).pack(side="left", anchor="n")
        # "Analyses", next to it: each one asks for files and opens them as its plots (same as
        # the icons)
        analyses = tk.Menu(self, tearoff=0)
        for view, name in views:
            for menu in (load, analyses):
                menu.add_command(label=name, command=lambda v=view: self.open_files(v))
        ttk.Menubutton(bar, text=tr("analyses"), style="Toolbutton", takefocus=False,
                       menu=analyses).pack(side="left", anchor="n")
        # "Tools": each one opens in a small floating window over the board, about the
        # selected plot
        self._tools_menu = tools_menu = tk.Menu(self, tearoff=0)
        for kind, key in (("calcs", "tool_calc"), ("files", "tool_files"),
                          ("adjust", "tool_adjust")):
            tools_menu.add_command(label=tr(key), command=lambda k=kind: self.open_tool(k))
        # the tools that only one analysis has, under its name
        for view, name in views:
            own = tk.Menu(tools_menu, tearoff=0)
            for kind, key in (("baseline", "tool_baseline"), ("peaks", "tool_peaks"),
                              ("models", "tool_models")):
                if TOOL_VIEW[kind] == view:
                    own.add_command(label=tr(key), command=lambda k=kind: self.open_tool(k))
            tools_menu.add_cascade(label=name, menu=own)
        ttk.Menubutton(bar, text=tr("tools"), style="Toolbutton", takefocus=False,
                       menu=tools_menu).pack(side="left", anchor="n")
        # "Options": opens a menu (the interface language and its theme)
        options = tk.Menu(self, tearoff=0)
        langs = tk.Menu(options, tearoff=0)
        options.add_cascade(label=tr("language"), menu=langs)
        self._lang_var = tk.StringVar(value=i18n.get_lang())
        for code, name in i18n.LANGS.items():
            # after_idle: the switch destroys this menu, so it waits for the menu to close
            langs.add_radiobutton(label=name, value=code, variable=self._lang_var,
                                  command=lambda c=code: self.after_idle(self.set_language, c))
        themes = tk.Menu(options, tearoff=0)
        options.add_cascade(label=tr("theme"), menu=themes)
        self._theme_var = tk.StringVar(value=theme.get_theme())
        for name, key in theme.THEMES.items():
            themes.add_radiobutton(label=tr(key), value=name, variable=self._theme_var,
                                   command=lambda n=name: self.after_idle(self.set_theme, n))
        ttk.Menubutton(bar, text=tr("options_btn"), style="Toolbutton", takefocus=False,
                       menu=options).pack(side="left", anchor="n")
        # middle of the bar, on its own line: small square icons in three groups set apart by a
        # line. Left: "Adjustment" and the tools only one analysis has; middle: the tools
        # every plot has; right: one per analysis, which opens new plots of it
        tools = ttk.Frame(bar)

        def group_line():
            ttk.Separator(tools, orient="vertical").pack(side="left", fill="y", padx=8)

        def icon_cell(kind: str, name: str, command):
            # fixed cell: the icon shrinks inside it when pressed, its neighbors do not move
            cell = ttk.Frame(tools, width=TOOL_SIZE + 2, height=TOOL_SIZE + 2)
            cell.pack(side="left", padx=2)
            icon = tk.Canvas(cell, background=theme.color("paper"), cursor="hand2",
                             highlightthickness=1,
                             highlightbackground=theme.color("icon_edge"))
            icon.place(relx=0.5, rely=0.5, anchor="center")
            icon.bind("<Button-1>", lambda _e: command())
            draw_tool_icon(icon, kind, TOOL_SIZE)
            Tooltip(icon, name, small=True)
            return icon

        # before the icons, at the left of "Adjustment", two arrows: "Back" undoes the last
        # change and "Forward" makes it again. Grayed while there is nowhere to go
        ttk.Style(self).configure("Arrow.Toolbutton", font=("Segoe UI Symbol", 13),
                                  padding=(3, 0))
        self._hist_btns = []
        for key, arrow, command in (("go_back", "↶", self.go_back),
                                    ("go_forward", "↷", self.go_forward)):
            btn = ttk.Button(tools, text=arrow, style="Arrow.Toolbutton", takefocus=False,
                             command=command)
            btn.pack(side="left")
            Tooltip(btn, tr(key), small=True)
            self._hist_btns.append(btn)
        self._history_buttons()
        group_line()
        # {tool: its icon}: grayed while the selected plot does not have that tool
        self._tool_icons = {}
        for kind, key in (("adjust", "tool_adjust"), ("baseline", "tool_baseline"),
                          ("peaks", "tool_peaks"), ("models", "tool_models"), (None, ""),
                          ("calcs", "tool_calc"), ("files", "tool_files")):
            if kind is None:
                group_line()
                continue
            self._tool_icons[kind] = icon_cell(kind, tr(key), lambda k=kind: self.open_tool(k))
        group_line()
        for view, name in views:
            icon_cell(view, tr("add_plot", name=name), lambda v=view: self.open_files(v))
        tools.place(relx=0.5, rely=0.5, anchor="center")
        # `place` does not make the bar taller: this strut gives it the height of the tools
        tools.update_idletasks()  # the requested height only exists after the layout
        ttk.Frame(bar, width=0, height=tools.winfo_reqheight()).pack(side="left")
        if self._update:  # the notice comes back with the bar on a language switch
            self._show_update()

    # ------------------------------------------------------------ updates
    def _check_update(self):
        """Looks for a newer release without holding the window: the request runs on a thread
        and the window picks its answer up (Tk must not be called from another thread)."""
        found = []
        worker = threading.Thread(target=lambda: found.append(updates.check()), daemon=True)
        worker.start()

        def poll():
            if worker.is_alive():
                self.after(300, poll)
            elif found and found[0]:
                self._update = found[0]
                self._show_update()
        self.after(300, poll)

    def _show_update(self):
        """Notice at the right end of the top bar: the newer release, the button that opens its
        download page and one that puts the notice away."""
        version, page = self._update
        box = ttk.Frame(self._bar)
        box.pack(side="right")

        def dismiss():
            self._update = None
            box.destroy()

        ttk.Label(box, text=tr("update_new", v=version), font=("Segoe UI", 8)).pack(side="left")
        ttk.Button(box, text=tr("update_get"), style="Small.TButton", takefocus=False,
                   command=lambda: webbrowser.open(page)).pack(side="left", padx=(6, 0))
        hide = ttk.Button(box, text="×", style="Small.Toolbutton", takefocus=False,
                          command=dismiss)
        hide.pack(side="left")
        Tooltip(hide, tr("update_hide"), small=True)

    def _tool_window(self, name: str, state: dict | None, view: str | None = None,
                     title: str | None = None, on_close=None) -> ToolWindow:
        """Floating window `name` with controls of an analysis: its "Adjustment" one, named
        after it ("tga" or "rheology"), or the "Baseline" one of the TGA (`view`: its
        analysis, `title` and `on_close`: its own). It always exists (its controls hold the
        settings); closing it only hides it. A language switch rebuilds it where it was, open
        if it was open."""
        win = ToolWindow(self, on_close=on_close or (lambda: win.withdraw()))
        win.withdraw()
        win.title(title or tr("tool_adjust"))
        was_open, geom = ((state or {}).get("tool_wins") or {}).get(name, (False, None))
        self._tool_wins[name], self._tool_placed[name] = win, bool(geom)
        # only on screen while a plot of this analysis is the selected one
        win.of_view = view or name
        if geom:
            win.geometry(geom)
        win.parked = was_open  # it comes back when the tools are fitted to the selected plot
        return win

    def add_tools(self, menu: tk.Menu, view: str):
        """Adds to the right-click menu of a plot the tools of its analysis, under a line, in
        the order of the top bar. A plot only gets a right click once it is the selected one,
        which is the one the tools are about; the files, about every plot, are not among
        them."""
        menu.add_separator()
        for kind, key in (("adjust", "tool_adjust"), ("baseline", "tool_baseline"),
                          ("peaks", "tool_peaks"), ("models", "tool_models"),
                          ("calcs", "tool_calc")):
            if TOOL_VIEW.get(kind, view) == view:
                menu.add_command(label=tr(key), command=lambda k=kind: self.open_tool(k))

    def open_tool(self, kind: str):
        """Shows a tool of the selected plot: "adjust" or one of the floating lists. With no
        plot selected, or one of an analysis that does not have that tool, it does nothing;
        except for the files, which are about every plot."""
        view = self._selected[0] if self._selected else None
        if kind == "files":
            open_palette(self, kind)
        elif view is None or TOOL_VIEW.get(kind, view) != view:
            return
        elif kind == "adjust":
            self.open_adjust()
        elif kind == "baseline":
            self.open_baseline()
        else:
            open_palette(self, kind)

    def open_adjust(self):
        """Shows the "Adjustment" window of the selected plot's analysis; the first time, at
        the right edge of the board."""
        view = self._selected[0]
        win = self._tool_wins[view]
        if not self._tool_placed[view]:
            self._tool_placed[view] = True
            self.update_idletasks()
            board = self.board
            # a few controls: as tall as they need
            w, h = 260 if view == "rheology" else 300, win.fit_height() + 16
            x = board.winfo_rootx() + max(board.winfo_width() - w - 16, 0)
            win.geometry(f"{w}x{h}+{x}+{board.winfo_rooty() + 16}")
        win.deiconify()
        win.lift()

    def open_baseline(self):
        """"Baseline" tool of the selected TGA plot, which until then shows its signal alone:
        its baseline and anchors are drawn on it, and the window of the tool comes with its
        corrected plot and what the baseline is made with."""
        if not self.base_on:
            self.base_on = True
            self.refresh(keep_view=True)
        self._fit_baseline(self._tool_wins["baseline"])
        self._tool_wins["baseline"].lift()

    def close_baseline(self):
        """Close button of the "Baseline" window: the selected TGA plot goes back to its
        signal alone. What was adjusted stays with the file, for the next time."""
        self.base_on = False
        self.refresh(keep_view=True)
        self._fit_baseline(self._tool_wins["baseline"])

    def _fit_baseline(self, win: ToolWindow):
        """The "Baseline" window is on screen exactly while the selected plot is a TGA one
        that shows its baseline; the first time, at the bottom right corner of the board."""
        if not (self._selected and self._selected[0] == "tga" and self.base_on):
            win.withdraw()
            return
        if not self._tool_placed["baseline"]:
            self._tool_placed["baseline"] = True
            self.update_idletasks()
            board = self.board
            w, h = 760, 440
            x = board.winfo_rootx() + max(board.winfo_width() - w - 16, 0)
            y = board.winfo_rooty() + max(board.winfo_height() - h - 16, 16)
            win.geometry(f"{w}x{h}+{x}+{y}")
        if win.state() == "withdrawn":
            win.deiconify()
            self.canvas2.draw_idle()  # not drawn while it was out of sight

    def _fit_tools(self):
        """The tools follow the selected plot. The icon of a tool it does not have is grayed
        (in place: no icon of the bar ever moves) and its menu entry is off; with no plot
        selected, all of them. And every floating window (tools, calculations, pictures)
        belongs to the analysis where it was opened: it is put away while the selected plot is
        of the other one and comes back with a plot of its own. The files are the exception
        to both: they list the plots of every analysis, so they are always there."""
        view = self._selected[0] if self._selected else None
        for kind, icon in self._tool_icons.items():
            on = kind == "files" or (view is not None and TOOL_VIEW.get(kind, view) == view)
            draw_tool_icon(icon, kind, TOOL_SIZE)
            icon.configure(cursor="hand2" if on else "", highlightbackground=theme.color(
                "icon_edge" if on else "icon_edge_off"))
            for item in () if on else icon.find_all():
                for opt in ("fill", "outline"):
                    try:
                        if icon.itemcget(item, opt) not in ("", theme.color("paper")):
                            icon.itemconfigure(item, **{opt: theme.color("icon_off")})
                    except tk.TclError:  # lines have no outline
                        pass
        # the menu: the three tools every plot has, then the tools of each analysis
        for i, own in enumerate((view, "files", view, "tga", "rheology")):
            on = own == "files" or (view is not None and own == view)
            self._tools_menu.entryconfigure(i, state="normal" if on else "disabled")
        # while the interface is rebuilt every file goes through the controls: the windows
        # would come and go with each one. They are fitted once, at the end
        for win in () if self._rebuilding else self.winfo_children():
            if getattr(win, "of_view", None) is None:  # not a floating window of an analysis
                continue
            if getattr(win, "of_base", False):  # the "Baseline" window has its own rule
                self._fit_baseline(win)
                continue
            if win.of_view != view:
                if win.state() != "withdrawn":
                    win.withdraw()
                    win.parked = True
            elif getattr(win, "parked", False):
                win.parked = False
                win.deiconify()

    # -------------------------------------------------------------- board
    def _new_sheet(self, n: int, name: str | None = None, zoom: float = 1.0,
                   origin: tuple[float, float] = (0.0, 0.0)) -> dict:
        """Sheet number `n`: its board, with the hint an empty one shows, and its tab, which
        shows `name` (None: "Sheet" and its number). A double click on the tab renames the
        sheet; a right click renames or closes it. `zoom`: how large its plots are shown;
        `origin`: where, on the board, the zoom has left the corner they are placed from."""
        board = tk.Frame(self, background=theme.color("board"))
        board.bind("<Configure>", self._board_resized)
        hint = tk.Label(board, text=tr("board_hint"), background=theme.color("board"),
                        foreground=theme.color("hint"), font=("Segoe UI", 11),
                        justify="center")
        hint.place(relx=0.5, rely=0.45, anchor="center")
        tab = tk.Label(self._tabs, text=name or f"Sheet{n}", padx=10, pady=2)
        # as tall as the strip, which "+" makes taller than a tab: the open one meets its board
        tab.pack(side="left", fill="y", padx=(0, 1), before=self._add_tab)
        # "selected": the plot selected on it, which is selected again when the sheet is opened
        sheet = {"n": n, "name": name, "zoom": zoom, "origin": origin, "board": board, "hint": hint, "tab": tab,
                 "selected": None}
        tab.bind("<Button-1>", lambda _e: self.show_sheet(self._sheets.index(sheet)))
        tab.bind("<Double-Button-1>", lambda _e: self.rename_sheet(sheet))
        tab.bind("<Button-3>", lambda e: self._sheet_menu(sheet, e))
        self._sheets.append(sheet)
        return sheet

    def add_sheet(self):
        """"+": a new, empty sheet, which becomes the open one."""
        self._new_sheet(max(sheet["n"] for sheet in self._sheets) + 1)
        self.show_sheet(len(self._sheets) - 1)

    def _sheet_menu(self, sheet: dict, event):
        """Right click on the tab of a sheet: "Rename" and "Close" (not the only sheet)."""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("sheet_rename"), command=lambda: self.rename_sheet(sheet))
        menu.add_command(label=tr("sheet_close"), command=lambda: self.close_sheet(sheet),
                         state="normal" if len(self._sheets) > 1 else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def rename_sheet(self, sheet: dict):
        """The name of a sheet is typed on its own tab: Enter (or a click elsewhere) keeps
        it, Esc gives up. An empty name changes nothing. The click that ends the typing does
        nothing else."""
        tab = sheet["tab"]
        entry = tk.Entry(self._tabs, font=("Segoe UI", 9), relief="solid", borderwidth=1)
        entry.insert(0, tab.cget("text"))
        entry.select_range(0, "end")
        entry.place(in_=tab, x=0, y=0, relheight=1, width=max(tab.winfo_width(), 100))
        entry.focus_set()

        def done(keep: bool):
            if not entry.winfo_exists():  # Enter already took it: this is its focus going
                return
            name = entry.get().strip()
            entry.destroy()
            if keep and name:
                sheet["name"] = name
                tab.configure(text=name)

        for seq, keep in (("<Return>", True), ("<KP_Enter>", True), ("<FocusOut>", True),
                          ("<Escape>", False)):
            entry.bind(seq, lambda _e, k=keep: done(k))

        # a click on what takes no keyboard (the board, a plot, a tab) moves no focus: with the
        # mouse held by the entry every click comes to it, and one outside it ends the typing
        def press(event):
            if not (0 <= event.x < entry.winfo_width() and 0 <= event.y < entry.winfo_height()):
                done(True)

        entry.bind("<ButtonPress>", press, add="+")
        entry.grab_set()

    def close_sheet(self, sheet: dict):
        """Closes a sheet and the plots on it (asking first, if there are any); the sheet
        next to it becomes the open one. The only sheet is not closed."""
        if len(self._sheets) < 2:
            return
        charts = [k for k, c in self._charts.items() if c.master is sheet["board"]]
        if charts and not messagebox.askyesno(
                tr("sheet_close"), parent=self,
                message=tr("sheet_close_ask", name=sheet["tab"].cget("text"), n=len(charts))):
            return
        i = self._sheets.index(sheet)
        if sheet["board"] is self.board:  # first: its plots go away out of sight
            self.show_sheet(i - 1 if i else 1)
        for view, key in charts:
            self._close_chart(view, key)
        self._sheets.remove(sheet)
        sheet["tab"].destroy()
        sheet["board"].destroy()

    def show_sheet(self, i: int):
        """Opens sheet `i`: its board takes the window, and the plot that was selected on it
        is the selected one again (an empty sheet has none: the tools are off)."""
        sheet = self._sheets[i]
        if sheet["board"] is not self.board:
            if self.board is not None:
                self.board.pack_forget()
            self.board = sheet["board"]
            self.board.pack(fill="both", expand=True)
        for other in self._sheets:  # the tab of the open sheet stands out
            on = other is sheet
            other["tab"].configure(background=theme.color("board" if on else "tab"),
                                   cursor="" if on else "hand2",
                                   font=("Segoe UI", 9, "bold" if on else "normal"))
        self._zoom.set(round(sheet["zoom"] * 100))
        if sheet["selected"] in self._charts:
            self.select_chart(*sheet["selected"])
        else:
            self._select_none()

    def set_zoom(self, zoom: float):
        """Zoom of the open sheet (1: 100%): its plots are shown that many times as large,
        and as far from the point `_zoom_at` of the board, which stays where it is (the top
        left corner, or the mouse when the zoom comes from the wheel)."""
        sheet = self._open_sheet()
        ratio = zoom / sheet["zoom"]
        sheet["origin"] = tuple(at - (at - o) * ratio
                                for at, o in zip(self._zoom_at, sheet["origin"]))
        sheet["zoom"] = zoom
        self._zoom.set(round(zoom * 100))
        for chart in self._charts.values():
            if chart.master is sheet["board"]:
                chart.set_scale(zoom, sheet["origin"])

    def _board_wheel(self, event):
        """Ctrl + mouse wheel over the blank of the board: zoom of the sheet, in and out,
        from where the mouse is. Over a plot the wheel is the plot's own (`on_scroll`)."""
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):  # open list of a Combobox (internal Tk window)
            return
        while w is not None and w is not self.board:
            if isinstance(w, ChartWindow):
                return
            w = w.master
        if w is not None:
            self._zoom_at = (event.x_root - w.winfo_rootx(), event.y_root - w.winfo_rooty())
            try:
                self._zoom.step(1 if event.delta > 0 else -1)
            finally:
                self._zoom_at = (0, 0)

    def _sheet_of(self, chart: ChartWindow) -> dict:
        return next(sheet for sheet in self._sheets if sheet["board"] is chart.master)

    def _add_chart(self, view: str, key: str, name: str) -> ChartWindow:
        """New window on the board of the open sheet for the plot of file `key` of an
        analysis; the plot itself goes in its `body`. Square, each one a little lower and to
        the right of the last."""
        i, box = self._chart_boxes.pop((view, key), (None, None))
        sheet = self._open_sheet() if i is None else self._sheets[i]
        board = sheet["board"]
        chart = ChartWindow(board, name, on_select=lambda: self.select_chart(view, key),
                            on_close=lambda: self._close_chart(view, key))
        chart.scale, chart.origin = sheet["zoom"], sheet["origin"]
        if box is None:
            self.update_idletasks()
            room = min(board.winfo_width(), board.winfo_height()) / chart.scale - 60
            side = max(280, min(460, room))
            step = 28 * (sum(c.master is board for c in self._charts.values()) % 8)
            # from the corner of the board, wherever the zoom has left the origin
            ox, oy = (o / chart.scale for o in chart.origin)
            box = (24 + step - ox, 16 + step - oy, side, side)
        self._charts[(view, key)] = chart
        chart.put(*box)
        sheet["hint"].place_forget()
        return chart

    def _open_sheet(self) -> dict:
        """The open sheet."""
        return next(sheet for sheet in self._sheets if sheet["board"] is self.board)

    def _owner(self, view: str):
        """Who keeps the files of an analysis: the rheology panel or this window."""
        return self.rheo if view == "rheology" else self

    def plot_list(self) -> list[tuple[tuple[str, str], str]]:
        """Every open plot, of all sheets, as the files tool lists them: ((analysis, file),
        the name of the file and of its analysis), each analysis in the order its files were
        opened."""
        return [((view, key), f"{file_title(key, df.name)} — {tr(VIEWS[view])}")
                for view in VIEWS for key, df in self._owner(view).files.items()]

    def select_chart(self, view: str, key: str):
        """Selects the plot of file `key` of an analysis: it comes to the front, its file goes
        to the controls of its analysis and the tools are about it from now on. It stays the
        selected one until another plot is. A plot of another sheet opens its sheet."""
        chart = self._charts[(view, key)] = self._charts.pop((view, key))  # now the top one
        chart.lift()
        sheet = self._sheet_of(chart)
        sheet["selected"] = (view, key)
        if sheet["board"] is not self.board:
            self.show_sheet(self._sheets.index(sheet))  # which selects it
            return
        if self._selected == (view, key):
            return
        self._selected = (view, key)
        self._owner(view).show_file(key)
        for k, c in self._charts.items():
            c.set_selected(k == self._selected)
        self.rheo_var.set(view == "rheology")
        self._fit_tools()

    def _select_none(self):
        """No plot is selected (an empty sheet is open): the tools are off."""
        self._selected = None
        for chart in self._charts.values():
            chart.set_selected(False)
        self._fit_tools()

    def _close_chart(self, view: str, key: str):
        """Close button of a plot's window: its file leaves the analysis."""
        owner = self._owner(view)
        if key in owner.files:
            owner.remove_file(list(owner.files).index(key))

    def _chart_closed(self, view: str, key: str):
        """The file of a plot was removed: its window goes away. If it was the selected one
        of its sheet, the selection passes to the plot on top of the ones left there."""
        chart = self._charts.pop((view, key))
        sheet = self._sheet_of(chart)
        chart.destroy()
        left = [k for k, c in self._charts.items() if c.master is sheet["board"]]
        if sheet["selected"] == (view, key):
            sheet["selected"] = left[-1] if left else None
            if sheet["board"] is self.board:
                if left:
                    self.select_chart(*left[-1])
                else:
                    self._select_none()
        if not left:
            sheet["hint"].place(relx=0.5, rely=0.45, anchor="center")

    def _board_resized(self, event):
        for chart in self._charts.values():  # a smaller board: no plot is left out of reach
            if chart.master is event.widget:
                chart.fit()

    def _figure(self, master=None) -> tuple:
        """(figure, axes, the corrected plot's axes, canvas, the corrected plot's figure, its
        canvas) of a TGA plot. The signal goes inside `master`; the corrected plot, a figure
        of its own with the same X axis, in the "Baseline" window, where it is only packed
        while its file is the open one. None: figures that are not shown anywhere."""
        fig = Figure(figsize=(4, 4), constrained_layout=True)
        fig2 = Figure(figsize=(4, 3), constrained_layout=True)
        ax1 = fig.add_subplot(111)
        ax2 = fig2.add_subplot(111, sharex=ax1)
        if master is None:
            return fig, ax1, ax2, FigureCanvasAgg(fig), fig2, FigureCanvasAgg(fig2)
        canvas, canvas2 = PlotCanvas(fig, master=master), PlotCanvas(fig2, master=self._corr_box)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        for c in (canvas, canvas2):
            for name, handler in (("button_press_event", self.on_click),
                                  ("motion_notify_event", self.on_motion),
                                  ("button_release_event", self.on_release),
                                  ("scroll_event", self.on_scroll),
                                  ("draw_event", self._on_draw)):
                c.mpl_connect(name, handler)
        return fig, ax1, ax2, canvas, fig2, canvas2

    def _new_plot(self, key: str, name: str):
        """The plot of a TGA file, in a window of its own on the board."""
        chart = self._add_chart("tga", key, name)
        self._plots[key] = self._figure(chart.body)
        chart.watch(self._plots[key][3])

    def _use_plot(self, key: str | None):
        """From now on the baseline draws on the plot of file `key`. What a pan or zoom left
        pending on the previous one is settled first."""
        for name in ("_view_after", "_drag_after"):
            if getattr(self, name) is not None:
                self.after_cancel(getattr(self, name))
                setattr(self, name, None)
        if self._settle_after is not None:
            self.after_cancel(self._settle_after)
            self._settle()
        self._view_snap = self._pan = self._drag = None
        if isinstance(self.canvas2, PlotCanvas):  # the corrected plot leaves its window
            self.canvas2.get_tk_widget().pack_forget()
        (self.fig, self.ax1, self.ax2, self.canvas, self.fig2,
         self.canvas2) = self._plots.get(key, self._no_plot)
        if isinstance(self.canvas2, PlotCanvas):
            self.canvas2.get_tk_widget().pack(fill="both", expand=True)

    def _section(self, box, title: str):
        """Section title; returns the Frame where the section content goes."""
        ttk.Label(box, text=title, style="Title.TLabel").pack(anchor="w")
        body = ttk.Frame(box)
        body.pack(fill="x")
        return body

    def _build_data(self, box):
        sec = self._section(box, tr("data"))
        cols = ttk.Frame(sec)
        cols.pack(fill="x")
        self.x_cb = self._combo(cols, tr("col_x"), 0, tr("help_x"))
        self.y_cb = self._combo(cols, tr("col_y"), 1, tr("help_y"))
        self.x_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        self.y_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        self.deriv_var = tk.BooleanVar(value=False)
        with_help(ttk.Checkbutton(ttk.Frame(sec), text=tr("dtg_chk"),
                                  variable=self.deriv_var, command=self.on_columns_change),
                  tr("help_dtg"), anchor="w", pady=(4, 0))
        self.invert_var = tk.BooleanVar(value=False)
        with_help(ttk.Checkbutton(ttk.Frame(sec), text=tr("invert_chk"),
                                  variable=self.invert_var, command=self.on_columns_change),
                  tr("help_invert"), anchor="w")

    def _build_advanced(self, box, peak_vals: dict | None = None):
        sec = self._section(box, tr("method"))
        self.method_var = tk.StringVar(value=DEFAULT_METHOD)  # internal method name
        self.mcb = ttk.Combobox(sec, state="readonly",
                                values=[i18n.method_name(k) for k in self.method_keys])
        self.mcb.pack(fill="x")
        self.mcb.bind("<<ComboboxSelected>>", lambda _e: self._method_picked())
        self.desc = ttk.Label(sec, wraplength=270, foreground=theme.color("dim"))
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
        with_help(ttk.Checkbutton(ttk.Frame(sec), text=tr("peaks_chk"),
                                  variable=self.peaks_var, command=self.refresh),
                  tr("help_peaks"), anchor="w", pady=(2, 4))
        peak_box = ttk.Frame(sec)
        peak_box.pack(fill="x")
        for p in PEAK_PARAMS:
            self._add_control(p, peak_box, self.peak_vars, (peak_vals or {}).get(p.key))
        self.noise_lbl = ttk.Label(sec, foreground=theme.color("dim"))
        self.noise_lbl.pack(anchor="w", pady=(6, 0))
        self.noise_btn = ttk.Button(sec, text=tr("restore_noise"), command=self.restore_noise,
                                    state="disabled")
        self.noise_btn.pack(fill="x", pady=(2, 0))

        ttk.Separator(box).pack(fill="x", pady=8)
        self.compare_var = tk.BooleanVar(value=False)
        with_help(ttk.Checkbutton(ttk.Frame(box), text=tr("compare_chk"),
                                  variable=self.compare_var, command=self.refresh),
                  tr("help_compare"), anchor="w")

    def _combo(self, parent, label, row, help_text):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=0, sticky="w", pady=2)
        Tooltip(lbl, help_text)
        cb = ttk.Combobox(parent, state="readonly", width=22)
        cb.grid(row=row, column=1, sticky="ew", padx=(6, 0))
        parent.columnconfigure(1, weight=1)
        return cb

    def _on_draw(self, event):
        if event.canvas in (self.canvas, self.canvas2):
            self._view_snap = None  # any new draw invalidates the stored image

    # -------------------------------------------------------------- language
    def _snapshot(self) -> dict:
        """Everything the user chose, to rebuild the interface in another language."""
        if self._cur_key in self.files:  # the open file, like the others: kept with its file
            self._file_states[self._cur_key] = self._file_state()
        return {
            "file": self._file_state(), "cur": self._cur_key,
            "adv_scroll": self.adv._canvas.yview()[0],
            "tool_wins": {v: (w.state() != "withdrawn" or getattr(w, "parked", False),
                              w.full_geometry() if self._tool_placed[v] else None)
                          for v, w in self._tool_wins.items()},
            "rheo": self.rheo.snapshot(),
            "sheets": [(sheet["n"], sheet["name"], sheet["zoom"], sheet["origin"])
                       for sheet in self._sheets],
            "sheet": self._sheets.index(self._open_sheet()),
            "sheet_sel": [sheet["selected"] for sheet in self._sheets],
            "charts": {k: (self._sheets.index(self._sheet_of(c)), c.box)
                       for k, c in self._charts.items()},
        }

    def _restore(self, st: dict):
        self._cur_key = None  # the new controls hold no file yet
        for key, df in self.files.items():
            self.listbox.insert("end", file_title(key, df.name))
            self._new_plot(key, file_title(key, df.name))
        # each plot is drawn as its file goes through the controls: the open one last
        cur = st["cur"]
        for key in [k for k in self.files if k != cur] + [k for k in self.files if k == cur]:
            self.show_file(key)
        if not self.files:  # only what was in the controls
            f = st["file"]
            for var, key in ((self.deriv_var, "deriv"), (self.invert_var, "invert"),
                             (self.peaks_var, "peaks"), (self.compare_var, "compare")):
                var.set(f[key])
            self.method_var.set(f["method"])
            self.on_method_change(f["param_vals"])
        self.update_idletasks()
        self.adv._canvas.yview_moveto(st["adv_scroll"])

    def set_language(self, code: str):
        if code == i18n.get_lang():
            return
        state = self._snapshot()
        i18n.set_lang(code)
        i18n.save_lang(code)
        self.unbind_all("<MouseWheel>")  # the new panels bind the wheel again
        self._rebuild(state)

    def set_theme(self, name: str):
        """The interface in another theme: rebuilt with its colors, as on a language switch."""
        if name == theme.get_theme():
            return
        # from before the new colors are given: the ttk controls take them at once
        with still(self, [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]):
            state = self._snapshot()
            theme.set_theme(name)
            theme.save_theme(name)
            theme.apply(self)
            self.unbind_all("<MouseWheel>")
            self._rebuild(state)
            for w in [self] + self.winfo_children():
                if w is self or getattr(w, "is_picture", False):
                    title_bar(w)

    def _rebuild(self, state: dict):
        # the floating tools follow what is shown
        # (a cover of `still` is not of the interface: it goes by itself)
        children = [w for w in self.winfo_children() if not getattr(w, "is_cover", False)]
        kept = [w for w in children
                if isinstance(w, (Palette, CalcWindow)) or getattr(w, "is_picture", False)]
        with still(self, [w for w in children if isinstance(w, tk.Toplevel)]):
            self._rebuilding = True
            try:
                for plot in self._plots.values():
                    plot[3].close()
                    plot[5].close()
                for w in children:
                    if w not in kept:
                        w.destroy()
                self._build_ui(state)
            finally:
                self._rebuilding = False
            self._fit_tools()
            for w in kept:
                if isinstance(w, (Palette, CalcWindow)):
                    w.paint()  # they kept the colors of the theme they were made in
                if isinstance(w, CalcWindow):
                    w.retranslate()
                if isinstance(w, Palette):
                    w.follow()  # not at its next poll: with the rest
            # everything drawn, to be shown: the plots take their sizes on the events of
            # their windows and are only drawn for good on a pause after them
            self.update()
            PlotCanvas.settle()
            self.update_idletasks()

    # ------------------------------------------------------------ history
    def _history_state(self) -> dict:
        """What "Back" and "Forward" go through: the files of each analysis and what was
        chosen for each one, the sheets and the places of the plots. Copies: what is kept in
        the history does not change with the program."""
        states = {k: s for k, s in self._file_states.items() if k in self.files}
        if self._cur_key in self.files:  # the open file's is in the controls
            states[self._cur_key] = self._file_state()
        return {
            "files": dict(self.files), "states": copy.deepcopy(states),
            "rheo_files": dict(self.rheo.files), "rheo": self.rheo.choices(),
            "sheets": [(sheet["n"], sheet["name"], sheet["zoom"], sheet["origin"])
                       for sheet in self._sheets],
            "charts": {k: (self._sheets.index(self._sheet_of(c)), c.box)
                       for k, c in self._charts.items()},
        }

    @staticmethod
    def _file_choices(st: dict | None) -> dict | None:
        """A file's state without what is only a way of looking at it: the zoom of its plot
        and the peaks marked on the table. Changing those is not a step of the history."""
        return st and {k: v for k, v in st.items() if k not in ("view", "tree_sel")}

    @staticmethod
    def _history_key(st: dict) -> tuple:
        """What tells one step of the history from another. The files by who they are: the
        same file opened again is another one. The zoom of the sheets is left out, like the
        selected plot and the open sheet, which are not even in the state."""
        return ([(k, id(df)) for k, df in st["files"].items()],
                {k: Program._file_choices(s) for k, s in st["states"].items()},
                [(k, id(df)) for k, df in st["rheo_files"].items()], st["rheo"],
                [sheet[:2] for sheet in st["sheets"]], st["charts"])

    def _history_tick(self):
        """Looks for a change, as the floating tools do: nothing in the program has to tell
        the history what it changed. A change is taken when it is still the same on the next
        look, so that a drag (a slider, an anchor, a plot on the board) is one step."""
        try:
            state = self._history_state()
            key = self._history_key(state)
            if self._hist_now is None or key == self._history_key(self._hist_now):
                self._hist_now, self._hist_seen = state, None  # same step, as it looks now
            elif key != self._hist_seen:
                self._hist_seen = key
            else:
                self._history_take(state)
        except (tk.TclError, KeyError):  # the interface is being rebuilt: next time
            pass
        self.after(HISTORY_MS, self._history_tick)

    def _history_take(self, state: dict):
        """A change was made: the state before it is one step back, and there is nothing to
        go forward to any more."""
        self._hist_back = (self._hist_back + [self._hist_now])[-HISTORY_STEPS:]
        self._hist_fwd = []
        self._hist_now, self._hist_seen = state, None
        self._history_buttons()

    def _history_buttons(self):
        for btn, steps in zip(self._hist_btns, (self._hist_back, self._hist_fwd)):
            btn.configure(state="normal" if steps else "disabled")

    def go_back(self):
        """"Back": undoes the last change."""
        self._history_go(back=True)

    def go_forward(self):
        """"Forward": makes again the change "Back" undid."""
        self._history_go(back=False)

    def _history_go(self, back: bool):
        """Goes one step back or forward; the state left is the way to return to it."""
        if self._hist_now is None:
            return
        state = self._history_state()
        if self._history_key(state) != self._history_key(self._hist_now):
            self._history_take(state)  # a change just made, not seen yet
        steps, others = ((self._hist_back, self._hist_fwd) if back else
                         (self._hist_fwd, self._hist_back))
        if not steps:
            return
        others.append(state)
        self._history_apply(state, steps.pop())
        # as it really is now: a state that did not come back exactly is not a new change
        self._hist_now, self._hist_seen = self._history_state(), None
        self._history_buttons()

    def _history_apply(self, now: dict, st: dict):
        """Takes the program from state `now` to `st`. With the same files and sheets, only
        what differs is put back, each file through the controls of its analysis, and the
        plot it was about becomes the selected one; otherwise the interface is rebuilt."""
        key, new = self._history_key(now), self._history_key(st)
        if (key[0], key[2], key[4]) != (new[0], new[2], new[4]):
            self._history_rebuild(now, st)
            return
        last = None
        for name, state in st["states"].items():
            old = now["states"].get(name)
            if self._file_choices(state) == self._file_choices(old):
                continue
            state = copy.deepcopy(state)
            # the same data: the plot stays as it is being looked at
            if old and all(state[k] == old[k] for k in ("x", "y", "deriv", "invert")):
                state["view"], state["tree_sel"] = old["view"], old["tree_sel"]
            self._file_states[name] = state
            if self._cur_key == name:
                self._cur_key = None  # read again from its state, not kept from the controls
            self.show_file(name)
            last = ("tga", name)
        for name, choices in st["rheo"].items():
            if choices != now["rheo"].get(name):
                self.rheo.put_back(name, *choices)
                last = ("rheology", name)
        for name, (_sheet, box) in st["charts"].items():
            if box != now["charts"][name][1]:
                self._charts[name].put(*box)
                last = name
        if last:
            self._selected = None  # selected from scratch: its file may have left the controls
            self.select_chart(*last)

    def _history_rebuild(self, now: dict, st: dict):
        """The files or the sheets of `st` are not the ones on screen: the interface is
        rebuilt with them, as on a language switch. A plot that comes back is the selected one
        of its sheet, which is the open one."""
        snap = self._snapshot()
        charts, sheets = st["charts"], st["sheets"]
        back = [k for k in charts if k not in now["charts"]]
        was = {sheet["n"]: sheet["selected"] for sheet in self._sheets}
        selected = []
        for i, sheet in enumerate(sheets):
            on = [k for k in charts if charts[k][0] == i]
            mine = [k for k in back if k in on] or [k for k in on if k == was.get(sheet[0])]
            selected.append((mine or on or [None])[-1])
        numbers = [sheet[0] for sheet in sheets]
        n = self._open_sheet()["n"]
        self.files = dict(st["files"])
        self._file_states = copy.deepcopy(st["states"])
        rheo = st["rheo"]
        snap.update(
            cur=self._cur_key if self._cur_key in self.files else None,
            sheets=list(sheets), sheet_sel=selected, charts=dict(charts),
            sheet=charts[back[-1]][0] if back else numbers.index(n) if n in numbers else 0,
            rheo={"files": dict(st["rheo_files"]), "cur": self.rheo._cur_key,
                  "cols": {k: v[0] for k, v in rheo.items()},
                  "logs": {k: v[1] for k, v in rheo.items()},
                  "shown": {k: set(v[2]) for k, v in rheo.items()}})
        self.unbind_all("<MouseWheel>")  # the new panels bind the wheel again
        self._rebuild(snap)

    # ------------------------------------------------------------ files
    def _shown_plot(self) -> tuple[Figure, DataFile | None]:
        """Figure of the selected plot and the file drawn on it (None: no plot selected)."""
        if self._selected is None:
            return self.fig, None
        if self._selected[0] == "rheology":
            return self.rheo.view.fig, self.rheo.current()
        return self.fig, self.current()

    def save_plot(self):
        """"File > Save": the selected plot, as it appears on screen (with the current zoom),
        as a JPEG wherever the user chooses."""
        fig, df = self._shown_plot()
        if df is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".jpg", filetypes=[("JPEG", "*.jpg *.jpeg")],
            initialfile=f"{df.path.stem}.jpg", initialdir=str(df.path.parent))
        if not path:
            return
        try:
            fig.savefig(path, dpi=200, format="jpeg")
        except OSError as exc:  # no permission, disk full...
            messagebox.showerror(tr("file_save"), str(exc), parent=self)
            return
        finally:
            # saving leaves the figure drawn at the file's resolution: until it is drawn again
            # for the screen, clicks miss what is on it (legend entries, anchors)
            fig.canvas.draw()
        messagebox.showinfo(tr("file_save"), tr("image_saved", path=path), parent=self)

    def open_files(self, view: str | None = None):
        """Asks for files and opens each one as a new plot of an analysis (default: the
        selected plot's)."""
        if view is None:
            view = self._selected[0] if self._selected else "tga"
        paths = filedialog.askopenfilenames(
            parent=self, title=f"{tr(VIEWS[view])} – {tr('open_title')}",
            filetypes=[(tr("ft_text"), "*.txt *.dat *.csv *.xlsx"), (tr("ft_all"), "*.*")])
        if paths:
            self._owner(view).load_files(paths)

    def load_files(self, paths):
        """Opens each file as a new TGA plot; one already open is opened again, as a
        copy."""
        errors = []
        for p in paths:
            try:
                df = read_file(p)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{Path(p).name}: {exc}")
                continue
            key = file_key(self.files, p)
            self.files[key] = df
            self.listbox.insert("end", file_title(key, df.name))
            self._new_plot(key, file_title(key, df.name))
            self.select_chart("tga", key)  # calculated and drawn as it is selected
        if errors:
            messagebox.showwarning(tr("read_fail"), "\n".join(errors), parent=self)

    def show_file(self, key: str):
        """Puts file `key` in the controls: the tools are about its plot."""
        i = list(self.files).index(key)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.on_file_change()

    def _file_menu(self, event):
        """Right click on a file of the list: "Remove"."""
        i = self.listbox.nearest(event.y)
        box = self.listbox.bbox(i) if i >= 0 else None
        if not box or not box[1] <= event.y < box[1] + box[3]:  # clicked below the last file
            return
        self._file_clicked = i
        try:
            self._file_menu_pop.tk_popup(event.x_root, event.y_root)
        finally:
            self._file_menu_pop.grab_release()

    def remove_file(self, i: int | None = None):
        """Removes file `i` from the list (default: the open one) and closes its plot; the
        open file stays open."""
        sel = self.listbox.curselection()
        if i is None:
            if not sel:
                return
            i = sel[0]
        if not 0 <= i < len(self.files):
            return
        was_open = bool(sel) and sel[0] == i
        key = list(self.files)[i]
        del self.files[key]
        self._file_states.pop(key, None)  # reopening the file starts from scratch
        plot = self._plots.pop(key)
        if key == self._cur_key:
            self._cur_key = None
        self.listbox.delete(i)
        if self.files and was_open:
            self.listbox.selection_set(min(i, len(self.files) - 1))
        self.on_file_change()
        plot[3].close()
        plot[5].close()
        plot[5].get_tk_widget().destroy()
        self._chart_closed("tga", key)

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
        self._use_plot(key)
        df = self.current()
        if not df:
            self.edits, self.peak_feet, self.noise_marks = None, None, []
            self.peak_colors, self.base_on = [], False
            self._log_entries = []
            self._log_render()
            self.refresh()
            return
        self._apply_file_state(df, self._file_states.get(key) or self._default_state(df))

    def _file_state(self) -> dict:
        """Everything that applies only to the open file: columns, whether its baseline is
        shown, method, parameters, peaks, adjusted anchors and feet, marked noise, painted
        peaks, the calculation log, the peak marked on the table and the zoom of its plot."""
        return {
            "base_on": self.base_on,
            "x": self.x_cb.get(), "y": self.y_cb.get(),
            "deriv": self.deriv_var.get(), "invert": self.invert_var.get(),
            "method": self.method_var.get(),
            "param_vals": {k: v.get() for k, v in self.param_vars.items()},
            "peak_vals": {k: v.get() for k, v in self.peak_vars.items()},
            "peaks": self.peaks_var.get(), "compare": self.compare_var.get(),
            "edits": None if self.edits is None else [list(e) for e in self.edits],
            "edit_sig": self._edit_sig,
            "noise_marks": list(self.noise_marks), "noise_sig": self._noise_sig,
            "peak_colors": list(self.peak_colors),
            "peak_feet": None if self.peak_feet is None else list(self.peak_feet),
            "log": list(self._log_entries),
            "tree_sel": self.tree.selection(),
            "view": ((self.ax1.get_xlim(), self.ax1.get_ylim(), self.ax2.get_ylim())
                     if self.ax1.has_data() else None),
        }

    @staticmethod
    def _default_state(df: DataFile) -> dict:
        """File opened for the first time: columns from the guess, default method and parameters."""
        xi, yi, calc = guess_columns(df.columns)
        return {
            "x": df.columns[xi], "y": df.columns[yi], "deriv": calc, "invert": False,
            "base_on": False, "method": DEFAULT_METHOD, "param_vals": {},
            "peak_vals": {p.key: np.log10(p.default) if p.log else p.default
                          for p in PEAK_PARAMS},
            "peaks": False, "compare": False, "edits": None, "edit_sig": None,
            "noise_marks": [], "noise_sig": None, "peak_colors": [], "peak_feet": None,
            "log": [], "tree_sel": (), "view": None,
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
        self.peak_colors = list(st["peak_colors"])
        self._log_entries = list(st["log"])
        self._log_render()
        self.base_on = st["base_on"]
        self.on_method_change(st["param_vals"])  # builds the parameters and redraws
        if st["peak_feet"] is not None:  # the redraw above discards the adjusted feet
            self.peak_feet = list(st["peak_feet"])
            self.refresh(keep_view=True)
        if st["view"] and self.ax1.has_data():  # the plot as it was left, not fitted again
            self.ax1.set_xlim(st["view"][0])
            self.ax1.set_ylim(st["view"][1])
            self.ax2.set_ylim(st["view"][2])
            self._draw_idle()
        marked = [i for i in st["tree_sel"] if self.tree.exists(i)]
        if marked:
            self.tree.selection_set(marked)
            self.highlight_selected()

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
            Tooltip(lbl, i18n.param_help(p))
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
            self._throttled_refresh()

        scale = ttk.Scale(row, from_=lo, to=hi, variable=var, command=changed,
                          style="Mark.Horizontal.TScale")
        scale.pack(fill="x")
        self._bind_mark(scale, var, changed)
        var.trace_add("write", show)  # value changed by code (another file): the label follows
        show()

    @staticmethod
    def _bind_mark(scale, var, changed, reach: int = 6):
        """Mouse on a slider: only its mark moves it (a click on the bar does nothing), and the
        mark can be grabbed from up to `reach` pixels on each side of it."""
        grab = []  # while dragging: distance from the mouse to the mark when it was grabbed

        def press(event):
            grab.clear()
            dx = event.x - scale.coords()[0]
            if abs(dx) <= reach + 1:
                grab.append(dx)
            return "break"  # the Scale's own click (jump along the bar) does not run

        def motion(event):
            if grab:
                var.set(float(scale.get(event.x - grab[0], event.y)))
                changed()
            return "break"

        def release(_event):
            grab.clear()
            return "break"

        scale.bind("<ButtonPress-1>", press)
        scale.bind("<B1-Motion>", motion)
        scale.bind("<ButtonRelease-1>", release)
        for seq in ("<ButtonPress-2>", "<ButtonPress-3>"):  # they also jump along the bar
            scale.bind(seq, lambda _e: "break")

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
        """'anchors': the method's anchors; 'feet': peak feet in 'peaks only' mode (two per
        peak); both can be added, dragged and deleted. None: nothing editable (comparison, or
        a plot that does not show its baseline)."""
        if not self.base_on or self.compare_var.get() or self._curve is None:
            return None
        return "feet" if self.peaks_var.get() else "anchors"

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
            auto = [] if self.auto_anchors is None else self.auto_anchors
            self.peak_feet = [float(a) for a in auto]
        elif mode == "anchors" and self.edits is None:
            cx, cbase = self._curve  # the baseline passes through the anchors: keep their height
            auto = [] if self.auto_anchors is None else self.auto_anchors
            self.edits = [[float(a), float(np.interp(a, cx, cbase))] for a in auto]

    def _foot_gap(self) -> float:
        """Smallest distance between two neighboring feet (3 samples)."""
        cx = self._curve[0]
        return 3 * (cx[-1] - cx[0]) / max(len(cx) - 1, 1)

    def _foot_group(self, i: int) -> list[int]:
        """Foot i and, if it is the foot shared by two peaks (a split peak), its twin."""
        feet = self.peak_feet
        twin = i + 1 if i % 2 else i - 1  # the neighbor that belongs to the other peak
        return sorted((i, twin)) if 0 <= twin < len(feet) and feet[twin] == feet[i] else [i]

    def _move_anchor(self, i: int, xv: float):
        cx = self._curve[0]
        x0, x1 = cx[0], cx[-1]
        if self._anchor_mode() == "feet":  # a foot cannot pass its neighbor: each peak keeps 2 feet
            feet, gap = self.peak_feet, self._foot_gap()
            group = self._foot_group(i)  # a shared foot moves as one
            if group[0] > 0:
                x0 = feet[group[0] - 1] + gap
            if group[-1] < len(feet) - 1:
                x1 = feet[group[-1] + 1] - gap
            for j in group:
                feet[j] = float(np.clip(xv, x0, max(x0, x1)))
        else:  # a moved anchor sits on the smoothed signal at the new position
            self.edits[i] = [float(np.clip(xv, x0, x1)), None]

    def _add_anchor(self, xv: float):
        """Click on the curve. Method anchors: a new anchor there. Peak feet: inside a peak,
        splits it in two at that point; outside, starts a new peak around it (feet to drag)."""
        self._begin_edit()
        if self._anchor_mode() != "feet":
            self.edits.append([float(xv), None])
            return
        feet, gap, cx = self.peak_feet, self._foot_gap(), self._curve[0]
        k = int(np.searchsorted(feet, xv))
        if k % 2:  # between the two feet of a peak
            if xv - feet[k - 1] >= gap and feet[k] - xv >= gap:
                feet[k:k] = [float(xv), float(xv)]
            return
        half = 0.02 * (cx[-1] - cx[0])  # the new peak does not run over its neighbors
        a = max(xv - half, feet[k - 1] + gap if k else cx[0])
        b = min(xv + half, feet[k] - gap if k < len(feet) else cx[-1])
        if b - a >= gap:
            feet[k:k] = [float(a), float(b)]

    def _remove_anchor(self, i: int):
        """Right click on an anchor. Method anchors: removes it. Peak feet: the foot shared by
        two peaks joins them into one; any other foot removes its peak."""
        self._begin_edit()
        if self._anchor_mode() != "feet":
            self.edits.pop(i)
            return
        group = self._foot_group(i)
        lo, hi = (group[0], group[-1]) if len(group) == 2 else (i - i % 2, i - i % 2 + 1)
        del self.peak_feet[lo:hi + 1]

    def undo_edits(self):
        """Goes back to the anchors found by the method (in 'peaks only' mode, to the feet found)."""
        if self.peaks_var.get():
            self.peak_feet = None
        else:
            self.edits = None
        self.refresh(keep_view=True)

    def clear_anchors(self):
        """Removes all anchors; with no anchors the baseline is 0 until new ones are placed on the plot."""
        self.edits = []
        self.refresh(keep_view=True)

    def _editing_anchors(self, event) -> bool:
        """Anchors are edited only on the TGA plot (the original signal), not on the corrected one."""
        return (self._anchor_mode() is not None and event.inaxes is self.ax1
                and event.xdata is not None)

    def _anchor_at(self, event, tol_px: float = 8.0):
        """Index of the editable anchor under the mouse (within tol_px pixels), or None."""
        anchors = self._anchor_xs()
        if not anchors or self._curve is None or event.inaxes is not self.ax1:
            return None
        ax_ = np.asarray(anchors, float)
        cx, cbase = self._curve
        ay = np.interp(ax_, cx, cbase)  # the anchor sits on the baseline
        # distance in x and y: only grabs the anchor when clicking near it, not at any height
        pts = event.inaxes.transData.transform(np.column_stack([ax_, ay]))
        dist = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
        i = int(np.argmin(dist))
        return i if dist[i] <= tol_px else None

    def _on_line(self, event, tol_px: float = 6.0) -> bool:
        """Did the click land on a curve of the TGA plot (signal or baseline), within tol_px
        pixels?"""
        if self._curve is None or event.inaxes is not self.ax1:
            return False
        cx, cbase = self._curve
        curves = [(cx, self._signal), (cx, cbase)]
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
        if event.canvas not in (self.canvas, self.canvas2):  # not the plot in the controls
            return
        editing = self._editing_anchors(event)
        if editing and event.button == 1:
            i = self._anchor_at(event)
            if i is not None:  # clicked on an anchor: start dragging
                self._begin_edit()
                self._drag = i
                return
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
            self._remove_anchor(i)
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
        """New figure with the `ax` plot (the signal's or the corrected one), a faithful copy
        (curves, anchors, highlights)."""
        fig = pickle.loads(pickle.dumps(ax.figure))
        fig.set_layout_engine("constrained")
        fig.set_size_inches(8, 5)
        return fig

    def open_image(self, ax):
        """Picture of the clicked plot, as it appears on screen (with the current zoom), in a
        window only to look at."""
        if ax is None or not ax.has_data():
            return
        df = self.current()
        name = tr("signal") if ax is self.ax1 else tr("corrected_axis")
        top = show_image(self, self._single_figure(ax), f"{df.name} – {name}" if df else name)
        set_icon(top)
        top.of_view = "tga"  # only on screen while a plot of its analysis is selected

    def save_image(self, ax):
        """Saves the clicked plot as PNG, as it appears on screen (with the current zoom)."""
        if ax is None or not ax.has_data():
            return
        df = self.current()
        part = tr("fname_signal") if ax is self.ax1 else tr("fname_corrected")
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile=f"{df.path.stem if df else tr('fname_plot')}_{part}.png",
            initialdir=str(df.path.parent) if df else None, parent=self)
        if not path:
            return
        self._single_figure(ax).savefig(path, dpi=200)
        messagebox.showinfo(tr("save_image"), tr("image_saved", path=path), parent=self)

    def copy_image(self, ax):
        """Puts on the clipboard the picture that "Open image" shows of the clicked plot."""
        if ax is None or not ax.has_data():
            return
        try:
            copy_figure(self._single_figure(ax))
        except OSError as exc:
            messagebox.showerror(tr("copy_image"), str(exc), parent=self)

    def on_motion(self, event):
        if event.canvas not in (self.canvas, self.canvas2):
            return
        if self._pan is not None:
            self._pan_to(event)
            return
        if self._drag is None:  # only changes the cursor over a draggable anchor
            over = self._editing_anchors(event) and self._anchor_at(event) is not None
            self.canvas.get_tk_widget().configure(cursor="sb_h_double_arrow" if over else "")
            return
        if event.xdata is None or event.inaxes is not self.ax1 or self._curve is None:
            return
        self._move_anchor(self._drag, event.xdata)
        if self._drag_after is None:  # redraws at most ~30 times per second
            self._drag_after = self.after(30, self._drag_redraw)

    def _drag_redraw(self):
        self._drag_after = None
        self.refresh(keep_view=True)

    def on_release(self, event):
        if event.canvas not in (self.canvas, self.canvas2):
            return
        if self._pan is not None:
            pan, self._pan = self._pan, None
            pan["ax"].figure.canvas.get_tk_widget().configure(cursor="")
            if pan["add"] and not pan["moved"]:
                self._add_anchor(pan["xdata"])
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
        ax = pan["ax"]
        dx, dy = event.x - pan["x"], event.y - pan["y"]
        if not pan["moved"]:
            if max(abs(dx), abs(dy)) < min_px:  # click jitter does not count as a drag
                return
            pan["moved"] = True
            ax.figure.canvas.get_tk_widget().configure(cursor="fleur")
        self._view_begin()
        bbox = ax.bbox
        for lim, d, size, set_ in ((pan["xlim"], dx, bbox.width, ax.set_xlim),
                                   (pan["ylim"], dy, bbox.height, ax.set_ylim)):
            shift = -d * (lim[1] - lim[0]) / size
            set_(lim[0] + shift, lim[1] + shift)
        self._view_preview()

    # Redrawing the figure takes ~0.1 s. While the user pans or zooms, the already drawn
    # image is shifted/stretched to the new limits (instant) and the real draw (axis numbers,
    # crisp lines) is only done when the mouse pauses.
    def _shown(self) -> list[tuple]:
        """(canvas, its axes) of what is on screen of the open file: its plot and, while its
        baseline is shown, the corrected one. The other is not drawn until it is."""
        return [(self.canvas, self.ax1)] + ([(self.canvas2, self.ax2)] if self.base_on else [])

    def _draw_idle(self):
        for canvas, _ax in self._shown():
            canvas.draw_idle()

    def _view_begin(self):
        self._fast_mode()
        if self._view_snap is None:
            for canvas, _ax in self._shown():
                if getattr(canvas, "_idle_draw_id", None):  # pending draw: stale image
                    canvas.draw()
            self._view_grab()

    def _view_grab(self):
        """Stores, for each plot, the current image, the data area and the limits."""
        snap = []
        for canvas, ax in self._shown():
            buf = np.asarray(canvas.get_renderer().buffer_rgba())
            h, w = buf.shape[:2]
            b = ax.bbox  # 2 px inwards: the borders (spines) stay still
            c0, c1 = max(int(np.ceil(b.x0)) + 2, 0), min(int(b.x1) - 2, w)
            r0, r1 = max(h - int(b.y1) + 2, 0), min(h - int(np.ceil(b.y0)) - 2, h)
            face = (np.array(ax.patch.get_facecolor()) * 255).round().astype(np.uint8)
            snap.append((canvas, ax, buf.copy(),
                         (r0, r1, c0, c1, face, b.frozen(), ax.get_xlim(), ax.get_ylim())))
        self._view_snap = snap

    def _view_preview(self, pause_ms: int = 120):
        """Shows the stored images fitted to the current limits, without redrawing."""
        for canvas, ax, bg, (r0, r1, c0, c1, face, b, xlim0, ylim0) in self._view_snap:
            out = np.asarray(canvas.get_renderer().buffer_rgba())
            if out.shape != bg.shape:  # window changed size: only the normal draw will do
                canvas.draw_idle()
                continue
            out[...] = bg
            h = bg.shape[0]
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
            if i.size and j.size:
                (i0, i1), (j0, j1) = (i[0], i[-1] + 1), (j[0], j[-1] + 1)
                rs, cs = rows[i0:i1], cols[j0:j1]
                if rs[-1] - rs[0] == i1 - i0 - 1 and cs[-1] - cs[0] == j1 - j0 - 1:  # only panned
                    region[i0:i1, j0:j1] = bg[rs[0]:rs[-1] + 1, cs[0]:cs[-1] + 1]
                else:  # zoom
                    region[i0:i1, j0:j1] = bg.take(rs, 0).take(cs, 1)
            canvas.blit()
        if self._view_after is not None:
            self.after_cancel(self._view_after)
        self._view_after = self.after(pause_ms, self._view_redraw)

    def _view_redraw(self):
        """Mouse stopped: does the real draw and continues from that image."""
        if self._view_after is not None:
            self.after_cancel(self._view_after)
            self._view_after = None
        for canvas, _ax in self._shown():
            canvas.draw()
        self._view_grab()

    def _fast_mode(self, settle_ms: int = 250):
        """While the user pans/zooms, draws without recalculating the layout (most of each
        frame's time) and with the legend fixed; when the mouse stops, turns the layout back on."""
        if self._settle_after is None:
            for fig in (self.fig, self.fig2):
                fig.set_layout_engine("none")  # axes stay where the layout left them
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
        for fig in (self.fig, self.fig2):
            fig.set_layout_engine("constrained")
        self._draw_idle()

    def on_scroll(self, event, factor: float = 1.25):
        """Ctrl + mouse-wheel zoom, centered on the cursor: forward zooms in, backward zooms
        out. X is shared by the plot and its corrected one; Y changes only on the one under
        the mouse."""
        ax = event.inaxes
        if not getattr(event.guiEvent, "state", 0) & 0x4:  # Ctrl not held
            return
        if (event.canvas not in (self.canvas, self.canvas2) or ax not in (self.ax1, self.ax2)
                or event.xdata is None or not ax.has_data()):
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
    def _throttled_refresh(self):
        """Slider being dragged: the plot follows it, redrawn at most ~30 times per second."""
        if self._after_id is None:
            self._after_id = self.after(30, self._slider_redraw)

    def _slider_redraw(self):
        self._after_id = None
        self.refresh()

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
            self.peak_colors = []  # ... and so did the painted peaks
        n = len(self.noise_marks)
        self.noise_lbl.config(text=tr("noise_marked", n=n) if n else "")
        self.noise_btn.config(state="normal" if n else "disabled")
        # the peak selected in the table stays marked after editing anchors
        keep_sel = [self.events[int(i)]["pico"] for i in self.tree.selection()] if keep_view else []
        self._curve = self._corr = None
        self.hide_area()  # the calculated area belongs to the previous result
        self._hl = []  # ax.clear() below already removes the artists
        self.anchor_lbl.config(text="")
        edited = self.peak_feet if self.peaks_var.get() else self.edits
        self.undo_btn.config(state="normal" if edited is not None else "disabled")
        self.clear_btn.config(state="disabled")
        self.auto_anchors, self.events = None, []
        self.tree.delete(*self.tree.get_children())
        self.summary.config(text=tr("no_file"))
        self.stats.config(text="")
        self.ax1.clear()
        self.ax2.clear()
        for ax in (self.ax1, self.ax2):  # no file: only the start screen, no empty axes
            ax.set_visible(bool(self.files))
            for spine in ax.spines.values():  # the same frame as the flow curve's
                spine.set_linewidth(1.2)
            ax.tick_params(width=1.2)
        try:
            data = self.xy()
        except Exception as exc:  # noqa: BLE001
            self.summary.config(text=tr("bad_cols", exc=exc))
            self._draw_idle()
            return
        if data is None:
            self._draw_idle()
            return
        x, y = data
        self.ax1.plot(x, y, color="#d62728", lw=1.4, label=tr("signal"))

        if self.compare_var.get():
            self._draw_compare(x, y)
        else:
            self._draw_single(x, y, self.method_var.get())

        for ax in (self.ax1, self.ax2):  # each one is a plot of its own, in its own window
            ax.set_title(self.current().name, fontsize=10)
        self.ax1.set_xlabel(self.x_cb.get())
        self.ax1.set_ylabel(self.y_label())
        if self.base_on:  # alone, the signal needs no legend
            self.ax1.legend(loc="best", fontsize=8, edgecolor="none")
        self.ax2.axhline(0, color="#999", lw=0.6)
        self.ax2.set_xlabel(self.x_cb.get())
        self.ax2.set_ylabel(tr("corrected_axis"))
        if view is not None:
            self.ax1.set_xlim(view[0])
            self.ax1.set_ylim(view[1])
            self.ax2.set_ylim(view[2])
        if keep_sel:  # reselect the events that still contain the marked peaks
            ids = [next((str(k) for k, e in enumerate(self.events)
                         if e["inicio"] <= p <= e["fim"]), None) for p in keep_sel]
            self.tree.selection_set([i for i in ids if i is not None])
        if keep_sel:
            self.highlight_selected(draw=False)
        self._draw_idle()

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
        if self.base_on:  # the TGA plot is the signal alone until its baseline is asked for
            self.ax1.plot(x, base, color="black", lw=2.0,
                          label=tr("baseline_zero") if only_peaks else tr("baseline"))
        self.ax2.plot(x, corr, color="black", lw=1.4)
        self.ax2.margins(y=0.12)  # headroom for the label of the selected peak
        self._corr = (x, corr)
        for e in self.events:  # the painted peaks: their area, on both plots, under the curves
            color = self._peak_color(e)
            sel = (x >= e["inicio"]) & (x <= e["fim"])
            if color and sel.any():
                if self.base_on:
                    self.ax1.fill_between(x[sel], y[sel], base[sel], color=color, lw=0,
                                          zorder=1.2)
                self.ax2.fill_between(x[sel], corr[sel], color=color, lw=0, zorder=1.2)
        info = [self._describe(name), tr("total_area", a=f"{np.trapezoid(corr, x):.4g}")]
        self.auto_anchors = anchors
        if not only_peaks:
            n = 0 if anchors is None else len(anchors)
            self.anchor_lbl.config(text=tr("n_anchors_edited" if self.edits is not None
                                           else "n_anchors", n=n))
            self.clear_btn.config(state="normal" if n else "disabled")
        if self.base_on and anchors is not None and len(anchors):
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

    def _tree_select(self):
        self.highlight_selected()

    def _toggle_row(self, event):
        """Clicking the only marked row unmarks the peak. With Ctrl the click is the Treeview's
        own: the row joins the marked ones, or leaves them."""
        if self.tree.identify_region(event.x, event.y) in ("separator", "heading"):
            return "break"  # titles are not buttons; columns (automatic width) are not dragged
        row = self.tree.identify_row(event.y)
        if not row or self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if not event.state & 0x5 and self.tree.selection() == (row,):
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

    def _menu_events(self) -> list[dict]:
        """The peaks the right-click menu acts on: the clicked one and, when it is one of the
        marked ones, the others with it."""
        row = self._area_row
        if row is None or not self.tree.exists(row):
            return []
        marked = self.tree.selection()
        return [self.events[int(i)] for i in (marked if row in marked else (row,))]

    def mark_noise(self):
        """The clicked peaks are noise: they leave the table and stop being the reference for
        the minimum height; in 'peaks only' mode they become 0."""
        events = self._menu_events()
        if not events:
            return
        for e in events:
            self.noise_marks.append((e["inicio"], e["fim"]))
            if self.peak_feet is not None:  # the dragged feet of that peak go with it
                f = self.peak_feet
                pairs = [(f[i], f[i + 1]) for i in range(0, len(f) - 1, 2)]
                self.peak_feet = [v for a, b in pairs if not a <= e["pico"] <= b for v in (a, b)]
        self.tree.selection_set(())
        self.refresh(keep_view=True)

    def _peak_color(self, e: dict) -> str | None:
        """Color the peak of event `e` was painted with (None: not painted)."""
        return next((c for p, c in reversed(self.peak_colors) if e["inicio"] <= p <= e["fim"]),
                    None)

    def paint_peak(self):
        """"Color": opens the palette of colors for the peaks clicked in the table."""
        events = self._menu_events()
        if not events:
            return
        e = self.events[int(self._area_row)]
        ColorWindow(self, self._peak_color(e), lambda color: self.set_peak_color(events, color))

    def paint_all(self):
        """"Color all": opens the palette of colors for every peak of the table."""
        events = list(self.events)
        if events:
            ColorWindow(self, None, lambda color: self.set_peak_color(events, color))

    def set_peak_color(self, events: list[dict], color: str | None):
        """Paints the peaks of `events` with a hex color (None: no color again). The color is
        kept by a position inside the peak, so it stays with it when its ends change."""
        for e in events:
            self.peak_colors = [(p, c) for p, c in self.peak_colors
                                if not e["inicio"] <= p <= e["fim"]]
            if color:
                self.peak_colors.append((e["pico"], color))
        self.tree.selection_set(())  # its red marking would cover the color
        self.refresh(keep_view=True)

    def restore_noise(self):
        """Undoes all noise marks of the current file."""
        self.noise_marks = []
        self.refresh(keep_view=True)

    def restore_noise_mark(self, i: int):
        """Undoes one noise mark of the current file: its peak counts again."""
        if 0 <= i < len(self.noise_marks):
            del self.noise_marks[i]
            self.refresh(keep_view=True)

    def calc_area(self):
        """Calculates the areas of the peak clicked in the table and appends them to the calculation log."""
        if self._area_row is None or not self.tree.exists(self._area_row):
            return
        e = self.events[int(self._area_row)]
        x, corr = self._corr
        a = pk.peak_areas(x, self._signal, self._curve[1], corr, e["inicio"], e["fim"])
        df = self.current()
        entry = {"source": df.name if df else "", "method": self.method_var.get(),
                 "edited": self.edits is not None, "lo": e["inicio"], "hi": e["fim"],
                 "pico": e["pico"], "areas": dict(a)}
        CalcWindow(self, entry)  # the calculation just made, alone in its own small window
        old = [self._log_key(o) for o in self._log_entries]
        if self._log_key(entry) in old:  # repeated calculation: just shows what is already in the log
            self.calc_log.see(f"{self._log_line(old.index(self._log_key(entry)))}.0")
            return
        self._log_entries.append(entry)
        self._log_render()
        self.calc_log.see("end-1c linestart")

    @staticmethod
    def _log_lines(entry) -> list[tuple[str, tuple]]:
        """(text, tags) of each line of a calculation, in the current language."""
        source = f"{entry['source']} · " if entry["source"] else ""
        edited = f" · {tr('calcs_edited')}" if entry["edited"] else ""
        lines = [(tr("area_title", p=f"{entry['pico']:.1f}"), ("head",)),
                 (f"{source}{i18n.method_name(entry['method'])}{edited}", ("dim",)),
                 (tr("area_range", a=f"{entry['lo']:.1f}", b=f"{entry['hi']:.1f}"), ("dim",))]
        for key, label in (("peak", "area_peak"), ("signal", "area_signal"),
                           ("baseline", "area_base")):
            lines.append((f"  {tr(label)}: {entry['areas'][key]:.5g}",
                          ("head",) if key == "peak" else ()))
        return lines

    def _log_key(self, entry) -> tuple:
        """What identifies a calculation: its text."""
        return tuple(text for text, _tags in self._log_lines(entry))

    def _log_blocks(self) -> list[list[tuple[str, tuple]]]:
        """The calculations as blocks of (text, tags) lines, in the current language."""
        return [self._log_lines(e) for e in self._log_entries]

    def _log_line(self, k: int) -> int:
        """Log line (1 = first) where calculation k starts (one separator line after each
        calculation)."""
        return 1 + k + sum(len(b) for b in self._log_blocks()[:k])

    def _log_render(self):
        """Redraws the log (read-only for the user): one block per calculation; with no
        calculations, shows how to make one."""
        render_log(self.calc_log, self._log_blocks(), tr("calcs_empty"))

    def _log_menu(self, event):
        """Right click on a log calculation: "Copy" and "Remove entry"."""
        self._log_clicked = block_at(self.calc_log, self._log_blocks(), event)
        if self._log_clicked is None:
            return
        try:
            self._log_menu_pop.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu_pop.grab_release()

    def copy_log_entry(self):
        """Puts the whole text of the clicked calculation on the clipboard."""
        if self._log_clicked is None or self._log_clicked >= len(self._log_entries):
            return
        lines = self._log_lines(self._log_entries[self._log_clicked])
        self.clipboard_clear()
        self.clipboard_append("\n".join(text.strip() for text, _tags in lines))

    def remove_log_entry(self):
        """Removes a calculation from the log; if done again, it shows up again."""
        if self._log_clicked is None or self._log_clicked >= len(self._log_entries):
            return
        view = self.calc_log.yview()[0]
        k, self._log_clicked = self._log_clicked, None
        self._log_entries.pop(k)
        self._log_render()
        self.calc_log.yview_moveto(view)  # does not jump to the top

    def clear_calc_log(self):
        self._log_entries = []
        self._log_render()

    def hide_area(self):
        self._area_row = None

    def highlight_selected(self, draw: bool = True):
        """Marks in red, on the corrected plot, the peak selected in the events table."""
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
            self._draw_idle()

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
            if self.base_on:
                self.ax1.plot(x, base, color=c, lw=1.7, label=shown)
            self.ax2.plot(x, corr, color=c, lw=1.3, label=shown)
            lines.append(tr("compare_area", m=shown, a=f"{np.trapezoid(corr, x):.4g}"))
        self.ax2.legend(loc="best", fontsize=7, edgecolor="none")
        self.summary.config(text=tr("compare_summary"))
        self.stats.config(text="\n".join(lines) or tr("compare_need"))


class App(Program, tk.Tk):
    """Main window: top bar and the board with the plots."""

    def __init__(self, initial_files: list[str] | None = None, lang: str | None = None,
                 theme_name: str | None = None):
        super().__init__()
        i18n.set_lang(lang or i18n.load_lang())
        theme.set_theme(theme_name or theme.load_theme())
        theme.apply(self)
        self.title("Baseline Lab")
        self.minsize(*self.MIN_SIZE)
        self._place_reduced()
        self._init_state()
        title_bar(self)
        self._check_update()
        if initial_files:
            self.after(50, lambda: self.load_files(initial_files))

    MIN_SIZE = (900, 560)
    START_SIZE = (1100, 680)  # the most the window takes when it opens
    START_SHARE = 0.7         # ... and the share of the screen it may take

    def _place_reduced(self):
        """Opens as a reduced window in the middle of the screen: a fixed size would cover a
        small screen entirely, as if maximized."""
        screen = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = (max(low, min(top, int(s * self.START_SHARE)))
                for low, top, s in zip(self.MIN_SIZE, self.START_SIZE, screen))
        # a little above the middle: the taskbar takes the bottom of the screen
        x, y = (screen[0] - w) // 2, max(0, (screen[1] - h) // 2 - 20)
        self.geometry(f"{w}x{h}+{x}+{y}")


def main(argv: list[str] | None = None):
    """Files passed on the command line (or dragged onto the shortcut) open already loaded."""
    import sys
    files = [a for a in (sys.argv[1:] if argv is None else argv) if Path(a).is_file()]
    if sys.platform == "win32":  # own taskbar identity: without it, run from Python, the
        import ctypes            # taskbar shows the interpreter's icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BaselineLab")
    App(files).mainloop()
