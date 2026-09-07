#!/usr/bin/env python3
"""
Query layer over a BFF run directory: search, species history, lineage, replay.

Library use:
    from bff_query import Run
    run = Run('runs/42')
    run.search('[[<,,,}]]', mode='substring')
    run.species(key_or_hash)                  # birth row + count history + selfrep scores
    run.trace_species(key_or_hash, depth=8)   # ancestry tree from the birth records (instant)
    run.slot_history(slot, before=epoch)      # tracked key changes of one slot
    run.soup_at(epoch)                        # exact soup at any epoch (replayed from a checkpoint)
    run.tape_at(epoch, slot)                  # the 128-byte tape of slot's pair before/after epoch

Command line:
    python bff_query.py search  runs/42 '[[<,,,}]]' [--mode substring|exact|regex|fuzzy] [--max-dist 3]
    python bff_query.py species runs/42 '[[<,,,}]]}}]]},,,<[['
    python bff_query.py lineage runs/42 '[[<,,,}]]}}]]},,,<[[' [--depth 8]
    python bff_query.py slot    runs/42 12345 [--before 39000]
    python bff_query.py top     runs/42 [--epoch 39424] [--n 20]
    python bff_query.py tape    runs/42 --epoch 39301 --slot 12345
"""

import argparse
import functools
import collections
import json
import os
import re
import sqlite3
import sys
import time

import numpy as np
import bff_core as core
from bff_core import levenshtein, batch_levenshtein
from bff_lineage import RunDir, CHANGE_DTYPE, NO_PARTNER, open_db, read_log


# ---------------------------------------------------------------------------
# Edit distance (Numba)
# ---------------------------------------------------------------------------
def edit_distance(a, b):
    return core.edit_distance(a, b)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class Run:
    """Read-only access to a run directory (safe to use while the simulation is writing)."""

    def __init__(self, path):
        self.rd = RunDir(path)
        if not self.rd.exists():
            raise FileNotFoundError(f"{path} is not a run directory (no meta.json)")
        self.meta = self.rd.read_meta()
        self.num_programs = self.meta['num_programs']
        self.seed = self.meta['seed']
        self.min_len = self.meta.get('lineage_min_len', 8)
        self.max_steps = self.meta.get('max_steps', core.DEFAULT_MAX_STEPS)
        self.mutation_int = int(round(self.meta.get('mutation_prob', 0.0) * (1 << 30)))
        # the rate may have been changed on a resume: (from_epoch, rate) pairs, ascending
        self.mutation_schedule = [(int(x['from_epoch']), int(round(x['prob'] * (1 << 30))))
                                  for x in self.meta.get('mutation_schedule') or [{'from_epoch': 0, 'prob': self.meta.get('mutation_prob', 0.0)}]]
        self.heads = bool(self.meta.get('heads', False))
        self.db = open_db(self.rd)
        self.db.create_function("REGEXP", 2, lambda pat, s: s is not None and re.search(pat, s) is not None)
        self._changes = None
        self._changes_size = -1
        self._slot_index = None
        self._soup_cache = {}          # epoch -> soup (small LRU)
        self._cursor = None            # (epoch, soup) for incremental replay

    # -- basics ---------------------------------------------------------------
    def reload_meta(self):
        self.meta = self.rd.read_meta()
        return self.meta

    def last_epoch(self):
        """Last epoch present in the log (the simulation may still be running)."""
        try:
            with open(self.rd.log_path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode(errors='ignore').strip().splitlines()
            for line in reversed(tail):
                head = line.split(',', 1)[0]
                if head.isdigit():
                    return int(head)
        except FileNotFoundError:
            pass
        return -1

    def log(self, columns=None):
        """The metrics log as dict of numpy arrays (layout changes at resumes are handled)."""
        out = read_log(self.rd.log_path)
        if columns:
            out = {c: out[c] for c in columns}
        return out

    def checkpoints(self):
        return self.rd.checkpoints()

    # -- keys and hashes ------------------------------------------------------
    @staticmethod
    def resolve_hash(key_or_hash):
        """Accept an instruction string, a decimal/hex hash, or an int; return the uint64 hash."""
        if isinstance(key_or_hash, (int, np.integer)):
            return core.to_unsigned(int(key_or_hash))
        s = str(key_or_hash)
        if re.fullmatch(r'0x[0-9a-fA-F]+', s):
            return int(s, 16)
        if re.fullmatch(r'-?\d{6,}', s):
            return core.to_unsigned(int(s))
        return core.key_hash(s)

    def species_row(self, key_or_hash):
        h = self.resolve_hash(key_or_hash)
        row = self.db.execute("SELECT * FROM species WHERE hash = ?", (core.to_signed(h),)).fetchone()
        return dict(row) if row else None

    def key_of(self, h):
        """Text of a hash if known (recorded species or snapshot key), else None."""
        hs = core.to_signed(h)
        row = self.db.execute("SELECT key FROM species WHERE hash = ?", (hs,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT key FROM keys WHERE hash = ?", (hs,)).fetchone()
        return row[0] if row else None

    # -- search ---------------------------------------------------------------
    def search(self, pattern, mode='substring', max_dist=2, limit=50, min_count=0, epoch=None):
        """
        Search tracked species. mode: exact | substring | regex | fuzzy.
        With `epoch`, searches the keys present in the checkpoint nearest below that epoch
        instead (all lengths, with counts at that epoch).
        Returns a list of dicts sorted by peak count.
        """
        if epoch is not None:
            return self._search_checkpoint(pattern, mode, max_dist, limit, epoch)

        if mode == 'exact':
            rows = self.db.execute("SELECT * FROM species WHERE key = ?", (pattern,)).fetchall()
        elif mode == 'substring':
            rows = self.db.execute("SELECT * FROM species WHERE instr(key, ?) > 0", (pattern,)).fetchall()
        elif mode == 'regex':
            rows = self.db.execute("SELECT * FROM species WHERE key REGEXP ?", (pattern,)).fetchall()
        elif mode == 'fuzzy':
            rows = self._fuzzy_rows(pattern, max_dist)
        else:
            raise ValueError(f"unknown mode {mode}")

        results = [dict(r) for r in rows]
        if mode == 'fuzzy':
            for r in results:
                r['distance'] = edit_distance(r['key'], pattern)
        self._attach_stats(results)
        results = [r for r in results if r['peak_count'] >= min_count]
        results.sort(key=lambda r: (-r['peak_count'], r['first_epoch']))
        return results[:limit] if limit else results

    def _fuzzy_rows(self, pattern, max_dist):
        q = np.frombuffer(pattern.encode('ascii'), dtype=np.uint8)
        out_rows = []
        cur = self.db.execute("SELECT hash, key FROM species WHERE length BETWEEN ? AND ?",
                              (max(0, q.size - max_dist), q.size + max_dist))
        while True:
            chunk = cur.fetchmany(200_000)
            if not chunk:
                break
            keys = np.zeros((len(chunk), core.TAPE_SIZE), dtype=np.uint8)
            lens = np.empty(len(chunk), dtype=np.int32)
            for i, (_, k) in enumerate(chunk):
                b = k.encode('ascii')
                keys[i, :len(b)] = np.frombuffer(b, dtype=np.uint8)
                lens[i] = len(b)
            dist = np.empty(len(chunk), dtype=np.int32)
            batch_levenshtein(keys, lens, q, q.size, max_dist, dist)
            for i in np.flatnonzero(dist <= max_dist).tolist():
                out_rows.append(chunk[i][0])
        if not out_rows:
            return []
        rows = []
        for i in range(0, len(out_rows), 900):
            part = out_rows[i:i + 900]
            rows += self.db.execute(f"SELECT * FROM species WHERE hash IN ({','.join('?' * len(part))})",
                                    part).fetchall()
        return rows

    def _attach_stats(self, results):
        """Add peak/latest counts and best selfrep score to species dicts (in place)."""
        for r in results:
            h = r['hash']
            row = self.db.execute("SELECT MAX(count), MAX(epoch), MIN(epoch) FROM species_counts WHERE hash = ?",
                                  (h,)).fetchone()
            r['peak_count'] = row[0] or 0
            r['last_seen_epoch'] = row[1]
            r['first_counted_epoch'] = row[2]
            if row[0]:
                pk = self.db.execute("SELECT epoch FROM species_counts WHERE hash = ? AND count = ? LIMIT 1",
                                     (h, row[0])).fetchone()
                r['peak_epoch'] = pk[0]
                lat = self.db.execute("SELECT count FROM species_counts WHERE hash = ? ORDER BY epoch DESC LIMIT 1",
                                      (h,)).fetchone()
                r['latest_count'] = lat[0]
            else:
                r['peak_epoch'] = None
                r['latest_count'] = 0
            sr = self.db.execute("SELECT MAX(score) FROM selfrep WHERE hash = ?", (h,)).fetchone()
            r['selfrep_score'] = sr[0]
            r['hash'] = core.to_unsigned(h)

    def _search_checkpoint(self, pattern, mode, max_dist, limit, epoch):
        ck_epoch, soup = self.checkpoint_at_or_before(epoch)
        hashes, lengths = core.compute_keys(soup)
        uniq, first, counts = np.unique(hashes, return_index=True, return_counts=True)
        rx = re.compile(pattern) if mode == 'regex' else None
        results = []
        for h, i, c in zip(uniq.tolist(), first.tolist(), counts.tolist()):
            k = core.program_key(soup[i])
            if mode == 'exact' and k != pattern:
                continue
            if mode == 'substring' and pattern not in k:
                continue
            if mode == 'regex' and not rx.search(k):
                continue
            d = None
            if mode == 'fuzzy':
                d = edit_distance(k, pattern)
                if d > max_dist:
                    continue
            results.append({'hash': h, 'key': k, 'length': int(lengths[i]), 'count': c,
                            'share': c / self.num_programs, 'epoch': ck_epoch, 'distance': d,
                            'slot': i})
        results.sort(key=lambda r: -r['count'])
        return results[:limit] if limit else results

    # -- species ---------------------------------------------------------------
    def species(self, key_or_hash):
        """Birth row, count history, and selfrep history of one species (None if untracked)."""
        row = self.species_row(key_or_hash)
        if row is None:
            return None
        h = row['hash']
        hist = self.db.execute("SELECT epoch, count FROM species_counts WHERE hash = ? ORDER BY epoch",
                               (h,)).fetchall()
        sr = self.db.execute("SELECT epoch, score, count FROM selfrep WHERE hash = ? ORDER BY epoch",
                             (h,)).fetchall()
        out = dict(row)
        self._attach_stats([out])
        out['counts'] = [(e, c) for e, c in hist]
        out['selfrep'] = [(e, s, c) for e, s, c in sr]
        out['birth'] = self.birth_event(out)
        return out

    def birth_event(self, row):
        """Describe the tape on which a species first appeared."""
        if row['first_partner'] is None:
            return {'epoch': 0, 'slot': row['first_slot'], 'initial': True}
        e, s, p = row['first_epoch'], row['first_slot'], row['first_partner']
        pos = self.tape_position(e, s)
        parent_key = row.get('parent_key') or self.key_of(core.to_unsigned(row['parent_hash']))
        partner_key = row.get('partner_key') or self.key_of(core.to_unsigned(row['partner_hash']))
        return {'epoch': e, 'slot': s, 'partner': p, 'initial': False,
                'slot_position': pos,                      # 0: slot was the first half of the tape
                'parent_key': parent_key, 'partner_key': partner_key,
                'parent_hash': core.to_unsigned(row['parent_hash']),
                'partner_hash': core.to_unsigned(row['partner_hash']),
                'parent_tracked': self.key_of(core.to_unsigned(row['parent_hash'])) is not None,
                'partner_tracked': self.key_of(core.to_unsigned(row['partner_hash'])) is not None}

    def top_species(self, epoch=None, n=20):
        """Most common species at the nearest recorded snapshot <= epoch (default: latest)."""
        if epoch is None:
            epoch = self.db.execute("SELECT MAX(epoch) FROM species_counts").fetchone()[0]
        else:
            epoch = self.db.execute("SELECT MAX(epoch) FROM species_counts WHERE epoch <= ?",
                                    (epoch,)).fetchone()[0]
        if epoch is None:
            return epoch, []
        rows = self.db.execute("""SELECT c.hash, c.count, COALESCE(s.key, k.key) AS key,
                                         COALESCE(s.length, k.length) AS length, s.first_epoch
                                  FROM species_counts c LEFT JOIN species s ON s.hash = c.hash
                                                        LEFT JOIN keys k ON k.hash = c.hash
                                  WHERE c.epoch = ? ORDER BY c.count DESC LIMIT ?""", (epoch, n)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['hash'] = core.to_unsigned(d['hash'])
            d['share'] = d['count'] / self.num_programs
            sr = self.db.execute("SELECT score FROM selfrep WHERE hash = ? ORDER BY epoch DESC LIMIT 1",
                                 (r['hash'],)).fetchone()
            d['selfrep_score'] = sr[0] if sr else None
            out.append(d)
        return epoch, out

    def species_timeline(self, hashes):
        """{hash: [(epoch, count), ...]} for several species (for stacked plots)."""
        out = {}
        for h in hashes:
            hs = core.to_signed(h)
            out[core.to_unsigned(h)] = self.db.execute(
                "SELECT epoch, count FROM species_counts WHERE hash = ? ORDER BY epoch", (hs,)).fetchall()
        return out

    # -- permutations ----------------------------------------------------------
    @functools.lru_cache(maxsize=256)
    def perm(self, epoch):
        return core.epoch_permutation(self.seed, epoch, self.num_programs)

    def tape_position(self, epoch, slot):
        """0 if slot formed the first half of its tape in that epoch, 1 if the second."""
        perm = self.perm(epoch)
        pos = int(np.flatnonzero(perm == slot)[0])
        return pos & 1

    def pair_of(self, epoch, slot):
        """(first_slot, second_slot) of the tape that `slot` was part of in `epoch`."""
        perm = self.perm(epoch)
        pos = int(np.flatnonzero(perm == slot)[0])
        i = pos & ~1
        return int(perm[i]), int(perm[i + 1])

    # -- change records ---------------------------------------------------------
    def changes(self):
        """Memory-mapped change records (re-opened when the file has grown)."""
        size = os.path.getsize(self.rd.changes_path)
        if self._changes is None or size != self._changes_size:
            n = size // CHANGE_DTYPE.itemsize
            self._changes = np.memmap(self.rd.changes_path, dtype=CHANGE_DTYPE, mode='r', shape=(n,))
            self._changes_size = size
            self._slot_index = None
        return self._changes

    def _build_slot_index(self):
        """Stable sort of record indices by slot, plus per-slot offsets (built lazily, kept in memory)."""
        recs = self.changes()
        slots = np.asarray(recs['slot'])
        order = np.argsort(slots, kind='stable').astype(np.uint32)
        offsets = np.searchsorted(slots[order], np.arange(self.num_programs + 1), side='left')
        self._slot_index = (order, offsets, recs.shape[0])

    def slot_history(self, slot, before=None, after=None):
        """
        Tracked key changes of one slot, oldest first: list of dicts
        (epoch, partner, hash, key, position). Untracked (short-key) periods are not visible.
        """
        recs = self.changes()
        if self._slot_index is None or self._slot_index[2] != recs.shape[0]:
            self._build_slot_index()
        order, offsets, _ = self._slot_index
        idx = order[offsets[slot]:offsets[slot + 1]]
        sub = recs[np.sort(idx)]
        if before is not None:
            sub = sub[sub['epoch'] < before]
        if after is not None:
            sub = sub[sub['epoch'] > after]
        out = []
        for r in sub.tolist():
            e, s, p, h = r
            out.append({'epoch': int(e), 'slot': int(s), 'partner': None if p == NO_PARTNER else int(p),
                        'hash': int(h), 'key': self.key_of(int(h)), 'length': None,
                        'position': None if p == NO_PARTNER else self.tape_position(int(e), int(s))})
        return out

    def key_in_slot_before(self, epoch, slot):
        """Last tracked change of `slot` strictly before `epoch` (None if never tracked)."""
        hist = self.slot_history(slot, before=epoch)
        return hist[-1] if hist else None

    # -- lineage ------------------------------------------------------------------
    def trace_species(self, key_or_hash, depth=8, _seen=None):
        """
        Ancestry tree from birth records. Each node: the species, its birth event, and
        'parent' (previous occupant of the slot) and 'partner' (other tape half) subtrees.
        Untracked ancestors are leaves with their text. The closer parent by edit distance
        is flagged as 'primary'.
        """
        _seen = _seen if _seen is not None else set()
        row = self.species_row(key_or_hash)
        if row is None:
            h = self.resolve_hash(key_or_hash)
            return {'hash': h, 'key': key_or_hash if isinstance(key_or_hash, str) else None,
                    'tracked': False, 'note': 'untracked (short/background key)'}
        h = core.to_unsigned(row['hash'])
        node = {'hash': h, 'key': row['key'], 'length': row['length'], 'tracked': True,
                'first_epoch': row['first_epoch'], 'first_slot': row['first_slot']}
        stats = [dict(row)]
        self._attach_stats(stats)
        node['peak_count'] = stats[0]['peak_count']
        node['selfrep_score'] = stats[0]['selfrep_score']
        birth = self.birth_event(row)
        node['birth'] = birth
        if birth['initial']:
            node['note'] = 'present in the initial soup'
            return node
        if h in _seen:
            node['note'] = 'cycle/duplicate, not expanded'
            return node
        _seen.add(h)
        if depth <= 0:
            node['note'] = 'depth limit'
            return node
        # a replicator may write its mirror image: compare against reversed keys as well
        key = row['key']
        dists = {}
        for role, k in (('parent', birth['parent_key'] or ''), ('partner', birth['partner_key'] or '')):
            direct, mirror = edit_distance(key, k), edit_distance(key, k[::-1])
            node[f'{role}_distance'] = direct
            node[f'{role}_mirror_distance'] = mirror
            node[f'{role}_mirror'] = mirror < direct
            dists[role] = min(direct, mirror)
        node['primary'] = 'parent' if dists['parent'] <= dists['partner'] else 'partner'
        node['primary_mirror'] = node[f"{node['primary']}_mirror"]
        for role, hh, key, tracked in (('parent', birth['parent_hash'], birth['parent_key'], birth['parent_tracked']),
                                       ('partner', birth['partner_hash'], birth['partner_key'], birth['partner_tracked'])):
            if tracked:
                node[role] = self.trace_species(hh, depth - 1, _seen)
            else:
                short = key is not None and len(key) < self.min_len
                node[role] = {'hash': hh, 'key': key, 'tracked': False,
                              'note': ('background key (shorter than lineage-min-len)' if short else
                                       'not recorded (never reached the promotion count while in the window)')}
        return node

    def trace_instance(self, epoch, slot, depth=8):
        """
        Slot-level ancestry of what slot `slot` held after `epoch`: which tape produced it and
        what the two tape halves held, following each half's last tracked change. Exact
        content of any node is available through tape_at()/soup_at().
        """
        hist = self.slot_history(slot, before=epoch + 1)
        if not hist:
            return {'epoch': epoch, 'slot': slot, 'note': 'no tracked change for this slot up to that epoch'}
        last = hist[-1]
        node = {'epoch': last['epoch'], 'slot': slot, 'hash': last['hash'], 'key': last['key'],
                'partner': last['partner'], 'position': last['position']}
        if last['partner'] is None or depth <= 0:
            node['note'] = 'initial soup' if last['partner'] is None else 'depth limit'
            return node
        e = last['epoch']
        node['parent'] = self.trace_instance(e - 1, slot, depth - 1)
        node['partner_node'] = self.trace_instance(e - 1, last['partner'], depth - 1)
        return node

    # -- replay ------------------------------------------------------------------
    def mutation_at(self, epoch):
        rate = self.mutation_schedule[0][1]
        for from_epoch, r in self.mutation_schedule:
            if epoch >= from_epoch:
                rate = r
        return rate

    def checkpoint_at_or_before(self, epoch):
        cps = [(e, p) for e, p in self.checkpoints() if e <= epoch]
        if not cps:
            raise ValueError(f"no checkpoint at or before epoch {epoch}")
        for e, p in reversed(cps):              # skip a truncated file (disk full while writing)
            try:
                soup, _ = core.load_checkpoint(p)
                return e, soup
            except core.CheckpointError:
                continue
        raise ValueError(f"no readable checkpoint at or before epoch {epoch}")

    def soup_at(self, epoch):
        """
        The soup after `epoch` has been executed (epoch -1 = initial soup). Replays from the
        nearest checkpoint at or below `epoch`; consecutive requests within a window reuse
        the previous replay state. Checkpoint file 0 holds the initial soup, file e > 0 the
        soup after epoch e.
        """
        if epoch in self._soup_cache:
            return self._soup_cache[epoch]
        if epoch < 0:
            path = self.rd.checkpoint_path(0)
            soup = (core.load_checkpoint(path)[0] if os.path.exists(path)
                    else core.random_soup(self.num_programs, self.seed, core.parse_init_dist(self.meta.get('init_dist'))))
            return self._remember(epoch, soup)
        ck_epoch, _ = max(((e, p) for e, p in self.checkpoints() if e <= epoch), key=lambda x: x[0])
        if self._cursor is not None and ck_epoch <= self._cursor[0] <= epoch:
            cur_epoch, soup = self._cursor
            soup = soup.copy()
            first = cur_epoch + 1
        else:
            cur_epoch, soup = self.checkpoint_at_or_before(epoch)
            first = 0 if cur_epoch == 0 else cur_epoch + 1
        ops = np.empty(self.num_programs // 2, dtype=np.int64)
        # checkpoint file 0 is the initial soup (before epoch 0); file e > 0 is the soup after epoch e
        for e in range(first, epoch + 1):
            core.run_epoch(soup, self.perm(e), self.max_steps, self.mutation_at(e), e, ops, self.heads)
        self._cursor = (epoch, soup.copy())
        return self._remember(epoch, soup)

    def _remember(self, epoch, soup):
        if len(self._soup_cache) >= 12:
            self._soup_cache.pop(next(iter(self._soup_cache)))
        self._soup_cache[epoch] = soup
        return soup

    def tape_at(self, epoch, slot):
        """
        The tape that `slot` was part of in `epoch`: dict with the two slots in tape order,
        the 128-byte tape before execution, the tape after, and the keys of both halves.
        """
        a, b = self.pair_of(epoch, slot)
        before = self.soup_at(epoch - 1)
        after = self.soup_at(epoch)
        tape_before = np.concatenate([before[a], before[b]])
        tape_after = np.concatenate([after[a], after[b]])
        return {'epoch': epoch, 'slots': (a, b), 'position': 0 if slot == a else 1,
                'before': tape_before, 'after': tape_after,
                'keys_before': (core.program_key(before[a]), core.program_key(before[b])),
                'keys_after': (core.program_key(after[a]), core.program_key(after[b]))}

    def first_occurrence_exact(self, key_or_hash):
        """
        Locate the exact epoch a species first appeared by replaying: uses the birth record
        when available, otherwise scans checkpoints and replays the window. Returns
        (epoch, slot) or None.
        """
        row = self.species_row(key_or_hash)
        if row is not None:
            return row['first_epoch'], row['first_slot']
        h = self.resolve_hash(key_or_hash)
        prev_e = -1
        for e, p in self.checkpoints():
            soup, _ = core.load_checkpoint(p)
            hashes, _ = core.compute_keys(soup)
            if (hashes == h).any():
                for ee in range(prev_e + 1, e + 1):
                    hh, _ = core.compute_keys(self.soup_at(ee))
                    hit = np.flatnonzero(hh == h)
                    if hit.size:
                        return ee, int(hit[0])
            prev_e = e
        return None


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------
def fmt_tape(tape, mark=None):
    """Render a tape as instruction characters, '.' for other bytes."""
    return ''.join(chr(b) if b in core.COMMANDS else '·' for b in tape)


def render_tree(node, indent=0, role='species'):
    pad = '  ' * indent
    lines = []
    if not node.get('tracked', True) or node.get('note') and 'untracked' in node.get('note', ''):
        lines.append(f"{pad}{role}: {node.get('key') or '?'}   [{node.get('note', 'untracked')}]")
        return lines
    b = node.get('birth') or {}
    head = f"{pad}{role}: {node['key']}  (len {node['length']}, peak {node.get('peak_count', '?')}"
    if node.get('selfrep_score') is not None:
        head += f", selfrep {node['selfrep_score']}"
    head += ")"
    lines.append(head)
    if b.get('initial'):
        lines.append(f"{pad}  present in the initial soup (slot {b['slot']})")
        return lines
    if b:
        order = "first half" if b['slot_position'] == 0 else "second half"
        lines.append(f"{pad}  born epoch {b['epoch']} in slot {b['slot']} ({order} of tape), "
                     f"partner slot {b['partner']}")
        if 'primary' in node:
            d = lambda r: (f"{node[r + '_mirror_distance']} as mirror image" if node[r + '_mirror']
                           else str(node[r + '_distance']))
            lines.append(f"{pad}  edit distance to parent {d('parent')}, to partner {d('partner')}"
                         f"  -> primary ancestor: {node['primary']}{' (mirror copy)' if node['primary_mirror'] else ''}")
    if node.get('note'):
        lines.append(f"{pad}  [{node['note']}]")
    for role_name in ('parent', 'partner'):
        if role_name in node:
            lines += render_tree(node[role_name], indent + 1, role_name)
    return lines


def render_instance(node, indent=0, role='slot'):
    pad = '  ' * indent
    lines = []
    if 'hash' not in node:
        lines.append(f"{pad}{role} {node['slot']}: [{node['note']}]")
        return lines
    pos = {0: 'first half', 1: 'second half', None: ''}[node.get('position')]
    lines.append(f"{pad}{role} {node['slot']} @ epoch {node['epoch']}: {node.get('key') or '?'} "
                 f"{'(' + pos + ', partner ' + str(node['partner']) + ')' if node.get('partner') is not None else '(initial)'}")
    if node.get('note'):
        lines.append(f"{pad}  [{node['note']}]")
    if 'parent' in node:
        lines += render_instance(node['parent'], indent + 1, 'previous')
    if 'partner_node' in node:
        lines += render_instance(node['partner_node'], indent + 1, 'partner')
    return lines


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    raise TypeError(str(type(o)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="Query a BFF run directory")
    p.add_argument('--json', action='store_true', help='print JSON instead of text')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('search', help='find species by key pattern')
    s.add_argument('run'); s.add_argument('pattern')
    s.add_argument('--mode', choices=['substring', 'exact', 'regex', 'fuzzy'], default='substring')
    s.add_argument('--max-dist', type=int, default=2)
    s.add_argument('--limit', type=int, default=30)
    s.add_argument('--min-count', type=int, default=0)
    s.add_argument('--epoch', type=int, default=None, help='search the checkpoint at/below this epoch instead')

    s = sub.add_parser('species', help='birth event and history of one species')
    s.add_argument('run'); s.add_argument('key')

    s = sub.add_parser('lineage', help='ancestry tree of a species from the birth records')
    s.add_argument('run'); s.add_argument('key'); s.add_argument('--depth', type=int, default=8)

    s = sub.add_parser('slot', help='tracked history of one slot')
    s.add_argument('run'); s.add_argument('slot', type=int)
    s.add_argument('--before', type=int, default=None); s.add_argument('--after', type=int, default=None)
    s.add_argument('--trace', action='store_true', help='walk the slot ancestry back (instance level)')
    s.add_argument('--depth', type=int, default=6)

    s = sub.add_parser('top', help='most common species at a snapshot')
    s.add_argument('run'); s.add_argument('--epoch', type=int, default=None); s.add_argument('--n', type=int, default=20)

    s = sub.add_parser('tape', help='the tape a slot was part of in an epoch (before/after execution)')
    s.add_argument('run'); s.add_argument('--epoch', type=int, required=True); s.add_argument('--slot', type=int, required=True)

    s = sub.add_parser('info', help='run summary')
    s.add_argument('run')

    s = sub.add_parser('culls', help='origin-rate experiment: the removed replicators (origins) and their engines')
    s.add_argument('run')
    s.add_argument('--export', metavar='FILE.npy', default=None,
                   help='write the raw 64-byte programs of the origins to FILE.npy (one per origin, in order of '
                        'appearance; usable with --seed-programs); scores go to FILE.scores.npy')
    s.add_argument('--min-score', type=int, default=None, help='with --export: keep origins scoring at least this (default: all)')

    s = sub.add_parser('variants', help='split the non-replicating near-variants of the dominant replicator into hijackers, killers and debris')
    s.add_argument('run'); s.add_argument('--epoch', type=int, default=None, help='checkpoint at/below this epoch (default: latest)')

    a = p.parse_args(argv)
    run = Run(a.run)
    t0 = time.time()

    if a.cmd == 'culls':
        path = os.path.join(run.rd.path, 'culls.jsonl')
        if os.path.exists(path):
            with open(path) as f:
                events = [json.loads(line) for line in f if line.strip()]
        else:
            events = run.meta.get('culls', [])
        ev = [c for c in events if c.get('kind', 'replicator') == 'replicator']
        if not ev:
            print("no replicators were removed in this run (was it started with --cull-replicators?)"); return
        last = run.last_epoch()
        if a.export:
            keep = [c for c in ev if c.get('program') and (a.min_score is None or c['score'] >= a.min_score)]
            if not keep:
                print("no origin in this run has its program bytes recorded (runs before 2026-09-07 stored keys only)"); return
            progs = np.array([np.frombuffer(bytes.fromhex(c['program']), dtype=np.uint8) for c in keep], dtype=np.uint8)
            np.save(a.export, progs)
            np.save(a.export[:-4] + '.scores.npy' if a.export.endswith('.npy') else a.export + '.scores.npy',
                    np.array([c['score'] for c in keep], dtype=np.int32))
            print(f"wrote {len(keep)} programs ({len(ev) - len(keep)} origins skipped) to {a.export}")
            return
        # every removal is an origin (its lineage was swept with it); group the origins by engine, i.e. by
        # shared innermost copy loop or a few edits, to see which engines the soup finds and how often
        keys = [c['key'] for c in ev]
        parent = list(range(len(keys)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        for i in range(len(keys)):
            for j in range(i):
                if core.same_lineage(keys[i], keys[j]):
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(len(keys)):
            groups.setdefault(find(i), []).append(i)
        engines = sorted(groups.values(), key=lambda g: (-len(g), ev[g[0]]['epoch']))
        eps = np.array([c['epoch'] for c in ev])
        n_lin = sum(1 for c in events if c.get('kind') == 'lineage')
        if a.json:
            print(json.dumps({'origins': len(ev), 'last_epoch': last, 'lineage_species_removed': n_lin,
                              'engines': [{'origins': len(g), 'first_epoch': min(ev[i]['epoch'] for i in g),
                                           'loops': sorted(core.copy_loops(keys[g[0]])), 'keys': [keys[i] for i in g]} for g in engines]}, indent=1))
            return
        print(f"{len(ev)} origins over {last + 1} epochs ({n_lin} further species removed as their lineages); "
              f"first at {eps.min()}, {1000 * len(ev) / (last + 1):.2f} per 1000 epochs overall")
        if len(eps) > 1:
            gaps = np.diff(np.sort(eps))
            print(f"epochs between origins: median {np.median(gaps):.0f}, mean {gaps.mean():.0f}")
            step = max(1000, int(round((last + 1) / 10, -3)))
            print("origins per window: " + ", ".join(f"{lo}-{min(lo + step, last + 1)}: {int(((eps >= lo) & (eps < lo + step)).sum())}"
                                                   for lo in range(0, last + 1, step)))
        sig_groups = collections.Counter(next(iter(sorted(core.engine_signatures(k)))) if core.engine_signatures(k) else '(none)' for k in keys)
        print(f"{len(sig_groups)} distinct engine signatures (head moves | copy instructions of the loop): "
              + ", ".join(f"{sg} x{n}" for sg, n in sig_groups.most_common(8)) + (" ..." if len(sig_groups) > 8 else ""))
        print(f"{len(engines)} distinct engines (origins sharing a copy loop, a signature, or within 3 edits):")
        print(f"{'origins':>7} {'first':>7}  engine")
        for g in engines[:25]:
            i0 = min(g, key=lambda i: ev[i]['epoch'])
            print(f"{len(g):7d} {ev[i0]['epoch']:7d}  {' '.join(sorted(core.copy_loops(keys[i0]))) or '(no loop)'}   e.g. {keys[i0][:40]}")
        if len(engines) > 25:
            print(f"   ... and {len(engines) - 25} more engines")
        return
    if a.cmd == 'info':
        e = run.last_epoch()
        n_species = run.db.execute("SELECT COUNT(*) FROM species").fetchone()[0]
        n_changes = run.changes().shape[0]
        n_keys = run.db.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
        out = {'run': a.run, 'num_programs': run.num_programs, 'seed': run.seed, 'last_epoch': e,
               'checkpoints': len(run.checkpoints()), 'recorded_species': n_species,
               'snapshot_keys': n_keys, 'change_records': int(n_changes),
               'lineage_min_len': run.min_len, 'lineage_window': run.meta.get('lineage_window'),
               'lineage_stats': run.meta.get('lineage_stats'),
               'stop_triggered': run.meta.get('stop_triggered')}
        if a.json:
            print(json.dumps(out, indent=2, default=_json_default))
        else:
            for k, v in out.items():
                print(f"{k:>18}: {v}")

    elif a.cmd == 'search':
        res = run.search(a.pattern, a.mode, a.max_dist, a.limit, a.min_count, a.epoch)
        if a.json:
            print(json.dumps(res, indent=2, default=_json_default))
        elif a.epoch is not None:
            print(f"{len(res)} matches in checkpoint at epoch {res[0]['epoch'] if res else '?'}")
            print(f"{'Count':>7} {'Share':>7} {'Len':>4}  Key")
            for r in res:
                print(f"{r['count']:7d} {100 * r['share']:6.2f}% {r['length']:4d}  {r['key']}")
        else:
            print(f"{len(res)} matches")
            print(f"{'Born':>7} {'Peak':>7} {'@epoch':>7} {'Latest':>7} {'Len':>4} {'SelfRep':>7}  Key")
            for r in res:
                sr = '-' if r['selfrep_score'] is None else str(r['selfrep_score'])
                pe = '-' if r['peak_epoch'] is None else str(r['peak_epoch'])
                extra = f"  (dist {r['distance']})" if 'distance' in r else ''
                print(f"{r['first_epoch']:7d} {r['peak_count']:7d} {pe:>7} {r['latest_count']:7d} "
                      f"{r['length']:4d} {sr:>7}  {r['key']}{extra}")

    elif a.cmd == 'species':
        sp = run.species(a.key)
        if sp is None:
            print("not a tracked species (shorter than lineage-min-len, or never appeared)")
        elif a.json:
            print(json.dumps(sp, indent=2, default=_json_default))
        else:
            b = sp['birth']
            print(f"key:     {sp['key']}   (len {sp['length']}, hash {sp['hash']})")
            if b['initial']:
                print(f"origin:  initial soup, slot {b['slot']}")
            else:
                print(f"origin:  epoch {b['epoch']}, slot {b['slot']} ({'first' if b['slot_position'] == 0 else 'second'} half), "
                      f"partner slot {b['partner']}")
                print(f"         slot held:    {b['parent_key']}{'' if b['parent_tracked'] else '   (background)'}")
                print(f"         partner held: {b['partner_key']}{'' if b['partner_tracked'] else '   (background)'}")
            print(f"peak:    {sp['peak_count']} at epoch {sp['peak_epoch']}   latest {sp['latest_count']} "
                  f"(last seen {sp['last_seen_epoch']})")
            if sp['selfrep']:
                print("selfrep: " + ', '.join(f"e{e}:{s}" for e, s, c in sp['selfrep'][-8:]))
            if sp['counts']:
                print("counts:  " + ', '.join(f"e{e}:{c}" for e, c in sp['counts'][-12:]))

    elif a.cmd == 'lineage':
        tree = run.trace_species(a.key, a.depth)
        if a.json:
            print(json.dumps(tree, indent=2, default=_json_default))
        else:
            print('\n'.join(render_tree(tree)))

    elif a.cmd == 'slot':
        if a.trace:
            node = run.trace_instance(a.before if a.before is not None else run.last_epoch(), a.slot, a.depth)
            print(json.dumps(node, indent=2, default=_json_default) if a.json else '\n'.join(render_instance(node)))
        else:
            hist = run.slot_history(a.slot, a.before, a.after)
            if a.json:
                print(json.dumps(hist, indent=2, default=_json_default))
            else:
                print(f"{len(hist)} tracked changes of slot {a.slot}")
                for h in hist:
                    pos = {0: 'first', 1: 'second', None: '-'}[h['position']]
                    print(f"  epoch {h['epoch']:>7}  partner {str(h['partner']):>7} ({pos:>6})  {h['key'] or '?'}")

    elif a.cmd == 'top':
        epoch, rows = run.top_species(a.epoch, a.n)
        if a.json:
            print(json.dumps({'epoch': epoch, 'species': rows}, indent=2, default=_json_default))
        else:
            print(f"Top {len(rows)} species at epoch {epoch}")
            print(f"{'Count':>7} {'Share':>7} {'Len':>4} {'Born':>7} {'SelfRep':>7}  Key")
            for r in rows:
                sr = '-' if r['selfrep_score'] is None else str(r['selfrep_score'])
                print(f"{r['count']:7d} {100 * r['share']:6.2f}% {str(r['length'] if r['length'] is not None else '?'):>4} "
                      f"{str(r['first_epoch'] if r['first_epoch'] is not None else '-'):>7} {sr:>7}  "
                      f"{r['key'] if r['key'] is not None else '(untracked short key)'}")

    elif a.cmd == 'variants':
        ck_epoch, soup = run.checkpoint_at_or_before(a.epoch if a.epoch is not None else run.last_epoch())
        res = core.classify_variants(soup, run.max_steps, run.heads)
        if a.json:
            print(json.dumps({'epoch': ck_epoch, **(res or {})}, indent=2, default=_json_default))
        elif res is None:
            print(f"checkpoint {ck_epoch}: no self-replicator among the 512 most common species")
        else:
            print(f"checkpoint {ck_epoch}: dominant host {res['host_key']} ; replicators hold {100 * res['host_share']:.1f}% of the soup")
            for kind, label in (('hijacker', 'hijackers (copied over a host they precede: parasites)'),
                                ('killer', 'killers (destroy such a host, not copied)'), ('debris', 'debris (the host survives)')):
                print(f"  {100 * res['shares'][kind]:5.1f}%  {res['species'][kind]:3d} species  {label}")
                for k, cnt in res['examples'][kind][:3]:
                    print(f"          {cnt:6d}  {k}")

    elif a.cmd == 'tape':
        t = run.tape_at(a.epoch, a.slot)
        if a.json:
            print(json.dumps(t, indent=2, default=_json_default))
        else:
            a_, b_ = t['slots']
            print(f"epoch {t['epoch']}: tape = slot {a_} (first half) + slot {b_} (second half)")
            print(f"before: {fmt_tape(t['before'][:64])} | {fmt_tape(t['before'][64:])}")
            print(f"after:  {fmt_tape(t['after'][:64])} | {fmt_tape(t['after'][64:])}")
            print(f"keys before: {t['keys_before'][0]!r} + {t['keys_before'][1]!r}")
            print(f"keys after:  {t['keys_after'][0]!r} + {t['keys_after'][1]!r}")

    if not a.json:
        print(f"[{time.time() - t0:.2f}s]", file=sys.stderr)


if __name__ == '__main__':
    main()
