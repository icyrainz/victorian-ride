from victorian_ride.app import Gaits
from victorian_ride.feel import ffb_events, snapshot
from victorian_ride.rig.state import Beat, FfbCue, InputState
from victorian_ride.sim import Crash, Drive, Hoof, Kerb
from victorian_ride.town import ashcombe


def inp(**kw) -> InputState:
    for k in ("down", "pressed", "system", "bound"):
        if k in kw:
            kw[k] = frozenset(kw[k])
    return InputState(**kw)


def test_shifter_gates_are_gaits_and_neutral_halts():
    g = Gaits()
    assert g.update(inp(down={"gate2"}, bound={"gate2", "gate1"})) == "trot"
    assert g.update(inp(bound={"gate2", "gate1"})) == "halt"
    assert g.update(inp(down={"gate5"}, bound={"gate5"})) == "back"


def test_keys_latch_a_gait_and_step_it():
    g = Gaits()
    assert g.update(inp(pressed={"gate3"})) == "canter"
    assert g.update(inp()) == "canter"                    # the key is up: the gait stays
    assert g.update(inp(system={"menu_down"})) == "trot"
    assert g.update(inp(pressed={"gate6"})) == "halt"


def test_ffb_mapping():
    ev = ffb_events([Hoof(1.0, "trot", 0.45, "road"), Kerb(1.0, -1, 0.5, True), Crash(1.0, 1, 0.8)])
    assert isinstance(ev[0], Beat) and ev[0].strength == 0.45
    assert isinstance(ev[1], FfbCue) and ev[1].name == "rumble"
    assert isinstance(ev[2], FfbCue) and ev[2].name == "kick" and ev[2].dir == -1.0


def test_snapshot_carries_the_horse_intent():
    d = Drive(ashcombe(), seed=1)
    d.intent = 0.3
    s = snapshot(d, InputState(), True)
    assert s.phase == "play" and s.echo == "listen" and s.echo_target == 0.3
    assert snapshot(d, InputState(), False).phase == "paused"
