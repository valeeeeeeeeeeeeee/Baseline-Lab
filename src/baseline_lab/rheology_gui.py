"""Rheology view: the flow curve of a file, the fitted models and the one that best describes it.

Flow: Import a .txt or .xlsx with shear rate (X) and shear stress (Y) -> the models are fitted
automatically -> the plot shows the curves and the left panel names the best model.
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.figure import Figure

from . import rheology as rh
from .i18n import tr
from .io_txt import DataFile, read_file
from .widgets import PlotCanvas, block_at, log_box, render_log, show_image

MODEL_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]


def guess_columns(columns: list[str]) -> tuple[int, int]:
    """Guess of (shear rate, shear stress) from the column names; default: the first two."""
    low = [c.lower() for c in columns]
    xi = next((i for i, c in enumerate(low) if re.search(r"rate|taxa|1/s|s\^?-1|s⁻¹", c)), 0)
    yi = next((i for i, c in enumerate(low) if i != xi and re.search(r"stress|tens", c)), None)
    if yi is None:
        yi = 1 if xi == 0 else 0
    return xi, min(yi, len(columns) - 1)


def axis_label(quantity: str, column: str) -> str:
    """Axis title in the current language: the quantity, with the unit the column name gives
    in brackets, if any ("Shear rate (1/s)" -> "Taxa de cisalhamento (1/s)")."""
    unit = re.search(r"[\(\[]\s*([^()\[\]]+?)\s*[\)\]]\s*$", column)
    return f"{quantity} ({unit.group(1)})" if unit else quantity


def format_params(fit: rh.Fit) -> str:
    return "   ".join(f"{s} = {v:.4g}" for s, v in zip(fit.model.symbols, fit.params))


class FlowCurveView(ttk.Frame):
    """Plot of a flow curve with the fitted models and, under it, the table of the models."""

    def __init__(self, parent):
        super().__init__(parent)
        # table of all fitted models, under the plot (best one first)
        # white around it, like the plot: only the left column of the view is gray
        table = tk.Frame(self, background="white")
        table.pack(side="bottom", fill="x")
        frame = tk.Frame(table, borderwidth=1, relief="solid")
        frame.pack(fill="x", padx=8, pady=8)
        self.tree = ttk.Treeview(frame, columns=("model", "eq", "params", "r2", "rmse", "aicc"),
                                 show="headings", height=len(rh.MODELS), selectmode="none")
        for c, text, width, anchor in (("model", tr("rheo_col_model"), 210, "w"),
                                       ("eq", tr("rheo_col_eq"), 150, "w"),
                                       ("params", tr("rheo_col_params"), 280, "w"),
                                       ("r2", "R²", 80, "e"), ("rmse", "RMSE", 90, "e"),
                                       ("aicc", "AICc", 90, "e")):
            self.tree.heading(c, text=text, anchor=anchor)
            self.tree.column(c, width=width, anchor=anchor, stretch=c in ("model", "params"))
        self.tree.tag_configure("best", font=("Segoe UI", 9, "bold"))
        # read-only table: no clicking on rows or titles and no dragging of the column edges
        for seq in ("<Button-1>", "<Double-Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
                    "<Motion>"):
            self.tree.bind(seq, lambda _e: "break")
        self.tree.configure(takefocus=False)
        self.tree.pack(fill="x")

        self.fig = Figure(figsize=(8, 6), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = PlotCanvas(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("pick_event", self._on_pick)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self._plot_menu = tk.Menu(self, tearoff=0)
        self._plot_menu.add_command(label=tr("open_image"), command=self.open_image)
        self._plot_menu.add_command(label=tr("save_image"), command=self.save_image)
        self._df = None                     # file drawn
        self._hidden: set[str] = set()      # curves hidden by clicking on the legend
        self._curves: dict[str, tuple] = {}  # {curve: (line, its legend line, its legend text)}
    def _on_pick(self, event):
        """Click on a legend entry: hides its curve, or shows it again."""
        key = next((k for k, arts in self._curves.items() if event.artist in arts[1:]), None)
        if key is None or event.mouseevent.button != 1:
            return
        self._hidden ^= {key}
        self._apply_hidden()
        self.canvas.draw_idle()

    def _on_click(self, event):
        """Right click on the plot: "Open image", "Download image"."""
        if event.button != 3 or self._df is None:
            return
        # the matplotlib event has no screen position: use the pointer's
        x_root, y_root = self.winfo_pointerxy()
        try:
            self._plot_menu.tk_popup(x_root, y_root)
        finally:
            self._plot_menu.grab_release()

    def open_image(self):
        """Picture of the plot, as it appears on screen (hidden curves stay hidden), in a window
        only to look at."""
        if self._df is None:
            return
        from .gui import set_icon  # here: gui imports this module
        top = show_image(self.winfo_toplevel(), self.fig,
                         f"{self._df.name} – {tr('rheo_flow_title')}")
        set_icon(top)
        top.of_view = "rheology"  # only on screen while this is the open view
        self.canvas.draw()  # the figure was drawn for the picture: back to the screen's

    def save_image(self):
        """Saves the plot as PNG, as it appears on screen (hidden curves stay hidden)."""
        df = self._df
        if df is None:
            return
        top = self.winfo_toplevel()
        path = filedialog.asksaveasfilename(
            parent=top, defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile=f"{df.path.stem}_{tr('fname_rheology')}.png", initialdir=str(df.path.parent))
        if not path:
            return
        self.fig.savefig(path, dpi=200)
        # saving leaves the figure drawn at the file's resolution: until it is drawn again for
        # the screen, clicks on the legend miss its entries
        self.canvas.draw()
        messagebox.showinfo(tr("save_image"), tr("image_saved", path=path), parent=top)

    def _apply_hidden(self):
        for key, (line, handle, text) in self._curves.items():
            shown = key not in self._hidden
            line.set_visible(shown)
            for art in (handle, text):  # a hidden curve stays in the legend, faded
                art.set_alpha(1.0 if shown else 0.3)
        self.ax.relim(visible_only=True)  # the axes fit what is left on the plot
        self.ax.autoscale_view()

    def show(self, df: DataFile | None, xname: str = "", yname: str = "",
             log: bool = False) -> tuple[list[rh.Fit], str]:
        """Fits and draws the flow curve of `df` (None: nothing, no empty axes).

        Returns the fits, from the best to the worst, and the error message if there are none."""
        self.tree.delete(*self.tree.get_children())
        ax = self.ax
        ax.set_xscale("linear")  # clearing a log axis warns about its default limits (0, 1)
        ax.set_yscale("linear")
        ax.clear()
        ax.set_visible(df is not None)
        if df is not self._df:  # another file: every curve is shown again
            self._df, self._hidden = df, set()
        self._curves = {}
        if df is None:
            self.canvas.draw_idle()
            return [], ""
        x, y = rh.clean(*df.xy(df.columns.index(xname), df.columns.index(yname)))
        lines = {"data": ax.plot(x, y, "^", color="black", ms=6, mew=0, zorder=4,
                                 label=tr("rheo_measured"))[0]}
        fits, error = [], ""
        try:
            fits = rh.fit_all(x, y)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        if fits:  # curves of all models (the best one in black) and the table
            xs = (np.geomspace if log else np.linspace)(x[0], x[-1], 400)
            for i, f in enumerate(fits):
                best = i == 0
                lines[f.model.key] = ax.plot(
                    xs, f.predict(xs), color="black" if best else MODEL_COLORS[i - 1],
                    lw=2.2 if best else 1.6, ls="-" if best else "--",
                    zorder=3 if best else 2, label=f"{f.model.name} (R² = {f.r2:.4f})")[0]
                self.tree.insert("", "end", tags=("best",) if best else (),
                                 values=(f.model.name, f.model.equation, format_params(f),
                                         f"{f.r2:.5f}", f"{f.rmse:.4g}", f"{f.aicc:.2f}"))
        ax.set_xscale("log" if log else "linear")
        ax.set_yscale("log" if log else "linear")
        # fixed corner: the legend must not move from under the mouse when a curve is hidden
        leg = ax.legend(loc="upper left", fontsize=8)
        for (key, line), handle, text in zip(lines.items(), leg.legend_handles, leg.get_texts()):
            handle.set_picker(6)  # clickable: hides / shows the curve
            text.set_picker(True)
            self._curves[key] = (line, handle, text)
        self._hidden &= set(lines)
        self._apply_hidden()
        ax.set_title(f"{df.name} – {tr('rheo_flow_title')}", fontsize=10)
        # the axes are named by what they are, not by the file's column titles: these stay in
        # the language of whoever exported the file
        ax.set_xlabel(axis_label(tr("rheo_axis_x"), xname))
        ax.set_ylabel(axis_label(tr("rheo_axis_y"), yname))
        self.canvas.draw_idle()
        return fits, error


class RheologyPanel(ttk.Frame):
    def __init__(self, parent, opts, state: dict | None = None):
        """`opts`: where the options go (the body of the "Adjustment" tool window)."""
        super().__init__(parent)
        self._opts = opts
        st = state or {}
        self.files: dict[str, DataFile] = dict(st.get("files", {}))
        # each file keeps its own columns: {file: (X, Y)}
        self._cols: dict[str, tuple[str, str]] = dict(st.get("cols", {}))
        self._cur_key: str | None = None    # file whose columns are in the controls
        self._build_ui(st)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, st: dict):
        # left column (files, results, calculations): never shown, it only holds the lists that
        # the tools of the top bar show
        self._left = left = ttk.Frame(self, padding=8)
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

        # options: the "Adjustment" tool, a floating window like the other tools
        opts = ttk.Frame(self._opts, padding=8)
        opts.pack(fill="both", expand=True)
        ttk.Label(opts, text=tr("data"), style="Title.TLabel").pack(anchor="w")
        self.x_cb = self._combo(opts, tr("rheo_col_x"))
        self.y_cb = self._combo(opts, tr("rheo_col_y"))
        self.log_var = tk.BooleanVar(value=st.get("log", False))
        ttk.Checkbutton(opts, text=tr("rheo_log"), variable=self.log_var,
                        command=self.refresh).pack(anchor="w", pady=(6, 0))

        ttk.Label(left, text=tr("rheo_best"), style="Title.TLabel").pack(anchor="w", pady=(12, 2))
        self.best_lbl = ttk.Label(left, wraplength=280, justify="left",
                                  font=("Segoe UI", 12, "bold"))
        self.best_lbl.pack(anchor="w")
        self.eq_lbl = ttk.Label(left, wraplength=280, justify="left", font=("Segoe UI", 10))
        self.eq_lbl.pack(anchor="w", pady=(2, 0))

        # the table under the plot again, as a log like the baseline's: takes the rest of the column
        calc_head = ttk.Frame(left)
        calc_head.pack(fill="x", pady=(12, 2))
        ttk.Label(calc_head, text=tr("calcs"), style="Title.TLabel").pack(side="left")
        clear_btn = ttk.Button(calc_head, text=tr("calcs_clear"), style="Small.Toolbutton",
                               takefocus=False, command=lambda: self._log_render([]))
        clear_btn.pack(side="right")
        box, self.calc_log, sb = log_box(left)
        box.pack(fill="both", expand=True)
        self.calc_log.bind("<Button-3>", self._log_menu)
        self._log_menu_pop = tk.Menu(self, tearoff=0)
        self._log_menu_pop.add_command(label=tr("calcs_copy"), command=self.copy_log_entry)
        self.log_blocks: list[list[tuple[str, tuple]]] = []  # the models shown in the log
        self._log_clicked = None          # model under the right click
        # the button ends at the edge of the box, not of its scrollbar
        clear_btn.pack_configure(padx=(0, sb.winfo_reqwidth()))

        self.view = FlowCurveView(self)
        self.view.pack(side="left", fill="both", expand=True)

        # start screen: a big button in the middle of the plot
        self.empty = ttk.Frame(self.view, padding=24, relief="groove")
        ttk.Label(self.empty, text=tr("rheo_empty_title"), font=("Segoe UI", 12)).pack(pady=(0, 12))
        ttk.Button(self.empty, text=tr("import_btn"), style="Big.TButton",
                   command=self.open_files).pack()
        ttk.Label(self.empty, foreground="#666", justify="center",
                  text=tr("rheo_empty_hint")).pack(pady=(12, 0))

        for df in self.files.values():
            self.listbox.insert("end", df.name)
        sel = st.get("sel")
        if sel and sel[0] < len(self.files):
            self.listbox.selection_set(sel[0])
            self.listbox.see(sel[0])
        self.on_file_change()

    def _combo(self, parent, label: str):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(2, 0))
        cb = ttk.Combobox(parent, state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        return cb

    def set_side_width(self, width: int):
        """Fixes the width of the left column (the same as the baseline view's)."""
        self._left.pack_propagate(False)
        self._left.configure(width=width)

    def snapshot(self) -> dict:
        """Everything the user loaded and chose, to rebuild the view in another language."""
        return {"files": self.files, "cols": self._cols, "sel": self.listbox.curselection(),
                "log": self.log_var.get()}

    # ------------------------------------------------------------ files
    def open_files(self):
        paths = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(), title=tr("open_title"),
            filetypes=[(tr("ft_text"), "*.txt *.dat *.csv *.xlsx"), (tr("ft_all"), "*.*")])
        if paths:
            self.load_files(paths)

    def load_files(self, paths):
        errors, first_new = [], None
        for p in paths:
            key = str(Path(p).resolve())
            if key in self.files:  # reopening does not duplicate
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
            messagebox.showwarning(tr("read_fail"), "\n".join(errors), parent=self.winfo_toplevel())
        if first_new is not None:  # show the first newly imported file
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(first_new)
            self.listbox.see(first_new)
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

    def _columns_of(self, key: str) -> tuple[str, str]:
        """(X, Y) of a file: the columns chosen for it, or the guess if it was never changed."""
        df = self.files[key]
        xi, yi = guess_columns(df.columns)
        return self._cols.get(key, (df.columns[xi], df.columns[yi]))

    def remove_file(self, i: int | None = None):
        """Removes file `i` from the list (default: the open one); the open file stays open."""
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
        self._cols.pop(key, None)  # reopening the file starts from scratch
        if key == self._cur_key:
            self._cur_key = None
        self.listbox.delete(i)
        if self.files and was_open:
            self.listbox.selection_set(min(i, len(self.files) - 1))
        self.on_file_change()

    def current(self) -> DataFile | None:
        sel = self.listbox.curselection()
        return list(self.files.values())[sel[0]] if sel else None

    def on_file_change(self):
        """File switch: puts the columns of the chosen file in the controls and fits it."""
        sel = self.listbox.curselection()
        key = list(self.files)[sel[0]] if sel else None
        if key is not None and key == self._cur_key:
            return  # clicked on the file that is already open
        self._cur_key = key
        df = self.current()
        if df is None:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
            for cb in (self.x_cb, self.y_cb):
                cb["values"] = ()
                cb.set("")
        else:
            self.empty.place_forget()
            for cb, name in zip((self.x_cb, self.y_cb), self._columns_of(key)):
                cb["values"] = df.columns
                cb.set(name)
        self.refresh()

    def on_columns_change(self):
        if self._cur_key is not None:
            self._cols[self._cur_key] = (self.x_cb.get(), self.y_cb.get())
        self.refresh()

    # ------------------------------------------------------------- plot
    def _log_menu(self, event):
        """Right click on a model of the log: "Copy"."""
        self._log_clicked = block_at(self.calc_log, self.log_blocks, event)
        if self._log_clicked is None:
            return
        try:
            self._log_menu_pop.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu_pop.grab_release()

    def copy_log_entry(self):
        """Puts the whole text of the clicked model on the clipboard."""
        if self._log_clicked is None or self._log_clicked >= len(self.log_blocks):
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(text for text, _tags in self.log_blocks[self._log_clicked]))

    def _log_render(self, fits: list[rh.Fit]):
        """Redraws the log (read-only): one block per model, from the best (in bold) to the worst,
        with what its row of the table under the plot shows."""
        self.log_blocks = [
            [(f.model.name, ("head",) if k == 0 else ()), (f.model.equation, ()),
             (format_params(f), ()),
             (f"R² = {f.r2:.5f}   RMSE = {f.rmse:.4g}   AICc = {f.aicc:.2f}", ())]
            for k, f in enumerate(fits)]
        render_log(self.calc_log, self.log_blocks)

    def refresh(self):
        fits, error = self.view.show(self.current(), self.x_cb.get(), self.y_cb.get(),
                                     self.log_var.get())
        self._log_render(fits)
        self.best_lbl.config(text=fits[0].model.name if fits else "")
        self.eq_lbl.config(text=f"⚠ {error}" if error else "")
        if fits:
            best = fits[0]
            self.eq_lbl.config(text=f"{best.model.equation}\n{format_params(best)}\n"
                                    f"R² = {best.r2:.5f}   RMSE = {best.rmse:.4g}")
