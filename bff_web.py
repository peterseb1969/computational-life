#!/usr/bin/env python3
"""
Local web viewer for BFF runs.

Serves the single-page app in web/ and a JSON API over bff_query.Run.
Reads are safe while a simulation is writing the run directory.

Usage:
    python bff_web.py [--runs runs] [--port 8765]
    then open http://localhost:8765/
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np

import bff_core as core
from bff_query import Run, _json_default
from bff_lineage import RunDir

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
MAX_STEPS_DEFAULT = core.DEFAULT_MAX_STEPS


class RunRegistry:
    """Caches Run objects and the parsed log; one lock serialises all queries."""

    def __init__(self, runs_root):
        self.root = runs_root
        self.runs = {}
        self.log_cache = {}
        self.lock = threading.Lock()

    def list_runs(self):
        out = []
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                rd = RunDir(os.path.join(self.root, name))
                if rd.exists():
                    meta = rd.read_meta()
                    out.append({'name': name, 'num_programs': meta.get('num_programs'),
                                'seed': meta.get('seed'), 'created': meta.get('created'),
                                'last_epoch': self.get(name).last_epoch(),
                                'finished': meta.get('finished'), 'state': run_state(rd, meta)})
        return out

    def get(self, name):
        if '/' in name or name.startswith('.'):
            raise KeyError(name)
        if name not in self.runs:
            self.runs[name] = Run(os.path.join(self.root, name))
        return self.runs[name]

    def log(self, name):
        run = self.get(name)
        st = os.stat(run.rd.log_path)
        key = (st.st_size, st.st_mtime)
        cached = self.log_cache.get(name)
        if cached and cached[0] == key:
            return cached[1]
        data = run.log()
        self.log_cache[name] = (key, data)
        return data


REGISTRY = None


def run_state(rd, meta):
    """finished (with reason) / running / stalled, from the finish flag and the log's age."""
    try:
        age = time.time() - os.path.getmtime(rd.log_path)
    except OSError:
        age = None
    if meta.get('finished'):
        st = meta.get('stop_triggered')
        reason = st['reason'] if st else f"reached the epoch cap ({meta.get('max_epochs')})"
        return {'state': 'finished', 'reason': reason, 'epoch': meta.get('last_epoch'), 'log_age_s': age}
    if age is not None and age > 60:          # a running soup writes the log several times a second
        return {'state': 'stalled', 'reason': f"no log update for {age / 60:.0f} minutes (crashed or paused?)", 'log_age_s': age}
    return {'state': 'running', 'reason': None, 'log_age_s': age}


def safe_ints(obj):
    """JavaScript numbers lose precision above 2**53: send 64-bit hashes as strings."""
    if isinstance(obj, dict):
        return {k: safe_ints(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe_ints(v) for v in obj]
    if isinstance(obj, (int, np.integer)) and not isinstance(obj, bool) and abs(int(obj)) > (1 << 53):
        return str(int(obj))
    return obj


def api(path, q):
    """Dispatch an API path to a JSON-serialisable result."""
    parts = path.strip('/').split('/')
    if parts == ['api', 'runs']:
        return REGISTRY.list_runs()
    if len(parts) < 4 or parts[0] != 'api' or parts[1] != 'run':
        raise KeyError(path)
    name, what = parts[2], parts[3]
    run = REGISTRY.get(name)
    g = lambda k, d=None: q.get(k, [d])[0]
    gi = lambda k, d=None: int(g(k)) if g(k) not in (None, '') else d

    if what == 'info':
        run.reload_meta()
        return {'name': name, 'meta': run.meta, 'last_epoch': run.last_epoch(), 'state': run_state(run.rd, run.meta),
                'checkpoints': [e for e, _ in run.checkpoints()],
                'recorded_species': run.db.execute("SELECT COUNT(*) FROM species").fetchone()[0],
                'change_records': int(run.changes().shape[0])}

    if what == 'log':
        data = REGISTRY.log(name)
        ep = data['epoch']
        lo, hi = gi('from', int(ep[0])), gi('to', int(ep[-1]))
        maxp = gi('max_points', 3000)
        sel = np.flatnonzero((ep >= lo) & (ep <= hi))
        if sel.size > maxp:
            sel = sel[np.linspace(0, sel.size - 1, maxp).astype(int)]
        cols = g('cols')
        cols = cols.split(',') if cols else list(data.keys())
        return {c: data[c][sel] for c in cols if c in data}

    if what == 'top':
        epoch, rows = run.top_species(gi('epoch'), gi('n', 25))
        return {'epoch': epoch, 'species': rows}

    if what == 'snapshots':
        return [e for (e,) in run.db.execute("SELECT DISTINCT epoch FROM species_counts ORDER BY epoch")]

    if what == 'timeline':
        n = gi('n', 15)
        lo, hi = gi('from', 0), gi('to', 10 ** 9)
        top = run.db.execute("""SELECT hash, MAX(count) AS peak FROM species_counts
                                WHERE epoch BETWEEN ? AND ? GROUP BY hash ORDER BY peak DESC LIMIT ?""",
                             (lo, hi, n)).fetchall()
        hashes = [r['hash'] for r in top]
        epochs = [e for (e,) in run.db.execute(
            "SELECT DISTINCT epoch FROM species_counts WHERE epoch BETWEEN ? AND ? ORDER BY epoch", (lo, hi))]
        series = []
        for h in hashes:
            counts = dict(run.db.execute("SELECT epoch, count FROM species_counts WHERE hash = ? AND epoch BETWEEN ? AND ?",
                                         (h, lo, hi)).fetchall())
            series.append({'hash': core.to_unsigned(h), 'key': run.key_of(core.to_unsigned(h)),
                           'counts': [counts.get(e, 0) for e in epochs]})
        return {'epochs': epochs, 'series': series, 'num_programs': run.num_programs}

    if what == 'search':
        return run.search(g('q', ''), g('mode', 'substring'), gi('max_dist', 2), gi('limit', 50),
                          gi('min_count', 0), gi('epoch'))

    if what == 'species':
        return run.species(g('key'))

    if what == 'lineage':
        return run.trace_species(g('key'), gi('depth', 8))

    if what == 'slot':
        slot = gi('slot')
        if g('trace'):
            return run.trace_instance(gi('before', run.last_epoch()), slot, gi('depth', 6))
        return run.slot_history(slot, gi('before'), gi('after'))

    if what == 'tape':
        t = run.tape_at(gi('epoch'), gi('slot'))
        t['before'] = t['before'].tolist()
        t['after'] = t['after'].tolist()
        t['max_steps'] = run.max_steps
        t['heads'] = run.heads
        return t

    if what == 'program':
        # raw bytes of one representative program of a species from the nearest checkpoint
        h = Run.resolve_hash(g('key'))
        epoch = gi('epoch', run.last_epoch())
        ck_epoch, soup = run.checkpoint_at_or_before(epoch)
        hashes, _ = core.compute_keys(soup)
        hit = np.flatnonzero(hashes == h)
        if hit.size == 0:
            return None
        return {'epoch': ck_epoch, 'slot': int(hit[0]), 'program': soup[hit[0]].tolist(),
                'count': int(hit.size), 'max_steps': run.max_steps, 'heads': run.heads}

    if what == 'selfrep':
        progs = np.array(json.loads(g('programs')), dtype=np.uint8).reshape(-1, core.TAPE_SIZE)
        scores = core.selfrep_test(progs, seed=gi('seed', 0), max_steps=gi('max_steps', run.max_steps), heads_init=run.heads)
        return {'scores': scores.tolist(), 'threshold': core.SELFREP_THRESHOLD}

    raise KeyError(path)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime('%H:%M:%S'), fmt % args))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith('/api/'):
            t0 = time.time()
            try:
                with REGISTRY.lock:
                    result = api(u.path, parse_qs(u.query))
                body = json.dumps(safe_ints(result), default=_json_default).encode()
                status = HTTPStatus.OK
            except KeyError as e:
                body = json.dumps({'error': f'not found: {e}'}).encode()
                status = HTTPStatus.NOT_FOUND
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                body = json.dumps({'error': f'{type(e).__name__}: {e}'}).encode()
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            self.log_message("%s %s %.2fs", status.value, u.path, time.time() - t0)
            return
        if u.path == '/':
            self.path = '/index.html'
        super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def main(argv=None):
    global REGISTRY
    p = argparse.ArgumentParser(description="BFF run viewer")
    p.add_argument('--runs', default='runs', help='directory containing run directories')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--no-browser', action='store_true')
    a = p.parse_args(argv)
    REGISTRY = RunRegistry(a.runs)
    server = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    url = f"http://localhost:{a.port}/"
    print(f"BFF viewer on {url}  (runs from {os.path.abspath(a.runs)})")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
