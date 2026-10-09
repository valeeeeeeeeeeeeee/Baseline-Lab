"""Colors of the color picker: hex text, HSV, the color wheel and the pictures it is made of.

No Tk here: a color is "#rrggbb", or "#rrggbbaa" when it is not opaque (matplotlib reads
both), and a picture is an array (rows, columns, 3 or 4) of bytes, which `png` writes as the
PNG that `tk.PhotoImage` reads.
"""
from __future__ import annotations

import colorsys
import math
import struct
import zlib

import numpy as np


def hex_color(text: str) -> str | None:
    """`text` as a color: "#rrggbb", or "#rrggbbaa" if it is not opaque. "#" and capitals are
    optional and "#rgb" is taken too. None: not a hex color."""
    text = text.strip().lstrip("#").lower()
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) not in (6, 8) or any(c not in "0123456789abcdef" for c in text):
        return None
    return f"#{text[:6]}" if text[6:] in ("", "ff") else f"#{text}"


def hex_rgba(color: str) -> tuple[int, int, int, int]:
    """(red, green, blue, alpha), 0 to 255 each, of a color as `hex_color` gives it."""
    text = color.lstrip("#") + "ff"
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))


def hex_hsva(color: str) -> tuple[float, float, float, int]:
    """(hue, saturation, value), 0 to 1 each, and alpha (0 to 255) of a color."""
    r, g, b, a = hex_rgba(color)
    return (*colorsys.rgb_to_hsv(r / 255, g / 255, b / 255), a)


def hsva_hex(h: float, s: float, v: float, a: int = 255) -> str:
    """The color of (hue, saturation, value), 0 to 1 each, and alpha (0 to 255)."""
    rgb = "".join(f"{round(c * 255):02x}" for c in colorsys.hsv_to_rgb(h, s, v))
    return f"#{rgb}" if a >= 255 else f"#{rgb}{max(int(a), 0):02x}"


# ------------------------------------------------------------------ the wheel
# A circle that fills a square of `size` pixels: the hue is the angle (red to the right,
# going round against the clock), the saturation is the distance from the center.

def wheel_point(x: float, y: float, size: int, clamp: bool = False) -> tuple[float, float] | None:
    """(hue, saturation) of the pixel (x, y) of the wheel; None outside the circle. `clamp`:
    a point outside counts as the one of the rim in its direction (a drag that left it)."""
    c = (size - 1) / 2
    dx, dy = x - c, c - y  # y grows downwards on screen
    s = math.hypot(dx, dy) / (size / 2)
    if s > 1:
        if not clamp:
            return None
        s = 1.0
    return (math.atan2(dy, dx) / (2 * math.pi)) % 1.0, s


def wheel_xy(h: float, s: float, size: int) -> tuple[float, float]:
    """Pixel of the wheel where (hue, saturation) is."""
    c, r, t = (size - 1) / 2, s * size / 2, h * 2 * math.pi
    return c + r * math.cos(t), c - r * math.sin(t)


def hsv_rgb(h, s, v=1.0) -> np.ndarray:
    """`colorsys.hsv_to_rgb` for arrays: (..., 3), 0 to 1."""
    h, s, v = (np.asarray(a, float)[..., None] for a in np.broadcast_arrays(h, s, v))
    return v * (1 - s + s * np.clip(np.abs((h * 6 + (0, 4, 2)) % 6 - 3) - 1, 0, 1))


def wheel_picture(size: int) -> np.ndarray:
    """The wheel, at full value: transparent outside the circle, its rim smoothed."""
    c = (size - 1) / 2
    y, x = np.mgrid[:size, :size]
    dx, dy = x - c, c - y
    r = np.hypot(dx, dy)
    rgb = hsv_rgb((np.arctan2(dy, dx) / (2 * np.pi)) % 1.0, np.clip(r / (size / 2), 0, 1))
    alpha = np.clip(size / 2 - r + 0.5, 0, 1)
    return (np.dstack([rgb, alpha]) * 255).round().astype(np.uint8)


# ------------------------------------------------------------------ pictures
def checker(w: int, h: int, light, dark, cell: int = 5) -> np.ndarray:
    """The squares that show through a transparent color: (h, w, 3), 0 to 1, of the colors
    `light` and `dark` (red, green, blue: 0 to 1)."""
    y, x = np.mgrid[:h, :w]
    return np.where(((x // cell + y // cell) % 2)[..., None], dark, light).astype(float)


def over(back: np.ndarray, rgb, alpha) -> np.ndarray:
    """The color `rgb` (0 to 1), `alpha` (0 to 1) opaque, over the picture `back`: as bytes."""
    alpha = np.asarray(alpha, float)[..., None]
    return ((back * (1 - alpha) + np.asarray(rgb, float) * alpha) * 255).round().astype(np.uint8)


def png(picture: np.ndarray) -> bytes:
    """A picture, (rows, columns, 3 or 4) of bytes, as a PNG file."""
    h, w, n = picture.shape

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    # each row starts with the byte of its filter: none
    rows = np.hstack([np.zeros((h, 1), np.uint8), picture.reshape(h, w * n)]).tobytes()
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if n == 3 else 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ ready-made colors
def _presets(columns: int = 14) -> tuple[tuple[str, ...], ...]:
    """Rows of the colors offered ready-made: grays from black to white, then each hue vivid,
    pastel and dark."""
    hues = [k / columns for k in range(columns)]
    return (tuple(hsva_hex(0, 0, k / (columns - 1)) for k in range(columns)),
            tuple(hsva_hex(h, 1, 1) for h in hues),
            tuple(hsva_hex(h, 0.4, 1) for h in hues),
            tuple(hsva_hex(h, 1, 0.55) for h in hues))


PRESETS = _presets()
