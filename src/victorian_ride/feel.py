"""What the wheel tells the driver: the drive mapped onto Torque Hero's FFB engine.

The engine and its safety rules are unchanged (rig/ffb.py). Its slots get new meanings:
- echo spring: the horse's intent. `echo_target` is the rein the horse wants; the engine
  moves the spring centre toward it at most 180 deg/s and never more than 45 deg from the
  wheel, so the base leans the wheel the way the horse is going.
- beat pulse: each hoof strike (4 beats at a walk, 2 at a trot, 3 at a canter).
- rumble: a cab wheel going up or down a kerb. kick: a wheel hitting a wall.
- weight damper: the heft of the reins; heavier with a passenger aboard.
"""
from __future__ import annotations

from .rig.state import Beat, FfbCue, GameEvent, InputState, Snapshot
from .sim import Crash, Drive, Hoof, Kerb

SURFACE_FEEL = {"road": 1.0, "pavement": 0.7, "wall": 0.0}


def ffb_events(events: list) -> list[GameEvent]:
    out: list[GameEvent] = []
    for e in events:
        if isinstance(e, Hoof):
            out.append(Beat(e.t, e.strength * SURFACE_FEEL.get(e.surface, 1.0)))
        elif isinstance(e, Kerb):
            out.append(FfbCue(e.t, "rumble"))
        elif isinstance(e, Crash):
            out.append(FfbCue(e.t, "kick", dir=-float(e.side)))
    return out


def snapshot(drive: Drive, inp: InputState, playing: bool) -> Snapshot:
    riding = drive.fare is not None and drive.fare.phase == "riding"
    return Snapshot(
        phase="play" if playing else "paused",
        now=drive.t,
        input=inp,
        echo="listen",
        echo_target=drive.intent,
        weight=0.45 if riding else 0.25,
    )
