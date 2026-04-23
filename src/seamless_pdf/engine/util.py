"""Small helpers shared by the engine modules."""
from __future__ import annotations

import re
from typing import Optional, Sequence


class EditError(Exception):
    """A user-facing failure (bad id, unsupported object, missing dependency)."""


def rect_list(r) -> list[float]:
    x0, y0, x1, y1 = list(r)
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(value) -> Optional[tuple[float, float, float]]:
    """Accept '#rrggbb', [r,g,b] in 0..1 or 0..255. None passes through."""
    if value is None:
        return None
    if isinstance(value, str):
        m = _HEX.match(value.strip())
        if not m:
            raise EditError(f"colour {value!r} is not #rrggbb")
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore
    vals = [float(v) for v in value]
    if len(vals) != 3:
        raise EditError("colour needs three components")
    if any(v > 1.0 for v in vals):
        vals = [v / 255.0 for v in vals]
    return tuple(max(0.0, min(1.0, v)) for v in vals)  # type: ignore


def rgb_to_hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb[:3])


def as_rect(value):
    from .geom import Rect
    try:
        r = Rect.of(value) if len(value) == 4 else None
    except Exception:
        r = None
    if r is None or r.is_empty:
        raise EditError(f"invalid rect {value!r}; expected [x0, y0, x1, y1] with x1>x0, y1>y0")
    return r
