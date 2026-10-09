"""Colors of the color picker: hex text, HSV, the wheel and its pictures."""
import struct
import zlib

import numpy as np
import pytest

from baseline_lab import colors as co


def test_hex_color_reads_every_form():
    assert co.hex_color("1A2b3C") == "#1a2b3c"
    assert co.hex_color(" #f80 ") == "#ff8800"
    assert co.hex_color("#11223380") == "#11223380"
    assert co.hex_color("#112233FF") == "#112233"  # opaque: no alpha written
    for bad in ("", "#", "#12345", "zzzzzz", "#1234567", "#1122334455"):
        assert co.hex_color(bad) is None


def test_hex_rgba():
    assert co.hex_rgba("#ff8000") == (255, 128, 0, 255)
    assert co.hex_rgba("#ff800040") == (255, 128, 0, 64)


@pytest.mark.parametrize("color", ["#000000", "#ffffff", "#808080", "#d62728", "#2563eb",
                                   "#0d9488", "#12345678", "#fedcba01"])
def test_hsv_round_trip_is_stable(color):
    assert co.hsva_hex(*co.hex_hsva(color)) == color


def test_hsva_hex():
    assert co.hsva_hex(0, 1, 1) == "#ff0000"
    assert co.hsva_hex(1 / 3, 1, 1) == "#00ff00"
    assert co.hsva_hex(0.6, 0, 1) == "#ffffff"   # no saturation: the hue does not matter
    assert co.hsva_hex(0.6, 1, 0) == "#000000"
    assert co.hsva_hex(0, 1, 1, 128) == "#ff000080"


def test_wheel_point():
    size, c = 141, 70
    h, s = co.wheel_point(c, c, size)
    assert s == 0                                  # the center: white
    assert co.wheel_point(c + 35, c, size) == pytest.approx((0, 35 / 70.5))  # 0 degrees: red
    h, s = co.wheel_point(c, c - 70, size)         # up: a quarter of the way round
    assert (h, s) == pytest.approx((0.25, 70 / 70.5))
    assert co.wheel_point(c - 20, c, size)[0] == pytest.approx(0.5)
    assert co.wheel_point(c, c + 20, size)[0] == pytest.approx(0.75)


def test_wheel_point_outside_the_circle():
    size = 140
    assert co.wheel_point(0, 0, size) is None      # a corner of the square
    assert co.wheel_point(300, 69.5, size) is None
    h, s = co.wheel_point(300, 69.5, size, clamp=True)
    assert (h, s) == pytest.approx((0, 1))


@pytest.mark.parametrize("h,s", [(0, 0.5), (0.2, 1), (0.61, 0.33), (0.99, 0.8)])
def test_wheel_xy_is_the_inverse(h, s):
    assert co.wheel_point(*co.wheel_xy(h, s, 140), 140) == pytest.approx((h, s))


def test_hsv_rgb_matches_colorsys():
    import colorsys
    for h, s, v in [(0, 1, 1), (0.1, 0.5, 0.7), (0.5, 1, 1), (0.83, 0.2, 0.4), (0.3, 0, 1)]:
        assert co.hsv_rgb(h, s, v) == pytest.approx(colorsys.hsv_to_rgb(h, s, v))


def test_wheel_picture():
    size = 141
    pic = co.wheel_picture(size)
    assert pic.shape == (size, size, 4) and pic.dtype == np.uint8
    assert tuple(pic[70, 70]) == (255, 255, 255, 255)   # the center
    r, g, b, a = pic[70, 139]                           # to the right: red, nearly pure
    assert r == 255 and g == b and g < 10 and a == 255
    assert pic[0, 0, 3] == 0                            # outside the circle: transparent
    r, g, b, a = pic[10, 70]                            # up: between yellow and green
    assert g == 255 and b < 60 and 100 < r < 160 and a == 255


def test_over_and_checker():
    back = co.checker(10, 10, (1, 1, 1), (0.5, 0.5, 0.5), cell=5)
    assert back.shape == (10, 10, 3)
    assert tuple(back[0, 0]) == (1, 1, 1) and tuple(back[0, 5]) == (0.5, 0.5, 0.5)
    assert tuple(back[5, 5]) == (1, 1, 1)
    full = co.over(back, (1, 0, 0), np.ones((10, 10)))
    assert (full == (255, 0, 0)).all()
    none = co.over(back, (1, 0, 0), np.zeros((10, 10)))
    assert tuple(none[0, 0]) == (255, 255, 255) and tuple(none[0, 5]) == (128, 128, 128)
    half = co.over(back, (1, 0, 0), np.full((10, 10), 0.5))
    assert tuple(half[0, 0]) == (255, 128, 128)


@pytest.mark.parametrize("n", [3, 4])
def test_png_holds_the_picture(n):
    pic = np.arange(5 * 7 * n, dtype=np.uint8).reshape(5, 7, n)
    data = co.png(pic)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, i = {}, 8
    while i < len(data):
        size, tag = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + size]
        assert struct.unpack(">I", data[i + 8 + size:i + 12 + size])[0] == zlib.crc32(tag + body)
        chunks[tag] = body
        i += 12 + size
    assert struct.unpack(">IIBB", chunks[b"IHDR"][:10]) == (7, 5, 8, 2 if n == 3 else 6)
    rows = np.frombuffer(zlib.decompress(chunks[b"IDAT"]), np.uint8).reshape(5, 1 + 7 * n)
    assert (rows[:, 0] == 0).all() and (rows[:, 1:].reshape(5, 7, n) == pic).all()


def test_presets():
    assert len(co.PRESETS) == 4 and all(len(row) == 14 for row in co.PRESETS)
    assert all(co.hex_color(c) == c for row in co.PRESETS for c in row)
    assert co.PRESETS[0][0] == "#000000" and co.PRESETS[0][-1] == "#ffffff"
    assert co.PRESETS[1][0] == "#ff0000"
