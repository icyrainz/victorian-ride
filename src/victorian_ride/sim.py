"""Horse, hansom cab, horse sense, passenger comfort and fares. Pure Python, no raylib.

The horse keeps to its lane by itself ("horse sense"): `HorseSense` works out the rein
the horse would like (`intent`, -1..1, + = right). The player's rein is the wheel. The
horse steers with `intent + (rein - centre)`, where `centre` is where the force
feedback spring is holding the wheel (the horse's intent, rate limited), or 0 without
force feedback. Hands off the wheel, the horse follows the street; hold against the
spring and you overrule it.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .town import PAVEMENT, ROAD, WALL, Place, Town, pick_fare, wrap

# --- the horse ---

GAITS = ("back", "halt", "walk", "trot", "canter", "gallop")
GAIT_SPEED = {"back": -0.8, "halt": 0.0, "walk": 1.6, "trot": 4.0, "canter": 6.5, "gallop": 9.5}
# footfalls per stride, as (phase in the stride, strength)
BEATS = {
    "back": ((0.0, 0.2), (0.5, 0.2)),
    "walk": ((0.0, 0.25), (0.25, 0.2), (0.5, 0.25), (0.75, 0.2)),
    "trot": ((0.0, 0.45), (0.5, 0.45)),
    "canter": ((0.0, 0.4), (0.22, 0.6), (0.44, 0.45)),
    "gallop": ((0.0, 0.4), (0.12, 0.45), (0.3, 0.5), (0.42, 0.55)),
}
# phase offset of each leg in the stride (render)
LEGS = ("lf", "rf", "lh", "rh")
LEG_PHASE = {
    "back": {"lf": 0.0, "rf": 0.5, "lh": 0.5, "rh": 0.0},
    "walk": {"lh": 0.0, "lf": 0.25, "rh": 0.5, "rf": 0.75},
    "trot": {"lf": 0.0, "rh": 0.0, "rf": 0.5, "lh": 0.5},
    "canter": {"lh": 0.0, "rh": 0.22, "lf": 0.22, "rf": 0.44},
    "gallop": {"lh": 0.0, "rh": 0.12, "lf": 0.3, "rf": 0.42},
}

SHAFT = 3.0             # cab axle to the horse's saddle, metres
TRACK = 0.95            # cab wheel, half the track
HEAD = 1.5              # saddle to the horse's head
MAX_REL = math.radians(28)   # the horse's heading inside the shafts, relative to the cab
MIN_RADIUS = 3.2        # tightest turn at a walk, metres
A_LAT_MAX = 3.0         # the horse will not turn harder than this at speed, m/s2
TAU = 0.35              # the horse answers the rein after this long, seconds
PIVOT = 0.4             # rad/s the horse turns on the spot when it stands at a wall
ACCEL = 1.2             # m/s2 the horse can add
HOLD = 1.3              # m/s2 the horse can take off by itself; downhill it is no match for the cab
WHOA = 1.6              # m/s2 when asked to halt
BRAKE = 4.0             # m/s2 of the cab's brake shoes at full pedal
GRAVITY_SHARE = 0.6     # part of the slope the horse and cab feel along the road
COLLAR = 0.35           # uphill the horse leans into the collar: only this part of the slope slows it
LAT_COMFORT = 1.8       # m/s2 the horse aims for in a turn
PASSENGER_LAT = 2.2     # m/s2 a passenger starts to mind

NAME = "Bess"


def gait_of(v: float) -> str:
    """The gait the legs show at a speed."""
    if v < -0.05:
        return "back"
    a = abs(v)
    return "halt" if a < 0.15 else "walk" if a < 2.6 else "trot" if a < 5.4 else "canter" if a < 8.0 else "gallop"


def cadence(gait: str, v: float) -> float:
    """Strides per second."""
    a = abs(v)
    return {"back": 0.8, "halt": 0.0, "walk": 0.55 + 0.2 * a, "trot": 1.15 + 0.08 * a,
            "canter": 1.45 + 0.05 * a, "gallop": 1.9 + 0.04 * a}[gait]


def kappa_max(v: float) -> float:
    return min(1.0 / MIN_RADIUS, A_LAT_MAX / max(v * v, 1e-3))


# --- events (audio, force feedback, HUD) ---

@dataclass(frozen=True)
class Hoof:
    t: float
    gait: str
    strength: float
    surface: str


@dataclass(frozen=True)
class Kerb:
    t: float
    side: int               # -1 left wheel, +1 right wheel
    severity: float         # 0..1
    up: bool                # onto the pavement (True) or back down


@dataclass(frozen=True)
class Crash:
    t: float
    side: int
    severity: float


@dataclass(frozen=True)
class Shy:
    t: float
    side: int


@dataclass(frozen=True)
class FareEvent:
    t: float
    what: str               # "hail", "board", "alight"
    pay: int = 0            # pence, on alight


@dataclass(frozen=True)
class Message:
    t: float
    text: str


# --- horse sense ---

def bezier(p0, p1, p2, n: int = 8) -> list[tuple[float, float]]:
    out = []
    for i in range(1, n + 1):
        u = i / n
        a, b, c = (1 - u) ** 2, 2 * (1 - u) * u, u * u
        out.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return out


def line_cross(p, d, q, e) -> tuple[float, float] | None:
    """Where line p + t d meets line q + s e; None when parallel."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-6:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p[0] + t * d[0], p[1] + t * d[1]


@dataclass
class SenseOut:
    intent: float = 0.0             # rein the horse would like, -1..1
    v_limit: float = math.inf       # the horse will not go faster than this, m/s
    path: list[tuple[float, float]] = field(default_factory=list)
    target: tuple[float, float] | None = None
    choice: str | None = None       # the way it means to go at the next junction
    exits: tuple[str, ...] = ()


class HorseSense:
    """The horse follows the lane of the street it is on and takes the way at a junction
    that the reins ask for (a steady pull while it approaches), else straight on."""

    APPROACH = 30.0     # metres before the junction where a pull chooses the way
    COMMIT = 7.0        # metres before the junction where the choice is fixed
    PULL = 0.12         # smoothed pull that counts as asking for a turn

    def __init__(self, town: Town) -> None:
        self.town = town
        self.cur: tuple[int, int] | None = None
        self.choice: str | None = None
        self.committed = False
        self.pull = 0.0

    def _next(self) -> tuple[int, int] | None:
        if self.cur is None or self.choice is None:
            return None
        c = self.town.exits(*self.cur).get(self.choice)
        return (self.cur[1], c) if c is not None else None

    @staticmethod
    def _project(p0, p1, x, z) -> tuple[float, float, float]:
        """(along, lateral with + = right of the lane, length)."""
        dx, dz = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(dx, dz) or 1e-6
        ux, uz = dx / n, dz / n
        return (x - p0[0]) * ux + (z - p0[1]) * uz, -(x - p0[0]) * uz + (z - p0[1]) * ux, n

    def _set(self, edge: tuple[int, int] | None) -> None:
        if edge != self.cur:
            self.cur, self.choice, self.committed = edge, None, False

    SWING = 0.6         # a left turn is taken this far left of the centre line, not in the lane ...
    CORNER = 1.5        # ... and this far past the kerb of the street it turns into
    LEAD = 12.0         # metres over which the horse moves out of its lane and back

    def _swing(self, nxt: tuple[int, int]) -> list[tuple[float, float]]:
        """A left turn swung wide, so the inner cab wheel clears the corner kerb: out of
        the lane toward the centre line, round the corner, back into the next lane."""
        town = self.town
        a, b = self.cur
        n = town.nodes[b]
        ux, uz, _ = town.direction(a, b)
        vx, vz, _ = town.direction(*nxt)
        cin, cout = town.edge(a, b).carriage_hw, town.edge(*nxt).carriage_hw
        oin, oout = town.lane_offset(a, b), town.lane_offset(*nxt)
        p = (n.x - ux * (cout + self.CORNER) + uz * self.SWING, n.z - uz * (cout + self.CORNER) - ux * self.SWING)
        q = (n.x + vx * (cin + self.CORNER) + vz * self.SWING, n.z + vz * (cin + self.CORNER) - vx * self.SWING)
        ctrl = line_cross(p, (ux, uz), q, (vx, vz)) or ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        d_in, d_out = cout + self.CORNER + self.LEAD, cin + self.CORNER + self.LEAD
        start = (n.x - ux * d_in + uz * oin, n.z - uz * d_in - ux * oin)
        back = (n.x + vx * d_out + vz * oout, n.z + vz * d_out - vx * oout)
        return [start, p, *bezier(p, ctrl, q), back]

    def update(self, x: float, z: float, heading: float, v: float, pull: float, dt: float,
               omega: float = 0.0) -> SenseOut:
        town = self.town
        if v < -0.05:
            return SenseOut()
        self.pull += (pull - self.pull) * min(1.0, dt / 0.3)
        # where are we: the lane we were on, the next one once we are on it, else search
        nxt = self._next()
        if nxt is not None:
            s, d, _ = self._project(*town.lane(*nxt), x, z)
            if s > 0.0 and abs(d) < 6.0:
                self._set(nxt)
        if self.cur is not None:
            p0, p1 = town.lane(*self.cur)
            s, d, n = self._project(p0, p1, x, z)
            align = math.cos(wrap(math.atan2(p1[1] - p0[1], p1[0] - p0[0]) - heading))
            reach = town.trim(self.cur[1], *self.cur) + 4.0
            if not (-10.0 < s < n + reach and abs(d) < 7.0 and align > (-0.2 if s > n else 0.3)):
                self._set(town.locate(x, z, heading))
        else:
            self._set(town.locate(x, z, heading))
        if self.cur is None:
            return SenseOut()

        p0, p1 = town.lane(*self.cur)
        s, d, n = self._project(p0, p1, x, z)
        exits = town.exits(*self.cur)
        to_end = n - s
        if not self.committed:
            if to_end < self.APPROACH:
                if self.pull > self.PULL and "right" in exits:
                    self.choice = "right"
                elif self.pull < -self.PULL and "left" in exits:
                    self.choice = "left"
                elif self.choice is None or self.choice not in exits:
                    self.choice = next((k for k in ("straight", "left", "right") if k in exits), None)
            if to_end < self.COMMIT:
                self.committed = True
        # the path: this lane, the turn, the next lane
        path = [p0, p1]
        nxt = self._next()
        v_limit = math.inf
        if nxt is not None:
            q0, q1 = town.lane(*nxt)
            e = ((q1[0] - q0[0]), (q1[1] - q0[1]))
            if self.choice == "left":
                path = [p0, *self._swing(nxt), q1]
            else:
                ctrl = line_cross(p1, (p1[0] - p0[0], p1[1] - p0[1]), q0, e) or ((p1[0] + q0[0]) / 2,
                                                                                (p1[1] + q0[1]) / 2)
                path += bezier(p1, ctrl, q0) + [q1]
            if self.choice != "straight":
                r = max(4.0, 0.5 * math.hypot(q0[0] - p1[0], q0[1] - p1[1]) * 1.4)
                v_turn = math.sqrt(LAT_COMFORT * r)
                v_limit = math.sqrt(v_turn ** 2 + 2 * 1.0 * max(0.0, to_end - 1.0))
        else:
            v_limit = math.sqrt(2 * 1.0 * max(0.0, to_end - 0.5))   # dead end: pull up at the end
        # pure pursuit: from the nearest point on the path to a point `look` metres further on
        at, best, arc = 0.0, math.inf, 0.0
        for a, b in zip(path, path[1:], strict=False):
            seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-6
            u = max(0.0, min(1.0, ((x - a[0]) * (b[0] - a[0]) + (z - a[1]) * (b[1] - a[1])) / (seg * seg)))
            dd = math.hypot(a[0] + (b[0] - a[0]) * u - x, a[1] + (b[1] - a[1]) * u - z)
            if dd < best:
                best, at = dd, arc + u * seg
            arc += seg
        look = max(4.0, min(12.0, 3.0 + 0.9 * abs(v)))
        target, left = path[-1], at + look
        for a, b in zip(path, path[1:], strict=False):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg >= left:
                u = left / seg if seg else 0.0
                target = (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
                break
            left -= seg
        dist = math.hypot(target[0] - x, target[1] - z)
        if dist < 0.5:
            intent = 0.0
        else:
            # aim from where the heading will be once the horse has answered the rein
            alpha = wrap(math.atan2(target[1] - z, target[0] - x) - heading)
            kappa = 2 * math.sin(alpha) / max(dist, 1.0)
            intent = max(-1.0, min(1.0, kappa / kappa_max(max(abs(v), 1.0))))
        return SenseOut(intent, v_limit, path, target, self.choice, tuple(exits))


# --- the drive ---

@dataclass
class Controls:
    rein: float = 0.0               # wheel, -1..1 (+ = right), full rein at the play range
    centre: float | None = None     # the force feedback spring centre, same units; None without
    brake: float = 0.0              # 0..1
    gait: str = "halt"              # asked for
    calm: bool = False              # "easy there" (left paddle)
    urge: bool = False              # a click (right paddle)


@dataclass
class Fare:
    pickup: Place
    dest: Place
    kind: str                       # "hurry" or "gentle"
    phase: str = "waiting"          # waiting, boarding, riding, alighting, done
    comfort: float = 1.0
    t_board: float = 0.0
    timer: float = 0.0
    budget: float = 0.0             # seconds for a hurry fare
    route: float = 0.0              # straight-line metres pickup to destination


HURRY = ("\"The 4.10 to London, cabbie, and quick!\"", "\"I'm late for the magistrate. Hurry!\"",
         "\"Double fare if you make it sharp!\"")
GENTLE = ("\"Gently, please. I have a new hat.\"", "\"Mind the cobbles, I've a basket of eggs.\"",
          "\"No rush. My nerves, you understand.\"")


def money(pence: int) -> str:
    """Pounds, shillings and pence."""
    lsd = f"£{pence // 240} {pence % 240 // 12}s {pence % 12}d"
    return lsd if pence >= 240 else f"{pence // 12}s {pence % 12}d"


class Drive:
    """The whole simulation: one horse, one cab, one fare at a time."""

    def __init__(self, town: Town, seed: int | None = None) -> None:
        self.town = town
        self.rng = random.Random(seed)
        self.sense = HorseSense(town)
        self.t = 0.0
        x, z, h = town.start
        self.heading = h
        self.cab_heading = h
        self.ax, self.az = x, z                                     # cab axle
        self.hx, self.hz = x + SHAFT * math.cos(h), z + SHAFT * math.sin(h)   # horse saddle
        self.v = 0.0
        self.omega = 0.0
        self.accel = 0.0
        self.stamina = 1.0
        self.stride = 0.0
        self.gait_asked = "halt"
        self.steer = 0.0                                            # rein the horse obeys, -1..1
        self.intent = 0.0
        self.sense_out = SenseOut()
        self.wheel_surface = [ROAD, ROAD]
        self.pushed = False
        self.blocked = False
        self.shy_at = self.rng.uniform(45.0, 90.0)
        self.shy: tuple[float, int] | None = None
        self.urge_until = -1.0
        self.money = 0
        self.fares_done = 0
        self.fare: Fare | None = None
        self._last_crash = -9.0
        self._last_refuse = -9.0
        self._tired_said = False
        self.log: list[Message] = []

    # --- helpers ---

    @property
    def gait(self) -> str:
        return gait_of(self.v)

    def grade(self) -> float:
        """Slope along the cab's heading, + = uphill."""
        c, s = math.cos(self.cab_heading), math.sin(self.cab_heading)
        h = self.town.height
        return (h(self.hx + c, self.hz + s) - h(self.hx - c, self.hz - s)) / 2.0

    def wheels(self) -> tuple[tuple[float, float], tuple[float, float]]:
        lx, lz = math.sin(self.cab_heading), -math.cos(self.cab_heading)
        return ((self.ax + lx * TRACK, self.az + lz * TRACK), (self.ax - lx * TRACK, self.az - lz * TRACK))

    def head(self) -> tuple[float, float]:
        return self.hx + HEAD * math.cos(self.heading), self.hz + HEAD * math.sin(self.heading)

    def _say(self, text: str, out: list) -> None:
        m = Message(self.t, text)
        self.log.append(m)
        del self.log[:-6]
        out.append(m)

    def _clear(self, heading: float, ahead: float) -> bool:
        x, z = self.head()
        return all(self.town.surface(x + d * math.cos(heading), z + d * math.sin(heading)) != WALL
                   for d in (0.4, ahead))

    # --- one step ---

    def step(self, dt: float, c: Controls) -> list:
        """Advance by dt (split into steps of at most 1/120 s). Returns the events."""
        out: list = []
        if not dt > 0:
            return out
        n = max(1, math.ceil(dt / (1 / 120)))
        for _ in range(n):
            self._step(dt / n, c, out)
        return out

    def _step(self, dt: float, c: Controls, out: list) -> None:
        self.t += dt
        town = self.town
        # gait asked for, and what the horse is willing to give
        self.gait_asked = c.gait
        want = GAIT_SPEED[c.gait]
        if self.stamina < 0.05:
            want = min(want, GAIT_SPEED["walk"])
        elif self.stamina < 0.2:
            want = min(want, GAIT_SPEED["trot"])
        if want > GAIT_SPEED["trot"] and self.stamina < 0.2 and not self._tired_said:
            self._tired_said = True
            self._say(f"{NAME} is blown. She will only trot until she has rested.", out)
        if self.stamina > 0.5:
            self._tired_said = False
        if c.urge and self.t > self.urge_until:
            self.urge_until = self.t + 2.0
        if self.t < self.urge_until and want > 0:
            want += 0.5

        # horse sense and the shy
        pull = c.rein - (c.centre if c.centre is not None else 0.0)
        so = self.sense.update(self.hx, self.hz, self.heading, self.v, pull, dt, self.omega)
        self.sense_out = so
        intent = so.intent
        if self.shy is None and self.t > self.shy_at and self.v > 1.0:
            self.shy = (self.t, self.rng.choice((-1, 1)))
            self._say(f"A dog darts out! {NAME} shies.", out)
            out.append(Shy(self.t, self.shy[1]))
        if self.shy is not None:
            u = (self.t - self.shy[0]) / (0.6 if c.calm else 1.4)
            if u >= 1.0:
                self.shy = None
                self.shy_at = self.t + self.rng.uniform(70.0, 150.0)
            else:
                intent += 0.4 * self.shy[1] * math.sin(math.pi * u)
                want += 1.0 * math.sin(math.pi * u) if want > 0 else 0.0
        self.intent = max(-1.0, min(1.0, intent))
        self.steer = max(-1.0, min(1.0, self.intent + pull)) if so.path else max(-1.0, min(1.0, c.rein))

        # the horse will not walk into a wall
        look = self.heading + 0.5 * self.steer
        self.blocked = want > 0 and not self._clear(look, 1.0 + 0.6 * max(self.v, 0.0))
        if self.blocked:
            want = 0.0
            if self.v > 0.5 and self.t - self._last_refuse > 3.0:
                self._last_refuse = self.t
                self._say(f"{NAME} refuses to walk into the wall.", out)
            if abs(self.v) < 0.3 and abs(self.steer) > 0.2:
                # standing at a wall, she turns her forehand the way the rein asks, inside the shafts
                h = wrap(self.heading + math.copysign(PIVOT * dt, self.steer))
                if abs(wrap(h - self.cab_heading)) <= MAX_REL:
                    self.heading = h
        if want > 0:
            want = min(want, so.v_limit)

        # speed
        grade = self.grade()
        a_grade = -9.81 * grade * GRAVITY_SHARE * (COLLAR if grade > 0 else 1.0)
        hold = HOLD if grade > -0.03 else HOLD * 0.5     # downhill the cab pushes: the brake is yours
        err = want - self.v
        if want == 0.0 and abs(self.v) > 0:
            a_horse = -math.copysign(min(WHOA * hold / HOLD, abs(self.v) / dt), self.v)
        else:
            a_horse = max(-hold, min(ACCEL * (0.5 + 0.5 * self.stamina), 2.0 * err))
        a_brake = -math.copysign(BRAKE * c.brake, self.v) if abs(self.v) > 1e-3 else 0.0
        v0 = self.v
        v = self.v + (a_horse + a_grade) * dt
        if a_brake:
            v = v + a_brake * dt if abs(a_brake * dt) < abs(v) else 0.0
        if want == 0.0 and v0 * v < 0:
            v = 0.0
        if abs(v) < 0.02 and abs(a_grade) < hold and want == 0.0:
            v = 0.0
        self.accel += ((v - v0) / dt - self.accel) * min(1.0, dt / 0.15)
        self.v = v
        self.pushed = a_grade > 0.2 and v > want + 0.6 and want >= 0
        # turning: the horse answers the rein after TAU
        target = self.steer * kappa_max(abs(v)) * v
        self.omega += (target - self.omega) * min(1.0, dt / TAU)

        prev = (self.hx, self.hz, self.heading, self.ax, self.az, self.cab_heading)
        self.heading = wrap(self.heading + self.omega * dt)
        rel = wrap(self.heading - self.cab_heading)
        if abs(rel) > MAX_REL:
            self.heading = wrap(self.cab_heading + math.copysign(MAX_REL, rel))
        self.hx += v * math.cos(self.heading) * dt
        self.hz += v * math.sin(self.heading) * dt
        dx, dz = self.hx - self.ax, self.hz - self.az
        dist = math.hypot(dx, dz) or 1e-6
        self.ax, self.az = self.hx - dx / dist * SHAFT, self.hz - dz / dist * SHAFT
        self.cab_heading = math.atan2(dz, dx)

        # the cab's wheels on the kerb or against a wall
        crashed = 0
        for i, (wx, wz) in enumerate(self.wheels()):
            side = -1 if i == 0 else 1
            s = town.surface(wx, wz)
            if s == WALL:
                crashed = side
                break
            if s != self.wheel_surface[i]:
                up = s == PAVEMENT
                out.append(Kerb(self.t, side, min(1.0, 0.25 + abs(v) / 4.0), up))
                self.wheel_surface[i] = s
                self._jolt(0.05 * min(1.0, 0.3 + abs(v) / 3.0), out, "The wheel bumps the kerb.")
        hx, hz = self.head()
        if crashed or town.surface(hx, hz) == WALL:
            self.hx, self.hz, self.heading, self.ax, self.az, self.cab_heading = prev
            sev = min(1.0, abs(v) / 3.0)
            self.v = self.omega = 0.0
            if crashed and self.t - self._last_crash > 1.0 and sev > 0.05:
                self._last_crash = self.t
                out.append(Crash(self.t, crashed, sev))
                self._jolt(0.25 * sev, out, "Crash! The wheel hits the wall.")
            return

        # footfalls
        g = gait_of(v)
        if g != "halt":
            before = self.stride
            self.stride += cadence(g, v) * dt
            for ph, strength in BEATS[g]:
                if math.floor(before - ph) < math.floor(self.stride - ph):
                    wx, wz = self.head()
                    out.append(Hoof(self.t, g, strength, town.surface(wx, wz)))

        # stamina
        drain = {"back": 0.0, "halt": -0.02, "walk": -0.008, "trot": 0.001, "canter": 0.012, "gallop": 0.03}[g]
        if v > 1.0 and grade > 0.02:
            drain += 0.03 * grade
        self.stamina = max(0.0, min(1.0, self.stamina - drain * dt))

        self._comfort(dt, g, out)
        self._fares(dt, out)

    # --- passengers ---

    def _jolt(self, amount: float, out: list, text: str | None = None) -> None:
        f = self.fare
        if f is not None and f.phase == "riding":
            f.comfort = max(0.0, f.comfort - amount)
            if text and amount >= 0.04:
                self._say(text + " Your passenger winces.", out)

    def _comfort(self, dt: float, g: str, out: list) -> None:
        f = self.fare
        if f is None or f.phase != "riding":
            return
        lat = abs(self.v * self.omega)
        loss = max(0.0, lat - PASSENGER_LAT) * 0.03
        loss += max(0.0, abs(self.accel) - 2.0) * 0.03
        loss += {"canter": 0.004, "gallop": 0.012}.get(g, 0.0)
        if self.pushed:
            loss += 0.015
        f.comfort = max(0.0, f.comfort - loss * dt)

    def new_fare(self, out: list, exclude: str | None = None) -> None:
        src, dst = pick_fare(self.town, self.rng, near=(self.ax, self.az), exclude=exclude)
        kind = self.rng.choice(("hurry", "gentle"))
        route = math.hypot(dst.x - src.x, dst.z - src.z)
        self.fare = Fare(src, dst, kind, route=route, budget=25.0 + route * 1.4 / 4.0)
        out.append(FareEvent(self.t, "hail"))
        self._say(f"A fare hails you at {src.name}.", out)

    def near(self, p: Place, r: float) -> bool:
        mx, mz = (self.ax + self.hx) / 2, (self.az + self.hz) / 2
        return math.hypot(p.x - mx, p.z - mz) < r

    def _fares(self, dt: float, out: list) -> None:
        f = self.fare
        if f is None:
            if self.t > 3.0:
                self.new_fare(out)
            return
        stopped = abs(self.v) < 0.3
        if f.phase == "waiting" and stopped and self.near(f.pickup, 8.0):
            f.phase, f.timer = "boarding", 0.0
        elif f.phase == "boarding":
            if not (stopped and self.near(f.pickup, 9.0)):
                f.phase = "waiting"
            else:
                f.timer += dt
                if f.timer > 2.0:
                    f.phase, f.t_board, f.comfort = "riding", self.t, 1.0
                    out.append(FareEvent(self.t, "board"))
                    line = self.rng.choice(HURRY if f.kind == "hurry" else GENTLE)
                    self._say(f"{line} To {f.dest.name}.", out)
        elif f.phase == "riding" and stopped and self.near(f.dest, 8.0):
            f.phase, f.timer = "alighting", 0.0
        elif f.phase == "alighting":
            if not (stopped and self.near(f.dest, 9.0)):
                f.phase = "riding"
            else:
                f.timer += dt
                if f.timer > 1.5:
                    self._pay(f, out)

    def _pay(self, f: Fare, out: list) -> None:
        took = self.t - f.t_board
        fare = 6 + int(f.route / 25)
        tip, says = 0, ""
        if f.kind == "hurry":
            if took <= f.budget:
                tip = 6 + int(12 * (1 - took / f.budget))
                says = "\"Splendid, just in time!\""
            else:
                says = "\"Too slow, cabbie. No tip.\""
        if f.comfort >= 0.85:
            tip += 12 if f.kind == "gentle" else 2
            says = says or "\"A lovely smooth ride. Thank you!\""
        elif f.comfort >= 0.6:
            tip += 6 if f.kind == "gentle" else 0
            says = says or "\"Not bad, not bad.\""
        else:
            tip = 0
            says = "\"My back! I shall walk next time.\""
        pay = fare + tip
        self.money += pay
        self.fares_done += 1
        f.phase = "done"
        out.append(FareEvent(self.t, "alight", pay))
        self._say(f"{says} Paid {money(pay)} (tip {money(tip)}).", out)
        self.fare = None
        self.new_fare(out, exclude=f.dest.name)
