#!/usr/bin/env python3
"""
BFF checkpoint analysis: the most common programs in a soup, with their
self-replication scores.

Usage:
    python bff_analysis.py runs/42                          # latest checkpoint of a run
    python bff_analysis.py runs/42/checkpoints/0000010240.dat --top 20
    python bff_analysis.py checkpoint.dat --no-selfrep       # skip the replication test
"""

import argparse
import os

import numpy as np

import bff_core as core
from bff_core import COMMANDS, TAPE_SIZE, SELFREP_THRESHOLD  # noqa: F401  (re-exported for callers)
from bff_lineage import RunDir


def extract_programs(soup):
    """Map instruction-only key -> count for a (n, 64) soup array."""
    hashes, _ = core.compute_keys(soup)
    uniq, first, counts = np.unique(hashes, return_index=True, return_counts=True)
    return {core.program_key(soup[i]): int(c) for i, c in zip(first.tolist(), counts.tolist())}


def top_programs(soup, top_n=10, selfrep=True, max_steps=core.DEFAULT_MAX_STEPS, heads=False):
    """
    Returns a list of dicts (key, count, share, length, selfrep_score) for the
    top_n most common keys. selfrep_score is None when the test is skipped.
    """
    hashes, lengths = core.compute_keys(soup)
    uniq, first, counts = np.unique(hashes, return_index=True, return_counts=True)
    order = np.argsort(-counts, kind='stable')[:top_n]
    reps = soup[first[order]]
    scores = core.selfrep_test(reps, seed=0, max_steps=max_steps, heads_init=heads) if selfrep else None
    n = soup.shape[0]
    out = []
    for k, i in enumerate(order.tolist()):
        out.append({
            'key': core.program_key(reps[k]),
            'count': int(counts[i]),
            'share': counts[i] / n,
            'length': int(lengths[first[i]]),
            'selfrep_score': int(scores[k]) if scores is not None else None,
        })
    return out


def print_top_programs(soup, top_n=10, selfrep=True, max_steps=core.DEFAULT_MAX_STEPS, heads=False):
    rows = top_programs(soup, top_n, selfrep, max_steps, heads)
    print(f"\nTop {top_n} programs" + (" (SelfRep = stable bytes over 13 trials; >=20 replicates, >=48 strict)" if selfrep else "") + ":")
    print(f"{'Count':>7} {'Share':>7} {'Len':>4} {'SelfRep':>8}  Key")
    for r in rows:
        display = r['key'] if r['key'] else "(empty)"
        sr = f"{r['selfrep_score']:8d}" if r['selfrep_score'] is not None else f"{'-':>8}"
        print(f"{r['count']:7d} {100 * r['share']:6.1f}% {r['length']:4d} {sr}  {display}")


def resolve_checkpoint(path):
    """A run directory resolves to its latest checkpoint."""
    if os.path.isdir(path):
        cps = RunDir(path).checkpoints()
        if not cps:
            raise SystemExit(f"No checkpoints in {path}")
        return cps[-1][1]
    return path


def analyze_checkpoint(path, top_n=10, selfrep=True):
    ckpt = resolve_checkpoint(path)
    soup, meta = core.load_checkpoint(ckpt)
    print(f"Checkpoint: {ckpt}")
    print(f"Epoch: {meta['epoch']}   Programs: {meta['num_programs']}   Tape size: {meta['tape_size']}"
          f"   Format: v{meta.get('format', 2)}")
    metrics = core.complexity_metrics(soup)
    print(f"Higher-order entropy: {metrics['higher_entropy']:.3f}   bits/byte: {metrics['bpb']:.3f}"
          f"   ({core.COMPRESSOR})")
    print_top_programs(soup, top_n, selfrep, meta.get('max_steps', core.DEFAULT_MAX_STEPS), bool(meta.get('heads', False)))


# Backwards-compatible aliases
save_checkpoint = core.save_checkpoint
load_checkpoint = core.load_checkpoint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze BFF checkpoint files")
    parser.add_argument("checkpoint", help="checkpoint file or run directory (latest checkpoint)")
    parser.add_argument("--top", type=int, default=10, help="number of top programs to show")
    parser.add_argument("--no-selfrep", action="store_true", help="skip the self-replication test")
    args = parser.parse_args()
    analyze_checkpoint(args.checkpoint, args.top, selfrep=not args.no_selfrep)
