"""Widgets shared by the baseline and rheology views."""
from __future__ import annotations

import base64
import ctypes
import io
import sys
import tkinter as tk
from tkinter import ttk

import numpy as np
from matplotlib.backends import _backend_tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


HEADER_BG = "#d0d0d0"


def _mouse_down() -> bool:
    """Windows: whether the left mouse button is held (elsewhere, unknown: False)."""
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON


class PlotCanvas(FigureCanvasTkAgg):
    """Figure canvas that stays light while its window is resized.

    Redrawing a figure takes a few tenths of a second, and a window being dragged to another
    size asks for it at every step. So at each step the image already drawn is only stretched
    to the new size (instant), and the real draw is done once, when the size stops changing."""

    PAUSE_MS = 150
    _resizing = False
    _resize_after = None

    def resize(self, event):
        if event.width < 2 or event.height < 2 or getattr(self, "renderer", None) is None:
            return super().resize(event)  # nothing drawn yet to stretch
        self._resizing = True
        try:
            super().resize(event)  # the figure and the image take the new size; no draw
        finally:
            self._resizing = False
        # the renderer still holds the last real draw, at the size it was made for
        old = np.asarray(self.renderer.buffer_rgba())
        rows = np.arange(event.height) * old.shape[0] // event.height
        cols = np.arange(event.width) * old.shape[1] // event.width
        _backend_tk.blit(self._tkphoto, old.take(rows, 0).take(cols, 1), (0, 1, 2, 3))
        if self._resize_after is not None:
            self._tkcanvas.after_cancel(self._resize_after)
        self._resize_after = self._tkcanvas.after(self.PAUSE_MS, self._resize_done)

    def _resize_done(self, at=None):
        # a step slower than the pause is not the end of the drag: while the button is held
        # and the mouse keeps moving, the real draw would only make the window lag behind
        here = self._tkcanvas.winfo_pointerxy()
        if here != at and _mouse_down():
            self._resize_after = self._tkcanvas.after(self.PAUSE_MS, self._resize_done, here)
            return
        self._resize_after = None
        self.draw_idle()

    def draw_idle(self):
        if not self._resizing:
            super().draw_idle()


class Header(tk.Canvas):
    """Title bar of a window without the system's one: the title on the left, buttons on the
    right. Dragging it moves `window`. `buttons`: (name, glyph, command) from the right end
    inwards."""

    def __init__(self, window, height: int, buttons, on_double=None):
        super().__init__(window, height=height, highlightthickness=0, borderwidth=0,
                         background=HEADER_BG)
        self._window, self._on_double, self._names = window, on_double, [b[0] for b in buttons]
        self._grab = None
        self.create_text(7, height // 2, anchor="w", font=("Segoe UI", 9, "bold"), fill="black",
                         tags="title")
        for name, glyph, command in buttons:
            self.create_rectangle(0, 0, 0, 0, outline="", fill="", tags=(name, "btn", f"{name}_bg"))
            self.create_text(0, 0, text=glyph, font=("Segoe UI", 10, "bold"), fill="black",
                             tags=(name, "btn", f"{name}_glyph"))
            # darker while the mouse is on it
            self.tag_bind(name, "<Enter>", lambda _e, n=name: self.itemconfigure(
                f"{n}_bg", fill="#b8b8b8"))
            self.tag_bind(name, "<Leave>", lambda _e, n=name: self.itemconfigure(
                f"{n}_bg", fill=""))
            # after the click is fully handled: a command may destroy this very canvas, and Tk
            # crashes if that happens while it is still delivering the click to it
            self.tag_bind(name, "<Button-1>", lambda _e, c=command: window.after(1, c))
        self.bind("<Configure>", self._place_buttons)
        self.bind("<Button-1>", self._drag_start)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Double-Button-1>", self._double)

    def set_title(self, text: str):
        self.itemconfigure("title", text=text)

    def _place_buttons(self, event):
        """The buttons stay at the right end of the bar, whatever its width."""
        h = int(self.cget("height"))
        for k, name in enumerate(self._names):
            x1 = event.width - 4 - 22 * k
            self.coords(f"{name}_bg", x1 - 20, 3, x1, h - 3)
            self.coords(f"{name}_glyph", x1 - 10, h // 2 - 1)

    def _on_button(self) -> bool:
        return "btn" in self.gettags("current")

    def _drag_start(self, event):
        w = self._window
        self._grab = None if self._on_button() else (event.x_root - w.winfo_x(),
                                                     event.y_root - w.winfo_y())

    def _drag(self, event):
        if self._grab is not None:
            self._window.geometry(f"+{event.x_root - self._grab[0]}+{event.y_root - self._grab[1]}")

    def _double(self, _event):
        if self._on_double and not self._on_button():  # on a button, its clicks did their job
            self._on_double()


def _wrapper(window) -> int:
    """Windows: handle of the real top-level window around a Tk window."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    return user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT


class ToolWindow(tk.Toplevel):
    """Floating tool window, always over the window `owner`, with its own header in place of
    the system's title bar: the title, a button that minimizes it (only the header stays on
    screen; again, it opens back) and one that closes it. It is moved by dragging the header and
    resized by dragging its left, right or bottom edge (or a bottom corner). The content goes
    in `body`.

    Laying out the controls again takes far longer than a step of the mouse. While an edge is
    dragged only the window follows it; `body` keeps its size and is fitted to the window when
    the mouse pauses or lets go."""

    HEADER = 22  # height of the header, in pixels
    RIM = 4      # margin left around `body`: with the outline, the edge that is dragged
    GRAB = 8     # how far from the outline a drag still resizes, where `body` is empty there
    CORNER = 16  # ... and how far from a bottom corner it resizes both ways
    MIN_SIZE = (160, 90)
    PAUSE_MS = 120
    _head = None  # canvas of the header (Toplevel sets a title before it exists)

    def __init__(self, owner, on_close=None):
        super().__init__(owner)
        self._owner_win = owner
        self.minimized = False
        self._full_height = None  # height to go back to when it is opened again
        self._sizing = None       # edge being dragged and where the drag started
        self._frozen = False      # `body` keeps its size while the window changes
        self._fit_after = None
        self._on_close = on_close or self.destroy
        if sys.platform == "win32":
            self.overrideredirect(True)  # no system title bar: the header below replaces it
            self.bind("<Map>", self._own, add="+")
        else:  # no way to keep a borderless window over its owner: system title bar too
            self.transient(owner)
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(highlightthickness=1, highlightbackground="#707070",
                       highlightcolor="#707070")  # thin outline, as a window border
        self._head = Header(self, self.HEADER, [("close", "\u00d7", lambda: self._on_close()),
                                                ("min", "\u2013", self.toggle_minimize)],
                            on_double=self.toggle_minimize)
        self._head.pack(side="top", fill="x")
        self.body = ttk.Frame(self)
        self._fit_body()
        self.bind("<Motion>", self._edge_cursor, add="+")
        self.bind("<Button-1>", self._size_start, add="+")
        self.bind("<B1-Motion>", self._size_drag, add="+")
        self.bind("<ButtonRelease-1>", self._size_end, add="+")

    def _fit_body(self):
        """`body` takes the whole window under the header, less the rim."""
        rim = self.RIM
        self.body.place(x=rim, y=self.HEADER, relwidth=1, relheight=1, width=-2 * rim,
                        height=-(self.HEADER + rim))
        self._frozen = False

    def fit_height(self) -> int:
        """Height of the window that shows the whole content (`body` is placed, so the window
        does not ask for it by itself)."""
        return self.HEADER + self.body.winfo_reqheight() + self.RIM + 2

    # ----------------------------------------------------- resizing by the edges
    def _edge(self, event) -> str:
        """Edge under the mouse, as compass points ("w", "e", "s", "sw", "se"), or ""."""
        # the events of every widget inside come here too: only the bare window counts
        if event.widget not in (self, self.body) or self.minimized:
            return ""
        w, h = self.winfo_width(), self.winfo_height()
        x, y = event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty()
        if y < self.HEADER:
            return ""
        side = "w" if x < self.GRAB else "e" if x >= w - self.GRAB else ""
        if y >= h - self.GRAB:
            return "s" + ("w" if x < self.CORNER else "e" if x >= w - self.CORNER else "")
        return "s" + side if side and y >= h - self.CORNER else side

    def _edge_cursor(self, event):
        if self._sizing is None:
            cursor = {"w": "size_we", "e": "size_we", "s": "size_ns", "sw": "size_ne_sw",
                      "se": "size_nw_se"}.get(self._edge(event), "")
            if self.cget("cursor") != cursor:
                self.configure(cursor=cursor)

    def _size_start(self, event):
        edge = self._edge(event)
        if edge:
            self._sizing = (edge, event.x_root, event.y_root, self.winfo_x(), self.winfo_y(),
                            self.winfo_width(), self.winfo_height())

    def _size_drag(self, event):
        if self._sizing is None:
            return
        edge, x0, y0, x, y, w, h = self._sizing
        dx, dy = event.x_root - x0, event.y_root - y0
        min_w, min_h = self.MIN_SIZE
        if "e" in edge:
            w = max(w + dx, min_w)
        elif "w" in edge:  # the right edge stays where it is
            new_w = max(w - dx, min_w)
            x, w = x + w - new_w, new_w
        if "s" in edge:
            h = max(h + dy, min_h)
        if not self._frozen:
            self._frozen = True
            self.body.place(relwidth=0, relheight=0, width=self.body.winfo_width(),
                            height=self.body.winfo_height())
        self.geometry(f"{w}x{h}+{x}+{y}")
        if self._fit_after is not None:
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(self.PAUSE_MS, self._size_pause, event.x_root, event.y_root)

    def _size_pause(self, x_root, y_root):
        """Fits the content if the mouse is still where its last step left it. On a slow
        machine a step may take longer than the pause: the mouse has gone on meanwhile, and
        fitting now would only make it later."""
        if self._sizing is not None and self.winfo_pointerxy() != (x_root, y_root):
            self._fit_after = self.after(self.PAUSE_MS, self._size_pause, *self.winfo_pointerxy())
        else:
            self._fit_after = None
            self._size_fit()

    def _size_fit(self):
        if self._fit_after is not None:
            self.after_cancel(self._fit_after)
            self._fit_after = None
        if self._frozen and not self.minimized:
            self._fit_body()

    def _size_end(self, _event):
        if self._sizing is not None:
            self._sizing = None
            self._size_fit()

    def _own(self, _event=None):
        """Windows: makes the main window the owner of this one, so it stays over it (and only
        over it) and is minimized and restored with it. `transient` does not do it for a
        window without the system's title bar."""
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            user32.SetWindowLongPtrW(_wrapper(self), -8, _wrapper(self._owner_win))  # the owner
        except (AttributeError, OSError, tk.TclError):
            pass

    def title(self, text=None):
        if text is not None and self._head is not None:
            self._head.set_title(text)
        return super().title(text)

    def toggle_minimize(self):
        """Minimized, only the header stays on screen; again, the window opens back."""
        self.minimized = not self.minimized
        if self.minimized:
            self._full_height = self.winfo_height()
            self.body.place_forget()
            self.configure(cursor="")
            self.geometry(f"{self.winfo_width()}x{self.HEADER + 2}")
        else:
            self._fit_body()
            self.geometry(f"{self.winfo_width()}x{self._full_height}")

    def full_geometry(self) -> str:
        """Its geometry when open (a minimized window is only as tall as its header)."""
        if not self.minimized:
            return self.geometry()
        return f"{self.winfo_width()}x{self._full_height}+{self.winfo_x()}+{self.winfo_y()}"


def show_image(parent, fig, title: str) -> tk.Toplevel:
    """Opens a window with a picture of the figure `fig` as it is now: only to look at, with
    none of the plot's functions (zoom, pan, anchors, legend clicks)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    top = tk.Toplevel(parent)
    top.title(title)
    image = tk.PhotoImage(master=top, data=base64.b64encode(buf.getvalue()))
    label = tk.Label(top, image=image, borderwidth=0)
    label.image = image  # kept: Tk does not hold it
    label.pack()
    top.resizable(False, False)  # a picture: its size is the image's
    top.is_picture = True  # nothing in it to translate: it survives a language switch
    return top


def _inner_width(text: tk.Text) -> int:
    edge = sum(int(text.cget(o)) for o in ("borderwidth", "highlightthickness", "padx"))
    return max(text.winfo_width() - 2 * edge, 1)


def rule(text: tk.Text) -> tk.Frame:
    """Thin black line to embed in `text`, across it. It is exactly as wide as the text area:
    wider, the box would scroll sideways to show its end, leaving the text out of sight."""
    return tk.Frame(text, height=1, width=_inner_width(text), background="black")


def bind_rules(text: tk.Text):
    """Keeps the lines made by `rule` as wide as `text` when it changes size."""
    def fit(_event):
        for line in text.winfo_children():
            line.configure(width=_inner_width(text))
        text.xview_moveto(0)
    text.bind("<Configure>", fit)


def log_box(parent) -> tuple[ttk.Frame, tk.Text, ttk.Scrollbar]:
    """Read-only box of text blocks (the calculation logs) with its scrollbar, inside a frame
    to be placed by the caller. `render_log` fills it."""
    box = ttk.Frame(parent)
    log = tk.Text(box, width=1, height=4, wrap="word", font=("Segoe UI", 9), relief="solid",
                  borderwidth=1, padx=0, pady=0, cursor="arrow", state="disabled")
    log.tag_configure("head", font=("Segoe UI", 9, "bold"))
    log.tag_configure("dim", foreground="#666")
    # each block has a background, alternating white and gray, and a black line under it
    for tag, bg in (("even", "white"), ("odd", "#ececec")):
        log.tag_configure(tag, background=bg, lmargin1=6, lmargin2=6, rmargin=6)
        # the thinnest line a font gives is 3 px: the black line is drawn at its bottom
        log.tag_configure(f"sep_{tag}", background=bg, font=("Segoe UI", 1))
    log.tag_configure("first", spacing1=4)  # spacing at the top of each block
    log.tag_configure("last", spacing3=4)   # and at the bottom
    sb = ttk.Scrollbar(box, orient="vertical", command=log.yview)
    log.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)
    # nothing to mark in it: the mouse does not select its text
    for seq in ("<Button-1>", "<B1-Motion>", "<Double-Button-1>", "<Triple-Button-1>"):
        log.bind(seq, lambda _e: "break")
    bind_rules(log)
    return box, log, sb


def render_log(log: tk.Text, blocks: list[list[tuple[str, tuple]]], empty: str = ""):
    """Redraws a `log_box`: one block per entry, each a list of (text, tags) lines; with no
    blocks, shows the `empty` hint."""
    log.configure(state="normal")
    log.delete("1.0", "end")
    shown = blocks or ([[(empty, ("dim",))]] if empty else [])
    stripe = "even"
    for k, lines in enumerate(shown):
        stripe = "odd" if k % 2 else "even"
        for j, (text, tags) in enumerate(lines):
            edge = (("first",) if j == 0 else ()) + (("last",) if j == len(lines) - 1 else ())
            # the line break goes inside the tag: the background reaches the right edge
            log.insert("end", text + "\n", tuple(tags) + (stripe,) + edge)
        if blocks:  # thin black line under the block
            log.window_create("end", align="bottom", window=rule(log))
            log.insert("end", "\n")
            log.tag_add(f"sep_{stripe}", "end-3c", "end-1c")
    if shown:
        log.delete("end-2c")  # no blank line at the end
        # the Text's own final line break takes the tags of the line it ends: up to the right edge
        for tag in (f"sep_{stripe}",) if blocks else (stripe, "last"):
            log.tag_add(tag, "end-1c", "end")
    log.configure(state="disabled")


def block_at(log: tk.Text, blocks: list[list], event) -> int | None:
    """Index of the block under the mouse in a `log_box`, or None (no blocks)."""
    if not blocks:
        return None
    line = int(log.index(f"@{event.x},{event.y}").split(".")[0])
    k, first = 0, 1  # each block takes its lines and the separator line under them
    while k < len(blocks) - 1 and line >= first + len(blocks[k]) + 1:
        first += len(blocks[k]) + 1
        k += 1
    return k
