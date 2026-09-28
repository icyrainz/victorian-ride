"""Synthesised sound: hooves on cobbles, iron tyres rumbling, kerb knocks, a bell when a
fare boards, coins when one pays, and a low street murmur. No sample files.

The sounddevice callback mixes one-shots from a queue and two loops whose levels the
game sets each frame. `NullAudio` does nothing (no device, or --no-audio).
"""
from __future__ import annotations

import logging
from collections import deque

import numpy as np

from .sim import Crash, FareEvent, Hoof, Kerb, Shy

log = logging.getLogger(__name__)

RATE = 48000
BLOCK = 512


def _env(n: int, decay: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (decay * RATE)).astype(np.float32)


def _noise(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def _lowpass(x: np.ndarray, a: float) -> np.ndarray:
    """One-pole lowpass, a = 0..1 (smaller = darker)."""
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def hoof(rng: np.random.Generator, pitch: float) -> np.ndarray:
    """A shod hoof on stone: a hard click, a hollow knock and a low thud."""
    n = int(0.09 * RATE)
    t = np.arange(n) / RATE
    click = _noise(rng, n) * _env(n, 0.004) * 0.6
    knock = (np.sin(2 * np.pi * 820 * pitch * t) + 0.6 * np.sin(2 * np.pi * 1370 * pitch * t)) * _env(n, 0.012)
    thud = np.sin(2 * np.pi * 110 * pitch * t) * _env(n, 0.03) * 0.8
    return ((click + knock * 0.5 + thud) * 0.5).astype(np.float32)


def bell() -> np.ndarray:
    n = int(1.6 * RATE)
    t = np.arange(n) / RATE
    parts = ((1.0, 1.0), (2.76, 0.5), (5.4, 0.25), (8.93, 0.12))
    x = sum(a * np.sin(2 * np.pi * 1320 * f * t) * _env(n, 0.5 / f ** 0.5) for f, a in parts)
    return (x * 0.18).astype(np.float32)


def coins(rng: np.random.Generator) -> np.ndarray:
    n = int(0.6 * RATE)
    out = np.zeros(n, np.float32)
    for k in range(4):
        start = int((0.05 + 0.09 * k + rng.uniform(0, 0.03)) * RATE)
        m = n - start
        t = np.arange(m) / RATE
        f = rng.uniform(2400, 3600)
        ring = sum(np.sin(2 * np.pi * f * r * t) for r in (1.0, 1.51, 2.37)) * _env(m, 0.06)
        out[start:] += ring.astype(np.float32) * 0.1
    return out


def knock(rng: np.random.Generator, level: float) -> np.ndarray:
    """A cab wheel on a kerb, or against a wall at a higher level."""
    n = int(0.35 * RATE)
    t = np.arange(n) / RATE
    body = np.sin(2 * np.pi * 70 * t) * _env(n, 0.08) + 0.5 * np.sin(2 * np.pi * 180 * t) * _env(n, 0.04)
    rattle = _lowpass(_noise(rng, n), 0.3) * _env(n, 0.05) * 0.8
    return ((body + rattle) * 0.5 * level).astype(np.float32)


def snort(rng: np.random.Generator) -> np.ndarray:
    n = int(0.5 * RATE)
    env = np.minimum(1.0, np.arange(n) / (0.04 * RATE)) * _env(n, 0.15)
    return (_lowpass(_noise(rng, n), 0.15) * env * 0.9).astype(np.float32)


class AudioEngine:
    """Every buffer is made once at start; the callback only adds and scales arrays."""

    LOOP_S = 4.0

    def __init__(self, volume: float = 0.8) -> None:
        import sounddevice as sd

        rng = np.random.default_rng(7)
        self._rng = rng
        self.volume = volume
        self._hooves = [hoof(rng, p) for p in np.linspace(0.9, 1.1, 8)]
        self._bell = bell()
        self._coins = coins(rng)
        self._snort = snort(rng)
        self._knocks = [knock(rng, 1.0) for _ in range(3)]
        n = int(self.LOOP_S * RATE)
        self._smooth = self._loop(_lowpass(_noise(rng, n), 0.02))    # tyres on a smooth road
        self._cobble = self._loop(_lowpass(_noise(rng, n), 0.06))    # tyres on cobbles
        self._murmur = self._loop(_lowpass(_noise(rng, n), 0.01)) * 0.25
        self._pos = 0
        self._queue: deque[tuple[np.ndarray, float]] = deque()
        self._voices: list[list] = []
        self.rumble = 0.0          # 0..1: iron tyres on stone, from speed
        self.rough = 1.0           # 1 on cobbles, 0 on a smooth road
        self._rumble_now = 0.0
        self._paused = False
        self._stream = sd.OutputStream(samplerate=RATE, channels=2, blocksize=BLOCK, dtype="float32",
                                       callback=self._callback)
        self._stream.start()

    @staticmethod
    def _loop(x: np.ndarray) -> np.ndarray:
        """Normalised, with the ends crossfaded so it loops without a click."""
        x = x / (np.abs(x).max() or 1.0)
        f = int(0.1 * RATE)
        ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
        x[:f] = x[:f] * ramp + x[-f:] * (1 - ramp)
        return x[:-f].astype(np.float32)

    def _take(self, loop: np.ndarray, frames: int) -> np.ndarray:
        idx = (self._pos + np.arange(frames)) % len(loop)
        return loop[idx]

    def _callback(self, outdata, frames, _time, _status) -> None:
        mix = np.zeros(frames, np.float32)
        while self._queue:
            buf, gain = self._queue.popleft()
            self._voices.append([buf, 0, gain])
        keep = []
        for v in self._voices:
            buf, pos, gain = v
            chunk = buf[pos:pos + frames]
            mix[:len(chunk)] += chunk * gain
            v[1] = pos + frames
            if v[1] < len(buf):
                keep.append(v)
        self._voices = keep
        target = 0.0 if self._paused else self.rumble
        ramp = np.linspace(self._rumble_now, target, frames, dtype=np.float32)
        self._rumble_now = target
        r = self.rough
        tyres = self._take(self._cobble, frames) * r + self._take(self._smooth, frames) * (1 - r)
        mix += tyres * ramp * 0.5
        if not self._paused:
            mix += self._take(self._murmur, frames)
        self._pos += frames
        mix *= self.volume
        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:, 0] = mix
        outdata[:, 1] = mix

    def play(self, buf: np.ndarray, gain: float = 1.0) -> None:
        if not self._paused:
            self._queue.append((buf, gain))

    def handle(self, events: list) -> None:
        for e in events:
            if isinstance(e, Hoof):
                g = (0.35 + 0.9 * e.strength) * (0.7 if e.surface == "pavement" else 1.0)
                self.play(self._hooves[int(self._rng.integers(len(self._hooves)))], g)
            elif isinstance(e, Kerb):
                self.play(self._knocks[int(self._rng.integers(3))], 0.4 + 0.6 * e.severity)
            elif isinstance(e, Crash):
                self.play(self._knocks[int(self._rng.integers(3))], 1.4 + e.severity)
            elif isinstance(e, Shy):
                self.play(self._snort, 0.8)
            elif isinstance(e, FareEvent):
                self.play(self._coins if e.what == "alight" else self._bell, 1.0)

    def update(self, speed: float, rough: float, paused: bool) -> None:
        self._paused = paused
        self.rumble = min(1.0, abs(speed) / 8.0)
        self.rough = rough

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class NullAudio:
    volume = 0.0

    def handle(self, events: list) -> None:
        pass

    def update(self, speed: float, rough: float, paused: bool) -> None:
        pass

    def close(self) -> None:
        pass


def open_audio(enabled: bool, volume: float):
    if not enabled:
        return NullAudio()
    try:
        return AudioEngine(volume)
    except Exception as e:   # no device, no PortAudio
        log.warning("no sound: %s", e)
        return NullAudio()
