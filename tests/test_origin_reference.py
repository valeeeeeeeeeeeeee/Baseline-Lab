"""Origin (Peak Analyzer) reference on a DTG: spreadsheet in examples/*.xlsx with
columns B-C = original curve, D-E = signal with the baseline subtracted, F-G = Origin's baseline.

The default method ("Derivada 1ª + 2ª") must reproduce that baseline and the peak areas."""
import zipfile
from pathlib import Path

import numpy as np
import pytest

from baseline_lab import baselines as bl
from baseline_lab import io_txt
from baseline_lab import peaks as pk
from baseline_lab.io_txt import read_file

EXAMPLES = Path(__file__).parent.parent / "examples"


def _reference():
    for p in sorted(EXAMPLES.glob("*.xlsx")):
        df = read_file(p)
        if "Baseline_Data Y1" in df.columns:
            return df
    return None


REF = _reference()
needs_ref = pytest.mark.skipif(REF is None, reason="Origin reference spreadsheet missing")


@needs_ref
def test_spreadsheet_read_with_units():
    assert REF.columns[:2] == ["Temperatura (°C)", "Deriv. Weight (%/°C)"]
    assert REF.data.shape[1] == 6 and REF.data.shape[0] > 10000
    x, y = REF.xy(0, 1)
    _, sub = REF.xy(2, 3)
    _, base = REF.xy(4, 5)
    assert np.allclose(y - base, sub, atol=2e-5)  # the spreadsheet itself is consistent


@needs_ref
def test_default_reproduces_origin_baseline():
    x, y = REF.xy(0, 1)
    _, base_origin = REF.xy(4, 5)
    base, corr, _ = bl.compute_full("Derivada 1ª + 2ª", x, y)
    assert np.sqrt(np.mean((base - base_origin) ** 2)) < 1.3e-3
    tail = x > 700  # noise only: the baseline must not dive into the negative spikes
    assert np.sqrt(np.mean((base[tail] - base_origin[tail]) ** 2)) < 1.3e-3


@needs_ref
def test_events_equal_to_origin_s():
    # peaks searched the same way on our corrected signal and on Origin's subtracted one
    x, y = REF.xy(0, 1)
    _, base_origin = REF.xy(4, 5)
    _, corr, _ = bl.compute_full("Derivada 1ª + 2ª", x, y)
    ours, _ = pk.find_events(x, corr)
    # in Origin the 1st peak starts at the 1st point (its baseline is below the signal there)
    origin, _ = pk.find_events(x, y - base_origin, skip_edges=False)
    assert len(ours) == len(origin) == 2  # no noise events in the tail
    for a, b in zip(ours, origin):
        assert a["pico"] == pytest.approx(b["pico"], abs=2)
        assert a["area"] == pytest.approx(b["area"], rel=0.03)


def _xlsx(path, lines):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    rows = "".join(
        f'<row r="{i}">' + "".join(
            f'<c r="{"AB"[j]}{i}" t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str)
            else f'<c r="{"AB"[j]}{i}"><v>{v}</v></c>' for j, v in enumerate(ln)) + "</row>"
        for i, ln in enumerate(lines, 1))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="a" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{rows}</sheetData></worksheet>")


def test_simple_xlsx(tmp_path):
    p = tmp_path / "a.xlsx"
    _xlsx(p, [["T", "Massa"], ["°C", "%"]] + [[25.0 + i, 100.0 - i] for i in range(10)])
    df = read_file(p)
    assert df.columns == ["T (°C)", "Massa (%)"] and df.data.shape == (10, 2)


def test_xlsx_too_large_is_refused(tmp_path, monkeypatch):
    # "zip bomb": reading stops at the cap instead of decompressing everything into memory
    p = tmp_path / "bomb.xlsx"
    _xlsx(p, [["T", "Y"]] + [[float(i), 0.0] for i in range(5000)])
    monkeypatch.setattr(io_txt, "XLSX_MAX_BYTES", 10_000)
    with pytest.raises(ValueError, match="MB"):
        read_file(p)
