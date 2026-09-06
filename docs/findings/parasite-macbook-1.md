# A parasite kills the first life in a BFF soup

*Run `macbook-1`, protocol `128k-8192` (131072 programs, 8192 steps per tape, no mutation, heads start at 0), MacBook Pro M4, 2026-09-06. Archive: [`ps-macbook-pro-macbook-1.json`](https://github.com/peterseb1969/computational-life-results/blob/main/archive/ps-macbook-pro-macbook-1.json) in the [results repository](https://github.com/peterseb1969/computational-life-results).*

## Summary

After 49,000 epochs of random soup, a 12-instruction self-replicator emerged and took over the soup within a thousand epochs. Thirty-five epochs after its birth, a copy error produced a variant that had lost the two closing brackets of its copy loop. That variant cannot copy anything, yet it reproduces: its first instructions leave the heads in the positions the replicator's loop expects, and its unmatched opening bracket lets execution run on into the partner's code. When the partner is the replicator, the replicator's own loop copies the parasite over the replicator. The parasite also survives encounters that destroy the replicator. Over 17,000 epochs it drove the replicator to extinction, held 99 percent of the soup for a moment, and then, with no host left to reproduce it, eroded away. What remained was not the random soup of the beginning but a desert: debris of the parasite, converging rather than diversifying, without a single closing bracket or `+`/`-` left to build a new loop from. By epoch 150,000 no new life had appeared; instead the debris underwent a second runaway into a frozen crystal of `>`, `,` and `{` bytes in which nothing changes any more. One seed, one complete arc: emergence, takeover, parasitism, host extinction, parasite extinction, desert, crystal.

## Timeline

| Epoch | Replicators | Parasites | Other | Distinct keys | Entropy | What happens |
|---|---|---|---|---|---|---|
| 0 to 49,000 | 0 | 0 | 100% | 110k to 118k | 0.2 to 0.5 | random background, no self-replicator found by the periodic test |
| 49,438 | | | | | | replicator `[..{>]]>{..[` born in slot 10489 |
| 49,473 | | | | | | parasite `[..{>>{..[` born in slot 71767, from a replicator that lost `]]` |
| 49,540 | | | | | 1.7 | distinct keys fall below 5% of the soup: the takeover |
| 49,858 | | | | | 3.0 | entropy crosses 3 |
| 53,440 | 38% | 60% | 2% | 156 | 3.1 | the replicator's peak |
| 60,064 | 17% | 80% | 3% | 186 | 3.0 | |
| 64,480 | 2% | 97% | 1% | 586 | 2.9 | |
| 66,700 | 0 | 98% | 2% | 340 | 3.0 | last replicator gone; the self-replication test finds nothing from 69k on |
| 68,896 | 0 | 99.4% | 0.6% | 539 | 2.9 | the parasite's peak, nothing in the soup can copy |
| 75,520 | 0 | 86% | 10% | 8.5k | 2.5 | erosion |
| 79,936 | 0 | 1% | 78% | 22.6k | 2.2 | the parasite lineage is scattered over dozens of decaying variants |
| 130,000 | 0 | 0 | 100% | 15.9k | 2.0 | the desert: homogeneous debris, no `]`, `+`, `-` or `,` left in the soup |
| 141,500 to 147,000 | 0 | 0 | 100% | 15k to 131k | 2.0 to -0.4 | the crystal: a byte-painting runaway rewrites every slot, then freezes |

Shares are the fraction of the 131,072 slots holding a key of each kind, from the species snapshots every 32 epochs. "Replicators" are keys containing the loop `>]]>`; "parasites" are keys built on `..{>>{..` or `..{>{..` without a closing bracket.

## The two organisms

Both keys are palindromes, so the mirror image a backwards copy produces is the same species.

**The replicator**, `[..{>]]>{..[`, 12 instructions, self-replication score 63 of 64. Its loop is `[ . . { > ]`. Both heads start at 0, so the first pass copies byte 0 onto itself, then `{` moves head1 to 127 and `>` moves head0 to 1, and `]` jumps back because the byte under head0 is not zero. From then on every iteration copies one byte from head0, moving right through the replicator, to head1, moving left from the end of the partner's half. The replicator writes itself, reversed, over its partner from the far end backwards. Traced on a real instance from epoch 55,808:

```
step   4  pc   4  [   head0=0   head1=0
step  12  pc  12  .   head0=0   head1=0
step  14  pc  14  .   head0=0   head1=0
step  17  pc  17  {   head0=0   head1=0
step  18  pc  18  >   head0=0   head1=127
step  31  pc  31  ]   head0=1   head1=127     jump back
step  39  pc  12  .   head0=1   head1=127     tape[127] = tape[1]
...
step  85  pc  31  ]   head0=3   head1=125
```

**The parasite**, `[..{>>{..[`, 10 instructions, self-replication score 0. It is the replicator's key with the `]]` removed and, in the raw bytes, one extra `>`. Its instructions execute once, in a straight line: `..` copy byte 0 onto itself, `{` head1 to 127, `>>` head0 to 2, `{` head1 to 126, `..` copy byte 2 to byte 126, `[` enter because byte 2 is not zero. There is no closing bracket, so the program counter walks through the parasite's remaining data bytes and crosses into the second half of the tape at step 64, with head0 = 2 and head1 = 126.

## The mechanism, traced

Tape `[parasite | replicator]`, real instances from epoch 55,808:

```
step  62  pc  62  [   head0=2   head1=126     the parasite's last instruction
   -> pc crosses into the second half at step 64 with head0=2, head1=126
step  68  pc  68  [   head0=2   head1=126     the replicator's loop begins
step  76  pc  76  .   head0=2   head1=126     tape[126] = tape[2]
step  81  pc  81  {   head0=2   head1=126
step  82  pc  82  >   head0=2   head1=125
step  95  pc  95  ]   head0=3   head1=125     jump back
step 103  pc  76  .   head0=3   head1=125     tape[125] = tape[3]
...
result after 965 steps:  first half '[..{>>{..['   second half '[..{>>{..['
```

The replicator's loop runs exactly as designed, but head0 is in the parasite's half and head1 in the replicator's own half, because the parasite put them there. The loop copies the parasite, byte by byte and reversed, over the replicator. The parasite has no copy machinery of its own; it borrows the host's, in the host's own body.

The other way round, tape `[replicator | parasite]`, the replicator runs first with the heads at 0, copies itself over the parasite, and the loop runs out the step budget: result `[..{>]]>{..[` in both halves.

## Pair outcomes at epoch 59,999

Measured on 300 random instances of each kind and a sample of the remaining background:

| First half | Second half | Result |
|---|---|---|
| replicator | parasite | both halves replicator, 100% |
| parasite | replicator | both halves parasite, 100% |
| replicator | background | replicator copies itself over the background, 100% |
| background | replicator | the replicator turns into the parasite, 80%; survives, 18% |
| parasite | background | parasite unchanged, 100% |
| background | parasite | parasite unchanged, 82% |
| replicator | replicator | replicator, 100% |
| parasite | parasite | parasite, 100% |

The fourth row is where parasites come from. A replicator in the second half behind a background program is entered by fall-through with the heads wherever the background left them; its loop then copies the background over its own bytes until it hits a zero, and four times in five what remains is exactly the loopless prefix. The same fall-through that lets the parasite hijack the host also manufactures new parasites from hosts, continuously.

## Why the host loses

Each epoch every program is in exactly one tape, in the first or the second half with equal probability.

- A replicator in the first half reproduces, whatever the partner is.
- A replicator in the second half is copied over if the partner is a replicator (no loss), converted into a parasite if the partner is a parasite (always), and converted into a parasite four times in five if the partner is background.
- A parasite in the first half reproduces only if the partner is a replicator, and is otherwise unchanged.
- A parasite in the second half is unchanged unless the partner is a replicator, which copies over it.

Against each other, host and parasite are symmetric: whoever runs first wins. Against the background, the host converts into the parasite while the parasite is untouched. As long as background exists, the balance tips towards the parasite; once the background is gone, the replicator's only losses are to parasites and its only gains are over parasites, again symmetric, and the drift accumulated earlier is never recovered. The replicator went from 38 percent at epoch 53k to zero at 66.7k.

## Why the parasite dies next

With the host extinct nothing in the soup can copy. The parasites are inert: 23 instructions per tape, and the simulation ran at 170 epochs per second, the fastest of the whole run. Every remaining active program that lands in front of a parasite and moves a head or writes a byte damages it, and no copy ever repairs the damage. The parasite population eroded from 99 percent at 69k to 1 percent at 80k while distinct keys climbed from a few hundred to 22,000. The dominant keys at the end are broken parasites such as `[..{>>{.[[[[[`, each below 1 percent.

## Relation to earlier findings

The fall-through of the program counter from the first half into the second is the same mechanism found in run 15 on the Mac mini, where a replicator rose to 31 percent and faded because, in the second half, its own loop copied the partner over it, with births and deaths per instance equal to three decimals. Here the damaged copy is not junk but a parasite, which turns a zero-drift random walk into a directed extinction.

The paper ("Computational Life", Agüera y Arcas et al., 2024) reports no parasites or hypercycles in BFF. This run was without mutation; background mutation would damage parasites as much as hosts, and might be what keeps them rare in the paper's default setup. Tierra's parasites, which reproduce by calling the host's copy routine, are the closest precedent.

## Can life re-emerge from the remains? The desert

The run was extended to 150,000 epochs to find out. No self-replicator appeared, and the soup shows why: it is not returning to the random state it started from.

| Epoch | Distinct keys | Entropy | Bits per byte | Key changes per epoch |
|---|---|---|---|---|
| 0 to 49k, before life | 110k to 118k | 0.2 to 0.5 | 6.3 | 20k to 38k |
| 80k | 25k | 2.2 | 1.50 | 44k |
| 100k | 25k | 2.1 | 1.55 | 36k |
| 130k | 16k | 2.0 | 1.57 | 26k |

After the parasite's decay the soup is a homogeneous field of its debris: at 130k, 70 percent of keys still contain `[..{>`, the soup compresses to 1.6 bits per byte, and it is converging rather than diversifying. Distinct keys fall and activity falls, because the few programs that still write copy bytes from one piece of debris onto another.

The decisive fact is the instruction repertoire. Instruction bytes per 1000 soup bytes:

| Epoch | `[` | `]` | `+` | `-` | `.` | `,` | `<` | `>` | `{` | `}` |
|---|---|---|---|---|---|---|---|---|---|---|
| 0, random | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 | 3.9 |
| 48.9k, just before life | 41 | 10 | 8 | 8 | 11 | 51 | 68 | 35 | 3 | 2 |
| 60k, replicator and parasite | 36 | 5.5 | 0 | 0 | 63 | 0 | 0 | 31 | 31 | 0 |
| 130k, the desert | 57 | 0 | 0 | 0 | 32 | 0 | 0 | 30 | 70 | 0 |

The parasite overwrote 99 percent of the soup with copies of itself, and its genome uses only `[`, `.`, `>` and `{`. The random background that carried every instruction is gone. In a sample of 8,192 programs at 130k there is not one `]`, so no loop can close, and there is no `+` or `-`, the only instructions that turn a data byte into a different byte value. The one remaining route to a new instruction byte is a `.` copying a data byte that happens to hold an instruction value, and the data bytes are being homogenised too. The soup has lost the means to evolve.

So the answer is no, and not because diversity is low in itself: the parasite stripped the soup of the building blocks. A random soup at epoch 0 is more fertile than this one at 130,000. Without mutation, which is the only process that writes uniformly random bytes, there is nothing to replenish them. Whether background mutation at the paper's rate would revive the desert is the natural follow-up experiment.

## Epilogue: the crystal

The desert did not stay a desert. The run was carried on to 150,000 epochs, and between 141,500 and 147,000 the soup went through a second, entirely different runaway.

| Epoch | Distinct keys | Key changes per epoch | Instructions per tape | Longest common key |
|---|---|---|---|---|
| 141,000 | 15k | 24k | 24 | 10 |
| 142,500 | 25k | 35k | 26 | 11 |
| 143,750 | 89k | 58k | 30 | 11 |
| 144,250 | 131,071 | 78k | 65 | 31 |
| 146,000 | 131,064 | 68k | 95 | 64 |
| 147,250 | 129,480 | 510 | 226 | 64 |

Within about 3,000 epochs every slot came to hold a different key, keys grew to fill all 64 bytes, and then the activity collapsed: from 77,000 key changes per epoch to about 500, while each tape still executed over 200 instructions. The soup had frozen. At 150,000 epochs, 90 percent of all bytes are one of four instructions, `>` 40 percent, `,` 22 percent, `{` 19 percent, `]` 7 percent, none of which except `[` was present in measurable amounts in the desert. A typical program reads `>>>>>>>>>>]>>]>,>>{{>{,>>,,{>,,,>>,,>{,>{{,>,{{>{{{>>>,>{,,>>{>`.

No self-replicator is involved: a screen of 2,000 random slots at 150,000 finds not one program that passes the self-replication test, and a program from this soup placed against random bytes leaves them untouched. What spread was not a program but a way of writing. The earliest long keys of the runaway, born around 143,000, look like `{..........>{{{.........{{{[{{{`: a run of `.` writes the byte under head0 into the cell under head1 again and again, `{` steps head1 along, and the next run of `.` paints it again. Such a program stamps its own first byte across its partner. Straight-line programs of this kind, executed once each and many bytes long, rewrite large parts of both tape halves on every encounter, and the bytes they spread are the instructions they are made of. The process feeds on itself until every program is a long straight line of `>`, `,` and `{` whose execution changes nothing any more, because every write copies a value onto an identical value. Every slot is unique in its exact sequence, and nothing moves. The higher-order entropy is negative here, an artefact: the byte distribution is so concentrated that Brotli at quality 2 does not even reach the order-zero bound on a 2 MB sample.

So the full arc of one seed, 150,000 epochs: random soup, a replicator, a parasite that ate it, a desert of debris, and finally a crystal. Life, in the sense of self-replication, existed for about 17,000 epochs of it.

## It happened again: a second seed

The Mac mini's statistics run `peters-mac-mini-m4-pro-20260906-a`, same protocol, different seed, went through the same arc faster. From its log and checkpoints:

| Epoch | What the soup looked like |
|---|---|
| 37,888 | random background, 115k distinct keys, no replicator in a screen of 1,000 slots |
| 39,936 | 18k distinct keys, entropy 2.65. Replicators in 3.4% of a random screen, but the three most common keys, `<[[,<},,}<,[[[[<` and variants at 4.4%, 2.9% and 1.9%, have no closing bracket and score 0: loopless variants of the replicator, already outnumbering it. Only one `]` per 1,000 soup bytes. |
| 42,496 | no replicator left in a screen of 1,000 slots; `]` gone from the soup; the loopless variants decaying |
| 48,128 | desert: 126k distinct keys, `]`, `+`, `-` absent, entropy near zero |
| 55,552 | crystal forming: `}` 100, `<` 77 and `{` 44 per 1,000 bytes, 130k distinct keys, entropy negative |

Emergence at about 37,500, hosts extinct by 42,000, desert by 48,000, crystal by 55,000: the macbook-1 sequence compressed into a third of the time. In this run the replicators never held more than 6.5 percent of the soup, so no takeover criterion fired at all; the loopless variants took the soup directly. This is why the statistics now record the emergence, self-replicators in 1 percent of the soup, as its own event.

## Watch it happen in the stepper

The viewer's Stepper tab replays the hijack with real bytes, instruction by instruction. It needs the run directory, which anyone can regenerate because the simulation is deterministic: the seed fixes the initial soup and every epoch's pairing. About 20 minutes on an M4 Pro, byte-identical to the original:

```bash
python3 bff_soup.py --seed macbook-1 --num 131072 --max-steps 8192 --metric-sample 32768 --epochs 60000
```

1. Start the viewer with `python3 bff_web.py` and pick run `macbook-1`. Open the **Stepper** tab.
2. Type the parasite's key `[..{>>{..[` into **Program A**, the replicator's key `[..{>]]>{..[` into **Program B**, and `56000` into **at epoch** (both species are abundant in the checkpoint at 55,808; at the latest checkpoint the replicator is extinct).
3. Click **A from soup**, then **B from soup**. Each field now holds the raw 64 bytes of a real instance as hex. Real bytes matter: a key typed as a bare instruction string is padded with zeros, and the copy loop stops at the first zero it meets.
4. Click **Load**. The tape shows the parasite in the first half, the replicator in the second. Both heads start on cell 0.
5. **Step** through the parasite's prefix and watch the heads: `{` moves head1 (red) to cell 127, the two `>` move head0 (blue) to cell 2, the next `{` brings head1 to cell 126, the two `.` write cell 2 into cell 126, and the final `[` enters a loop that has no end.
6. Keep stepping. The program counter (green outline) walks through the parasite's data bytes and crosses into the second half at step 64, with head0 still on cell 2 and head1 on cell 126. The replicator's own `[..{>]]>` now executes.
7. Click **Play**. Each iteration of the replicator's loop copies one byte from head0, moving right through the parasite, to head1, moving left through the replicator's own body. The orange "just written" marker walks down through the second half. When execution halts, **keys now** shows `[..{>>{..[` in both halves: the parasite has been copied over the host by the host's loop.
8. Click **Swap A/B**, then **Load**: with the replicator first, its loop copies the replicator over the parasite instead. Whoever runs first wins.
9. Click **Test self-replication**: the replicator scores 63 of 64, the parasite 0. The parasite reproduces only in the presence of a host.
10. To see where parasites come from, go to the **Search** tab, search for `[..{>>{..[`, open its details and click **Watch its birth in the stepper**. That loads the tape of epoch 49,473 in which a background program ran first, fell through into a replicator in the second half, and left behind the first parasite.

## Reproducing this analysis

The run is deterministic: seed `macbook-1`, protocol `128k-8192`, reproduces it on any machine.

```bash
python3 bff_query.py species runs/macbook-1 '[..{>>{..['                    # birth and history of the parasite
python3 bff_query.py lineage runs/macbook-1 '[..{>>{..[' --depth 3          # its parent is the replicator, edit distance 2
python3 bff_query.py tape    runs/macbook-1 --epoch 49473 --slot 71767      # the tape on which the parasite was born
python3 bff_web.py                                                          # Stepper tab: replay that birth step by step
```
