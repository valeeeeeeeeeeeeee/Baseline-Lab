# Baseline Lab

Desktop app to calculate and compare baselines from `.txt` files already exported
from the instruments (independent of Origin).

## Install and run
    pip install -r requirements.txt
Then **double-click `Open Baseline Lab.bat`** (or run `python main.py`).
Dragging .txt files onto the `.bat` opens the app with them already loaded.
(Tkinter ships with Python on Windows/macOS; on Linux: `sudo apt install python3-tk`.)

## Quick start
1. **📂 Import .txt** (Ctrl+O) — you can select several files. Also accepts **.xlsx**
   spreadsheets (e.g. exported from Origin): the 1st sheet is read, and the text rows at the
   top become the column names and units.
2. The baseline is calculated immediately: columns, delimiter and decimal comma are detected;
   in thermal analysis X is the temperature and Y is the DTG (if the file only has the mass,
   the DTG is calculated automatically).
3. Right click a peak in the table → **Calculate area**: the result goes to **Calculations**.

Each file is independent: columns, method, parameters, adjusted anchors, peaks marked as
noise and the **Calculations** log are stored per file. Switching files in the list brings
it back exactly as it was; a new file starts with the default values.

Method, columns, parameters, manual anchors and comparison are under **⚙ Advanced options**.
Each option has an **ⓘ** icon: hover over it to see what it does.

**Language** (next to Advanced options): Português (BR) or English (EN). The switch is
immediate, keeps files, columns, method, parameters and anchors, and is saved for the next
session (`~/.baseline_lab.json`).

## Peaks only
In **⚙ Advanced options → Peak detection**, tick **Peaks only**. Signal near 0 is ambient
noise: the baseline there becomes 0 and only the peaks are considered. The same rule applies
to the whole signal:
1. the ambient noise (σ) is estimated automatically from signal − baseline (MAD, discarding
   the peaks);
2. the baseline within **k·σ** of 0 (default k = 3) becomes exactly 0, with a ramp up to 2k·σ
   (no step); only wide stretches (≥ 3 % of the points) are zeroed, not isolated points;
3. a peak is a stretch above k·σ with height ≥ **% of the largest peak** (default 5 %); each
   peak extends until the signal returns to the noise, without cutting off the feet — delimited
   and measured on the same final baseline;
4. the method's anchors are discarded: each peak gets **one anchor at each foot** (value of
   the smoothed signal, or 0 if it is near 0), and the baseline follows the shape of the
   method's baseline, shifted to pass through these points, going to 0 in the ambient noise
   stretches;
5. outside the peaks the corrected signal is 0; peaks cut off at the edge of the data are
   ignored (optional).

The peak feet can be dragged on the plot to adjust where each peak starts and ends.

The events table gets one row per peak. Adjust k and the minimum height under
**⚙ Advanced options → Peak detection**.

## Baseline from the derivative (thermal analysis)
In the "Derivative" methods, the anchors are found automatically (Savitzky-Golay after
resampling X at a constant step) and joined by straight lines or a spline. Options:
- **Baseline does not cross the signal**: adds anchors where the line would pass above the
  curve.
- **Manual adjustment**: in any method, click the plot to place an anchor, drag to move it
  and right click it to delete it. The method and parameters still apply; "Undo adjustments"
  goes back to the method's anchors. In the polynomial method, which has no anchors, the
  curve is simply shifted to pass through the placed anchors.
- **Calculate DTG = −dY/dX**: when the file only has the mass; the result is in %/°C as in TA.

The **Events** table integrates the signal between consecutive anchors. For a DTG in %/°C:
- *Event* = mass loss (%) above the baseline;
- *Total* = loss over the interval (event + baseline) = TGA drop between start and end.

TA Instruments files (Q600, Q500…): the column names come from the `Sig1…SigN` lines and
X/Y are chosen automatically (Temperature × Deriv. Weight).

## How to use
1. **Open .txt files** (several at once). Delimiter, decimal comma, header and encoding are
   detected automatically. Choose the X and Y columns.
2. Choose the **method** and adjust the controls (the plot updates live).
3. Tick **Compare all methods** to overlay the baselines and see the corrected area of each.
4. **Anchor** methods: left click on the plot adds a point, **dragging** an anchor moves it
   along the signal (the cursor turns into ↔ over it), right click removes it. The zoom is
   kept.
5. Click a row in the **Results** table to mark that peak in red on the corrected plot;
   clicking the same row again unmarks it (Ctrl/Shift+click marks several; Esc unmarks all).

## Origin reference
`examples/*.xlsx` holds a DTG processed in Origin (Peak Analyzer): columns B–C = original
curve, D–E = signal with the baseline subtracted, F–G = Origin's baseline. The default
calculation reproduces Origin's:
1. **Anchors** from the derivative ("Derivative 1st + 2nd": smoothing 0.5 %, y'' tolerance
   5 %, y' tolerance 2 %, 16 anchors), joined by straight lines.
2. **Do not cross the signal** only at the peak feet: a valley next to a peak becomes an
   anchor; a negative spike in the middle of the noise is ignored (the baseline passes over
   it, as in Origin).
3. **Ends**: if the end of the data only has noise, the anchor goes at the median level of
   the signal there, not at the last point (which usually drops because of an end-of-run
   artifact).
4. **Events**: with the baseline subtracted, the peaks are searched in the corrected signal
   (above k·σ of the noise and with a minimum height, under ⚙ Advanced options → Peak
   detection) and integrated foot to foot; noise ripples do not become events.

On the reference sample: baseline within 1.0e-3 %/°C (RMS) of Origin's, the same 2 peaks,
areas +0.6 % and −2.7 % from Origin's. `tests/test_origin_reference.py` fails if this gets
worse.

## Methods
| Method | When to use |
|---|---|
| **Derivative 1st + 2nd** (default) | Anchors where y' ≈ 0 and y'' ≈ 0 (flat stretches). Recommended for DTG. Defaults calibrated against Origin (see above) |
| Derivative 2nd (zeros) | Anchors where the curvature ≈ 0 (Origin's Peak Analyzer method) |
| Derivative 2nd (peaks) | Anchors at the maxima of y'' (Origin). On broad peaks it cuts the peak base |
| Iterative polynomial | Smooth background and well-defined peaks |

Note: on curves with steps (e.g. TGA) the automatic methods can diverge a lot; use the
comparison and validate with anchors.

## Structure
- `baseline_lab/io_txt.py` .txt reading · `derivative.py` derivative anchors and events · `baselines.py` algorithms · `gui.py` interface · `i18n.py` PT/EN texts
- `tests/` core tests (`python -m pytest -q`) · `examples/` test files
