"""Tolerant reading of .txt files exported by instruments (and of .xlsx spreadsheets).

Handles:
- varied delimiters (tab, ;, comma, spaces)
- decimal comma (Brazilian / European convention)
- header/metadata lines before the data
- common encodings (utf-8, utf-16, latin-1)
- files with more than two columns (the user chooses X and Y)
- .xlsx spreadsheets (e.g. exported from Origin), read without decompressing past a limit
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .i18n import tr

ENCODINGS = ("utf-8-sig", "utf-16", "latin-1")
_NUM = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


@dataclass
class DataFile:
    path: Path
    columns: list[str]
    data: np.ndarray  # shape (n_rows, n_columns)
    header_lines: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    def xy(self, x_col: int = 0, y_col: int = 1) -> tuple[np.ndarray, np.ndarray]:
        x = self.data[:, x_col].astype(float)
        y = self.data[:, y_col].astype(float)
        ok = np.isfinite(x) & np.isfinite(y)  # spreadsheets may have columns of different lengths
        x, y = x[ok], y[ok]
        order = np.argsort(x, kind="stable")  # baselines require increasing x
        return x[order], y[order]


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # utf-16 only with a BOM (otherwise latin-1 is read as garbage)
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _detect_delimiter(lines: list[str]) -> str | None:
    """Picks the delimiter that yields the most fully numeric lines with the same field count.

    Counting only numeric lines keeps the decimal comma from being taken as a delimiter
    (e.g. "25,0 10,5" separated by a space)."""
    best, best_score = None, 0
    for delim in ("\t", ";", ",", None):  # None = whitespace
        counts = [len(toks) for toks in (ln.split(delim) for ln in lines)
                  if len(toks) >= 2 and all(_to_float(t) is not None for t in toks)]
        if not counts:
            continue
        common = max(set(counts), key=counts.count)
        score = counts.count(common)
        if score > best_score:
            best, best_score = delim, score
    return best


def _to_float(tok: str) -> float | None:
    tok = tok.strip().strip('"')
    if not tok or not _NUM.match(tok):
        return None
    return float(tok.replace(",", "."))


def parse_text(text: str, path: Path | str = "memoria.txt") -> DataFile:
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError(tr("err_empty"))

    # Delimiter estimated from the last lines (most likely to be data)
    sample = lines[-min(len(lines), 50):]
    delim = _detect_delimiter(sample)

    numeric: list[tuple[int, list[float]]] = []
    for i, ln in enumerate(lines):
        toks = ln.split(delim) if delim else ln.split()
        vals = [_to_float(t) for t in toks]
        if all(v is not None for v in vals) and len(vals) >= 2:
            numeric.append((i, vals))  # type: ignore[arg-type]
    if not numeric:
        raise ValueError(tr("err_no_numeric"))
    # column count = the most frequent one (stray numeric lines in the header do not count)
    lens = [len(v) for _, v in numeric]
    ncols = max(set(lens), key=lens.count)
    first_data_idx = next(i for i, v in numeric if len(v) == ncols)
    rows = [v for i, v in numeric if len(v) == ncols and i >= first_data_idx]

    header = lines[:first_data_idx]
    columns = [f"Coluna {j + 1}" for j in range(ncols)]
    sig = _ta_signal_names(header)  # TA Instruments (Q600, Q500, Q20…): linhas "Sig1\tTime (min)"
    if len(sig) == ncols:
        columns = sig
    elif header:  # last header line with field count = column count
        toks = [t.strip().strip('"') for t in (header[-1].split(delim) if delim else header[-1].split())]
        if len(toks) == ncols and all(toks):
            columns = toks
    columns = [_fix_degree(c) for c in columns]
    return DataFile(Path(path), columns, np.asarray(rows, dtype=float), header)


_SIG = re.compile(r"^Sig(\d+)\s+(.+)$")


def _ta_signal_names(header: list[str]) -> list[str]:
    found = {}
    for ln in header:
        m = _SIG.match(ln.strip())
        if m:
            found[int(m.group(1))] = m.group(2).strip()
    return [found[k] for k in sorted(found)] if found else []


def _fix_degree(name: str) -> str:
    # TA exports often have a corrupted "°" (e.g. "�C", "Â°C")
    name = re.sub(r"(�+|Â°|º)(?=[CFK]\b)", "°", name)
    return re.sub(r"(�+|Âµ)(?=[VWAgLm]\b)", "µ", name)


# ------------------------------------------------------------------------ .xlsx
XLSX_MAX_BYTES = 50_000_000  # cap per decompressed part: protects against a "zip bomb"
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _zip_read(z: zipfile.ZipFile, name: str) -> bytes:
    """Reads a part of the .xlsx, stopping at the cap whatever the declared size."""
    with z.open(name) as f:
        data = f.read(XLSX_MAX_BYTES + 1)
    if len(data) > XLSX_MAX_BYTES:
        raise ValueError(f"{name}: > {XLSX_MAX_BYTES // 1_000_000} MB")
    return data


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path: str | Path) -> DataFile:
    """First sheet of the spreadsheet. Text rows before the numbers become the column names
    (the 1st is the name, the following ones, as units, go in parentheses)."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(_zip_read(z, "xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(_zip_read(z, "xl/workbook.xml"))
        rid = wb.find("m:sheets/m:sheet", _NS).get(f"{{{_NS['r']}}}id")
        rels = ET.fromstring(_zip_read(z, "xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.findall("rel:Relationship", _NS) if r.get("Id") == rid)
        sheet = ET.fromstring(_zip_read(z, "xl/" + target.lstrip("/").removeprefix("xl/")))

    rows: list[dict[int, object]] = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, object] = {}
        for c in row.findall("m:c", _NS):
            t, v = c.get("t"), c.find("m:v", _NS)
            if t == "s" and v is not None:
                cells[_col_index(c.get("r"))] = shared[int(v.text)]
            elif t == "inlineStr":
                cells[_col_index(c.get("r"))] = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            elif v is not None and v.text is not None:
                try:
                    cells[_col_index(c.get("r"))] = float(v.text)
                except ValueError:
                    cells[_col_index(c.get("r"))] = v.text
        if cells:
            rows.append(cells)
    used = sorted({k for r in rows for k in r})
    is_data = [sum(isinstance(r.get(k), float) for k in used) >= 2 and
               not any(isinstance(r.get(k), str) for k in used) for r in rows]
    if not any(is_data):
        raise ValueError(tr("err_no_numeric"))
    first = is_data.index(True)
    header, data = rows[:first], [r for r, ok in zip(rows[first:], is_data[first:]) if ok]
    columns = []
    for j, k in enumerate(used):
        parts = [str(r[k]).strip() for r in header if str(r.get(k, "")).strip()]
        name = parts[0] if parts else f"Coluna {j + 1}"
        columns.append(_fix_degree(name + "".join(f" ({u})" for u in parts[1:])))
    arr = np.array([[r.get(k, np.nan) for k in used] for r in data], dtype=float)
    header_lines = ["\t".join(str(r.get(k, "")) for k in used) for r in header]
    return DataFile(path, columns, arr, header_lines)


def read_file(path: str | Path) -> DataFile:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm") or zipfile.is_zipfile(path):
        return read_xlsx(path)
    return parse_text(_read_text(path), path)
