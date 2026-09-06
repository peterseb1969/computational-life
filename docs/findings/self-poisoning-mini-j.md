# A replicator that poisons its own soup

*Mac mini, run `peters-mac-mini-m4-pro-20260906-j`, protocol `128k-8192-heads` (131,072 programs, 8192 steps, no mutation, the first two bytes of a tape set the heads). Analysed at epoch 47,000 while the run was still going.*

The run's only event is a spike at epoch 3,200: within 50 epochs a ten-instruction replicator went from nothing to 22 percent of the soup, and within the next 100 it was gone. What it left behind is a soup in which no replicator of its kind can ever grow again, and the small self-replicating species that keep appearing afterwards (35 to 500 slots, every few hundred epochs) are its own fragments, dead on arrival.

## What appeared

The species `[,{<][,{<]`, first seen at epoch 3,208 (4 copies at 3,210, none at 3,205). Its most common instance, bytes to paste into the stepper:

```
ff1ff850f8f0795f5b462c7b00237490653c5d3d000101010101010001010000ff1ff850f8f0795f5b462c7b00237490653c5d3d000101010101010001010000
```

The 64 bytes are the same 32-byte unit twice. The first two bytes set the heads: head0 at 255, which wraps to 127, the last byte of the tape; head1 at 31, the last byte of the unit. The loop `[,{<]` then runs backwards: while the byte under head0 is not zero, copy the byte under head1 onto it and move both heads one step down. So it writes the unit onto the partner's second half, then, with head1 wrapped round to the bytes it has just written, onto the partner's first half, and stops at the first zero it meets under head0 (the partner's byte 63, which is now the unit's last byte, a zero, once the partner is a copy). Against a partner with no zero byte, the partner becomes an exact copy in 260 steps:

```
393939393939393939393939393d2c393c3838393838383838de2cde5dde2c28d5de0e87199142329095907236642c4e4d38f25d611f2322c1c18538a73e3858
```

Paste the replicator into A and this partner into B, Load, Play: both halves end as the replicator. Two replicators on one tape do nothing to each other, because the copy stops before its first write.

Growth was explosive. Copies with the key and intact head bytes, by exact replay:

| Epoch | 3,210 | 3,225 | 3,240 | 3,250 | 3,260 | 3,280 | 3,300 | 3,350 | 3,500 |
|---|---|---|---|---|---|---|---|---|---|
| live copies | 3 | 84 | 2,597 | 14,752 | 22,578 | 10,478 | 3,098 | 4 | 0 |
| all copies of the key | 4 | 98 | 2,931 | 17,344 | 29,546 | 19,531 | 11,656 | 5,923 | 4,160 |

Threefold every five epochs up to 17 percent of the soup, then a collapse just as fast. The key itself lingered as dead debris for thousands of epochs.

## Why it disappeared

The loop's exit test is on the *destination*. The replicator only writes onto bytes that are not zero, so it can only convert a tape that contains no zero at all in the region it overwrites. Pairing the replicator, run first, against partners taken from the soup at epoch 3,200:

| Partner | Becomes a live copy |
|---|---|
| no zero byte anywhere (61 percent of the soup then) | 1,220 of 1,220 |
| at least one zero byte | 46 of 780 |

And every copy it makes carries five zeros into a tape that had none. Tapes without a zero byte:

| Epoch | 0 | 2,048 | 3,200 | 3,250 | 3,300 | 4,096 | 10,240 onwards |
|---|---|---|---|---|---|---|---|
| zero-free tapes | 78% | 64% | 61% | 33% | 0.3% | 0.1% | 0.0% |
| zero bytes per tape | 0.2 | 0.7 | 0.8 | 4.0 | 13.2 | 21.9 | 27 to 31 |

The replicator ate its own food supply in a hundred epochs. That alone would have capped it; what killed it was its debris. A copy that lands on a tape with a zero in the wrong place is written only partly, or has its head bytes clipped later by another program, and the result is a program with the same ten instructions but the wrong head bytes: a live copy's loop starting from the wrong positions. Such a program run first smears a shifted image of itself over its partner. This instance, from epoch 3,300, has heads 67 and 0:

```
43001615f8f0795f5b462c7b00237490653c5d0000010101010101008b640000ff1ff850f8f0795f5b462c7b00237490653c5d3d00640000f80101008b640001
```

Put it into A and the replicator into B, Load, Play: after 24 steps the replicator's first bytes read `01 00 00 43`. Its instructions are all still there, its head bytes are not, and it will never copy again. The asymmetry is complete:

| Tape | Outcome |
|---|---|
| replicator, zero-free partner | partner becomes a live copy: 100% |
| replicator, replicator | nothing changes |
| debris, replicator | replicator loses its head bytes: 27% |
| replicator, debris | debris repaired: 0% |

The replicator cannot repair its own debris, because the debris is full of zeros and the copy stops at the first one, while the debris keeps running the loop and destroys live copies. A census of every pairing in the replay from epoch 3,255 to 3,300 counts 18,744 births against 37,068 deaths; 82 percent of the deaths hit a live copy sitting in the second half of a tape behind one of its own dead relatives. Once the soup had no zero-free tapes left, births stopped and only the killing went on.

## The desert it left

At epoch 47,000 the soup holds 26,000 distinct keys, down from 93,000 before the spike and falling by about 150 per thousand epochs; the average tape has 31 zero bytes, 13 bytes of value one, and fewer than five instruction bytes, half of what it had before. The higher-order entropy is slightly negative. Nothing in the protocol adds new bytes: with no mutation, a byte value that has vanished from the soup is gone.

Self-replicating species keep appearing in this soup and vanishing again within one or two tests, and they are all the same thing: the loop `[,{<]` of the original unit, still carried around as the byte string `79 5f 5b 46 2c 7b 00 23 74 90 65 3c 5d` inside thousands of tapes, occasionally reassembled with head bytes that make it a copier again. Each passes the self-replication test perfectly, because that test uses random partners:

| Epoch | Species | Slots | Score against random partners |
|---|---|---|---|
| 10,240 | `-[,{<]` | 35 | 64 |
| 27,904 | `{<[,{<]` and `<{,[{,,<]` | 154 and 117 | 64 |
| 31,744 | `<[,{<]` | 496 | 64 |
| 46,592 | `<,{[,{<][,{` | 36 | 64 |

The largest of them, `<[,{<]` with heads 0 and 191, is a clean whole-tape copier: it converts 1,605 of 2,000 random partners. Against 2,000 partners drawn from its own soup at epoch 31,744 it converts none. Every one of these is a destination-tested copier, and the soup has no zero-free tape left for it to write on.

Bytes to paste for `<[,{<]`:

```
00bfc0790079005e3c00795f5b462c7b00237490653c5d0701000000000001000000000000000000000000000000000000000000000000000000000000000000
```

## What is different about this replicator

Every host found in the runs without head initialisation copies with `.` inside a loop like `[..{>]` or `[>..}]`: the loop's test falls on the source byte, so the copy length is fixed by the host's own bytes and does not depend on what the partner contains. The two heads-variant takeovers seen since (`..[}.>]..[}.` on the mini's run `-k`, `[<{,][<{,]` on the PoE Pi's run `-b`) confirm the rule from the other side. The Pi's winner even uses the same `,` and the same backwards copy as this spike, but its loop reads `<{,` and then `]`: it moves, writes, and only then tests, so the byte under test is the one it has just written, a byte of its own body. Its 32-byte unit contains no zero, its soup at the takeover had no zero byte in any tape, and it converts three quarters of random partners whether they contain zeros or not.

This spike's loop reads `[` first and `,{<` after: it tests the destination byte *before* overwriting it, so any zero in the target stops it, and its own body carries five zeros that every copy plants into a tape that had none. A copier that tests after writing is stopped only by its own bytes; one that tests before writing is stopped by the soup, and this one made the soup into its own stop signal. The heads variant makes such loops easy to build, because the two head bytes can place head0 at the far end of the tape without any head-moving instructions; this one needed four instructions.

## Reproducing the spike

The replicator does the same thing in a fresh random soup, without the 3,200 epochs of churn. Seed a soup with a single copy:

```
python3 bff_soup.py --seed-programs testdata/destination_copier.npy:1 --heads --num 8192 --epochs 120 --max-steps 8192 --run-dir /tmp/spike --no-archive
```

At epoch 32 the copier holds 27 percent of the 8,192 slots; at 64, 20 percent; at 96, 12 percent; the soup's zero-free tapes are gone and the copier with them.

## Two things this taught the tooling

- The self-replication test ran every 256 epochs and the spike lived between two of them (epochs 3,208 to 3,350, tests at 3,072 and 3,328). The outcome detector never saw a 22 percent emergence. The simulator now runs the test early whenever the largest species with at least five instructions holds a percent of the soup and has doubled since the last test.
- The logged parasite load reported 25 to 39 percent of the soup during the later blips. That was a metric artefact: a six-instruction replicator has the empty program and every one- and two-instruction program within four edits of it. Variants now count only within half the host's length.
