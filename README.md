# Victorian Ride

Drive a hansom cab through Ashcombe, a small Victorian market town at dusk, on a
sim-racing rig. The wheel is the reins. Bess, your horse, keeps to her lane by
herself; the direct-drive base leans the wheel the way she wants to go. Let go and
she follows the street. Pull steadily before a junction to choose the turn. Hold
against her to overrule her.

This is prototype 0 (see [docs/CONCEPT.md](docs/CONCEPT.md)): one horse, one cab,
one town, one fare at a time. It exists to answer one question: is driving a horse
with this rig fun?

The rig layer (input, bindings, force feedback engine and its safety rules) is
copied from [Torque Hero](https://github.com/icyrainz/torquehero) (MIT).

## Run it on the rig (Windows)

The rig must already be set up for Torque Hero: uv installed, controls bound, and
the force feedback checklist passed. On its first start this game copies
`bindings.json` and `ffb.json` (with the verified force sign) from Torque Hero's
settings folder. Without a verified `ffb.json` it runs without force feedback.

In PowerShell, in this folder:

```powershell
uv run victorian-ride play --ffb-gain 0.2
```

Across the triples (use the same span as Torque Hero):

```powershell
uv run victorian-ride play --span 5760x1080-1920+0 --ffb-gain 0.2
```

Or edit and double-click `scripts\play.cmd` / `scripts\play-triples.cmd`.

The game starts paused. Click the window, then press P or Enter.

## Controls

| Rig | Keyboard | Does |
|---|---|---|
| Wheel | A / D | Reins. Hands off: Bess follows her lane. |
| Shifter 1-4 | 1-4 | Walk, trot, canter, gallop |
| Shifter neutral | N | Halt |
| Shifter 5 or 6 | R | Back up |
| (menu up / down) | W / X | A gait faster / slower |
| Brake pedal, handbrake | S, Space | The cab's brake. You need it down the hill. |
| Left paddle | Q | "Easy there": calms her after a shy |
| Right paddle | E | A click: a little more pace |
| Pause button | Esc or P | Pause (stops force feedback) |
| ffb_up / ffb_down | ] / [ | Force feedback gain, 0.05 per press |
| vol_up / vol_down | 0 / 9 | Volume |
| | C | Chase camera |
| | M | Map |
| | F5 | Back to the stable |
| | PgUp / PgDn | Field of view (saved) |
| | Home / End | Look up / down (saved) |

Fares: drive to the amber light, stop beside the waving passenger and wait while
they get in. Then drive to the blue light and stop. Hurried fares tip for speed,
nervous ones for a smooth ride. Kerbs, sharp turns, hard stops and a gallop on the
cobbles all cost comfort.

## What the wheel tells you

- **Bess's intent**: a spring whose centre is the rein she wants (Torque Hero's echo
  spring, same limits: 180 deg/s, never more than 45 deg from the wheel, cap 0.3).
- **Hoof beats**: a short pulse per footfall: 4 at a walk, 2 at a trot, 3 at a canter.
- **Kerbs**: a rumble when a cab wheel goes up or down a kerb. A wall: a kick.
- **Weight**: a damper, heavier with a passenger aboard.

`--reins` changes how the wheel and the horse combine: `spring` (default with force
feedback: the wheel follows her), `offset` (default without: the wheel adds to her
rein), `direct` (no horse sense).

## Develop (macOS, no rig)

```
uv run victorian-ride play --kb
uv run pytest
```

Settings live in `%APPDATA%\victorian-ride` (Windows) or
`~/Library/Application Support/victorian-ride` (macOS): `render.json` holds field of
view, seat pitch, fog, supersampling.
