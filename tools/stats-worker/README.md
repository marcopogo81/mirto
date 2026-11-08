# Server delle statistiche anonime (Cloudflare Worker)

Riceve i messaggi anonimi di Mirto (`mirto/telemetry.py`) e mostra i numeri su una pagina protetta da un
token. Gira sul **piano gratuito** di Cloudflare (Workers + database D1): basta per migliaia di installazioni.

Cosa conta: installazioni attive (1, 7, 30 giorni), nuove, ancora attive dopo 30 e 90 giorni, quante hanno detto
sì e quante no, versioni, sistemi, lingue, camere e nodi per fasce, funzioni accese, controlli di versione.
Cosa **non** salva: indirizzi IP, nomi, testo libero (ogni campo deve rispettare un formato fisso). I registri delle richieste sono spenti in
`wrangler.toml` e i dati più vecchi di 13 mesi si cancellano ogni notte. Dettagli in `PRIVACY.md` alla radice.

## Metterlo in linea (una volta, ~15 minuti)

Servono un account Cloudflare gratuito (dash.cloudflare.com → Sign up) e Node.js sul Mac (`brew install node`).

```sh
cd ~/Documents/Mirto/tools/stats-worker
npx wrangler login                                   # apre il browser: autorizza Wrangler
npx wrangler d1 create myhouse-stats                 # stampa database_id: copialo in wrangler.toml
npx wrangler d1 execute myhouse-stats --remote --file=schema.sql
openssl rand -hex 24                                 # il token per vedere i numeri: salvalo nel Portachiavi
npx wrangler secret put STATS_TOKEN                  # incolla il token
npx wrangler deploy                                  # stampa l'indirizzo, es. https://myhouse-stats.<nome>.workers.dev
```

Poi:

1. Apri l'indirizzo nel browser e incolla il token: vedi la pagina dei numeri (vuota all'inizio).
2. Scrivi l'indirizzo in `ENDPOINT` in `mirto/telemetry.py` (per Mirto è già fatto:
   `https://myhouse-stats.myhousesurveillance.workers.dev`), fai il commit e pubblica.
   Finché `ENDPOINT` è vuoto Mirto non invia niente. Per provare prima: in `config.yaml`
   `telemetry: {endpoint: "https://…workers.dev"}` oppure la variabile `MIRTO_STATS_URL`.

## A ogni nuova versione di Mirto

Cambia `LATEST_VERSION` in `wrangler.toml` e rilancia `npx wrangler deploy`: dal giorno dopo Mirto avvisa chi
ha una versione più vecchia (una volta per versione).

## Indirizzi

| Indirizzo | Cosa fa |
|---|---|
| `POST /v1/ping` | battito giornaliero di chi ha detto sì (ID casuale + fasce) |
| `POST /v1/declined` | il «no» anonimo, una volta sola, senza ID |
| `POST /v1/forget` | cancella i dati di un ID (consenso ritirato o ID rinnovato) |
| `GET /v1/latest` | ultima versione; conta solo quante domande al giorno |
| `GET /v1/stats` | i numeri in JSON, con `Authorization: Bearer <STATS_TOKEN>` |
| `GET /` | pagina con i numeri e il grafico degli ultimi 90 giorni |

## Limiti noti

Gli indirizzi sono pubblici: chiunque potrebbe gonfiare i conteggi. Se succede, in Cloudflare → Security →
WAF si aggiunge una regola di *rate limiting* gratuita su `/v1/` (es. 20 richieste al minuto per IP).

## Spegnere tutto

`MIRTO_STATS_URL=off` (variabile d'ambiente) o `telemetry: {endpoint: "off"}` in `config.yaml`: Mirto non
contatta nessun server. Le prove automatiche lo fanno sempre (`tests/__init__.py`).

## Prove

`npm test` (Node 22 o più recente): prova il Worker con un database finto in memoria.
