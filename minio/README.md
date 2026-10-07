# MinIO

S3-compatible object storage, migrated from the official TrueNAS MinIO app to
a repo-managed Custom App.

- S3 API: port `9000`
- Web console (portal button in the Apps UI): port `9002`
- Image: `minio/minio:RELEASE.2025-04-08T15-41-24Z` — the last community
  build MinIO published to Docker Hub. Renovate keeps the digest pinned, but
  do not expect newer `RELEASE.*` tags to appear.

## Storage

The deployer manages dataset `tank/red/Applications/minio` (`MINIO_DATASET`
in `.truenas-generated.env`). Buckets and the `.minio.sys` metadata tree live
in its `export/` subdirectory — the same directory the official app used, so
existing buckets carry over unchanged.

The container runs as `473:473` (plus the TrueNAS apps group `568`) to match
the ownership of `export/` left behind by the official app.

## Credentials

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` live in `minio/.env` on the
TrueNAS repo clone (never committed; see `.env.example`). Reuse the values
from the converted app so existing clients and `mc` aliases keep working.

## Migration notes

The converted app's YAML included several official-app artifacts that are
intentionally dropped here:

- The `permissions` sidecar service, its `configs`, and the shared `tmp`
  volume only ever operated on a scratch volume, not on the real datasets;
  ownership of `export/` already matches the container user.
- `data/`, `postgres-data/`, and `postgres-backup/` siblings of `export/`
  belong to the official app's optional Log Search (Postgres) feature, which
  was never enabled (the converted app had no postgres service), so they are
  no longer mounted. The datasets still exist on disk; inspect them and
  delete manually once you are confident nothing there is needed — the
  deployer never deletes datasets.
- Resource limits (2 CPUs / 2048M) were official-app defaults and were not
  carried over; re-add `deploy.resources.limits` if MinIO needs bounding.

## Operation

Inspect on the TrueNAS host:

```sh
docker compose -p ix-minio ps
```

Before enabling the cron deployer for this app, confirm the converted Custom
App is named exactly `minio` in the Apps UI — the deployer matches apps by
that name — and create `minio/.env` with the real credentials.
