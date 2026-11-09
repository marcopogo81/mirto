// Prove del Worker con un D1 finto sopra node:sqlite (Node 22+): node --no-warnings test/worker.test.js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import worker, { cleanup, stats, validatePing } from "../src/index.js";

class Stmt {
  constructor(db, sql) { this.db = db; this.sql = sql.replace(/\?(\d+)/g, "?$1"); this.args = []; }
  bind(...a) { this.args = a; return this; }
  _st() { return this.db.prepare(this.sql); }
  async run() { this._st().run(...this.args); return { success: true }; }
  async first() { return this._st().get(...this.args) ?? null; }
  async all() { return { results: this._st().all(...this.args) }; }
}
class FakeD1 {
  constructor() {
    this.db = new DatabaseSync(":memory:");
    this.db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  }
  prepare(sql) { return new Stmt(this.db, sql); }
  async batch(list) { for (const s of list) await s.run(); return []; }
}

const env = { DB: new FakeD1(), LATEST_VERSION: "0.2.0", STATS_TOKEN: "finto-solo-per-queste-prove-non-e-un-token" };
const DAY = 86400000;
const T0 = Date.UTC(2026, 9, 17, 12);
const call = (method, path, body, now = T0, headers = {}) => worker.fetch(
  new Request("https://stats.example" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }),
  env, {}, now);
const id = (n) => n.toString(16).padStart(32, "0");
const ping = (n, extra = {}) => ({ id: id(n), version: "0.1.0", os: "macOS 15", lang: "it", cameras: "2-4",
  nodes: "1", recording: "events", features: ["telegram", "sms"], ...extra });

// validazione: niente campi liberi, niente ID non casuali
assert.equal(validatePing({ ...ping(1), id: "mario" }), null);
assert.equal(validatePing({ ...ping(1), os: "<script>" }).os, "?");
assert.equal(validatePing({ ...ping(1), cameras: "7" }).cameras, "?");
assert.deepEqual(validatePing({ ...ping(1), features: ["telegram", "hack", "telegram"] }).features, "telegram");
assert.equal(validatePing({ ...ping(1), id: "a".repeat(64) }), null);            // troppo lungo: rifiutato, non tagliato
assert.equal(validatePing({ ...ping(1), os: "Linux Mario Rossi PC" }).os, "?"); // niente testo libero
assert.equal(validatePing({ ...ping(1), lang: "english" }).lang, "?");
assert.equal(validatePing({ ...ping(1), os: "Windows 11" }).os, "Windows 11");

// battiti
let r = await call("POST", "/v1/ping", ping(1));
assert.equal(r.status, 200);
assert.equal((await r.json()).latest, "0.2.0");
await call("POST", "/v1/ping", ping(1));              // stesso giorno: un solo battito
await call("POST", "/v1/ping", ping(2), T0 - 40 * DAY); // installazione vecchia...
await call("POST", "/v1/ping", ping(2), T0 - 1 * DAY);  // ...ancora attiva
await call("POST", "/v1/ping", ping(3), T0 - 40 * DAY); // installazione abbandonata
assert.equal((await call("POST", "/v1/ping", { id: "x" })).status, 400);
const big = await call("POST", "/v1/ping", { ...ping(4), pad: "x".repeat(5000) });
assert.equal(big.status, 400);

// "no" anonimi e controlli di versione
await call("POST", "/v1/declined", { version: "0.1.0" });
await call("POST", "/v1/declined", { version: "0.1.0" });
assert.equal((await call("POST", "/v1/declined", {})).status, 400);
r = await call("GET", "/v1/latest");
assert.equal((await r.json()).latest, "0.2.0");

// numeri: solo con il token
assert.equal((await call("GET", "/v1/stats")).status, 401);
assert.equal((await call("GET", "/v1/stats", undefined, T0, { authorization: "Bearer sbagliato" })).status, 401);
r = await call("GET", "/v1/stats", undefined, T0, { authorization: "Bearer " + env.STATS_TOKEN });
assert.equal(r.status, 200);
const s = await r.json();
assert.equal(s.installs_total, 3);
assert.equal(s.active_1d, 1);
assert.equal(s.active_7d, 2);
assert.equal(s.active_30d, 2);
assert.equal(s.declined_total, 2);
assert.equal(s.update_checks_30d, 1);
assert.equal(s.consent_rate, 3 / 5);
assert.deepEqual(s.retention_30d, { base: 2, kept: 1, rate: 0.5 });
assert.equal(s.daily.length, 90);
assert.equal(s.daily.at(-1).active, 1);
assert.equal(s.daily.at(-1).declined, 2);
assert.deepEqual(s.features.map((f) => f.k).sort(), ["sms", "telegram"]);

// cancellazione di un ID su richiesta
await call("POST", "/v1/ping", ping(9));
assert.equal((await call("POST", "/v1/forget", { id: "zz" })).status, 400);
assert.equal((await call("POST", "/v1/forget", { id: id(9) })).status, 200);
assert.equal((await stats(env, T0)).installs_total, 3);

// la pagina
r = await call("GET", "/");
assert.match(await r.text(), /Mirto – statistiche anonime/);
assert.equal((await call("GET", "/nulla")).status, 404);

// pulizia dopo 13 mesi
await cleanup(env, T0 + 400 * DAY);
const after = await stats(env, T0 + 400 * DAY);
assert.equal(after.installs_total, 0);
assert.equal(after.declined_total, 0);

console.log("worker: tutte le prove superate");
