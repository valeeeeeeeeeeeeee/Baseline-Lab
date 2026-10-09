"""Rheology analysis: the flow curve of a file, the fitted models and the one that best describes it.

Flow: a .txt or .xlsx with shear rate (X) and shear stress (Y) is opened as a plot on the board
-> the models are fitted automatically -> the plot shows the curve of the best one, and the
"Models" tool lists them all.
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
from .widgets import (CAN_COPY_IMAGE, Layout, PlotCanvas, block_at, copy_figure, file_key, file_title,
                      log_box, render_log, show_image)

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
    """Plot of a flow curve with the fitted models. The table of the models is the "Models"
    tool: it shows `rows()` and chooses the curves drawn with `select()`."""

    def __init__(self, parent):
        super().__init__(parent)
        self.fig = Figure(figsize=(8, 6), layout=Layout())
        self.ax = self.fig.add_subplot(111)
        self.canvas = PlotCanvas(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("pick_event", self._on_pick)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self._plot_menu = tk.Menu(self, tearoff=0)
        self._plot_menu.add_command(label=tr("open_image"), command=self.open_image)
        self._plot_menu.add_command(label=tr("save_image"), command=self.save_image)
        if CAN_COPY_IMAGE:
            self._plot_menu.add_command(label=tr("copy_image"), command=self.copy_image)
        self.winfo_toplevel().add_tools(self._plot_menu, "rheology")
        self._df = None                    # file drawn
        self._hidden: set[str] = set()      # curves hidden by clicking on the legend
        self._curves: dict[str, tuple] = {}  # {curve: (line, its legend line, its legend text)}
        self._fits: list[rh.Fit] = []       # models fitted to the file drawn, best one first
        self._shown: set[str] = set()       # models whose curve is drawn (rows chosen on the table)
        self._names = ("", "")              # columns (X, Y) of the fit
        self._plot = None                   # (x, y, log) of the fit

    def rows(self) -> list[tuple[str, tuple, bool]]:
        """The fitted models, best one first: (model, the texts of its row of the table,
        whether its curve is drawn)."""
        return [(f.model.key, (f.model.name, f.model.equation, format_params(f), f"{f.r2:.5f}",
                               f"{f.rmse:.4g}", f"{f.aicc:.2f}"), f.model.key in self._shown)
                for f in self._fits]

    def shown(self) -> set[str]:
        return set(self._shown)

    def select(self, keys):
        """Draws the curves of the models `keys`, and only them."""
        self._shown = set(keys) & {f.model.key for f in self._fits}
        self._hidden -= self._shown  # choosing a curve hidden on the legend shows it again
        self._draw()

    def _on_pick(self, event):
        """Click on a legend entry: hides its curve, or shows it again."""
        key = next((k for k, arts in self._curves.items() if event.artist in arts[1:]), None)
        if key is None or event.mouseevent.button != 1:
            return
        self._hidden ^= {key}
        self._apply_hidden()
        self.canvas.draw_idle()

    def _on_click(self, event):
        """Right click on the plot: "Open image", "Download image", "Copy image"."""
        if event.button != 3 or self._df is None:
            return
        # the matplotlib event has no screen position: use the pointer's
        x_root, y_root = self.winfo_pointerxy()
        try:
            self._plot_menu.tk_popup(x_root, y_root)
        finally:
            self._plot_menu.grab_release()

    def _note(self):
        """Adds to the plot what a picture of it carries, as it goes without the table of the
        models: in the lower right corner, the equation and the result of each model drawn
        (named if there are several). Returns the text, to be removed once the picture is made."""
        fits = [f for f in self._fits if f.model.key in self._shown - self._hidden]
        blocks = [([f.model.name] if len(fits) > 1 else [])
                  + [f.model.equation, format_params(f),
                     f"R² = {f.r2:.5f}   RMSE = {f.rmse:.4g}"] for f in fits]
        return self.ax.text(
            0.98, 0.03, "\n\n".join("\n".join(b) for b in blocks), transform=self.ax.transAxes,
            ha="right", va="bottom", multialignment="left", fontsize=9, zorder=5, visible=bool(fits),
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "white", "edgecolor": "none"})

    def copy_image(self):
        """Puts on the clipboard the picture that "Open image" shows."""
        if self._df is None:
            return
        note = self._note()
        try:
            copy_figure(self.fig)
        except OSError as exc:
            messagebox.showerror(tr("copy_image"), str(exc), parent=self.winfo_toplevel())
        finally:
            note.remove()
            self.canvas.draw()  # the figure was drawn for the picture: back to the screen's

    def open_image(self):
        """Picture of the plot, as it appears on screen (hidden curves stay hidden), in a window
        only to look at."""
        if self._df is None:
            return
        from .gui import set_icon  # here: gui imports this module
        note = self._note()
        try:
            top = show_image(self.winfo_toplevel(), self.fig,
                             f"{self._df.name} – {tr('rheo_flow_title')}")
        finally:
            note.remove()
        set_icon(top)
        top.of_view = "rheology"  # only on screen while a plot of its analysis is selected
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
        new = df is not self._df or (xname, yname) != self._names
        if df is not self._df:  # another file: nothing is hidden any more
            self._df, self._hidden = df, set()
        self._names, self._fits, self._plot = (xname, yname), [], None
        if df is None:
            self._draw()
            return [], ""
        x, y = rh.clean(*df.xy(df.columns.index(xname), df.columns.index(yname)))
        error = ""
        try:
            self._fits = rh.fit_all(x, y)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        if new:  # a new fit starts with the curve of its best model only
            self._shown = {f.model.key for f in self._fits[:1]}
        self._shown &= {f.model.key for f in self._fits}
        self._plot = (x, y, log)
        self._draw()
        return self._fits, error

    def _draw(self):
        """Draws the measured points and the curves of the models chosen on the table."""
        ax = self.ax
        ax.set_xscale("linear")  # clearing a log axis warns about its default limits (0, 1)
        ax.set_yscale("linear")
        ax.clear()
        ax.set_visible(self._plot is not None)
        self._curves = {}
        if self._plot is None:
            self.canvas.draw_idle()
            return
        df, (xname, yname), (x, y, log) = self._df, self._names, self._plot
        for spine in ax.spines.values():
            spine.set_linewidth(1.2)
        ax.tick_params(width=1.2)
        # hollow triangles: the curve stays visible through the points
        lines = {"data": ax.plot(x, y, "^", color="black", ms=7.5, mfc="none", mew=1.2, zorder=4,
                                 label=tr("rheo_measured"))[0]}
        xs = (np.geomspace if log else np.linspace)(x[0], x[-1], 400)
        # each model keeps its look whatever is drawn with it (the best one in black)
        for i, f in enumerate(self._fits):
            if f.model.key not in self._shown:
                continue
            best = i == 0
            lines[f.model.key] = ax.plot(
                xs, f.predict(xs), color="black" if best else MODEL_COLORS[i - 1],
                lw=2.6 if best else 2.0, ls="-" if best else "--",
                zorder=3 if best else 2, label=f"{f.model.name} (R² = {f.r2:.4f})")[0]
        ax.set_xscale("log" if log else "linear")
        ax.set_yscale("log" if log else "linear")
        # fixed corner: the legend must not move from under the mouse when a curve is hidden
        leg = ax.legend(loc="upper left", fontsize=8, edgecolor="none")
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


class RheologyPanel(ttk.Frame):
    """The rheology analysis of the main window `app`: its files, each one a plot on the board,
    and the lists the tools show. It is never on screen itself."""

    def __init__(self, app, opts, state: dict | None = None):
        """`opts`: where the options go (the body of the "Adjustment" tool window)."""
        super().__init__(app)
        self._app, self._opts = app, opts
        st = state or {}
        self.files: dict[str, DataFile] = dict(st.get("files", {}))
        # each file keeps its own columns, {file: (X, Y)}, and its own kind of axes
        self._cols: dict[str, tuple[str, str]] = dict(st.get("cols", {}))
        self._logs: dict[str, bool] = dict(st.get("logs", {}))
        self._views: dict[str, FlowCurveView] = {}  # {file: its plot, on the board}
        self.view: FlowCurveView | None = None      # the plot of the file in the controls
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
        self.log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=tr("rheo_log"), variable=self.log_var,
                        command=self.on_log_change).pack(anchor="w", pady=(6, 0))

        ttk.Label(left, text=tr("rheo_best"), style="Title.TLabel").pack(anchor="w", pady=(12, 2))
        self.best_lbl = ttk.Label(left, wraplength=280, justify="left",
                                  font=("Segoe UI", 12, "bold"))
        self.best_lbl.pack(anchor="w")
        self.eq_lbl = ttk.Label(left, wraplength=280, justify="left", font=("Segoe UI", 10))
        self.eq_lbl.pack(anchor="w", pady=(2, 0))

        # the table of the models again, as a log like the baseline's: takes the rest of the column
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

        # each plot is drawn as its file goes through the controls: the open one last
        shown, cur = st.get("shown", {}), st.get("cur")
        for key, df in self.files.items():
            self.listbox.insert("end", file_title(key, df.name))
            self._new_plot(key, file_title(key, df.name))
        for key in [k for k in self.files if k != cur] + [k for k in self.files if k == cur]:
            self.show_file(key)
            if key in shown:  # the fit above started from the best model again
                self.view.select(shown[key])
        if not self.files:
            self.on_file_change()

    def _combo(self, parent, label: str):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(2, 0))
        cb = ttk.Combobox(parent, state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda _e: self.on_columns_change())
        return cb

    def snapshot(self) -> dict:
        """Everything the user loaded and chose, to rebuild the analysis in another language."""
        return {"files": self.files, "cols": self._cols, "logs": self._logs,
                "cur": self._cur_key, "shown": {k: v.shown() for k, v in self._views.items()}}

    def choices(self) -> dict:
        """What was chosen for each file, as the history of the main window keeps it:
        {file: ((X, Y), log axes, the models whose curve is drawn)}."""
        return {key: (self._columns_of(key), self._logs.get(key, False),
                      frozenset(self._views[key].shown())) for key in self.files}

    def put_back(self, key: str, cols: tuple[str, str], log: bool, shown):
        """Gives file `key` the choices it once had (`choices`): it goes through the controls
        again, which fits and draws it."""
        self._cols[key], self._logs[key] = cols, log
        self._cur_key = None  # read again, even if it is the file in the controls
        self.show_file(key)
        self._views[key].select(shown)

    # ------------------------------------------------------------ files
    def _new_plot(self, key: str, name: str):
        """The plot of a file, in a window of its own on the board."""
        chart = self._app._add_chart("rheology", key, name)
        view = self._views[key] = FlowCurveView(chart.body)
        view.pack(fill="both", expand=True)
        chart.watch(view.canvas)

    def load_files(self, paths):
        """Opens each file as a new plot; one already open is opened again, as a copy."""
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
            self._app.select_chart("rheology", key)  # fitted and drawn as it is selected
        if errors:
            messagebox.showwarning(tr("read_fail"), "\n".join(errors), parent=self.winfo_toplevel())

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

    def _columns_of(self, key: str) -> tuple[str, str]:
        """(X, Y) of a file: the columns chosen for it, or the guess if it was never changed."""
        df = self.files[key]
        xi, yi = guess_columns(df.columns)
        return self._cols.get(key, (df.columns[xi], df.columns[yi]))

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
        self._cols.pop(key, None)  # reopening the file starts from scratch
        self._logs.pop(key, None)
        self._views.pop(key).canvas.close()
        if key == self._cur_key:
            self._cur_key = None
        self.listbox.delete(i)
        if self.files and was_open:
            self.listbox.selection_set(min(i, len(self.files) - 1))
        self.on_file_change()
        self._app._chart_closed("rheology", key)

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
        self.view = self._views.get(key)
        df = self.current()
        self.log_var.set(self._logs.get(key, False))
        if df is None:
            for cb in (self.x_cb, self.y_cb):
                cb["values"] = ()
                cb.set("")
        else:
            for cb, name in zip((self.x_cb, self.y_cb), self._columns_of(key)):
                cb["values"] = df.columns
                cb.set(name)
        self.refresh()

    def on_columns_change(self):
        if self._cur_key is not None:
            self._cols[self._cur_key] = (self.x_cb.get(), self.y_cb.get())
        self.refresh()

    def on_log_change(self):
        if self._cur_key is not None:
            self._logs[self._cur_key] = self.log_var.get()
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
        with what its row of the table of the models shows."""
        self.log_blocks = [
            [(f.model.name, ("head",) if k == 0 else ()), (f.model.equation, ()),
             (format_params(f), ()),
             (f"R² = {f.r2:.5f}   RMSE = {f.rmse:.4g}   AICc = {f.aicc:.2f}", ())]
            for k, f in enumerate(fits)]
        render_log(self.calc_log, self.log_blocks)

    def refresh(self):
        fits, error = [], ""
        if self.view is not None:
            fits, error = self.view.show(self.current(), self.x_cb.get(), self.y_cb.get(),
                                         self.log_var.get())
        self._log_render(fits)
        self.best_lbl.config(text=fits[0].model.name if fits else "")
        self.eq_lbl.config(text=f"⚠ {error}" if error else "")
        if fits:
            best = fits[0]
            self.eq_lbl.config(text=f"{best.model.equation}\n{format_params(best)}\n"
                                    f"R² = {best.r2:.5f}   RMSE = {best.rmse:.4g}")
