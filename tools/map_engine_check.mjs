// Load .map26a files with the CAMBC engine's own WASM (the module the map editor uses) and
// print what it decodes, as one JSON object per line. Used by validate_maps.py as an
// independent decoder check; needs Node >= 20.
//
//   node tools/map_engine_check.mjs <engine.wasm> <map>...
//
// The wasm ships with cambc: <cambc cache>/assets/<hash>/mapeditor/assets/v*.wasm.
import fs from 'fs';
import { WASI } from 'node:wasi';

process.removeAllListeners('warning');
const [wasmPath, ...maps] = process.argv.slice(2);
const wasi = new WASI({ version: 'preview1' });
const mod = await WebAssembly.compile(fs.readFileSync(wasmPath));
const inst = await WebAssembly.instantiate(mod, {
  wasi_snapshot_preview1: wasi.wasiImport,
  deep_wasm: { on_assert_fail_impl: () => 1 },
});
wasi.initialize(inst);
const X = inst.exports;
const out = X.Engine_Malloc(64);
const dv = () => new DataView(X.memory.buffer);
const cstr = (p) => { const m = new Uint8Array(X.memory.buffer); let s = ''; while (m[p]) s += String.fromCharCode(m[p++]); return s; };
const chk = (r, what) => { if (r !== 0) throw new Error(`${what}: engine error ${r}: ${cstr(X.GetLastErrorMsg())}`); };

function check(bytes) {
  const p = X.Engine_Malloc(bytes.length);
  new Uint8Array(X.memory.buffer, p, bytes.length).set(bytes);
  try {
    chk(X.MapBuilder_FromFileBytes(p, bytes.length, out), 'MapBuilder_FromFileBytes');
    const h = dv().getBigUint64(out, true);
    const r = {};
    try {
      chk(X.MapBuilder_VerifyIntegrity(h), 'MapBuilder_VerifyIntegrity');
      chk(X.MapBuilder_GetSymmetry(h, out), 'symmetry'); r.symmetry = dv().getInt32(out, true);
      chk(X.MapBuilder_GetNumChampsPerTeam(h, out), 'champs'); r.champions = dv().getInt32(out, true);
      chk(X.MapBuilder_GetTeamASpawnArea(h, out), 'spawn'); r.spawn_a = [0, 4, 8, 12].map((o) => dv().getInt32(out + o, true));
      chk(X.MapBuilder_GetBitboardMetadata(h, out), 'meta'); const W = dv().getInt32(out, true), H = dv().getInt32(out + 4, true);
      r.width = W; r.height = H;
      for (const k of ['Bush', 'Wall', 'Hole', 'Altar']) {
        const rows = [];
        for (let y = 0; y < H; y++) {
          let s = '';
          for (let x = 0; x < W; x++) { chk(X[`MapBuilder_Get${k}`](h, x, y, out), k); s += dv().getUint8(out) ? '1' : '0'; }
          rows.push(s);
        }
        r[k.toLowerCase()] = rows;
      }
      chk(X.MapBuilder_GetNumCamps(h, out), 'camps'); const n = dv().getUint32(out, true); r.camps = [];
      for (let i = 0; i < n; i++) {
        chk(X.MapBuilder_GetCampHandle(h, i, out), 'camp handle'); const c = dv().getBigUint64(out, true);
        chk(X.MapBuilder_GetCamp(h, c, out), 'camp');
        r.camps.push([dv().getInt32(out, true), dv().getInt32(out + 4, true), dv().getInt32(out + 8, true)]);
      }
      chk(X.MapBuilder_Encode(h, out, out + 4), 'MapBuilder_Encode');
      const ep = dv().getUint32(out, true), el = dv().getUint32(out + 4, true);
      r.engine_reencode_identical = Buffer.compare(Buffer.from(new Uint8Array(X.memory.buffer, ep, el)), Buffer.from(bytes)) === 0;
    } finally { X.MapBuilder_Destroy(h); }
    chk(X.Game_FromMapFileBytes(p, bytes.length, out), 'Game_FromMapFileBytes');
    X.Game_Destroy(dv().getBigUint64(out, true));
    return r;
  } finally { X.Engine_Free(p); }
}

for (const m of maps) {
  try { console.log(JSON.stringify({ map: m, ok: true, ...check(fs.readFileSync(m)) })); }
  catch (e) { console.log(JSON.stringify({ map: m, ok: false, error: String(e.message || e) })); }
}
