#!/usr/bin/env python3
"""The outcome detector on synthetic self-replicator counts (tests every 256 epochs)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from bff_soup import OutcomeDetector

N = 131072
def run(seq):
    d = OutcomeDetector(N)
    for i, v in enumerate(seq):
        r = d.update(i * 256, v)
        if r: return i * 256, r
    return None, None

e, r = run([0] * 50); assert r is None, r
e, r = run([0] * 10 + [2000] + [70000] * 20); assert r and r.startswith('takeover') and e == 256 * 18, (e, r)
e, r = run([0] * 10 + [2000, 9000, 12000, 8000, 3000] + [0] * 10); assert r and r.startswith('extinction') and e == 256 * 18, (e, r)
e, r = run([0] * 10 + [5000] * 200); assert r and r.startswith('unresolved') and e == 256 * (10 + 128), (e, r)
e, r = run([0] * 10 + [2000] + [70000] * 3 + [0] * 3 + [70000] * 8); assert r.startswith('takeover'), r   # streaks reset
def first(seq):
    d = OutcomeDetector(N); res = None
    for i, (s, p) in enumerate(seq):
        res = res or d.update(i * 256, s, p)
        if res: return i * 256, res
    return None, None
# a constant parasite load (mutation) does not block the verdict
e, r = first([(0, 0)] * 10 + [(2000, 0)] + [(70000, 7000)] * 30); assert r and r.startswith('takeover') and e == 256 * 18, (e, r)
# a rising load restarts the streak until it stops rising
rising = [(70000, p) for p in (0, 3000, 6000, 9000, 12000, 15000, 18000, 21000, 24000, 27000, 30000)]
# every rising step restarts the streak, so the last rising test is streak member 1 and the 7th flat test the 8th
e, r = first([(0, 0)] * 10 + [(2000, 0)] + rising + [(70000, 30000)] * 8); assert r and r.startswith('takeover') and e == 256 * (11 + len(rising) - 1 + 7), (e, r)
e, r = first([(0, 0)] * 10 + [(2000, 0)] + rising + [(70000, 33000), (70000, 36000)]); assert r is None, r
# a load that fades: takeover
e, r = first([(0, 0)] * 10 + [(2000, 0)] + [(70000, 5000)] * 5 + [(70000, 500)] * 8); assert r and r.startswith('takeover'), r
print("ok  outcome detector: none, takeover, extinction, unresolved, streak reset, constant parasite load passes, rising load blocks")
