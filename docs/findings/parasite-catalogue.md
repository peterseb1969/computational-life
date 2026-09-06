# Catalogue of hosts and parasites

Every host and parasite pair encountered so far, with the raw bytes of a real instance of each so the pair can be replayed in the viewer's stepper without regenerating a run. All runs: protocol `128k-8192` (131,072 programs, 8192 steps per tape, no mutation, heads start at 0).

**How to replay a pair.** The hex strings below are the raw 64 bytes of real instances and can be pasted straight into the stepper:

1. Start the viewer with `python3 bff_web.py` and open the **Stepper** tab. No run is needed.
2. Paste the host's hex into **Program A** and the parasite's hex into **Program B** (or the other way round, to see the other order).
3. Click **Load**. Nothing happens until you do: the tape is built from the two fields only when Load is clicked.
4. Click **Step** to follow the heads instruction by instruction, or **Play** to run it through. The `keys now` box shows what each half has become when execution halts.
5. **Swap A/B** and **Load** again for the reverse order; **Test self-replication** scores both halves.

Real bytes matter: the same key typed as a bare instruction string is zero-padded, and a copy loop stops at the first zero it meets, so the outcome would differ from the tables below.

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

Not a parasite in the strict sense: preceding the host it destroys the host but is not copied over it. It reproduces only against background partners whose code happens to close its open loop, one in four. It kills hosts without living off them, and outnumbered them 4 to 1 by the end of the run. What the coexistence would have turned into after 60k is unknown.

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

## Not a parasite: run 45's `{<[[>.,,{,,.>[[<{`

The host `{<[[>.,,{]{,,.>[[<{` (19 instructions, palindrome, score 64, 20% of the soup at 60k) is accompanied by loopless variants such as `{<[[>.,,{,,.>[[<{` at 1 to 2%. Preceding the host, this variant destroys both halves in 94% of cases and is never copied; preceding background it destroys itself in 90%. A broken copy that neither reproduces nor spreads, kept at a low level only by being produced. Listed here as the counterexample: not every loopless variant is a parasite.

Host bytes, for comparison:

```
7b3c5b5b383e27372737295f37373d372e3b2c2c09122f3f39392a7b5d7b2a39393f2f12092c2c3b2e373d37375f29372737273e385b5b3c7b49273927272916
```

---

## What the cases have in common

- Every parasite is the host minus its loop-closing bracket, made continuously by hosts that end up in the second half of a tape behind a background program: the fall-through of the program counter runs the host's loop with foreign head positions and the host copies its partner over its own tail.
- The parasite's straight-line prefix reproduces the host's head configuration and its open `[` hands execution to the partner, so a following host copies the parasite over itself.
- Whoever runs first wins the host-parasite encounter; the difference is made by the background, which destroys hosts and spares parasites.
- The outcome depends on how far the host got before the parasite rose. A host that swept the background first (cases 1 and 2) was then eaten by its parasite, which starved in turn and left a desert. A host capped early by its parasite while the background was still the majority (case 4, and the unresolved coexistences in runs 46, mini `-d` and pi-1) settled into a standoff that 60,000 further epochs did not resolve.
- Across the thirteen emergences seen so far, clean takeovers are the minority: five takeovers, two parasite-driven extinctions, five coexistences, one fade.
