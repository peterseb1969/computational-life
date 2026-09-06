#!/usr/bin/env python3
"""
Cross-run analytics over run archives (archive/*.json, built by bff_archive.py).

    python bff_compare.py                      # table of all runs in archive/
    python bff_compare.py archive/*.json       # specific archives
    python bff_compare.py --families           # leading family cores across runs, with edit distances
    python bff_compare.py --json               # machine-readable

Reports per run: size, seed, step budget, mutation, transition epoch, first
self-replicator, final entropy, leading family core and share. With --families
it compares the leading family cores of every run pair by edit distance and lists
cores that recur across runs.
"""

import argparse
import glob
import json
import os
import sys

from bff_query import edit_distance


def load_archives(paths):
    if not paths:
        paths = sorted(glob.glob(os.path.join('archive', '*.json')))
    out = []
    for p in paths:
        with open(p) as f:
            a = json.load(f)
        a['_file'] = p
        out.append(a)
    return out


def run_row(a):
    ev, pr, fi = a['events'], a['params'], a['final']
    fam = a['families'][0] if a['families'] else None
    return {
        'run': a['run'], 'programs': pr['num_programs'], 'seed': pr['seed'], 'max_steps': pr['max_steps'],
        'mutation': pr['mutation_prob'], 'epochs': ev['last_epoch'], 'eps': ev['epochs_per_second'],
        'transition': ev['transition_epoch'], 'first_selfrep': ev['first_selfrep_epoch'],
        'takeover': ev.get('selfrep_gt_50pct'),
        'share_gt_20pct': ev['share_gt_20pct'], 'final_entropy': fi['higher_entropy'],
        'final_species': fi['unique_species'],
        'top_core': fam['core'] if fam else None, 'top_share': fam['share'] if fam else None,
        'top_selfrep': fam['selfrep_score'] if fam else None,
        'top_family_variants': len(fam['members']) if fam else 0,
        'families_replicating': sum(1 for f in a['families'] if f['selfrep_score'] >= 5),
    }


def print_table(rows):
    cols = [('run', 8), ('programs', 8), ('seed', 5), ('max_steps', 9), ('mutation', 9), ('epochs', 7), ('eps', 5),
            ('transition', 10), ('first_selfrep', 13), ('takeover', 8), ('final_entropy', 13), ('top_share', 9), ('top_selfrep', 11), ('top_core', 0)]
    print(' '.join(f"{c:>{w}}" if w else c for c, w in cols))
    for r in rows:
        cells = []
        for c, w in cols:
            v = r[c]
            if v is None:
                t = '-'
            elif isinstance(v, float):
                t = f"{v:.3g}" if c in ('mutation', 'eps') else (f"{100 * v:.1f}%" if c == 'top_share' else f"{v:.2f}")
            else:
                t = str(v)
            cells.append(f"{t:>{w}}" if w else t)
        print(' '.join(cells))


def family_comparison(archives, top=3):
    """Leading family cores of each run and pairwise edit distances between runs' top cores."""
    entries = []
    for a in archives:
        for f in a['families'][:top]:
            entries.append({'run': a['run'], 'rank': f['id'], 'core': f['core'], 'representative': f['representative'],
                            'share': f['share'], 'selfrep': f['selfrep_score'],
                            'writing_head': f['profile']['writing_head'],
                            'ops': f['profile']['ops_per_execution']})
    # recurring cores
    by_core = {}
    for e in entries:
        by_core.setdefault(e['core'], set()).add(e['run'])
    recurring = {c: sorted(r) for c, r in by_core.items() if len(r) > 1}
    # pairwise distance between runs' leading cores
    leads = [(a['run'], a['families'][0]['core']) for a in archives if a['families']]
    dist = []
    for i in range(len(leads)):
        for j in range(i + 1, len(leads)):
            dist.append({'run_a': leads[i][0], 'run_b': leads[j][0], 'distance': edit_distance(leads[i][1], leads[j][1])})
    return {'entries': entries, 'recurring_cores': recurring, 'leading_core_distances': dist}


def main(argv=None):
    p = argparse.ArgumentParser(description="Compare BFF run archives")
    p.add_argument('archives', nargs='*', help='archive JSON files (default: archive/*.json)')
    p.add_argument('--families', action='store_true', help='compare leading family cores across runs')
    p.add_argument('--top', type=int, default=3, help='families per run to include with --families')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    archives = load_archives(a.archives)
    if not archives:
        sys.exit("no archives found")
    rows = [run_row(x) for x in archives]
    if a.json:
        out = {'runs': rows}
        if a.families:
            out['families'] = family_comparison(archives, a.top)
        print(json.dumps(out, indent=2))
        return
    print_table(rows)
    if a.families:
        fc = family_comparison(archives, a.top)
        print("\nLeading families per run:")
        for e in fc['entries']:
            print(f"  {e['run']:>8} #{e['rank']}  {100 * e['share']:5.1f}%  selfrep {e['selfrep']:2d}  {e['writing_head']}  "
                  f"{e['ops']:6.0f} ops  core {e['core']}")
        if fc['recurring_cores']:
            print("\nCores recurring across runs:")
            for c, runs in fc['recurring_cores'].items():
                print(f"  {c}   in {', '.join(runs)}")
        if fc['leading_core_distances']:
            print("\nEdit distance between runs' leading cores:")
            for d in fc['leading_core_distances']:
                print(f"  {d['run_a']} vs {d['run_b']}: {d['distance']}")


if __name__ == '__main__':
    main()
