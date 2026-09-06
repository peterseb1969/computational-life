# Catalogue of hosts and parasites

Every host and parasite pair encountered so far, with the raw bytes of a real instance of each so the pair can be replayed in the viewer's stepper without regenerating a run. Cases 1 to 4: protocol `128k-8192` (131,072 programs, 8192 steps per tape, no mutation, heads start at 0). Case 5 is from the heads variant, `128k-8192-heads`, where the first two bytes of a tape set the heads; tick **heads** in the stepper before loading it.

**How to replay a pair.** The hex strings below are the raw 64 bytes of real instances and can be pasted straight into the stepper:

1. Start the viewer with `python3 bff_web.py` and open the **Stepper** tab. No run is needed.
2. Paste the host's hex into **Program A** and the parasite's hex into **Program B** (or the other way round, to see the other order).
3. Click **Load**. Nothing happens until you do: the tape is built from the two fields only when Load is clicked.
4. Click **Step** to follow the heads instruction by instruction, or **Play** to run it through. The `keys now` box shows what each half has become when execution halts.
5. **Swap A/B** and **Load** again for the reverse order; **Test self-replication** scores both halves.

Real bytes matter: the same key typed as a bare instruction string is zero-padded, and a copy loop stops at the first zero it meets, so the outcome would differ from the tables below.

**A note on the statistics' "parasite load".** The simulator logs, at every self-replication test, the slots held by species that fail the test but lie within four edits of one that passes. That number is a *candidate* load: it mixes strict parasites with killers and harmless debris in proportions that differ from run to run (macbook-1: almost all hijackers; run 46 and pi-1: mostly killers). `python3 bff_query.py variants <run> --epoch <e>` splits it for any checkpoint by running each variant ahead of the dominant host.

**Definitions.** A *host* is a self-replicator: it passes the self-replication test (13 random partners, 5 generations, at least 5 stable bytes). A *parasite* is a variant that fails the test, so it cannot copy on its own, yet is copied over the host when it precedes the host on a tape: it borrows the host's copy loop. All parasites found so far are the host's key minus the instruction that closes its loop, produced continuously by hosts that get damaged in the second half of a tape. Pair outcomes below are percentages of 200 pairings on the stated checkpoint; "bg" is the random background of that soup.

---

## 1. macbook-1: `[..{>]]>{..[` and `[..{>>{..[`

*MacBook, run `macbook-1`, checkpoint 55,808. Emergence 49.4k, host extinct 66.7k. Full story in [parasite-macbook-1.md](parasite-macbook-1.md).*

**Host** `[..{>]]>{..[`, 12 instructions, palindrome, self-replication score 63. Loop `[..{>]`: copies one byte per iteration from head0, moving right through itself, to head1, moving left from the end of the partner's half. Writes itself, reversed, over the partner. Bytes to paste into the stepper:

```
3a5a5b371b5a5a3b5a5a2e3a2e46037b3e5f663d3f3f3f3f3d3d66755f5d5d5f75663d3d3f3f3f3f3d665f3e7b03462e3a2e5a5a3b5a5a1b375b5a3a3a4a3a32
```

**Parasite** `[..{>>{..[`, 10 instructions, palindrome, score 0. The host minus `]]`, plus one `>`. Its straight-line prefix parks head0 on its own byte 2 and head1 on the partner's byte 126, exactly the host's working configuration, and its unmatched `[` hands execution to the partner. The host's loop then copies the parasite over the host. Bytes to paste:

```
5a3a3a5a5b371b5a5a3b5a5a2e3a2e46037b3e5f663d3f3f3f3f3d3d663d5f66663d5f3f663d3d3f3f3f3f3d665f3e7b03462e3a2e5a5a3b5a5a1b375b5a3a37
```

| Tape | Outcome |
|---|---|
| host, parasite | host, host: 100% |
| parasite, host | parasite, parasite: 94% |
| bg, host | host becomes the parasite: 84%, destroyed otherwise |
| bg, parasite | parasite survives: 46% |
| parasite, bg | parasite survives: 100%, and converts the background in 27% |

Fate: host extinct at 66.7k; parasite at 99% at 69k, eroded to 1% by 80k; desert; crystal from 141k.

---

## 2. Mac mini statistics run: `<[[,<},,],,}<,[<[[` and `<[[,<},,}<,[[[[<`

*Mac mini, run `peters-mac-mini-m4-pro-20260906-a`, checkpoint 39,936. Emergence about 37.5k, host extinct by 42k.*

**Host** `<[[,<},,],,}<,[<[[`, 18 instructions, score 64. Family of near-mirror variants such as `[<[,<},,],,}<,[[<`. Copies with `,` (head1 to head0), the reverse direction of host 1. Bytes to paste:

```
3c5b5b3737f637372c373c372122617d382c3d033a383338033a3a03353d352c5d2c353d35033a3a033833383a033d2c387d612221373c372c0f0f0f5b3c5b5b
```

**Parasite** `<[[,<},,}<,[[[[<`, 16 instructions, score 0. The host minus the `],,` that closes its loop. It held 4.4% of the soup at 39.9k while the host held 0.8%. Bytes to paste:

```
3c5b5b3737f637372c373c372122617d382c3d033a383338033a3a03353d35353d35033a3a033833383a033d2c387d612221373c372c373737375b5b4b5b5b3c
```

| Tape | Outcome |
|---|---|
| host, parasite | host, host: 100% |
| parasite, host | parasite, parasite: 100% |
| bg, host | host destroyed: 100% |
| bg, parasite | parasite destroyed: 100% |
| parasite, bg | parasite survives: 4% |

A harsher pairing than case 1: this host does not survive a single second-half encounter with the background, and the parasite is fragile too, so both collapsed within 4,500 epochs of the emergence. Fate: hosts gone by 42k, desert by 48k, crystal forming at 55k.

---

## 3. Run 46: `{[[[[[[[[[}<,,,,,,,,,,]],,,,,,,,,,<}[[[[[[[[{` and `{[[[[[[[[}<,,,,,,,,,,,,,,,,,,,,<}[[[[[[[[{`, a killer rather than a parasite

*MacBook, run `46`, checkpoint 59,904. Emergence 43.3k, transition 45.1k, run ended at 60k with the pair coexisting.*

**Host** `{[[[[[[[[[}<,,,,,,,,,,]],,,,,,,,,,<}[[[[[[[[{`, 45 instructions, score 64, a mirror pair holding 7.6% each at the end. Bytes to paste:

```
7b3d5c5b5b5b5b5b5b5b5b5b497d3b3c31692c2c2c2c2c2c422c2c2c2c6969315d5d3169692c2c2c2c422c2c2c2c2c2c69313c3b7d495b5b5b5b5b5b5b5b7b49
```

**Variant** `{[[[[[[[[}<,,,,,,,,,,,,,,,,,,,,<}[[[[[[[[{`, 42 instructions, score 0. The host minus `]]`. The single most common key of the run at 31%. Bytes to paste:

```
497b5b5b5b5b5b5b5b5b497d3b3c31692c2c2c2c2c2c422c2c2c2c6969313169692c2c2c2c422c2c2c2c2c2c69313c3b7d495b5b5b5b5b5b5b5b7b49493d3d49
```

| Tape | Outcome |
|---|---|
| host, variant | host, host: 100% |
| variant, host | host destroyed, variant not copied: 100% |
| bg, host | host destroyed: 73%, survives: 17% |
| bg, variant | variant survives: 28% |
| variant, bg | variant survives: 100%, and converts the background in 24% |

A killer rather than a strict parasite, but by the same mechanism. Traced on real instances: the variant's own code writes nothing useful; its `{`, `}` and `<` place head1 at the start of its own half and head0 at the end of the host's, then the program counter runs into the host, whose loop copies the variant's bytes, reversed, over the host's tail. That is exactly the hijack of case 1. The difference is the extent: the host's loop-closing `]]` lie in the overwritten tail, but the host's head is preserved, so the product is not the variant's own key but a hybrid, `{[[[[[[[[[}<,,,,,,,,,,,,,,,,,,,,<}[[[[[[[[{{`, host head plus variant body, two edits from the variant. That hybrid has no closing bracket either and kills hosts in 100 of 100 tests. So the killer does not copy its sequence; it converts hosts into new killers. Against random partners it reproduces in 0 of 300 cases, with or without a `]` in the partner: like the hijacker it is an obligate user of the host's loop. (An earlier version of this entry claimed reproduction off background loops one time in four; that measurement used a background sample contaminated with family members and was wrong.) It outnumbered the hosts 4 to 1 by the end of the run; what the coexistence would have turned into after 60k is unknown.

---

## 4. MacBook `-b`: `{<[}<,,],,<}[<{` and `{<[}<,,,,<}[<{{`, a standoff

*MacBook, run `ps-macbook-pro-20260906-b`, 100,000 epochs. Emergence 17.9k; parasite crash by 20k; recovery from 22k; host and parasite coexisting from 40k to the end.*

**Round one, epochs 17.9k to 20k.** The host `{<[}<,,],,<}[<{` (15 instructions, palindrome, score 63) reached 3,700 slots at 18.4k; within 250 epochs its loopless variants, `],,` turned into `,,,,`, outnumbered it. Both then crashed together, the host to about 500 slots by 20k, the parasites to 0.2 percent, because with hosts that scarce the parasites had nothing to reproduce from. Checkpoint 18,432:

Host, bytes to paste:

```
7bedd83d3cd8d81bd8d61b5b047dd83c20bd46a42c3a2c48a4244634bd44205d2044bd344624a4482c3a2ca446bd203cd87d045b1bd6d81bd8d83c3d7bd8d8d8
```

Parasite `{<[}<,,,,<}[<{{` (15 instructions, score 0), bytes to paste:

```
3d7b3bcded3cd8d81bd8d61b5b047dd83c20bd46a42c3a2c48a4244634bd44202c3a2ca446bd203cd87d045b1bd6d81bd8d83c3dd8edd87bedd67bd6edd6edd6
```

| Tape | Outcome |
|---|---|
| host, parasite | host, host: 100% |
| parasite, host | parasite, parasite: 98% |
| bg, host | both halves become the parasite: 60%; host survives: about 25% |
| host, bg | host, host: 100% |

**Round two, epochs 22k to 100k.** The survivors were the host with one extra `.`, `{<[}<,,],,<}[<.{` (16 instructions, score 63), and its mirror image `{.<[}<,,],,<}[<{`; not a palindrome, so copies alternate between the two forms. It is exactly as vulnerable as the round-one host: the parasite hijacks it 100 percent of the time and background damages it in three encounters out of four. It grew from 1,000 slots to 19,000 over 30,000 epochs and then stayed: from 40k to 100k the replicators wandered between 0.6 and 15 percent of the soup, the parasite load in lockstep at about 2.7 times the host count, the background never below 22,000 distinct keys. No trend, no cycle, no resolution in 60,000 epochs. Checkpoint 99,999:

Host, bytes to paste:

```
7b3cd8d81bd8d61b5b047dd83c20bd46a42c3a2c48a4244634bd44205d2044bd344624a4482c3a2ca446bd203cd87d045b1bd6d81bd8d83c3dd82e7bd6d6d6d6
```

Parasite `{<[}<,,,,<}[{{{` (15 instructions, score 0), bytes to paste:

```
7b3cd8d81bd8d61b5b047dd83c20bd46a42c3a2c48a4244634bd4420a424462044bd344624a4482c3a2ca446bd203cd87d045b1bd6d8d6d87b7b7bd6d6d6d6d6
```

Why this pair coexists where case 1 ended in extinction: the host never swept the background. In macbook-1 the host had converted 99 percent of the soup into copies of itself before the parasite rose, leaving the parasite a soup of nothing but hosts. Here the round-two host started from a thousand slots with parasites already present and was capped early, so the background stayed the majority and kept destroying parasites and hosts alike. Host beats background, parasite beats host, background beats both when it runs first: a three-way standoff that wanders instead of resolving.

---

## 5. Mac mini `-j` (heads): `[,{<][,{<]`, a replicator killed by its own debris

*Mac mini, run `peters-mac-mini-m4-pro-20260906-j`, protocol `128k-8192-heads`. Emergence 3.2k; 22 percent of the soup at 3,260; extinct by 3,500; the soup a zero-flooded desert ever since. Full account in [self-poisoning-mini-j.md](self-poisoning-mini-j.md).*

Not a host and parasite pair: the variants here reproduce nothing and hijack nothing. They are copies of the host with clipped head bytes, and they kill. The host is a 32-byte unit repeated twice; its head bytes put head0 at the end of the tape and head1 at the end of the unit, and the loop `[,{<]` copies the unit backwards over the partner while the destination byte is not zero. Tick **heads** in the stepper before Load.

Host, bytes to paste:

```
ff1ff850f8f0795f5b462c7b00237490653c5d3d000101010101010001010000ff1ff850f8f0795f5b462c7b00237490653c5d3d000101010101010001010000
```

A zero-free partner from the soup at epoch 3,200, bytes to paste:

```
393939393939393939393939393d2c393c3838393838383838de2cde5dde2c28d5de0e87199142329095907236642c4e4d38f25d611f2322c1c18538a73e3858
```

Killer debris `[,{<][,{<]` with heads 67 and 0 (same ten instructions, score 0), from epoch 3,300, bytes to paste:

```
43001615f8f0795f5b462c7b00237490653c5d0000010101010101008b640000ff1ff850f8f0795f5b462c7b00237490653c5d3d00640000f80101008b640001
```

| Tape | Outcome |
|---|---|
| host, zero-free partner | host, host: 100% |
| host, partner with a zero byte | partner converted: 6% |
| host, host | nothing changes |
| debris, host | host loses its head bytes: 27% |
| host, debris | debris repaired: 0% |

The host's loop tests the destination, so every zero blocks it and every copy it makes plants five zeros. Within a hundred epochs the soup had no zero-free tape left (61 percent before the spike, 0.3 percent after), births stopped, and the debris went on clipping the survivors' head bytes. The later self-replicating blips in this run (`-[,{<]`, `<[,{<]`, `<,{[,{<][,{`, 35 to 500 slots each) are the same loop reassembled; they score 64 against random partners and convert nothing in their own soup.

---

## Not a parasite: run 45's `{<[[>.,,{,,.>[[<{`

The host `{<[[>.,,{]{,,.>[[<{` (19 instructions, palindrome, score 64, 20% of the soup at 60k) is accompanied by loopless variants such as `{<[[>.,,{,,.>[[<{` at 1 to 2%. Preceding the host, this variant destroys both halves in 94% of cases and is never copied; preceding background it destroys itself in 90%. A broken copy that neither reproduces nor spreads, kept at a low level only by being produced. Listed here as the counterexample: not every loopless variant is a parasite.

Host bytes, for comparison:

```
7b3c5b5b383e27372737295f37373d372e3b2c2c09122f3f39392a7b5d7b2a39393f2f12092c2c3b2e373d37375f29372737273e385b5b3c7b49273927272916
```

---

## The control that changes the picture

Crossing every parasite and killer above with every host, across families, converts the host into a loopless variant in 100 percent of pairings. That universality is not a skill of the variants but a fragility of the hosts. Random programs placed before a host, 100 pairings each:

| Partner before the host | Host turned loopless | Host intact |
|---|---|---|
| all zeros | 0% | 100% |
| random bytes | 72 to 74% | 16 to 19% |
| random bytes with an unmatched `[` | 83% | 1 to 2% |
| random bytes containing no bracket at all | 90 to 91% | 1 to 3% |

The same four hosts, the same numbers within a few percent. A host that lands in the second half behind almost any program that moves a head is entered by fall-through with displaced heads, and its own loop then copies the partner's bytes over its own tail, where its closing brackets sit. Zeros do nothing because no instruction runs before the fall-through. So the parasites and killers do not carry a trick that spreads through the population; they are the products of the host's self-mutilation and mutilate hosts exactly as the random background does. What distinguishes a full parasite from the background is only that the host copies it *exactly*, because its instruction bytes lie inside the region the host's loop overwrites, so its count grows with every encounter; a killer's product is a different sequence each time. No information spreads from variant to host. The information that matters, the deletion of the loop closer, is produced afresh by every host that runs second.

## What the cases have in common

- Every parasite is the host minus its loop-closing bracket, made continuously by hosts that end up in the second half of a tape behind a background program: the fall-through of the program counter runs the host's loop with foreign head positions and the host copies its partner over its own tail.
- The parasite's straight-line prefix reproduces the host's head configuration and its open `[` hands execution to the partner, so a following host copies the parasite over itself.
- The mechanism is the host's, not the variant's: a random bracket-free program mutilates a host it precedes in 91 percent of cases. Hijackers and killers are one mechanism with two extents. When the overwritten region covers all of the host's instruction bytes, the product is the variant's exact key: a hijacker. When it covers the host's loop-closing brackets but not its head, the product is a loopless hybrid, a new killer of a slightly different sequence. Neither can reproduce without a host.
- Whoever runs first wins the host-parasite encounter; the difference is made by the background, which destroys hosts and spares parasites.
- The outcome depends on how far the host got before the parasite rose. A host that swept the background first (cases 1 and 2) was then eaten by its parasite, which starved in turn and left a desert. A host capped early by its parasite while the background was still the majority (case 4, and the unresolved coexistences in runs 46, mini `-d` and pi-1) settled into a standoff that 60,000 further epochs did not resolve.
- Across the thirteen emergences seen so far, clean takeovers are the minority: five takeovers, two parasite-driven extinctions, five coexistences, one fade.
- The heads variant adds a failure mode of its own (case 5): a copy loop that tests the destination byte *before* writing it is blocked by zeros, plants zeros with every copy if its body contains any, and cannot repair its own debris, which keeps running the loop and kills it. Loops that write first and test afterwards (`[..{>]`, `[<{,]`) are stopped only by their own bytes, and every takeover so far, heads or not, uses one of those.
