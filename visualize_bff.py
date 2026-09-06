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
    status = ("🔴 Pre-life" if max_entropy < 1.0 else
              "🟢 TRANSITION DETECTED!" if max_entropy > 3.0 else "🟡 Evolving...")
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

    try:
        while True:
            os.system('clear' if os.name != 'nt' else 'cls')
            data = read_log(log_file)
            if last_n and data.get('epoch'):
                for key in data:
                    data[key] = data[key][-last_n:]

            print("=" * 75)
            print(f"  BFF PRIMORDIAL SOUP EXPERIMENT - REAL-TIME MONITOR{zoom_msg}")
            print("=" * 75)
            print()

            epochs = data.get('epoch', [])
            min_ep = epochs[0] if epochs else 0
            max_ep = epochs[-1] if epochs else 0
            print(ascii_graph(data.get('higher_entropy', []), width=60, height=12,
                              title="Higher-Order Entropy (complexity metric)",
                              min_epoch=min_ep, max_epoch=max_ep))
            print()
            print(ascii_graph(data.get('bpb', []), width=60, height=8,
                              title="Bits per Byte (compression - lower = more structure)",
                              min_epoch=min_ep, max_epoch=max_ep))
            print()
            print("-" * 75)
            for line in status_lines(data):
                print(line)
            print("-" * 75)
            print("\n📊 What to look for:")
            print("   • Entropy spike to 4-6 = Phase transition (life emerges!)")
            print("   • Bits per byte drop = Structure forming (replicators taking over)")
            print(f"\n⏱️  Last update: {time.strftime('%H:%M:%S')}")
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
