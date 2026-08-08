# Deployment

## What's here

- `docker-compose.yml` — 4 services: `db` (Postgres + pgvector), `backend`
  (FastAPI), `frontend` (React, built and served via nginx), `scheduler`
  (the daily prediction/backtest jobs from Phase 6, no exposed ports).
- Each service's own `Dockerfile` lives in its folder.

**Note on testing this locally:** Docker wasn't available in the sandbox
this was built in, so the compose file and Dockerfiles are written and
YAML-validated but not run end-to-end here. Test locally with the steps
below before deploying — that's the real first checkpoint, not a formality.

## 1. Test locally with Docker Compose

```bash
cp .env.example .env
# fill in real values: POSTGRES_PASSWORD, JWT_SECRET_KEY (any long random
# string), OPENAI_API_KEY, ALPACA_API_KEY/SECRET_KEY

docker compose up --build
```

Then check:
- `http://localhost:8000/health` → `{"status": "ok"}`
- `http://localhost:8000/docs` → interactive API docs load
- `http://localhost` → the frontend loads and can reach the backend
- `docker compose logs scheduler` → confirms the scheduler container
  started without crashing (it won't do anything until 6am server time,
  per `ml/scheduler.py` — that's expected)

**Known gotcha to watch for:** `schema.sql` only runs automatically on
the *first* startup of the `db` container (Postgres's init-script
behavior — it skips init scripts if the data directory already has
data). If you change `db/schema.sql` later, apply the change manually:
```bash
docker compose exec db psql -U postgres -d fincopilot -f /docker-entrypoint-initdb.d/schema.sql
```
or `docker compose down -v` to wipe the volume and start fresh (this
deletes all data, so only do this in development).

**Upgrading an existing database:** `schema.sql` is the current shape,
not a patch — re-running it against a populated database won't add
columns to tables that already exist. Files in `db/migrations/` handle
that, in numeric order:
```bash
for f in db/migrations/*.sql; do
  docker compose exec -T db psql -U postgres -d fincopilot < "$f"
done
```
Skipping these is the usual cause of an `UndefinedColumn` error from the
backend or the daily jobs against a database that was created before the
column existed.

Most of these migrations are additive (they add a column). `005` is the
exception: it *replaces* a CHECK constraint on `predictions` that never
constrained anything, because it was written `IN ('up','down',NULL)` and a
NULL inside an `IN` list makes the whole expression evaluate to NULL for a
bad value — which a CHECK accepts. Applying it is safe and idempotent on a
populated database; if the `ALTER` fails, that means a bad value is already
stored, and the error names the row so you can decide what to do with it.

**Changing embedding provider** (`EMBEDDING_PROVIDER` in `.env`) needs no
SQL. The vector column's width belongs to whichever model embedded the
corpus, so `ingestion/chunk_and_embed.py` sets it to match the active
model — but only while `filing_chunks` is empty, where that is free. A
fresh clone with no OpenAI key therefore just works: the schema ships at
OpenAI's 1536, and the first keyless ingestion narrows it to the local
model's 384.

Switching provider on a database that already holds chunks is the one
case that costs something, because the existing vectors cannot be
converted — they have to be recomputed:

```bash
docker compose exec -T db psql -U postgres -d fincopilot -c "TRUNCATE filing_chunks;"
python ingestion/ingest_filing.py AAPL MSFT
```

Until you do, the app will not silently mix the two. If the configured
model and the model that embedded the corpus disagree, `/query` returns
409 naming both and telling you how to reconcile them, rather than
returning rankings computed from incomparable vectors.

`/health` reports the live half of that comparison under `embeddings`
(`provider`, `model`, `dimension`), so you can check what would embed a
query today without running one. Note it reports the model actually in
use, which is not always what `EMBEDDING_PROVIDER` says: `auto` resolves
against whether a key is set, and a value that isn't `auto`, `openai` or
`local` falls back rather than being honoured — so a typo shows up here
as `local`, not as the typo.

**Another gotcha:** the frontend bakes `VITE_API_BASE_URL` in at *build*
time, not runtime (this is how Vite works — see the comment in
`frontend/Dockerfile`). If you change that URL, you must rebuild the
frontend image (`docker compose build frontend`), not just restart it.

## 2. Deploy to a free host

Railway and Fly.io both handle multi-service docker-compose-style
projects on free tiers reasonably well. Railway is the simpler path if
this is your first time deploying something like this.

### Railway
1. Push this repo to GitHub
2. In Railway, "New Project" → "Deploy from GitHub repo"
3. Add a Postgres plugin (use Railway's managed Postgres, not the `db`
   service in docker-compose.yml — Railway's plugin doesn't run your
   custom init script, so instead run `schema.sql` against it manually
   once, from your machine: `psql <railway-connection-string> -f db/schema.sql`
   — you'll need to `CREATE EXTENSION vector;` first, which Railway's
   Postgres supports)
4. Add two services from the repo: point one at `/backend`, one at
   `/frontend` (Railway auto-detects each `Dockerfile`)
5. Set environment variables in Railway's dashboard for each service
   (same names as `.env.example`)
6. Add a third service for `/ml` (the scheduler) — background worker,
   no public port needed

### Fly.io
Similar shape, but you'll write one `fly.toml` per service (`backend`,
`frontend`, `ml`) since Fly deploys one app per config. Fly's own
Postgres offering works the same way as Railway's — managed instance,
schema applied manually once.

## 3. Post-deploy smoke test

Once live, walk through the whole user flow on the real URL, not just
individual endpoints:
1. Register a new account
2. Ask a question in the chat page — confirm a real, cited answer comes back
3. Look up a ticker's prediction/backtest
4. Buy and sell a few shares on the portfolio page
5. Check `/portfolio/history` starts accumulating once the scheduler has
   run at least once. To seed it immediately for a demo rather than
   waiting for the nightly cycle:
   `docker compose exec scheduler python scheduler.py --now`
   That also evaluates standing alerts, so it's the fastest way to
   confirm the Alerts page is wired end to end.

   Sharpe, volatility, drawdown and `/portfolio/vs-benchmark` need **two**
   snapshots on separate days, so they stay null until the cycle has run
   on a second day — that's expected, not a failure.

If any of these breaks in production but worked locally, the most common
causes are: a missing environment variable in the hosting dashboard, the
frontend still pointing at `localhost` (rebuild needed, see gotcha
above), or the database missing `schema.sql` (managed Postgres services
don't run it automatically — see step 1's Railway note).
