import math

import pytest

from victorian_ride.sim import SHAFT, Controls, Crash, Drive, FareEvent, Kerb, money
from victorian_ride.town import PAVEMENT, ROAD, WALL, ashcombe

TOWN = ashcombe()


def drive_at(a: int, b: int, back: float = 30.0, v: float = 4.0) -> Drive:
    """A drive with the cab in the lane of a -> b, `back` metres before the lane end."""
    d = Drive(TOWN, seed=0)
    d.shy_at = math.inf
    d._fares = lambda dt, out: None
    (x0, z0), (x1, z1) = TOWN.lane(a, b)
    n = math.hypot(x1 - x0, z1 - z0)
    u = max(0.0, (n - back) / n)
    hdg = math.atan2(z1 - z0, x1 - x0)
    d.hx, d.hz = x0 + (x1 - x0) * u, z0 + (z1 - z0) * u
    d.ax, d.az = d.hx - SHAFT * math.cos(hdg), d.hz - SHAFT * math.sin(hdg)
    d.heading = d.cab_heading = hdg
    d.v = v
    return d


def run(d: Drive, seconds: float, c: Controls) -> list:
    out = []
    for _ in range(round(seconds * 60)):
        out += d.step(1 / 60, c)
    return out


def test_surfaces():
    assert TOWN.surface(40.0, 1.0) == ROAD
    assert TOWN.surface(40.0, 5.0) == PAVEMENT
    assert TOWN.surface(40.0, 20.0) == WALL
    assert TOWN.surface(3.0, 3.0) == ROAD              # junction square
    assert TOWN.surface(7.0, 7.0) == WALL              # the building on the corner


def test_lanes_keep_left():
    (x0, z0), (x1, z1) = TOWN.lane(0, 1)               # eastward along z = 0: the lane is north (z < 0)
    assert z0 < 0 and z1 < 0 and x1 > x0


@pytest.mark.parametrize("gait,v", [("walk", 1.6), ("trot", 4.0), ("gallop", 6.0)])
def test_horse_sense_takes_every_turn_without_a_kerb(gait, v):
    bad = []
    for e in TOWN.edges:
        for a, b in ((e.a, e.b), (e.b, e.a)):
            for choice, c in TOWN.exits(a, b).items():
                d = drive_at(a, b, v=v)
                d.sense.cur, d.sense.choice, d.sense.committed = (a, b), choice, True
                reached = None
                for i in range(45 * 4):
                    ctl = Controls(gait=gait, brake=0.5 if d.pushed else 0.0)   # a driver brakes down the hill
                    out = [x for x in run(d, 0.25, ctl) if isinstance(x, Kerb | Crash)]
                    if out:
                        bad.append((a, b, choice, out[0]))
                        break
                    if reached is None and d.sense.cur == (b, c):
                        reached = i
                    if reached is not None and i > reached + 12:
                        break
                if reached is None:
                    bad.append((a, b, choice, "lost"))
    assert bad == []


def test_a_steady_pull_chooses_the_turn():
    d = drive_at(1, 2, back=45.0)                      # east along z = 0 toward (160, 0): straight or right
    run(d, 5.0, Controls(gait="trot"))
    assert d.sense.choice == "straight"
    d2 = drive_at(1, 2, back=45.0)
    run(d2, 4.0, Controls(gait="trot"))
    run(d2, 1.0, Controls(gait="trot", rein=0.3, centre=0.0))   # a short pull in the approach
    assert d2.sense.choice == "right"
    run(d2, 12.0, Controls(gait="trot"))
    assert d2.sense.cur == (2, 7)                      # took the right turn south


def test_overruling_the_horse_hits_the_kerb():
    d = drive_at(0, 1, back=40.0)
    out = run(d, 4.0, Controls(gait="trot", rein=-0.6, centre=0.0))
    assert any(isinstance(x, Kerb) for x in out)


def test_the_horse_stops_at_a_wall_by_itself():
    d = drive_at(5, 20, back=20.0, v=1.6)              # Stable Lane west toward the yard's dead end
    run(d, 30.0, Controls(gait="walk"))
    assert abs(d.v) < 0.05
    assert TOWN.surface(*d.head()) != WALL


def test_downhill_the_cab_pushes_unless_you_brake():
    free = drive_at(9, 8, back=70.0)
    run(free, 10.0, Controls(gait="trot"))
    braked = drive_at(9, 8, back=70.0)
    vmax = 0.0
    for _ in range(40):
        run(braked, 0.25, Controls(gait="trot", brake=0.3))
        vmax = max(vmax, braked.v)
    assert free.v > 5.5 or free.pushed
    assert vmax < 4.6


def test_a_fare_boards_rides_and_pays():
    d = Drive(TOWN, seed=3)
    run(d, 3.5, Controls())
    f = d.fare
    assert f is not None and f.phase == "waiting"
    p = f.pickup
    d.hx, d.hz = p.x + SHAFT * 0.5 * math.cos(p.heading), p.z + SHAFT * 0.5 * math.sin(p.heading)
    d.ax, d.az = p.x - SHAFT * 0.5 * math.cos(p.heading), p.z - SHAFT * 0.5 * math.sin(p.heading)
    out = run(d, 2.5, Controls())
    assert f.phase == "riding" and any(isinstance(x, FareEvent) and x.what == "board" for x in out)
    q = f.dest
    d.hx, d.hz = q.x + SHAFT * 0.5 * math.cos(q.heading), q.z + SHAFT * 0.5 * math.sin(q.heading)
    d.ax, d.az = q.x - SHAFT * 0.5 * math.cos(q.heading), q.z - SHAFT * 0.5 * math.sin(q.heading)
    out = run(d, 2.0, Controls())
    paid = [x for x in out if isinstance(x, FareEvent) and x.what == "alight"]
    assert paid and d.money == paid[0].pay > 0 and d.fare is not f


def test_money():
    assert money(18) == "1s 6d"
    assert money(250) == "£1 0s 10d"
