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
from bff_core import TAPE_SIZE, DEFAULT_MAX_STEPS, SELFREP_THRESHOLD
from bff_lineage import (RunDir, LineageWriter, truncate_log, DEFAULT_MIN_LEN, DEFAULT_BUDGET_MB,
                         DEFAULT_WINDOW, DEFAULT_PROMOTE_COUNT, DEFAULT_CASCADE_DEPTH, DEFAULT_CASCADE_MAX)

LOG_COLUMNS = ['epoch', 'compressed_size', 'soup_bytes', 'higher_entropy', 'h0', 'bpb',
               'ops_per_pair', 'unique_species', 'top_share', 'top_key_len',
               'key_changes', 'new_species', 'promoted_species', 'selfrep_slots', 'elapsed_s']


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


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


def protocol_name(num_programs, max_steps, mutation_prob):
    """Canonical label of the experimental setup, so runs can be grouped for statistics."""
    size = f"{num_programs // 1024}k" if num_programs % 1024 == 0 else str(num_programs)
    return f"{size}-{max_steps}" + ("-mut" if mutation_prob > 0 else "")


def _warmup():
    """Trigger Numba compilation on tiny inputs so timings exclude JIT."""
    dummy = np.zeros((4, TAPE_SIZE), dtype=np.uint8)
    core.run_epoch(dummy, np.arange(4), 16, 0, 0, np.empty(2, dtype=np.int64))
    core.compute_keys(dummy)
    core.selfrep_test(dummy[:1], 0, 16)


def run_soup(num_programs=1024, max_epochs=10000, seed=42, run_dir_path=None,
             checkpoint_interval=256, resume_path=None,
             mutation_prob=0.0, max_steps=DEFAULT_MAX_STEPS,
             metric_interval=1, metric_sample=0,
             species_interval=32, selfrep_interval=256, selfrep_top=512,
             lineage_min_len=DEFAULT_MIN_LEN, lineage_budget_mb=DEFAULT_BUDGET_MB,
             lineage_window=None, promote_count=None, cascade_depth=None, cascade_max=None,
             stop_entropy=None, stop_share=None, stop_selfreps=None, stop_after=0,
             print_interval=100, seed_programs=None, archive=True, archive_dir='archive', protocol=None):
    """Run (or resume) the simulation. Returns the final soup."""

    # ---- resolve run directory and starting state --------------------------
    if resume_path:
        if os.path.isdir(resume_path):
            rd = RunDir(resume_path)
            cps = rd.checkpoints()
            if not cps:
                sys.exit(f"No checkpoints in {resume_path}")
            ckpt = cps[-1][1]
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
        mutation_prob = ck.get('mutation_prob', 0.0)
        max_steps = ck.get('max_steps', DEFAULT_MAX_STEPS)
        start_epoch = ck['epoch'] + 1
        meta = rd.read_meta() if rd.exists() else {}
        # recording settings must stay what they were for the run to remain consistent
        checkpoint_interval = meta.get('checkpoint_interval', checkpoint_interval)
        species_interval = meta.get('species_interval', species_interval)
        selfrep_interval = meta.get('selfrep_interval', selfrep_interval)
        selfrep_top = meta.get('selfrep_top', selfrep_top)
        metric_interval = meta.get('metric_interval', metric_interval)
        metric_sample = meta.get('metric_sample', metric_sample)
        lineage_min_len = meta.get('lineage_min_len', lineage_min_len)
        lineage_budget_mb = meta.get('lineage_budget_mb', lineage_budget_mb)
        # the recording policy may be tightened or loosened on resume (explicit flags win)
        lineage_window = lineage_window if lineage_window is not None else meta.get('lineage_window', DEFAULT_WINDOW)
        promote_count = promote_count if promote_count is not None else meta.get('promote_count', DEFAULT_PROMOTE_COUNT)
        cascade_depth = cascade_depth if cascade_depth is not None else meta.get('cascade_depth', DEFAULT_CASCADE_DEPTH)
        cascade_max = cascade_max if cascade_max is not None else meta.get('cascade_max', DEFAULT_CASCADE_MAX)
        meta.setdefault('resumes', []).append({'from': ckpt, 'epoch': start_epoch, 'time': _now()})
        truncate_log(rd.log_path, ck['epoch'])
        print(f"Resuming {rd.path} from {ckpt} at epoch {start_epoch}")
    else:
        rd = RunDir(run_dir_path or os.path.join('runs', str(seed)))
        if rd.exists():
            sys.exit(f"Run directory {rd.path} already exists. Use --resume {rd.path} or pick another --run-dir.")
        rd.create()
        soup = core.random_soup(num_programs, seed)
        start_epoch = 0
        meta = {'created': _now(), 'resumes': []}
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
        'num_programs': num_programs, 'tape_size': TAPE_SIZE, 'seed': seed,
        'mutation_prob': mutation_prob, 'max_steps': max_steps,
        'checkpoint_interval': checkpoint_interval, 'species_interval': species_interval,
        'selfrep_interval': selfrep_interval, 'selfrep_top': selfrep_top,
        'metric_interval': metric_interval, 'metric_sample': metric_sample,
        'lineage_min_len': lineage_min_len, 'lineage_budget_mb': lineage_budget_mb,
        'lineage_window': lineage_window, 'promote_count': promote_count, 'cascade_depth': cascade_depth,
        'cascade_max': cascade_max,
        'compressor': core.COMPRESSOR, 'max_epochs': max_epochs,
        'stop': {'entropy': stop_entropy, 'share': stop_share, 'selfreps': stop_selfreps,
                 'after': stop_after},
        'log_columns': LOG_COLUMNS,
        'protocol': protocol or meta.get('protocol') or protocol_name(num_programs, max_steps, mutation_prob),
        'host': host_info(),
    })
    rd.write_meta(meta)
    ckpt_meta = {'seed': seed, 'mutation_prob': mutation_prob, 'max_steps': max_steps}
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
    print(f"BFF Primordial Soup: {num_programs} programs, seed {seed}, "
          f"mutation {mutation_prob:g}, max_steps {max_steps}, run dir {rd.path}")
    print(f"{'Epoch':>8} {'Entropy':>8} {'bpb':>6} {'Ops/Pair':>9} {'Species':>8} {'Top%':>6} "
          f"{'SelfRep':>8} {'ep/s':>6}")
    print("-" * 70)

    _warmup()
    num_pairs = num_programs // 2
    ops = np.empty(num_pairs, dtype=np.int64)
    prev_soup = np.empty_like(soup)
    t0 = time.time()
    t_last = t0
    epoch_last = start_epoch
    stop_at = None
    selfrep_slots = -1
    if resume_path:   # carry the last known self-replicator count across the resume
        last = lineage.db.execute("SELECT MAX(epoch) FROM selfrep").fetchone()[0]
        if last is not None:
            row = lineage.db.execute("SELECT COALESCE(SUM(count), 0) FROM selfrep WHERE score >= ? AND epoch = ?",
                                     (SELFREP_THRESHOLD, last)).fetchone()
            selfrep_slots = int(row[0])
    epoch = start_epoch - 1
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
            core.run_epoch(soup, perm, max_steps, mutation_int, epoch, ops)

            # -- keys, changes, births ---------------------------------------
            if metric_interval and epoch % metric_interval == 0:
                sample = (soup if metric_sample <= 0 else soup[:metric_sample]).copy()
                metrics_future = pool.submit(core.complexity_metrics, sample)
                if len(queue) >= 4:            # never let more than a few epochs run ahead of their metrics
                    finish_rows(wait=True)
            else:
                metrics_future = None

            cur_hash, cur_len = core.compute_keys(soup)
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
            if selfrep_interval and epoch % selfrep_interval == 0:
                order = np.argsort(-counts, kind='stable')[:selfrep_top]
                reps = soup[first_idx[order]]
                scores = core.selfrep_test(reps, seed=epoch, max_steps=max_steps)
                lineage.record_selfrep(epoch, uniq[order], scores, counts[order])
                selfrep_slots = int(counts[order][scores >= SELFREP_THRESHOLD].sum())

            # -- checkpoint --------------------------------------------------
            if checkpoint_interval and epoch % checkpoint_interval == 0 and epoch != 0:
                core.save_checkpoint(soup, epoch, rd.checkpoint_path(epoch), ckpt_meta)
                lineage.commit()

            # -- log ---------------------------------------------------------
            elapsed = time.time() - t0
            queue.append((f"{epoch},{{compressed}},{{nbytes}},{{higher_entropy:.6f}},{{h0:.6f}},{{bpb:.6f}},"
                          f"{ops.mean():.2f},{uniq.size},{top_share:.6f},{top_len},"
                          f"{changed.size},{n_new},{n_prom},{selfrep_slots},{elapsed:.1f}\n", metrics_future))
            finish_rows()

            # -- progress ----------------------------------------------------
            if epoch % print_interval == 0:
                finish_rows(wait=True)
                now = time.time()
                rate = (epoch - epoch_last) / (now - t_last) if now > t_last and epoch > epoch_last else 0.0
                t_last, epoch_last = now, epoch
                print(f"{epoch:8d} {metrics['higher_entropy']:8.3f} {metrics['bpb']:6.2f} "
                      f"{ops.mean():9.1f} {uniq.size:8d} {100 * top_share:6.2f} "
                      f"{selfrep_slots:8d} {rate:6.1f}", flush=True)

            # -- stop conditions ---------------------------------------------
            if stop_at is None and (stop_entropy is not None or stop_share is not None or stop_selfreps is not None):
                reason = None              # entropy may lag a few epochs behind (metrics run in the background)
                if stop_entropy is not None and metrics['higher_entropy'] > stop_entropy:
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
            from bff_archive import build_and_save, print_summary
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
    g.add_argument("--num", type=int, default=1024, help="number of programs (even)")
    g.add_argument("--epochs", type=int, default=10000, help="run until this epoch number")
    g.add_argument("--seed", type=int, default=42, help="random seed")
    g.add_argument("--mutation-prob", type=float, default=0.0,
                   help="per-byte mutation probability per epoch (paper default 1/4096 = 0.000244)")
    g.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                   help="step budget per tape execution (paper/cubff: 8192)")
    g.add_argument("--seed-programs", type=str, default=None, metavar="FILE.npy[:COUNT]",
                   help="plant COUNT copies of the programs in FILE (n x 64 uint8) into random slots")
    g = p.add_argument_group("output")
    g.add_argument("--run-dir", type=str, default=None, help="run directory (default runs/<seed>)")
    g.add_argument("--resume", type=str, default=None,
                   help="run directory (latest checkpoint) or checkpoint file to resume from; "
                        "the run's recorded settings are kept, only --epochs, stop conditions, "
                        "--print-interval and the recording policy flags apply")
    g.add_argument("--checkpoint-interval", type=int, default=256, help="epochs between checkpoints (0=off)")
    g.add_argument("--species-interval", type=int, default=32,
                   help="epochs between species-count snapshots (0=off)")
    g.add_argument("--selfrep-interval", type=int, default=256,
                   help="epochs between self-replication tests (0=off)")
    g.add_argument("--selfrep-top", type=int, default=512,
                   help="number of most common species to test for self-replication")
    g.add_argument("--lineage-min-len", type=int, default=DEFAULT_MIN_LEN,
                   help="track births/changes only for keys with at least this many instructions")
    g.add_argument("--lineage-budget-mb", type=float, default=DEFAULT_BUDGET_MB,
                   help="stop appending change records once changes.bin reaches this size")
    g.add_argument("--lineage-window", type=int, default=None,
                   help=f"epochs a birth is remembered while waiting to be promoted (default {DEFAULT_WINDOW})")
    g.add_argument("--promote-count", type=int, default=None,
                   help=f"a species is recorded once it occupies this many slots at once (default {DEFAULT_PROMOTE_COUNT}; may be changed on resume)")
    g.add_argument("--cascade-max", type=int, default=None,
                   help=f"ancestors recorded per promotion, nearest first (default {DEFAULT_CASCADE_MAX}; may be changed on resume)")
    g.add_argument("--cascade-depth", type=int, default=None,
                   help=f"generations of pending ancestors recorded along with a promoted species (default {DEFAULT_CASCADE_DEPTH}; may be changed on resume)")
    g.add_argument("--metric-interval", type=int, default=1, help="epochs between compression metrics")
    g.add_argument("--metric-sample", type=int, default=0,
                   help="programs to compress for the metrics (0 = whole soup)")
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
    g.add_argument("--stop-after", type=int, default=0,
                   help="keep running this many epochs after a stop condition fires")
    args = p.parse_args(argv)

    run_soup(
        num_programs=args.num, max_epochs=args.epochs, seed=args.seed,
        run_dir_path=args.run_dir, checkpoint_interval=args.checkpoint_interval,
        resume_path=args.resume, mutation_prob=args.mutation_prob, max_steps=args.max_steps,
        metric_interval=args.metric_interval, metric_sample=args.metric_sample,
        species_interval=args.species_interval, selfrep_interval=args.selfrep_interval,
        selfrep_top=args.selfrep_top, lineage_min_len=args.lineage_min_len,
        lineage_budget_mb=args.lineage_budget_mb, lineage_window=args.lineage_window,
        promote_count=args.promote_count, cascade_depth=args.cascade_depth, cascade_max=args.cascade_max,
        stop_entropy=args.stop_entropy, stop_share=args.stop_share,
        stop_selfreps=args.stop_selfreps, stop_after=args.stop_after,
        print_interval=args.print_interval, seed_programs=args.seed_programs, archive=not args.no_archive,
        archive_dir=args.archive_dir, protocol=args.protocol,
    )


if __name__ == "__main__":
    main()
