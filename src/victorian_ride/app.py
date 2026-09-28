"""The game loop: input -> horse and cab -> force feedback, sound, view.

Force feedback runs only while driving. Pause, focus loss, exit or any error stops
it (Torque Hero's engine latches until an explicit resume). The first run starts at
gain 0.2 (SPEC 5 rule 12 of Torque Hero) until the player raises it.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .rig.config import Config, config_dir
from .rig.ffb import rate_text as rate_text_
from .rig.state import GATES, InputState

log = logging.getLogger(__name__)

FIRST_RUN_GAIN = 0.2
GAIN_STEP = 0.05
TORQUEHERO_FILES = ("bindings.json", "ffb.json")
GAIT_ORDER = ("back", "halt", "walk", "trot", "canter", "gallop")
GATE_GAIT = {0: "halt", 1: "walk", 2: "trot", 3: "canter", 4: "gallop", 5: "back", 6: "back"}

KEYMAP: dict[str, tuple[str, ...]] = {
    "steer_left": ("A", "LEFT"),
    "steer_right": ("D", "RIGHT"),
    "brake": ("S", "DOWN"),
    "handbrake": ("SPACE",),
    "gate1": ("ONE",), "gate2": ("TWO",), "gate3": ("THREE",), "gate4": ("FOUR",),
    "gate5": ("R",),                      # back up
    "gate6": ("N", "GRAVE"),              # halt (keyboard only; on a shifter neutral is halt)
    "paddle_l": ("Q",),                   # "easy there": calms a shy
    "paddle_r": ("E",),                   # a click: a little more pace
    "menu_up": ("W", "UP"),               # a gait faster
    "menu_down": ("X",),                  # a gait slower
    "menu_ok": ("ENTER",),
    "menu_back": ("BACKSPACE",),
    "pause": ("ESCAPE", "P"),
    "vol_down": ("NINE",),
    "vol_up": ("ZERO",),
    "ffb_down": ("LEFT_BRACKET",),
    "ffb_up": ("RIGHT_BRACKET",),
}

PAUSE_LINES = [
    "The wheel is the reins. Let go and Bess keeps to her lane.",
    "Pull steadily before a junction to choose the turn. Hold against her to overrule.",
    "Shifter: 1 walk, 2 trot, 3 canter, 4 gallop, neutral halt, 5/6 back up.",
    "Keys: A/D reins, 1-4 gaits, N halt, R back, W/X faster/slower, S brake.",
    "Q calms her, E urges her. C chase camera, M map, F5 back to the stable.",
    "PgUp/PgDn field of view, Home/End look up/down. [ ] force feedback, 9/0 volume.",
    "Stop beside the waving fare to pick up. Stop by the blue light to set down.",
    "",
    "Click this window, then press P or Enter to drive.",
]


@dataclass
class AppSettings:
    ffb_first_run_done: bool = False
    volume: float = 0.8

    @classmethod
    def load(cls) -> AppSettings:
        try:
            d = json.loads((config_dir() / "app.json").read_text())
            return cls(ffb_first_run_done=d.get("ffb_first_run_done") is True,
                       volume=float(d.get("volume", 0.8)))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        p = config_dir() / "app.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2) + "\n")


def torquehero_dir() -> Path:
    here = config_dir()
    return here.parent / "torquehero"


def import_from_torquehero() -> list[str]:
    """Copy the rig's bindings and verified force feedback sign from Torque Hero, once:
    only files this game does not have yet."""
    src, dst = torquehero_dir(), config_dir()
    done = []
    for name in TORQUEHERO_FILES:
        if (src / name).exists() and not (dst / name).exists():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / name, dst / name)
            done.append(name)
    return done


class Gaits:
    """The gait asked for: the shifter's gate when a shifter is bound (neutral = halt),
    else number keys latch a gait and W/X step it."""

    def __init__(self) -> None:
        self.asked = "halt"

    def update(self, inp: InputState) -> str:
        if any(g in inp.bound for g in GATES):
            self.asked = GATE_GAIT[inp.gate]
            return self.asked
        for i, g in enumerate(GATES, 1):
            if g in inp.pressed:
                self.asked = GATE_GAIT[i] if i <= 5 else "halt"
        k = GAIT_ORDER.index(self.asked)
        if "menu_up" in inp.system:
            self.asked = GAIT_ORDER[min(len(GAIT_ORDER) - 1, k + 1)]
        if "menu_down" in inp.system:
            self.asked = GAIT_ORDER[max(0, k - 1)]
        return self.asked


class Game:
    def __init__(self, args, cfg: Config, settings: AppSettings) -> None:
        from .audio import open_audio
        from .render import RaylibKeys, RenderSettings, View, Window, parse_span
        from .rig.ffb import FfbEngine, FfbSettings, NullFfb, open_backend
        from .rig.input import CompositeInput, KeyboardMouseInput, SdlInput
        from .sim import Drive
        from .town import ashcombe

        self.args, self.cfg, self.settings = args, cfg, settings
        self.closers: list = []
        span = parse_span(args.span) if args.span else None
        self.win = Window(size=(1600, 900), span=span, fullscreen=args.fullscreen, hidden=args.hidden,
                          settings=RenderSettings.load())
        self.closers.append(self.win.close)
        self.town = ashcombe()
        triple = {"single": False, "triple": True}.get(args.view)
        self.view = View(self.win, self.town, triple)
        keys = RaylibKeys()
        self.keys = keys
        kb = KeyboardMouseInput(keys, cfg, KEYMAP)
        self.inp = kb
        self.backend = NullFfb(reason="--kb" if args.kb else "no wheel")
        if not args.kb:
            try:
                from .rig.bindings import Bindings

                bindings = Bindings.load()
                sdl = SdlInput(bindings, cfg)
                self.inp = CompositeInput(sdl, kb, cfg)
                steer = bindings.get("steer")
                self.wheel_range = getattr(steer, "range_deg", None) if steer else None
            except Exception as e:
                log.warning("no rig input (%s): keyboard only", e)
        self.closers.append(self.inp.close)
        ffb_settings = FfbSettings.load()
        if args.no_ffb or args.hidden:
            self.backend = NullFfb(reason="--no-ffb" if args.no_ffb else "--hidden")
        elif not (config_dir() / "ffb.json").exists():
            # the sign of every force was verified on the rig with Torque Hero's checklist; never guess it
            self.backend = NullFfb(reason="no verified ffb.json: do Torque Hero's FFB checklist first")
        elif ffb_settings.sign_error:
            self.backend = NullFfb(reason=ffb_settings.sign_error)
        elif hasattr(self.inp, "steer_joystick"):
            self.backend = open_backend(self.inp.steer_joystick)
        self.real_ffb = not isinstance(self.backend, NullFfb)
        self.ffb = FfbEngine(self.backend, cfg, ffb_settings, wheel_range_deg=getattr(self, "wheel_range", None))
        if hasattr(self.inp, "on_steer_lost"):
            self.inp.on_steer_lost(lambda _h: self.ffb.device_lost())
        gain = args.ffb_gain if args.ffb_gain is not None else (
            cfg.ffb_gain if settings.ffb_first_run_done else min(cfg.ffb_gain, FIRST_RUN_GAIN))
        self.ffb.set_gain(gain)
        self.audio = open_audio(not args.no_audio and not args.hidden, settings.volume)
        self.closers.insert(0, self.audio.close)
        self.drive = Drive(self.town)
        self.gaits = Gaits()
        self.reins = args.reins or ("spring" if self.real_ffb else "offset")
        self.playing = bool(args.autodrive)
        self.focused_seen = bool(args.hidden)
        self.report: dict = {}
        self.inp_state = InputState()
        self._last = None
        self._calm_until = -1.0
        log.info("force feedback: %s, reins: %s, gain %.2f", "on" if self.real_ffb else
                 f"off ({getattr(self.backend, 'reason', '')})", self.reins, self.ffb.gain)

    # --- controls ---

    def _focus(self) -> bool:
        if self.win.hidden or self.args.autodrive:
            return True
        f = self.win.focused()
        rl = self.win.rl
        if f and not self.focused_seen and (rl.get_key_pressed() or rl.is_mouse_button_pressed(0)):
            self.focused_seen = True
        return f and self.focused_seen

    def _system(self, inp: InputState, focused: bool) -> None:
        sys_ = inp.system
        if self.playing and not focused:
            self.playing = False
            self.ffb.stop_all()
            log.info("window not focused: paused, force feedback stopped")
        elif "pause" in sys_:
            if self.playing:
                self.playing = False
                self.ffb.stop_all()
            elif focused:
                self._resume()
        elif "menu_ok" in sys_ and not self.playing and focused:
            self._resume()
        if "ffb_up" in sys_ or "ffb_down" in sys_:
            up = "ffb_up" in sys_
            g = self.ffb.set_gain(round(self.ffb.gain + (GAIN_STEP if up else -GAIN_STEP), 3))
            self.cfg.ffb_gain = g
            if up and not self.settings.ffb_first_run_done:
                self.settings.ffb_first_run_done = True
            self._save()
        if "vol_up" in sys_ or "vol_down" in sys_:
            v = self.settings.volume + (0.1 if "vol_up" in sys_ else -0.1)
            self.settings.volume = round(max(0.0, min(1.0, v)), 2)
            self.audio.volume = self.settings.volume
            self._save()

    def _resume(self) -> None:
        self.playing = True
        self.ffb.resume()

    def _save(self) -> None:
        try:
            self.cfg.save()
            self.settings.save()
        except OSError as e:
            log.warning("cannot save settings: %s", e)

    def _view_keys(self) -> None:
        rl, st, v = self.win.rl, self.win.settings, self.view
        changed = False
        if rl.is_key_pressed(rl.KEY_C):
            v.chase = not v.chase
        if rl.is_key_pressed(rl.KEY_M):
            v.show_map = not v.show_map
        if rl.is_key_pressed(rl.KEY_F5):
            from .sim import Drive

            money, fares = self.drive.money, self.drive.fares_done
            self.drive = Drive(self.town)
            self.drive.money, self.drive.fares_done = money, fares
            v._last_pos = None
        for key, attr, step in ((rl.KEY_PAGE_UP, "fov", 1.0), (rl.KEY_PAGE_DOWN, "fov", -1.0),
                                (rl.KEY_HOME, "seat_pitch", 1.0), (rl.KEY_END, "seat_pitch", -1.0)):
            if rl.is_key_pressed(key) or rl.is_key_pressed_repeat(key):
                if attr == "fov":
                    name = "triple_hfov" if v.triple else "single_hfov"
                    setattr(st, name, max(20.0, min(120.0, getattr(st, name) + step)))
                else:
                    st.seat_pitch = max(-30.0, min(10.0, st.seat_pitch + step))
                changed = True
        if changed:
            try:
                st.save()
            except OSError:
                pass

    # --- the frame ---

    def frame(self, capture: str | None = None) -> None:
        from .feel import ffb_events, snapshot
        from .rig.ffb import rate_text
        from .sim import Controls

        now = time.perf_counter()
        dt = 0.0 if self._last is None else min(0.1, now - self._last)
        if self.args.hidden:
            dt = 1 / 60
        self._last = now
        inp = self.inp.poll(dt)
        self.inp_state = inp
        focused = self._focus()
        self._system(inp, focused)
        self._view_keys()
        asked = self.gaits.update(inp)
        if "paddle_l" in inp.pressed:
            self._calm_until = self.drive.t + 1.0
        rein = max(-1.0, min(1.0, inp.steer))
        centre = None
        if self.reins == "spring" and self.real_ffb and not self.report.get("latched", True):
            centre = self.report.get("echo_centre")
        if self.args.autodrive:
            asked = "trot"
        events: list = []
        if self.playing:
            c = Controls(rein=rein, centre=centre,
                         brake=max(inp.brake, inp.handbrake), gait=asked,
                         calm=self.drive.t < self._calm_until, urge="paddle_r" in inp.pressed)
            if self.reins == "direct":
                c.centre = None
            events = self.drive.step(dt, c)
        snap = snapshot(self.drive, inp, self.playing)
        self.report = self.ffb.update(ffb_events(events), snap, dt)
        self.audio.handle(events)
        surf = self.town.surface(self.drive.ax, self.drive.az)
        self.audio.update(self.drive.v, 1.0 if surf == "road" else 0.3, not self.playing)
        ffb = (f"FFB {self.ffb.gain * 100:.0f}%" if self.real_ffb
               else f"FFB off ({getattr(self.backend, 'reason', '')})")
        hud = {
            "status": f"{ffb}  ·  {rate_text(self.report)}  ·  reins {self.reins}",
            "paused": not self.playing,
            "pause_lines": PAUSE_LINES if focused else [*PAUSE_LINES[:-1], "Click this window first."],
        }
        self.view.draw(self.drive, hud, capture)

    def run(self) -> int:
        gc.collect()
        gc.freeze()
        frames = self.args.frames
        n = 0
        try:
            while not self.win.should_close() and (frames is None or n < frames):
                cap = self.args.capture if self.args.capture and frames is not None and n == frames - 1 else None
                try:
                    self.frame(cap)
                except BaseException:
                    self.ffb.stop_all()
                    raise
                n += 1
        finally:
            if self.report.get("fps"):
                log.info("last frames: %s", rate_text_(self.report))
            self.close()
        return 0

    def close(self) -> None:
        try:
            self.ffb.close()
        finally:
            for c in self.closers:
                try:
                    c()
                except Exception as e:
                    log.warning("close: %s", e)


def _play(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.ffb_gain is not None and not (0.0 <= args.ffb_gain <= 1.0 and math.isfinite(args.ffb_gain)):
        print("play: --ffb-gain must be between 0 and 1", file=sys.stderr)
        return 2
    for name in import_from_torquehero():
        log.info("copied %s from Torque Hero's settings", name)
    cfg = Config.load()
    settings = AppSettings.load()
    game = Game(args, cfg, settings)
    try:
        return game.run()
    except KeyboardInterrupt:
        return 130


def add_cli(sub) -> None:
    p = sub.add_parser("play", help="drive the cab")
    p.add_argument("--span", help="one borderless window over the triples, e.g. 5760x1080+0+0")
    p.add_argument("--fullscreen", action="store_true", help="borderless fullscreen on the current monitor")
    p.add_argument("--view", choices=("single", "triple"), help="panes (default: triple when the window is 3 wide)")
    p.add_argument("--kb", action="store_true", help="keyboard only: do not open the rig")
    p.add_argument("--no-ffb", action="store_true", help="no force feedback")
    p.add_argument("--ffb-gain", type=float, help="force feedback gain 0..1 (first run: 0.2)")
    p.add_argument("--reins", choices=("spring", "offset", "direct"),
                   help="spring: the wheel follows the horse (needs FFB); offset: the wheel adds to the horse's "
                        "rein; direct: no horse sense")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--hidden", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--frames", type=int, help=argparse.SUPPRESS)
    p.add_argument("--capture", help=argparse.SUPPRESS)
    p.add_argument("--autodrive", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=_play)
