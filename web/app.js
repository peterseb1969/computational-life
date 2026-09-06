/* BFF Soup Viewer - single page app over the bff_web.py JSON API */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const state = { run: null, info: null, ovMode: 'all', snapshots: [], detail: null };

// ---------------------------------------------------------------- utilities
async function api(what, params = {}) {
  const q = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  const url = what === 'runs' ? '/api/runs' : `/api/run/${encodeURIComponent(state.run)}/${what}${q ? '?' + q : ''}`;
  const r = await fetch(url);
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  return j;
}
let toastTimer = null;
function toast(msg, sticky = false) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  if (!sticky) toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}
function hideToast() { $('#toast').hidden = true; }
const fmt = (n, d = 0) => n === null || n === undefined ? '-' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });
const pct = (x) => (100 * x).toFixed(2) + '%';
const showKey = (k) => (k === '' ? '(empty)' : k);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function plotLayout(title, extra = {}) {
  return Object.assign({
    title: { text: title, font: { size: 14 } }, margin: { l: 55, r: 15, t: 36, b: 40 },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: cssVar('--ink'), size: 12 },
    xaxis: { title: 'epoch', gridcolor: cssVar('--line') }, yaxis: { gridcolor: cssVar('--line') },
    showlegend: false, hovermode: 'x unified',
  }, extra);
}
const plotConfig = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };

// ------------------------------------------------------------------- tabs
$$('nav button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
function showTab(name) {
  $$('nav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab').forEach((t) => t.classList.toggle('active', t.id === 'tab-' + name));
  setHash({ tab: name });
  if (name === 'overview') loadOverview();
  if (name === 'species') loadSpecies();
  window.dispatchEvent(new Event('resize'));
}

// ------------------------------------------------------------------- runs
async function loadRuns() {
  const runs = await api('runs');
  const sel = $('#run-select');
  const tag = (r) => ({ running: ' ▶ running', stalled: ' ⚠ stalled', finished: ' ■ finished' }[r.state ? r.state.state : 'running']);
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.name)}">${esc(r.name)} — ${fmt(r.num_programs)} programs, epoch ${fmt(r.last_epoch)}${tag(r)}</option>`).join('');
  if (!runs.length) { toast('No runs found in the runs directory', true); return; }
  const want = hashParams().run;
  const pick = runs.find((r) => r.name === want) || runs.reduce((a, b) => (b.created > a.created ? b : a));
  sel.value = pick.name;
  await selectRun(pick.name);
  const tab = hashParams().tab;
  if (tab && $(`nav button[data-tab="${tab}"]`)) showTab(tab);
}
function hashParams() {
  const out = {};
  for (const kv of location.hash.replace(/^#/, '').split('&')) {
    const [k, v] = kv.split('='); if (k) out[decodeURIComponent(k)] = decodeURIComponent(v || '');
  }
  return out;
}
function setHash(changes) {
  const p = Object.assign(hashParams(), changes);
  location.hash = Object.entries(p).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
}
$('#run-select').addEventListener('change', (e) => selectRun(e.target.value));
async function selectRun(name) {
  state.run = name;
  setHash({ run: name });
  state.info = await api('info');
  const m = state.info.meta;
  $('#run-status').innerHTML = statusText(state.info);
  $('#ov-meta').textContent = JSON.stringify(m, null, 2);
  state.ovMode = 'all';
  loadOverview();
}

// --------------------------------------------------------------- overview
async function loadOverview() {
  if (!state.run) return;
  const info = await api('info');
  state.info = info;
  const last = info.last_epoch;
  let from = 0, to = last;
  if (state.ovMode === 'last') from = Math.max(0, last - 500);
  if (state.ovMode === 'range') { from = +$('#ov-from').value || 0; to = +$('#ov-to').value || last; }
  $('#ov-from').value = from; $('#ov-to').value = to;
  const d = await api('log', { from, to, max_points: 3000 });
  if (!d.epoch || !d.epoch.length) return;
  const x = d.epoch;
  const tIdx = d.higher_entropy.findIndex((v) => v > 3);
  const shapes = [];
  if (tIdx >= 0) {
    shapes.push({ type: 'line', x0: x[tIdx], x1: x[tIdx], y0: 0, y1: 1, yref: 'paper', line: { color: cssVar('--pc'), dash: 'dot' } });
    $('#ov-transition').textContent = `transition: entropy > 3 at epoch ${fmt(x[tIdx])}`;
  } else {
    $('#ov-transition').textContent = `no transition yet (max entropy ${Math.max(...d.higher_entropy).toFixed(2)})`;
  }
  const line = (div, y, title, color, extra = {}) =>
    Plotly.react(div, [{ x, y, type: 'scatter', mode: 'lines', line: { color, width: 1.5 }, hovertemplate: '%{y:.3f}<extra></extra>' }],
      plotLayout(title, Object.assign({ shapes }, extra)), plotConfig);
  line('ov-entropy', d.higher_entropy, 'Higher-order entropy (H0 − bits/byte after compression)', cssVar('--accent'));
  line('ov-bpb', d.bpb, 'Bits per byte after compression', cssVar('--h1'));
  line('ov-ops', d.ops_per_pair, 'Instructions executed per tape', cssVar('--written'));
  line('ov-species', d.unique_species, 'Unique species (distinct keys)', cssVar('--pc'));
  line('ov-share', d.top_share.map((v) => 100 * v), 'Top species share (%)', cssVar('--h0'));
  line('ov-selfrep', d.selfrep_slots.map((v) => (v < 0 ? null : v)), 'Slots holding a self-replicator (tested every 256 epochs)', cssVar('--h1'));
  $('#run-status').innerHTML = statusText(info);
}
function statusText(info) {
  const m = info.meta, s = info.state || { state: 'running' };
  const badge = { running: '<span class="state running">▶ running</span>', stalled: '<span class="state stalled">⚠ stalled</span>', finished: '<span class="state finished">■ finished</span>' }[s.state];
  let text = `${badge} ${fmt(m.num_programs)} programs · seed ${esc(m.seed_label || m.seed)} · epoch ${fmt(info.last_epoch)} · ${fmt(info.recorded_species)} recorded species`;
  if (m.emergence_epoch != null) text += ` · <b>emergence at ${fmt(m.emergence_epoch)}</b>`;
  if (s.state === 'finished') text += ` · stopped: ${esc(s.reason)}`;
  if (s.state === 'stalled') text += ` · ${esc(s.reason)}`;
  return text;
}
$('#ov-apply').addEventListener('click', () => { state.ovMode = 'range'; loadOverview(); });
$('#ov-last').addEventListener('click', () => { state.ovMode = 'last'; loadOverview(); });
$('#ov-all').addEventListener('click', () => { state.ovMode = 'all'; loadOverview(); });
setInterval(() => {
  if ($('#ov-auto').checked && $('#tab-overview').classList.contains('active') && state.info && state.info.state && state.info.state.state !== 'finished') loadOverview();
}, 6000);
// the header (state badge, epoch, emergence) and the run list refresh on every tab
async function refreshHeader() {
  if (!state.run || (state.info && state.info.state && state.info.state.state === 'finished' && !$('#tab-overview').classList.contains('active'))) return;
  try {
    const info = await api('info');
    state.info = info;
    $('#run-status').innerHTML = statusText(info);
    const opt = $('#run-select').querySelector(`option[value="${CSS.escape(state.run)}"]`);
    if (opt) {
      const tag = { running: ' ▶ running', stalled: ' ⚠ stalled', finished: ' ■ finished' }[info.state ? info.state.state : 'running'];
      opt.textContent = `${state.run} — ${fmt(info.meta.num_programs)} programs, epoch ${fmt(info.last_epoch)}${tag}`;
    }
  } catch (e) { /* server away: keep the last state */ }
}
setInterval(refreshHeader, 10000);

// ---------------------------------------------------------------- species
async function loadSpecies() {
  if (!state.run) return;
  await loadTimeline();
  state.snapshots = await api('snapshots');
  const r = $('#sp-epoch');
  r.min = 0; r.max = Math.max(0, state.snapshots.length - 1); r.value = r.max;
  loadSnapshot();
}
async function loadTimeline() {
  const n = +$('#sp-n').value || 15;
  const d = await api('timeline', { n, from: $('#sp-from').value, to: $('#sp-to').value });
  const share = $('#sp-share').checked;
  const scale = share ? 100 / d.num_programs : 1;
  const traces = d.series.map((s) => ({
    x: d.epochs, y: s.counts.map((c) => c * scale), name: s.key == null ? '#' + s.hash : showKey(s.key), type: 'scatter', mode: 'lines',
    stackgroup: 'one', line: { width: 0.5 }, hovertemplate: `${esc(s.key == null ? '#' + s.hash : showKey(s.key))}<br>%{y:.2f}${share ? '%' : ''}<extra></extra>`,
  }));
  Plotly.react('sp-timeline', traces, plotLayout(`Top ${n} species by peak count (${share ? 'share of soup, %' : 'count'})`,
    { showlegend: true, legend: { font: { family: cssVar('--mono'), size: 11 }, orientation: 'v', x: 1.01 }, margin: { r: 260 }, hovermode: 'closest' }), plotConfig);
}
$('#sp-apply').addEventListener('click', loadTimeline);
$('#sp-share').addEventListener('change', loadTimeline);
$('#sp-epoch').addEventListener('input', loadSnapshot);
async function loadSnapshot() {
  const epoch = state.snapshots[+$('#sp-epoch').value];
  if (epoch === undefined) return;
  const d = await api('top', { epoch, n: 40 });
  $('#sp-epoch-label').textContent = `epoch ${fmt(d.epoch)}`;
  $('#sp-table tbody').innerHTML = d.species.map((s) => `<tr>
    <td>${fmt(s.count)}</td><td>${pct(s.share)}</td><td>${s.length ?? '?'}</td><td>${s.first_epoch ?? '-'}</td>
    <td>${s.selfrep_score ?? '-'}</td><td class="key">${esc(s.key == null ? '(unknown short key)' : showKey(s.key))}</td>
    <td>${s.key !== null ? actionLinks(s.key, s.first_epoch !== null) : ''}</td></tr>`).join('');
}
function actionLinks(key, recorded) {
  const k = encodeURIComponent(key);
  return `<a onclick="showDetail(decodeURIComponent('${k}'))">details</a> · ` +
    (recorded ? `<a onclick="traceLineage(decodeURIComponent('${k}'))">lineage</a> · ` : '') +
    `<a onclick="runSpecies(decodeURIComponent('${k}'))">run</a>`;
}

// ----------------------------------------------------------------- search
$('#se-go').addEventListener('click', doSearch);
$('#se-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
async function doSearch() {
  const q = $('#se-q').value;
  const params = { q, mode: $('#se-mode').value, max_dist: $('#se-dist').value, limit: $('#se-limit').value,
    min_count: $('#se-min').value, epoch: $('#se-epoch').value };
  $('#se-status').textContent = 'searching…';
  try {
    const res = await api('search', params);
    const inCheckpoint = params.epoch !== '';
    $('#se-status').textContent = `${res.length} match${res.length === 1 ? '' : 'es'}${inCheckpoint && res.length ? ` in checkpoint at epoch ${res[0].epoch}` : ' among recorded species'}`;
    $('#se-table tbody').innerHTML = res.map((r) => inCheckpoint ? `<tr>
      <td>-</td><td>${fmt(r.count)}</td><td>${r.epoch}</td><td>-</td><td>${r.length}</td><td>-</td>
      <td class="key">${esc(r.key)}${r.distance != null ? ` <span class="muted">(dist ${r.distance})</span>` : ''}</td>
      <td>${actionLinks(r.key, false)}</td></tr>` : `<tr>
      <td>${fmt(r.first_epoch)}</td><td>${fmt(r.peak_count)}</td><td>${r.peak_epoch ?? '-'}</td><td>${fmt(r.latest_count)}</td>
      <td>${r.length}</td><td>${r.selfrep_score ?? '-'}</td>
      <td class="key">${esc(r.key)}${r.distance != null ? ` <span class="muted">(dist ${r.distance})</span>` : ''}</td>
      <td>${actionLinks(r.key, true)}</td></tr>`).join('');
  } catch (e) { $('#se-status').textContent = 'error: ' + e.message; }
}
async function showDetail(key) {
  showTab('search');
  const sp = await api('species', { key });
  const box = $('#se-detail');
  box.hidden = false;
  state.detail = sp;
  $('#sd-key').textContent = key;
  if (!sp) {
    $('#sd-origin').innerHTML = `<span class="muted">Not a recorded species (shorter than the tracking length, or never copied ${''}enough to be recorded). It can still be run in the stepper.</span>`;
    Plotly.purge('sd-counts');
    $('#sd-lineage').disabled = true; $('#sd-birth-tape').disabled = true;
    return;
  }
  $('#sd-lineage').disabled = false; $('#sd-birth-tape').disabled = sp.birth.initial;
  const b = sp.birth;
  $('#sd-origin').innerHTML = b.initial
    ? `Present in the initial soup (slot ${b.slot}). Peak ${fmt(sp.peak_count)} at epoch ${fmt(sp.peak_epoch)}, latest ${fmt(sp.latest_count)}.`
    : `Born in <b>epoch ${fmt(b.epoch)}</b>, slot ${b.slot} (${b.slot_position === 0 ? 'first' : 'second'} half of the tape), partner slot ${b.partner}.<br>
       The slot held <span class="mono">${esc(b.parent_key)}</span>${b.parent_tracked ? '' : ' <span class="muted">(background)</span>'},
       the partner held <span class="mono">${esc(b.partner_key)}</span>${b.partner_tracked ? '' : ' <span class="muted">(background)</span>'}.<br>
       Recorded at epoch ${fmt(sp.promoted_epoch)}. Peak ${fmt(sp.peak_count)} at epoch ${fmt(sp.peak_epoch)}, latest ${fmt(sp.latest_count)} (last seen ${fmt(sp.last_seen_epoch)}).
       ${sp.selfrep_score != null ? ` Best self-replication score ${sp.selfrep_score}/64.` : ''}`;
  Plotly.react('sd-counts', [{ x: sp.counts.map((c) => c[0]), y: sp.counts.map((c) => c[1]), type: 'scatter', mode: 'lines', line: { color: cssVar('--accent') }, name: 'count' },
    { x: sp.selfrep.map((c) => c[0]), y: sp.selfrep.map((c) => c[1]), type: 'scatter', mode: 'markers', marker: { color: cssVar('--h1') }, name: 'selfrep score', yaxis: 'y2' }],
    plotLayout('Count over time (blue) and self-replication score (red)', { margin: { t: 30, b: 30, l: 50, r: 40 }, yaxis2: { overlaying: 'y', side: 'right', range: [0, 66], showgrid: false } }), plotConfig);
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
$('#sd-lineage').addEventListener('click', () => traceLineage(state.detail.key));
$('#sd-birth-tape').addEventListener('click', () => watchBirth(state.detail.birth.epoch, state.detail.birth.slot));
$('#sd-run').addEventListener('click', () => runSpecies($('#sd-key').textContent));

// ---------------------------------------------------------------- lineage
$('#li-go').addEventListener('click', () => traceLineage($('#li-key').value));
$('#li-key').addEventListener('keydown', (e) => { if (e.key === 'Enter') traceLineage(e.target.value); });
async function traceLineage(key) {
  showTab('lineage');
  $('#li-key').value = key;
  $('#li-tree').innerHTML = '<span class="muted">tracing…</span>';
  try {
    const tree = await api('lineage', { key, depth: $('#li-depth').value });
    $('#li-tree').innerHTML = renderNode(tree, 'species', true);
  } catch (e) { $('#li-tree').innerHTML = `<span class="muted">error: ${esc(e.message)}</span>`; }
}
function renderNode(n, role, primary) {
  const k = encodeURIComponent(n.key || '');
  if (!n.tracked) {
    return `<div class="node leaf ${primary ? 'primary' : ''}"><div class="role">${role}</div>
      <div class="k">${esc(n.key == null ? '?' : showKey(n.key))}</div><div class="facts">${esc(n.note || 'not recorded')}${n.key ? ` · <a onclick="runSpecies(decodeURIComponent('${k}'))">run</a>` : ''}</div></div>`;
  }
  const b = n.birth || {};
  let facts = `len ${n.length} · peak <b>${fmt(n.peak_count)}</b>` + (n.selfrep_score != null ? ` · selfrep <b>${n.selfrep_score}</b>` : '');
  let body = '';
  if (b.initial) {
    facts += ` · <b>present in the initial soup</b> (slot ${b.slot})`;
  } else if (b.epoch !== undefined) {
    facts += ` · born <b>epoch ${fmt(b.epoch)}</b> in slot ${b.slot} (${b.slot_position === 0 ? 'first' : 'second'} half), partner slot ${b.partner}`;
    if (n.primary) {
      const d = (r) => (n[r + '_mirror'] ? `${n[r + '_mirror_distance']} as mirror image` : `${n[r + '_distance']}`);
      facts += ` · distance to parent ${d('parent')}, to partner ${d('partner')}` + (n.primary_mirror ? ' · <b>mirror copy</b>' : '');
    }
  }
  if (n.note && !b.initial) facts += ` · <i>${esc(n.note)}</i>`;
  const actions = `<div class="actions">
      <button onclick="showDetail(decodeURIComponent('${k}'))">details</button>
      ${!b.initial && b.epoch !== undefined ? `<button onclick="watchBirth(${b.epoch}, ${b.slot})">watch birth</button>` : ''}
      <button onclick="runSpecies(decodeURIComponent('${k}'))">run</button>
      <button onclick="traceLineage(decodeURIComponent('${k}'))">trace from here</button></div>`;
  if (n.parent) body += renderNode(n.parent, 'parent (slot held)', n.primary === 'parent');
  if (n.partner) body += renderNode(n.partner, 'partner (other half)', n.primary === 'partner');
  return `<div class="node ${primary ? 'primary' : ''}"><div class="role">${role}</div><div class="k">${esc(showKey(n.key))}</div>
    <div class="facts">${facts}</div>${actions}${body}</div>`;
}

// ---------------------------------------------------------------- stepper
const CMD_CHARS = { 60: '<', 62: '>', 123: '{', 125: '}', 43: '+', 45: '-', 46: '.', 44: ',', 91: '[', 93: ']' };
const keyOf = (bytes) => Array.from(bytes).filter((b) => CMD_CHARS[b]).map((b) => CMD_CHARS[b]).join('');

class BFF {
  constructor(tape, maxSteps, heads = false) { this.initial = Uint8Array.from(tape); this.max = maxSteps; this.heads = heads; this.reset(); }
  reset() { this.tape = Uint8Array.from(this.initial); this.pc = 0; this.h0 = 0; this.h1 = 0;
    if (this.heads) { this.h0 = this.tape[0] & 127; this.h1 = this.tape[1] & 127; this.pc = 2; } this.ops = 0; this.steps = 0; this.halted = null; this.lastWrite = -1; this.tortoise = -1; this.power = 1; this.lam = 0; }
  changed() { this.tortoise = -1; this.power = 1; this.lam = 0; }
  step() {
    if (this.halted) return false;
    if (this.steps >= this.max) { this.halted = 'step budget spent'; return false; }
    if (this.pc < 0 || this.pc >= 128) { this.halted = 'program counter left the tape'; return false; }
    this.steps++;
    this.h0 &= 127; this.h1 &= 127;
    // Brent's cycle detection over (pc, head0, head1) since the tape last changed (same as bff_core.evaluate)
    const state = (this.pc << 14) | (this.h0 << 7) | this.h1;
    if (state === this.tortoise) { this.halted = 'stuck in a loop that can never change the tape again'; return false; }
    if (this.lam === this.power) { this.tortoise = state; this.power <<= 1; this.lam = 0; }
    this.lam++;
    const t = this.tape, c = t[this.pc];
    this.lastWrite = -1;
    switch (c) {
      case 60: this.h0--; this.ops++; break;
      case 62: this.h0++; this.ops++; break;
      case 123: this.h1--; this.ops++; break;
      case 125: this.h1++; this.ops++; break;
      case 43: t[this.h0]++; this.ops++; this.lastWrite = this.h0; this.changed(); break;
      case 45: t[this.h0]--; this.ops++; this.lastWrite = this.h0; this.changed(); break;
      case 46: this.ops++; if (t[this.h1] !== t[this.h0]) { t[this.h1] = t[this.h0]; this.lastWrite = this.h1; this.changed(); } break;
      case 44: this.ops++; if (t[this.h0] !== t[this.h1]) { t[this.h0] = t[this.h1]; this.lastWrite = this.h0; this.changed(); } break;
      case 91: this.ops++;
        if (t[this.h0] === 0) {
          let d = 1; this.pc++;
          while (this.pc < 128 && d > 0) { if (t[this.pc] === 93) d--; else if (t[this.pc] === 91) d++; this.pc++; }
          this.pc--;
          if (d !== 0) { this.halted = 'unmatched ['; return false; }
        }
        break;
      case 93: this.ops++;
        if (t[this.h0] !== 0) {
          let d = 1; this.pc--;
          while (this.pc >= 0 && d > 0) { if (t[this.pc] === 91) d--; else if (t[this.pc] === 93) d++; this.pc--; }
          this.pc++;
          if (d !== 0) { this.halted = 'unmatched ]'; return false; }
        }
        break;
      default: break;
    }
    this.pc++;
    return true;
  }
}

const st = { vm: null, timer: null, expectedAfter: null, source: '', cells: [] };
function buildCells() {
  for (const [id, off] of [['#st-tape-a', 0], ['#st-tape-b', 64]]) {
    const box = $(id); box.innerHTML = '';
    for (let i = 0; i < 64; i++) {
      const c = document.createElement('div'); c.className = 'cell'; c.innerHTML = `<span class="idx">${off + i}</span><span class="ch"></span>`;
      box.appendChild(c); st.cells[off + i] = c;
    }
  }
}
function randomBytes(n) { const a = new Uint8Array(n); crypto.getRandomValues(a); return a; }
function parseProgram(text) {
  const out = new Uint8Array(64);
  const t = text.trim();
  if (!t) return null;
  if (/^[0-9a-fA-F\s]+$/.test(t) && t.replace(/\s/g, '').length >= 4 && t.replace(/\s/g, '').length % 2 === 0) {
    const hex = t.replace(/\s/g, '');
    for (let i = 0; i < Math.min(64, hex.length / 2); i++) out[i] = parseInt(hex.substr(2 * i, 2), 16);
    return out;
  }
  for (let i = 0; i < Math.min(64, t.length); i++) out[i] = t.charCodeAt(i) & 0xFF;
  return out;
}
function loadTape(tape, source, expectedAfter = null, maxSteps = 32768, heads = null) {
  if (heads === null) heads = !!(state.info && state.info.meta && state.info.meta.heads);
  st.vm = new BFF(tape, maxSteps, heads);
  st.expectedAfter = expectedAfter;
  st.source = source;
  $('#st-source').textContent = source;
  $('#st-keys-before').textContent = `A: ${keyOf(tape.slice(0, 64))}\nB: ${keyOf(tape.slice(64))}`;
  stopPlay();
  renderTape();
  showTab('stepper');
}
function renderTape() {
  const vm = st.vm; if (!vm) return;
  const h0 = vm.h0 & 127, h1 = vm.h1 & 127;
  for (let i = 0; i < 128; i++) {
    const b = vm.tape[i], c = st.cells[i], isCmd = !!CMD_CHARS[b];
    c.className = 'cell' + (isCmd ? ' cmd' : '') + (i === vm.pc ? ' pc' : '') + (i === h0 ? ' h0' : '') + (i === h1 ? ' h1' : '') + (i === vm.lastWrite ? ' written' : '');
    c.querySelector('.ch').textContent = isCmd ? CMD_CHARS[b] : (b === 0 ? '0' : '·');
    c.title = `pos ${i}: byte ${b} (0x${b.toString(16).padStart(2, '0')})`;
  }
  const a = keyOf(vm.tape.slice(0, 64)), b = keyOf(vm.tape.slice(64));
  let verdict = '';
  if (vm.halted && st.expectedAfter) {
    const same = vm.tape.every((v, i) => v === st.expectedAfter[i]);
    verdict = same ? ' · matches the recorded outcome ✓' : ' · differs from the recorded outcome ✗';
  }
  $('#st-keys').textContent = `A: ${a}\nB: ${b}${a && a === b ? '\n(identical: B is now a copy of A)' : ''}`;
  $('#st-status').textContent = `step ${fmt(vm.steps)} · ${fmt(vm.ops)} instructions · pc ${vm.pc} · head0 ${h0} · head1 ${h1}` + (vm.halted ? ` · halted: ${vm.halted}` : '') + verdict;
}
function stepN(n) { const vm = st.vm; if (!vm) return; for (let i = 0; i < n; i++) if (!vm.step()) break; renderTape(); if (vm.halted) stopPlay(); }
function stopPlay() { if (st.timer) { clearInterval(st.timer); st.timer = null; } $('#st-play').textContent = '▶ Play'; }
$('#st-step').addEventListener('click', () => stepN(1));
$('#st-step10').addEventListener('click', () => stepN(10));
$('#st-play').addEventListener('click', () => {
  if (st.timer) { stopPlay(); return; }
  if (!st.vm) return;
  $('#st-play').textContent = '⏸ Pause';
  st.timer = setInterval(() => stepN(Math.max(1, Math.round(+$('#st-speed').value / 10))), 40);
});
$('#st-end').addEventListener('click', () => { if (!st.vm) return; stopPlay(); while (st.vm.step()); renderTape(); });
$('#st-reset').addEventListener('click', () => { if (!st.vm) return; stopPlay(); st.vm.reset(); renderTape(); });
$('#st-next-gen').addEventListener('click', () => {
  if (!st.vm) return;
  const t = new Uint8Array(128); t.set(st.vm.tape.slice(64), 0); t.set(randomBytes(64), 64);
  loadTape(t, st.source.replace(/ · generation \d+$/, '') + ` · generation ${(+(st.source.match(/generation (\d+)$/) || [0, 1])[1]) + 1}`, null, st.vm.max, st.vm.heads);
});
$('#st-selfrep').addEventListener('click', async () => {
  if (!st.vm) { toast('Load a tape first'); return; }
  const a = Array.from(st.vm.initial.slice(0, 64)), b = Array.from(st.vm.initial.slice(64));
  toast('Running 13 trials × 5 generations for each half…', true);
  try {
    const r = await api('selfrep', { programs: JSON.stringify([a, b]), max_steps: st.vm.max });
    const verdict = (s) => `${s}/64 ${s >= r.threshold ? '✓ replicates' : '✗'}`;
    toast(`Self-replication: A ${verdict(r.scores[0])} · B ${verdict(r.scores[1])}  (score = tape bytes stable across 13 random partners, ≥ ${r.threshold} counts as a replicator)`, true);
  } catch (e) { toast('test failed: ' + e.message); }
});
$('#st-load').addEventListener('click', () => {
  const a = parseProgram($('#st-a').value), b = parseProgram($('#st-b').value);
  if (!a && !b) { toast('Enter at least program A'); return; }
  const t = new Uint8Array(128); t.set(a || randomBytes(64), 0); t.set(b || randomBytes(64), 64);
  loadTape(t, `typed programs${b ? '' : ' (B random)'}`);
});
$('#st-swap').addEventListener('click', () => { const a = $('#st-a').value; $('#st-a').value = $('#st-b').value; $('#st-b').value = a; });
const toHex = (bytes) => Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
async function fromSoup(which) {
  const field = $(which === 'A' ? '#st-a' : '#st-b');
  const key = field.value.trim();
  if (!key) { toast(`Type the species key into ${which} first`); return; }
  try {
    const p = await api('program', { key, epoch: $('#st-epoch').value });
    if (!p) { toast(`No instance of that key in the checkpoint at/below that epoch (last checkpoint: try an earlier epoch)`); return; }
    field.value = toHex(p.program);
    toast(`${which}: raw instance from checkpoint epoch ${fmt(p.epoch)}, slot ${p.slot} (${fmt(p.count)} copies there)`);
  } catch (e) { toast('failed: ' + e.message); }
}
$('#st-a-soup').addEventListener('click', () => fromSoup('A'));
$('#st-b-soup').addEventListener('click', () => fromSoup('B'));

async function watchBirth(epoch, slot) {
  toast(`Replaying epoch ${fmt(epoch)} from the nearest checkpoint… (this can take a while on a big soup)`, true);
  try {
    const t = await api('tape', { epoch, slot });
    hideToast();
    const [a, b] = t.slots;
    loadTape(t.before, `epoch ${fmt(epoch)}: slot ${a} (first half) + slot ${b} (second half) — the birth happened in slot ${slot}`, t.after, t.max_steps, !!t.heads);
  } catch (e) { toast('replay failed: ' + e.message); }
}
async function runSpecies(key) {
  try {
    const p = await api('program', { key, epoch: $('#st-epoch').value });
    const t = new Uint8Array(128);
    if (p) { t.set(p.program, 0); $('#st-a').value = key; }
    else { t.set(parseProgram(key), 0); $('#st-a').value = key; toast('No raw copy in a checkpoint; using the instruction string with zero padding'); }
    t.set(randomBytes(64), 64);
    $('#st-b').value = '';
    loadTape(t, `${p ? `raw program from checkpoint epoch ${fmt(p.epoch)}, slot ${p.slot}` : 'instruction string'} + random partner`, null, p ? p.max_steps : 32768, p ? !!p.heads : null);
  } catch (e) { toast('failed: ' + e.message); }
}
window.showDetail = showDetail; window.traceLineage = traceLineage; window.watchBirth = watchBirth; window.runSpecies = runSpecies;

// ------------------------------------------------------------------ init
buildCells();
loadRuns().catch((e) => toast('failed to load runs: ' + e.message, true));
