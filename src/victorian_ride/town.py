"""The town of Ashcombe: streets, lanes, surfaces, height and named places.

Ground plane is (x, z) in metres, y is up (raylib). Heading 0 points to +x and grows
toward +z, which is a right turn seen from above. Traffic keeps left: the lane of a
directed edge a -> b lies on its left side. Pure Python, no raylib.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

ROAD, PAVEMENT, WALL = "road", "pavement", "wall"


@dataclass(frozen=True)
class Node:
    x: float
    z: float


@dataclass(frozen=True)
class Edge:
    a: int
    b: int
    carriage_hw: float = 4.0    # half width of the carriageway (road)
    street_hw: float = 6.5      # half width incl. pavements; buildings start here
    kind: str = "street"        # "street", "bridge", "yard"
    name: str = ""


@dataclass(frozen=True)
class Place:
    name: str
    x: float                    # where the fare waits (on the pavement)
    z: float
    heading: float              # direction of travel of the lane beside it
    edge: tuple[int, int]       # directed edge whose lane passes the place


def smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def wrap(a: float) -> float:
    """Angle to -pi..pi."""
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class Town:
    nodes: list[Node]
    edges: list[Edge]
    places: list[Place] = field(default_factory=list)
    hill_x0: float = 232.0      # the hill rises east of here ...
    hill_x1: float = 292.0      # ... and is level again past here
    hill_h: float = 9.0
    bridge: tuple[float, float, float, float] = (160.0, 228.0, 262.0, 1.2)   # x, z0, z1, hump height
    river: tuple[float, float] = (233.0, 257.0)                               # z extent of the water
    start: tuple[float, float, float] = (-62.0, 70.0, 0.0)                    # x, z, heading of the cab

    def __post_init__(self) -> None:
        self.adj: dict[int, list[int]] = {i: [] for i in range(len(self.nodes))}
        self._edge: dict[frozenset[int], Edge] = {}
        for e in self.edges:
            self.adj[e.a].append(e.b)
            self.adj[e.b].append(e.a)
            self._edge[frozenset((e.a, e.b))] = e
        # how far each edge's corridor reaches past its end nodes: covers the junction square
        self._ext: dict[tuple[int, int], float] = {}
        for e in self.edges:
            for n in (e.a, e.b):
                others = [self._edge[frozenset((n, m))] for m in self.adj[n]
                          if frozenset((n, m)) != frozenset((e.a, e.b))]
                self._ext[(id(e), n)] = max((o.street_hw for o in others), default=0.0)

    # --- geometry ---

    def edge(self, a: int, b: int) -> Edge:
        return self._edge[frozenset((a, b))]

    def direction(self, a: int, b: int) -> tuple[float, float, float]:
        """Unit vector and length from node a to node b."""
        na, nb = self.nodes[a], self.nodes[b]
        dx, dz = nb.x - na.x, nb.z - na.z
        n = math.hypot(dx, dz)
        return dx / n, dz / n, n

    def surface(self, x: float, z: float) -> str:
        """ROAD on a carriageway, PAVEMENT on a pavement, WALL anywhere else."""
        best = WALL
        for e in self.edges:
            na = self.nodes[e.a]
            ux, uz, n = self.direction(e.a, e.b)
            s = (x - na.x) * ux + (z - na.z) * uz
            d = abs(-(x - na.x) * uz + (z - na.z) * ux)
            if not (-self._ext[(id(e), e.a)] <= s <= n + self._ext[(id(e), e.b)]):
                continue
            if d <= e.carriage_hw:
                return ROAD
            if d <= e.street_hw:
                best = PAVEMENT
        return best

    def height(self, x: float, z: float) -> float:
        h = self.hill_h * smoothstep((x - self.hill_x0) / (self.hill_x1 - self.hill_x0))
        bx, z0, z1, hump = self.bridge
        if abs(x - bx) < 8 and z0 < z < z1:
            h += hump * math.sin(math.pi * (z - z0) / (z1 - z0))
        return h

    def in_river(self, x: float, z: float) -> bool:
        return self.river[0] < z < self.river[1]

    # --- lanes ---

    def lane_offset(self, a: int, b: int) -> float:
        return self.edge(a, b).carriage_hw * 0.5

    def trim(self, n: int, a: int, b: int) -> float:
        """Distance from node n where the lane of (a, b) stops for a junction. Zero where
        the street just carries on (two collinear edges) or at a dead end."""
        ns = self.adj[n]
        if len(ns) == 1:
            return 3.0
        if len(ns) == 2:
            m = ns[0] if ns[1] in (a, b) else ns[1]
            other = (n, m) if n == b else (m, n)
            u = self.direction(a, b)
            v = self.direction(*other)
            if u[0] * v[0] + u[1] * v[1] > 0.99:
                return 0.0
        return max(self.edge(n, m).street_hw for m in ns) + 1.0

    def lane(self, a: int, b: int) -> tuple[tuple[float, float], tuple[float, float]]:
        """Start and end points of the lane of directed edge a -> b, trimmed at junctions."""
        ux, uz, n = self.direction(a, b)
        off = self.lane_offset(a, b)
        lx, lz = uz, -ux                          # left normal
        na = self.nodes[a]
        t0, t1 = self.trim(a, a, b), n - self.trim(b, a, b)
        return ((na.x + ux * t0 + lx * off, na.z + uz * t0 + lz * off),
                (na.x + ux * t1 + lx * off, na.z + uz * t1 + lz * off))

    def exits(self, a: int, b: int) -> dict[str, int]:
        """Where a horse can go at node b coming from a: {"straight"|"left"|"right": node}."""
        ux, uz, _ = self.direction(a, b)
        out: dict[str, int] = {}
        for c in self.adj[b]:
            if c == a:
                continue
            vx, vz, _ = self.direction(b, c)
            turn = math.atan2(ux * vz - uz * vx, ux * vx + uz * vz)   # + = right
            key = "straight" if abs(turn) < math.radians(35) else "right" if turn > 0 else "left"
            if key not in out:
                out[key] = c
        return out

    def locate(self, x: float, z: float, heading: float) -> tuple[int, int] | None:
        """The directed edge whose lane best matches a position and heading."""
        best, score = None, math.inf
        for e in self.edges:
            for a, b in ((e.a, e.b), (e.b, e.a)):
                ux, uz, n = self.direction(a, b)
                align = math.cos(wrap(math.atan2(uz, ux) - heading))
                if align < 0.3:
                    continue
                na = self.nodes[a]
                s = (x - na.x) * ux + (z - na.z) * uz
                d = abs(-(x - na.x) * uz + (z - na.z) * ux + self.lane_offset(a, b))
                if s < -8 or s > n + 8:
                    continue
                sc = d + 6 * (1 - align)
                if sc < score:
                    best, score = (a, b), sc
        return best if score < 12 else None

    def place_by_name(self, name: str) -> Place:
        return next(p for p in self.places if p.name == name)


def ashcombe() -> Town:
    """The hand-made market town: a grid of five north-south and four east-west streets,
    a market square, a church, a hill to the east, a river with one bridge to the
    station, and a lane to the stable yard in the west."""
    xs = {"W": 0.0, "M": 80.0, "C": 160.0, "H": 230.0, "E": 300.0}
    zs = {"N": 0.0, "High": 70.0, "S": 140.0, "Low": 200.0}
    nodes: list[Node] = []
    ids: dict[str, int] = {}

    def node(key: str, x: float, z: float) -> int:
        ids[key] = len(nodes)
        nodes.append(Node(x, z))
        return ids[key]

    for zk, z in zs.items():
        for xk, x in xs.items():
            node(f"{xk}{zk}", x, z)
    node("yard", -42.0, 70.0)
    node("bridge_n", 160.0, 226.0)
    node("bridge_s", 160.0, 264.0)
    node("station", 160.0, 296.0)

    edges: list[Edge] = []

    def street(a: str, b: str, **kw) -> None:
        edges.append(Edge(ids[a], ids[b], **kw))

    order = list(xs)
    missing = {("WS", "MS"), ("HHigh", "HS")}
    for zk in zs:
        for x0, x1 in zip(order, order[1:], strict=False):
            if (f"{x0}{zk}", f"{x1}{zk}") in missing:
                continue
            kw = {"carriage_hw": 5.0, "street_hw": 8.0, "name": "High Street"} if zk == "High" else {}
            street(f"{x0}{zk}", f"{x1}{zk}", **kw)
    zorder = list(zs)
    for xk in xs:
        for z0, z1 in zip(zorder, zorder[1:], strict=False):
            if (f"{xk}{z0}", f"{xk}{z1}") in missing:
                continue
            street(f"{xk}{z0}", f"{xk}{z1}")
    street("yard", "WHigh", carriage_hw=4.0, street_hw=5.5, name="Stable Lane")
    street("CLow", "bridge_n")
    street("bridge_n", "bridge_s", carriage_hw=3.5, street_hw=4.2, kind="bridge", name="Ash Bridge")
    street("bridge_s", "station", carriage_hw=9.0, street_hw=12.0, kind="yard", name="Station Yard")

    town = Town(nodes, edges)
    town.start = (-30.0, 70.0 - town.lane_offset(ids["yard"], ids["WHigh"]), 0.0)

    def place(name: str, a: str, b: str, u: float) -> None:
        ia, ib = ids[a], ids[b]
        (x0, z0), (x1, z1) = town.lane(ia, ib)
        ux, uz, _ = town.direction(ia, ib)
        side = town.edge(ia, ib).carriage_hw * 0.5 + 1.5      # from the lane to the pavement
        x, z = x0 + (x1 - x0) * u + uz * side, z0 + (z1 - z0) * u - ux * side
        town.places.append(Place(name, x, z, math.atan2(uz, ux), (ia, ib)))

    place("The Railway Station", "bridge_s", "station", 0.75)
    place("Market Cross", "MHigh", "CHigh", 0.5)
    place("St Mary's Church", "CN", "HN", 0.5)
    place("The Crown & Anchor", "MS", "CS", 0.35)
    place("Town Hall", "CS", "CHigh", 0.5)
    place("Hill Terrace", "EN", "EHigh", 0.5)
    place("Mill Lane", "WLow", "WS", 0.5)
    place("The Grammar School", "MN", "MHigh", 0.5)
    place("Riverside Walk", "HLow", "CLow", 0.4)
    place("Ropewalk Row", "EHigh", "ES", 0.5)
    return town


def pick_fare(town: Town, rng: random.Random, near: tuple[float, float] | None = None,
              exclude: str | None = None) -> tuple[Place, Place]:
    """A pickup and a destination at least 120 m apart; the pickup is nearer `near` when given."""
    places = [p for p in town.places if p.name != exclude]
    if near:
        places.sort(key=lambda p: math.hypot(p.x - near[0], p.z - near[1]))
        places = places[: max(3, len(places) // 2)]
    src = rng.choice(places)
    far = [p for p in town.places if p is not src and math.hypot(p.x - src.x, p.z - src.z) >= 120]
    return src, rng.choice(far)
