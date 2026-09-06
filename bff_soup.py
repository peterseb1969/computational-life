#!/usr/bin/env python3
"""
Numba-accelerated BFF Primordial Soup Simulation

Runs the "computational life" experiment: a soup of random 64-byte programs
is repeatedly paired up, each pair executed as one 128-byte BFF tape, and
split again. Self-replicators emerge from self-modification alone (no
mutation unless --mutation-prob is given).

Every run writes to a run directory (default runs/<seed>) containing the
metrics log, checkpoints, and the lineage records used by the analysis tools.
The pairing of each epoch is derived from (seed, epoch), so a run can be
resumed from any checkpoint and reproduces the original trajectory exactly.

Requires: pip install numba numpy brotli

Usage:
    python bff_soup.py --num 131072 --epochs 40000
    python bff_soup.py --resume runs/42            # continue latest checkpoint
    python bff_soup.py --resume runs/42/checkpoints/0000010240.dat --epochs 60000
"""

import argparse
import json
import os
import platform
import socket
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import bff_core as core
from bff_core import TAPE_SIZE, DEFAULT_MAX_STEPS, SELFREP_THRESHOLD, SELFREP_STRICT

SPIKE_TEST_SHARE = 0.01     # a species reaching this share (and doubling since the last test) is tested at once
SPIKE_MIN_LEN = 5           # ... if it has at least this many instructions
from bff_lineage import (RunDir, LineageWriter, truncate_log, migrate_log, DEFAULT_MIN_LEN, DEFAULT_BUDGET_MB,
                         DEFAULT_WINDOW, DEFAULT_PROMOTE_COUNT, DEFAULT_CASCADE_DEPTH, DEFAULT_CASCADE_MAX)
# imported up front so that a code update during a long run cannot leave the exit-time archive
# with a mix of old and new modules (bff_archive pulls in bff_query and bff_core)
from bff_archive import build_and_save, print_summary  # noqa: E402

LOG_COLUMNS = ['epoch', 'compressed_size', 'soup_bytes', 'higher_entropy', 'h0', 'bpb',
               'ops_per_pair', 'unique_species', 'top_share', 'top_key_len',
               'key_changes', 'new_species', 'promoted_species', 'selfrep_slots', 'parasite_slots',
               'selfrep_strict_slots', 'sample_selfrep_share', 'sample_strict_share', 'elapsed_s']


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class OutcomeDetector:
    """
    Decides when a statistics run has told its story. Fed the self-replication count
    after every test (every --selfrep-interval epochs):
      emergence   self-replicators hold >= 1% of the soup (recorded, not a stop)
      takeover    >= 50% of the soup at 8 consecutive tests after emergence, with no rising
                  parasite load: the non-replicating near-variants of a replicator may not grow by more
                  than 2% of the soup over the streak (a constant load, as mutation produces, is fine)
      extinction  no self-replicator at 4 consecutive tests after emergence (parasites, fading)
      unresolved  32768 epochs after emergence without either (coexistence)
    Returns the reason string when the run should stop, else None.
    """

    def __init__(self, num_programs, takeover_tests=8, extinct_tests=4, unresolved_epochs=32768):
        self.n = num_programs
        self.takeover_tests, self.extinct_tests, self.unresolved_epochs = takeover_tests, extinct_tests, unresolved_epochs
        self.emerged = None
        self.high = 0
        self.zero = 0
        self.parasite_base = 0

    def update(self, epoch, selfrep_slots, parasite_slots=0):
        if selfrep_slots < 0:
            return None
        if self.emerged is None:
            if selfrep_slots >= 0.01 * self.n:
                self.emerged = epoch
            return None
        if selfrep_slots < 0.5 * self.n:
            self.high = 0
        elif self.high and parasite_slots > self.parasite_base + 0.02 * self.n:
            self.high, self.parasite_base = 1, parasite_slots     # the load is rising: start the streak over
        else:
            if self.high == 0:
                self.parasite_base = parasite_slots
            self.high += 1
        self.zero = self.zero + 1 if selfrep_slots == 0 else 0
        if self.high >= self.takeover_tests:
            return f"takeover: self-replicators in half the soup at {self.high} consecutive tests (emerged at epoch {self.emerged})"
        if self.zero >= self.extinct_tests:
            return f"extinction: no self-replicator at {self.zero} consecutive tests after emergence at epoch {self.emerged}"
        if epoch - self.emerged >= self.unresolved_epochs:
            return f"unresolved: {epoch - self.emerged} epochs after emergence at epoch {self.emerged} without takeover or extinction"
        return None


def host_name():
    return socket.gethostname().split('.')[0].lower()


def host_info():
    try:
        import numba
        threads = numba.config.NUMBA_NUM_THREADS
    except Exception:  # noqa: BLE001
        threads = None
    return {'host': host_name(), 'machine': platform.machine(), 'system': platform.system(),
            'processor': platform.processor() or platform.machine(), 'cpus': os.cpu_count(), 'threads': threads,
            'python': platform.python_version()}


def probability(text):
    """Argument type for rates: a float, or a fraction such as 1/4096."""
    if '/' in text:
        num, den = text.split('/', 1)
        return float(num) / float(den)
    return float(text)


def protocol_name(num_programs, max_steps, mutation_prob, heads=False, init_dist=None):
    """Canonical label of the experimental setup, so runs can be grouped for statistics."""
    size = f"{num_programs // 1024}k" if num_programs % 1024 == 0 else str(num_programs)
    mut = f"-mut{int(round(1 / mutation_prob))}" if mutation_prob > 0 else ""      # -mut4096 = one byte in 4096 per epoch
    return f"{size}-{max_steps}{mut}" + ("-heads" if heads else "") + core.init_dist_label(init_dist)


def _letter_suffix(i):
    return (chr(ord('a') + i // 26 - 1) if i >= 26 else '') + chr(ord('a') + i % 26)


def default_seed_name(runs_root='runs', archive_dir='archive'):
    """
    <host>-<date>-<letter>: the letter after the last one used today. A name counts as used when
    its run directory exists, an archive of it exists, or it is listed in the ledger runs/.names,
    so that deleting old run directories does not hand their names to new runs.
    """
    stem = f"{host_name()}-{datetime.now().strftime('%Y%m%d')}"
    used = set()
    for root in (runs_root, archive_dir):
        if os.path.isdir(root):
            for entry in os.listdir(root):
                entry = entry[:-5] if entry.endswith('.json') else entry
                if entry.startswith(stem + '-'):
                    used.add(entry[len(stem) + 1:])
    ledger = os.path.join(runs_root, '.names')
    if os.path.exists(ledger):
        with open(ledger) as f:
            for line in f:
                line = line.strip()
                if line.startswith(stem + '-'):
                    used.add(line[len(stem) + 1:])
    suffixes = [_letter_suffix(i) for i in range(26 * 27)]
    last = max((suffixes.index(u) for u in used if u in suffixes), default=-1)
    if last + 1 >= len(suffixes):
        raise RuntimeError("too many runs today")
    name = f"{stem}-{suffixes[last + 1]}"
    os.makedirs(runs_root, exist_ok=True)
    with open(ledger, 'a') as f:
        f.write(name + '\n')
    return name


def _warmup():
    """Trigger Numba compilation on tiny inputs so timings exclude JIT."""
    dummy = np.zeros((4, TAPE_SIZE), dtype=np.uint8)
    core.run_epoch(dummy, np.arange(4), 16, 0, 0, np.empty(2, dtype=np.int64))
    core.compute_keys(dummy)
    core.selfrep_test(dummy[:1], 0, 16)


def run_soup(num_programs=1024, max_epochs=10000, seed=42, run_dir_path=None,
             checkpoint_interval=256, resume_path=None,
             mutation_prob=None, max_steps=DEFAULT_MAX_STEPS,
             metric_interval=1, metric_sample=0,
             species_interval=32, selfrep_interval=256, selfrep_top=512, selfrep_sample=2048,
             lineage_min_len=DEFAULT_MIN_LEN, lineage_budget_mb=DEFAULT_BUDGET_MB,
             lineage_window=None, promote_count=None, cascade_depth=None, cascade_max=None,
             stop_entropy=None, stop_share=None, stop_selfreps=None, stop_after=0, stop_outcome=False,
             cull_replicators=0, cull_interval=4,
             print_interval=100, seed_programs=None, archive=True, archive_dir='archive', protocol=None,
             heads=False, init_dist=None):
    """Run (or resume) the simulation. Returns the final soup."""

    # ---- resolve run directory and starting state --------------------------
    if resume_path:
        if os.path.isdir(resume_path):
            rd = RunDir(resume_path)
            cps = rd.checkpoints()
            if not cps:
                sys.exit(f"No checkpoints in {resume_path}")
            # the latest checkpoint may be a stub if the disk filled while it was written: fall back
            for e, ckpt in reversed(cps):
                try:
                    soup, ck = core.load_checkpoint(ckpt)
                    break
                except core.CheckpointError as err:
                    print(f"Skipping unreadable checkpoint {ckpt}: {err}")
            else:
                sys.exit(f"No readable checkpoint in {resume_path}")
        else:
            ckpt = resume_path
            rd = RunDir(run_dir_path or os.path.dirname(os.path.dirname(os.path.abspath(ckpt))))
            soup, ck = core.load_checkpoint(ckpt)
        if ck.get('format', 1) == 1:
            sys.exit("Cannot resume from a v1 checkpoint: it has no seed/pairing information.")
        if ck['epoch'] == 0:
            sys.exit("Checkpoint 0 is the initial soup; delete the run directory and start a fresh run instead.")
        num_programs = ck['num_programs']
        seed = ck['seed']
        old_mutation = ck.get('mutation_prob', 0.0)
        mutation_changed = mutation_prob is not None and mutation_prob != old_mutation
        mutation_prob = mutation_prob if mutation_prob is not None else old_mutation
        max_steps = ck.get('max_steps', DEFAULT_MAX_STEPS)
        heads = bool(ck.get('heads', False))
        start_epoch = ck['epoch'] + 1
        meta = rd.read_meta() if rd.exists() else {}
        init_dist = meta.get('init_dist', 'uniform')     # the initial soup is history; the flag is ignored on resume
        seed_label = meta.get('seed_label', str(seed))
        # recording settings must stay what they were for the run to remain consistent
        checkpoint_interval = meta.get('checkpoint_interval', checkpoint_interval)
        species_interval = meta.get('species_interval', species_interval)
        selfrep_interval = meta.get('selfrep_interval', selfrep_interval)
        selfrep_top = meta.get('selfrep_top', selfrep_top)
        selfrep_sample = meta.get('selfrep_sample', selfrep_sample)
        metric_interval = meta.get('metric_interval', metric_interval)
        metric_sample = meta.get('metric_sample', metric_sample)
        lineage_min_len = meta.get('lineage_min_len', lineage_min_len)
        lineage_budget_mb = meta.get('lineage_budget_mb', lineage_budget_mb)
        # the recording policy may be tightened or loosened on resume (explicit flags win)
        lineage_window = lineage_window if lineage_window is not None else meta.get('lineage_window', DEFAULT_WINDOW)
        promote_count = promote_count if promote_count is not None else meta.get('promote_count', DEFAULT_PROMOTE_COUNT)
        cascade_depth = cascade_depth if cascade_depth is not None else meta.get('cascade_depth', DEFAULT_CASCADE_DEPTH)
        cascade_max = cascade_max if cascade_max is not None else meta.get('cascade_max', DEFAULT_CASCADE_MAX)
        resume_note = {'from': ckpt, 'epoch': start_epoch, 'time': _now()}
        # the run is live again: its previous ending goes into the resume history
        for k in ('finished', 'stop_triggered', 'last_epoch'):
            if k in meta:
                resume_note['previous_' + k] = meta.pop(k)
        if mutation_changed:
            # an experiment on the old soup, not a continuation: the trajectory diverges from here
            resume_note['mutation_prob_changed'] = {'from': old_mutation, 'to': mutation_prob}
            meta['protocol'] = None                 # re-derived below with the new rate
            print(f"Mutation rate changed from {old_mutation:g} to {mutation_prob:g} at epoch {start_epoch}: "
                  f"the run diverges from its original trajectory from here on.")
        meta.setdefault('resumes', []).append(resume_note)
        schedule = meta.get('mutation_schedule') or [{'from_epoch': 0, 'prob': old_mutation}]
        if mutation_changed:
            schedule.append({'from_epoch': start_epoch, 'prob': mutation_prob})
        meta['mutation_schedule'] = schedule
        truncate_log(rd.log_path, ck['epoch'])
        if migrate_log(rd.log_path, LOG_COLUMNS):
            print("Log rewritten in the current column layout (older columns kept, new ones filled with -1).")
        print(f"Resuming {rd.path} from {ckpt} at epoch {start_epoch}")
    else:
        seed_label = str(seed)
        seed = core.seed_to_int(seed)
        rd = RunDir(run_dir_path or os.path.join('runs', seed_label))
        if rd.exists():
            sys.exit(f"Run directory {rd.path} already exists. Use --resume {rd.path} or pick another --run-dir.")
        rd.create()
        init_dist = init_dist or 'uniform'
        soup = core.random_soup(num_programs, seed, core.parse_init_dist(init_dist))
        start_epoch = 0
        mutation_prob = mutation_prob or 0.0
        meta = {'created': _now(), 'resumes': [], 'mutation_schedule': [{'from_epoch': 0, 'prob': mutation_prob}],
                'init_dist': init_dist}
        if seed_programs:
            # "<file.npy>[:count]": plant copies of given programs into random slots
            path, _, count = seed_programs.partition(':')
            progs = np.load(path).reshape(-1, TAPE_SIZE).astype(np.uint8)
            count = int(count) if count else progs.shape[0]
            slots = core.init_rng(seed + 1).choice(num_programs, size=count, replace=False)
            for i, slot in enumerate(slots.tolist()):
                soup[slot] = progs[i % progs.shape[0]]
            meta['seed_programs'] = {'file': path, 'count': count, 'slots': slots.tolist()}

    if num_programs % 2:
        sys.exit("--num must be even")
    lineage_window = DEFAULT_WINDOW if lineage_window is None else lineage_window
    promote_count = DEFAULT_PROMOTE_COUNT if promote_count is None else promote_count
    cascade_depth = DEFAULT_CASCADE_DEPTH if cascade_depth is None else cascade_depth
    cascade_max = DEFAULT_CASCADE_MAX if cascade_max is None else cascade_max

    meta.update({
        'num_programs': num_programs, 'tape_size': TAPE_SIZE, 'seed': seed, 'seed_label': seed_label,
        'mutation_prob': mutation_prob, 'max_steps': max_steps, 'heads': heads,
        'checkpoint_interval': checkpoint_interval, 'species_interval': species_interval,
        'selfrep_interval': selfrep_interval, 'selfrep_top': selfrep_top, 'selfrep_sample': selfrep_sample,
        'metric_interval': metric_interval, 'metric_sample': metric_sample,
        'lineage_min_len': lineage_min_len, 'lineage_budget_mb': lineage_budget_mb,
        'lineage_window': lineage_window, 'promote_count': promote_count, 'cascade_depth': cascade_depth,
        'cascade_max': cascade_max,
        'compressor': core.COMPRESSOR, 'max_epochs': max_epochs,
        'stop': {'entropy': stop_entropy, 'share': stop_share, 'selfreps': stop_selfreps,
                 'after': stop_after, 'outcome': stop_outcome},
        'cull': {'replicators': cull_replicators, 'interval': cull_interval} if cull_replicators else None,
        'log_columns': LOG_COLUMNS,
        'protocol': protocol or meta.get('protocol') or protocol_name(num_programs, max_steps, mutation_prob, heads, init_dist)
                    + ('-resumed' if meta.get('protocol') is None and resume_path else ''),
        'host': host_info(),
    })
    rd.write_meta(meta)
    ckpt_meta = {'seed': seed, 'mutation_prob': mutation_prob, 'max_steps': max_steps, 'heads': heads}
    mutation_int = int(round(mutation_prob * (1 << 30)))

    # ---- lineage + log ------------------------------------------------------
    lineage = LineageWriter(rd, min_len=lineage_min_len, window=lineage_window, budget_mb=lineage_budget_mb,
                            promote_count=promote_count, cascade_depth=cascade_depth, cascade_max=cascade_max,
                            resume_epoch=(start_epoch - 1) if resume_path else None)
    if lineage.window_reset:
        meta.setdefault('window_resets', []).append(start_epoch)
        rd.write_meta(meta)
    prev_hash, prev_len = core.compute_keys(soup)
    if not resume_path:
        lineage.record_initial(soup, prev_hash, prev_len)
        u0, f0, c0 = core.unique_counts(prev_hash)
        lineage.record_counts(0, u0, c0, soup, f0)
        core.save_checkpoint(soup, 0, rd.checkpoint_path(0), ckpt_meta)
        lineage.commit()

    new_log = not os.path.exists(rd.log_path) or os.path.getsize(rd.log_path) == 0
    log = open(rd.log_path, 'a')
    if new_log:
        log.write(','.join(LOG_COLUMNS) + '\n')

    # ---- main loop ----------------------------------------------------------
    print(f"BFF Primordial Soup: {num_programs} programs, seed {seed_label}{'' if seed_label == str(seed) else f' ({seed})'}, "
          f"mutation {mutation_prob:g}, max_steps {max_steps}{', heads from tape' if heads else ''}, run dir {rd.path}")
    print(f"{'Epoch':>8} {'Entropy':>8} {'bpb':>6} {'Ops/Pair':>9} {'Species':>8} {'Top%':>6} "
          f"{'SelfRep':>8} {'Parasit':>8} {'Rep%':>6} {'ep/s':>6}")
    print("-" * 86)

    _warmup()
    num_pairs = num_programs // 2
    ops = np.empty(num_pairs, dtype=np.int64)
    prev_soup = np.empty_like(soup)
    t0 = time.time()
    t_last = t0
    epoch_last = start_epoch
    stop_at = None
    cull_dist = core.parse_init_dist(init_dist) if cull_replicators else None
    culls_path = os.path.join(rd.path, 'culls.jsonl')       # one removal per line; meta.json keeps only the counts
    culls = []
    if cull_replicators and os.path.exists(culls_path):
        with open(culls_path) as f:
            culls = [json.loads(line) for line in f if line.strip()]
    n_removed = sum(1 for c in culls if c['kind'] == 'replicator')   # every removed replicator is one origin:
    # its lineage (offspring and carriers of its loop) goes with it, so a later appearance of the same
    # engine was made anew from the pool
    outcome = OutcomeDetector(num_programs) if stop_outcome else None
    selfrep_slots = -1
    selfrep_strict_slots = -1
    sample_selfrep_share = -1.0     # unbiased share of the soup that replicates, from a random slot sample
    sample_strict_share = -1.0
    tested_share = 0.0              # top share at the last self-replication test
    parasite_slots = -1
    if resume_path:   # carry the last known self-replicator counts across the resume
        last = lineage.db.execute("SELECT MAX(epoch) FROM selfrep").fetchone()[0]
        if last is not None:
            q = "SELECT COALESCE(SUM(count), 0) FROM selfrep WHERE score >= ? AND epoch = ?"
            selfrep_slots = int(lineage.db.execute(q, (SELFREP_THRESHOLD, last)).fetchone()[0])
            selfrep_strict_slots = int(lineage.db.execute(q, (SELFREP_STRICT, last)).fetchone()[0])
    epoch = start_epoch - 1
    outcome_reason = None
    metrics = {'higher_entropy': float('nan'), 'bpb': float('nan'), 'h0': float('nan'),
               'compressed': -1, 'nbytes': 0}
    pool = ThreadPoolExecutor(max_workers=2)   # compression overlaps later epochs (brotli releases the GIL)
    queue = deque()                              # (row template, future or None) in epoch order
    metrics_future = None                        # future of the most recent metrics job

    def finish_rows(wait=False):
        """Write queued log rows whose metrics are ready (all of them when wait=True)."""
        nonlocal metrics
        while queue:
            row, fut = queue[0]
            if fut is not None:
                if not (wait or fut.done()):
                    break
                metrics = fut.result()
            log.write(row.format(**metrics))
            queue.popleft()
        log.flush()

    try:
        for epoch in range(start_epoch, max_epochs):
            # -- execute one epoch -------------------------------------------
            perm = core.epoch_permutation(seed, epoch, num_programs)
            np.copyto(prev_soup, soup)
            core.run_epoch(soup, perm, max_steps, mutation_int, epoch, ops, heads)

            # -- keys, changes, births ---------------------------------------
            if metric_interval and epoch % metric_interval == 0:
                sample = (soup if metric_sample <= 0 else soup[:metric_sample]).copy()
                metrics_future = pool.submit(core.complexity_metrics, sample)
                if len(queue) >= 4:            # never let more than a few epochs run ahead of their metrics
                    finish_rows(wait=True)
            else:
                metrics_future = None

            cur_hash, cur_len = core.compute_keys(soup)

            # -- origin-rate experiment: remove every replicator as soon as it is seen ----------
            if cull_replicators and epoch % cull_interval == 0:
                uniq, first_idx, counts = core.unique_counts(cur_hash)
                long_enough = np.flatnonzero(cur_len[first_idx] >= SPIKE_MIN_LEN)
                cand = long_enough[np.argsort(-counts[long_enough], kind='stable')[:selfrep_top]]
                scores = core.selfrep_test(soup[first_idx[cand]], seed=epoch, max_steps=max_steps, heads_init=heads)
                hits = np.flatnonzero(scores >= SELFREP_THRESHOLD)
                if hits.size:
                    # A replicator is a lineage, not a key: a copier that copies only part of itself lives in
                    # thousands of keys with varying junk. Remove every species in the soup that carries one
                    # of the hit's copy loops or lies within a few edits of it (the lineage and the debris it
                    # re-forms from); the tested hit is logged as the removal, the rest as its lineage.
                    all_keys = None
                    doomed = {}
                    programs = {}
                    for k in hits:
                        i = cand[k]
                        key = core.program_key(soup[first_idx[i]])
                        if int(i) in doomed:
                            continue
                        doomed[int(i)] = (key, int(scores[k]), 'replicator')
                        programs[int(i)] = soup[first_idx[i]].tobytes().hex()     # the raw bytes, head values included
                        loops = core.copy_loops(key)
                        if all_keys is None:
                            all_keys = [core.program_key(soup[j]) for j in first_idx]
                        members = set(core.near_variants(soup[first_idx], key, core.variant_distance(key)).tolist())
                        if loops:
                            members.update(j for j, kj in enumerate(all_keys) if any(l in kj for l in loops))
                        for j in members:
                            if j not in doomed:
                                doomed[j] = (all_keys[j], -1, 'lineage')
                    for n_done, (i, (key, score, kind)) in enumerate(sorted(doomed.items())):
                        slots = np.flatnonzero(cur_hash == uniq[i])
                        rng = np.random.default_rng([int(seed), 3, int(epoch), n_done])
                        if cull_dist is None:
                            soup[slots] = rng.integers(0, 256, (slots.size, TAPE_SIZE), dtype=np.uint8)
                        else:
                            soup[slots] = rng.choice(256, size=(slots.size, TAPE_SIZE), p=cull_dist).astype(np.uint8)
                        event = {'epoch': epoch, 'key': key, 'count': int(slots.size), 'score': score, 'kind': kind}
                        if kind == 'replicator':
                            n_removed += 1
                            event['program'] = programs[i]
                        culls.append(event)
                        with open(culls_path, 'a') as f:
                            f.write(json.dumps(event) + '\n')
                        if kind == 'replicator':
                            n_lin = sum(1 for _, (_, _, kd) in doomed.items() if kd == 'lineage')
                            n_slots = sum(np.count_nonzero(cur_hash == uniq[j]) for j, (_, _, kd) in doomed.items() if kd == 'lineage')
                            print(f"*** cull at epoch {epoch}: origin {n_removed}, {key!r} ({slots.size} copies, score {score}); "
                                  f"its lineage: {n_lin} more species, {n_slots} slots, all replaced by random programs ***",
                                  flush=True)
                if hits.size:
                    cur_hash, cur_len = core.compute_keys(soup)
                    meta['cull_counts'] = {'origins': n_removed,
                                           'lineage_species': sum(1 for c in culls if c['kind'] == 'lineage')}
                    rd.write_meta(meta)

            changed = np.flatnonzero(cur_hash != prev_hash)
            partner = core.partners_from_perm(perm)
            uniq, first_idx, counts = core.unique_counts(cur_hash)
            n_new, n_prom = lineage.record_epoch(epoch, soup, prev_soup, prev_hash, prev_len, cur_hash, cur_len,
                                                 changed, partner, uniq, first_idx, counts)
            prev_hash, prev_len = cur_hash, cur_len

            # -- population statistics ---------------------------------------
            top_i = int(np.argmax(counts))
            top_share = counts[top_i] / num_programs
            top_len = int(cur_len[first_idx[top_i]])
            if species_interval and epoch % species_interval == 0:
                lineage.record_counts(epoch, uniq, counts, soup, first_idx)

            # -- self-replication test on the most common species ------------
            due = bool(selfrep_interval) and epoch % selfrep_interval == 0
            # a species that has at least doubled since the last test and already holds a percent of
            # the soup is tested right away: a burst can rise and collapse between two regular tests
            if not due and species_interval and epoch % species_interval == 0:
                long_enough = cur_len[first_idx] >= SPIKE_MIN_LEN     # the empty and one-byte keys never count
                spike_share = counts[long_enough].max() / num_programs if long_enough.any() else 0.0
                if spike_share >= SPIKE_TEST_SHARE and spike_share >= 2 * tested_share:
                    due = True
            if due:
                tested_share = top_share
                order = np.argsort(-counts, kind='stable')[:selfrep_top]
                reps = soup[first_idx[order]]
                scores = core.selfrep_test(reps, seed=epoch, max_steps=max_steps, heads_init=heads)
                lineage.record_selfrep(epoch, uniq[order], scores, counts[order])
                selfrep_slots = int(counts[order][scores >= SELFREP_THRESHOLD].sum())
                selfrep_strict_slots = int(counts[order][scores >= SELFREP_STRICT].sum())
                parasite_slots, _ = core.parasite_load([core.program_key(p) for p in reps], counts[order], scores)
                # the top-K species undercount a replicating population spread over many keys (mutation,
                # instruction-rich soups); a random slot sample gives an unbiased share of the soup
                replicating_slots = selfrep_slots
                if selfrep_sample:
                    sample_idx = np.random.default_rng([int(seed), 2, int(epoch)]).choice(
                        num_programs, min(selfrep_sample, num_programs), replace=False)
                    sample_scores = core.selfrep_test(soup[sample_idx], seed=epoch, max_steps=max_steps, heads_init=heads)
                    sample_selfrep_share = float((sample_scores >= SELFREP_THRESHOLD).mean())
                    sample_strict_share = float((sample_scores >= SELFREP_STRICT).mean())
                    replicating_slots = max(selfrep_slots, int(round(sample_selfrep_share * num_programs)))
                outcome_reason = outcome.update(epoch, replicating_slots, parasite_slots) if outcome is not None else None
                if outcome is not None and outcome.emerged == epoch:
                    print(f"*** Self-replicators emerged at epoch {epoch} ({selfrep_slots} slots in the top {selfrep_top} "
                          f"species, {100 * sample_selfrep_share:.1f}% of a {selfrep_sample}-slot sample) ***", flush=True)
                    meta['emergence_epoch'] = epoch
                    rd.write_meta(meta)

            # -- checkpoint --------------------------------------------------
            if checkpoint_interval and epoch % checkpoint_interval == 0 and epoch != 0:
                core.save_checkpoint(soup, epoch, rd.checkpoint_path(epoch), ckpt_meta)
                lineage.commit()

            # -- log ---------------------------------------------------------
            elapsed = time.time() - t0
            queue.append((f"{epoch},{{compressed}},{{nbytes}},{{higher_entropy:.6f}},{{h0:.6f}},{{bpb:.6f}},"
                          f"{ops.mean():.2f},{uniq.size},{top_share:.6f},{top_len},"
                          f"{changed.size},{n_new},{n_prom},{selfrep_slots},{parasite_slots},{selfrep_strict_slots},"
                          f"{sample_selfrep_share:.5f},{sample_strict_share:.5f},{elapsed:.1f}\n", metrics_future))
            finish_rows()

            # -- progress ----------------------------------------------------
            if epoch % print_interval == 0:
                finish_rows(wait=True)
                now = time.time()
                rate = (epoch - epoch_last) / (now - t_last) if now > t_last and epoch > epoch_last else 0.0
                t_last, epoch_last = now, epoch
                print(f"{epoch:8d} {metrics['higher_entropy']:8.3f} {metrics['bpb']:6.2f} "
                      f"{ops.mean():9.1f} {uniq.size:8d} {100 * top_share:6.2f} "
                      f"{selfrep_slots:8d} {parasite_slots:8d} "
                      f"{('%5.1f' % (100 * sample_selfrep_share)) if sample_selfrep_share >= 0 else '-':>6} {rate:6.1f}", flush=True)

            # -- stop conditions ---------------------------------------------
            if stop_at is None and (stop_entropy is not None or stop_share is not None or stop_selfreps is not None
                                    or outcome is not None):
                reason = outcome_reason if outcome is not None else None
                outcome_reason = None      # entropy may lag a few epochs behind (metrics run in the background)
                if reason:
                    pass
                elif stop_entropy is not None and metrics['higher_entropy'] > stop_entropy:
                    reason = f"higher-order entropy {metrics['higher_entropy']:.2f} > {stop_entropy}"
                elif stop_share is not None and 100 * top_share > stop_share:
                    reason = f"top species share {100 * top_share:.1f}% > {stop_share}%"
                elif stop_selfreps is not None and selfrep_slots >= stop_selfreps:
                    reason = f"{selfrep_slots} self-replicating slots >= {stop_selfreps}"
                if reason:
                    stop_at = epoch + stop_after
                    print(f"*** Stop condition met at epoch {epoch}: {reason}; "
                          f"stopping at epoch {stop_at} ***", flush=True)
                    meta['stop_triggered'] = {'epoch': epoch, 'reason': reason, 'stop_at': stop_at}
                    rd.write_meta(meta)
            if cull_replicators and stop_at is None and n_removed >= cull_replicators:
                reason = f"culled {cull_replicators} replicator origins (origin-rate experiment)"
                stop_at = epoch
                print(f"*** {reason}; stopping ***", flush=True)
                meta['stop_triggered'] = {'epoch': epoch, 'reason': reason, 'stop_at': stop_at}
                rd.write_meta(meta)
            if stop_at is not None and epoch >= stop_at:
                break
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)

    # ---- final checkpoint and cleanup -------------------------------------
    finish_rows(wait=True)
    pool.shutdown()
    if epoch >= start_epoch:
        core.save_checkpoint(soup, epoch, rd.checkpoint_path(epoch), ckpt_meta)
    changes_exhausted = lineage.changes_exhausted_epoch
    meta['lineage_stats'] = lineage.stats
    lineage.close(epoch if epoch >= start_epoch else None)
    log.close()
    if changes_exhausted is not None:
        meta['changes_budget_exhausted_epoch'] = changes_exhausted
    meta['last_epoch'] = int(epoch)
    meta['finished'] = _now()
    rd.write_meta(meta)

    elapsed = time.time() - t0
    done = epoch - start_epoch + 1
    print(f"\nDone: {done} epochs in {elapsed:.1f}s ({done / max(elapsed, 1e-9):.1f} epochs/sec). "
          f"Run dir: {rd.path}")

    if archive and epoch >= 0:
        try:
            print("Building the run archive (winners, families, emergence story)...", flush=True)
            out, size, arc = build_and_save(rd.path, archive_dir=archive_dir, verbose=False)
            print_summary(arc)
            print(f"Archive written to {out} ({size / 1024:.0f} KB)")
        except Exception as e:  # noqa: BLE001 - never lose a run over the summary
            print(f"Archive failed ({type(e).__name__}: {e}); build it later with: python bff_archive.py {rd.path}")
    return soup


def main(argv=None):
    p = argparse.ArgumentParser(description="BFF Primordial Soup (Numba)",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    g = p.add_argument_group("simulation")
    g.add_argument("--num", type=int, default=None, help="number of programs, even (default 1024; 131072 with --stats)")
    g.add_argument("--epochs", type=int, default=None, help="run until this epoch number (default 10000; 100000 with --stats)")
    g.add_argument("--seed", type=str, default=None,
                   help="random seed: a number, or any name (hashed to an integer; also the run's name). "
                        "Default: <host>-<date>-<letter>")
    g.add_argument("--stats", action="store_true",
                   help="statistics preset: 131072 programs, 8192 steps, sampled metrics, --stop-outcome with "
                        "--stop-after 2048, cap 100000 epochs, checkpoints every 1024 epochs, 256 MB change log "
                        "(explicit flags win)")
    g.add_argument("--heads", action="store_true",
                   help="the paper's 'bff' variant: the first two tape bytes set the head positions, execution starts at byte 2")
    g.add_argument("--init-dist", default=None, metavar="PRESET|SPEC",
                   help="byte distribution of the initial soup: " + ", ".join(core.INIT_PRESETS) +
                        ", or key:weight pairs such as '[:25 ]:12 ,:18 0:5 64:3 rest:37' (keys: instruction "
                        "characters, byte values 0-255, 'rest' for every other value; default uniform)")
    g.add_argument("--mutation-prob", type=probability, default=None,
                   help="per-byte mutation probability per epoch (default 0; paper 1/4096 = 0.000244). "
                        "May be given on resume to change the rate from that epoch on (an experiment on the old soup)")
    g.add_argument("--max-steps", type=int, default=None,
                   help=f"step budget per tape execution (default {DEFAULT_MAX_STEPS}; 8192 with --stats, as in the paper)")
    g.add_argument("--seed-programs", type=str, default=None, metavar="FILE.npy[:COUNT]",
                   help="plant COUNT copies of the programs in FILE (n x 64 uint8) into random slots")
    g = p.add_argument_group("output")
    g.add_argument("--run-dir", type=str, default=None, help="run directory (default runs/<seed>)")
    g.add_argument("--resume", type=str, default=None,
                   help="run directory (latest checkpoint) or checkpoint file to resume from; "
                        "the run's recorded settings are kept, only --epochs, stop conditions, "
                        "--print-interval and the recording policy flags apply")
    g.add_argument("--checkpoint-interval", type=int, default=None, help="epochs between checkpoints (default 256; 1024 with --stats; 0=off)")
    g.add_argument("--species-interval", type=int, default=32,
                   help="epochs between species-count snapshots (0=off)")
    g.add_argument("--selfrep-interval", type=int, default=256,
                   help="epochs between self-replication tests (0=off)")
    g.add_argument("--selfrep-top", type=int, default=512,
                   help="number of most common species to test for self-replication")
    g.add_argument("--selfrep-sample", type=int, default=2048,
                   help="random slots tested at every self-replication test for an unbiased replicator share "
                        "of the soup (logged as sample_selfrep_share; 0 disables)")
    g.add_argument("--lineage-min-len", type=int, default=DEFAULT_MIN_LEN,
                   help="track births/changes only for keys with at least this many instructions")
    g.add_argument("--lineage-budget-mb", type=float, default=None,
                   help=f"stop appending change records once changes.bin reaches this size (default {DEFAULT_BUDGET_MB}; 256 with --stats)")
    g.add_argument("--lineage-window", type=int, default=None,
                   help=f"epochs a birth is remembered while waiting to be promoted (default {DEFAULT_WINDOW})")
    g.add_argument("--promote-count", type=int, default=None,
                   help=f"a species is recorded once it occupies this many slots at once (default {DEFAULT_PROMOTE_COUNT}; may be changed on resume)")
    g.add_argument("--cascade-max", type=int, default=None,
                   help=f"ancestors recorded per promotion, nearest first (default {DEFAULT_CASCADE_MAX}; may be changed on resume)")
    g.add_argument("--cascade-depth", type=int, default=None,
                   help=f"generations of pending ancestors recorded along with a promoted species (default {DEFAULT_CASCADE_DEPTH}; may be changed on resume)")
    g.add_argument("--metric-interval", type=int, default=1, help="epochs between compression metrics")
    g.add_argument("--metric-sample", type=int, default=None,
                   help="programs to compress for the metrics (default 0 = whole soup; 32768 with --stats)")
    g.add_argument("--print-interval", type=int, default=100)
    g.add_argument("--no-archive", action="store_true", help="do not build the run archive on exit")
    g.add_argument("--archive-dir", type=str, default=os.environ.get("BFF_ARCHIVE_DIR", "archive"),
                   help="where archives are written (default: archive/, or $BFF_ARCHIVE_DIR)")
    g.add_argument("--protocol", type=str, default=None,
                   help="label of the experimental setup for statistics (default: derived, e.g. 128k-8192-mut)")
    g = p.add_argument_group("stop conditions (optional, first one met wins)")
    g.add_argument("--stop-entropy", type=float, default=None, help="stop when higher-order entropy exceeds this")
    g.add_argument("--stop-share", type=float, default=None,
                   help="stop when one species exceeds this percentage of the soup")
    g.add_argument("--stop-selfreps", type=int, default=None,
                   help="stop when at least this many slots hold a self-replicator")
    g.add_argument("--stop-outcome", action="store_const", const=True, default=None,
                   help="after self-replicators emerge (1%% of the soup), stop once they hold half the soup for "
                        "2048 epochs (takeover), are gone for 1024 epochs (extinction), or 32768 epochs pass "
                        "(unresolved); on with --stats")
    g.add_argument("--cull-replicators", type=int, default=0, metavar="N",
                   help="origin-rate experiment: every --cull-interval epochs test the most common long species and "
                        "replace every copy of any self-replicator by fresh random programs (from the run's initial "
                        "distribution) together with its lineage (offspring and carriers of its copy loop), so that "
                        "each later appearance is made anew from the pool; every removal is one origin; stop after N")
    g.add_argument("--cull-interval", type=int, default=4, help="epochs between removal tests (default: 4)")
    g.add_argument("--stop-after", type=int, default=None,
                   help="keep running this many epochs after a stop condition fires (2048 with --stats)")
    args = p.parse_args(argv)

    # defaults, with the --stats preset filling in what was not given explicitly
    # stop on emergence (replicators in 1% of the soup) plus enough epochs to see whether a takeover,
    # a parasite or a collapse follows; a takeover criterion alone can wait forever
    # statistics runs keep their disk footprint small: checkpoints every 1024 epochs (replays take
    # a minute instead of seconds) and a 256 MB change log instead of 2 GB
    preset = ({'num': 131072, 'epochs': 100000, 'max_steps': 8192, 'metric_sample': 32768,
               'stop_after': 2048, 'stop_outcome': True, 'checkpoint_interval': 1024,
               'lineage_budget_mb': 256.0} if args.stats else {})
    base = {'num': 1024, 'epochs': 10000, 'max_steps': DEFAULT_MAX_STEPS, 'metric_sample': 0,
            'stop_after': 0, 'stop_outcome': False, 'checkpoint_interval': 256, 'lineage_budget_mb': DEFAULT_BUDGET_MB}
    for k, v in base.items():
        if getattr(args, k) is None:
            setattr(args, k, preset.get(k, v))
    if args.seed is None and not args.resume:
        args.seed = default_seed_name(os.path.dirname(args.run_dir) if args.run_dir else 'runs', args.archive_dir)

    run_soup(
        num_programs=args.num, max_epochs=args.epochs, seed=args.seed,
        run_dir_path=args.run_dir, checkpoint_interval=args.checkpoint_interval,
        resume_path=args.resume, mutation_prob=args.mutation_prob, max_steps=args.max_steps,
        metric_interval=args.metric_interval, metric_sample=args.metric_sample,
        species_interval=args.species_interval, selfrep_interval=args.selfrep_interval,
        selfrep_top=args.selfrep_top, selfrep_sample=args.selfrep_sample, lineage_min_len=args.lineage_min_len,
        lineage_budget_mb=args.lineage_budget_mb, lineage_window=args.lineage_window,
        promote_count=args.promote_count, cascade_depth=args.cascade_depth, cascade_max=args.cascade_max,
        stop_entropy=args.stop_entropy, stop_share=args.stop_share,
        stop_selfreps=args.stop_selfreps, stop_after=args.stop_after, stop_outcome=bool(args.stop_outcome),
        cull_replicators=args.cull_replicators, cull_interval=args.cull_interval,
        print_interval=args.print_interval, seed_programs=args.seed_programs, archive=not args.no_archive,
        archive_dir=args.archive_dir, protocol=args.protocol, heads=args.heads, init_dist=args.init_dist,
    )


if __name__ == "__main__":
    main()
