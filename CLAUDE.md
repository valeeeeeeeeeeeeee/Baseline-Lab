# Baseline Lab

Windows desktop app (Tkinter + Matplotlib) for lab data analysis. Two analyses share one window:

- **Baseline**: reads a signal (typically DTG), fits a baseline, detects peaks and calculates
  their areas.
- **Rheology**: fits a flow curve (shear rate × shear stress) with the Newton, Bingham,
  power-law, Herschel-Bulkley and Casson models and ranks them by AICc.

The user works in Brazilian Portuguese: talk to them in Portuguese. Code, comments and this file
are in English.

## Commands

```
python src/main.py            # run the app
python -m pytest -q           # tests (core only, no GUI is opened)
```

Dependencies: `numpy`, `scipy`, `matplotlib` (see `requirements.txt`). The release build is a
PyInstaller one-file exe made by `.github/workflows/release.yml`; it excludes unused modules by
name, so a new standard-library or third-party import may need to be checked against that list.

## Layout

```
src/main.py                  entry point
src/baseline_lab/
  gui.py           the windows: Program (shared interface), App (main), ViewWindow (a file
                   in its own window); the whole baseline view lives here
  rheology_gui.py  FlowCurveView (plot + models table) and RheologyPanel
  palettes.py      floating tool windows: Palette (calcs / peaks / files), CalcWindow
  widgets.py       shared widgets: Header, ToolWindow, log box, picture window
  baselines.py     baseline methods        derivative.py   DTG and derivative-based anchors
  peaks.py         peak detection, areas   rheology.py     models and fitting
  io_txt.py        .txt / .csv / .xlsx reader (DataFile)
  i18n.py          every UI string, in Portuguese and English
tests/             pytest, numeric core and i18n
```

## How the interface is put together

- `Program` is a mixin with the whole interface. `App(Program, tk.Tk)` is the main window (top
  bar, both analyses); `ViewWindow(Program, tk.Toplevel)` shows one file with a single analysis
  and no top bar (`_solo` is set), keeping its side columns docked.
- The main window has **no side columns**. The lists still exist as widgets that are never
  packed (file list, peaks table, calculation log); the tools of the top bar show them in
  floating windows. The baseline options live in the "Adjustment" tool window, which always
  exists and is only hidden when closed.
- Floating windows carry `of_view` ("baseline" / "rheology"): they are hidden while the other
  analysis is open and come back with their own (`Program._fit_tools`). "Peaks" and
  "Adjustment" exist only for the baseline.
- `Palette` windows do not get called by the main window: they poll it every 250 ms and redraw
  when what they show changed. Their right-click menus are the main window's own menus.
- `ToolWindow` has no system title bar (`overrideredirect`); its `Header` gives title, minimize
  (only the header stays) and close, and on Windows the owner is set through `ctypes` so it
  stays over the main window.
- **Switching the language rebuilds the interface**: `_snapshot()` → destroy the children →
  `_build_ui(state)` → `_restore()`. Anything the user chose must go through the snapshot, or it
  is lost on a language switch. Floating windows, `ViewWindow`s and picture windows are kept.
- Per-file settings (columns, method, parameters, adjusted anchors, noise marks, calculation
  log) are stored in `_file_states` and swapped on a file switch.

## Conventions

- **Strings**: every UI text goes through `tr("key")`, with `STRINGS[key] = (pt, en)` in
  `i18n.py`. Portuguese is the source language. Tests require every literal `tr("key")` to exist
  and the English text to contain no Portuguese; build dynamic keys with an f-string
  (`tr(f"rheo_m_{key}")`) so the test's regex does not pick up a partial key.
- **Text that outlives a language switch is stored as data**, not as rendered text (see the
  calculation log entries and `_log_lines`).
- **Comments** are in English and say why, not what. Match the density and tone of the
  surrounding code.
- **Line endings**: the `.py` files under `src/` are CRLF (`core.autocrlf=true`). Preserve them;
  `sed -i` in Git Bash strips the CRs.
- Internal names of methods and of event fields are Portuguese (`"Derivada 1ª + 2ª"`,
  `inicio` / `pico` / `fim`); what the user sees is translated by `i18n.method_name` and `tr`.
- Tk pitfalls already met here: do not destroy a canvas from inside its own item binding (run
  the command with `after`); `savefig` at another dpi leaves the figure drawn at that
  resolution, so call `canvas.draw()` afterwards; an embedded widget wider than a `Text` makes
  it scroll sideways.

## Checking a change

Tests cover the numeric core only, so a UI change is checked by driving the real app from a
script: build `App(lang="pt")`, feed it **synthetic** data through `io_txt.parse_text`, call the
handlers or generate events, and read the widgets back. A window opened this way appears on the
user's screen: keep such runs short, and if a screenshot is needed, capture only the app's own
rectangle while it is on top.

## Rules for this repository

- `examples/` holds sensitive lab data. Never commit, publish or copy it elsewhere; use
  synthetic data for checks.
- Commit and push only when asked. No Claude attribution or co-author lines in commits or PRs.
