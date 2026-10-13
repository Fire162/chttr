#!/usr/bin/python3
"""Read the focused text field through the accessibility bus (AT-SPI).

Runs under the *system* Python (needs PyGObject with Atspi 2.0, which most
desktops ship), like overlay.py. Prints one JSON object and exits:

    {"app": "...", "before": "...", "after": "...", "caret": 123, "count": 456}

or {} when nothing focused is a readable text field. Never writes anything.

    atspi_helper.py [--timeout SECONDS] [--before N] [--after N]
"""

import argparse
import json
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=0.6)
    ap.add_argument("--before", type=int, default=600)
    ap.add_argument("--after", type=int, default=200)
    a = ap.parse_args()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        print("{}")
        return 0

    deadline = time.monotonic() + a.timeout
    S = Atspi.StateType

    def states(obj):
        try:
            return obj.get_state_set()
        except Exception:  # noqa: BLE001 - the app may vanish mid-walk
            return None

    def find(obj, depth):
        if obj is None or depth > 40 or time.monotonic() > deadline:
            return None
        st = states(obj)
        if st is None:
            return None
        if st.contains(S.FOCUSED) and (st.contains(S.EDITABLE) or st.contains(S.MULTI_LINE)):
            return obj
        try:
            n = min(obj.get_child_count(), 400)
        except Exception:  # noqa: BLE001
            return None
        for i in range(n):
            try:
                child = obj.get_child_at_index(i)
            except Exception:  # noqa: BLE001
                continue
            cst = states(child)
            if cst is None or not (cst.contains(S.SHOWING) or cst.contains(S.FOCUSED)):
                continue
            hit = find(child, depth + 1)
            if hit is not None:
                return hit
        return None

    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            windows = [app.get_child_at_index(j) for j in range(app.get_child_count())]
        except Exception:  # noqa: BLE001
            continue
        for win in windows:
            st = states(win)
            if st is None or not st.contains(S.ACTIVE):
                continue
            field = find(win, 0)
            if field is None:
                continue
            try:
                count = field.get_character_count()
                caret = max(0, min(field.get_caret_offset(), count))
                before = field.get_text(max(0, caret - a.before), caret)
                after = field.get_text(caret, min(count, caret + a.after))
            except Exception:  # noqa: BLE001 - not a text field after all
                continue
            print(json.dumps({"app": app.get_name() or "", "before": before, "after": after,
                              "caret": caret, "count": count}))
            return 0
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
