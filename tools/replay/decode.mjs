// Headless CAMBC 2026a replay decoder: replay or bundle -> compact per-round JSON timeline.
//
//   node tools/replay/decode.mjs <replay.repl26a|bundle.zip|dir> [-o out.json|outdir] [--assets DIR] [--stats]
//
// A replay stores only inputs, so we re-simulate it with the visualiser's own engine WASM
// (v0_0_1-*.wasm) and read every step's state through the visualiser's snapshot decoder
// (exported, minified, from PanelChrome-*.js). Export names change between builds, so the
// decoder is found by behaviour (its source mentions `roundFlags` and `channelType`).
// Assets come from the cambc cache (`cambc view` downloads them); override with --assets or
// CAMBC_ASSETS. Schema: tools/replay/README.md. Needs Node >= 20.
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import { WASI } from 'node:wasi';
import { pathToFileURL } from 'url';

process.removeAllListeners('warning');
process.on('warning', () => {});

export const FORMAT = 'cambc-timeline/1';
const DRAFT = ['Ryth', 'Vesper Vale', 'Vendetta', 'Cassi & Issac', 'Stellar', 'Prism', 'Marcella', 'Willow', 'Ren', 'Varg', 'Tavi'];
// Champion body types as the snapshot reports them (C&I are two bodies).
const BODY = ['Ryth', 'Vesper Vale', 'Vendetta', 'Cassi', 'Issac', 'Stellar', 'Prism', 'Marcella', 'Willow', 'Ren', 'Varg', 'Tavi', 'Heart of Ruin'];
const CAMP = ['wolf', 'elk', 'gem_cluster', 'gold_cluster', 'monolith'];
// Step kinds, in the visualiser's label order.
export const STEP = ['phase', 'respawn', 'capture', 'tick', 'move', 'attack', 'cast', 'input', 'end_turn', 'set_priority', '',
  'recall', 'submit_gems', 'shop', 'place_ward', 'consumable', 'cancel_channel', 'sandbox', 'gems_submitted', 'draft',
  'place_altar_lock', 'teleport_channel', 'teleport_complete', 'movement_blocked', 'resign', 'sell_item', 'turn_rollover'];
// Steps worth recording as actions (moves are implied by positions in frames).
const ACT_KINDS = new Set([5, 6, 11, 12, 13, 14, 15, 16, 18, 20, 21, 22, 24, 25]);
const CHANNEL = { 1: 'R', 2: 'T', 3: 'P' };

// ---------- input ----------
function unzip(buf) {
  const out = {};
  let e = buf.length - 22;
  while (e >= 0 && buf.readUInt32LE(e) !== 0x06054b50) e--;
  if (e < 0) throw new Error('not a zip');
  const n = buf.readUInt16LE(e + 10);
  let p = buf.readUInt32LE(e + 16);
  for (let i = 0; i < n; i++) {
    const method = buf.readUInt16LE(p + 10), csize = buf.readUInt32LE(p + 20);
    const fl = buf.readUInt16LE(p + 28), xl = buf.readUInt16LE(p + 30), cl = buf.readUInt16LE(p + 32), lo = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + fl);
    const start = lo + 30 + buf.readUInt16LE(lo + 26) + buf.readUInt16LE(lo + 28);
    const data = buf.subarray(start, start + csize);
    out[name] = method === 0 ? data : zlib.inflateRawSync(data);
    p += 46 + fl + xl + cl;
  }
  return out;
}

// Local `cambc run` bundles (.repl26a) and platform bundles (.zip) are both zips holding
// replay.repl26a; a bare replay is passed through. Detect by signature, not extension.
export function readReplay(file) {
  const buf = fs.readFileSync(file);
  if (buf[0] !== 0x50 || buf[1] !== 0x4b) return { replay: buf, files: {} };
  const files = unzip(buf);
  const key = Object.keys(files).find((k) => k.endsWith('.repl26a'));
  if (!key) throw new Error(`${file}: bundle has no .repl26a`);
  return { replay: files[key], files };
}

// ---------- assets ----------
function* candidateRoots() {
  if (process.env.CAMBC_ASSETS) yield process.env.CAMBC_ASSETS;
  const h = os.homedir();
  for (const r of [process.env.XDG_CACHE_HOME && path.join(process.env.XDG_CACHE_HOME, 'cambc'), path.join(h, '.cache', 'cambc'),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'cambc'), process.env.APPDATA && path.join(process.env.APPDATA, 'cambc'),
    path.join(h, 'Library', 'Caches', 'cambc'), path.join(h, 'AppData', 'Local', 'cambc')]) if (r) yield r;
}

function findAssetDirs(root, depth = 0, out = []) {
  let ents;
  try { ents = fs.readdirSync(root, { withFileTypes: true }); } catch { return out; }
  const names = ents.map((e) => e.name);
  if (names.some((n) => /^v\d+_\d+_\d+-.*\.wasm$/.test(n)) && names.some((n) => /^PanelChrome-.*\.js$/.test(n))) out.push(root);
  if (depth < 6) for (const e of ents) if (e.isDirectory()) findAssetDirs(path.join(root, e.name), depth + 1, out);
  return out;
}

export function locateAssets(explicit) {
  const dirs = [];
  for (const r of explicit ? [explicit] : candidateRoots()) dirs.push(...findAssetDirs(r));
  if (!dirs.length) throw new Error('visualiser assets not found: run `cambc view <replay>` once, or pass --assets <dir>');
  // Prefer the visualiser copy, newest first.
  dirs.sort((a, b) => (b.includes('visualiser') - a.includes('visualiser')) || fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  const d = dirs[0], names = fs.readdirSync(d);
  return { dir: d, wasm: path.join(d, names.find((n) => /^v\d+_\d+_\d+-.*\.wasm$/.test(n))), panel: path.join(d, names.find((n) => /^PanelChrome-.*\.js$/.test(n))) };
}

export async function loadEngine(assets) {
  const mod = await import(pathToFileURL(assets.panel).href);
  const fns = Object.values(mod).filter((f) => typeof f === 'function');
  const decode = fns.find((f) => { const s = f.toString(); return s.includes('roundFlags') && s.includes('channelType') && s.includes('kda'); });
  if (!decode) throw new Error(`no snapshot decoder found in ${assets.panel} (visualiser build changed?)`);
  const wasi = new WASI({ version: 'preview1' });
  const inst = await WebAssembly.instantiate(await WebAssembly.compile(fs.readFileSync(assets.wasm)), {
    wasi_snapshot_preview1: wasi.wasiImport, deep_wasm: { on_assert_fail_impl: () => 1 },
  });
  wasi.initialize(inst);
  return { X: inst.exports, decode };
}

// ---------- simulation ----------
class Game {
  constructor(engine, replayBytes) {
    const X = (this.X = engine.X);
    this.decode = engine.decode;
    this.out = X.Engine_Malloc(64);
    this.args = X.Engine_Malloc(128);
    this.snap = X.Engine_Malloc(X.Game_Visualiser_SnapshotSize());
    const p = X.Engine_Malloc(replayBytes.length);
    new Uint8Array(X.memory.buffer, p, replayBytes.length).set(replayBytes);
    try { this.chk(X.Game_FromReplayFileBytes(p, replayBytes.length, this.out), 'Game_FromReplayFileBytes'); } finally { X.Engine_Free(p); }
    this.h = this.dv().getBigUint64(this.out, true);
    this.chk(X.Game_Visualiser_Timeline_GetMain(this.h, this.out), 'Timeline_GetMain');
    this.timeline = this.dv().getBigUint64(this.out, true);
    this.chk(X.Game_Visualiser_Timeline_Info(this.h, this.timeline, this.args, this.args + 8, this.args + 16), 'Timeline_Info');
    this.size = this.dv().getUint32(this.args, true);
    this.chk(X.Game_Visualiser_Timeline_Seek(this.h, this.timeline, 0, 0), 'Timeline_Seek');
  }
  dv() { return new DataView(this.X.memory.buffer); }
  cstr(p) { const m = new Uint8Array(this.X.memory.buffer); let s = ''; while (m[p]) s += String.fromCharCode(m[p++]); return s; }
  chk(r, what) { if (r !== 0) throw new Error(`${what}: engine error ${r}: ${this.cstr(this.X.GetLastErrorMsg())}`); }
  read() {
    const pp = this.args + 48, dv = this.dv();
    dv.setUint32(pp, 2, true); // spectator perspective: team 2, no champion
    dv.setUint32(pp + 4, 0xffffffff, true);
    this.chk(this.X.Game_Visualiser_ReadState(this.h, pp, this.snap), 'ReadState');
    return this.decode(this.dv(), this.snap);
  }
  next() { this.chk(this.X.Game_Visualiser_Timeline_ReplayNext(this.h, 1), 'ReplayNext'); }
  destroy() { this.X.Game_Destroy(this.h); for (const p of [this.out, this.args, this.snap]) this.X.Engine_Free(p); }
}

const r1 = (v) => Math.round(v * 10) / 10;
const posKey = (p) => `${p.x},${p.y}`;

function itemList(items) {
  const out = [];
  items?.forEach((n, i) => { for (let k = 0; k < n; k++) out.push(i); });
  return out;
}

export function championRow(c) {
  const stun = c.buffs?.find((b) => b.type === 1), shield = c.buffs?.find((b) => b.type === 0);
  // channelType: 1 recall, 2 tribute (submit gems), 3 teleport crystal; 0 none.
  const ch = c.channelType ? CHANNEL[c.channelType] ?? `c${c.channelType}` : null;
  return [c.position?.x ?? -1, c.position?.y ?? -1, r1(c.health ?? 0), r1(c.maxHealth ?? 0), c.alive ? 1 : 0, c.gems ?? 0, c.gold ?? 0,
    c.cooldowns?.[0] ?? 0, c.cooldowns?.[1] ?? 0, stun ? stun.timer : 0, shield ? r1(shield.magnitude) : 0, ch, ch ? c.channelTimer ?? 0 : 0,
    c.alive ? 0 : c.respawnTimer ?? 0, c.kda?.[0] ?? 0, c.kda?.[1] ?? 0, c.kda?.[2] ?? 0, itemList(c.items), c.consumables ?? [0, 0, 0, 0],
    c.ward?.charges ?? 0, c.earnedGold ?? 0];
}
export const CHAMP_COLS = ['x', 'y', 'hp', 'max_hp', 'alive', 'gems', 'gold', 'cd0', 'cd1', 'stun', 'shield', 'channel', 'channel_left',
  'respawn', 'kills', 'deaths', 'assists', 'items', 'consumables', 'ward_charges', 'earned_gold'];

// Resolve who acted in a step: a champion index, a camp ('m<idx>'), or null.
function stepSource(step, prev, cur) {
  let a = step.actor;
  if (a.kind === 4) {
    const sp = [...prev.spells, ...cur.spells].find((s) => s.entity.index === a.index);
    if (sp) a = sp.owner;
  }
  if (a.kind === 1) return a.index;
  if (a.kind === 2) return `m${a.index}`;
  return null;
}

function targetAt(state, pos) {
  if (!pos) return null;
  const k = posKey(pos);
  const c = state.champions.find((c) => c.alive && c.position && posKey(c.position) === k);
  if (c) return c.entity.index;
  const m = state.camps.find((m) => (m.health ?? 0) > 0 && posKey(m.position) === k);
  return m ? `m${m.entity.index}` : null;
}

export async function decodeReplay(file, engine, opts = {}) {
  const { replay, files } = readReplay(file);
  const g = new Game(engine, replay);
  const t0 = Date.now();
  try {
    let cur = g.read(), prev = cur;
    const frames = [], acts = [], hits = [], stuns = [], deaths = [], captures = [], purchases = [], locks = [], campKills = [];
    let lastG = '', lastW = '', steps = 0;
    const byIdx = (s) => new Map(s.champions.map((c) => [c.entity.index, c]));
    let champs = null, groupsStatic = null;

    const emitFrame = (s) => {
      if (s.step.phase === 12 || s.phase === 12) return; // draft
      if (!champs) {
        champs = s.champions.map((c) => ({ id: c.entity.index, team: c.team, type: c.type, name: BODY[c.type] ?? `type${c.type}`, slot: c.draftOrder }));
        const gs = s.groups.map((_, i) => ({ id: i, camps: [] }));
        for (const m of s.camps) gs[m.group]?.camps.push({ id: m.entity.index, type: CAMP[m.type] ?? m.type, x: m.position.x, y: m.position.y });
        groupsStatic = gs;
      }
      const round = s.step.round;
      if (frames.length && frames.at(-1).r === round) frames.pop();
      const fr = { r: round, sg: s.submittedGems.map(Number), c: s.champions.map(championRow) };
      const alive = new Map();
      for (const m of s.camps) if ((m.health ?? 0) > 0) { const a = alive.get(m.group) ?? [0, 0]; a[0]++; a[1] += m.health; alive.set(m.group, a); }
      const gRows = s.groups.map((q, i) => [q.state, q.captureTeam, q.captureTimer, alive.get(i)?.[0] ?? 0, Math.round(alive.get(i)?.[1] ?? 0), q.respawnRounds]);
      const gk = JSON.stringify(gRows);
      if (gk !== lastG) { fr.g = gRows; lastG = gk; }
      const wRows = [...s.wards.map((w) => [w.team, w.position.x, w.position.y, w.owner.index, w.disabled ? 1 : 0, 0]),
        ...s.controlWards.map((w) => [w.team, w.position.x, w.position.y, w.owner.index, 0, 1])];
      const wk = JSON.stringify(wRows);
      if (wk !== lastW) { fr.w = wRows; lastW = wk; }
      frames.push(fr);
    };

    while (true) {
      if (cur.outcome || (cur.awaitingInput && cur.inputPosition >= g.size)) break;
      g.next();
      steps++;
      prev = cur;
      cur = g.read();
      if (cur.inputPosition === prev.inputPosition && cur.step.substep === prev.step.substep) throw new Error('timeline made no progress');
      const st = cur.step, k = st.kind, r = st.round;
      if (st.phase === 12) continue; // draft
      if (prev.step.phase !== 12 && prev.step.round !== r) emitFrame(prev);
      const P = byIdx(prev), C = byIdx(cur);
      const src = stepSource(st, prev, cur);

      // champion damage, stuns, deaths
      const damaged = [];
      for (const [i, c] of C) {
        const p = P.get(i);
        if (!p) continue;
        // Damage = health lost + shield absorbed (a shield shrinking, or vanishing before it expires).
        let amt = p.alive ? Math.max(0, (p.health ?? 0) - (c.health ?? 0)) : 0;
        const psh = p.buffs?.find((b) => b.type === 0), csh = c.buffs?.find((b) => b.type === 0);
        if (p.alive && psh && psh.magnitude > 0) {
          const left = csh ? csh.magnitude : 0;
          const expired = !csh && (psh.timer <= 1 || k === 3 || k === 0);
          if (!expired && psh.magnitude > left + 1e-3 && (!csh || csh.timer <= psh.timer)) amt += psh.magnitude - left;
        }
        if (amt > 1e-3) damaged.push([i, amt, c]);
        const ps = p.buffs?.find((b) => b.type === 1), cs = c.buffs?.find((b) => b.type === 1);
        if (cs && (!ps || cs.timer > ps.timer)) stuns.push([r, cs.source.kind === 1 ? cs.source.index : cs.source.kind === 2 ? `m${cs.source.index}` : null, i, cs.timer]);
      }
      for (const [i, amt, c] of damaged) {
        let s2 = src;
        // Damage to the actor's own side in its own step is retaliation (Ren passive) from the enemy it hit.
        if (typeof s2 === 'number' && C.get(s2)?.team === c.team) {
          const hitEnemy = damaged.find(([j, , d]) => d.team !== c.team) ?? (targetAt(prev, st.to) !== null ? [targetAt(prev, st.to)] : null);
          s2 = hitEnemy ? hitEnemy[0] : null;
        }
        // Cassi & Issac die together: the partner's health drop is the link, not damage (kind -1).
        const linked = !c.alive && (c.type === 3 || c.type === 4) && damaged.some(([j, , d]) => j !== i && !d.alive && d.team === c.team && (d.type === 3 || d.type === 4))
          && targetAt(prev, st.to) !== i;
        hits.push([r, s2, i, r1(amt), linked ? -1 : k]);
      }
      const killers = [];
      for (const [i, c] of C) { const p = P.get(i); if (p && (c.kda?.[0] ?? 0) > (p.kda?.[0] ?? 0)) killers.push(i); }
      for (const [i, c] of C) {
        const p = P.get(i);
        if (!p || !p.alive || c.alive) continue;
        let killer = killers.find((j) => C.get(j).team !== c.team);
        if (killer === undefined) killer = typeof src === 'string' ? src : (typeof src === 'number' && C.get(src)?.team !== c.team ? src : null);
        const gained = typeof killer === 'number' ? Math.max(0, (C.get(killer).gems ?? 0) - (P.get(killer).gems ?? 0)) : 0;
        deaths.push({ r, victim: i, team: c.team, killer, by: typeof killer === 'number' ? 'champion' : typeof killer === 'string' ? 'camp' : 'unknown',
          gems: p.gems ?? 0, pos: p.position ? [p.position.x, p.position.y] : null, credited: killers.includes(killer) });
        if (gained) deaths.at(-1).gems_to_killer = gained;
      }

      // camps killed (health > 0 -> 0); killer is the step's acting champion when there is one
      cur.camps.forEach((m, j) => {
        const pm = prev.camps[j];
        if (pm && pm.entity.index === m.entity.index && (pm.health ?? 0) > 0 && (m.health ?? 0) <= 0)
          campKills.push([r, m.entity.index, m.group, typeof src === 'number' ? src : null]);
      });
      // captures: a group leaving WaitingForRewardClaim with the reward claimed
      if (k === 2) {
        cur.groups.forEach((q, i) => {
          const pq = prev.groups[i];
          if (pq && pq.rewardAvailable && !q.rewardAvailable) {
            const rec = { r, group: i, team: pq.captureTeam, gold: 0, gems: 0, champs: [] };
            for (const [j, c] of C) {
              const p = P.get(j); if (!p) continue;
              const dg = (c.gold ?? 0) - (p.gold ?? 0), dm = (c.gems ?? 0) - (p.gems ?? 0);
              if ((dg > 0 || dm > 0) && c.team === pq.captureTeam) { rec.gold += Math.max(0, dg); rec.gems += Math.max(0, dm); rec.champs.push(j); (rec.split ??= {})[j] = [Math.max(0, dg), Math.max(0, dm)]; }
            }
            captures.push(rec);
          }
        });
      }
      // purchases / sales: item and consumable count changes in shop/sell steps
      if ((k === 13 || k === 25) && typeof src === 'number') {
        const c = C.get(src), p = P.get(src);
        if (c && p) {
          const di = [], dc = [];
          c.items?.forEach((n, j) => { if (n !== p.items[j]) di.push([j, n - p.items[j]]); });
          c.consumables?.forEach((n, j) => { if (n !== p.consumables[j]) dc.push([j, n - p.consumables[j]]); });
          purchases.push({ r, ch: src, sell: k === 25, items: di, consumables: dc, gold: (c.gold ?? 0) - (p.gold ?? 0) });
        }
      }
      if (k === 20) locks.push({ r, ch: src, team: C.get(src)?.team, x: st.to.x, y: st.to.y });
      if (ACT_KINDS.has(k) && (typeof src === 'number')) {
        const a = [r, STEP[k], src];
        if (k === 5 || k === 6) { a.push(targetAt(prev, st.to), st.to.x, st.to.y); if (k === 6) a.push(st.abilitySlot ?? null); }
        else if (k === 18) a.push(Number(cur.submittedGems[C.get(src).team] - prev.submittedGems[C.get(src).team]));
        else if (k === 14 || k === 20 || k === 21 || k === 22 || k === 15) a.push(st.to.x, st.to.y);
        acts.push(a);
      }
    }
    emitFrame(cur);
    const sg = cur.submittedGems.map(Number);
    const meta = {
      file: path.basename(file), rounds: cur.step.round, outcome: cur.outcome, winner: cur.outcome === 1 ? 0 : cur.outcome === 2 ? 1 : null,
      final_gems: sg, steps, decode_ms: 0, engine: path.basename(engine.wasmName ?? ''),
      drafts: cur.drafts.map((d, t) => d.slice(0, cur.draftCounts[t]).map((x) => DRAFT[x] ?? x)),
      bundle_files: Object.keys(files),
    };
    // Sidecar metadata written by fetch.py (team names, official statistics).
    const side = file.replace(/\.(zip|repl26a)$/i, '.meta.json');
    if (side !== file && fs.existsSync(side)) meta.platform = JSON.parse(fs.readFileSync(side, 'utf8'));
    meta.decode_ms = Date.now() - t0;
    return { format: FORMAT, meta, champions: champs, groups: groupsStatic, champion_cols: CHAMP_COLS, frames, acts, hits, stuns, deaths, captures, purchases, altar_locks: locks, camp_kills: campKills };
  } finally { g.destroy(); }
}

// outcome codes: the snapshot reports 1 = team A won, 2 = team B won (0 = running).
async function main() {
  const argv = process.argv.slice(2);
  let out = null, assetsDir = null;
  const inputs = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '-o') out = argv[++i];
    else if (argv[i] === '--assets') assetsDir = argv[++i];
    else inputs.push(argv[i]);
  }
  if (!inputs.length) { console.error('usage: node decode.mjs <replay|bundle|dir>... [-o out.json|outdir] [--assets DIR]'); process.exit(2); }
  const files = inputs.flatMap((f) => fs.statSync(f).isDirectory()
    ? fs.readdirSync(f).filter((n) => /\.(zip|repl26a)$/i.test(n)).map((n) => path.join(f, n)) : [f]);
  const assets = locateAssets(assetsDir);
  const engine = await loadEngine(assets);
  engine.wasmName = assets.wasm;
  const many = files.length > 1 || (out && fs.existsSync(out) && fs.statSync(out).isDirectory());
  if (many && out) fs.mkdirSync(out, { recursive: true });
  for (const f of files) {
    try {
      const tl = await decodeReplay(f, engine);
      const json = JSON.stringify(tl);
      const dest = !out ? null : many ? path.join(out, path.basename(f).replace(/\.(zip|repl26a)$/i, '') + '.timeline.json') : out;
      if (dest) fs.writeFileSync(dest, json); else process.stdout.write(json + '\n');
      console.error(`${f}: rounds ${tl.meta.rounds}, gems ${tl.meta.final_gems.join('-')}, ${tl.meta.decode_ms} ms${dest ? ' -> ' + dest : ''}`);
    } catch (e) {
      console.error(`${f}: FAILED ${e.message}`);
      process.exitCode = 1;
    }
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) await main();
