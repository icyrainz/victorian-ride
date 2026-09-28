"""The static town as triangle soup: numpy arrays of positions and colours, built once.

Pure numpy, no raylib, so it can be tested and timed alone. Colours carry baked dusk
light (a low sun in the west) and lamp pools; the alpha channel is not transparency but
how much the fog may cover the vertex (255 = fully, lower = it glows through the fog).
"""
from __future__ import annotations

import math
import random

import numpy as np

from .town import Town

SUN = np.array([-0.8, 0.45, 0.35])
SUN = SUN / np.linalg.norm(SUN)
SUN_TINT = np.array([1.15, 0.92, 0.72])
AMBIENT = np.array([0.52, 0.52, 0.62])

BRICKS = [(0.50, 0.24, 0.17), (0.43, 0.21, 0.16), (0.56, 0.30, 0.22), (0.62, 0.53, 0.40),
          (0.70, 0.66, 0.57), (0.38, 0.36, 0.35), (0.47, 0.40, 0.33)]
ROOF = (0.20, 0.21, 0.25)
COBBLE = np.array([0.33, 0.31, 0.29])
FLAGS = np.array([0.50, 0.48, 0.45])
EARTH = np.array([0.24, 0.21, 0.17])
GRASS = np.array([0.22, 0.31, 0.16])
WATER = (0.16, 0.22, 0.27)
LIT = (1.0, 0.80, 0.45)
DARK_GLASS = (0.10, 0.12, 0.16)
LAMP = (1.0, 0.86, 0.55)
STONE = (0.56, 0.54, 0.50)


def shade(color, normal) -> tuple[float, float, float]:
    n = np.asarray(normal, float)
    d = max(0.0, float(n @ SUN))
    c = np.asarray(color) * (AMBIENT + SUN_TINT * d * 0.75)
    return tuple(np.clip(c, 0, 1))


class Builder:
    def __init__(self) -> None:
        self.pos: list[tuple[float, float, float]] = []
        self.col: list[tuple[int, int, int, int]] = []

    def tri(self, a, b, c, color, fog: int = 255, lit: bool = True) -> None:
        if lit:
            n = np.cross(np.subtract(b, a), np.subtract(c, a))
            ln = np.linalg.norm(n)
            color = shade(color, n / ln if ln else (0, 1, 0))
        rgba = (int(color[0] * 255), int(color[1] * 255), int(color[2] * 255), fog)
        self.pos += [a, b, c]
        self.col += [rgba, rgba, rgba]

    def quad(self, a, b, c, d, color, fog: int = 255, lit: bool = True) -> None:
        """a b c d counter-clockwise seen from the front."""
        self.tri(a, b, c, color, fog, lit)
        self.tri(a, c, d, color, fog, lit)

    def box(self, x0, y0, z0, x1, y1, z1, color, fog: int = 255, top: bool = True, bottom: bool = False) -> None:
        p = [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
             (x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
        faces = [(0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        if top:
            faces.append((4, 7, 6, 5))
        if bottom:
            faces.append((0, 1, 2, 3))
        for f in faces:
            self.quad(*(p[i] for i in f), color, fog)

    def gable(self, x0, z0, x1, z1, y, h, along_x: bool, color) -> None:
        """A pitched roof on the rectangle, ridge along x or z."""
        if along_x:
            zm = (z0 + z1) / 2
            self.quad((x0, y, z0), (x0, y + h, zm), (x1, y + h, zm), (x1, y, z0), color)
            self.quad((x1, y, z1), (x1, y + h, zm), (x0, y + h, zm), (x0, y, z1), color)
            self.tri((x0, y, z1), (x0, y + h, zm), (x0, y, z0), color)
            self.tri((x1, y, z0), (x1, y + h, zm), (x1, y, z1), color)
        else:
            xm = (x0 + x1) / 2
            self.quad((x1, y, z0), (xm, y + h, z0), (xm, y + h, z1), (x1, y, z1), color)
            self.quad((x0, y, z1), (xm, y + h, z1), (xm, y + h, z0), (x0, y, z0), color)
            self.tri((x0, y, z0), (xm, y + h, z0), (x1, y, z0), color)
            self.tri((x1, y, z1), (xm, y + h, z1), (x0, y, z1), color)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(self.pos, np.float32), np.asarray(self.col, np.uint8)


# --- surfaces on a grid (vectorised Town.surface) ---

def classify(town: Town, x: np.ndarray, z: np.ndarray) -> np.ndarray:
    """0 wall/ground, 1 pavement, 2 road, for arrays of points."""
    out = np.zeros(x.shape, np.int8)
    for e in town.edges:
        na = town.nodes[e.a]
        ux, uz, n = town.direction(e.a, e.b)
        s = (x - na.x) * ux + (z - na.z) * uz
        d = np.abs(-(x - na.x) * uz + (z - na.z) * ux)
        inside = (s >= -town._ext[(id(e), e.a)]) & (s <= n + town._ext[(id(e), e.b)])
        out = np.maximum(out, np.where(inside & (d <= e.street_hw), 1, 0))
        out = np.maximum(out, np.where(inside & (d <= e.carriage_hw), 2, 0))
    return out


def heights(town: Town, x: np.ndarray, z: np.ndarray, surf: np.ndarray) -> np.ndarray:
    h0, h1 = town.hill_x0, town.hill_x1
    u = np.clip((x - h0) / (h1 - h0), 0, 1)
    h = town.hill_h * u * u * (3 - 2 * u)
    bx, bz0, bz1, hump = town.bridge
    on_bridge = (np.abs(x - bx) < 8) & (z > bz0) & (z < bz1)
    h = h + np.where(on_bridge, hump * np.sin(np.pi * np.clip((z - bz0) / (bz1 - bz0), 0, 1)), 0)
    river = (z > town.river[0] - 3) & (z < town.river[1] + 3) & ~(on_bridge & (surf > 0))
    return np.where(river, -2.6, h)


def lamp_posts(town: Town) -> list[tuple[float, float]]:
    """Gas lamps on the pavements: at junction corners and every 30 m along streets."""
    out = []
    for e in town.edges:
        if e.kind != "street":
            continue
        ux, uz, n = town.direction(e.a, e.b)
        na = town.nodes[e.a]
        off = e.carriage_hw + 0.9
        k = 0
        s = 12.0
        while s < n - 10:
            side = 1 if k % 2 == 0 else -1
            out.append((na.x + ux * s + uz * off * side, na.z + uz * s - ux * off * side))
            s += 30.0
            k += 1
    return out


def build_town(town: Town, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    b = Builder()
    lamps = lamp_posts(town)
    _ground(town, b, lamps)
    _water(town, b)
    _buildings(town, b, rng)
    _landmarks(town, b, rng)
    _bridge(town, b)
    for (x, z) in lamps:
        y = town.height(x, z)
        b.box(x - 0.07, y, z - 0.07, x + 0.07, y + 3.3, z + 0.07, (0.08, 0.08, 0.09))
        b.box(x - 0.22, y + 3.3, z - 0.22, x + 0.22, y + 3.85, z + 0.22, LAMP, fog=40)
    return b.arrays()


def _ground(town: Town, b: Builder, lamps: list[tuple[float, float]], step: float = 2.0) -> None:
    xs = np.arange(-110.0, 350.0 + step, step)
    zs = np.arange(-60.0, 340.0 + step, step)
    gx, gz = np.meshgrid(xs, zs)                       # corners
    cx, cz = gx[:-1, :-1] + step / 2, gz[:-1, :-1] + step / 2
    surf = classify(town, cx, cz)
    corner_surf = classify(town, gx, gz)
    gy = heights(town, gx, gz, corner_surf)
    rng = np.random.default_rng(3)
    noise = rng.uniform(-0.05, 0.05, cx.shape)
    base = np.where(surf[..., None] == 2, COBBLE, np.where(surf[..., None] == 1, FLAGS, EARTH))
    green = (cx > 165) & (cx < 225) & (cz > 5) & (cz < 65)     # the church green
    base = np.where((green & (surf == 0))[..., None], GRASS, base)
    col = base + noise[..., None]
    # dusk light on the ground, and pools of lamplight
    col = col * (AMBIENT + SUN_TINT * SUN[1] * 0.75)
    glow = np.zeros(cx.shape)
    for (lx, lz) in lamps:
        glow += np.exp(-((cx - lx) ** 2 + (cz - lz) ** 2) / (2 * 4.5 ** 2))
    col = col + np.minimum(glow, 1.2)[..., None] * np.array([0.55, 0.40, 0.18])
    col = np.clip(col, 0, 1)
    rgba = np.concatenate([(col * 255).astype(np.uint8), np.full(cx.shape + (1,), 255, np.uint8)], axis=-1)
    p00 = np.stack([gx[:-1, :-1], gy[:-1, :-1], gz[:-1, :-1]], -1)
    p10 = np.stack([gx[:-1, 1:], gy[:-1, 1:], gz[:-1, 1:]], -1)
    p01 = np.stack([gx[1:, :-1], gy[1:, :-1], gz[1:, :-1]], -1)
    p11 = np.stack([gx[1:, 1:], gy[1:, 1:], gz[1:, 1:]], -1)
    # two triangles per cell, facing up (raylib: counter-clockwise from above is x -> -z ... any order, no culling)
    tris = np.stack([p00, p01, p11, p00, p11, p10], axis=2).reshape(-1, 3)
    cols = np.repeat(rgba.reshape(-1, 4), 6, axis=0)
    b.pos += [tuple(p) for p in tris.astype(np.float32)]
    b.col += [tuple(c) for c in cols]


def _water(town: Town, b: Builder) -> None:
    z0, z1 = town.river
    b.quad((-110, -1.4, z0 - 3), (-110, -1.4, z1 + 3), (350, -1.4, z1 + 3), (350, -1.4, z0 - 3), WATER, lit=False)
    # stone embankments
    for z in (z0 - 3, z1 + 3):
        b.quad((-110, -2.6, z), (350, -2.6, z), (350, 0.0, z), (-110, 0.0, z), STONE)


def _facade(b: Builder, rng: random.Random, x0, z0, x1, z1, y0, height, normal, shop: bool) -> None:
    """Windows and a door on one wall, from (x0, z0) to (x1, z1), facing `normal`."""
    nx, nz = normal
    length = math.hypot(x1 - x0, z1 - z0)
    ux, uz = (x1 - x0) / length, (z1 - z0) / length
    ox, oz = nx * 0.06, nz * 0.06
    floors = max(1, int(height // 3.3))
    per = max(1, int(length // 3.0))
    for f in range(floors):
        yb = y0 + 1.0 + f * 3.3
        for i in range(per):
            s = (i + 0.5) * length / per
            w, h = (1.1, 1.6) if not (shop and f == 0) else (length / per * 0.7, 2.0)
            if f == 0 and i == per // 2 and not shop:
                w, h, color, fog = 1.1, 2.2, (0.18, 0.11, 0.07), 255
                yb2 = y0
            else:
                lit = rng.random() < (0.65 if shop and f == 0 else 0.33)
                color, fog, yb2 = (LIT, 70, yb) if lit else (DARK_GLASS, 255, yb)
            cx, cz = x0 + ux * s + ox, z0 + uz * s + oz
            a = (cx - ux * w / 2, yb2, cz - uz * w / 2)
            c = (cx + ux * w / 2, yb2 + h, cz + uz * w / 2)
            b.quad(a, (a[0], c[1], a[2]), c, (c[0], a[1], c[2]), color, fog, lit=False)


def _row(b: Builder, rng: random.Random, town: Town, x0, z0, x1, z1, depth, normal, shop=False) -> None:
    """A terrace of houses along one side of a block, from (x0, z0) to (x1, z1) on the street
    line, `depth` deep, facing `normal` (unit, toward the street)."""
    nx, nz = normal
    length = math.hypot(x1 - x0, z1 - z0)
    ux, uz = (x1 - x0) / length, (z1 - z0) / length
    s = 0.0
    while s < length - 3:
        w = min(rng.uniform(6.0, 11.0), length - s)
        h = rng.choice((7.0, 8.5, 10.0, 10.0, 11.5, 13.0))
        color = rng.choice(BRICKS)
        ax, az = x0 + ux * s, z0 + uz * s
        bx, bz = ax + ux * w, az + uz * w
        # the box spans from the street line back by depth
        px = [ax, bx, bx - nx * depth, ax - nx * depth]
        pz = [az, bz, bz - nz * depth, az - nz * depth]
        ys = [town.height(x, z) for x, z in zip(px, pz, strict=False)]
        y0, top = min(ys) - 0.6, max(ys) + h
        xa, xb, za, zb = min(px), max(px), min(pz), max(pz)
        b.box(xa, y0, za, xb, top, zb, color)
        b.gable(xa, za, xb, zb, top, rng.uniform(1.6, 2.8), along_x=abs(ux) > 0.5, color=ROOF)
        if rng.random() < 0.8:
            cx = ax + ux * w * rng.uniform(0.2, 0.8) - nx * depth * 0.5
            cz = az + uz * w * rng.uniform(0.2, 0.8) - nz * depth * 0.5
            b.box(cx - 0.45, top, cz - 0.45, cx + 0.45, top + 3.4, cz + 0.45, (0.40, 0.20, 0.15))
        _facade(b, rng, ax, az, bx, bz, max(ys[0], ys[1]) - 0.1, h - 1, normal, shop)
        s += w


def _street_side(town: Town, x0, z0, x1, z1, nx, nz) -> float | None:
    """If a street runs along this cell side, its half width (the building line)."""
    mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
    for e in town.edges:
        na, nb = town.nodes[e.a], town.nodes[e.b]
        if abs(na.x - nb.x) < 1e-6 and abs(x0 - x1) < 1e-6 and abs(na.x - x0) < 1e-6:
            if min(na.z, nb.z) - 1 <= mz <= max(na.z, nb.z) + 1:
                return e.street_hw
        if abs(na.z - nb.z) < 1e-6 and abs(z0 - z1) < 1e-6 and abs(na.z - z0) < 1e-6:
            if min(na.x, nb.x) - 1 <= mx <= max(na.x, nb.x) + 1:
                return e.street_hw
    return None


XS = [-40.0, 0.0, 80.0, 160.0, 230.0, 300.0, 345.0]
ZS = [-45.0, 0.0, 70.0, 140.0, 200.0, 230.0]


def _buildings(town: Town, b: Builder, rng: random.Random) -> None:
    for i in range(len(XS) - 1):
        for j in range(len(ZS) - 1):
            x0, x1, z0, z1 = XS[i], XS[i + 1], ZS[j], ZS[j + 1]
            if (x0, z0) == (80.0, 70.0) or (x0, z0) == (160.0, 0.0):
                continue                                   # market square, church
            sides = {
                "n": _street_side(town, x0, z0, x1, z0, 0, -1),
                "s": _street_side(town, x0, z1, x1, z1, 0, 1),
                "w": _street_side(town, x0, z0, x0, z1, -1, 0),
                "e": _street_side(town, x1, z0, x1, z1, 1, 0),
            }
            ix0 = x0 + (sides["w"] or 0.0)
            ix1 = x1 - (sides["e"] or 0.0)
            iz0 = z0 + (sides["n"] or 0.0)
            iz1 = z1 - (sides["s"] or 0.0)
            depth = min(12.0, (ix1 - ix0) / 2, (iz1 - iz0) / 2)
            high = j == 1 or j == 2
            # rows along each side; north and south rows run full width, east and west fit between
            b.box(ix0 + 2, -0.5, iz0 + 2, ix1 - 2, 0.6 + town.height(ix0, iz0), iz1 - 2, EARTH)   # yard behind
            _row(b, rng, town, ix1, iz0, ix0, iz0, depth, (0, -1), shop=high and sides["n"] is not None)
            _row(b, rng, town, ix0, iz1, ix1, iz1, depth, (0, 1), shop=high and sides["s"] is not None)
            if iz1 - iz0 > 2 * depth + 4:
                _row(b, rng, town, ix0, iz0 + depth, ix0, iz1 - depth, depth, (-1, 0))
                _row(b, rng, town, ix1, iz1 - depth, ix1, iz0 + depth, depth, (1, 0))


def _landmarks(town: Town, b: Builder, rng: random.Random) -> None:
    # market square: flagstones are ground; stalls with striped awnings round a market cross
    for k in range(14):
        a = k / 14 * 2 * math.pi
        x, z = 120 + 26 * math.cos(a), 105 + 18 * math.sin(a)
        b.box(x - 1.3, 0, z - 0.8, x + 1.3, 1.0, z + 0.8, (0.40, 0.28, 0.18))
        stripe = [(0.70, 0.15, 0.12), (0.85, 0.82, 0.74), (0.18, 0.35, 0.22)][k % 3]
        b.box(x - 1.5, 2.1, z - 1.0, x + 1.5, 2.3, z + 1.0, stripe)
        for sx in (-1.3, 1.3):
            b.box(x + sx - 0.05, 1.0, z - 0.05, x + sx + 0.05, 2.1, z + 0.05, (0.3, 0.2, 0.12))
    b.box(116, 0, 101, 124, 0.8, 109, STONE)
    b.box(118.5, 0.8, 103.5, 121.5, 1.6, 106.5, STONE)
    b.box(119.6, 1.6, 104.6, 120.4, 7.5, 105.4, STONE)
    b.box(118.4, 6.4, 104.8, 121.6, 6.9, 105.2, STONE)
    b.quad((86.5, 0.03, 78.0), (86.5, 0.03, 133.5), (153.5, 0.03, 133.5), (153.5, 0.03, 78.0), FLAGS * 0.8)
    for x in (86, 154):
        for z in (78, 132):
            b.box(x - 0.07, 0, z - 0.07, x + 0.07, 3.3, z + 0.07, (0.08, 0.08, 0.09))
            b.box(x - 0.22, 3.3, z - 0.22, x + 0.22, 3.85, z + 0.22, LAMP, fog=40)
    # St Mary's church on its green
    b.box(182, -0.3, 22, 214, 13, 38, STONE)
    b.gable(182, 22, 214, 38, 13, 6.5, along_x=True, color=ROOF)
    b.box(170, -0.3, 23, 181, 26, 37, STONE)
    for f in range(3):
        _facade(b, rng, 182, 38, 214, 38, 2 + f * 3.3, 3, (0, 1), False)
    b.tri((170, 26, 23), (175.5, 40, 30), (181, 26, 23), ROOF)
    b.tri((181, 26, 23), (175.5, 40, 30), (181, 26, 37), ROOF)
    b.tri((181, 26, 37), (175.5, 40, 30), (170, 26, 37), ROOF)
    b.tri((170, 26, 37), (175.5, 40, 30), (170, 26, 23), ROOF)
    b.box(174.5, 18, 36.9, 176.5, 20, 37.3, (0.92, 0.88, 0.75), fog=90)      # clock face
    for (x, z) in ((194, 50), (204, 56), (214, 48), (176, 55), (220, 12)):
        b.box(x - 0.25, 0, z - 0.25, x + 0.25, 3.0, z + 0.25, (0.25, 0.17, 0.10))
        b.box(x - 2.2, 2.6, z - 2.2, x + 2.2, 7.0, z + 2.2, (0.14, 0.24, 0.12))
    # the stable yard at the west end of Stable Lane
    b.box(-60, -0.3, 58, -46.5, 7, 82, (0.45, 0.30, 0.20))
    b.gable(-60, 58, -46.5, 82, 7, 3, along_x=False, color=ROOF)
    b.box(-46.4, 0, 66, -46.3, 4, 74, (0.20, 0.12, 0.08))                    # stable doors
    # the railway station beyond the river
    b.box(120, -0.3, 300, 200, 9, 322, (0.55, 0.45, 0.35))
    b.gable(120, 300, 200, 322, 9, 3.5, along_x=True, color=ROOF)
    b.box(155, -0.3, 296, 165, 20, 306, (0.55, 0.45, 0.35))
    b.box(157, 16, 295.8, 163, 19, 296.0, (0.92, 0.88, 0.75), fog=90)
    _facade(b, rng, 120, 300, 200, 300, 1, 8, (0, -1), True)
    for (x0, x1) in ((80, 146), (174, 250)):
        b.box(x0, -0.3, 270, x1, 8, 296, (0.40, 0.34, 0.30))                  # warehouses
        b.gable(x0, 270, x1, 296, 8, 3, along_x=True, color=ROOF)


def _bridge(town: Town, b: Builder) -> None:
    bx, z0, z1, _ = town.bridge
    e = next(e for e in town.edges if e.kind == "bridge")
    w = e.street_hw
    n = 17
    for k in range(n):
        za, zb = z0 + (z1 - z0) * k / n, z0 + (z1 - z0) * (k + 1) / n
        ya, yb = town.height(bx, za), town.height(bx, zb)
        for side in (-1, 1):
            x = bx + side * w
            b.quad((x, ya, za), (x, ya + 1.1, za), (x, yb + 1.1, zb), (x, yb, zb), STONE)
            b.quad((x, ya - 3.0, za), (x, ya, za), (x, yb, zb), (x, yb - 3.0, zb), STONE)
            xo = x + side * 0.4
            b.quad((xo, ya, za), (xo, ya + 1.1, za), (xo, yb + 1.1, zb), (xo, yb, zb), STONE)
            b.quad((x, ya + 1.1, za), (xo, ya + 1.1, za), (xo, yb + 1.1, zb), (x, yb + 1.1, zb), STONE)
