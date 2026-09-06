#!/usr/bin/env python3
"""
Real-time terminal visualization of BFF experiment progress.

Reads the metrics log of a run and draws entropy and compression as ASCII
graphs, refreshing every 2 seconds.

Usage:
    python visualize_bff.py runs/42              # a run directory
    python visualize_bff.py runs/42/log.csv      # or the log file itself
    python visualize_bff.py bff_run.log          # cubff CSV logs work too
    python visualize_bff.py runs/42 --last 500   # zoom in on the last 500 epochs
"""

import os
import sys
import time

# column aliases so both this simulator's log and cubff's log can be read
TRANSITION_ENTROPY = 3.0     # a run has transitioned once entropy exceeded this
COLLAPSE_ENTROPY = 2.5       # ... and has collapsed if entropy later falls below this

ALIASES = {
    'brotli_size': 'compressed_size',
    'soup_size': 'soup_bytes',
    'num_programs': 'num_programs',
}


def read_log(filepath):
    """Read a CSV log with a header. Returns dict of column -> list."""
    data = {}
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return data
    if len(lines) < 2:
        return data
    cols = [ALIASES.get(c.strip(), c.strip()) for c in lines[0].split(',')]
    data = {c: [] for c in cols}
    for line in lines[1:]:
        parts = line.strip().split(',')
        if len(parts) != len(cols):
            continue
        try:
            for c, v in zip(cols, parts):
                data[c].append(float(v) if c != 'epoch' else int(v))
        except ValueError:
            continue
    # bits per byte: derive from compressed size when not logged directly
    if 'bpb' not in data and 'compressed_size' in data:
        if 'soup_bytes' in data:
            nbytes = data['soup_bytes']
        elif 'num_programs' in data:  # legacy Python log: third column was the sample size
            nbytes = [n * 64 for n in data['num_programs']]
        else:
            nbytes = None
        if nbytes:
            data['bpb'] = [s * 8 / n if n else 0 for s, n in zip(data['compressed_size'], nbytes)]
    return data


def ascii_graph(values, width=60, height=15, title="", min_epoch=0, max_epoch=None):
    """Create an ASCII graph of values."""
    if not values:
        return f"  {title}\n  No data yet..."

    if max_epoch is None:
        max_epoch = len(values)

    min_val = min(values)
    max_val = max(values)
    if max_val == min_val:
        max_val = min_val + 1

    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
        width = len(sampled)

    lines = [f"  {title}", f"  {max_val:8.3f} ┤"]
    for row in range(height - 2, -1, -1):
        line = "           │"
        for val in sampled:
            normalized = (val - min_val) / (max_val - min_val) * (height - 1)
            if int(normalized) >= row:
                line += "█" if val > 1.0 else "▄"
            else:
                line += " "
        lines.append(line)

    lines.append(f"  {min_val:8.3f} ┤" + "─" * width)
    lines.append(f"           └{'─' * (width // 2)}┬{'─' * (width // 2)}")
    start_label = str(min_epoch)
    lines.append(f"            {start_label}{' ' * (width // 2 - len(start_label))}epochs"
                 f"{' ' * (width // 2 - 6)}{max_epoch}")
    return "\n".join(lines)


def status_lines(data):
    """Status summary from the last log row."""
    if not data.get('epoch'):
        return ["Waiting for data..."]
    last = {k: v[-1] for k, v in data.items() if v}
    entropy = last.get('higher_entropy', 0.0)
    max_entropy = max(data['higher_entropy']) if data.get('higher_entropy') else 0.0
    if max_entropy < 1.0:
        status = "🔴 Pre-life"
    elif max_entropy <= TRANSITION_ENTROPY:
        status = "🟡 Evolving..."
    elif entropy < COLLAPSE_ENTROPY:
        status = f"🟠 COLLAPSED (entropy back below {COLLAPSE_ENTROPY} after a transition)"
    else:
        status = "🟢 TRANSITION DETECTED!"
    line1 = (f"Epoch: {last['epoch']:,} | Entropy: {entropy:.4f} | Max Entropy: {max_entropy:.4f}"
             f" | Bits/byte: {last.get('bpb', 0.0):.2f} | {status}")
    extras = []
    if 'ops_per_pair' in last:
        extras.append(f"Ops/pair: {last['ops_per_pair']:.0f}")
    if 'unique_species' in last:
        extras.append(f"Species: {int(last['unique_species']):,}")
    if 'top_share' in last:
        extras.append(f"Top species: {100 * last['top_share']:.1f}% (len {int(last.get('top_key_len', 0))})")
    if 'selfrep_slots' in last and last['selfrep_slots'] >= 0:
        extras.append(f"Self-replicating slots: {int(last['selfrep_slots']):,}")
    if 'elapsed_s' in last:
        extras.append(f"Elapsed: {last['elapsed_s'] / 60:.1f} min")
    return [line1] + ([" | ".join(extras)] if extras else [])


def resolve_log_path(arg):
    if os.path.isdir(arg):
        return os.path.join(arg, 'log.csv')
    return arg


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_bff.py <run_dir|log_file> [--last N]")
        sys.exit(1)

    log_file = resolve_log_path(sys.argv[1])

    last_n = None
    if len(sys.argv) >= 4 and sys.argv[2] == "--last":
        try:
            last_n = int(sys.argv[3])
        except ValueError:
            print("Error: --last requires a number")
            sys.exit(1)

    zoom_msg = f" (last {last_n} epochs)" if last_n else ""
    print(f"\n🧬 BFF Experiment Visualizer{zoom_msg}")
    print(f"📁 Monitoring: {log_file}")
    print("Press Ctrl+C to stop\n")

    print("\033[2J", end="")          # clear once; later frames overwrite in place (no flicker)
    try:
        while True:
            out = []
            data = read_log(log_file)
            if last_n and data.get('epoch'):
                for key in data:
                    data[key] = data[key][-last_n:]

            out.append("=" * 75)
            out.append(f"  BFF PRIMORDIAL SOUP EXPERIMENT - REAL-TIME MONITOR{zoom_msg}")
            out.append("=" * 75)
            out.append("")

            epochs = data.get('epoch', [])
            min_ep = epochs[0] if epochs else 0
            max_ep = epochs[-1] if epochs else 0
            out.append(ascii_graph(data.get('higher_entropy', []), width=60, height=12,
                                   title="Higher-Order Entropy (complexity metric)",
                                   min_epoch=min_ep, max_epoch=max_ep))
            out.append("")
            out.append(ascii_graph(data.get('bpb', []), width=60, height=8,
                                   title="Bits per Byte (compression - lower = more structure)",
                                   min_epoch=min_ep, max_epoch=max_ep))
            out.append("")
            out.append("-" * 75)
            out += status_lines(data)
            out.append("-" * 75)
            out.append("")
            out.append("📊 What to look for:")
            out.append("   • Entropy above 3 = Phase transition (life emerges!)")
            out.append("   • Bits per byte drop = Structure forming (replicators taking over)")
            out.append("   • Entropy falling below 2.5 again = the replicators are gone")
            out.append("")
            out.append(f"⏱️  Last update: {time.strftime('%H:%M:%S')}")
            # home the cursor, print every line padded and cleared to its end, then clear below
            frame = "\033[H" + "\n".join(line + "\033[K" for line in "\n".join(out).split("\n")) + "\033[J"
            sys.stdout.write(frame)
            sys.stdout.flush()
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nStopped monitoring.")
        data = read_log(log_file)
        if data.get('higher_entropy'):
            max_ent = max(data['higher_entropy'])
            print("\n📈 Final Summary:")
            print(f"   Total epochs: {data['epoch'][-1] if data.get('epoch') else 0:,}")
            print(f"   Max entropy reached: {max_ent:.4f}")
            print(f"   Transition occurred: {'Yes! 🎉' if max_ent > 3.0 else 'No'}")


if __name__ == "__main__":
    main()
