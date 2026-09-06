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
d = OutcomeDetector(N); res = None
for i, (s, p) in enumerate([(0, 0)] * 10 + [(2000, 0)] + [(70000, 5000)] * 30):   # parasites at 4%: no takeover verdict
    res = res or d.update(i * 256, s, p)
assert res is None, res
d = OutcomeDetector(N); res = None
for i, (s, p) in enumerate([(0, 0)] * 10 + [(2000, 0)] + [(70000, 5000)] * 5 + [(70000, 500)] * 8):   # parasites fade: takeover
    res = res or d.update(i * 256, s, p)
assert res and res.startswith('takeover'), res
print("ok  outcome detector: none, takeover, extinction, unresolved, streak reset, parasite load blocks takeover")
