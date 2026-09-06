#!/usr/bin/env python3
"""
Core BFF machinery shared by the simulator and the analysis tools.

Contents:
  * the BFF interpreter and the parallel epoch kernel (Numba)
  * program "keys" (instruction-only strings) and 64-bit key hashes
  * the cubff-style self-replication test
  * checkpoint save/load (format v2, with v1 read support)
  * compression-based complexity metrics

Requires: numpy, numba (brotli optional but recommended)
"""

import json
import os
import re

import numpy as np
from numba import njit, prange  # noqa: F401

TAPE_SIZE = 64
COMBINED_SIZE = 2 * TAPE_SIZE
DEFAULT_MAX_STEPS = 32768        # step budget per tape evaluation (paper/cubff use 8192)
SELFREP_THRESHOLD = 20           # stable bytes (of 64) for a species to count as a self-replicator; cubff used 5,
                                 # the 2026 BFF paper 48 and reports no change above about 20
SELFREP_STRICT = 48              # the paper's threshold, logged and archived alongside
CHECKPOINT_MAGIC_V1 = b'BFFS'


class CheckpointError(ValueError):
    """A checkpoint file is unreadable (truncated or not a checkpoint)."""
CHECKPOINT_MAGIC_V2 = b'BFF2'

# ---------------------------------------------------------------------------
# Instruction set
# ---------------------------------------------------------------------------
LOOP_START = 91      # [
LOOP_END = 93        # ]
PLUS = 43            # +
MINUS = 45           # -
COPY_TO_HEAD1 = 46   # .   tape[head1] = tape[head0]
COPY_TO_HEAD0 = 44   # ,   tape[head0] = tape[head1]
DEC_HEAD0 = 60       # <
INC_HEAD0 = 62       # >
DEC_HEAD1 = 123      # {
INC_HEAD1 = 125      # }

COMMAND_BYTES = (LOOP_START, LOOP_END, PLUS, MINUS, COPY_TO_HEAD1, COPY_TO_HEAD0,
                 DEC_HEAD0, INC_HEAD0, DEC_HEAD1, INC_HEAD1)
COMMANDS = frozenset(COMMAND_BYTES)
IS_CMD = np.zeros(256, dtype=np.bool_)
IS_CMD[list(COMMAND_BYTES)] = True
OP_CHARS = '<>{}+-.,[]'

# Byte distributions for the initial soup ("codon tables"). A spec is a whitespace- or
# semicolon-separated list of key:weight pairs; a key is an instruction character, a byte value
# 0..255 (a data byte with a meaning: 0 ends a copy loop, 64 puts a head on the partner), or
# 'rest', whose weight is spread evenly over every value not named. Weights are relative.
INIT_PRESETS = {
    'uniform': None,                                                   # every byte value equally likely
    'ops50': ' '.join(f'{c}:5' for c in OP_CHARS) + ' rest:50',        # half instructions (the BFF follow-up paper)
    'ops100': ' '.join(f'{c}:10' for c in OP_CHARS),                   # instructions only, no data bytes
    # instructions at the frequencies of the winners in the results collection (half the bytes),
    # a stop byte, an alignment byte, and random data for the rest
    'winners': '[:12.6 ,:9.1 <:6.7 ]:6.1 }:5.1 .:3.3 {:3.2 >:2.7 +:0.9 -:0.6 0:5 64:3 rest:42',
}


def parse_init_dist(spec):
    """
    Probability over the 256 byte values for the initial soup, or None for uniform.
    `spec` is a preset name from INIT_PRESETS or a key:weight list (see above).
    """
    if spec is None or spec == 'uniform':
        return None
    spec = INIT_PRESETS.get(spec, spec)
    weights = np.zeros(256, dtype=np.float64)
    named = np.zeros(256, dtype=np.bool_)
    rest = 0.0
    for token in re.split(r'[\s;]+', spec.strip()):
        if not token:
            continue
        key, sep, w = token.rpartition(':')
        if not sep:
            raise ValueError(f"init distribution: expected key:weight, got {token!r}")
        w = float(w)
        if w < 0:
            raise ValueError(f"init distribution: negative weight in {token!r}")
        if key == 'rest':
            rest += w
            continue
        if len(key) == 1 and key in OP_CHARS:
            b = ord(key)
        else:
            b = int(key)
            if not 0 <= b <= 255:
                raise ValueError(f"init distribution: byte value out of range in {token!r}")
        weights[b] += w
        named[b] = True
    if rest > 0 and not named.all():
        weights[~named] += rest / (~named).sum()
    if weights.sum() <= 0:
        raise ValueError("init distribution: all weights are zero")
    return weights / weights.sum()


def init_dist_label(spec):
    """Short protocol tag for an initial distribution: '' for uniform, else '-init-<preset|custom>'."""
    if spec is None or spec == 'uniform':
        return ''
    return f"-init-{spec}" if spec in INIT_PRESETS else '-init-custom'

FNV_OFFSET = np.uint64(0xcbf29ce484222325)
FNV_PRIME = np.uint64(0x100000001b3)
EMPTY_KEY_HASH = int(FNV_OFFSET)


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------
@njit(cache=True)
def evaluate(tape, max_steps, heads_init=False):
    """
    Execute BFF on a 128-byte tape in place. Both heads and the program
    counter start at 0; heads wrap modulo 128. With heads_init (the paper's
    'bff' variant, as opposed to 'bff_noheads'), the first two tape bytes set
    the initial positions of head0 and head1 and execution starts at byte 2. Execution stops when the
    program counter leaves the tape, a bracket is unmatched, the step budget
    is spent, or the program is provably stuck: once the tape has stopped
    changing, the machine state is just (pc, head0, head1), and if that state
    recurs the program loops forever without ever writing again, so the tape
    is already final (Brent's cycle detection; the outcome is identical to
    running out the budget, only fewer instructions are counted).
    Returns the number of instructions executed.
    """
    head0 = 0
    head1 = 0
    pc = 0
    if heads_init:
        head0 = tape[0] & (COMBINED_SIZE - 1)
        head1 = tape[1] & (COMBINED_SIZE - 1)
        pc = 2
    ops = 0
    # cycle detection over (pc, head0, head1) since the last change of the tape
    tortoise = -1
    power = 1
    lam = 0

    for _ in range(max_steps):
        if pc < 0 or pc >= COMBINED_SIZE:
            break

        head0 = head0 & (COMBINED_SIZE - 1)
        head1 = head1 & (COMBINED_SIZE - 1)

        state = (pc << 14) | (head0 << 7) | head1
        if state == tortoise:
            break                       # same state, same tape: infinite loop, nothing can change
        if lam == power:
            tortoise = state
            power <<= 1
            lam = 0
        lam += 1

        cmd = tape[pc]

        if cmd == DEC_HEAD0:
            head0 -= 1
            ops += 1
        elif cmd == INC_HEAD0:
            head0 += 1
            ops += 1
        elif cmd == DEC_HEAD1:
            head1 -= 1
            ops += 1
        elif cmd == INC_HEAD1:
            head1 += 1
            ops += 1
        elif cmd == PLUS:
            tape[head0] = (tape[head0] + 1) & 0xFF
            ops += 1
            tortoise = -1
            power = 1
            lam = 0
        elif cmd == MINUS:
            tape[head0] = (tape[head0] - 1) & 0xFF
            ops += 1
            tortoise = -1
            power = 1
            lam = 0
        elif cmd == COPY_TO_HEAD1:
            ops += 1
            if tape[head1] != tape[head0]:
                tape[head1] = tape[head0]
                tortoise = -1
                power = 1
                lam = 0
        elif cmd == COPY_TO_HEAD0:
            ops += 1
            if tape[head0] != tape[head1]:
                tape[head0] = tape[head1]
                tortoise = -1
                power = 1
                lam = 0
        elif cmd == LOOP_START:
            ops += 1
            if tape[head0] == 0:
                depth = 1
                pc += 1
                while pc < COMBINED_SIZE and depth > 0:
                    if tape[pc] == LOOP_END:
                        depth -= 1
                    elif tape[pc] == LOOP_START:
                        depth += 1
                    pc += 1
                pc -= 1
                if depth != 0:
                    break
        elif cmd == LOOP_END:
            ops += 1
            if tape[head0] != 0:
                depth = 1
                pc -= 1
                while pc >= 0 and depth > 0:
                    if tape[pc] == LOOP_START:
                        depth -= 1
                    elif tape[pc] == LOOP_END:
                        depth += 1
                    pc -= 1
                pc += 1
                if depth != 0:
                    break

        pc += 1

    return ops


@njit(cache=True)
def splitmix64(x):
    """SplitMix64 hash of a uint64 (same construction cubff uses for mutation)."""
    z = x + np.uint64(0x9E3779B97F4A7C15)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


@njit(parallel=True, cache=True)
def run_epoch(soup, perm, max_steps, mutation_prob, epoch_seed, ops_out, heads_init=False):
    """
    One epoch: programs perm[2i] and perm[2i+1] form tape i, are optionally
    mutated, executed, and written back to their own slots. The soup is
    never physically reordered, so a slot index is a stable identity.

    mutation_prob is an integer in units of 2**-30 per byte (0 disables).
    ops_out[i] receives the instruction count of tape i.
    """
    num_pairs = perm.shape[0] // 2
    mprob = np.uint64(mutation_prob)
    mask30 = np.uint64((1 << 30) - 1)
    for i in prange(num_pairs):
        tape = np.empty(COMBINED_SIZE, dtype=np.uint8)
        a = perm[2 * i]
        b = perm[2 * i + 1]
        for j in range(TAPE_SIZE):
            tape[j] = soup[a, j]
            tape[j + TAPE_SIZE] = soup[b, j]

        if mprob > 0:
            base = (np.uint64(epoch_seed) * np.uint64(num_pairs) + np.uint64(i)) * np.uint64(COMBINED_SIZE)
            for j in range(COMBINED_SIZE):
                r = splitmix64(base + np.uint64(j))
                if ((r >> np.uint64(8)) & mask30) < mprob:
                    tape[j] = np.uint8(r & np.uint64(0xFF))

        ops_out[i] = evaluate(tape, max_steps, heads_init)

        for j in range(TAPE_SIZE):
            soup[a, j] = tape[j]
            soup[b, j] = tape[j + TAPE_SIZE]


# ---------------------------------------------------------------------------
# Program keys
# ---------------------------------------------------------------------------
@njit(parallel=True, cache=True)
def key_hashes(soup, is_cmd, out_hash, out_len):
    """FNV-1a hash and length of each program's instruction-only key."""
    n = soup.shape[0]
    m = soup.shape[1]
    for i in prange(n):
        h = np.uint64(0xcbf29ce484222325)
        length = 0
        for j in range(m):
            b = soup[i, j]
            if is_cmd[b]:
                h = (h ^ np.uint64(b)) * np.uint64(0x100000001b3)
                length += 1
        out_hash[i] = h
        out_len[i] = length


@njit(cache=True)
def _byte_histogram(arr):
    """Histogram of a uint8 array. Deliberately single-threaded: it runs in the metrics
    thread, and Numba's default threading layer must not be entered from two threads."""
    counts = np.zeros(256, dtype=np.int64)
    for i in range(arr.shape[0]):
        counts[arr[i]] += 1
    return counts


def byte_histogram(arr):
    return _byte_histogram(np.ascontiguousarray(arr).reshape(-1))


@njit(cache=True)
def _unique_counts(hashes, uniq, first, counts, table_key, table_idx):
    """Open-addressing hash table: unique hashes in order of first occurrence."""
    n = hashes.shape[0]
    cap = table_key.shape[0]
    mask = cap - 1
    k = 0
    for i in range(n):
        h = hashes[i]
        slot = int((h * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(40)) & mask
        while True:
            j = table_idx[slot]
            if j < 0:
                table_key[slot] = h
                table_idx[slot] = k
                uniq[k] = h
                first[k] = i
                counts[k] = 1
                k += 1
                break
            if table_key[slot] == h:
                counts[j] += 1
                break
            slot = (slot + 1) & mask
    return k


def unique_counts(hashes):
    """
    (uniq, first_index, counts) of a uint64 array, like np.unique(..., return_index=True,
    return_counts=True) but in order of first occurrence and without sorting.
    """
    n = hashes.shape[0]
    cap = 1
    while cap < 2 * n:
        cap <<= 1
    uniq = np.empty(n, dtype=np.uint64)
    first = np.empty(n, dtype=np.int64)
    counts = np.empty(n, dtype=np.int64)
    table_key = np.empty(cap, dtype=np.uint64)
    table_idx = np.full(cap, -1, dtype=np.int64)
    k = _unique_counts(np.ascontiguousarray(hashes), uniq, first, counts, table_key, table_idx)
    return uniq[:k], first[:k], counts[:k]


def compute_keys(soup):
    """Return (hashes uint64[n], lengths int32[n]) for a (n, 64) soup."""
    n = soup.shape[0]
    hashes = np.empty(n, dtype=np.uint64)
    lengths = np.empty(n, dtype=np.int32)
    key_hashes(soup, IS_CMD, hashes, lengths)
    return hashes, lengths


def program_key(row):
    """Instruction-only string of one program (numpy uint8 row or bytes)."""
    row = np.asarray(row, dtype=np.uint8)
    return row[IS_CMD[row]].tobytes().decode('ascii')


def key_hash(key):
    """FNV-1a hash of an instruction string, matching key_hashes()."""
    h = 0xcbf29ce484222325
    for ch in key.encode('ascii'):
        h = ((h ^ ch) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def to_signed(h):
    """uint64 -> signed int64 (for SQLite storage)."""
    h = int(h)
    return h - (1 << 64) if h >= (1 << 63) else h


def to_unsigned(h):
    h = int(h)
    return h + (1 << 64) if h < 0 else h


# ---------------------------------------------------------------------------
# Self-replication test (port of cubff CheckSelfRep)
# ---------------------------------------------------------------------------
@njit(parallel=True, cache=True)
def selfrep_scores(programs, seed, max_steps, out, heads_init=False):
    """
    For each program: 13 trials, each pairing the program with a fresh random
    64-byte partner, running one epoch, then 4 more generations in which the
    partner half is moved to the program half and paired with the same noise.
    A tape byte counts as stable if its value recurs in more than 13/4 trials
    (and, for the program half, equals the original). Score = the smaller of
    the two halves' stable-byte counts. Score >= SELFREP_THRESHOLD means the
    program reliably copies itself.
    """
    K = programs.shape[0]
    NITER = 13
    NGEN = 4
    for idx in prange(K):
        tapes = np.empty((NITER, COMBINED_SIZE), dtype=np.uint8)
        noise = np.empty(TAPE_SIZE, dtype=np.uint8)
        local_seed = splitmix64(np.uint64(K) * np.uint64(seed) + np.uint64(idx))
        for it in range(NITER):
            for j in range(TAPE_SIZE):
                r = splitmix64(local_seed ^ splitmix64(np.uint64((it + 1) * TAPE_SIZE + j)))
                noise[j] = np.uint8(r & np.uint64(0xFF))
            tape = tapes[it]
            for j in range(TAPE_SIZE):
                tape[j] = programs[idx, j]
                tape[j + TAPE_SIZE] = noise[j]
            evaluate(tape, max_steps, heads_init)
            for g in range(NGEN):
                for j in range(TAPE_SIZE):
                    tape[j] = tape[j + TAPE_SIZE]
                    tape[j + TAPE_SIZE] = noise[j]
                evaluate(tape, max_steps, heads_init)
        res0 = 0
        res1 = 0
        for i in range(COMBINED_SIZE):
            for a in range(NITER):
                if i < TAPE_SIZE and tapes[a, i] != programs[idx, i]:
                    continue
                count = 1
                for b in range(a + 1, NITER):
                    if tapes[a, i] == tapes[b, i]:
                        count += 1
                if count > NITER // 4:
                    if i < TAPE_SIZE:
                        res0 += 1
                    else:
                        res1 += 1
                    break
        out[idx] = res0 if res0 < res1 else res1


def selfrep_test(programs, seed=0, max_steps=DEFAULT_MAX_STEPS, heads_init=False):
    """Self-replication scores (int32[K]) for a (K, 64) array of programs."""
    programs = np.ascontiguousarray(programs, dtype=np.uint8)
    out = np.empty(programs.shape[0], dtype=np.int32)
    selfrep_scores(programs, seed, max_steps, out, heads_init)
    return out


# ---------------------------------------------------------------------------
# Edit distance and parasite load
# ---------------------------------------------------------------------------
@njit(cache=True)
def levenshtein(a, la, b, lb, max_d):
    """Levenshtein distance between a[:la] and b[:lb]; returns max_d+1 early when exceeded."""
    if abs(la - lb) > max_d:
        return max_d + 1
    prev = np.empty(lb + 1, dtype=np.int32)
    cur = np.empty(lb + 1, dtype=np.int32)
    for j in range(lb + 1):
        prev[j] = j
    for i in range(1, la + 1):
        cur[0] = i
        row_min = cur[0]
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            v = prev[j - 1] + cost
            if prev[j] + 1 < v:
                v = prev[j] + 1
            if cur[j - 1] + 1 < v:
                v = cur[j - 1] + 1
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > max_d:
            return max_d + 1
        for j in range(lb + 1):
            prev[j] = cur[j]
    return prev[lb]


@njit(parallel=True, cache=True)
def batch_levenshtein(keys, lens, q, lq, max_d, out):
    for i in prange(keys.shape[0]):
        out[i] = levenshtein(keys[i], lens[i], q, lq, max_d)


def edit_distance(a, b):
    a = np.frombuffer(a.encode('ascii'), dtype=np.uint8)
    b = np.frombuffer(b.encode('ascii'), dtype=np.uint8)
    return int(levenshtein(a, a.size, b, b.size, 10 ** 6))


def parasite_load(keys, counts, scores, threshold=SELFREP_THRESHOLD, max_dist=4):
    """
    Slots held by non-replicating near-variants of a replicator: species that fail the
    self-replication test but lie within `max_dist` edits (up to reversal) of a species that
    passes, such as a host minus the bracket that closes its loop. This is the *candidate*
    load: it mixes true parasites (copied over a host they precede), killers (destroy such a
    host without being copied) and harmless debris; classify_variants() separates them.
    keys: list of instruction strings; counts, scores: arrays. Returns (slots, species).
    """
    keys = list(keys)
    hosts = [i for i, s in enumerate(scores) if s >= threshold]
    if not hosts:
        return 0, 0
    slots = 0
    n_cand = 0
    for i, k in enumerate(keys):
        if scores[i] >= threshold or not k:
            continue
        for h in hosts:
            hk = keys[h]
            if _near(k, hk, variant_distance(hk, max_dist)):
                slots += int(counts[i])
                n_cand += 1
                break
    return int(slots), n_cand


def variant_distance(host_key, max_dist=4):
    """
    Edit distance within which a species counts as a near-variant of `host_key`: at most
    `max_dist`, and never more than half the host's instructions, so that a 6-instruction
    replicator does not sweep every 2-instruction program (or the empty one) into its load.
    """
    return max(1, min(max_dist, (len(host_key) - 1) // 2))


def _near(a, b, d):
    return abs(len(a) - len(b)) <= d and (edit_distance(a, b) <= d or edit_distance(a, b[::-1]) <= d)


def classify_variants(soup, max_steps, heads=False, top=512, trials=8, max_dist=4, seed=0):
    """
    Split the non-replicating near-variants of the dominant replicator by what they do to a
    host that follows them on a tape:
      hijacker  the host's slot ends up holding the variant (a parasite in the strict sense)
      killer    the host is destroyed but the variant is not copied
      debris    the host survives
    Returns dict(host_key, host_share, shares={kind: share of soup}, species={kind: count},
    examples={kind: [(key, count), ...]}), or None when the soup has no replicator.
    """
    rng = np.random.default_rng(seed)
    h, _ = compute_keys(soup)
    u, f, cnt = unique_counts(h)
    order = np.argsort(-cnt, kind='stable')[:top]
    reps = soup[f[order]]
    sc = selfrep_test(reps, seed=0, max_steps=max_steps, heads_init=heads)
    keys = [program_key(p) for p in reps]
    hosts = [i for i in range(len(keys)) if sc[i] >= SELFREP_THRESHOLD]
    if not hosts:
        return None
    dom = hosts[0]
    hk = keys[dom]
    host_idx = np.flatnonzero(h == u[order][dom])
    n = soup.shape[0]
    shares = {'hijacker': 0, 'killer': 0, 'debris': 0}
    species = {'hijacker': 0, 'killer': 0, 'debris': 0}
    examples = {'hijacker': [], 'killer': [], 'debris': []}
    for i, k in enumerate(keys):
        if sc[i] >= SELFREP_THRESHOLD or not k:
            continue
        if not any(_near(k, keys[j], variant_distance(keys[j], max_dist)) for j in hosts):
            continue
        hij = kill = 0
        for _ in range(trials):
            host = soup[host_idx[rng.integers(host_idx.size)]]
            t = np.concatenate([reps[i], host])
            evaluate(t, max_steps, heads)
            after = program_key(t[TAPE_SIZE:])
            if _near(after, k, 2):
                hij += 1
            elif not _near(after, hk, 2):
                kill += 1
        kind = 'hijacker' if hij >= trials / 2 else ('killer' if kill >= trials / 2 else 'debris')
        shares[kind] += int(cnt[order][i])
        species[kind] += 1
        if len(examples[kind]) < 5:
            examples[kind].append((k, int(cnt[order][i])))
    return {'host_key': hk, 'host_share': float(cnt[order][sc >= SELFREP_THRESHOLD].sum() / n),
            'shares': {k: v / n for k, v in shares.items()}, 'species': species, 'examples': examples}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
try:
    import brotli as _brotli

    def compressed_size(data):
        return len(_brotli.compress(data, quality=2))
    COMPRESSOR = 'brotli-q2'
except ImportError:  # pragma: no cover
    import zlib as _zlib

    def compressed_size(data):
        return len(_zlib.compress(data, 1))
    COMPRESSOR = 'zlib-1'


def shannon_entropy(data):
    """H0 in bits per byte of a bytes object / uint8 array."""
    arr = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray)) else data.reshape(-1)
    counts = byte_histogram(arr)
    probs = counts[counts > 0] / arr.size
    return float(-np.sum(probs * np.log2(probs)))


def complexity_metrics(soup):
    """
    Returns dict(compressed, nbytes, h0, bpb, higher_entropy) for a soup array.
    higher_entropy = H0 - bits_per_byte_after_compression (paper's definition).
    """
    arr = np.ascontiguousarray(soup).reshape(-1)
    data = arr.tobytes()
    comp = compressed_size(data)
    h0 = shannon_entropy(arr)
    bpb = comp * 8.0 / len(data)
    return {'compressed': comp, 'nbytes': len(data), 'h0': h0, 'bpb': bpb,
            'higher_entropy': h0 - bpb}


# ---------------------------------------------------------------------------
# Deterministic RNG streams
# ---------------------------------------------------------------------------
def seed_to_int(seed):
    """
    Seeds may be any text. A decimal number is used as is; any other string is hashed
    (SHA-256, first 8 bytes) so names like 'mini-15' are valid, unique and reproducible
    on every machine.
    """
    if isinstance(seed, (int, np.integer)):
        return int(seed)
    s = str(seed).strip()
    if s.lstrip('-').isdigit():
        return int(s)
    import hashlib
    return int.from_bytes(hashlib.sha256(s.encode('utf-8')).digest()[:8], 'little')


def init_rng(seed):
    """RNG used to draw the initial random soup."""
    return np.random.default_rng([int(seed), 0, 0])


def epoch_rng(seed, epoch):
    """RNG used for the pairing permutation of one epoch (replay-exact)."""
    return np.random.default_rng([int(seed), 1, int(epoch)])


def random_soup(num_programs, seed, dist=None):
    """Initial soup: uniform bytes, or drawn from a byte distribution (see parse_init_dist)."""
    rng = init_rng(seed)
    if dist is None:
        return rng.integers(0, 256, (num_programs, TAPE_SIZE), dtype=np.uint8)
    return rng.choice(256, size=(num_programs, TAPE_SIZE), p=dist).astype(np.uint8)


def epoch_permutation(seed, epoch, num_programs):
    return epoch_rng(seed, epoch).permutation(num_programs).astype(np.int64)


def partners_from_perm(perm):
    """partner[slot] = the slot it shared a tape with under this permutation."""
    partner = np.empty(perm.shape[0], dtype=np.int64)
    partner[perm[0::2]] = perm[1::2]
    partner[perm[1::2]] = perm[0::2]
    return partner


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------
def save_checkpoint(soup, epoch, path, meta=None):
    """
    Format v2: b'BFF2' | u32 json_len | json header | raw soup bytes.
    The header always carries num_programs, tape_size and epoch; the caller
    may add seed, mutation_prob, max_steps etc. via `meta`.
    """
    soup = np.ascontiguousarray(soup, dtype=np.uint8)
    header = {'num_programs': int(soup.shape[0]), 'tape_size': int(soup.shape[1]),
              'epoch': int(epoch)}
    if meta:
        header.update(meta)
    hjson = json.dumps(header).encode('utf-8')
    tmp = path + '.tmp'                     # write, then rename: a full disk or a crash leaves no stub behind
    with open(tmp, 'wb') as f:
        f.write(CHECKPOINT_MAGIC_V2)
        f.write(len(hjson).to_bytes(4, 'little'))
        f.write(hjson)
        f.write(soup.tobytes())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_checkpoint(path):
    """Returns (soup uint8[n, tape_size], meta dict). Reads v1 and v2 files."""
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic == CHECKPOINT_MAGIC_V2:
            hlen = int.from_bytes(f.read(4), 'little')
            meta = json.loads(f.read(hlen).decode('utf-8'))
            n, t = meta['num_programs'], meta['tape_size']
            raw = f.read(n * t)
            if len(raw) != n * t:
                raise CheckpointError(f"{path} is truncated ({len(raw)} of {n * t} soup bytes); "
                                      "the disk was probably full when it was written")
            soup = np.frombuffer(raw, dtype=np.uint8).reshape(n, t).copy()
            meta.setdefault('format', 2)
            return soup, meta
        if magic == CHECKPOINT_MAGIC_V1:
            n = int.from_bytes(f.read(4), 'little')
            t = int.from_bytes(f.read(4), 'little')
            epoch = int.from_bytes(f.read(4), 'little')
            f.read(8)
            soup = np.frombuffer(f.read(n * t), dtype=np.uint8).reshape(n, t).copy()
            return soup, {'num_programs': n, 'tape_size': t, 'epoch': epoch, 'format': 1}
    raise CheckpointError(f"Not a BFF checkpoint (magic {magic!r}): {path}")
