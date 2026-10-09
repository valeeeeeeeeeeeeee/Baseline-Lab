# Baseline Lab

Windows desktop app (Tkinter + Matplotlib) for lab data analysis. The main window is a blank
board; each file opened is a plot in a small window of its own on it, of one of two analyses:

- **TGA** (`"tga"` in the code): shows a signal (typically DTG). Its "Baseline" tool fits a
  baseline to it, detects peaks and calculates their areas.
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

`scipy` takes longer to import than everything else together, so no module imports it at the
top: its parts come from `scipy_load.scipy_parts()` where they are used, and the main window
loads them on a thread once it is on screen. The imports there are literal ones, which is how
PyInstaller finds them.

`__version__` is `"dev"` in the source and the workflow writes the tag into it, so only a
released exe checks for updates (on opening, on a thread; a notice at the right end of the top
bar). The check needs `ssl` in the exe.

## Layout

```
src/main.py                  entry point
src/baseline_lab/
  gui.py           the main window: Program (the interface) and App; the board and the whole
                   TGA analysis (with its baseline) live here
  rheology_gui.py  FlowCurveView (the plot of one file) and RheologyPanel (the analysis)
  palettes.py      floating tool windows: Palette (calcs / peaks / models / files), CalcWindow,
                   ColorWindow (the color picker)
  colors.py        hex / HSV colors, the color wheel and the pictures of the color picker
  widgets.py       shared widgets: Header, ToolWindow, ChartWindow, log box, picture window
  baselines.py     baseline methods        derivative.py   DTG and derivative-based anchors
  peaks.py         peak detection, areas   rheology.py     models and fitting
  io_txt.py        .txt / .csv / .xlsx reader (DataFile)
  scipy_load.py    scipy, imported on first use (`scipy_parts`)
  updates.py       update check: latest GitHub release against `__version__`
  i18n.py          every UI string, in Portuguese and English
  theme.py         light and dark themes: the colors of the interface, by role
tests/             pytest, numeric core and i18n
```

## How the interface is put together

- `Program` is a mixin with the whole interface. `App(Program, tk.Tk)` is the main window: the
  top bar, the **board** (`self.board`), a blank area, and the sheet tabs at the bottom left.
- **Sheets** (`_sheets`; tabs "Sheet1", "Sheet2"... and "+", which adds one): each sheet is a
  board of its own, and only the open one's is packed (`show_sheet`). The files and controls of
  the analyses are shared by all sheets; a plot belongs to the sheet whose board is its
  master. Each sheet remembers its selected plot, selected again when the sheet is opened; an
  empty sheet has none. Selecting a plot of another sheet opens that sheet.
- **Each sheet has a zoom** (`sheet["zoom"]`, `set_zoom`), set by the `ZoomSlider` at the right
  end of the tabs strip or by Ctrl + wheel over the blank of the board. `ChartWindow.box` is the
  plot's place at 100%; what is on screen is `box` times the zoom plus the sheet's `origin`
  (`fit`), which moves so that the wheel zooms from where the mouse is, and the figure's dpi
  is scaled with it (`PlotCanvas.set_zoom`), so saved pictures do not depend on the zoom. Over
  a TGA plot, Ctrl + wheel zooms its axes instead (`on_scroll`); the plain wheel does
  nothing there.
- The "TGA" and "Rheology" icons (and the "Analyses" menu) ask for files and open each one
  as a plot in a `ChartWindow`: a frame placed on the board, square at first, moved by its
  header and resized by its edges. `_charts` maps `(analysis, file)` to it, bottom to top.
- **One plot is the selected one** (`_selected`, `select_chart`); a click anywhere on a plot
  selects it, and it stays selected until another one is. The tools act on the selected plot
  only: with none, they are grayed. The first click on an unselected plot only selects it
  (`ChartWindow.watch` stops it from reaching the plot).
- Each analysis has **one set of controls and lists**, about one file at a time, and one
  figure per file. Selecting a plot puts its file in the controls (`show_file` →
  `on_file_change`) and points the analysis at that file's figure: `Program._use_plot` swaps
  `fig` / `ax1` / `canvas` (and `fig2` / `ax2` / `canvas2`), `RheologyPanel.view` is the file's `FlowCurveView`. A plot
  is only drawn while its file is in the controls, so every file goes through them once when
  loaded or rebuilt. Mouse events of a TGA canvas that is not `self.canvas` / `canvas2` are ignored.
- **A TGA plot is its signal alone until "Baseline" is asked for** (`open_baseline`; per file,
  `base_on`). Then the baseline and its anchors are drawn on the plot, where they are edited
  with the mouse, and the "Baseline" window comes: the corrected plot on the left and what the
  baseline is made with on the right (method, parameters, anchors, peaks, comparison). Its
  close button turns it off again (`close_baseline`); the window is on screen exactly while
  the selected plot is a TGA one with `base_on` (`_fit_baseline`). The corrected plot is a
  figure of its own (`fig2` / `ax2` / `canvas2`, X shared with `ax1`), one per file, all inside
  that window with only the open file's packed; it is computed always (peaks and areas come
  from it) but drawn only while shown (`_shown`, `_draw_idle`). The columns of a TGA plot are
  in its "Adjustment" window, not there.
- There are **no side columns**. The lists still exist as widgets that are never packed (file
  list, peaks table, calculation log); the tools of the top bar show them in floating windows.
  The options of each analysis live in its own "Adjustment" tool window, which always exists
  and is only hidden when closed.
- Floating windows carry `of_view` ("tga" / "rheology"): they are hidden while the
  selected plot is of the other analysis and come back with one of their own
  (`Program._fit_tools`). "Baseline" and "Peaks" exist only for the TGA, "Models" (the table of
  the fitted models) only for rheology (`TOOL_VIEW`).
- **"Files" is the one tool of no analysis** (`of_view` None): a single window that lists
  every open plot, of both analyses and all sheets (`Program.plot_list`), always available,
  even with no plot selected. A click on a row selects that plot on the board
  (`select_chart`, which opens its sheet), and the row of the selected plot is the marked one.
- `Palette` windows do not get called by the main window: they poll it every 250 ms and redraw
  when what they show changed. Their right-click menus are the main window's own menus.
- `ToolWindow` has no system title bar (`overrideredirect`); its `Header` gives title, minimize
  (only the header stays) and close, and on Windows the owner is set through `ctypes` so it
  stays over the main window. It is resized by dragging its left, right or bottom edge.
- **Resizing is kept light on purpose**: Tk repaints every control at each step of a drag, and a
  figure takes a few tenths of a second to draw. `PlotCanvas` (both plots) only stretches the
  image already drawn and does the real draw when the size stops changing; `ToolWindow` keeps
  `body` at its size while an edge is dragged and fits it on a pause or on release. `body` is
  placed, not packed: the window does not ask for its content's size (`fit_height`).
- **A figure is drawn as seldom as it can be**, and its layout worked out less still. The
  constrained layout is most of a draw: `widgets.Layout` keeps the one it made until what it
  depends on changes (`_state`: size, limits, titles, texts, legend), so whatever else may
  stick out of the axes has to be added there. A `PlotCanvas` is not drawn before it has its
  place in the window (taking it asks for the draw) nor while its `on_screen()` says no: the
  corrected plot of a file that is not showing its baseline, which matplotlib would draw at
  every change of the X axis it shares with the signal. The figures in use while no plot is
  open are on an `UnseenCanvas`, never drawn.
- **Switching the language rebuilds the interface**: `_snapshot()` → destroy the children →
  `_build_ui(state)` → `_restore()`. Anything the user chose must go through the snapshot, or it
  is lost on a language switch (the plots' places on the board and the selected one included).
  Floating windows and picture windows are kept.
- **Switching the theme** ("Options > Theme") rebuilds the interface the same way
  (`set_theme`). No color of the interface is written in a widget: the ones the program paints
  are roles of `theme.COLORS`, read with `theme.color("role")`; the rest is the system's in the
  light theme and given dark colors by `theme.apply` (ttk on "clam", and the option database
  for classic widgets that do not say theirs). The windows kept through a rebuild take the new
  colors in their `paint()`. The plots stay on white in both themes.
- **A rebuild is not seen until it ends** (`Program._rebuild`, inside `widgets.still`): it
  takes seconds and Tk draws along the way, so each window would change at a time of its own.
  The main window and every floating window are covered by a picture of themselves
  (`PrintWindow`, no PIL), a `ToolWindow` made meanwhile is born transparent, and the tools
  are fitted to the selected plot once, at the end (`_rebuilding`); then the pictures go and
  everything shows together. `set_theme` starts it before `theme.apply`, which the ttk
  controls follow at once, and sets the title bars just before it ends. Hiding a window from
  the system instead (`WM_SETREDRAW`) was tried: Tk then takes it for a withdrawn one.
- **"Back" and "Forward"** (the two arrows at the left of the "Adjustment" icon; `go_back`,
  `go_forward`) go through the changes made. Nothing tells the history what changed: like
  the palettes, `_history_tick` polls `_history_state()` (the files of both analyses, each
  file's choices, the sheets, the plots' places) and takes a new step when it differs and has
  stopped changing, so a drag is one step. What is only a way of looking is not a change
  (`_history_key`): the selected plot, the open sheet, the zooms, the peaks marked on the
  table. Going to a state puts back only what differs, each file through the controls of its
  analysis (`_history_apply`); when the files or the sheets differ, the interface is rebuilt
  as on a language switch (`_history_rebuild`). A new per-file choice is part of the history
  once it is in `_file_state()` (TGA) or `RheologyPanel.choices()`.
- Per-file settings (columns, method, parameters, adjusted anchors, noise marks, painted peaks,
  calculation log, marked peak, zoom) are stored in `_file_states` and swapped on a file switch.
- Several peaks can be marked in the peaks table (Ctrl + click); the right-click menu then acts
  on all of them (`_menu_events`), except "Calculate area", which is about the clicked one.
- A peak is painted from the right-click menu of the peaks table ("Color", `paint_peak`;
  "Color all", `paint_all`, paints every peak of the table), with a color taken in
  `palettes.ColorWindow`: a color picker with a hue/saturation wheel, a value
  bar, a sample, the hex field, ready-made colors and an alpha slider. It keeps its color as
  HSV + alpha and gives `on_pick` "#rrggbb", or "#rrggbbaa" when not opaque (matplotlib reads
  both; Tk only the first). Its pictures (wheel, gradients) are numpy arrays written as PNG by
  `colors.png`, with no PIL; everything about colors that needs no Tk is in `colors.py`.
  `peak_colors` keeps (an X inside the peak, the color), so the color follows the peak when
  its ends change; it is dropped with the noise marks, when the data changes.

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
