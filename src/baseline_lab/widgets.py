"""Widgets shared by the baseline and rheology views."""
from __future__ import annotations

import base64
import ctypes
import io
import sys
import tkinter as tk
from tkinter import ttk


HEADER_BG = "#d0d0d0"


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
    resized by the grip in its corner. The content goes in `body`."""

    HEADER = 22  # height of the header, in pixels
    _head = None  # canvas of the header (Toplevel sets a title before it exists)

    def __init__(self, owner, on_close=None):
        super().__init__(owner)
        self._owner_win = owner
        self.minimized = False
        self._full_height = None  # height to go back to when it is opened again
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
        self.body.pack(fill="both", expand=True)
        self._grip = ttk.Sizegrip(self)
        self._grip.place(relx=1.0, rely=1.0, anchor="se")

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
            self.body.pack_forget()
            self._grip.place_forget()
            self.geometry(f"{self.winfo_width()}x{self.HEADER + 2}")
        else:
            self.body.pack(fill="both", expand=True)
            self._grip.place(relx=1.0, rely=1.0, anchor="se")
            self._grip.lift()
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
