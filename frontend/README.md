# Frontend — UNSW Handbook Assistant

Minimal single-page chat view for the RAG backend. **Vite + React + TypeScript + Tailwind v4.**
Deliberately small — the backend is the star. It consumes the `/ask` SSE stream and renders
grounded answers with inline `[n]` citations linking to the source handbook pages.

## Requirements

Node **>=22.12** (Vite 8 / Rolldown needs it). An `.nvmrc` pins Node 22:

```bash
nvm use          # picks up .nvmrc → Node 22
```

## Run (dev)

The dev server proxies `/ask` and `/healthz` to the backend on `:8000`, so start the
backend first:

```bash
# from the repo root
docker compose up            # Postgres + FastAPI on :8000

# in this directory
npm install
npm run dev                  # Vite dev server on :5173
```

Open http://localhost:5173. Point the proxy elsewhere with `VITE_BACKEND_URL` if the
backend isn't on `localhost:8000`.

## Build

```bash
npm run build                # → dist/  (static, served same-origin by FastAPI in prod)
```

## How it talks to the backend

`src/api.ts` POSTs to `/ask` and parses the Server-Sent Events stream: one `meta` event
(citations + refusal flag), a run of `token` deltas, then `done`. Because the answer text
and the citation list share the same `[n]` numbering, `src/App.tsx` turns each inline marker
into a link to the matching source URL.
