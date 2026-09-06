#!/usr/bin/env python3
"""
Run archive: the durable, self-contained summary of a run for cross-run analytics.

A run directory is a large working set (checkpoints, change records, species
database). The archive is one JSON file of a few hundred KB that answers "what
emerged, when, and from what" without the raw files:

  run facts and event epochs        transition, first self-replicator, share crossings
  winners                           top-N species at the end and at milestones after
                                    the transition (raw bytes, share, selfrep score, birth)
  families                          winners clustered into variants of one core, with a
                                    functional profile of the representative
  emergence story                    ancestry tree of each family's representative; for the
                                    leading families the exact tapes of the birth events
  metrics log                       decimated, full resolution around the transition

Usage:
    python bff_archive.py runs/42                 # writes archive/42.json
    python bff_archive.py runs/42 --out x.json --top 20 --tapes 5
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

import bff_core as core
from bff_lineage import RunDir
from bff_query import Run, edit_distance, _json_default

MILESTONES = (256, 1024, 4096)     # epochs after the transition at which winners are recorded
FAMILY_DISTANCE = 3                # single-linkage edit distance for grouping variants
TRANSITION_ENTROPY = 3.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def first_epoch_where(epochs, values, pred):
    idx = np.flatnonzero(pred(values))
    return int(epochs[idx[0]]) if idx.size else None


def longest_common_substring(strings):
    if not strings:
        return ''
    s0 = min(strings, key=len)
    for length in range(len(s0), 0, -1):
        for start in range(0, len(s0) - length + 1):
            sub = s0[start:start + length]
            if all(sub in s for s in strings):
                return sub
    return ''


def family_distance(a, b):
    """Edit distance up to reversal: replicators often produce mirror images of themselves."""
    return min(edit_distance(a, b), edit_distance(a, b[::-1]))


def cluster_families(keys, max_dist=FAMILY_DISTANCE):
    """Single-linkage clustering by edit distance (up to reversal). Returns list of index lists."""
    n = len(keys)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if abs(len(keys[i]) - len(keys[j])) <= max_dist and family_distance(keys[i], keys[j]) <= max_dist:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def functional_profile(program, max_steps, trials=8, generations=8, seed=0):
    """
    How a program behaves against random partners: instruction usage, which head
    writes, net head movement, instructions per execution, and how many generations
    the copy stays faithful (partner half moved to the first half each generation).
    """
    program = np.asarray(program, dtype=np.uint8)
    key = core.program_key(program)
    hist = {chr(b): int((program == b).sum()) for b in core.COMMAND_BYTES}
    rng = np.random.default_rng(seed)
    ops_list = []
    faithful = []
    mirror = 0
    for _ in range(trials):
        tape = np.concatenate([program, rng.integers(0, 256, core.TAPE_SIZE, dtype=np.uint8)])
        ops_list.append(int(core.evaluate(tape, max_steps)))
        gens = 0
        for _ in range(generations):
            child = core.program_key(tape[core.TAPE_SIZE:])
            if child == key[::-1] and key != key[::-1]:
                mirror += 1                       # a faithful copy may be the mirror image
            elif child != key:
                break
            gens += 1
            tape = np.concatenate([tape[core.TAPE_SIZE:], rng.integers(0, 256, core.TAPE_SIZE, dtype=np.uint8)])
            core.evaluate(tape, max_steps)
        faithful.append(gens)
    writes_head0 = hist[','] + hist['+'] + hist['-']
    writes_head1 = hist['.']
    return {
        'instruction_histogram': hist,
        'writing_head': 'head0' if writes_head0 >= writes_head1 else 'head1',
        'head0_net_movement': hist['>'] - hist['<'],
        'head1_net_movement': hist['}'] - hist['{'],
        'loop_depth': max(0, hist['[']),
        'ops_per_execution': float(np.mean(ops_list)),
        'faithful_generations_mean': float(np.mean(faithful)),
        'faithful_generations_min': int(min(faithful)),
        'copies_as': 'mirror' if mirror > 0 else 'direct',   # alternate generations return to the original
    }


def primary_chain(tree):
    """Birth events along the primary ancestor line of a lineage tree, root first."""
    chain = []
    node = tree
    while node and node.get('tracked') and node.get('birth') and not node['birth'].get('initial'):
        chain.append((node['birth']['epoch'], node['birth']['slot']))
        node = node.get(node.get('primary') or 'parent')
    return chain


def attach_tapes(run, tree, max_births):
    """Replay the birth tapes along the primary chain and store them in the tree nodes."""
    node = tree
    done = 0
    while node and done < max_births and node.get('tracked') and node.get('birth') and not node['birth'].get('initial'):
        b = node['birth']
        t = run.tape_at(b['epoch'], b['slot'])
        node['tape'] = {'epoch': b['epoch'], 'slots': list(t['slots']), 'before': t['before'].tolist(),
                        'after': t['after'].tolist(), 'keys_before': list(t['keys_before']),
                        'keys_after': list(t['keys_after'])}
        done += 1
        node = node.get(node.get('primary') or 'parent')
    return done


# ---------------------------------------------------------------------------
# archive builder
# ---------------------------------------------------------------------------
def winners_at(run, epoch, top_n):
    """Top species from the checkpoint at or below `epoch`, with raw bytes and fresh selfrep scores."""
    ck_epoch, soup = run.checkpoint_at_or_before(epoch)
    hashes, lengths = core.compute_keys(soup)
    uniq, first, counts = np.unique(hashes, return_index=True, return_counts=True)
    order = np.argsort(-counts, kind='stable')[:top_n]
    reps = soup[first[order]]
    scores = core.selfrep_test(reps, seed=ck_epoch, max_steps=run.max_steps)
    out = []
    for k, i in enumerate(order.tolist()):
        h = int(uniq[i])
        row = run.species_row(h)
        out.append({'key': core.program_key(reps[k]), 'hash': str(h), 'length': int(lengths[first[i]]),
                    'program': reps[k].tolist(), 'count': int(counts[i]), 'share': float(counts[i] / soup.shape[0]),
                    'selfrep_score': int(scores[k]),
                    'first_epoch': row['first_epoch'] if row else None,
                    'first_slot': row['first_slot'] if row else None})
    return ck_epoch, out


def build_archive(run_path, top_n=20, tape_families=5, tape_births=12, log_points=2000, verbose=True):
    t0 = time.time()
    run = Run(run_path)
    name = os.path.basename(os.path.normpath(run_path))
    meta = run.meta
    log = run.log()
    ep = log['epoch']
    last_epoch = int(ep[-1])
    say = (lambda *a: print(*a, file=sys.stderr, flush=True)) if verbose else (lambda *a: None)

    # ---- events -----------------------------------------------------------
    sr = run.db.execute("SELECT MIN(epoch) FROM selfrep WHERE score >= ?", (core.SELFREP_THRESHOLD,)).fetchone()[0]
    long_top = log['top_key_len'] >= run.min_len
    events = {
        'entropy_gt_1': first_epoch_where(ep, log['higher_entropy'], lambda v: v > 1),
        'entropy_gt_3': first_epoch_where(ep, log['higher_entropy'], lambda v: v > TRANSITION_ENTROPY),
        # share crossings count only when the top species is a real program, not a short background key
        'share_gt_1pct': first_epoch_where(ep, log['top_share'], lambda v: (v > 0.01) & long_top),
        'share_gt_5pct': first_epoch_where(ep, log['top_share'], lambda v: (v > 0.05) & long_top),
        'share_gt_20pct': first_epoch_where(ep, log['top_share'], lambda v: (v > 0.20) & long_top),
        'first_selfrep_epoch': sr,
        'stop_triggered': meta.get('stop_triggered'),
        'last_epoch': last_epoch,
    }
    # takeover: half the soup holds self-replicators (a diverse replicator ecosystem may never push
    # entropy above 3, as in run 44). The transition epoch is the earliest of the two signals.
    sr_slots = log['selfrep_slots']
    events['selfrep_gt_50pct'] = first_epoch_where(ep, sr_slots, lambda v: v >= 0.5 * run.num_programs)
    candidates = [e for e in (events['entropy_gt_3'], events['selfrep_gt_50pct']) if e is not None]
    transition = min(candidates) if candidates else events['share_gt_20pct']
    events['transition_epoch'] = transition
    # the plateau phenomenon: replicators present but not taking over
    before = sr_slots[ep < transition] if transition is not None else sr_slots
    events['max_selfrep_share_before_takeover'] = float(before.max() / run.num_programs) if before.size and before.max() > 0 else 0.0
    dt = np.diff(log['elapsed_s'])          # elapsed restarts at 0 after a resume: count only forward steps
    fwd = dt >= 0
    events['epochs_per_second'] = float(fwd.sum() / dt[fwd].sum()) if fwd.any() and dt[fwd].sum() > 0 else None

    # ---- winners at end and milestones -----------------------------------
    say(f"[archive] winners at end (epoch {last_epoch})")
    end_epoch, end_winners = winners_at(run, last_epoch, top_n)
    winners = {'end': {'epoch': end_epoch, 'species': end_winners}}
    if transition is not None:
        for off in MILESTONES:
            target = transition + off
            if target <= last_epoch:
                say(f"[archive] winners at transition+{off} (epoch {target})")
                e, w = winners_at(run, target, top_n)
                winners[f'transition+{off}'] = {'epoch': e, 'species': w}

    # ---- families of the end winners --------------------------------------
    keys = [w['key'] for w in end_winners]
    families = []
    for fi, members in enumerate(cluster_families(keys)):
        members = sorted(members, key=lambda i: -end_winners[i]['count'])
        rep = end_winners[members[0]]
        # orient every member like the representative (reverse mirror-image members) before finding the core
        oriented = [keys[i] if edit_distance(keys[i], rep['key']) <= edit_distance(keys[i][::-1], rep['key'])
                    else keys[i][::-1] for i in members]
        fam = {
            'id': fi,
            'core': longest_common_substring(oriented) if len(members) > 1 else rep['key'],
            'representative': rep['key'],
            'members': [keys[i] for i in members],
            'mirror_members': sum(1 for i, o in zip(members, oriented) if o != keys[i]),
            'share': float(sum(end_winners[i]['share'] for i in members)),
            'selfrep_score': rep['selfrep_score'],
            'profile': functional_profile(rep['program'], run.max_steps),
        }
        for i in members:
            end_winners[i]['family'] = fi
        families.append(fam)
    families.sort(key=lambda f: -f['share'])
    for new_id, fam in enumerate(families):
        for w in end_winners:
            if w.get('family') == fam['id']:
                w['family'] = new_id
        fam['id'] = new_id

    # ---- emergence story --------------------------------------------------
    for fi, fam in enumerate(families):
        tree = run.trace_species(fam['representative'], depth=12)
        fam['lineage'] = tree
        fam['primary_chain'] = primary_chain(tree)
        if fi < tape_families and tree.get('tracked'):
            say(f"[archive] replaying birth tapes for family {fi} ({fam['representative']})")
            fam['tapes_recorded'] = attach_tapes(run, tree, tape_births)

    # ---- decimated log ----------------------------------------------------
    keep = np.zeros(ep.size, dtype=bool)
    keep[np.linspace(0, ep.size - 1, min(log_points, ep.size)).astype(int)] = True
    if transition is not None:
        keep |= (ep >= transition - 512) & (ep <= transition + 512)
    log_out = {c: v[keep].tolist() for c, v in log.items()}

    host = meta.get('host') or {}
    if not host:                                         # runs recorded before host info existed
        import socket, platform
        host = {'host': socket.gethostname().split('.')[0].lower(), 'machine': platform.machine(), 'inferred': True}
    protocol = meta.get('protocol') or f"{meta['num_programs'] // 1024}k-{meta.get('max_steps', 32768)}" + \
        ("-mut" if meta.get('mutation_prob', 0) > 0 else "")
    archive = {
        'run': name, 'host': host, 'protocol': protocol, 'path': os.path.abspath(run_path),
        'archived': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'params': {k: meta.get(k) for k in ('num_programs', 'tape_size', 'seed', 'mutation_prob', 'max_steps',
                                             'checkpoint_interval', 'lineage_min_len', 'promote_count', 'created',
                                             'finished', 'resumes', 'seed_programs')},
        'events': events,
        'final': {'epoch': last_epoch, 'higher_entropy': float(log['higher_entropy'][-1]), 'bpb': float(log['bpb'][-1]),
                  'unique_species': int(log['unique_species'][-1]), 'ops_per_pair': float(log['ops_per_pair'][-1]),
                  'top_share': float(log['top_share'][-1])},
        'winners': winners,
        'families': families,
        'log': log_out,
    }
    say(f"[archive] built in {time.time() - t0:.1f}s")
    return archive


def save_archive(archive, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(archive, f, default=_json_default)
    return os.path.getsize(out_path)


def default_archive_path(run_path, archive_dir='archive', host=None):
    """archive/<host>-<run>.json: runs are named by seed, so the host keeps machines apart."""
    name = os.path.basename(os.path.normpath(run_path))
    return os.path.join(archive_dir, f"{host}-{name}.json" if host else f"{name}.json")


def build_and_save(run_path, out_path=None, archive_dir='archive', **kw):
    archive = build_archive(run_path, **kw)
    out_path = out_path or default_archive_path(run_path, archive_dir, archive['host'].get('host'))
    size = save_archive(archive, out_path)
    return out_path, size, archive


def print_summary(a):
    ev = a['events']
    print(f"Run {a['run']} on {a.get('host', {}).get('host', '?')} [{a.get('protocol', '?')}]: {a['params']['num_programs']} programs, seed {a['params']['seed']}, "
          f"{ev['last_epoch']} epochs, {ev['epochs_per_second'] or 0:.1f} epochs/s")
    print(f"  transition: {ev['transition_epoch']} (entropy > 3: {ev['entropy_gt_3']}, replicators > 50%: {ev.get('selfrep_gt_50pct')})   "
          f"first self-replicator: {ev['first_selfrep_epoch']}   "
          f"share > 5%: {ev['share_gt_5pct']}   share > 20%: {ev['share_gt_20pct']}")
    print(f"  final: entropy {a['final']['higher_entropy']:.2f}, bpb {a['final']['bpb']:.2f}, "
          f"{a['final']['unique_species']} species, top share {100 * a['final']['top_share']:.1f}%")
    print(f"  families at the end ({len(a['families'])}):")
    for f in a['families'][:8]:
        p = f['profile']
        print(f"    {100 * f['share']:5.1f}%  selfrep {f['selfrep_score']:2d}  {len(f['members']):2d} variants"
              f"{' (' + str(f['mirror_members']) + ' mirrored)' if f.get('mirror_members') else ''}  "
              f"core {f['core'] or '(empty)'}   (rep {f['representative'] or '(empty)'}, born {f['lineage'].get('first_epoch')}, "
              f"{p['ops_per_execution']:.0f} ops, faithful {p['faithful_generations_mean']:.1f} gens"
              f"{', copies as mirror image' if p.get('copies_as') == 'mirror' else ''})")


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the archive of a BFF run")
    p.add_argument('run')
    p.add_argument('--out', default=None, help='output file (default <archive-dir>/<host>-<run>.json)')
    p.add_argument('--archive-dir', default=os.environ.get('BFF_ARCHIVE_DIR', 'archive'))
    p.add_argument('--top', type=int, default=20)
    p.add_argument('--tapes', type=int, default=5, help='families whose birth tapes are replayed and stored')
    p.add_argument('--tape-births', type=int, default=12, help='birth events per family to store tapes for')
    a = p.parse_args(argv)
    out, size, archive = build_and_save(a.run, a.out, archive_dir=a.archive_dir, top_n=a.top, tape_families=a.tapes,
                                        tape_births=a.tape_births)
    print_summary(archive)
    print(f"  written to {out} ({size / 1024:.0f} KB)")


if __name__ == '__main__':
    main()
