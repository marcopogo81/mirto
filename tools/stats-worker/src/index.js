// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Marco Dalicco
//
// Server delle statistiche anonime di Mirto (Cloudflare Worker + D1, piano gratuito).
//
//   POST /v1/ping       battito giornaliero di chi ha detto sì (ID casuale + fasce, vedi mirto/telemetry.py)
//   POST /v1/declined   il "no" anonimo, una volta sola, senza ID: si conta e basta
//   POST /v1/forget     cancella un ID (consenso ritirato o ID rinnovato dall'utente)
//   GET  /v1/latest     ultima versione pubblicata (anche per chi non ha dato il consenso): si conta e basta
//   GET  /v1/stats      i numeri, solo con "Authorization: Bearer <STATS_TOKEN>"
//   GET  /              pagina con i grafici (chiede il token, che resta nel browser)
//
// Non si legge né si salva mai l'indirizzo IP (né altre intestazioni). I dati più vecchi di 13 mesi si
// cancellano ogni notte (per un'installazione ancora attiva resta solo la data del primo battito).

const RETENTION_DAYS = 396;
const BUCKETS = new Set(["0", "1", "2-4", "5-9", "10+"]);
const RECORDING = new Set(["", "off", "events", "continuous", "mixed"]);
const FEATURES = new Set(["telegram", "email", "sms", "presence", "schedule", "mic", "actions", "deterrent",
  "watch_more"]);

const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

export const today = (now = Date.now()) => new Date(now).toISOString().slice(0, 10);
const daysAgo = (n, now = Date.now()) => today(now - n * 86400000);

// valore accettato solo se intero, entro la lunghezza e conforme: niente tagli, niente testo libero
const clean = (v, re, max = 30) => {
  if (typeof v !== "string" || v.length > max) return null;
  return re.test(v) ? v : null;
};
const OS_RE = /^(macOS( \d{1,2})?|Windows( \d{1,2})?|Linux)$/;
const VERSION_RE = /^[0-9][0-9A-Za-z.+-]{0,19}$/;

export function validatePing(body) {
  if (!body || typeof body !== "object") return null;
  const id = clean(body.id, /^[0-9a-f]{32}$/, 32);
  const version = clean(body.version, VERSION_RE, 20);
  if (!id || !version) return null;
  const os = clean(body.os, OS_RE, 12) ?? "?";
  const lang = clean(body.lang, /^[a-z]{2}$/, 2) ?? "?";
  const cameras = BUCKETS.has(body.cameras) ? body.cameras : "?";
  const nodes = BUCKETS.has(body.nodes) ? body.nodes : "?";
  const recording = RECORDING.has(body.recording) ? body.recording : "?";
  const features = Array.isArray(body.features)
    ? [...new Set(body.features.filter((f) => FEATURES.has(f)))].sort().join(",") : "";
  return { id, version, os, lang, cameras, nodes, recording, features };
}

async function readJson(request) {
  const text = await request.text();
  if (text.length > 2048) return null;
  try { return JSON.parse(text); } catch { return null; }
}

async function ping(request, env, now) {
  const p = validatePing(await readJson(request));
  if (!p) return json({ ok: false, error: "dati non validi" }, 400);
  const day = today(now);
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO installs (id, first_day, last_day, version, os, lang, cameras, nodes, recording, features)
       VALUES (?1, ?2, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
       ON CONFLICT(id) DO UPDATE SET last_day = ?2, version = ?3, os = ?4, lang = ?5, cameras = ?6,
         nodes = ?7, recording = ?8, features = ?9`,
    ).bind(p.id, day, p.version, p.os, p.lang, p.cameras, p.nodes, p.recording, p.features),
    env.DB.prepare("INSERT OR IGNORE INTO pings (day, id) VALUES (?1, ?2)").bind(day, p.id),
  ]);
  return json({ ok: true, latest: env.LATEST_VERSION || "" });
}

async function declined(request, env, now) {
  const body = await readJson(request);
  const version = clean(body?.version, VERSION_RE, 20);
  if (!version) return json({ ok: false, error: "dati non validi" }, 400);
  await env.DB.prepare(
    "INSERT INTO declines (day, version, n) VALUES (?1, ?2, 1) ON CONFLICT(day, version) DO UPDATE SET n = n + 1",
  ).bind(today(now), version).run();
  return json({ ok: true, latest: env.LATEST_VERSION || "" });
}

// consenso ritirato o ID rinnovato: si cancella tutto ciò che riguarda quell'ID
async function forget(request, env) {
  const body = await readJson(request);
  const id = clean(body?.id, /^[0-9a-f]{32}$/, 32);
  if (!id) return json({ ok: false, error: "dati non validi" }, 400);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pings WHERE id = ?1").bind(id),
    env.DB.prepare("DELETE FROM installs WHERE id = ?1").bind(id),
  ]);
  return json({ ok: true });
}

async function latest(env, now) {
  await env.DB.prepare("INSERT INTO checks (day, n) VALUES (?1, 1) ON CONFLICT(day) DO UPDATE SET n = n + 1")
    .bind(today(now)).run();
  return json({ ok: true, latest: env.LATEST_VERSION || "" });
}

function authorized(request, env) {
  const token = env.STATS_TOKEN || "";
  const got = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (token.length < 16 || got.length !== token.length) return false;
  let diff = 0;  // confronto a tempo costante
  for (let i = 0; i < token.length; i++) diff |= token.charCodeAt(i) ^ got.charCodeAt(i);
  return diff === 0;
}

export async function stats(env, now = Date.now()) {
  const d = (n) => daysAgo(n, now);
  const one = async (sql, ...args) => (await env.DB.prepare(sql).bind(...args).first()) || {};
  const all = async (sql, ...args) => (await env.DB.prepare(sql).bind(...args).all()).results || [];
  const active = async (n) => (await one("SELECT COUNT(*) AS n FROM installs WHERE last_day >= ?1", d(n - 1))).n || 0;
  const by = (col) => all(
    `SELECT ${col} AS k, COUNT(*) AS n FROM installs WHERE last_day >= ?1 GROUP BY ${col} ORDER BY n DESC`, d(29));

  const total = (await one("SELECT COUNT(*) AS n FROM installs")).n || 0;
  const declinedTotal = (await one("SELECT COALESCE(SUM(n), 0) AS n FROM declines")).n || 0;
  const declined30 = (await one("SELECT COALESCE(SUM(n), 0) AS n FROM declines WHERE day >= ?1", d(29))).n || 0;
  const checks30 = (await one("SELECT COALESCE(SUM(n), 0) AS n FROM checks WHERE day >= ?1", d(29))).n || 0;

  // fedeltà: di chi ha iniziato almeno 30 (90) giorni fa, quanti si sono visti negli ultimi 7 giorni
  const retention = async (age) => {
    const r = await one(
      `SELECT COUNT(*) AS base, SUM(CASE WHEN last_day >= ?2 THEN 1 ELSE 0 END) AS kept
       FROM installs WHERE first_day <= ?1`, d(age), d(6));
    return { base: r.base || 0, kept: r.kept || 0, rate: r.base ? (r.kept || 0) / r.base : null };
  };

  const daily = await all(
    "SELECT day, COUNT(*) AS active FROM pings WHERE day >= ?1 GROUP BY day ORDER BY day", d(89));
  const fresh = await all(
    "SELECT first_day AS day, COUNT(*) AS n FROM installs WHERE first_day >= ?1 GROUP BY first_day", d(89));
  const declines = await all(
    "SELECT day, SUM(n) AS n FROM declines WHERE day >= ?1 GROUP BY day", d(89));
  const series = {};
  for (let i = 89; i >= 0; i--) series[d(i)] = { day: d(i), active: 0, new: 0, declined: 0 };
  for (const r of daily) if (series[r.day]) series[r.day].active = r.active;
  for (const r of fresh) if (series[r.day]) series[r.day].new = r.n;
  for (const r of declines) if (series[r.day]) series[r.day].declined = r.n;

  const featureRows = await all("SELECT features FROM installs WHERE last_day >= ?1", d(29));
  const features = {};
  for (const r of featureRows) for (const f of (r.features || "").split(",").filter(Boolean)) {
    features[f] = (features[f] || 0) + 1;
  }
  const answered = total + declinedTotal;
  return {
    as_of: today(now),
    installs_total: total,
    active_1d: await active(1),
    active_7d: await active(7),
    active_30d: await active(30),
    declined_total: declinedTotal,
    declined_30d: declined30,
    consent_rate: answered ? total / answered : null,
    update_checks_30d: checks30,
    retention_30d: await retention(30),
    retention_90d: await retention(90),
    versions: await by("version"),
    os: await by("os"),
    lang: await by("lang"),
    cameras: await by("cameras"),
    nodes: await by("nodes"),
    recording: await by("recording"),
    features: Object.entries(features).map(([k, n]) => ({ k, n })).sort((a, b) => b.n - a.n),
    daily: Object.values(series),
  };
}

export async function cleanup(env, now = Date.now()) {
  const limit = daysAgo(RETENTION_DAYS, now);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pings WHERE day < ?1").bind(limit),
    env.DB.prepare("DELETE FROM installs WHERE last_day < ?1").bind(limit),
    env.DB.prepare("DELETE FROM declines WHERE day < ?1").bind(limit),
    env.DB.prepare("DELETE FROM checks WHERE day < ?1").bind(limit),
  ]);
}

export default {
  async fetch(request, env, ctx, now = Date.now()) {
    const { pathname } = new URL(request.url);
    const m = request.method;
    try {
      if (m === "POST" && pathname === "/v1/ping") return await ping(request, env, now);
      if (m === "POST" && pathname === "/v1/declined") return await declined(request, env, now);
      if (m === "POST" && pathname === "/v1/forget") return await forget(request, env);
      if (m === "GET" && pathname === "/v1/latest") return await latest(env, now);
      if (m === "GET" && pathname === "/v1/stats") {
        if (!authorized(request, env)) return json({ ok: false, error: "token mancante o errato" }, 401);
        return json(await stats(env, now));
      }
      if (m === "GET" && pathname === "/") {
        return new Response(PAGE, { headers: {
          "content-type": "text/html; charset=utf-8",
          "content-security-policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
          "referrer-policy": "no-referrer",
        } });
      }
      return json({ ok: false, error: "non trovato" }, 404);
    } catch (err) {
      return json({ ok: false, error: "errore del server" }, 500);
    }
  },
  async scheduled(event, env) {
    await cleanup(env);
  },
};

const PAGE = `<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirto statistiche</title>
<style>
:root{--bg:#121A26;--card:#1B2636;--text:#E8EDF3;--muted:#9AA8BA;--accent:#5B9BF0;--ok:#2FB383;--warn:#E8A33D}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
main{max-width:1000px;margin:0 auto;padding:20px 16px}
h1{font-size:22px;margin:0 0 4px} .muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border-radius:10px;padding:12px 14px}
.big{font-size:28px;font-weight:700} .lbl{color:var(--muted);font-size:13px}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
table{width:100%;border-collapse:collapse;font-size:14px} td{padding:3px 0} td.n{text-align:right;color:var(--muted)}
.bar{height:6px;background:var(--accent);border-radius:3px}
input,button{font:inherit;padding:7px 10px;border-radius:8px;border:1px solid #33435A;background:#0E1520;color:var(--text)}
button{background:var(--accent);border:0;color:#fff;cursor:pointer}
svg{width:100%;height:auto;display:block}
</style></head><body><main>
<h1>Mirto – statistiche anonime</h1>
<div class="muted" id="asof">Inserisci il token per vedere i numeri.</div>
<form id="f" style="margin:12px 0;display:flex;gap:8px;flex-wrap:wrap">
<input id="t" type="password" placeholder="STATS_TOKEN" autocomplete="off" style="flex:1;min-width:200px">
<button>Mostra</button></form>
<div id="out"></div>
<p><button id="lo" style="display:none;background:#33435A">Dimentica il token su questo browser</button></p>
</main><script>
const $=s=>document.querySelector(s);
const pct=x=>x==null?"–":Math.round(x*100)+"%";
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function card(v,l){return '<div class="card"><div class="big">'+v+'</div><div class="lbl">'+l+'</div></div>'}
function list(title,rows){const max=Math.max(1,...rows.map(r=>r.n));
 return '<div class="card"><b>'+title+'</b><table>'+rows.map(r=>'<tr><td>'+esc(r.k||"?")+
 '<div class="bar" style="width:'+(r.n/max*100)+'%"></div></td><td class="n">'+r.n+'</td></tr>').join("")+'</table></div>'}
function chart(days){const W=900,H=220,P=30,max=Math.max(1,...days.map(d=>d.active));
 const x=i=>P+i*(W-2*P)/(days.length-1),y=v=>H-P-v*(H-2*P)/max;
 const line=days.map((d,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(d.active).toFixed(1)).join("");
 const bars=days.map((d,i)=>d.new?'<rect x="'+(x(i)-2)+'" y="'+y(d.new)+'" width="4" height="'+(H-P-y(d.new))+'" fill="#2FB383"/>':"").join("");
 const decl=days.map((d,i)=>d.declined?'<circle cx="'+x(i)+'" cy="'+y(d.declined)+'" r="3" fill="#E8A33D"/>':"").join("");
 return '<div class="card"><b>Ultimi 90 giorni</b> <span class="muted">— linea: attive nel giorno · barre verdi: nuove · punti gialli: "no"</span>'+
 '<svg viewBox="0 0 '+W+' '+H+'"><line x1="'+P+'" y1="'+(H-P)+'" x2="'+(W-P)+'" y2="'+(H-P)+'" stroke="#33435A"/>'+
 '<text x="'+P+'" y="'+(P-8)+'" fill="#9AA8BA" font-size="12">max '+max+'</text>'+
 '<text x="'+P+'" y="'+(H-8)+'" fill="#9AA8BA" font-size="12">'+days[0].day+'</text>'+
 '<text x="'+(W-P)+'" y="'+(H-8)+'" fill="#9AA8BA" font-size="12" text-anchor="end">'+days[days.length-1].day+'</text>'+
 bars+'<path d="'+line+'" fill="none" stroke="#5B9BF0" stroke-width="2.5"/>'+decl+'</svg></div>'}
async function load(token){
 const r=await fetch("/v1/stats",{headers:{authorization:"Bearer "+token}});
 if(!r.ok){$("#asof").textContent="Token errato.";return}
 try{localStorage.setItem("t",token)}catch(e){}
 const s=await r.json();
 $("#asof").textContent="Dati al "+s.as_of+". Nessun indirizzo IP né dato personale: solo ID casuali e fasce.";
 $("#f").style.display="none";$("#lo").style.display="";
 $("#out").innerHTML='<div class="grid">'+card(s.active_7d,"attive negli ultimi 7 giorni")+card(s.active_30d,"attive negli ultimi 30 giorni")+
  card(s.installs_total,"installazioni con il sì (totale)")+card(s.declined_total,'"no" anonimi (totale)')+
  card(pct(s.consent_rate),"hanno detto sì")+card(pct(s.retention_30d.rate),"ancora attive dopo 30 giorni ("+s.retention_30d.base+")")+
  card(pct(s.retention_90d.rate),"ancora attive dopo 90 giorni ("+s.retention_90d.base+")")+card(s.update_checks_30d,"controlli versione senza consenso (30 gg)")+
  '</div>'+chart(s.daily)+'<div class="two" style="margin-top:12px">'+list("Versioni (attive 30 gg)",s.versions)+list("Sistema",s.os)+
  list("Lingua",s.lang)+list("Camere",s.cameras)+list("Nodi",s.nodes)+list("Registrazione",s.recording)+list("Funzioni accese",s.features)+'</div>'}
$("#lo").onclick=()=>{try{localStorage.removeItem("t")}catch(e){}location.reload()};
$("#f").onsubmit=e=>{e.preventDefault();load($("#t").value.trim())};
let saved="";try{saved=localStorage.getItem("t")||""}catch(e){}
if(saved)load(saved);
</script></body></html>`;
