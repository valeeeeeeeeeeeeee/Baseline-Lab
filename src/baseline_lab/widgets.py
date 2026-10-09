"""Widgets shared by the baseline and rheology views."""
from __future__ import annotations

import base64
import contextlib
import ctypes
import io
import re
import sys
import tkinter as tk
import weakref
from pathlib import Path
from tkinter import ttk

import numpy as np
from matplotlib.backends import _backend_tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.layout_engine import ConstrainedLayoutEngine
from PIL import Image, ImageDraw

from . import theme


def _mouse_down() -> bool:
    """Windows: whether the left mouse button is held (elsewhere, unknown: False)."""
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON


class Layout(ConstrainedLayoutEngine):
    """Constrained layout of a figure, worked out again only when what it depends on changed.

    It is most of the time a draw takes (every tick label is measured, twice), and most draws
    change nothing it looks at: an anchor dragged, a peak marked or painted, a curve hidden."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._made = None  # what the layout in force was made for

    def execute(self, fig):
        state = self._state(fig)
        if state != self._made:
            super().execute(fig)
            self._made = state

    @staticmethod
    def _state(fig) -> tuple:
        """What sets the room the axes need around them: the size of the figure, the limits
        and scales (the tick labels come from them), the titles and what is not clipped to
        the axes (the texts and the legend)."""
        def texts(ax):
            legend = ax.get_legend()
            return ([(t.get_text(), t.get_position(), getattr(t, "xy", None), t.get_visible())
                     for t in ax.texts]
                    + [t.get_text() for t in (legend.get_texts() if legend else ())])

        return (fig.bbox.size.tolist(), fig.dpi,
                [(ax.get_visible(), ax.get_xlim(), ax.get_ylim(), ax.get_xscale(),
                  ax.get_yscale(), ax.get_title(), ax.get_xlabel(), ax.get_ylabel(), texts(ax))
                 for ax in fig.axes])


class PlotCanvas(FigureCanvasTkAgg):
    """Figure canvas that stays light while its window is resized.

    Redrawing a figure takes a few tenths of a second, and a window being dragged to another
    size asks for it at every step. So at each step the image already drawn is only stretched
    to the new size (instant), and the real draw is done once, when the size stops changing."""

    PAUSE_MS = 150
    _resizing = False
    _resize_after = None
    _all: weakref.WeakSet = weakref.WeakSet()  # every canvas there is

    def __init__(self, figure, master, on_screen=None):
        """`on_screen()`: whether the plot is to be seen now (default: always). While it is
        not, nothing draws it: matplotlib itself asks for a draw at every change of an axis
        shared with another figure."""
        self._dpi = figure.dpi  # of the figure at 100% zoom
        self._on_screen = on_screen or (lambda: True)
        super().__init__(figure, master=master)
        self._all.add(self)

    @classmethod
    def settle(cls):
        """Does now the real draws that wait for a pause, in every canvas."""
        for canvas in list(cls._all):
            if canvas._resize_after is not None:
                canvas._tkcanvas.after_cancel(canvas._resize_after)
                canvas._resize_after = None
                canvas.draw_idle()

    def set_zoom(self, zoom: float):
        """The figure is drawn `zoom` times as large (1: 100%), text and strokes included: its
        resolution changes, not its size in inches, so a picture saved from it is the same at
        any zoom. As on a resize, the real draw waits for the changes to stop."""
        fig = self.figure
        # the resolution matplotlib goes back to, which it multiplies by the screen's scale
        fig._original_dpi = self._dpi * zoom
        fig.set_dpi(fig._original_dpi * self.device_pixel_ratio)
        w, h = self._tkcanvas.winfo_width(), self._tkcanvas.winfo_height()
        if w < 2 or h < 2 or getattr(self, "renderer", None) is None:
            return  # not on screen yet: its first draw is already at this zoom
        # a window that changes size with the zoom does this again in `resize`
        fig.set_size_inches(w / fig.dpi, h / fig.dpi, forward=False)
        if self._resize_after is not None:
            self._tkcanvas.after_cancel(self._resize_after)
        self._resize_after = self._tkcanvas.after(self.PAUSE_MS, self._resize_done)

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
        if self._resizing or not self._on_screen():
            return
        # never drawn and not yet at its place in the window: taking it asks for a draw
        # (`resize`), and one made now, at another size, would only be thrown away
        if getattr(self, "renderer", None) is None:
            size = self._tkcanvas.winfo_width(), self._tkcanvas.winfo_height()
            if size != tuple(round(v) for v in self.figure.bbox.size):
                return
        super().draw_idle()

    def close(self):
        """Cancels the draws it still has scheduled: its widget is about to be destroyed."""
        for name in ("_idle_draw_id", "_resize_after"):
            job = getattr(self, name, None)
            if job:
                self._tkcanvas.after_cancel(job)
                setattr(self, name, None)


class Header(tk.Canvas):
    """Title bar of a window without the system's one: the title on the left, buttons on the
    right. Dragging it moves `window`. `buttons`: (name, glyph, command) from the right end
    inwards. `move`: how the window is taken to (x, y); by default, as a top-level window."""

    def __init__(self, window, height: int, buttons, on_double=None, move=None):
        super().__init__(window, height=height, highlightthickness=0, borderwidth=0)
        self._window, self._on_double, self._names = window, on_double, [b[0] for b in buttons]
        self._move = move or (lambda x, y: window.geometry(f"+{x}+{y}"))
        self._grab = None
        self.create_text(7, height // 2, anchor="w", font=("Segoe UI", 9, "bold"),
                         tags=("title", "ink"))
        for name, glyph, command in buttons:
            self.create_rectangle(0, 0, 0, 0, outline="", fill="", tags=(name, "btn", f"{name}_bg"))
            self.create_text(0, 0, text=glyph, font=("Segoe UI", 10, "bold"),
                             tags=(name, "btn", f"{name}_glyph", "ink"))
            # darker while the mouse is on it
            self.tag_bind(name, "<Enter>", lambda _e, n=name: self.itemconfigure(
                f"{n}_bg", fill=theme.color("header_hover")))
            self.tag_bind(name, "<Leave>", lambda _e, n=name: self.itemconfigure(
                f"{n}_bg", fill=""))
            # after the click is fully handled: a command may destroy this very canvas, and Tk
            # crashes if that happens while it is still delivering the click to it
            self.tag_bind(name, "<Button-1>", lambda _e, c=command: window.after(1, c))
        self.bind("<Configure>", self._place_buttons)
        self.bind("<Button-1>", self._drag_start)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Double-Button-1>", self._double)
        self.paint()

    def paint(self):
        """Takes the colors of the current theme."""
        self.configure(background=theme.color("header"))
        self.itemconfigure("ink", fill=theme.color("ink"))

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

    def _drag_start(self, _event):
        w, (x, y) = self._window, self.winfo_pointerxy()
        self._grab = None if self._on_button() else (x - w.winfo_x(), y - w.winfo_y())

    def _drag(self, _event):
        # where the mouse is now, not where the event says: an event handled after the window
        # has moved again carries a place worked out from where the window was
        if self._grab is not None:
            x, y = self.winfo_pointerxy()
            self._move(x - self._grab[0], y - self._grab[1])

    def _double(self, _event):
        if self._on_double and not self._on_button():  # on a button, its clicks did their job
            self._on_double()


def _wrapper(window) -> int:
    """Windows: handle of the real top-level window around a Tk window."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    return user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT


def title_bar(window):
    """Windows: the system's title bar of `window` follows the theme (it is only dark when
    asked for)."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()  # the real window only exists once it is laid out
        dark = ctypes.c_int(theme.get_theme() == "dark")
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        # DWMWA_USE_IMMERSIVE_DARK_MODE
        handle = _wrapper(window)
        dwm(handle, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        # Windows 10 only draws the bar again when the window gains or loses the keyboard:
        # WM_NCACTIVATE tells it so, first the opposite of what it is, then what it is
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                        ctypes.c_void_p]
        active = user32.GetForegroundWindow() == handle
        for state in (not active, active):
            user32.SendMessageW(handle, 0x0086, int(state), 0)
    except (AttributeError, OSError, tk.TclError):
        pass


def _own(window, owner):
    """Windows: makes `owner` the owner of the top-level `window`, so it stays over it (and
    only over it) and is minimized and restored with it. `transient` does not do it for a
    window without the system's title bar."""
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        user32.SetWindowLongPtrW(_wrapper(window), -8, _wrapper(owner))  # the owner
    except (AttributeError, OSError, tk.TclError):
        pass


class _BitmapInfo(ctypes.Structure):
    """BITMAPINFOHEADER, and room for what the system writes after it."""
    _fields_ = [("size", ctypes.c_uint32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                ("planes", ctypes.c_uint16), ("bits", ctypes.c_uint16),
                ("compression", ctypes.c_uint32), ("rest", ctypes.c_uint32 * 8)]


def _picture(window) -> tk.PhotoImage | None:
    """Windows: what the top-level `window` shows now, under the system's title bar if it has
    one; None if it is not on screen. Asked from the window itself, not copied from the
    screen: whatever covers it is not in the picture."""
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    void, num = ctypes.c_void_p, ctypes.c_int
    for lib, name, args in ((user32, "GetDC", [void]), (gdi32, "CreateCompatibleDC", [void]),
                            (gdi32, "CreateCompatibleBitmap", [void, num, num]),
                            (gdi32, "SelectObject", [void, void])):
        getattr(lib, name).restype, getattr(lib, name).argtypes = void, args
    for lib, name, args in ((user32, "IsWindowVisible", [void]), (user32, "IsIconic", [void]),
                            (user32, "PrintWindow", [void, void, ctypes.c_uint]),
                            (user32, "ReleaseDC", [void, void]),
                            (user32, "GetWindowRect", [void, void]),
                            (gdi32, "GetDIBits", [void, void, ctypes.c_uint, ctypes.c_uint,
                                                  void, void, ctypes.c_uint]),
                            (gdi32, "DeleteObject", [void]), (gdi32, "DeleteDC", [void])):
        getattr(lib, name).argtypes = args
    handle = _wrapper(window)
    if not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
        return None
    # the whole window is asked for, and the part of Tk taken from it: its inside alone comes
    # blank from a window that has been transparent
    box = (ctypes.c_int32 * 4)()
    user32.GetWindowRect(handle, box)
    w, h = box[2] - box[0], box[3] - box[1]
    left, top = window.winfo_rootx() - box[0], window.winfo_rooty() - box[1]
    if w < 2 or h < 2:
        return None
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    before = gdi32.SelectObject(dc, bitmap)
    done = user32.PrintWindow(handle, dc, 2)  # PW_RENDERFULLCONTENT
    gdi32.SelectObject(dc, before)
    # 4 bytes a pixel, from the top row down
    info = _BitmapInfo(size=40, width=w, height=-h, planes=1, bits=32)
    pixels = ctypes.create_string_buffer(w * h * 4)
    rows = gdi32.GetDIBits(dc, bitmap, 0, h, pixels, ctypes.byref(info), 0) if done else 0
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    if rows != h:
        return None
    rgb = np.frombuffer(pixels, np.uint8, w * h * 4).reshape(h, w, 4)[:, :, 2::-1]  # from BGRA
    rgb = rgb[top:top + window.winfo_height(), left:left + window.winfo_width()]
    h, w = rgb.shape[:2]
    return tk.PhotoImage(master=window, data=b"P6 %d %d 255\n" % (w, h) + rgb.tobytes())


def _stacked(windows) -> list:
    """Windows: the top-level `windows`, from the one under the others to the one on top."""
    user32 = ctypes.WinDLL("user32")
    user32.GetTopWindow.restype, user32.GetTopWindow.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    user32.GetWindow.restype = ctypes.c_void_p
    user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    mine = {_wrapper(w): w for w in windows if w.winfo_viewable()}
    found, handle = [], user32.GetTopWindow(None)
    for _ in range(10000):  # the order may change while it is read: never around forever
        if not handle or len(found) == len(mine):
            break
        if handle in mine:
            found.append(mine[handle])
        handle = user32.GetWindow(handle, 2)  # GW_HWNDNEXT
    return found[::-1]


_born: list | None = None  # a `still` is on: the tool windows made since, shown at its end


@contextlib.contextmanager
def still(main, windows=()):
    """Windows: the screen stays as it is while the interface is rebuilt, and changes at once
    at the end. Rebuilding takes seconds and Tk draws along the way: each window would take
    its new look at a time of its own. So the main window `main` and each of its top-level
    `windows` is covered by a picture of itself, and a tool window made meanwhile is
    transparent; at the end the pictures go and those windows show, all together. Elsewhere
    nothing is done."""
    global _born
    if sys.platform != "win32" or _born is not None:  # no way to; or inside another one
        yield
        return
    covers = []
    try:
        for w in _stacked([main, *windows]):  # each cover comes on top of the ones before
            picture = _picture(w)
            if picture is None:
                continue
            cover = tk.Toplevel(main)
            cover.overrideredirect(True)
            cover.geometry(f"{picture.width()}x{picture.height()}"
                           f"+{w.winfo_rootx()}+{w.winfo_rooty()}")
            tk.Label(cover, image=picture, borderwidth=0, padx=0, pady=0,
                     cursor="watch").pack()
            cover.picture = picture  # kept: Tk does not hold it
            cover.is_cover = True    # the one window of the main one that a rebuild leaves
            covers.append(cover)
            cover.update_idletasks()
            _own(cover, main)
        main.update()  # the covers are drawn on the events of their windows
    except (AttributeError, OSError, tk.TclError):  # as far as it got: what is left shows early
        pass
    _born = []
    try:
        yield
    finally:
        born, _born = _born, None
        for w in born:
            if w.winfo_exists():  # not closed meanwhile
                w.attributes("-alpha", 1.0)
        for cover in covers:
            cover.destroy()


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
        if _born is not None:  # the interface is being rebuilt: it comes with the rest
            self.attributes("-alpha", 0.0)
            _born.append(self)
        self.configure(highlightthickness=1)  # thin outline, as a window border
        self._head = Header(self, self.HEADER, [("close", "\u00d7", lambda: self._on_close()),
                                                ("min", "\u2013", self.toggle_minimize)],
                            on_double=self.toggle_minimize)
        self._head.pack(side="top", fill="x")
        self.body = ttk.Frame(self)
        self._fit_body()
        ToolWindow.paint(self)  # its own colors: a subclass paints what it has not built yet
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

    def paint(self):
        """Takes the colors of the current theme: a window that outlives a theme switch does
        it again."""
        theme.repaint(self)
        outline = theme.color("outline")
        self.configure(highlightbackground=outline, highlightcolor=outline)
        self._head.paint()

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

    def _own(self, event=None):
        if event is None or event.widget is self:  # every widget inside sends its <Map> here
            _own(self, self._owner_win)

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


def file_key(files: dict, path) -> str:
    """Key of a file about to be opened among `files`: its full path, and from its second
    copy on (the same file may be opened more than once) the number of the copy after it."""
    base = key = str(Path(path).resolve())
    n = 1
    while key in files:
        n += 1
        key = f"{base}|{n}"
    return key


def file_title(key: str, name: str) -> str:
    """Name of an open file as lists and headers show it: its copies are numbered."""
    copy = re.search(r"\|(\d+)$", key)
    return f"{name} ({copy.group(1)})" if copy else name


class ChartWindow(tk.Frame):
    """Small window of one plot on the board (the blank area of the main window): a header
    with the title and a button that closes it and, under it, `body`, where the plot goes. It
    is moved over the board by dragging the header and resized by dragging its left, right or
    bottom edge (or a bottom corner). Its outline and header are colored while it is the
    selected one; a click anywhere on it selects it (`on_select`)."""

    HEADER = 22  # height of the header, in pixels
    EDGE = 2     # outline
    RIM = 4      # margin left around `body`: with the outline, the edge that is dragged
    CORNER = 16  # how far from a bottom corner a drag resizes both ways
    REACH = 60   # how much of the header always stays on the board, to be dragged back
    MIN_SIZE = (220, 180)
    PICK = "ChartPick"  # binding tag of the widgets whose clicks select their window

    def __init__(self, board, title: str, on_select, on_close):
        super().__init__(board, background="white", highlightthickness=self.EDGE)
        self._on_select = on_select
        self.selected = False
        self.box = (0, 0, *self.MIN_SIZE)  # (x, y, width, height) on the board, at 100% zoom
        self.scale = 1.0                   # zoom of the board
        self.origin = (0.0, 0.0)           # where the (0, 0) of `box` is on the board, in pixels
        self._at = self.box                # where it is on screen, in pixels
        self._plot = None                  # the PlotCanvas inside `body`
        self._sizing = None                # edge being dragged and where the drag started
        self._head = Header(self, self.HEADER, [("close", "×", on_close)],
                            move=self._drag_to)
        self._head.set_title(title)
        self._head.place(x=0, y=0, relwidth=1, height=self.HEADER)
        self._head.bind("<Button-1>", self._head_press, add="+")
        self.body = tk.Frame(self, background="white")
        self.body.place(x=self.RIM, y=self.HEADER, relwidth=1, relheight=1, width=-2 * self.RIM,
                        height=-(self.HEADER + self.RIM))
        # only its bare rim sends events to the frame itself
        self.bind("<Motion>", self._edge_cursor)
        self.bind("<Leave>", self._edge_left)
        self.bind("<Button-1>", self._size_start)
        self.bind("<B1-Motion>", self._size_drag)
        self.bind("<ButtonRelease-1>", self._size_end)
        self.set_selected(False)

    def set_selected(self, on: bool):
        self.selected = on
        mark = "_on" if on else ""
        edge = theme.color(f"chart_edge{mark}")
        self.configure(highlightbackground=edge, highlightcolor=edge)
        self._head.configure(background=theme.color(f"header{mark}"))

    def watch(self, canvas: PlotCanvas):
        """`canvas` is the plot inside `body`: it follows the zoom of the board, and the
        clicks on it select the window. On a window that is not the selected one that is all
        the click does: the plot does not get it."""
        self._plot = canvas
        canvas.set_zoom(self.scale)
        widget = canvas.get_tk_widget()
        widget.bindtags((self.PICK,) + widget.bindtags())
        widget.bind_class(self.PICK, "<ButtonPress>", self._pick)

    @staticmethod
    def _pick(event):
        chart = event.widget
        while chart is not None and not isinstance(chart, ChartWindow):
            chart = chart.master
        if chart is None or chart.selected:
            return None
        chart._on_select()
        return "break"

    def _head_press(self, _event):
        if not self.selected and not self._head._on_button():  # not on its way to be closed
            self._on_select()

    # ------------------------------------------------------- place on the board
    def put(self, x: float, y: float, w: float, h: float):
        self.box = (x, y, w, h)
        self.fit()

    def set_scale(self, scale: float, origin: tuple[float, float]):
        """The zoom of the board changed: the window and its plot take it."""
        self.scale, self.origin = scale, origin
        if self._plot is not None:
            self._plot.set_zoom(scale)
        self.fit()

    def fit(self):
        """Takes its place on screen: `box` at the zoom of the board, no smaller than MIN_SIZE
        (which a zoom under 100% makes smaller too). `box` is left as it is: a zoom or a board
        that kept the window from its place gives it back when undone."""
        x, y, w, h = (round(v * self.scale) for v in self.box)
        low = min(self.scale, 1)
        self._place(x + round(self.origin[0]), y + round(self.origin[1]),
                    max(w, round(self.MIN_SIZE[0] * low)),
                    max(h, round(self.MIN_SIZE[1] * low)))

    def _place(self, x: int, y: int, w: int, h: int):
        """Goes to (x, y) on screen, as far as its header stays within reach."""
        board = self.master
        if board.winfo_width() > 1:  # a board not laid out yet has no size to keep it within
            x = min(max(x, self.REACH - w), max(board.winfo_width() - self.REACH, 0))
            y = min(max(y, 0), max(board.winfo_height() - self.HEADER, 0))
        self._at = (x, y, w, h)
        self.place(x=x, y=y, width=w, height=h)

    def _move_px(self, x: int, y: int):
        """A drag took it to (x, y) on screen: from now on that is its place."""
        self._place(x, y, *self._at[2:])
        self.box = (*self._unscaled()[:2], *self.box[2:])

    def _put_px(self, x: int, y: int, w: int, h: int):
        """The same for a drag of an edge, which gives it its size too."""
        self._place(x, y, w, h)
        self.box = self._unscaled()

    def _unscaled(self) -> tuple:
        """Where it is on screen, as a `box`."""
        (x, y, w, h), (ox, oy), s = self._at, self.origin, self.scale
        return ((x - round(ox)) / s, (y - round(oy)) / s, w / s, h / s)

    # ----------------------------------------------------- resizing by the edges
    def _edge(self, event) -> str:
        """Edge under the mouse, as compass points ("w", "e", "s", "sw", "se"), or ""."""
        w, h = self.winfo_width(), self.winfo_height()
        grab = self.EDGE + self.RIM
        if event.y < self.HEADER:
            return ""
        side = "w" if event.x < grab else "e" if event.x >= w - grab else ""
        if event.y >= h - grab:
            return "s" + ("w" if event.x < self.CORNER else "e" if event.x >= w - self.CORNER
                          else "")
        return "s" + side if side and event.y >= h - self.CORNER else side

    def _edge_cursor(self, event):
        if self._sizing is None:
            cursor = {"w": "size_we", "e": "size_we", "s": "size_ns", "sw": "size_ne_sw",
                      "se": "size_nw_se"}.get(self._edge(event), "")
            if self.cget("cursor") != cursor:
                self.configure(cursor=cursor)

    def _edge_left(self, _event=None):
        # what is inside the window has no cursor of its own and shows this one: left as the
        # arrows of an edge, they would stay all over the plot
        if self._sizing is None and self.cget("cursor"):
            self.configure(cursor="")

    def _size_start(self, event):
        edge = self._edge(event)
        if not self.selected:
            self._on_select()
        if edge:
            self._sizing = (edge, *self.winfo_pointerxy(), self._at)

    def _size_drag(self, _event):
        if self._sizing is None:
            return
        edge, x0, y0, (x, y, w, h) = self._sizing
        here = self.winfo_pointerxy()  # as in `Header._drag`
        dx, dy = here[0] - x0, here[1] - y0
        min_w, min_h = self.MIN_SIZE
        if "e" in edge:
            w = max(w + dx, min_w)
        elif "w" in edge:  # the right edge stays where it is
            new_w = max(w - dx, min_w)
            x, w = x + w - new_w, new_w
        if "s" in edge:
            h = max(h + dy, min_h)
        self._step(self._put_px, x, y, w, h)

    def _drag_to(self, x: int, y: int):
        self._step(self._move_px, x, y)

    _painting = False  # one step at a time, whichever window asks
    _next = None       # the step that came while the previous one was being painted

    def _step(self, place, *box):
        """Takes one step of a drag, `place(*box)`, and paints now what it uncovered (the
        board, the plots under this window).

        Left to the event loop the painting comes late: Windows only asks for a repaint when
        no mouse message is waiting, and while the mouse keeps moving there always is one.
        Until then the place the window left still shows it, as a trail behind the window.

        A step that comes while another is being painted only waits its turn (the last one
        to come is the one taken): were it to move the window right then, that move would be
        one more left unpainted, and on a fast drag they come one after the other."""
        ChartWindow._next = (place, box)
        if ChartWindow._painting:
            return
        ChartWindow._painting = True
        try:
            while ChartWindow._next:
                (place, box), ChartWindow._next = ChartWindow._next, None
                place(*box)
                self.update_idletasks()  # the window goes to its new place
                if sys.platform == "win32":
                    user32 = ctypes.windll.user32
                    user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                    ctypes.c_void_p, ctypes.c_uint]
                    # RDW_ALLCHILDREN | RDW_UPDATENOW: the repaint requests go out right away
                    user32.RedrawWindow(self.master.winfo_id(), None, None, 0x0180)
                self.update()  # ... and are answered: Tk draws on the events it takes them as
        except tk.TclError:  # the window was closed meanwhile
            pass
        finally:
            ChartWindow._painting, ChartWindow._next = False, None

    def _size_end(self, event):
        self._sizing = None
        if self.winfo_containing(*self.winfo_pointerxy()) is self:
            self._edge_cursor(event)
        else:  # the drag ended off the edge, which was left with no `<Leave>` to say so
            self._edge_left()


class ZoomSlider(tk.Canvas):
    """Zoom control of the strip at the bottom of the main window: "−", a thin line with a
    small rectangular knob, "+" and, at the right end, the zoom in percent. 100% is the middle
    of the line: each half has its own scale, down to LOW and up to HIGH. A click on a sign
    takes the zoom to the next multiple of STEP. `command(zoom)` is called with the zoom the
    user asks for, as a factor (1: 100%); `set` only shows one."""

    LOW, HIGH = 25, 400  # percent
    STEP = 10
    X0, LINE = 22, 100   # where the line starts and its length, in pixels

    def __init__(self, parent, command):
        super().__init__(parent, width=self.X0 + self.LINE + 68, height=1,
                         highlightthickness=0, background=theme.color("tab"))
        self._command = command
        self.percent = 100
        self._dragging = False
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)

    def set(self, percent: int):
        self.percent = min(max(int(percent), self.LOW), self.HIGH)
        self._draw()

    def step(self, direction: int):
        """One step in (1) or out (-1), as a click on "+" or "−"."""
        if direction > 0:
            self._ask((self.percent // self.STEP + 1) * self.STEP)
        else:
            self._ask((-(-self.percent // self.STEP) - 1) * self.STEP)

    def _ask(self, percent: int):
        before = self.percent
        self.set(percent)
        if self.percent != before:
            self._command(self.percent / 100)

    def _x_of(self, percent: int) -> float:
        half = self.LINE / 2
        span = 100 - self.LOW if percent < 100 else self.HIGH - 100
        return self.X0 + half + half * (percent - 100) / span

    def _percent_at(self, x: int) -> int:
        half = self.LINE / 2
        d = min(max(x - self.X0, 0), self.LINE) - half
        if abs(d) <= 2:  # the middle holds the knob: 100% is easy to come back to
            return 100
        span = 100 - self.LOW if d < 0 else self.HIGH - 100
        return 5 * round((100 + d / half * span) / 5)

    def _press(self, event):
        x1 = self.X0 + self.LINE
        self._dragging = self.X0 - 5 <= event.x <= x1 + 5
        if self._dragging:
            self._drag(event)
        elif event.x < self.X0:
            self.step(-1)
        elif event.x < x1 + 22:
            self.step(1)

    def _drag(self, event):
        if self._dragging:
            self._ask(self._percent_at(event.x))

    def _draw(self, _event=None):
        self.delete("all")
        y, x1 = self.winfo_height() // 2, self.X0 + self.LINE
        ink, sign = theme.color("ink"), ("Segoe UI", 10, "bold")
        self.create_text(self.X0 - 12, y - 1, text="−", font=sign, fill=ink)
        self.create_line(self.X0, y, x1 + 1, y, fill=theme.color("dim"))
        x = round(self._x_of(self.percent))
        self.create_rectangle(x - 2, y - 5, x + 3, y + 6, fill=ink, outline="")
        self.create_text(x1 + 12, y - 1, text="+", font=sign, fill=ink)
        self.create_text(int(self.cget("width")) - 6, y, anchor="e", text=f"{self.percent}%",
                         font=("Segoe UI", 9), fill=ink)


def paint_icon(master, color: str | None, size: int = 16) -> tk.PhotoImage:
    """Picture for a menu entry: a paint bucket, tilted, pouring a drop, in `color`; None: a
    blank one, which keeps the texts of the other entries in line with that entry's. Drawn
    large and scaled down, so its edges come out smooth."""
    k = 8
    img = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    if color is not None:
        d, u = ImageDraw.Draw(img), size * k / 64
        ink = tuple(v // 256 for v in master.winfo_rgb(color)) + (255,)

        def at(*points):
            return [(x * u, y * u) for x, y in points]

        d.line(at((8, 31), (27, 12), (48, 33), (29, 52), (8, 31), (27, 12)), fill=ink,
               width=round(4 * u), joint="curve")
        d.polygon(at((11, 32), (46, 32), (29, 49)), fill=ink)  # the paint in it
        d.arc(at((17, 3), (37, 23)), 150, 20, fill=ink, width=round(3.5 * u))  # the handle
        d.polygon(at((56, 34), (50.5, 47), (61.5, 47)), fill=ink)
        d.ellipse(at((50, 43), (62, 55)), fill=ink)
    buf = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(buf, format="png")
    return tk.PhotoImage(master=master, data=base64.b64encode(buf.getvalue()))


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
    title_bar(top)
    return top


CAN_COPY_IMAGE = sys.platform == "win32"  # the clipboard takes pictures only through Windows


def copy_figure(fig, dpi: int = 200):
    """Puts a picture of the figure `fig` as it is now on the clipboard, to be pasted elsewhere.
    Raises OSError if the clipboard does not take it."""
    buf, bmp = io.BytesIO(), io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    # with its resolution: pasted in a document, the picture has the size of the figure
    Image.open(buf).convert("RGB").save(bmp, "BMP", dpi=(dpi, dpi))
    data = bmp.getvalue()[14:]  # what the clipboard holds is a .bmp without its file header
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    for f in (kernel32.GlobalLock, kernel32.GlobalUnlock, kernel32.GlobalFree,
              user32.OpenClipboard):
        f.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise ctypes.WinError()
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):  # another program is holding it
        error = ctypes.WinError()
        kernel32.GlobalFree(handle)
        raise error
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(8, handle):  # CF_DIB; taken: the memory is the system's
            error = ctypes.WinError()
            kernel32.GlobalFree(handle)
            raise error
    finally:
        user32.CloseClipboard()


def _inner_width(text: tk.Text) -> int:
    edge = sum(int(text.cget(o)) for o in ("borderwidth", "highlightthickness", "padx"))
    return max(text.winfo_width() - 2 * edge, 1)


def rule(text: tk.Text) -> tk.Frame:
    """Thin line to embed in `text`, across it. It is exactly as wide as the text area:
    wider, the box would scroll sideways to show its end, leaving the text out of sight."""
    return tk.Frame(text, height=1, width=_inner_width(text), background=theme.color("rule"))


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
    # each block has a background, alternating two shades, and a line under it
    for tag in ("even", "odd"):
        log.tag_configure(tag, lmargin1=6, lmargin2=6, rmargin=6)
        # the thinnest line a font gives is 3 px: the line is drawn at its bottom
        log.tag_configure(f"sep_{tag}", font=("Segoe UI", 1))
    log.tag_configure("first", spacing1=4)  # spacing at the top of each block
    log.tag_configure("last", spacing3=4)   # and at the bottom
    paint_log(log)
    sb = ttk.Scrollbar(box, orient="vertical", command=log.yview)
    log.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)
    # nothing to mark in it: the mouse does not select its text
    for seq in ("<Button-1>", "<B1-Motion>", "<Double-Button-1>", "<Triple-Button-1>"):
        log.bind(seq, lambda _e: "break")
    bind_rules(log)
    return box, log, sb


def paint_log(log: tk.Text):
    """The colors of a `log_box` in the current theme. The lines under its blocks take theirs
    when it is rendered."""
    log.tag_configure("dim", foreground=theme.color("dim"))
    for tag, role in (("even", "paper"), ("odd", "stripe")):
        for name in (tag, f"sep_{tag}"):
            log.tag_configure(name, background=theme.color(role))


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
        if blocks:  # thin line under the block
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
