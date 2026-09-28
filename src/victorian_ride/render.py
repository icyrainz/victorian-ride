"""The raylib view: one window, one pane per monitor (one, or three for a triple
surround), a fog shader, the town mesh, the horse, the cab and the HUD.

Each pane is its own camera rendered into its own texture, so a side pane can be yawed
to match an angled side monitor. The finished frame is composed in a screen texture,
which also serves `--capture` in a hidden window.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .rig.config import config_dir, load_settings
from .scene import build_town
from .sim import GAIT_SPEED, LEG_PHASE, LEGS, TRACK, Drive, gait_of, money
from .town import Town

log = logging.getLogger(__name__)

SETTINGS_FILE = "render.json"

FOG_VS = """#version 330
in vec3 vertexPosition;
in vec4 vertexColor;
uniform mat4 mvp;
uniform vec4 colDiffuse;
out vec4 fragColor;
out float fragDepth;
void main() {
    fragColor = vertexColor * colDiffuse;
    gl_Position = mvp * vec4(vertexPosition, 1.0);
    fragDepth = gl_Position.w;
}
"""
FOG_FS = """#version 330
in vec4 fragColor;
in float fragDepth;
uniform vec3 fogColor;
uniform float fogDensity;
out vec4 finalColor;
void main() {
    float f = 1.0 - exp(-pow(fragDepth * fogDensity, 2.0));
    // alpha is how much fog may cover this vertex: lamps and lit windows glow through
    finalColor = vec4(mix(fragColor.rgb, fogColor, f * fragColor.a), 1.0);
}
"""
TRANSPARENT_FS = """#version 330
in vec4 fragColor;
in float fragDepth;
out vec4 finalColor;
void main() { finalColor = fragColor; }
"""

SKY_TOP = (22, 26, 52)
SKY_HORIZON = (196, 128, 92)
FOG = (0.56, 0.47, 0.45)
FONT_CANDIDATES = (
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
)
GLYPHS = [*range(32, 127), 163, 183, 8212, 8220, 8221, 8217]

BAY = (0.42, 0.25, 0.14)
MANE = (0.10, 0.07, 0.05)
HOOF = (0.12, 0.11, 0.10)
LEATHER = (0.12, 0.08, 0.06)
LACQUER = (0.09, 0.13, 0.11)
BLACK = (0.05, 0.05, 0.06)
WHEEL = (0.30, 0.10, 0.08)
COATS = ((0.20, 0.22, 0.35), (0.35, 0.12, 0.12), (0.18, 0.28, 0.20), (0.30, 0.26, 0.20), (0.45, 0.40, 0.50))


@dataclass
class RenderSettings:
    fps: int = 144              # frame cap when vsync is off
    vsync: bool = True
    msaa: bool = True
    ssaa: float = 1.0           # panes render at this scale, then filter down (2.0 on a big GPU)
    triple_hfov: float = 50.0   # horizontal field of view of ONE monitor of a triple
    single_hfov: float = 85.0   # horizontal field of view of a single screen
    side_yaw: float | None = None   # yaw of the side panes; None = triple_hfov (a seamless panorama)
    seat_pitch: float = -8.0    # degrees the driver looks down
    fog_density: float = 0.0105

    @classmethod
    def load(cls, path: str | Path | None = None) -> RenderSettings:
        path = Path(path) if path else config_dir() / SETTINGS_FILE
        return load_settings(cls, path, {"side_yaw": lambda v: v is None or isinstance(v, int | float)})

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else config_dir() / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path


def parse_span(s: str) -> tuple[int, int, int, int]:
    """'5760x1080+0+0' -> (w, h, x, y). Offsets may be negative ('5760x1080-1920+0')."""
    import re

    m = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", s.strip())
    if not m:
        raise ValueError(f"span must look like WxH+X+Y, got {s!r}")
    w, h, x, y = (int(g) for g in m.groups())
    if w <= 0 or h <= 0:
        raise ValueError(f"span size must be positive, got {s!r}")
    return w, h, x, y


def vfov(hfov_deg: float, aspect: float) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(hfov_deg) / 2) / aspect))


class RaylibKeys:
    """KeySource for input.py: keyboard only. The mouse never steers (it sits at 0.5)."""

    def __init__(self) -> None:
        import pyray as rl

        self.rl = rl

    def is_down(self, key: str) -> bool:
        return self.rl.is_key_down(getattr(self.rl, f"KEY_{key}"))

    def pressed(self, key: str) -> bool:
        return self.rl.is_key_pressed(getattr(self.rl, f"KEY_{key}"))

    def mouse_x_norm(self) -> float:
        return 0.5


class Window:
    """A borderless window over `span` (a triple surround), fullscreen, a plain window
    of `size`, or `hidden` (drawn off screen, for captures)."""

    def __init__(self, size=(1600, 900), span=None, fullscreen=False, hidden=False,
                 settings: RenderSettings | None = None, title: str = "Victorian Ride") -> None:
        import pyray as rl

        self.rl, self.hidden, self._open = rl, hidden, False
        st = self.settings = settings or RenderSettings()
        rl.set_trace_log_level(rl.LOG_WARNING)
        flags = rl.FLAG_WINDOW_HIDDEN if hidden else (
            (rl.FLAG_VSYNC_HINT if st.vsync else 0) | (rl.FLAG_MSAA_4X_HINT if st.msaa else 0)
            | (rl.FLAG_WINDOW_UNDECORATED if span else 0)
            | (rl.FLAG_WINDOW_HIGHDPI if sys.platform == "darwin" else 0))
        rl.set_config_flags(flags)
        w, h = (span[0], span[1]) if span else (0, 0) if fullscreen and not hidden else size
        rl.init_window(0 if w == 0 else 320, 0 if h == 0 else 180, title)   # small first: see Torque Hero
        if not rl.is_window_ready():
            raise RuntimeError("could not open a window: no display found")
        self._open = True
        if not hidden and w:
            rl.set_window_size(w, h)
        if span and not hidden:
            rl.set_window_position(span[2], span[3])
        if fullscreen and not hidden:
            rl.toggle_borderless_windowed()
        rl.set_exit_key(rl.KEY_NULL)
        rl.set_target_fps(0 if hidden or st.vsync else st.fps)
        self.w, self.h = (w, h) if hidden else (rl.get_screen_width(), rl.get_screen_height())
        self.rw, self.rh = (w, h) if hidden else (rl.get_render_width(), rl.get_render_height())
        log.info("window %sx%s (render %sx%s)%s", self.w, self.h, self.rw, self.rh, " hidden" if hidden else "")

    def focused(self) -> bool:
        return bool(self.rl.is_window_focused())

    def should_close(self) -> bool:
        return bool(self.rl.window_should_close())

    def close(self) -> None:
        if self._open:
            self._open = False
            self.rl.close_window()


def _upload(rl, pos, col, shader, chunk: int = 60000 * 3):
    """numpy triangle soup -> raylib models (memory from raylib's allocator, freed by it)."""
    models = []
    for i in range(0, len(pos), chunk):
        p, c = pos[i:i + chunk], col[i:i + chunk]
        mesh = rl.Mesh()
        mesh.vertexCount = len(p)
        mesh.triangleCount = len(p) // 3
        mesh.vertices = rl.ffi.cast("float *", rl.mem_alloc(p.nbytes))
        rl.ffi.memmove(mesh.vertices, p.tobytes(), p.nbytes)
        mesh.colors = rl.ffi.cast("unsigned char *", rl.mem_alloc(c.nbytes))
        rl.ffi.memmove(mesh.colors, c.tobytes(), c.nbytes)
        rl.upload_mesh(mesh, False)
        model = rl.load_model_from_mesh(mesh)
        model.materials[0].shader = shader
        models.append(model)
    return models


def _unit_box(rl, shader):
    """A 1 m cube centred on the origin, faces shaded so a tint still reads as a solid."""
    import numpy as np

    from .scene import Builder

    b = Builder()
    shades = {"top": 1.0, "front": 0.86, "side": 0.72, "back": 0.6, "bottom": 0.45}
    s = 0.5
    p = [(-s, -s, -s), (s, -s, -s), (s, -s, s), (-s, -s, s), (-s, s, -s), (s, s, -s), (s, s, s), (-s, s, s)]
    faces = [((0, 4, 5, 1), "side"), ((1, 5, 6, 2), "front"), ((2, 6, 7, 3), "side"), ((3, 7, 4, 0), "back"),
             ((4, 7, 6, 5), "top"), ((0, 1, 2, 3), "bottom")]
    for idx, name in faces:
        g = shades[name]
        b.quad(*(p[i] for i in idx), (g, g, g), lit=False)
    pos, col = b.arrays()
    return _upload(rl, pos, col.astype(np.uint8), shader)[0]


class View:
    def __init__(self, win: Window, town: Town, triple: bool | None = None) -> None:
        import pyray as rl

        self.rl, self.win, self.town, self.st = rl, win, town, win.settings
        self.triple = triple if triple is not None else win.w >= 3 * win.h
        rl.rl_disable_backface_culling()
        rl.rl_set_clip_planes(0.1, 900.0)
        self.shader = rl.load_shader_from_memory(FOG_VS, FOG_FS)
        self.glass = rl.load_shader_from_memory(FOG_VS, TRANSPARENT_FS)
        self._loc_fog = rl.get_shader_location(self.shader, "fogColor")
        self._loc_density = rl.get_shader_location(self.shader, "fogDensity")
        self._fog_arr = rl.ffi.new("float[]", FOG)       # kept alive: raylib reads it through a pointer
        rl.set_shader_value(self.shader, self._loc_fog, rl.ffi.cast("float *", self._fog_arr), rl.SHADER_UNIFORM_VEC3)
        self.set_fog(self.st.fog_density)
        t = time.perf_counter()
        pos, col = build_town(town)
        self.town_models = _upload(rl, pos, col, self.shader)
        log.info("town: %d triangles in %.2f s", len(pos) // 3, time.perf_counter() - t)
        self.box = _unit_box(rl, self.shader)
        self.rim = rl.load_model_from_mesh(rl.gen_mesh_torus(0.1, 1.0, 10, 28))
        self.rim.materials[0].shader = self.shader
        self.font = self._font()
        n = 3 if self.triple else 1
        pw, ph = int(win.rw / n * self.st.ssaa), int(win.rh * self.st.ssaa)
        self.panes = [rl.load_render_texture(pw, ph) for _ in range(n)]
        for p in self.panes:
            rl.set_texture_filter(p.texture, rl.TEXTURE_FILTER_BILINEAR)
        self.screen = rl.load_render_texture(win.rw, win.rh)
        self.chase = False
        self.show_map = True
        self.wheel_turn = 0.0
        self._last_pos: tuple[float, float] | None = None

    def set_fog(self, density: float) -> None:
        self.rl.set_shader_value(self.shader, self._loc_density, self.rl.ffi.new("float *", density),
                                 self.rl.SHADER_UNIFORM_FLOAT)

    def _font(self):
        rl = self.rl
        for p in FONT_CANDIDATES:
            if Path(p).exists():
                arr = rl.ffi.new("int[]", GLYPHS)
                cps = rl.ffi.cast("int *", arr)
                self._glyph_arr = arr
                self._glyphs = cps
                f = rl.load_font_ex(p, 64, cps, len(GLYPHS))
                rl.set_texture_filter(f.texture, rl.TEXTURE_FILTER_BILINEAR)
                return f
        return rl.get_font_default()

    # --- 3D helpers ---

    def _part(self, model, cx, cy, cz, sx, sy, sz, color, alpha: float = 1.0) -> None:
        rl = self.rl
        c = rl.Color(int(color[0] * 255), int(color[1] * 255), int(color[2] * 255), int(alpha * 255))
        rl.draw_model_ex(model, rl.Vector3(cx, cy, cz), rl.Vector3(0, 1, 0), 0.0, rl.Vector3(sx, sy, sz), c)

    def _frame(self, x, y, z, yaw_rad) -> None:
        rl = self.rl
        rl.rl_push_matrix()
        rl.rl_translatef(x, y, z)
        rl.rl_rotatef(-math.degrees(yaw_rad), 0, 1, 0)

    def _limb(self, px, py, pz, angle_deg, length, width, color, hoof: bool = True) -> None:
        """A leg hanging from a pivot, swung about the lateral axis."""
        rl = self.rl
        rl.rl_push_matrix()
        rl.rl_translatef(px, py, pz)
        rl.rl_rotatef(angle_deg, 0, 0, 1)
        self._part(self.box, 0, -length / 2, 0, width, length, width, color)
        if hoof:
            self._part(self.box, 0, -length + 0.06, 0, width + 0.03, 0.12, width + 0.05, HOOF)
        rl.rl_pop_matrix()

    def _horse(self, d: Drive) -> None:
        rl, h = self.rl, self.town.height
        g = gait_of(d.v)
        amp = {"back": 12, "halt": 0, "walk": 18, "trot": 26, "canter": 32, "gallop": 42}[g]
        bob = 0.05 * abs(math.sin(math.pi * 2 * d.stride * (2 if g == "trot" else 1))) if g != "halt" else 0.0
        y = h(d.hx, d.hz)
        self._frame(d.hx, y + bob, d.hz, d.heading)
        self._part(self.box, -0.05, 1.38, 0, 1.9, 0.72, 0.6, BAY)
        self._part(self.box, 0.78, 1.42, 0, 0.5, 0.82, 0.62, BAY)
        self._part(self.box, -0.8, 1.45, 0, 0.45, 0.7, 0.62, BAY)
        self._part(self.box, 0.35, 1.45, 0, 0.22, 0.76, 0.66, LEATHER)          # girth
        self._part(self.box, 0.95, 1.62, 0, 0.26, 0.75, 0.72, LEATHER)          # collar
        rl.rl_push_matrix()                                                     # neck and head
        rl.rl_translatef(0.95, 1.62, 0)
        rl.rl_rotatef(48 - (8 if g in ("canter", "gallop") else 0), 0, 0, 1)
        self._part(self.box, 0.5, 0, 0, 1.05, 0.42, 0.32, BAY)
        self._part(self.box, 0.45, 0.22, 0, 0.95, 0.1, 0.12, MANE)
        rl.rl_translatef(1.0, 0.0, 0)
        rl.rl_rotatef(-105, 0, 0, 1)
        self._part(self.box, 0.28, 0, 0, 0.66, 0.3, 0.27, BAY)
        self._part(self.box, -0.05, 0.12, 0.08, 0.08, 0.18, 0.06, BAY)          # ears
        self._part(self.box, -0.05, 0.12, -0.08, 0.08, 0.18, 0.06, BAY)
        self._part(self.box, 0.1, 0.02, 0, 0.1, 0.33, 0.33, LEATHER)           # blinkers
        rl.rl_pop_matrix()
        rl.rl_push_matrix()                                                     # tail
        rl.rl_translatef(-1.0, 1.6, 0)
        rl.rl_rotatef(-115 + 6 * math.sin(d.t * 1.7), 0, 0, 1)
        self._part(self.box, 0.4, 0, 0, 0.8, 0.14, 0.12, MANE)
        rl.rl_pop_matrix()
        phase = LEG_PHASE.get(g, LEG_PHASE["walk"])
        for leg in LEGS:
            px = 0.72 if leg[1] == "f" else -0.8
            pz = 0.2 if leg[0] == "r" else -0.2
            sw = amp * math.sin(2 * math.pi * (d.stride - phase[leg])) if amp else 0.0
            self._limb(px, 1.1, pz, sw, 1.08, 0.14, BAY)
        rl.rl_pop_matrix()

    def _cab(self, d: Drive) -> None:
        rl, h = self.rl, self.town.height
        y = h(d.ax, d.az)
        if self._last_pos is not None:
            self.wheel_turn += math.hypot(d.ax - self._last_pos[0], d.az - self._last_pos[1]) / 0.85 * (
                1 if d.v >= 0 else -1)
        self._last_pos = (d.ax, d.az)
        self._frame(d.ax, y, d.az, d.cab_heading)
        self._part(self.box, 0.3, 1.5, 0, 1.25, 1.3, 1.3, LACQUER)             # the body
        self._part(self.box, 0.3, 0.95, 0, 1.0, 0.25, 1.1, LACQUER)
        self._part(self.box, 0.22, 2.2, 0, 1.75, 0.09, 1.5, BLACK)              # roof
        self._part(self.box, 0.97, 1.2, 0, 0.07, 0.65, 1.2, LACQUER)            # half doors
        self._part(self.box, 0.62, 1.75, 0.66, 0.5, 0.45, 0.02, (0.2, 0.22, 0.25))   # side windows
        self._part(self.box, 0.62, 1.75, -0.66, 0.5, 0.45, 0.02, (0.2, 0.22, 0.25))
        self._part(self.box, -0.62, 2.45, 0, 0.45, 0.1, 0.55, LEATHER)          # the driver's seat
        self._part(self.box, -0.84, 2.7, 0, 0.06, 0.45, 0.55, LEATHER)
        self._part(self.box, -0.2, 2.05, 0, 1.2, 0.08, 0.2, BLACK)              # seat support
        for side in (-1, 1):
            self._part(self.box, 2.3, 1.0, side * 0.5, 2.9, 0.07, 0.07, WHEEL)     # shafts
            self._part(self.box, 0.98, 1.95, side * 0.72, 0.16, 0.24, 0.16, (1.0, 0.85, 0.5))   # lamps
            rl.rl_push_matrix()                                                 # a wheel with spokes
            rl.rl_translatef(0, 0.85, side * TRACK)
            rl.rl_rotatef(-math.degrees(self.wheel_turn), 0, 0, 1)
            for k in range(4):
                rl.rl_push_matrix()
                rl.rl_rotatef(45 * k, 0, 0, 1)
                self._part(self.box, 0, 0, 0, 1.62, 0.05, 0.04, WHEEL)
                rl.rl_pop_matrix()
            self._part(self.box, 0, 0, 0, 0.18, 0.18, 0.16, WHEEL)                # hub
            rl.draw_model_ex(self.rim, rl.Vector3(0, 0, 0), rl.Vector3(0, 1, 0), 0, rl.Vector3(1.7, 1.7, 1.7),
                             rl.Color(26, 22, 20, 255))
            rl.rl_pop_matrix()
        rl.rl_pop_matrix()

    def _person(self, d: Drive) -> None:
        f = d.fare
        if f is None or f.phase not in ("waiting", "boarding"):
            return
        rl = self.rl
        p = f.pickup
        y = self.town.height(p.x, p.z)
        coat = COATS[hash(p.name) % len(COATS)]
        self._frame(p.x, y, p.z, p.heading + math.pi / 2)
        self._part(self.box, 0, 0.45, 0.12, 0.18, 0.9, 0.16, BLACK)
        self._part(self.box, 0, 0.45, -0.12, 0.18, 0.9, 0.16, BLACK)
        self._part(self.box, 0, 1.3, 0, 0.32, 0.85, 0.52, coat)
        self._part(self.box, 0, 1.87, 0, 0.24, 0.26, 0.22, (0.85, 0.68, 0.55))
        self._part(self.box, 0, 2.02, 0, 0.3, 0.04, 0.34, BLACK)
        self._part(self.box, 0, 2.18, 0, 0.22, 0.3, 0.24, BLACK)               # top hat
        wave = math.dist((p.x, p.z), (d.ax, d.az)) < 70
        rl.rl_push_matrix()
        rl.rl_translatef(0, 1.62, 0.3)
        rl.rl_rotatef(150 + 20 * math.sin(d.t * 6) if wave else 10, 1, 0, 0)
        self._part(self.box, 0, -0.35, 0, 0.12, 0.7, 0.12, coat)
        rl.rl_pop_matrix()
        rl.rl_pop_matrix()

    def _beam(self, x, z, color) -> None:
        rl = self.rl
        y = self.town.height(x, z)
        c = rl.Color(*(int(v * 255) for v in color), 70)
        rl.draw_cylinder(rl.Vector3(x, y, z), 0.5, 0.5, 60.0, 10, c)

    def _seat(self, d: Drive, yaw_off: float, hfov: float, aspect: float):
        rl = self.rl
        pitch = math.radians(self.st.seat_pitch)
        if self.chase:
            back, up = 11.0, 5.5
            ex = d.ax - back * math.cos(d.cab_heading)
            ez = d.az - back * math.sin(d.cab_heading)
            ey = self.town.height(d.ax, d.az) + up
            pitch = math.radians(-14)
        else:
            ex = d.ax - 0.7 * math.cos(d.cab_heading)
            ez = d.az - 0.7 * math.sin(d.cab_heading)
            g = gait_of(d.v)
            bob = 0.025 * math.sin(2 * math.pi * d.stride * 2) if g not in ("halt", "back") else 0.0
            ey = self.town.height(d.ax, d.az) + 3.1 + bob
        yaw = d.cab_heading + yaw_off
        fx, fy, fz = math.cos(pitch) * math.cos(yaw), math.sin(pitch), math.cos(pitch) * math.sin(yaw)
        return rl.Camera3D(rl.Vector3(ex, ey, ez), rl.Vector3(ex + fx, ey + fy, ez + fz), rl.Vector3(0, 1, 0),
                           vfov(hfov, aspect), rl.CAMERA_PERSPECTIVE)

    def _sky(self, w: int, h: int, fov_v: float) -> None:
        rl = self.rl
        pitch = self.st.seat_pitch if not self.chase else -14
        yh = h / 2 - math.tan(math.radians(-pitch)) / math.tan(math.radians(fov_v / 2)) * h / 2
        fog = rl.Color(int(FOG[0] * 255), int(FOG[1] * 255), int(FOG[2] * 255), 255)
        rl.clear_background(fog)
        top = rl.Color(*SKY_TOP, 255)
        hor = rl.Color(*SKY_HORIZON, 255)
        rl.draw_rectangle_gradient_v(0, 0, w, max(1, int(yh * 0.8)), top, hor)
        rl.draw_rectangle_gradient_v(0, int(yh * 0.8), w, max(1, int(yh * 0.2) + 2), hor, fog)

    def _pane(self, i: int, d: Drive) -> None:
        rl = self.rl
        rt = self.panes[i]
        w, h = rt.texture.width, rt.texture.height
        hfov = self.st.triple_hfov if self.triple else self.st.single_hfov
        yaw = 0.0
        if self.triple:
            side = self.st.side_yaw if self.st.side_yaw is not None else hfov
            yaw = math.radians((i - 1) * side)
        cam = self._seat(d, yaw, hfov, w / h)
        rl.begin_texture_mode(rt)
        self._sky(w, h, cam.fovy)
        rl.begin_mode_3d(cam)
        rl.begin_shader_mode(self.shader)
        for m in self.town_models:
            rl.draw_model(m, rl.Vector3(0, 0, 0), 1.0, rl.WHITE)
        self._horse(d)
        self._cab(d)
        self._person(d)
        # reins, from the driver's hands over the roof to the bit
        self._part(self.box, 0, -100, 0, 0.001, 0.001, 0.001, (1, 1, 1))     # resets the tint for the batch
        hx = d.ax - 0.35 * math.cos(d.cab_heading)
        hz = d.az - 0.35 * math.sin(d.cab_heading)
        hy = self.town.height(d.ax, d.az) + 2.55
        bx = d.hx + 2.05 * math.cos(d.heading)
        bz = d.hz + 2.05 * math.sin(d.heading)
        by = self.town.height(d.hx, d.hz) + 1.75
        lx, lz = math.sin(d.heading), -math.cos(d.heading)
        for s in (-1, 1):
            rl.draw_line_3d(rl.Vector3(hx + lx * 0.15 * s, hy, hz + lz * 0.15 * s),
                            rl.Vector3(bx + lx * 0.12 * s, by, bz + lz * 0.12 * s), rl.Color(40, 28, 20, 255))
        rl.end_shader_mode()
        rl.begin_shader_mode(self.glass)
        f = d.fare
        if f is not None:
            if f.phase in ("waiting", "boarding"):
                self._beam(f.pickup.x, f.pickup.z, (1.0, 0.75, 0.3))
            elif f.phase in ("riding", "alighting"):
                self._beam(f.dest.x, f.dest.z, (0.55, 0.8, 1.0))
        rl.end_shader_mode()
        rl.end_mode_3d()
        rl.end_texture_mode()

    # --- the frame ---

    def draw(self, d: Drive, hud: dict, capture: str | None = None) -> None:
        rl = self.rl
        for i in range(len(self.panes)):
            self._pane(i, d)
        rl.begin_texture_mode(self.screen)
        rl.clear_background(rl.BLACK)
        n = len(self.panes)
        pw = self.win.rw / n
        for i, p in enumerate(self.panes):
            src = rl.Rectangle(0, 0, p.texture.width, -p.texture.height)
            rl.draw_texture_pro(p.texture, src, rl.Rectangle(i * pw, 0, pw, self.win.rh), rl.Vector2(0, 0), 0,
                                rl.WHITE)
        cx0 = pw * (1 if n == 3 else 0)
        self._hud(d, hud, cx0, pw, self.win.rh)
        rl.end_texture_mode()
        if capture:
            img = rl.load_image_from_texture(self.screen.texture)
            rl.image_flip_vertical(img)
            rl.export_image(img, capture)
            rl.unload_image(img)
        rl.begin_drawing()
        rl.clear_background(rl.BLACK)
        src = rl.Rectangle(0, 0, self.screen.texture.width, -self.screen.texture.height)
        rl.draw_texture_pro(self.screen.texture, src, rl.Rectangle(0, 0, self.win.w, self.win.h), rl.Vector2(0, 0), 0,
                            rl.WHITE)
        rl.end_drawing()

    # --- HUD ---

    def _text(self, s: str, x: float, y: float, size: float, color=(240, 228, 200), align: str = "left",
              alpha: float = 1.0) -> float:
        rl = self.rl
        m = rl.measure_text_ex(self.font, s, size, 1)
        if align == "center":
            x -= m.x / 2
        elif align == "right":
            x -= m.x
        rl.draw_text_ex(self.font, s, rl.Vector2(x + 2, y + 2), size, 1, rl.Color(0, 0, 0, int(150 * alpha)))
        rl.draw_text_ex(self.font, s, rl.Vector2(x, y), size, 1, rl.Color(*color, int(255 * alpha)))
        return m.x

    def _bar(self, x, y, w, h, frac, color) -> None:
        rl = self.rl
        rl.draw_rectangle(int(x), int(y), int(w), int(h), rl.Color(0, 0, 0, 120))
        rl.draw_rectangle(int(x), int(y), int(w * max(0.0, min(1.0, frac))), int(h), rl.Color(*color, 220))

    def _hud(self, d: Drive, hud: dict, x0: float, w: float, h: float) -> None:
        rl = self.rl
        u = h / 1080
        cx = x0 + w / 2
        pad = 30 * u
        # the fare, top centre, with an arrow toward where to go
        f = d.fare
        if f is not None:
            tgt = f.pickup if f.phase in ("waiting", "boarding") else f.dest
            dist = math.hypot(tgt.x - d.ax, tgt.z - d.az)
            yards = dist * 1.094
            if f.phase in ("waiting", "boarding"):
                line = f"Fare waiting at {tgt.name}  ·  {yards:.0f} yds"
            else:
                left = f.budget - (d.t - f.t_board)
                clock = f"  ·  {max(0, int(left)) // 60}:{max(0, int(left)) % 60:02d} left" if f.kind == "hurry" else ""
                line = f"To {tgt.name}  ·  {yards:.0f} yds{clock}"
            if f.phase in ("boarding", "alighting"):
                line = "Your passenger is getting in..." if f.phase == "boarding" else "Your passenger is paying..."
            self._text(line, cx, pad, 40 * u, align="center")
            bearing = math.atan2(tgt.z - d.az, tgt.x - d.ax) - d.cab_heading   # + = to the right
            ax, ay, r = cx, pad + 100 * u, 26 * u
            tri = [rl.Vector2(ax + math.sin(bearing + a) * s, ay - math.cos(bearing + a) * s)
                   for a, s in ((0.0, r * 1.3), (2.5, r), (-2.5, r))]
            rl.draw_triangle(tri[0], tri[2], tri[1], rl.Color(255, 200, 90, 230))
            rl.draw_triangle(tri[0], tri[1], tri[2], rl.Color(255, 200, 90, 230))
        # money and fares, top left of the centre screen
        self._text(money(d.money), x0 + pad, pad, 44 * u, (255, 214, 120))
        self._text(f"{d.fares_done} fare{'s' if d.fares_done != 1 else ''}", x0 + pad, pad + 52 * u, 28 * u)
        # the horse, bottom left
        y = h - 190 * u
        asked, now = d.gait_asked, gait_of(d.v)
        mph = abs(d.v) * 2.237
        self._text(f"{now.upper()}  {mph:4.1f} mph", x0 + pad, y, 40 * u)
        if asked != now and GAIT_SPEED.get(asked, 0) != 0:
            self._text(f"asked: {asked}", x0 + pad, y + 46 * u, 26 * u, (220, 200, 160))
        self._text("Bess", x0 + pad, y + 84 * u, 26 * u)
        self._bar(x0 + pad + 80 * u, y + 90 * u, 240 * u, 16 * u, d.stamina,
                  (120, 200, 110) if d.stamina > 0.3 else (230, 140, 60))
        if f is not None and f.phase in ("riding", "alighting"):
            self._text("Comfort", x0 + pad, y + 118 * u, 26 * u)
            self._bar(x0 + pad + 110 * u, y + 124 * u, 210 * u, 16 * u, f.comfort,
                      (120, 170, 230) if f.comfort > 0.6 else (230, 110, 80))
        if d.pushed:
            self._text("The cab is pushing the horse: brake!", cx, h * 0.3, 36 * u, (255, 150, 110), "center")
        # messages, bottom centre
        now_t = d.t
        msgs = [m for m in d.log if now_t - m.t < 8.0][-3:]
        for k, m in enumerate(reversed(msgs)):
            a = max(0.0, min(1.0, (8.0 - (now_t - m.t)) / 1.5))
            self._text(m.text, cx, h - (120 + 44 * k) * u, 30 * u, align="center", alpha=a)
        if self.show_map:
            self._map(d, x0 + w - 330 * u, h - 330 * u, 300 * u)
        # status line
        self._text(hud.get("status", ""), x0 + w - pad, pad, 20 * u, (200, 190, 170), "right")
        if hud.get("paused"):
            self._pause(hud, x0, w, h, u)

    def _map(self, d: Drive, x: float, y: float, size: float) -> None:
        rl = self.rl
        town = self.town
        xmin, xmax, zmin, zmax = -70.0, 320.0, -20.0, 310.0
        k = size / max(xmax - xmin, zmax - zmin)

        def sp(px, pz):
            return rl.Vector2(x + (px - xmin) * k, y + (pz - zmin) * k)

        rl.draw_rectangle(int(x - 8), int(y - 8), int(size + 16), int(size + 16), rl.Color(20, 18, 16, 150))
        rl.draw_rectangle(int(x - 8), int(sp(0, town.river[0]).y), int(size + 16),
                          int((town.river[1] - town.river[0]) * k), rl.Color(60, 80, 100, 160))
        for e in town.edges:
            a, b = town.nodes[e.a], town.nodes[e.b]
            rl.draw_line_ex(sp(a.x, a.z), sp(b.x, b.z), max(2.0, e.carriage_hw * k * 1.4), rl.Color(200, 190, 170, 200))
        f = d.fare
        if f is not None:
            tgt = f.pickup if f.phase in ("waiting", "boarding") else f.dest
            col = rl.Color(255, 190, 80, 255) if tgt is f.pickup else rl.Color(140, 200, 255, 255)
            rl.draw_circle_v(sp(tgt.x, tgt.z), 7, col)
        c = sp(d.ax, d.az)
        hx, hz = math.cos(d.cab_heading), math.sin(d.cab_heading)
        tip = rl.Vector2(c.x + hx * 12, c.y + hz * 12)
        l_ = rl.Vector2(c.x - hx * 6 + hz * 6, c.y - hz * 6 - hx * 6)
        r_ = rl.Vector2(c.x - hx * 6 - hz * 6, c.y - hz * 6 + hx * 6)
        rl.draw_triangle(tip, l_, r_, rl.Color(255, 240, 200, 255))
        rl.draw_triangle(tip, r_, l_, rl.Color(255, 240, 200, 255))

    def _pause(self, hud: dict, x0: float, w: float, h: float, u: float) -> None:
        rl = self.rl
        rl.draw_rectangle(int(x0), 0, int(w), int(h), rl.Color(10, 8, 6, 170))
        cx = x0 + w / 2
        y = h * 0.16
        self._text("Victorian Ride", cx, y, 90 * u, (255, 220, 150), "center")
        y += 120 * u
        for line in hud.get("pause_lines", []):
            self._text(line, cx, y, 30 * u, align="center")
            y += 42 * u
