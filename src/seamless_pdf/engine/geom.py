"""2-D geometry with PDF's row-vector convention: p' = p x M.

`A * B` means "apply A, then B", exactly as PDF composes matrices (the
`cm` operator computes CTM' = M x CTM).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Matrix:
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    @staticmethod
    def of(v: Sequence[float]) -> "Matrix":
        return Matrix(*[float(x) for x in v])

    @staticmethod
    def translate(x: float, y: float) -> "Matrix":
        return Matrix(1, 0, 0, 1, x, y)

    @staticmethod
    def scale(sx: float, sy: float | None = None) -> "Matrix":
        return Matrix(sx, 0, 0, sx if sy is None else sy, 0, 0)

    @staticmethod
    def rotate(deg: float) -> "Matrix":
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        return Matrix(c, s, -s, c, 0, 0)

    def __mul__(self, o: "Matrix") -> "Matrix":
        return Matrix(self.a * o.a + self.b * o.c, self.a * o.b + self.b * o.d,
                      self.c * o.a + self.d * o.c, self.c * o.b + self.d * o.d,
                      self.e * o.a + self.f * o.c + o.e, self.e * o.b + self.f * o.d + o.f)

    def inverse(self) -> "Matrix":
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            raise ValueError("singular matrix")
        ia, ib, ic, id_ = self.d / det, -self.b / det, -self.c / det, self.a / det
        return Matrix(ia, ib, ic, id_, -(self.e * ia + self.f * ic), -(self.e * ib + self.f * id_))

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.a + y * self.c + self.e, x * self.b + y * self.d + self.f)

    def apply_vec(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.a + y * self.c, x * self.b + y * self.d)

    @property
    def det(self) -> float:
        return self.a * self.d - self.b * self.c

    def list(self) -> list[float]:
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    def __iter__(self):
        return iter(self.list())


IDENTITY = Matrix()


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def of(v: Sequence[float]) -> "Rect":
        x0, y0, x1, y1 = [float(t) for t in v]
        return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    @staticmethod
    def bound(points: Iterable[tuple[float, float]]) -> "Rect":
        pts = list(points)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Rect(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def empty() -> "Rect":
        return Rect(math.inf, math.inf, -math.inf, -math.inf)

    @property
    def is_empty(self) -> bool:
        return not (self.x1 > self.x0 and self.y1 > self.y0)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return 0.0 if self.is_empty else self.width * self.height

    def __or__(self, o: "Rect") -> "Rect":
        return Rect(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    def __and__(self, o: "Rect") -> "Rect":
        return Rect(max(self.x0, o.x0), max(self.y0, o.y0), min(self.x1, o.x1), min(self.y1, o.y1))

    def intersects(self, o: "Rect") -> bool:
        return self.x0 < o.x1 and o.x0 < self.x1 and self.y0 < o.y1 and o.y0 < self.y1

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def transform(self, m: Matrix) -> "Rect":
        return Rect.bound([m.apply(self.x0, self.y0), m.apply(self.x1, self.y0),
                           m.apply(self.x0, self.y1), m.apply(self.x1, self.y1)])

    def expand(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)

    def offset(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def distance_to(self, x: float, y: float) -> float:
        dx = max(self.x0 - x, 0, x - self.x1)
        dy = max(self.y0 - y, 0, y - self.y1)
        return math.hypot(dx, dy)

    def list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    def __iter__(self):
        return iter(self.list())
