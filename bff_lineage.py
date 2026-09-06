#!/usr/bin/env python3
"""
Lineage recording for BFF soup runs.

A run directory holds:
  meta.json          run parameters and resume history
  log.csv            per-epoch metrics
  checkpoints/       full soup snapshots (every --checkpoint-interval epochs)
  changes.bin        change records (epoch, slot, partner, new key hash) for slots that
                     changed to a *recorded* species (see below), plus every slot at epoch 0
  species.db         SQLite: recorded species with their birth events, key texts of
                     snapshot species, periodic species counts, self-replication scores
  pending.npz        the writer's rolling window, saved on exit so a resume from the
                     final checkpoint continues exactly

Which species get recorded
--------------------------
Programs are identified by their key (instruction bytes only). Slots are stable
identities, so the change records of one slot form its line of descent.

Pre-transition soups produce thousands of never-repeated keys per epoch. Persisting
all of them is neither affordable nor useful, so births are held in a rolling window
of `window` epochs and a species is *promoted* into the database only when it is
observed in `promote_count` or more slots at once (it was copied repeatedly) or when
it is an ancestor of a promoted species (up to `cascade_depth` generations and
`cascade_max` ancestors per promotion, nearest first, so ancestry chains stay complete
without dragging in the whole background). Births that leave the window unpromoted are
dropped. Memory: the window holds about candidates_per_epoch x window x 224 bytes. Keys shorter than `min_len` instructions are the random
background and never enter the window, but their text is stored wherever they are
the parent of a recorded species. Change records are buffered the same way and only
kept for recorded species.
"""

import json
import os
import sqlite3
from collections import deque

import numpy as np

from bff_core import to_signed, to_unsigned, program_key, TAPE_SIZE

CHANGE_DTYPE = np.dtype([('epoch', '<u4'), ('slot', '<u4'), ('partner', '<u4'), ('hash', '<u8')])
PENDING_DTYPE = np.dtype([('hash', '<u8'), ('epoch', '<u4'), ('slot', '<u4'), ('partner', '<u4'),
                          ('parent_hash', '<u8'), ('partner_hash', '<u8'),
                          ('child', np.uint8, TAPE_SIZE), ('parent', np.uint8, TAPE_SIZE),
                          ('partner_prog', np.uint8, TAPE_SIZE)])
NO_PARTNER = 0xFFFFFFFF
DEFAULT_MIN_LEN = 8
DEFAULT_WINDOW = 128
DEFAULT_PROMOTE_COUNT = 6
DEFAULT_CASCADE_DEPTH = 12
DEFAULT_CASCADE_MAX = 24
DEFAULT_BUDGET_MB = 2048
COUNTS_MAX_SPECIES = 4096

SCHEMA = """
CREATE TABLE IF NOT EXISTS species (
    hash           INTEGER PRIMARY KEY,  -- signed view of the uint64 FNV-1a key hash
    key            TEXT    NOT NULL,     -- instruction-only program string
    length         INTEGER NOT NULL,
    first_epoch    INTEGER NOT NULL,
    first_slot     INTEGER NOT NULL,
    first_partner  INTEGER,              -- NULL for the initial soup
    parent_hash    INTEGER,              -- key hash of first_slot before the execution
    partner_hash   INTEGER,              -- key hash of first_partner before the execution
    parent_key     TEXT,                 -- texts of both tape halves before the execution
    partner_key    TEXT,
    promoted_epoch INTEGER               -- epoch the species was recorded (seen twice, or as a parent)
);
CREATE INDEX IF NOT EXISTS species_first_epoch ON species(first_epoch);

CREATE TABLE IF NOT EXISTS keys (          -- key text for snapshot species that have no species row
    hash   INTEGER PRIMARY KEY,
    key    TEXT NOT NULL,
    length INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS species_counts (
    epoch INTEGER NOT NULL,
    hash  INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (epoch, hash)
);
CREATE INDEX IF NOT EXISTS species_counts_hash ON species_counts(hash);

CREATE TABLE IF NOT EXISTS selfrep (
    epoch INTEGER NOT NULL,
    hash  INTEGER NOT NULL,
    score INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (epoch, hash)
);
"""


class RunDir:
    """Paths and metadata of a run directory."""

    def __init__(self, path):
        self.path = path
        self.meta_path = os.path.join(path, 'meta.json')
        self.log_path = os.path.join(path, 'log.csv')
        self.checkpoint_dir = os.path.join(path, 'checkpoints')
        self.changes_path = os.path.join(path, 'changes.bin')
        self.db_path = os.path.join(path, 'species.db')
        self.pending_path = os.path.join(path, 'pending.npz')

    def create(self):
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def exists(self):
        return os.path.exists(self.meta_path)

    def read_meta(self):
        with open(self.meta_path) as f:
            return json.load(f)

    def write_meta(self, meta):
        with open(self.meta_path, 'w') as f:
            json.dump(meta, f, indent=2)

    def checkpoint_path(self, epoch):
        return os.path.join(self.checkpoint_dir, f"{epoch:010d}.dat")

    def checkpoints(self):
        """Sorted list of (epoch, path)."""
        out = []
        if os.path.isdir(self.checkpoint_dir):
            for name in os.listdir(self.checkpoint_dir):
                if name.endswith('.dat'):
                    out.append((int(name[:-4]), os.path.join(self.checkpoint_dir, name)))
        return sorted(out)


class HashSet:
    """
    Membership set for uint64 hashes: a sorted base array (binary search) plus a
    Python set of recent additions, merged into the base once it grows large.
    """

    def __init__(self, initial=None, merge_threshold=1 << 18):
        self.base = np.unique(np.asarray(initial if initial is not None else [], dtype=np.uint64))
        self.recent = set()
        self.merge_threshold = merge_threshold

    def __len__(self):
        return int(self.base.size + len(self.recent))

    def __contains__(self, h):
        h = int(h)
        if h in self.recent:
            return True
        i = int(np.searchsorted(self.base, np.uint64(h)))
        return i < self.base.size and int(self.base[i]) == h

    def contains_mask(self, hashes):
        hashes = np.asarray(hashes, dtype=np.uint64)
        if hashes.size == 0:
            return np.zeros(0, dtype=bool)
        idx = np.searchsorted(self.base, hashes)
        idx_c = np.minimum(idx, max(self.base.size - 1, 0))
        mask = (idx < self.base.size) & (self.base[idx_c] == hashes) if self.base.size else np.zeros(hashes.size, bool)
        if self.recent:
            rec = self.recent
            mask |= np.fromiter((int(h) in rec for h in hashes.tolist()), dtype=bool, count=hashes.size)
        return mask

    def add(self, h):
        self.recent.add(int(h))
        if len(self.recent) >= self.merge_threshold:
            self._merge()

    def _merge(self):
        if self.recent:
            self.base = np.union1d(self.base, np.fromiter(self.recent, dtype=np.uint64, count=len(self.recent)))
            self.recent = set()


class LineageWriter:
    """Records change records and species births while a simulation runs."""

    def __init__(self, run_dir, min_len=DEFAULT_MIN_LEN, window=DEFAULT_WINDOW,
                 budget_mb=DEFAULT_BUDGET_MB, promote_count=DEFAULT_PROMOTE_COUNT,
                 cascade_depth=DEFAULT_CASCADE_DEPTH, cascade_max=DEFAULT_CASCADE_MAX, resume_epoch=None):
        """
        resume_epoch: when resuming from a checkpoint at that epoch, data recorded for
        later epochs is discarded; the rolling window is restored if pending.npz was
        saved at exactly that epoch, otherwise it starts empty (window_reset=True).
        """
        self.run_dir = run_dir
        self.min_len = int(min_len)
        self.window = int(window)
        self.promote_count = int(promote_count)
        self.cascade_depth = int(cascade_depth)
        self.cascade_max = int(cascade_max)
        self.budget_bytes = int(budget_mb * 1024 * 1024)
        self.db = sqlite3.connect(run_dir.db_path)
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA cache_size=-262144")   # 256 MB
        if resume_epoch is not None:
            self._truncate_after(resume_epoch)
        self.promoted = HashSet(np.fromiter((to_unsigned(h) for (h,) in self.db.execute("SELECT hash FROM species")),
                                            dtype=np.uint64))
        self.known_text = set(to_unsigned(h) for (h,) in self.db.execute("SELECT hash FROM keys"))
        # rolling window
        self.pending = {}                 # hash -> (epoch, row index)
        self.pending_epochs = deque()     # (epoch, PENDING_DTYPE array)
        self.change_buffer = deque()      # (epoch, CHANGE_DTYPE array)
        self.window_reset = False
        if resume_epoch is not None:
            self.window_reset = not self._load_pending(resume_epoch)
            if not self.window_reset:
                # the buffered records were flushed early at exit; they are re-evaluated at expiry
                self._truncate_changes(resume_epoch - self.window)
        self.changes = open(run_dir.changes_path, 'ab')
        self.changes_bytes = os.path.getsize(run_dir.changes_path)
        self.changes_exhausted_epoch = None
        self._pending_commit = 0
        self.stats = {'candidates': 0, 'promoted': 0}

    # -- truncation / restore on resume ------------------------------------------
    def _truncate_after(self, epoch):
        self.db.execute("DELETE FROM species WHERE first_epoch > ? OR promoted_epoch > ?", (epoch, epoch))
        self.db.execute("DELETE FROM species_counts WHERE epoch > ?", (epoch,))
        self.db.execute("DELETE FROM selfrep WHERE epoch > ?", (epoch,))
        self.db.commit()
        self._truncate_changes(epoch)

    def _truncate_changes(self, epoch):
        if os.path.exists(self.run_dir.changes_path):
            recs = np.fromfile(self.run_dir.changes_path, dtype=CHANGE_DTYPE)
            keep = recs[recs['epoch'] <= epoch]
            keep.tofile(self.run_dir.changes_path)

    def _load_pending(self, epoch):
        path = self.run_dir.pending_path
        if not os.path.exists(path):
            return False
        with np.load(path) as z:
            if int(z['epoch']) != epoch:
                return False
            pend = z['pending']
            chg = z['changes']
        pend = pend[~self.promoted.contains_mask(pend['hash'])]
        for e in np.unique(pend['epoch']).tolist():
            arr = pend[pend['epoch'] == e]
            self.pending_epochs.append((e, arr))
            for i, h in enumerate(arr['hash'].tolist()):
                self.pending[h] = (e, i)
        for e in np.unique(chg['epoch']).tolist():
            self.change_buffer.append((e, chg[chg['epoch'] == e]))
        return True

    def save_pending(self, epoch):
        parts = []
        for e, arr in self.pending_epochs:   # keep only births still pending (not promoted/superseded)
            live = np.fromiter((self.pending.get(h) == (e, i) for i, h in enumerate(arr['hash'].tolist())),
                               dtype=bool, count=arr.shape[0])
            parts.append(arr[live])
        pend = np.concatenate(parts) if parts else np.empty(0, dtype=PENDING_DTYPE)
        chg = (np.concatenate([a for _, a in self.change_buffer]) if self.change_buffer
               else np.empty(0, dtype=CHANGE_DTYPE))
        np.savez(self.run_dir.pending_path, epoch=np.int64(epoch), pending=pend, changes=chg)

    # -- change records ------------------------------------------------------------
    def _write_changes(self, recs):
        if recs.size == 0 or self.changes_exhausted_epoch is not None:
            return
        if self.changes_bytes + recs.nbytes > self.budget_bytes:
            self.changes_exhausted_epoch = int(recs['epoch'][0])
            return
        recs.tofile(self.changes)
        self.changes_bytes += recs.nbytes

    def _flush_changes_for(self, epoch, recs):
        """Write the buffered records of one epoch that refer to recorded species."""
        keep = self.promoted.contains_mask(recs['hash'])
        self._write_changes(recs[keep])

    # -- recording -------------------------------------------------------------------
    def record_initial(self, soup, hashes, lengths, counts_by_hash=None):
        """Register the initial soup (epoch 0, before any execution)."""
        n = soup.shape[0]
        recs = np.empty(n, dtype=CHANGE_DTYPE)
        recs['epoch'] = 0
        recs['slot'] = np.arange(n, dtype=np.uint32)
        recs['partner'] = NO_PARTNER
        recs['hash'] = hashes
        self._write_changes(recs)

        slots = np.flatnonzero(lengths >= self.min_len)
        if slots.size:
            uniq, first, counts = np.unique(hashes[slots], return_index=True, return_counts=True)
            arr = np.zeros(uniq.size, dtype=PENDING_DTYPE)
            arr['hash'] = uniq
            arr['epoch'] = 0
            arr['slot'] = slots[first]
            arr['partner'] = NO_PARTNER
            arr['child'] = soup[slots[first]]
            self.pending_epochs.append((0, arr))
            for i, h in enumerate(uniq.tolist()):
                self.pending[h] = (0, i)
            for h in uniq[counts >= self.promote_count].tolist():
                self._promote(h, 0)
        self.db.commit()

    def record_epoch(self, epoch, soup, prev_soup, prev_hashes, prev_lengths, hashes, lengths,
                     changed, partner, uniq, first_idx, counts):
        """
        soup / prev_soup: the soup after and before this epoch's execution.
        changed: slots whose key hash changed. partner[slot]: the epoch's pairing.
        uniq/first_idx/counts: np.unique(hashes, return_index=True, return_counts=True).
        Returns (candidate_births, promotions).
        """
        n_new = 0
        n_prom = 0
        if changed.size:
            sig = changed[lengths[changed] >= self.min_len]
            if sig.size:
                recs = np.empty(sig.size, dtype=CHANGE_DTYPE)
                recs['epoch'] = epoch
                recs['slot'] = sig
                recs['partner'] = partner[sig]
                recs['hash'] = hashes[sig]
                self.change_buffer.append((epoch, recs))

                # candidate births: hashes neither recorded nor already pending
                u, first = np.unique(hashes[sig], return_index=True)
                known = self.promoted.contains_mask(u)
                if self.pending:
                    pend = self.pending
                    known |= np.fromiter((int(h) in pend for h in u.tolist()), dtype=bool, count=u.size)
                new = np.flatnonzero(~known)
                if new.size:
                    slots = sig[first[new]]
                    arr = np.empty(new.size, dtype=PENDING_DTYPE)
                    arr['hash'] = u[new]
                    arr['epoch'] = epoch
                    arr['slot'] = slots
                    arr['partner'] = partner[slots]
                    arr['parent_hash'] = prev_hashes[slots]
                    arr['partner_hash'] = prev_hashes[partner[slots]]
                    arr['child'] = soup[slots]
                    arr['parent'] = prev_soup[slots]
                    arr['partner_prog'] = prev_soup[partner[slots]]
                    self.pending_epochs.append((epoch, arr))
                    for i, h in enumerate(u[new].tolist()):
                        self.pending[h] = (epoch, i)
                    n_new = int(new.size)

        # promotions: pending species now present in two or more slots
        if self.pending:
            multi = uniq[(counts >= self.promote_count) & (lengths[first_idx] >= self.min_len)]
            for h in multi.tolist():
                if h in self.pending:
                    n_prom += self._promote(h, epoch)

        # expiry
        cutoff = epoch - self.window
        while self.pending_epochs and self.pending_epochs[0][0] <= cutoff:
            e, arr = self.pending_epochs.popleft()
            for i, h in enumerate(arr['hash'].tolist()):
                if self.pending.get(h) == (e, i):
                    del self.pending[h]
        while self.change_buffer and self.change_buffer[0][0] <= cutoff:
            e, recs = self.change_buffer.popleft()
            self._flush_changes_for(e, recs)

        self.stats['candidates'] += n_new
        self.stats['promoted'] += n_prom
        self._maybe_commit()
        return n_new, n_prom

    def _pending_record(self, h):
        loc = self.pending.pop(h, None)
        if loc is None:
            return None
        e, i = loc
        for pe, arr in self.pending_epochs:
            if pe == e:
                return arr[i]
        return None

    def _promote(self, h, epoch):
        """
        Insert a pending birth into the database, then its pending ancestors breadth-first
        (nearest generations first) up to cascade_depth generations / cascade_max rows.
        """
        queue = deque([(h, 0)])
        n = 0
        while queue and n < 1 + self.cascade_max:
            hh, depth = queue.popleft()
            rec = self._pending_record(hh)
            if rec is None:
                continue
            key = program_key(rec['child'])
            if rec['partner'] == NO_PARTNER:
                row = (to_signed(hh), key, len(key), 0, int(rec['slot']), None, None, None, None, None, epoch)
            else:
                row = (to_signed(hh), key, len(key), int(rec['epoch']), int(rec['slot']), int(rec['partner']),
                       to_signed(rec['parent_hash']), to_signed(rec['partner_hash']),
                       program_key(rec['parent']), program_key(rec['partner_prog']), epoch)
            self.db.execute("INSERT OR IGNORE INTO species VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            self.promoted.add(hh)
            n += 1
            if rec['partner'] != NO_PARTNER and depth < self.cascade_depth:
                for ph in (int(rec['parent_hash']), int(rec['partner_hash'])):
                    if ph in self.pending:
                        queue.append((ph, depth + 1))
        return n

    def record_counts(self, epoch, uniq_hashes, counts, soup=None, first_idx=None,
                      min_count=2, max_species=COUNTS_MAX_SPECIES):
        """Store species counts for this epoch (count >= min_count, top max_species) and key texts."""
        order = np.argsort(-counts, kind='stable')
        order = order[counts[order] >= min_count][:max_species]
        if order.size == 0:
            order = np.array([int(np.argmax(counts))])
        sel = uniq_hashes[order]
        rows = [(epoch, to_signed(h), int(c)) for h, c in zip(sel.tolist(), counts[order].tolist())]
        self.db.executemany("INSERT OR REPLACE INTO species_counts VALUES (?,?,?)", rows)
        if soup is not None and first_idx is not None:
            known = self.promoted.contains_mask(sel)
            krows = []
            for j in np.flatnonzero(~known).tolist():
                h = int(sel[j])
                if h in self.known_text:
                    continue
                key = program_key(soup[first_idx[order[j]]])
                krows.append((to_signed(h), key, len(key)))
                self.known_text.add(h)
            if krows:
                self.db.executemany("INSERT OR IGNORE INTO keys VALUES (?,?,?)", krows)

    def record_selfrep(self, epoch, hashes, scores, counts):
        rows = [(epoch, to_signed(h), int(s), int(c)) for h, s, c in
                zip(hashes.tolist(), scores.tolist(), counts.tolist())]
        self.db.executemany("INSERT OR REPLACE INTO selfrep VALUES (?,?,?,?)", rows)

    def _maybe_commit(self):
        self._pending_commit += 1
        if self._pending_commit >= 16:
            self.commit()

    def commit(self):
        self.changes.flush()
        self.db.commit()
        self._pending_commit = 0

    def close(self, epoch=None):
        """Save the window, flush buffered change records for recorded species, close."""
        if epoch is not None:
            self.save_pending(epoch)          # before the flush: the buffer is restored on resume
        while self.change_buffer:
            e, recs = self.change_buffer.popleft()
            self._flush_changes_for(e, recs)
        self.commit()
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        self.changes.close()
        self.db.close()


# ---------------------------------------------------------------------------
# Read side helpers (used by the analysis tools)
# ---------------------------------------------------------------------------
def open_db(run_dir):
    db = sqlite3.connect(run_dir.db_path if isinstance(run_dir, RunDir) else run_dir)
    db.row_factory = sqlite3.Row
    return db


def load_changes(run_dir):
    """All change records as a structured numpy array (memory-mapped)."""
    path = run_dir.changes_path if isinstance(run_dir, RunDir) else run_dir
    return np.memmap(path, dtype=CHANGE_DTYPE, mode='r')


def truncate_log(log_path, epoch):
    """Drop log rows after `epoch` (used when resuming)."""
    if not os.path.exists(log_path):
        return
    with open(log_path) as f:
        lines = f.readlines()
    if not lines:
        return
    header, body = lines[0], lines[1:]
    kept = [ln for ln in body if ln.strip() and int(ln.split(',', 1)[0]) <= epoch]
    with open(log_path, 'w') as f:
        f.write(header)
        f.writelines(kept)
