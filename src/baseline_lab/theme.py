"""Interface themes: light (the system's own look) and dark.

Only the interface follows the theme. The plots stay on white in both: they are the pictures
that get saved and copied elsewhere.

The colors the program paints by itself are roles, read with `color("role")`. What it leaves
to the system in the light theme (the ttk controls, the classic widgets that do not say their
colors) is given the dark colors by `apply`.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import i18n

THEMES = {"light": "theme_light", "dark": "theme_dark"}  # {theme: the text of its name}
_theme = "light"

# the colors painted by the program: {role: (light, dark)}
COLORS = {
    "ink": ("black", "#e0e0e0"),            # titles of the headers, strokes of the icons
    "paper": ("white", "#1e1e1e"),          # background of the icons, balloons and log blocks
    "dim": ("#555", "#a8a8a8"),             # secondary text
    "hint": ("#888", "#777777"),            # the hint of an empty board
    "board": ("white", "#181818"),
    "tab": ("#e4e4e4", "#333333"),          # strip of the sheet tabs and the tabs not open
    "header": ("#d0d0d0", "#3f3f3f"),       # header of a window; "_on": of the selected plot
    "header_on": ("#bcd4f0", "#2f5d8a"),
    "header_hover": ("#b8b8b8", "#5a5a5a"),  # a button of a header under the mouse
    "chart_edge": ("#9a9a9a", "#5f5f5f"),   # outline of a plot's window
    "chart_edge_on": ("#2f7fd6", "#4a9cf0"),
    "outline": ("#707070", "#6a6a6a"),      # outline of a tool window
    "icon_edge": ("#999", "#6a6a6a"),       # an icon of the top bar; "_off": grayed
    "icon_edge_off": ("#ccc", "#3a3a3a"),
    "icon_off": ("#c4c4c4", "#4d4d4d"),
    "stripe": ("#ececec", "#282828"),       # every other block of a log
    "rule": ("black", "#4a4a4a"),           # line under a block of a log
}

# the dark theme's colors of what the system paints in the light one
DARK = {
    "bg": "#2b2b2b", "text": "#e0e0e0", "field": "#1e1e1e", "off": "#7a7a7a",
    "select": "#2f5d8a", "select_text": "#ffffff",
    "button": "#3c3c3c", "hover": "#4a4a4a", "edge": "#555555", "line": "#000000",
}
_line: tk.PhotoImage | None = None  # one dot of "line": the picture of a dark separator

# what a classic widget that does not say its colors takes in the dark theme:
# {class: {option, as the option database names it: role of DARK}}
_PLAIN = {"background": "bg"}
_BOX = {"background": "field", "foreground": "text", "selectBackground": "select",
        "selectForeground": "select_text"}
DEFAULTS = {
    "Toplevel": _PLAIN, "Frame": _PLAIN, "Canvas": _PLAIN,
    "Label": {"background": "bg", "foreground": "text"},
    "Listbox": _BOX,
    "Text": {**_BOX, "insertBackground": "text"},
    "Entry": {**_BOX, "insertBackground": "text"},
    "Menu": {"background": "bg", "foreground": "text", "activeBackground": "select",
             "activeForeground": "select_text", "disabledForeground": "off",
             "selectColor": "text"},
}
_system: dict[tuple[str, str], str] = {}  # the same options as the system gives them: light
_native: str | None = None                # ttk theme of the system


def get_theme() -> str:
    return _theme


def set_theme(name: str) -> None:
    global _theme
    if name not in THEMES:
        raise ValueError(name)
    _theme = name


def load_theme() -> str:
    """Theme saved in the last session (default: light)."""
    name = i18n.load_setting("theme", "light")
    return name if name in THEMES else "light"


def save_theme(name: str) -> None:
    i18n.save_setting("theme", name)


def color(role: str) -> str:
    """Color of a role of COLORS in the current theme."""
    return COLORS[role][_theme == "dark"]


def _default(cls: str, option: str) -> str:
    if _theme == "dark":
        return DARK[DEFAULTS[cls][option]]
    return _system[cls, option]


def apply(root: tk.Tk) -> None:
    """Gives the current theme to the program of the window `root`: the ttk style, and the
    colors of the classic widgets created from now on. The ones that already exist keep
    theirs: the interface is rebuilt after a switch, and what outlives it uses `repaint`."""
    global _native
    style = ttk.Style(root)
    if _native is None:  # nothing touched yet: what the system gives now is the light theme
        _native = style.theme_use()
        for cls, options in DEFAULTS.items():
            # a Toplevel would show on screen: the main window has the same colors
            probe = root if cls == "Toplevel" else getattr(tk, cls)(root)
            for option in options:
                _system[cls, option] = str(probe.cget(option.lower()))
            if probe is not root:
                probe.destroy()
    for cls, option in _system:
        root.option_add(f"*{cls}.{option}", _default(cls, option))
    root.configure(background=_default("Toplevel", "background"))
    if _theme == "dark":
        _dark_style(style)
    else:
        style.theme_use(_native)


def repaint(widget: tk.Misc) -> None:
    """Gives the current theme to the classic widgets that took the colors of another: `widget`
    and what is inside it. The colors a widget chose itself are its own to set again."""
    options = DEFAULTS.get(widget.winfo_class())
    if options and _system:  # no theme applied yet: every widget still has the system's
        widget.configure(**{o.lower(): _default(widget.winfo_class(), o) for o in options})
    for child in widget.winfo_children():
        repaint(child)


def _dark_style(style: ttk.Style) -> None:
    """The ttk controls in dark colors. The system's own theme draws them with the system's
    pictures, which take no colors, so the dark theme is built on "clam"."""
    c = DARK
    style.theme_use("clam")
    style.configure(".", background=c["bg"], foreground=c["text"], fieldbackground=c["field"],
                    bordercolor=c["edge"], darkcolor=c["bg"], lightcolor=c["bg"],
                    troughcolor=c["field"], selectbackground=c["select"],
                    selectforeground=c["select_text"], insertcolor=c["text"],
                    arrowcolor=c["text"], focuscolor=c["edge"])
    style.map(".", background=[("disabled", c["bg"]), ("active", c["hover"])],
              foreground=[("disabled", c["off"])],
              selectbackground=[("!focus", c["select"])],
              selectforeground=[("!focus", c["select_text"])])
    pressed = [("disabled", c["bg"]), ("pressed", c["field"]), ("active", c["hover"])]
    for name in ("TButton", "TScrollbar", "Treeview.Heading"):
        style.configure(name, background=c["button"], darkcolor=c["button"],
                        lightcolor=c["button"])
        style.map(name, background=pressed, darkcolor=pressed, lightcolor=pressed)
    style.map("Toolbutton", background=pressed, darkcolor=pressed, lightcolor=pressed)
    style.configure("TCheckbutton", indicatorbackground=c["field"],
                    indicatorforeground=c["text"])
    style.map("TCheckbutton", background=[("active", c["bg"])],
              indicatorbackground=[("pressed", c["hover"]), ("disabled", c["bg"])])
    # a read-only box looks the same with the keyboard on it: no selection over its text
    style.configure("TCombobox", background=c["button"])
    style.map("TCombobox", background=[("active", c["hover"]), ("pressed", c["hover"])],
              fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])],
              selectbackground=[("readonly", c["field"])],
              selectforeground=[("readonly", c["text"])],
              arrowcolor=[("disabled", c["off"])],
              bordercolor=[("focus", c["edge"])], lightcolor=[("focus", c["field"])],
              darkcolor=[("focus", c["field"])])
    # a separator is drawn as a bevel, in a darker and a lighter shade of its color: the
    # lighter one shows as a white line on a dark window, whatever the color. A stretched dot
    # draws a plain line instead
    global _line
    if "Line.separator" not in style.element_names():
        _line = tk.PhotoImage(master=style.master, width=1, height=1)
        _line.put(c["line"])
        style.element_create("Line.separator", "image", _line, sticky="nswe")
    style.layout("TSeparator", [("Line.separator", {"sticky": "nswe"})])
    style.configure("Treeview", background=c["field"])
    style.map("Treeview", background=[("selected", c["select"])],
              foreground=[("selected", c["select_text"])])
