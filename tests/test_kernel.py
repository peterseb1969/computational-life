#!/usr/bin/env python3
"""
Regression tests for the BFF kernel. Run: ../.venv/bin/python tests/test_kernel.py

1. The Numba interpreter (with stuck-program detection) must produce the same
   tapes as a plain reference interpreter that always runs out the step budget.
2. The fixture replicators must pass the self-replication test; random programs must not.
3. A short run must be reproducible from its own checkpoints (replay exactness).
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import bff_core as core  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')


def reference_evaluate(tape, max_steps, heads=False):
    """Original semantics: runs until halt or budget, no cycle detection."""
    head0 = head1 = pc = 0
    n = len(tape)
    if heads:
        head0, head1, pc = tape[0] & (n - 1), tape[1] & (n - 1), 2
    for _ in range(max_steps):
        if pc < 0 or pc >= n:
            break
        head0 &= n - 1
        head1 &= n - 1
        cmd = tape[pc]
        if cmd == 60: head0 -= 1
        elif cmd == 62: head0 += 1
        elif cmd == 123: head1 -= 1
        elif cmd == 125: head1 += 1
        elif cmd == 43: tape[head0] = (int(tape[head0]) + 1) & 0xFF
        elif cmd == 45: tape[head0] = (int(tape[head0]) - 1) & 0xFF
        elif cmd == 46: tape[head1] = tape[head0]
        elif cmd == 44: tape[head0] = tape[head1]
        elif cmd == 91:
            if tape[head0] == 0:
                depth, pc = 1, pc + 1
                while pc < n and depth > 0:
                    if tape[pc] == 93: depth -= 1
                    elif tape[pc] == 91: depth += 1
                    pc += 1
                pc -= 1
                if depth != 0: break
        elif cmd == 93:
            if tape[head0] != 0:
                depth, pc = 1, pc - 1
                while pc >= 0 and depth > 0:
                    if tape[pc] == 91: depth -= 1
                    elif tape[pc] == 93: depth += 1
                    pc -= 1
                pc += 1
                if depth != 0: break
        pc += 1
    return tape


def test_interpreter_matches_reference():
    rng = np.random.default_rng(0)
    reps = np.load(os.path.join(ROOT, 'testdata', 'replicators_run1.npy'))
    cases = []
    for _ in range(150):                                   # random tapes with a high instruction density
        t = rng.integers(0, 256, 128, dtype=np.uint8)
        mask = rng.random(128) < 0.35
        t[mask] = rng.choice(list(core.COMMAND_BYTES), mask.sum())
        cases.append(t)
    for r in reps:                                          # replicators against random / each other
        cases.append(np.concatenate([r, rng.integers(0, 256, 64, dtype=np.uint8)]))
        cases.append(np.concatenate([r, reps[rng.integers(len(reps))]]))
    for heads in (False, True):
        for budget in (8192, 32768):
            for i, t in enumerate(cases):
                a = t.copy(); b = t.copy()
                core.evaluate(a, budget, heads)
                reference_evaluate(b, budget, heads)
                assert np.array_equal(a, b), f"tape {i} differs from reference at budget {budget}, heads={heads}"
    print(f"ok  interpreter == reference on {len(cases)} tapes x 2 budgets x 2 head modes")


def test_selfrep_fixtures():
    reps = np.load(os.path.join(ROOT, 'testdata', 'replicators_run1.npy'))
    rnd = np.load(os.path.join(ROOT, 'testdata', 'random_programs.npy'))
    s = core.selfrep_test(np.vstack([reps, rnd]), seed=1)
    assert (s[:len(reps)] >= core.SELFREP_THRESHOLD).all(), s[:len(reps)]
    assert (s[len(reps):] < core.SELFREP_THRESHOLD).all(), s[len(reps):]
    print(f"ok  selfrep: replicators {s[:len(reps)].tolist()}, random max {int(s[len(reps):].max())}")


def test_replay_exactness(init_dist=None):
    from bff_soup import run_soup
    from bff_query import Run
    with tempfile.TemporaryDirectory() as d:
        rd = os.path.join(d, 'r')
        run_soup(num_programs=2048, max_epochs=120, seed=5, run_dir_path=rd, checkpoint_interval=40,
                 print_interval=10 ** 9, archive=False, init_dist=init_dist)
        run = Run(rd)
        for e in (40, 80):
            ck, _ = core.load_checkpoint(run.rd.checkpoint_path(e))
            assert np.array_equal(run.soup_at(e), ck), f"replay to {e} differs from checkpoint"
        log = run.log()
        share = log['sample_selfrep_share']
        assert share[0] >= 0 and share.max() <= 1, "sample replicator share not logged"
        os.remove(run.rd.checkpoint_path(0))            # replay must regenerate the initial soup itself
        run._soup_cache.clear(); run._cursor = None
        assert np.array_equal(run.soup_at(40), core.load_checkpoint(run.rd.checkpoint_path(40))[0]), \
            "replay from a regenerated initial soup differs"
        run.db.close()
    print("ok  replay matches checkpoints" + (f" (init {init_dist})" if init_dist else ""))


def test_init_dist():
    assert core.parse_init_dist(None) is None and core.parse_init_dist('uniform') is None
    assert np.array_equal(core.random_soup(64, 7), core.random_soup(64, 7, None))
    d = core.parse_init_dist('ops100')
    assert abs(d.sum() - 1) < 1e-12 and d[~core.IS_CMD].sum() == 0 and np.allclose(d[core.IS_CMD], 0.1)
    d = core.parse_init_dist('ops50')
    assert abs(d[core.IS_CMD].sum() - 0.5) < 1e-12 and np.allclose(d[~core.IS_CMD], 0.5 / 246)
    d = core.parse_init_dist('[:1 0:1 rest:2')
    assert abs(d[ord('[')] - 0.25) < 1e-12 and abs(d[0] - 0.25) < 1e-12 and abs(d[5] - 0.5 / 254) < 1e-12
    soup = core.random_soup(4096, 3, core.parse_init_dist('winners'))
    share = core.IS_CMD[soup].mean()
    assert 0.47 < share < 0.53, share
    assert (soup == 0).mean() > 0.03 and (soup == 64).mean() > 0.015
    assert np.array_equal(soup, core.random_soup(4096, 3, core.parse_init_dist('winners'))), "not reproducible"
    assert core.init_dist_label('ops50') == '-init-ops50' and core.init_dist_label('[:1 rest:1') == '-init-custom'
    print("ok  initial byte distributions")


def test_hashmap_matches_dict():
    from bff_hash import HashMap
    rng = np.random.default_rng(0)
    ref, hm = {}, HashMap(1 << 12)
    for _ in range(30):
        k = rng.integers(0, 2 ** 63, 4000, dtype=np.int64).astype(np.uint64)
        v = rng.integers(0, 2 ** 40, 4000, dtype=np.int64)
        hm.insert(k, v)
        ref.update(zip(k.tolist(), v.tolist()))
        allk = np.array(list(ref), dtype=np.uint64)
        dk = allk[rng.random(allk.size) < 0.3]
        exp = np.array([ref[x] for x in dk.tolist()], dtype=np.int64)
        exp[::5] += 1                                        # wrong expected value: must not delete
        hm.delete(dk, exp)
        for x, e in zip(dk.tolist(), exp.tolist()):
            if ref[x] == e:
                del ref[x]
        assert len(hm) == len(ref)
        probe = np.concatenate([allk[:1000], rng.integers(0, 2 ** 63, 300, dtype=np.int64).astype(np.uint64)])
        assert np.array_equal(hm.get(probe), np.array([ref.get(x, -1) for x in probe.tolist()], dtype=np.int64))
    k, v = hm.items()
    assert len(k) == len(ref) and all(ref[x] == y for x, y in zip(k.tolist(), v.tolist()))
    print(f"ok  hashmap == dict ({len(ref)} entries, capacity {hm.capacity()})")
    # churn: constant live size with heavy insert/delete traffic must not grow the table
    hm = HashMap(1 << 12)
    caps = set()
    window = []
    for e in range(400):
        k = rng.integers(0, 2 ** 63, 2000, dtype=np.int64).astype(np.uint64)
        hm.insert(k, np.full(2000, e, dtype=np.int64))
        window.append(k)
        if len(window) > 16:
            hm.delete(window.pop(0))
        caps.add(hm.capacity())
    assert len(hm) == 16 * 2000 and max(caps) <= 1 << 18, (len(hm), sorted(caps))
    print(f"ok  hashmap capacity bounded under churn (max {max(caps)})")


def test_mirror_families():
    from bff_archive import cluster_families
    a = '<[[[[[,,.[.[[}<,]],<}[,<'
    keys = [a, a[::-1], a[:-1], '[[<,,,}]]}}]]},,,<[[', '[[<,,,}]]}}]]},,,<[[['[::-1]]
    groups = sorted(sorted(g) for g in cluster_families(keys))
    assert groups == [[0, 1, 2], [3, 4]], groups
    print("ok  mirror-image variants cluster into one family")


if __name__ == '__main__':
    import io, contextlib
    test_mirror_families()
    test_interpreter_matches_reference()
    test_selfrep_fixtures()
    test_hashmap_matches_dict()
    test_init_dist()
    with contextlib.redirect_stdout(io.StringIO()):
        test_replay_exactness()
        test_replay_exactness('winners')
    print("ok  replay matches checkpoints (uniform and winners initial soups)")
    print("all tests passed")
