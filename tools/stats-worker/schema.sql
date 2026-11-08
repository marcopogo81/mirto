-- Statistiche anonime di Mirto (Cloudflare D1). Nessun indirizzo IP, nessun nome: solo ID casuali e fasce.
CREATE TABLE IF NOT EXISTS installs (
  id TEXT PRIMARY KEY,          -- numero casuale creato da Mirto con il consenso (azzerabile dall'utente)
  first_day TEXT NOT NULL,      -- AAAA-MM-GG del primo battito
  last_day TEXT NOT NULL,       -- AAAA-MM-GG dell'ultimo battito
  version TEXT, os TEXT, lang TEXT, cameras TEXT, nodes TEXT, recording TEXT,
  features TEXT                 -- elenco separato da virgole
);
CREATE TABLE IF NOT EXISTS pings (      -- un battito al giorno per installazione: attivi, fedeltà
  day TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY (day, id)
);
CREATE TABLE IF NOT EXISTS declines (   -- i "no" anonimi: solo quanti, per giorno e versione
  day TEXT NOT NULL, version TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, version)
);
CREATE TABLE IF NOT EXISTS checks (     -- domande "qual è l'ultima versione?" senza consenso: solo quante
  day TEXT NOT NULL PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS installs_last ON installs(last_day);
