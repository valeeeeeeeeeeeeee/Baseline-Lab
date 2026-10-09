"""scipy, imported when it is first needed.

Importing the parts of it the program uses takes longer than everything else it imports put
together, and the window needs none of them to open: the main window loads them on a thread
once it is on screen, and whoever needs them before that waits for them here.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()  # one import at a time: two threads importing scipy may meet halfway


def scipy_parts():
    """The scipy package, with `interpolate`, `optimize` and `signal` loaded."""
    with _lock:
        import scipy.interpolate
        import scipy.optimize
        import scipy.signal
    return scipy
