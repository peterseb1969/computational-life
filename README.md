# BFF Primordial Soup

A simple and basic (Numba-accelerated) Python implementation of the BFF (Brainfuck variant) primordial soup experiment from ["Computational Life: How Well-formed, Self-replicating Programs Emerge from Simple Interaction"](https://arxiv.org/abs/2406.19108) by Blaise Agüera y Arcas et al.

This demonstrates how **self-replicating programs can emerge spontaneously** from random programs through self-modification — no fitness function, no selection pressure, just random interactions.

![Computational Life - BFF Primordial Soup experiment showing phase transition](computational_life.jpg)

## How It Works

1. Start with a "soup" of random 64-byte programs with completely randomized byte values (only 10 of which correspond to actual brainfuck instructions, meaning only about ~4% of cells will actually have any type of instruction at all in them and, the rest are just no-ops)
2. Each epoch: shuffle the programs, pair them up, concatenate into 128-byte tapes
3. Execute the BFF interpreter on each tape (programs can modify themselves and each other)
4. Split tapes back into programs
5. Repeat — eventually, self-replicators emerge and take over the soup

The **phase transition** is detected when higher-order entropy spikes above 3.0, indicating structured replicators have emerged from random noise.

**No mutation:** This Python implementation deliberately uses no external mutation. Programs only change through self-modification during BFF execution. This demonstrates the paper's key insight — self-replicators can emerge purely from program interactions without any external randomness or mutation pressure.

## BFF Instruction Set

BFF (Brainfuck variant) modifies standard Brainfuck for self-modification instead of I/O. It uses two head pointers on a shared tape:

| Command | Description |
|---------|-------------|
| `>` `<` | Move head0 right/left |
| `}` `{` | Move head1 right/left |
| `+` `-` | Increment/decrement byte at head0 |
| `.` | Copy byte from head0 position to head1 position |
| `,` | Copy byte from head1 position to head0 position |
| `[` | Jump past matching `]` if byte at head0 is 0 |
| `]` | Jump back to matching `[` if byte at head0 is not 0 |

**Note:** Standard Brainfuck's I/O commands (`.` and `,`) are repurposed for copying between heads.

### Execution semantics

Both heads and the program counter start at 0; heads wrap modulo 128; non-instruction bytes are skipped. A tape stops when the program counter leaves the tape, a bracket is unmatched, or the step budget (`--max-steps`, default 32768) is spent.

The interpreter also stops a program that is **provably stuck**: once the tape has stopped changing, the machine state is just (program counter, head0, head1), and if that state recurs the program loops forever without writing again, so the tape is already final. This is detected with Brent's cycle algorithm and gives exactly the same tapes as running out the budget, about 9x faster on a mature soup where most tapes spin. The only visible effect is that "instructions per tape" counts useful work rather than spinning, so it no longer jumps into the thousands for stuck programs.

### Emergent Replicators

A replicator that emerges may look like the following and often has a palindrome-like pattern:

```
{[<},]],}<[{
```

**How it works:**
1. `{` — Move head1 left (wraps to position 127)
2. `[<},]` — Copy loop:
   - `[` — Start loop (exits when byte at head0 is 0)
   - `<` — Move head0 (destination) left
   - `}` — Move head1 (source) right
   - `,` — Copy byte from head1 to head0
   - `]` — Jump back to `[` if byte at head0 ≠ 0
3. `]` — End outer structure
4. `}<[{` — Near-mirror of the start

**Why palindrome structure?**

When two programs meet on a 128-byte tape:
```
|  Program A (0-63)  |  Program B (64-127)  |
     ↑ head1 (source)     ↑ head0 (destination)
```

The replicator **copies itself backwards** into the other program's space. The palindrome structure ensures that when concatenated with another copy, the combined tape still contains a valid copy loop — making it robust to being "cut" at different points.

## Files

| File | Description |
|------|-------------|
| `bff_soup.py` | Numba-accelerated simulation. Writes a run directory with metrics, checkpoints and lineage records |
| `bff_core.py` | Shared machinery: BFF interpreter kernels, program keys, self-replication test, checkpoint I/O |
| `bff_lineage.py` | Lineage recording (species births, per-slot change records, species counts) |
| `bff_query.py` | Query layer and CLI: search, species, lineage, slot history, exact replay |
| `bff_web.py` + `web/` | Local web viewer |
| `bff_archive.py` | Builds the durable run archive (winners, families, emergence story) |
| `bff_compare.py` | Cross-run analytics over archives |
| `bff_analysis.py` | Most common programs in a checkpoint, with self-replication scores |
| `visualize_bff.py` | Real-time ASCII visualization of entropy and compression |
| `testdata/` | A few raw replicators from an earlier run, used as test fixtures |

## Quick Start

```bash
# Use the project venv (has numba, numpy, brotli)
source ../.venv/bin/activate

# Run simulation with 131k programs (as used in the paper); ~17 epochs/sec pre-transition on an M2
python3 bff_soup.py --num 131072 --epochs 40000 --seed 42

# In a separate terminal, watch the progress
python3 visualize_bff.py runs/42
python3 visualize_bff.py runs/42 --last 500     # zoom in on the last 500 epochs
```

### The run directory

Every run writes to `runs/<seed>` (or `--run-dir`):

| Path | Content |
|------|---------|
| `meta.json` | All parameters, resume history, stop-condition events |
| `log.csv` | Per-epoch metrics: compressed size, higher-order entropy, H0, bits/byte, ops per pair, unique species, top species share, key changes, new species, self-replicating slots |
| `checkpoints/*.dat` | Full soup every `--checkpoint-interval` epochs (format v2: JSON header + raw bytes) |
| `changes.bin` | Change records (epoch, slot, partner slot, new key hash) for slots that changed to a recorded species, plus every slot at epoch 0 |
| `species.db` | SQLite: recorded species with their birth event (epoch, slot, partner, both parent keys), key texts of snapshot species, species counts every `--species-interval` epochs, self-replication scores |
| `pending.npz` | The recorder's rolling window, saved on exit so a resume from the final checkpoint continues exactly |

Programs are identified by their **key**: the program with all non-instruction bytes removed. Slots are stable identities: the soup is never reordered, so the change records of one slot form its line of descent.

**Which species get recorded.** A pre-transition soup produces thousands of never-repeated keys per epoch, so births are held in a rolling window of `--lineage-window` epochs and a species is written to the database only once it occupies `--promote-count` slots at the same time (it has been copied repeatedly), together with its pending ancestors (nearest first, up to `--cascade-depth` generations and `--cascade-max` rows per promotion). The promotion flags may be changed when resuming a run. Keys shorter than `--lineage-min-len` instructions are the random background and are never tracked individually, but their text is stored wherever they are the parent of a recorded species.

### Determinism and resuming

The pairing of every epoch is derived from `(seed, epoch)`, so a run resumed from any checkpoint reproduces exactly the trajectory the uninterrupted run would have taken. Resuming keeps the run's recorded settings; only `--epochs`, the stop conditions and `--print-interval` apply. Resuming from the final checkpoint of a stopped run restores the recorder's window too, so the result is identical to an uninterrupted run; resuming from an earlier checkpoint starts the window empty (noted in `meta.json`).

```bash
python3 bff_soup.py --resume runs/42 --epochs 60000                 # latest checkpoint
python3 bff_soup.py --resume runs/42/checkpoints/0000010240.dat     # a specific one
```

### Command-line Options

```
simulation:
  --num N               Number of programs (default: 1024)
  --epochs N            Run until this epoch number (default: 10000)
  --seed N              Random seed (default: 42)
  --mutation-prob P     Per-byte mutation probability per epoch (default: 0; paper: 0.000244)
  --max-steps N         Step budget per tape execution (default: 32768; paper/cubff: 8192)
output:
  --run-dir DIR         Run directory (default: runs/<seed>)
  --resume PATH         Run directory or checkpoint file to resume from
  --checkpoint-interval N   Epochs between checkpoints (default: 256)
  --species-interval N  Epochs between species-count snapshots (default: 32)
  --selfrep-interval N  Epochs between self-replication tests (default: 256)
  --selfrep-top K       Most common species to test (default: 512)
  --lineage-min-len L   Track births/changes only for keys with >= L instructions (default: 8)
  --lineage-window N    Epochs a birth is remembered while waiting to be promoted (default: 128)
  --promote-count K     Record a species once it occupies K slots at once (default: 6)
  --cascade-depth D     Generations of pending ancestors recorded with it (default: 12)
  --cascade-max M       Ancestors recorded per promotion, nearest first (default: 24)
  --lineage-budget-mb M Stop appending change records at this file size (default: 2048)
  --seed-programs F.npy[:N]  Plant N copies of the programs in F into random slots (experiments)
  --metric-interval N   Epochs between compression metrics (default: 1)
  --metric-sample N     Programs to compress for the metrics (default: 0 = whole soup)
stop conditions (optional):
  --stop-entropy X      Stop when higher-order entropy exceeds X
  --stop-share X        Stop when one species exceeds X percent of the soup
  --stop-selfreps N     Stop when at least N slots hold a self-replicator
  --stop-after N        Keep running N more epochs after a stop condition fires
  --no-archive          Do not build archive/<run>.json on exit
```

### Analyzing Results

```bash
python3 bff_analysis.py runs/42 --top 10                    # latest checkpoint of the run
python3 bff_analysis.py runs/42/checkpoints/0000012800.dat  # a specific checkpoint
```

Each listed program comes with its **self-replication score**, the cubff test: the program is paired with 13 random partners, run for 5 generations each, and the score is the number of tape bytes that stay stable. A score of 5 or more means the program reliably copies itself; a perfect replicator scores 64.

## Analysis Tools

### Web viewer

```bash
python3 bff_web.py                 # serves runs/ on http://localhost:8765/ and opens a browser
python3 bff_web.py --runs runs --port 8765 --no-browser
```

Works while a simulation is running. Tabs:

| Tab | What it shows |
|-----|---------------|
| Overview | Entropy, bits per byte, instructions per tape, unique species, top species share, self-replicator count over time, with the transition marked. Auto-refreshes on a running run. |
| Species | Stacked area of the top species over time (Muller-style), and a snapshot table at any recorded epoch with details / lineage / run links |
| Search | Exact, substring, regex or fuzzy (edit distance) search over recorded species, or over the keys present in a checkpoint. Click a result for its birth event, count history and self-replication history. |
| Lineage | Ancestry tree of a species: each node shows where and when it was born, which key the slot held before ("parent") and which key shared the tape ("partner"), with edit distances marking the primary ancestor |
| Stepper | Animated BFF interpreter. Load two programs by hand, run a species against random partners, or replay the exact tape on which a species was born, then step through it with the program counter and both heads highlighted. "Next generation" moves the second half into the first and pairs it with fresh random bytes, like the self-replication test. |

### Run archives and cross-run analytics

The run directory is a large working set. The durable output of a run is its **archive**, one JSON file of a few hundred KB written to `archive/<run>.json` when the simulation finishes or is stopped (also `python3 bff_archive.py runs/42` at any time). It contains:

- run facts and event epochs: transition (entropy > 3), first self-replicator, share crossings
- the top 20 species at the end and at 256, 1024 and 4096 epochs after the transition, each with raw bytes, share, self-replication score and birth
- **families**: the winners clustered into variants of one core (edit distance ≤ 3), with a functional profile of the representative (instruction usage, writing head, instructions per execution, faithful generations)
- the emergence story: the ancestry tree of each family's representative, and for the five leading families the exact tapes of the birth events along the primary ancestor line
- the metrics log, decimated, at full resolution around the transition

```bash
python3 bff_compare.py                 # table of all archived runs
python3 bff_compare.py --families      # leading family cores across runs, recurring cores, edit distances
```

### Command line

```bash
python3 bff_query.py info    runs/42
python3 bff_query.py top     runs/42 --epoch 39424 --n 20
python3 bff_query.py search  runs/42 '[[<,,,}]]' --mode substring      # also exact | regex | fuzzy
python3 bff_query.py search  runs/42 '<,,,}' --epoch 12800              # keys in the checkpoint at/below that epoch
python3 bff_query.py species runs/42 '[[<,,,}]]}}]]},,,<[['
python3 bff_query.py lineage runs/42 '[[<,,,}]]}}]]},,,<[[' --depth 8
python3 bff_query.py slot    runs/42 12345 --before 39000 [--trace]
python3 bff_query.py tape    runs/42 --epoch 39301 --slot 12345         # exact tape before/after, replayed
```

Add `--json` before the subcommand for machine-readable output. Replaying a tape re-executes the epochs since the nearest checkpoint (up to 256), which takes seconds on a 131k soup and competes with a running simulation for CPU.

**Lineage semantics.** A species' *birth* is the first execution that produced its key. The *parent* is what the slot held before that execution and the *partner* is the other half of the tape; whichever is closer by edit distance is marked primary. Ancestors older than the recording window when the species was promoted are not recorded, but their text is stored in the child's birth row, so a chain never ends blind.

## Running with cubff (C++ Implementation)

For maximum speed, the [cubff](https://github.com/paradigms-of-intelligence/cubff) C++ implementation is ~4x faster than this Python version:

```bash
# Clone and build
git clone https://github.com/paradigms-of-intelligence/cubff.git
cd cubff && mkdir build && cd build
cmake .. -DCUDA=OFF
make -j$(nproc)

# Run (outputs to stdout in CSV format)
./main --lang bff_noheads --soup-size 1024 --print-interval 64 > ../bff_run.log &

# Use the same visualizer
cd ..
python3 visualize_bff.py bff_run.log
```

## Metrics Explained

The simulation tracks two metrics based on compression (Brotli quality 2 on the whole soup, as in the paper; falls back to zlib if brotli is not installed):

**Higher-Order Entropy** (complexity metric, as used in the paper):
```
entropy = H0 - bpb
```
Where H0 is Shannon entropy and bpb is bits-per-byte after compression.
- Measures "bits saved per byte" through compression beyond simple character frequencies
- **Random soup ≈ 0**: Incompressible noise, no patterns
- **Structured soup > 3**: Repetitive patterns (replicators) compress well
- A sudden spike indicates phase transition — replicators have taken over

**Bits per Byte** (compression ratio):
```
bpb = compressed_size × 8 / original_size
```
- Inverse of entropy: how many bits needed per byte after compression
- **Random soup ≈ 8 bpb**: No compression possible
- **Structured soup < 5 bpb**: Significant compression = replicators present

## What to Look For

In the visualizer:
- **🔴 Pre-life**: Entropy near 0, ~8 bpb, random noise
- **🟡 Evolving**: Entropy 1-3, structure forming
- **🟢 TRANSITION**: Entropy spikes to 4-6, bpb drops below 4, replicators have emerged!

A successful transition typically shows:
- Sudden entropy spike (0 → 4+)
- Bits per byte drops (8 → 4 or lower)
- Instructions per tape rise as copy loops take over

## References

- Paper: [arXiv:2406.19108](https://arxiv.org/abs/2406.19108)
- Original implementation: [github.com/paradigms-of-intelligence/cubff](https://github.com/paradigms-of-intelligence/cubff)
- Sean Carroll interview: [Mindscape Podcast](https://www.preposterousuniverse.com/podcast/2024/07/22/283-blaise-aguera-y-arcas-on-the-emergence-of-replication-and-computation/)
