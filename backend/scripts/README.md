# Backend scripts

One-off scripts. **Nothing here is deployed** — the Dockerfile copies only
`app/` and `start_api.py`.

| Directory | Purpose |
|---|---|
| `ops/` | Worker and server process management |
| `data/` | One-off data operations (these mutate real data — read before running) |
| `simulations/` | End-to-end pipeline exercises |

The `backend/` root is reserved for deploy-referenced entrypoints
(`start_api.py`, and anything named in `Procfile` or `Dockerfile`). Do not add
scripts there.
