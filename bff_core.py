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
import numpy as np
from numba import njit, prange

TAPE_SIZE = 64
COMBINED_SIZE = 2 * TAPE_SIZE
DEFAULT_MAX_STEPS = 32768        # step budget per tape evaluation (paper/cubff use 8192)
SELFREP_THRESHOLD = 5            # cubff kSelfrepThreshold
CHECKPOINT_MAGIC_V1 = b'BFFS'
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

FNV_OFFSET = np.uint64(0xcbf29ce484222325)
FNV_PRIME = np.uint64(0x100000001b3)
EMPTY_KEY_HASH = int(FNV_OFFSET)


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------
@njit(cache=True)
def evaluate(tape, max_steps):
    """
    Execute BFF on a 128-byte tape in place. Both heads and the program
    counter start at 0; heads wrap modulo 128. Execution stops when the
    program counter leaves the tape, a bracket is unmatched, or the step
    budget is spent. Returns the number of instructions executed.
    """
    head0 = 0
    head1 = 0
    pc = 0
    ops = 0

    for _ in range(max_steps):
        if pc < 0 or pc >= COMBINED_SIZE:
            break

        head0 = head0 & (COMBINED_SIZE - 1)
        head1 = head1 & (COMBINED_SIZE - 1)

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
        elif cmd == MINUS:
            tape[head0] = (tape[head0] - 1) & 0xFF
            ops += 1
        elif cmd == COPY_TO_HEAD1:
            tape[head1] = tape[head0]
            ops += 1
        elif cmd == COPY_TO_HEAD0:
            tape[head0] = tape[head1]
            ops += 1
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
def run_epoch(soup, perm, max_steps, mutation_prob, epoch_seed, ops_out):
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

        ops_out[i] = evaluate(tape, max_steps)

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
def selfrep_scores(programs, seed, max_steps, out):
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
            evaluate(tape, max_steps)
            for g in range(NGEN):
                for j in range(TAPE_SIZE):
                    tape[j] = tape[j + TAPE_SIZE]
                    tape[j + TAPE_SIZE] = noise[j]
                evaluate(tape, max_steps)
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


def selfrep_test(programs, seed=0, max_steps=DEFAULT_MAX_STEPS):
    """Self-replication scores (int32[K]) for a (K, 64) array of programs."""
    programs = np.ascontiguousarray(programs, dtype=np.uint8)
    out = np.empty(programs.shape[0], dtype=np.int32)
    selfrep_scores(programs, seed, max_steps, out)
    return out


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
    arr = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray)) else data.ravel()
    counts = np.bincount(arr, minlength=256)
    probs = counts[counts > 0] / arr.size
    return float(-np.sum(probs * np.log2(probs)))


def complexity_metrics(soup):
    """
    Returns dict(compressed, nbytes, h0, bpb, higher_entropy) for a soup array.
    higher_entropy = H0 - bits_per_byte_after_compression (paper's definition).
    """
    data = np.ascontiguousarray(soup).tobytes()
    comp = compressed_size(data)
    h0 = shannon_entropy(data)
    bpb = comp * 8.0 / len(data)
    return {'compressed': comp, 'nbytes': len(data), 'h0': h0, 'bpb': bpb,
            'higher_entropy': h0 - bpb}


# ---------------------------------------------------------------------------
# Deterministic RNG streams
# ---------------------------------------------------------------------------
def init_rng(seed):
    """RNG used to draw the initial random soup."""
    return np.random.default_rng([int(seed), 0, 0])


def epoch_rng(seed, epoch):
    """RNG used for the pairing permutation of one epoch (replay-exact)."""
    return np.random.default_rng([int(seed), 1, int(epoch)])


def random_soup(num_programs, seed):
    return init_rng(seed).integers(0, 256, (num_programs, TAPE_SIZE), dtype=np.uint8)


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
    with open(path, 'wb') as f:
        f.write(CHECKPOINT_MAGIC_V2)
        f.write(len(hjson).to_bytes(4, 'little'))
        f.write(hjson)
        f.write(soup.tobytes())


def load_checkpoint(path):
    """Returns (soup uint8[n, tape_size], meta dict). Reads v1 and v2 files."""
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic == CHECKPOINT_MAGIC_V2:
            hlen = int.from_bytes(f.read(4), 'little')
            meta = json.loads(f.read(hlen).decode('utf-8'))
            n, t = meta['num_programs'], meta['tape_size']
            soup = np.frombuffer(f.read(n * t), dtype=np.uint8).reshape(n, t).copy()
            meta.setdefault('format', 2)
            return soup, meta
        if magic == CHECKPOINT_MAGIC_V1:
            n = int.from_bytes(f.read(4), 'little')
            t = int.from_bytes(f.read(4), 'little')
            epoch = int.from_bytes(f.read(4), 'little')
            f.read(8)
            soup = np.frombuffer(f.read(n * t), dtype=np.uint8).reshape(n, t).copy()
            return soup, {'num_programs': n, 'tape_size': t, 'epoch': epoch, 'format': 1}
    raise ValueError(f"Not a BFF checkpoint (magic {magic!r}): {path}")
