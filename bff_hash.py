#!/usr/bin/env python3
"""
Vectorised open-addressing hash map for uint64 keys -> int64 values (Numba).

Used by the lineage recorder for the set of recorded species and the map of
pending births, where tens of thousands of keys are inserted, looked up and
deleted every epoch. All operations take arrays; deletion uses tombstones and
the table rebuilds itself when it gets too full.
"""

import numpy as np
from numba import njit

EMPTY = 0
LIVE = 1
DEAD = 2
_MULT = np.uint64(0x9E3779B97F4A7C15)


@njit(cache=True)
def _slot(key, mask):
    return int((key * _MULT) >> np.uint64(24)) & mask


@njit(cache=True)
def _insert(keys, vals, tk, tv, ts):
    """Insert or overwrite. Returns the number of newly occupied slots."""
    mask = tk.shape[0] - 1
    new = 0
    for n in range(keys.shape[0]):
        k = keys[n]
        s = _slot(k, mask)
        first_dead = -1
        while True:
            st = ts[s]
            if st == EMPTY:
                if first_dead >= 0:
                    s = first_dead
                tk[s] = k
                tv[s] = vals[n]
                ts[s] = LIVE
                new += 1
                break
            if st == LIVE and tk[s] == k:
                tv[s] = vals[n]
                break
            if st == DEAD and first_dead < 0:
                first_dead = s
            s = (s + 1) & mask
    return new


@njit(cache=True)
def _get(keys, tk, tv, ts, out):
    """out[n] = value of keys[n], or -1 when absent."""
    mask = tk.shape[0] - 1
    for n in range(keys.shape[0]):
        k = keys[n]
        s = _slot(k, mask)
        out[n] = -1
        while True:
            st = ts[s]
            if st == EMPTY:
                break
            if st == LIVE and tk[s] == k:
                out[n] = tv[s]
                break
            s = (s + 1) & mask


@njit(cache=True)
def _delete(keys, expected, check, tk, tv, ts):
    """Delete keys (only where value == expected[n] when check). Returns deletions."""
    mask = tk.shape[0] - 1
    removed = 0
    for n in range(keys.shape[0]):
        k = keys[n]
        s = _slot(k, mask)
        while True:
            st = ts[s]
            if st == EMPTY:
                break
            if st == LIVE and tk[s] == k:
                if (not check) or tv[s] == expected[n]:
                    ts[s] = DEAD
                    removed += 1
                break
            s = (s + 1) & mask
    return removed


@njit(cache=True)
def _live_entries(tk, tv, ts, out_k, out_v):
    n = 0
    for s in range(tk.shape[0]):
        if ts[s] == LIVE:
            out_k[n] = tk[s]
            out_v[n] = tv[s]
            n += 1
    return n


class HashMap:
    """uint64 -> int64 map with array-valued operations."""

    def __init__(self, capacity=1 << 16, keys=None, vals=None):
        cap = 1 << 10
        while cap < capacity:
            cap <<= 1
        self._alloc(cap)
        if keys is not None:
            self.insert(keys, vals if vals is not None else np.ones(len(keys), dtype=np.int64))

    def _alloc(self, cap):
        self.tk = np.empty(cap, dtype=np.uint64)
        self.tv = np.empty(cap, dtype=np.int64)
        self.ts = np.zeros(cap, dtype=np.uint8)
        self.size = 0          # live entries
        self.used = 0          # live + tombstones

    def __len__(self):
        return int(self.size)

    def capacity(self):
        return int(self.tk.shape[0])

    def _rebuild(self, incoming):
        """Rebuild sized from the LIVE entries (tombstones are dropped): capacity stays
        proportional to what is actually stored, however many deletions happen."""
        k, v = self.items()
        cap = 1 << 10
        while cap * 6 < (self.size + incoming) * 10 * 2:      # load factor <= 0.3 after the rebuild
            cap <<= 1
        self._alloc(cap)
        if k.size:
            self.used += _insert(k, v, self.tk, self.tv, self.ts)
            self.size = self.used

    def insert(self, keys, vals):
        keys = np.ascontiguousarray(keys, dtype=np.uint64)
        vals = np.ascontiguousarray(vals, dtype=np.int64)
        if keys.size == 0:
            return 0
        if (self.used + keys.size) * 10 > self.tk.shape[0] * 6:      # live + tombstones above 0.6: rebuild
            self._rebuild(keys.size)
        new = _insert(keys, vals, self.tk, self.tv, self.ts)
        self.used += new
        self.size += new
        return new

    def get(self, keys):
        keys = np.ascontiguousarray(keys, dtype=np.uint64)
        out = np.empty(keys.size, dtype=np.int64)
        if keys.size:
            _get(keys, self.tk, self.tv, self.ts, out)
        return out

    def contains(self, keys):
        return self.get(keys) >= 0

    def __contains__(self, key):
        return bool(self.get(np.array([key], dtype=np.uint64))[0] >= 0)

    def delete(self, keys, expected=None):
        keys = np.ascontiguousarray(keys, dtype=np.uint64)
        if keys.size == 0:
            return 0
        if expected is None:
            expected = np.zeros(keys.size, dtype=np.int64)
            check = False
        else:
            expected = np.ascontiguousarray(expected, dtype=np.int64)
            check = True
        removed = _delete(keys, expected, check, self.tk, self.tv, self.ts)
        self.size -= removed
        return removed

    def items(self):
        k = np.empty(self.size, dtype=np.uint64)
        v = np.empty(self.size, dtype=np.int64)
        n = _live_entries(self.tk, self.tv, self.ts, k, v) if self.size else 0
        return k[:n], v[:n]

    def keys(self):
        return self.items()[0]
