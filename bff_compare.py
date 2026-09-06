#!/usr/bin/env python3
"""
Cross-run analytics over run archives (built by bff_archive.py).

    python bff_compare.py                          # all archives in archive/ (or $BFF_ARCHIVE_DIR)
    python bff_compare.py ../results/archive       # a directory, or individual files
    python bff_compare.py --survival               # fraction transitioned by epoch, per protocol (censoring-aware)
    python bff_compare.py --families               # leading family cores across runs, with edit distances
    python bff_compare.py --csv runs.csv           # one row per run for spreadsheets
    python bff_compare.py --json

A run that ended without a transition is a censored observation (the transition
would have come later than its last epoch); the survival table uses the
Kaplan-Meier estimate so such runs count for the epochs they did cover.
"""

import argparse
import csv
import glob
import json
import os
import sys

from bff_query import edit_distance

SURVIVAL_EPOCHS = (5000, 10000, 16000, 20000, 30000, 40000, 50000, 60000, 80000, 100000)


def load_archives(paths):
    files = []
    for p in (paths or [os.environ.get('BFF_ARCHIVE_DIR', 'archive')]):
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, '*.json')))
        else:
            files += sorted(glob.glob(p))
    out = []
    for f in files:
        with open(f) as fh:
            a = json.load(fh)
        a['_file'] = f
        out.append(a)
    return out


def run_row(a):
    ev, pr, fi = a['events'], a['params'], a['final']
    fam = a['families'][0] if a['families'] else None
    host = a.get('host') or {}
    return {
        'run': a['run'], 'host': host.get('host', '?'), 'protocol': a.get('protocol', '?'),
        'programs': pr['num_programs'], 'seed': pr['seed'], 'max_steps': pr['max_steps'],
        'mutation': pr['mutation_prob'], 'epochs': ev['last_epoch'], 'eps': ev['epochs_per_second'],
        'transition': ev['transition_epoch'], 'first_selfrep': ev['first_selfrep_epoch'],
        'takeover': ev.get('selfrep_gt_50pct'), 'entropy_gt_3': ev.get('entropy_gt_3'),
        'plateau': ev.get('max_selfrep_share_before_takeover'),
        'final_entropy': fi['higher_entropy'], 'final_species': fi['unique_species'],
        'top_core': fam['core'] if fam else None, 'top_share': fam['share'] if fam else None,
        'top_selfrep': fam['selfrep_score'] if fam else None,
        'top_len': len(fam['representative']) if fam else None,
        'top_copies_as': fam['profile'].get('copies_as') if fam else None,
        'families_replicating': sum(1 for f in a['families'] if f['selfrep_score'] >= 5),
    }


def fmt(c, v):
    if v is None:
        return '-'
    if isinstance(v, float):
        if c in ('mutation', 'eps'):
            return f"{v:.3g}"
        if c in ('top_share', 'plateau'):
            return f"{100 * v:.1f}%"
        return f"{v:.2f}"
    return str(v)


def print_table(rows):
    cols = [('run', 5), ('host', 12), ('protocol', 14), ('seed', 5), ('epochs', 7), ('eps', 5),
            ('first_selfrep', 13), ('transition', 10), ('plateau', 8), ('final_entropy', 13),
            ('top_share', 9), ('top_selfrep', 11), ('top_len', 7), ('top_copies_as', 13), ('top_core', 0)]
    print(' '.join(f"{c:>{w}}" if w else c for c, w in cols))
    for r in sorted(rows, key=lambda r: (r['protocol'], r['host'], r['seed'])):
        print(' '.join(f"{fmt(c, r[c]):>{w}}" if w else fmt(c, r[c]) for c, w in cols))


def kaplan_meier(times, transitioned, at):
    """Fraction transitioned by each epoch in `at`, from (time, event) pairs with right-censoring."""
    obs = sorted(zip(times, transitioned))
    out = []
    for t_eval in at:
        surv = 1.0
        n_risk = len(obs)
        i = 0
        while i < len(obs) and obs[i][0] <= t_eval:
            t, ev = obs[i]
            d = sum(1 for tt, ee in obs[i:] if tt == t and ee)
            c = sum(1 for tt, ee in obs[i:] if tt == t and not ee)
            if n_risk > 0 and d:
                surv *= 1 - d / n_risk
            n_risk -= d + c
            i += d + c
        out.append((t_eval, 1 - surv, sum(1 for t, _ in obs if t >= t_eval)))
    return out


def survival(rows):
    """Per protocol: runs, transitions, and the transitioned fraction at fixed epochs."""
    by = {}
    for r in rows:
        by.setdefault(r['protocol'], []).append(r)
    result = {}
    for proto, rs in sorted(by.items()):
        times = [r['transition'] if r['transition'] is not None else r['epochs'] for r in rs]
        events = [r['transition'] is not None for r in rs]
        horizon = max(times) if times else 0
        at = [e for e in SURVIVAL_EPOCHS if e < horizon] + [horizon]
        km = kaplan_meier(times, events, at)
        result[proto] = {'runs': len(rs), 'transitions': sum(events),
                         'transition_epochs': sorted(t for t, e in zip(times, events) if e),
                         'censored_at': sorted(t for t, e in zip(times, events) if not e),
                         'curve': [{'epoch': e, 'fraction_transitioned': f, 'runs_observed_this_far': n} for e, f, n in km],
                         'plateau_runs': sum(1 for r in rs if (r['plateau'] or 0) >= 0.01)}
    return result


def print_survival(sv):
    for proto, s in sv.items():
        print(f"\nprotocol {proto}: {s['runs']} runs, {s['transitions']} transitions at epochs {s['transition_epochs']}, "
              f"censored at {s['censored_at']}; runs with an early replicator plateau (>= 1% of slots before takeover): {s['plateau_runs']}")
        print(f"  {'epoch':>7} {'transitioned':>13} {'runs observed this far':>23}")
        for c in s['curve']:
            print(f"  {c['epoch']:7d} {100 * c['fraction_transitioned']:12.0f}% {c['runs_observed_this_far']:23d}")


def family_comparison(archives, top=3):
    """Leading family cores of each run and pairwise edit distances between runs' top cores."""
    entries = []
    for a in archives:
        for f in a['families'][:top]:
            entries.append({'run': a['run'], 'host': (a.get('host') or {}).get('host', '?'), 'rank': f['id'], 'core': f['core'],
                            'representative': f['representative'], 'share': f['share'], 'selfrep': f['selfrep_score'],
                            'writing_head': f['profile']['writing_head'], 'copies_as': f['profile'].get('copies_as'),
                            'ops': f['profile']['ops_per_execution']})
    by_core = {}
    for e in entries:
        if e['selfrep'] >= 5 and e['core']:
            by_core.setdefault(e['core'], set()).add(f"{e['host']}/{e['run']}")
    recurring = {c: sorted(r) for c, r in by_core.items() if len(r) > 1}
    leads = [(f"{(a.get('host') or {}).get('host', '?')}/{a['run']}", a['families'][0]['core']) for a in archives
             if a['families'] and a['families'][0]['selfrep_score'] >= 5]
    dist = []
    for i in range(len(leads)):
        for j in range(i + 1, len(leads)):
            dist.append({'run_a': leads[i][0], 'run_b': leads[j][0],
                         'distance': min(edit_distance(leads[i][1], leads[j][1]), edit_distance(leads[i][1], leads[j][1][::-1]))})
    return {'entries': entries, 'recurring_cores': recurring, 'leading_core_distances': dist}


def main(argv=None):
    p = argparse.ArgumentParser(description="Compare BFF run archives")
    p.add_argument('archives', nargs='*', help='archive files or directories (default: archive/ or $BFF_ARCHIVE_DIR)')
    p.add_argument('--survival', action='store_true', help='fraction transitioned by epoch, per protocol')
    p.add_argument('--families', action='store_true', help='compare leading family cores across runs')
    p.add_argument('--top', type=int, default=3, help='families per run to include with --families')
    p.add_argument('--csv', metavar='FILE', help='write one row per run to FILE')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    archives = load_archives(a.archives)
    if not archives:
        sys.exit("no archives found")
    rows = [run_row(x) for x in archives]
    if a.csv:
        with open(a.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {a.csv}", file=sys.stderr)
    if a.json:
        out = {'runs': rows}
        if a.survival:
            out['survival'] = survival(rows)
        if a.families:
            out['families'] = family_comparison(archives, a.top)
        print(json.dumps(out, indent=2))
        return
    print_table(rows)
    if a.survival:
        print_survival(survival(rows))
    if a.families:
        fc = family_comparison(archives, a.top)
        print("\nLeading replicating families per run:")
        for e in fc['entries']:
            if e['selfrep'] >= 5:
                print(f"  {e['host']:>12}/{e['run']:<4} #{e['rank']}  {100 * e['share']:5.1f}%  selfrep {e['selfrep']:2d}  "
                      f"{e['copies_as'] or '':6}  {e['ops']:6.0f} ops  core {e['core']}")
        if fc['recurring_cores']:
            print("\nCores recurring across runs:")
            for c, runs in fc['recurring_cores'].items():
                print(f"  {c}   in {', '.join(runs)}")
        if fc['leading_core_distances']:
            print("\nEdit distance (up to reversal) between runs' leading replicator cores:")
            for d in fc['leading_core_distances']:
                print(f"  {d['run_a']} vs {d['run_b']}: {d['distance']}")


if __name__ == '__main__':
    main()
