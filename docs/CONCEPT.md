# Victorian Ride — concept and first prototype

Working title. Status: concept agreed in the brainstorm on 2026-09-27. Nothing is built yet.

## 1. Pitch

You drive a hansom cab in a small Victorian English market town. You pick up fares and
parcels and take them across town. The sim rig is the driver's seat: the wheel is the reins.

The hook: **the horse pulls back.** A car does exactly what the wheel says. A horse has its
own intent: it keeps to the street, leans toward a water trough, wants to go home at dusk,
and shies from a dog. The direct-drive base lets you feel that intent. You drive by agreeing
or disagreeing with an animal.

Tone: a cosy sim. Calm drives, fog, gas lamps, the sound of hooves on cobbles, and a horse
you get attached to. No fail state. Rough or slow driving costs tips, not lives.

## 2. Decisions from the brainstorm

| Topic | Decision |
|---|---|
| Reins | The wheel is the reins. The horse turns with a short lag. |
| Horse AI | The horse follows the street by itself. You choose the turns and overrule it by torque. |
| Temper | Gentle character: light tugs, rare mild spooks. Horses with different tempers come later. |
| Tone | Cosy sim (Euro Truck Simulator in 1888). |
| Vehicle | Hansom cab first: one horse, two wheels, passengers, driver seat high behind the roof. |
| Town | A small, hand-made, fictional market town. |
| Tech | Python 3.12 + uv + raylib 3D. Reuse Torque Hero's input, bindings, FFB and companion code. |
| Prototype look | A box town with cheap mood: fog, dusk palette, gas-lamp lights, hoof sounds. |

## 3. Controls

| Control | Use |
|---|---|
| Wheel | Rein direction. Hands off: the wheel follows the horse. Hold against it: you overrule the horse. |
| Shifter | Ask for a gait: 1 walk, 2 trot, 3 canter, 4 gallop, R back up, neutral = halt. The horse changes gait over about a second, and only if it has the stamina. |
| Brake pedal | Brake shoe on the cab wheels. Needed on the hill. |
| Handbrake | Park brake. While a fare boards, the horse stands still only if it is set. |
| Paddles | Right: a click to urge the horse on. Left: a soothing "whoa" to calm it. |
| Button box | Bell or "Oi!" to clear pedestrians. Rotary: master volume or FFB gain. |
| Throttle, clutch, STECS | Not used in the prototype. Kept free for later ideas (lamp, whip, map). |

Keyboard fallback for development on macOS, as in Torque Hero.

## 4. The horse and the cab

**Horse sense.** Each street has a lane line on its left side (British traffic keeps left).
The horse follows the line on its own. At a junction it goes straight on unless the wheel
asks for a turn. The horse's chosen steering angle becomes the centre of a movable software
spring on the wheel. This is Torque Hero's echo spring with the same limits.

**Rein input.** The difference between the wheel angle and the horse's chosen angle is how
hard you are pulling. A small pull changes lane or line. A firm pull at a junction makes the
turn. The horse's heading follows the rein with a lag of about 0.3 to 0.5 s.

**Gaits.** Walk about 1.5 m/s, trot about 4 m/s, canter about 6 m/s, gallop about 10 m/s.
Each gait has its own hoof rhythm in the sound and in the FFB. Stamina drains at canter and
gallop and recovers at walk and halt.

**Gentle character.** The horse tugs toward water troughs when thirsty and toward home at the
end of the day. Now and then it gives a mild shy (a dog, a dropped crate). All of these are
small, ramped changes to the spring centre, never sudden yanks.

**Cab physics.** The cab is a two-wheel trailer on shafts behind the horse. It cuts corners
(off-tracking): turn too early and a cab wheel hits the kerb. A kerb hit is a bump in the FFB
and a jolt for the passenger. Brake shoes slow the cab and the horse must not be pushed
downhill.

## 5. Force feedback

All of Torque Hero's safety rules apply without change (its SPEC.md sections 5 and 10, and
docs/FFB-CHECKLIST.md): a total torque budget, a stop latch, a dead-man on sustained forces,
ramps, a stop on pause, focus loss or exit, and a first run at gain 0.2.

| Effect | Type | Feel |
|---|---|---|
| Horse intent | Software spring with a movable centre | The horse's line. Centre moves at most 180°/s and stays within 45° of the wheel. |
| Gait rhythm | Short sine pulses | 4-beat walk, 2-beat trot, 3-beat canter. Low magnitude. |
| Road surface | Small periodic | Cobbles buzz, a smooth road is quiet, a rut grabs a little. |
| Kerb hit | One-shot constant | A short knock, inside the 0.6 one-shot cap. |
| Load | Damper | A loaded cab turns more heavily. |

## 6. The full game loop (later, not in the prototype)

- Fares: some want speed ("late for the 4:10 train"), some want comfort (a lady with a
  hatbox). Comfort drops with every jolt, kerb hit and hard stop.
- Parcels: fragile (eggs, a wedding cake), heavy (a crate of gin), urgent (a telegram).
- Tips and reputation. Spend them on a better horse, springs, a night lamp.
- Horse care: water, feed, rest at the stable. The horse's mood follows how you treat it.
- A day from dawn to night, gas lamps, the lamplighter, fog and rain.
- Traffic: pedestrians, other carts, an omnibus, a policeman.
- Top monitor: a street map with fares. Dash: pocket watch, fare meter, horse stamina.

## 7. The town

A fictional market town (placeholder name: Ashcombe). Small enough to learn by heart.

- Market square with stalls (tight turns, pedestrians)
- Railway station (fares on a timetable)
- River and a stone bridge (a narrow crossing)
- Church and green
- Pub (fares at night)
- One hill with a steep descent (brake test)
- The stable (home)

## 8. Tech

**Reuse from Torque Hero (copy, then adapt; extract a shared package only if both games live on):**

- `input.py`, `bindings.py`: multi-device SDL input, learn-mode binding, calibration, hot-plug.
- `ffb.py`: the effect engine and all safety rules. Add the horse-intent spring by adapting
  the echo spring.
- `config.py`, `app_settings.py`: config and settings patterns.
- `companion/`: the web server for the top monitor and dash (later milestone).
- `audio.py`: sounddevice output for hoof sounds and ambience.

**New:**

- 3D rendering. Torque Hero draws in 2D. Here, one borderless window spans the triples
  (5760x1080). Three raylib 3D cameras render into three viewports. The side cameras are
  yawed to match the angle of the side monitors, so the perspective is correct.
- The town: a street graph (nodes, edges, lane lines) and box buildings made from it.
- The horse and cab simulation: pure Python, unit-tested without a window or a rig.

Cost to move to Godot later: rewrite rendering and the town; keep input and FFB as a Python
sidecar that talks to Godot over UDP, because Godot's own FFB is rumble only.

## 9. Prototype 0: "is driving a horse with this rig fun?"

**In scope:** one horse, one hansom cab, a flat box town of a few blocks with the market
square and one hill, horse sense and the intent spring, shifter gaits with hoof rhythm, brake
and park brake, off-tracking and kerb hits, cheap mood (fog, dusk, lamps, hoof sounds), one
fare from A to B with a comfort meter and a tip at the end.

**Out of scope:** money, upgrades, traffic, weather, day cycle, companion screens, other
vehicles, menus beyond binding and calibration.

| Step | Work | Done when |
|---|---|---|
| P0.1 | uv project; copy input, bindings, FFB, config; triple-span window; three-camera 3D view of a box grid; free camera on keyboard. | Runs on macOS with the keyboard. Tests pass. |
| P0.2 | Horse and cab simulation: gaits, rein lag, trailer kinematics, off-tracking, brakes, kerb collision. | Unit tests cover gait changes, off-tracking and kerb hits. Drivable on the keyboard. |
| P0.3 | Street graph and horse sense: lane following, junction choice by rein, gentle tugs. | Hands off the keyboard, the horse follows streets. A held turn takes a junction. |
| P0.4 | FFB: intent spring, gait pulses, cobbles, kerb knock, load damper, all through the safety engine. | NullFfb tests pass. The FFB checklist passes on the rig at gain 0.2. |
| P0.5 | Mood and one fare: fog, dusk palette, lamp lights, hoof and ambience audio, pick-up, drop-off, comfort meter, tip. | A full fare can be driven on the rig. |

**Fun check on the rig after P0.5.** Drive for 15 minutes and answer:

1. Does the horse feel alive through the wheel, or like a laggy car?
2. Is hands-off lane following relaxing, or boring?
3. Is taking a junction at a trot satisfying?
4. Does the gait rhythm on the wheel add to the feel, or is it noise?
5. Do you want to do one more fare?

If most answers are yes, continue with the full loop. If not, change the horse model first.

## 10. Open questions (decide after the fun check)

- Town name and map layout in detail.
- A lever-reins mode as an option.
- When to move to Godot, if at all.
- VR on the Quest.
