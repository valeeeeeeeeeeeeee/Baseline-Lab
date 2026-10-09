"""Floating tool windows ("Tools" menu): calculations, peaks, models and files, each in a small
resizable window that stays over the board, at its side, like the palettes of an image editor.

The main window has no side columns: these windows are where its lists are seen and used. They
show the lists of the selected plot's analysis (kept by the main window) and follow them as they
change; clicks on them act on the main window, with its own menus. The files window is about no
analysis: it lists every open plot and stays on screen whichever one is selected.
"""
from __future__ import annotations

import base64
import colorsys
import tkinter as tk
from tkinter import ttk

import numpy as np

from . import i18n, theme
from .colors import (PRESETS, checker, hex_color, hex_hsva, hsva_hex, over, png, wheel_picture,
                     wheel_point, wheel_xy)
from .i18n import tr
from .widgets import ToolWindow, block_at, log_box, paint_log, render_log

TITLES = {"calcs": "tool_calc", "peaks": "tool_peaks", "files": "tool_files",
          "models": "tool_models"}
SIZE = (300, 340)   # initial size, in pixels
SIZES = {"models": (660, 190)}  # ... of the ones that need another: a wide table of few rows
# the models table: (column, its title, width, side its text is on)
MODEL_COLUMNS = (("model", "rheo_col_model", 150, "w"), ("eq", "rheo_col_eq", 120, "w"),
                 ("params", "rheo_col_params", 200, "w"), ("r2", "R²", 60, "e"),
                 ("rmse", "RMSE", 60, "e"), ("aicc", "AICc", 60, "e"))
POLL_MS = 250


def popup(menu: tk.Menu, event):
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


class Palette(ToolWindow):
    """One floating tool window of the main window `app`. `kind`: "calcs", "peaks", "models"
    or "files"."""

    def __init__(self, app, kind: str):
        super().__init__(app, on_close=self._close)
        self.app, self.kind = app, kind
        # it belongs to the analysis of the plot selected when it was created: it shows that
        # analysis's lists and is only on screen while a plot of it is the selected one. The
        # files are the exception: one window for every plot, of both analyses (no view)
        self.of_view = None if kind == "files" else palette_view(app)
        self._place()
        self._shown = None  # what is drawn: redrawn only when it changes
        build = {"calcs": self._build_calcs, "peaks": self._build_peaks,
                 "models": self._build_models, "files": self._build_files}[kind]
        build()
        self._tick()

    def _place(self):
        """At the right edge of the board, a little lower for each kind so they do not hide
        one another."""
        app = self.app
        app.update_idletasks()
        plot = app.board
        w, h = SIZES.get(self.kind, SIZE)
        step = 34 * list(TITLES).index(self.kind)
        x = plot.winfo_rootx() + max(plot.winfo_width() - w - 16, 0) - step
        y = plot.winfo_rooty() + 16 + step
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _owner(self):
        """Who keeps the lists of its view: the rheology panel or the main window."""
        return self.app.rheo if self.of_view == "rheology" else self.app

    def paint(self):
        super().paint()
        if self.kind == "calcs":
            paint_log(self.log)
        elif self.kind == "peaks":
            self.stats.configure(foreground=theme.color("dim"))
        self._shown = None  # drawn again: the lines of the log take their color as they are made

    # ------------------------------------------------------------ contents
    def _build_calcs(self):
        self.clear_btn = ttk.Button(self.body, style="Small.Toolbutton", takefocus=False,
                                    command=self._clear)
        self.clear_btn.pack(anchor="e", padx=6, pady=(4, 0))
        box, self.log, _sb = log_box(self.body)
        box.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.log.bind("<Button-3>", self._calc_menu)
        self._blocks: list[list[tuple[str, tuple]]] = []

    def _build_peaks(self):
        # what the table is about (signal, method, total area), above it
        self.summary = ttk.Label(self.body, justify="left")
        self.summary.pack(anchor="w", padx=6, pady=(6, 0))
        self.stats = ttk.Label(self.body, justify="left", foreground=theme.color("dim"))
        self.stats.pack(anchor="w", padx=6)
        self.body.bind("<Configure>", self._wrap)
        peaks = frame = tk.Frame(self.body, borderwidth=1, relief="solid")
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.table = ttk.Treeview(frame, show="headings", selectmode="none", takefocus=False)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        # the clicks act on the peaks of the main window; no highlight under the cursor and no
        # dragging of the column edges
        self.table.bind("<Button-1>", self._pick_peak)
        self.table.bind("<Button-3>", self._peak_menu)
        for seq in ("<B1-Motion>", "<Motion>", "<Double-Button-1>"):
            self.table.bind(seq, lambda _e: "break")
        # under it, the peaks taken out of the table as noise: where each one starts and ends
        # (placed before the table: in a short window it is the table that gives up room)
        frame = tk.Frame(self.body, borderwidth=1, relief="solid")
        frame.pack(side="bottom", fill="x", padx=6, pady=(2, 6), before=peaks)
        self.noise_title = ttk.Label(self.body, style="Title.TLabel")
        self.noise_title.pack(side="bottom", anchor="w", padx=6, before=peaks)
        self.noise = ttk.Treeview(frame, show="headings", selectmode="none", takefocus=False,
                                  height=3, columns=("lo", "hi"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.noise.yview)
        self.noise.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.noise.pack(side="left", fill="x", expand=True)
        self.noise.bind("<Button-3>", self._noise_menu)
        for seq in ("<Button-1>", "<B1-Motion>", "<Motion>", "<Double-Button-1>"):
            self.noise.bind(seq, lambda _e: "break")

    def _build_models(self):
        frame = tk.Frame(self.body, borderwidth=1, relief="solid")
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        # the highlighted rows are the models whose curve is on the plot
        self.table = ttk.Treeview(frame, columns=[c[0] for c in MODEL_COLUMNS], show="headings",
                                  selectmode="none", takefocus=False)
        for c, _title, width, anchor in MODEL_COLUMNS:
            self.table.column(c, width=width, anchor=anchor, stretch=c in ("model", "params"))
        self.table.tag_configure("best", font=("Segoe UI", 9, "bold"))
        self.table.pack(fill="both", expand=True)
        # a click on a row chooses the curve on the plot; nothing else: no clicking on titles
        # and no dragging of the column edges
        self.table.bind("<Button-1>", self._pick_model)
        for seq in ("<Double-Button-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Motion>"):
            self.table.bind(seq, lambda _e: "break")

    def _wrap(self, event):
        for label in (self.summary, self.stats):  # the text above the table follows its width
            label.configure(wraplength=max(event.width - 12, 60))

    def _build_files(self):
        box = ttk.Frame(self.body)
        box.pack(fill="both", expand=True, padx=6, pady=6)
        self.list = tk.Listbox(box, exportselection=False, activestyle="none", relief="solid",
                               borderwidth=1, highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<<ListboxSelect>>", lambda _e: self._pick_file())
        self.list.bind("<Button-3>", self._file_menu)
        self.list.bind("<Delete>", lambda _e: self._remove_file())
        self._plots: list[tuple[str, str]] = []  # (analysis, file) of each row

    # ------------------------------------------------------------- content
    def _state(self):
        """What the window must show now, read from the main window."""
        app = self.app
        if self.kind == "calcs":
            if self.of_view == "rheology":
                return app.rheo.log_blocks, ""
            return app._log_blocks(), tr("calcs_empty")
        if self.kind == "peaks":
            tree = app.tree
            rows = tree.get_children()
            return (app.summary.cget("text"), app.stats.cget("text"),
                    [tree.heading(c, "text") for c in tree["columns"]],
                    [tuple(tree.item(i, "values")) for i in rows],
                    [rows.index(i) for i in tree.selection() if i in rows],
                    [tuple(m) for m in app.noise_marks])
        if self.kind == "models":
            return app.rheo.view.rows() if app.rheo.view else []
        return app.plot_list(), app._selected

    def _draw(self, state):
        if self.kind == "calcs":
            self._blocks, empty = state
            self.clear_btn.configure(text=tr("calcs_clear"))
            view = self.log.yview()[0]
            render_log(self.log, self._blocks, empty)
            self.log.yview_moveto(view)
        elif self.kind == "peaks":
            summary, stats, heads, rows, marked, noise = state
            self.summary.configure(text=summary)
            self.stats.configure(text=stats)
            cols = [str(i) for i in range(len(heads))]
            self.table.configure(columns=cols)
            for c, text in zip(cols, heads):
                self.table.heading(c, text=text)
                self.table.column(c, width=50, anchor="e", stretch=True)
            self.table.delete(*self.table.get_children())
            for row in rows:
                self.table.insert("", "end", values=row)
            ids = self.table.get_children()
            self.table.selection_set([ids[k] for k in marked])
            self.noise_title.configure(text=tr("noise_list"))
            for c, text in (("lo", heads[0]), ("hi", heads[2])):  # "Start" and "End"
                self.noise.heading(c, text=text)
                self.noise.column(c, width=50, anchor="e", stretch=True)
            self.noise.delete(*self.noise.get_children())
            for lo, hi in noise:
                self.noise.insert("", "end", values=(f"{lo:.1f}", f"{hi:.1f}"))
        elif self.kind == "models":
            for c, title, _width, anchor in MODEL_COLUMNS:
                self.table.heading(c, anchor=anchor,
                                   text=tr(title) if title.startswith("rheo_") else title)
            self.table.delete(*self.table.get_children())
            for k, (key, values, _drawn) in enumerate(state):
                self.table.insert("", "end", iid=key, values=values,
                                  tags=("best",) if k == 0 else ())
            self.table.selection_set([key for key, _values, drawn in state if drawn])
        else:
            rows, selected = state
            view = self.list.yview()[0]
            self._plots = [plot for plot, _name in rows]
            self.list.delete(0, "end")
            self.list.insert("end", *[name for _plot, name in rows])
            self.list.yview_moveto(view)
            if selected in self._plots:
                i = self._plots.index(selected)
                self.list.selection_set(i)
                self.list.see(i)

    def _tick(self):
        """Follows the main window: redraws when what it shows (or the language) changed.

        Polling keeps the main window free of calls to windows that may not be open."""
        self.follow()
        self._timer = self.after(POLL_MS, self._tick)

    def follow(self):
        """Redraws now, if what it shows changed."""
        try:
            state = (i18n.get_lang(), self._state())
            if state != self._shown:
                self._shown = state
                self.title(tr(TITLES[self.kind]))
                self._draw(state[1])
        except tk.TclError:  # the main window is being rebuilt (language switch): next time
            pass

    def _close(self):
        """Close button. With the peaks table gone, no peak stays marked on the plot: there
        would be nowhere to see which ones are, or to unmark them."""
        if self.kind == "peaks":
            self.app.tree.selection_set(())
        self.destroy()

    def destroy(self):
        self.after_cancel(self._timer)  # closed: nothing left to follow
        super().destroy()

    # ------------------------------------------------------------- actions
    def _pick_file(self):
        """Click on a file: selects its plot on the board (opening its sheet, if it is on
        another one)."""
        sel = self.list.curselection()
        if sel and self._plots[sel[0]] != self.app._selected:
            self.app.select_chart(*self._plots[sel[0]])

    def _remove_file(self):
        """Delete key: removes the file marked on the list, which is the selected plot's."""
        sel = self.list.curselection()
        if sel:
            self.app._close_chart(*self._plots[sel[0]])

    def _pick_model(self, event):
        """Click on a model: its curve is the one drawn; with Ctrl, it is added to the ones
        drawn, or taken out."""
        view, key = self.app.rheo.view, self.table.identify_row(event.y)
        if view is not None and key and self.table.identify_region(event.x, event.y) == "cell":
            view.select(view.shown() ^ {key} if event.state & 0x4 else {key})
        return "break"

    def _file_menu(self, event):
        """Right click on a file: the menu of the main window ("Remove")."""
        i = self.list.nearest(event.y)
        box = self.list.bbox(i) if i >= 0 else None
        if not box or not box[1] <= event.y < box[1] + box[3]:  # clicked below the last file
            return
        view, key = self._plots[i]
        owner = self.app._owner(view)
        owner._file_clicked = list(owner.files).index(key)
        popup(owner._file_menu_pop, event)

    def _peak_row(self, event) -> str | None:
        """Row of the main window's table for the peak under the mouse."""
        row = self.table.identify_row(event.y)
        if not row or self.table.identify_region(event.x, event.y) != "cell":
            return None
        rows = self.app.tree.get_children()
        k = self.table.index(row)
        return rows[k] if k < len(rows) else None

    def _pick_peak(self, event):
        """Click on a peak: marks it on the plot; on the marked one, unmarks it. With Ctrl, it
        is added to the marked ones, or taken out."""
        row, tree = self._peak_row(event), self.app.tree
        if row is None:
            return "break"
        if event.state & 0x4:
            tree.selection_toggle(row)
        else:
            tree.selection_set(() if tree.selection() == (row,) else (row,))
        return "break"

    def _peak_menu(self, event):
        """Right click on a peak: the menu of the main window ("Calculate area", "Mark as
        noise", "Color")."""
        app, row = self.app, self._peak_row(event)
        if row is None or app._corr is None or app._curve is None:
            return
        if row not in app.tree.selection():
            app.tree.selection_set(row)  # highlights on the plot the peak that will be calculated
        app._area_row = row
        popup(app._area_menu, event)

    def _noise_menu(self, event):
        """Right click on a peak marked as noise: "Restore peak" (it goes back to the table)."""
        row = self.noise.identify_row(event.y)
        if not row:
            return
        k = self.noise.index(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("noise_restore_one"),
                         command=lambda: self.app.restore_noise_mark(k))
        popup(menu, event)

    def _calc_menu(self, event):
        """Right click on a calculation: the menu of the main window ("Copy", "Remove entry")."""
        k = block_at(self.log, self._blocks, event)
        if k is None:
            return
        owner = self._owner()
        owner._log_clicked = k
        popup(owner._log_menu_pop, event)

    def _clear(self):
        if self.of_view == "rheology":
            self.app.rheo._log_render([])
        else:
            self.app.clear_calc_log()


class CalcWindow(ToolWindow):
    """Small window with a single calculation: the one just made. `owner` is the window where
    it was made; `entry`, the calculation as the log keeps it."""

    def __init__(self, owner, entry: dict):
        super().__init__(owner)
        self.owner, self.entry = owner, entry
        self.of_view = "tga"  # only on screen while a TGA plot is the selected one
        box, self.log, _sb = log_box(self.body)
        box.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.bind("<Button-3>", self._menu)
        self.retranslate()
        # at the right edge of the board, each new one a little lower than the ones still open
        owner.update_idletasks()
        plot = owner.board
        n = sum(isinstance(w, CalcWindow) for w in owner.winfo_children()) - 1
        w, h = 300, 150
        x = plot.winfo_rootx() + max(plot.winfo_width() - w - 16, 0) - 26 * (n % 8)
        self.geometry(f"{w}x{h}+{x}+{plot.winfo_rooty() + 16 + 26 * (n % 8)}")

    def paint(self):
        super().paint()
        paint_log(self.log)

    def retranslate(self):
        """Writes the calculation in the current language (also after a language switch)."""
        self.title(tr("calcs"))
        self._lines = self.owner._log_lines(self.entry)
        render_log(self.log, [self._lines])

    def _menu(self, event):
        """Right click: "Copy"."""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("calcs_copy"), command=self._copy)
        popup(menu, event)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append("\n".join(text.strip() for text, _tags in self._lines))


class ColorWindow(ToolWindow):
    """Color picker, to paint a peak. From the top: a color wheel (the hue is the angle, the
    saturation the distance from the center) with the bar of its value at its side, a sample
    of the color and its hex; the ready-made colors; and how opaque the color is ("alpha", 255:
    fully). Each one shows the color the others make. "OK" takes it: `on_pick(color)` gets
    "#rrggbb" or, if it is not opaque, "#rrggbbaa"; "No color" gives None. `color`: the one
    the peak has now. While it is open the clicks are its own, so the peak it was opened for
    is still there when a color is taken.

    The color is kept as hue, saturation, value and alpha, not as the hex shown: a gray or a
    black has no hue of its own, and the mark on the wheel would jump as the value went by."""

    WHEEL = 140        # side of the wheel, in pixels
    BAR = 16           # thickness of the bars of value and alpha
    CELL, GAP = 16, 2  # side of a ready-made color and the space between two

    def __init__(self, owner, color: str | None, on_pick):
        super().__init__(owner)
        self._on_pick = on_pick
        self.title(tr("paint_title"))
        self.h, self.s, self.v, self.a = hex_hsva(color) if color else (0.0, 1.0, 1.0, 255)
        self._imgs = {}        # the pictures on screen: Tk does not hold them
        self._syncing = False  # the fields are being written by the program, not typed in
        self._in_wheel = False
        ink, edge = theme.color("ink"), theme.color("outline")
        # the squares under a transparent color
        self._squares = [tuple(v / 65535 for v in self.winfo_rgb(theme.color(role)))
                         for role in ("paper", "tab")]
        size, bar, step = self.WHEEL, self.BAR, self.CELL + self.GAP
        width = len(PRESETS[0]) * step - self.GAP  # of the content: the ready-made colors'

        def canvas(parent, w, h, cursor, drag=None):
            c = tk.Canvas(parent, width=w, height=h, highlightthickness=0, cursor=cursor)
            c.create_image(0, 0, anchor="nw", tags="img")
            for seq in ("<Button-1>", "<B1-Motion>") if drag else ():
                c.bind(seq, drag)
            return c

        def marks(c):  # a mark that shows on any color: a line of ink inside one of paper
            c.create_rectangle(0, 0, 0, 0, outline=theme.color("paper"), tags=("mark", "out"))
            c.create_rectangle(0, 0, 0, 0, outline=ink, tags=("mark", "in"))

        top = ttk.Frame(self.body)
        top.pack(padx=6, pady=(6, 6))
        self.wheel = canvas(top, size, size, "crosshair")
        self.wheel.pack(side="left")
        self.wheel.bind("<Button-1>", self._wheel_press)
        self.wheel.bind("<B1-Motion>", self._wheel_drag)
        if getattr(owner, "_wheel_img", None) is None:  # drawn once for the program
            owner._wheel_img = self._photo(wheel_picture(size))
        self.wheel.itemconfigure("img", image=owner._wheel_img)
        self.wheel.create_oval(0, 0, 0, 0, outline=theme.color("paper"), tags=("mark", "out"))
        self.wheel.create_oval(0, 0, 0, 0, outline=ink, tags=("mark", "in"))
        self.value = canvas(top, bar, size, "", self._value_drag)
        self.value.pack(side="left", padx=8)
        marks(self.value)
        side = ttk.Frame(top)
        side.pack(side="left", fill="y")
        self._sample_size = (width - size - bar - 16, 44)
        self.sample = canvas(side, *self._sample_size, "")
        self.sample.pack()
        self.sample.create_rectangle(0, 0, self._sample_size[0] - 1, self._sample_size[1] - 1,
                                     outline=edge)
        ttk.Label(side, text=tr("paint_hex")).pack(anchor="w", pady=(8, 0))
        self._hex = tk.StringVar()
        entry = ttk.Entry(side, textvariable=self._hex, width=9, font=("Consolas", 9))
        entry.pack(anchor="w")

        rows = len(PRESETS)
        grid = tk.Canvas(self.body, width=width, height=rows * step - self.GAP,
                         highlightthickness=0, cursor="hand2")
        grid.pack(padx=6)
        for r, row in enumerate(PRESETS):
            for c, value in enumerate(row):
                on = color is not None and value == color[:7]  # the color the peak has
                item = grid.create_rectangle(
                    c * step, r * step, c * step + self.CELL - 1, r * step + self.CELL - 1,
                    fill=value, outline=ink if on else edge, width=2 if on else 1)
                grid.tag_bind(item, "<Button-1>", lambda _e, v=value: self._preset(v))

        ttk.Label(self.body, text=tr("paint_alpha")).pack(anchor="w", padx=6, pady=(8, 0))
        line = ttk.Frame(self.body)
        line.pack(fill="x", padx=6)
        self._alpha_w = width - 44
        self.alpha = canvas(line, self._alpha_w, bar, "", self._alpha_drag)
        self.alpha.pack(side="left")
        marks(self.alpha)
        self._alpha = tk.StringVar()
        alpha = ttk.Entry(line, textvariable=self._alpha, width=4, justify="right")
        alpha.pack(side="right")

        line = ttk.Frame(self.body)
        line.pack(fill="x", padx=6, pady=(8, 6))
        ttk.Button(line, text="OK", width=6, style="Small.TButton", takefocus=False,
                   command=self._ok).pack(side="left")
        ttk.Button(line, text=tr("paint_none"), style="Small.TButton", takefocus=False,
                   command=lambda: self._take(None)).pack(side="right")

        self._hex.trace_add("write", lambda *_a: self._hex_typed())
        self._alpha.trace_add("write", lambda *_a: self._alpha_typed())
        entry.bind("<Return>", lambda _e: self._ok())
        # the number as it was taken (300 is 255), once the typing is over
        for seq in ("<Return>", "<FocusOut>"):
            alpha.bind(seq, lambda _e: self._show())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._show()
        self.update_idletasks()
        w, h = width + 12 + 2 * self.RIM + 2, self.fit_height()
        x, y = owner.winfo_pointerxy()
        self.geometry(f"{w}x{h}+{max(x - w // 2, 0)}+{max(y - h // 2, 0)}")
        self.update()
        self.grab_set()
        entry.focus_force()
        entry.icursor("end")

    def _photo(self, picture) -> tk.PhotoImage:
        return tk.PhotoImage(master=self, data=base64.b64encode(png(picture)))

    def _picture(self, c: tk.Canvas, picture):
        self._imgs[c] = self._photo(picture)
        c.itemconfigure("img", image=self._imgs[c])

    def _show(self, typed: str = ""):
        """Every part shows the color as it is now, but the field `typed` ("hex" or "alpha"):
        what is being typed there is the user's to finish."""
        size, bar = self.WHEEL, self.BAR
        rgb = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        x, y = wheel_xy(self.h, self.s, size)
        self.wheel.coords("out", x - 5, y - 5, x + 5, y + 5)
        self.wheel.coords("in", x - 4, y - 4, x + 4, y + 4)
        # value: from the color of the wheel, at the top, down to black
        self._picture(self.value, over(np.zeros((size, bar, 3)),
                                       colorsys.hsv_to_rgb(self.h, self.s, 1),
                                       np.tile(np.linspace(1, 0, size)[:, None], (1, bar))))
        y = round((1 - self.v) * (size - 1))
        self.value.coords("out", 0, y - 3, bar - 1, y + 3)
        self.value.coords("in", 1, y - 2, bar - 2, y + 2)
        w, h = self._sample_size
        self._picture(self.sample, over(checker(w, h, *self._squares), rgb,
                                        np.full((h, w), self.a / 255)))
        # alpha: from the bare squares, at the left, to the opaque color
        w = self._alpha_w
        self._picture(self.alpha, over(checker(w, bar, *self._squares), rgb,
                                       np.tile(np.linspace(0, 1, w), (bar, 1))))
        x = round(self.a / 255 * (w - 1))
        self.alpha.coords("out", x - 3, 0, x + 3, bar - 1)
        self.alpha.coords("in", x - 2, 1, x + 2, bar - 2)
        self._syncing = True
        try:
            if typed != "hex":
                self._hex.set(hsva_hex(self.h, self.s, self.v, self.a))
            if typed != "alpha":
                self._alpha.set(str(self.a))
        finally:
            self._syncing = False

    # ------------------------------------------------------------- actions
    def _wheel_press(self, event):
        self._in_wheel = wheel_point(event.x, event.y, self.WHEEL) is not None
        self._wheel_drag(event)

    def _wheel_drag(self, event):
        if self._in_wheel:  # a drag that started on the wheel goes on along its rim
            self.h, self.s = wheel_point(event.x, event.y, self.WHEEL, clamp=True)
            self._show()

    def _value_drag(self, event):
        self.v = 1 - min(max(event.y / (self.WHEEL - 1), 0), 1)
        self._show()

    def _alpha_drag(self, event):
        self.a = round(255 * min(max(event.x / (self._alpha_w - 1), 0), 1))
        self._show()

    def _preset(self, value: str):
        self.h, self.s, self.v = hex_hsva(value)[:3]  # how opaque it is stays as chosen
        self._show()

    def _hex_typed(self):
        color = None if self._syncing else hex_color(self._hex.get())
        if color is not None:
            self.h, self.s, self.v, self.a = hex_hsva(color)
            self._show(typed="hex")

    def _alpha_typed(self):
        text = self._alpha.get().strip()
        if not self._syncing and text.isdigit():
            self.a = min(int(text), 255)
            self._show(typed="alpha")

    def _ok(self):
        # the field follows every other part: what it holds is the color, unless it was left
        # half typed
        color = hex_color(self._hex.get())
        if color is None:
            self.bell()
        else:
            self._take(color)

    def _take(self, color: str | None):
        self.destroy()
        self._on_pick(color)


def palette_view(app) -> str:
    """Analysis of the selected plot, which a tool opened now is about."""
    return "rheology" if app.rheo_var.get() else "tga"


def open_palette(app, kind: str) -> Palette:
    """Opens the floating window of a tool for the analysis of the selected plot, or brings
    the one already open to the front. Each analysis has its own, except for the files: one
    window lists the plots of both."""
    view = None if kind == "files" else palette_view(app)
    for w in app.winfo_children():
        if isinstance(w, Palette) and w.kind == kind and w.of_view == view:
            w.deiconify()
            w.lift()
            return w
    return Palette(app, kind)
