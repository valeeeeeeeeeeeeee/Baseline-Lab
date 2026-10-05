"""Floating tool windows ("Tools" menu): calculations, peaks and files, each in a small resizable
window that stays over the plot, at its side, like the palettes of an image editor.

The main window has no side columns: these windows are where its lists are seen and used. They
show the lists of the view that is open (kept by the main window) and follow them as they change;
clicks on them act on the main window, with its own menus.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import i18n
from .i18n import tr
from .widgets import ToolWindow, block_at, log_box, render_log

TITLES = {"calcs": "tool_calc", "peaks": "tool_peaks", "files": "tool_files"}
SIZE = (300, 340)   # initial size, in pixels
POLL_MS = 250


def popup(menu: tk.Menu, event):
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


class Palette(ToolWindow):
    """One floating tool window of the main window `app`. `kind`: "calcs", "peaks" or "files"."""

    def __init__(self, app, kind: str):
        super().__init__(app)
        self.app, self.kind = app, kind
        # it belongs to the view open when it was created: it shows that view's lists and is
        # only on screen while that view is the open one
        self.of_view = "rheology" if app.rheo_var.get() else "baseline"
        self._place()
        self._shown = None  # what is drawn: redrawn only when it changes
        build = {"calcs": self._build_calcs, "peaks": self._build_peaks,
                 "files": self._build_files}[kind]
        build()
        self._tick()

    def _place(self):
        """At the right edge of the plot, a little lower for each kind so they do not hide
        one another."""
        app = self.app
        app.update_idletasks()
        plot = app.rheo.view if self.of_view == "rheology" else app.plot_frame
        w, h = SIZE
        step = 34 * list(TITLES).index(self.kind)
        x = plot.winfo_rootx() + max(plot.winfo_width() - w - 16, 0) - step
        y = plot.winfo_rooty() + 16 + step
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _owner(self):
        """Who keeps the lists of its view: the rheology panel or the main window."""
        return self.app.rheo if self.of_view == "rheology" else self.app

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
        self.stats = ttk.Label(self.body, justify="left", foreground="#555")
        self.stats.pack(anchor="w", padx=6)
        self.bind("<Configure>", self._wrap, add="+")
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

    def _wrap(self, event):
        if event.widget is self:  # the text above the table follows the width of the window
            for label in (self.summary, self.stats):
                label.configure(wraplength=max(event.width - 16, 60))

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
        self.list.bind("<Delete>", lambda _e: self._owner().remove_file())

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
        src = self._owner().listbox
        return src.get(0, "end"), src.curselection()

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
        else:
            names, sel = state
            self.list.delete(0, "end")
            self.list.insert("end", *names)
            for i in sel:
                self.list.selection_set(i)
                self.list.see(i)

    def _tick(self):
        """Follows the main window: redraws when what it shows (or the language) changed.

        Polling keeps the main window free of calls to windows that may not be open."""
        try:
            state = (i18n.get_lang(), self._state())
            if state != self._shown:
                self._shown = state
                self.title(tr(TITLES[self.kind]))
                self._draw(state[1])
        except tk.TclError:  # the main window is being rebuilt (language switch): next time
            pass
        self._timer = self.after(POLL_MS, self._tick)

    def destroy(self):
        self.after_cancel(self._timer)  # closed: nothing left to follow
        super().destroy()

    # ------------------------------------------------------------- actions
    def _pick_file(self):
        """Click on a file: opens it in the main window."""
        sel = self.list.curselection()
        src = self._owner().listbox
        if not sel or src.curselection() == sel:
            return
        src.selection_clear(0, "end")
        src.selection_set(sel[0])
        src.see(sel[0])
        src.event_generate("<<ListboxSelect>>")

    def _file_menu(self, event):
        """Right click on a file: the menu of the main window ("Open in a new window", "Remove")."""
        i = self.list.nearest(event.y)
        box = self.list.bbox(i) if i >= 0 else None
        if not box or not box[1] <= event.y < box[1] + box[3]:  # clicked below the last file
            return
        owner = self._owner()
        owner._file_clicked = i
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
        """Click on a peak: marks it on the plot; on the marked one, unmarks it."""
        row, tree = self._peak_row(event), self.app.tree
        if row is not None:
            tree.selection_set(() if row in tree.selection() else (row,))
        return "break"

    def _peak_menu(self, event):
        """Right click on a peak: the menu of the main window ("Calculate area", "Mark as noise")."""
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
        self.of_view = "baseline"  # only on screen while the baseline is the open view
        box, self.log, _sb = log_box(self.body)
        box.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.bind("<Button-3>", self._menu)
        self.retranslate()
        # at the right edge of the plot, each new one a little lower than the ones still open
        owner.update_idletasks()
        plot = owner.plot_frame
        n = sum(isinstance(w, CalcWindow) for w in owner.winfo_children()) - 1
        w, h = 300, 150
        x = plot.winfo_rootx() + max(plot.winfo_width() - w - 16, 0) - 26 * (n % 8)
        self.geometry(f"{w}x{h}+{x}+{plot.winfo_rooty() + 16 + 26 * (n % 8)}")

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


def open_palette(app, kind: str) -> Palette:
    """Opens the floating window of a tool for the view that is open, or brings the one
    already open to the front. Each view has its own."""
    view = "rheology" if app.rheo_var.get() else "baseline"
    for w in app.winfo_children():
        if isinstance(w, Palette) and w.kind == kind and w.of_view == view:
            w.deiconify()
            w.lift()
            return w
    return Palette(app, kind)
