# Silo

S3-compatible object storage — [Silo](https://silo.pgsty.com/), the
community-maintained MinIO fork published by [Pigsty](https://pigsty.io).
It replaced the MinIO app here after MinIO wound down its community
edition. Silo keeps the S3 wire protocol, `MINIO_*` environment variables,
`mc` client compatibility, and the on-disk format, so the existing data
serves unchanged.

- S3 API: port `9000`
- Web console (portal button in the Apps UI): port `9002` (Silo's default
  is 9001; 9002 is kept from the MinIO app so the portal and bookmarks do
  not change)
- Image: `pgsty/silo`, digest-pinned; Renovate tracks new `RELEASE.*` tags
  (the fork publishes roughly every 1–2 months)

## Storage

The deployer manages dataset `tank/red/Applications/silo` (`SILO_DATASET`
in `.truenas-generated.env`). Buckets and the `.minio.sys` metadata tree
live in its `export/` subdirectory, migrated as-is from the MinIO app —
Silo reads MinIO's on-disk format directly, so no rebuild or copy is
needed.

The container runs as `473:473` (plus the TrueNAS apps group `568`) to
match the ownership `export/` already has.

## Credentials

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` live in `silo/.env` on the
TrueNAS repo clone (never committed; see `.env.example`). Silo keeps the
`MINIO_*` names. Reuse the MinIO values so existing clients and `mc`
aliases keep working.

## Migration from the MinIO app

Silo is a drop-in replacement, but the TrueNAS app name, dataset name, and
image all change, so the switchover is manual (the deployer never deletes
apps or datasets):

1. Merge this branch so it lands in the repo clone on the TrueNAS host.
2. Stop the `minio` custom app so ports 9000/9002 are free.
3. Rename the dataset (any child datasets rename with it) so the
   deployer-managed path matches:
   ```sh
   zfs list -r tank/red/Applications/minio   # confirm what exists
   zfs rename tank/red/Applications/minio tank/red/Applications/silo
   ```
   If `tank/red/Applications/minio` is a plain directory rather than a
   dataset, use `mv` instead.
4. Create `silo/.env` in the TrueNAS repo clone with the same
   `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` values.
5. Deploy: `python3 deploy.py --app silo`. This creates the `silo` custom
   app pointing at the renamed dataset. Do this before the next cron run —
   without the rename, the deployer would create a fresh empty `silo`
   dataset and the app would come up with no buckets.
6. Verify: console at `http://<truenas>:9002`, bucket listing, and one S3
   client. Then delete the stopped `minio` custom app from the Apps UI.

Rollback within a short window: stop the `silo` app, rename the dataset
back, and redeploy the MinIO definition from git history (the `add-minio`
commit). Keep the window short — a newer Silo release may write
`.minio.sys` metadata that the old MinIO build cannot read; check Silo's
[compatibility notes](https://silo.pgsty.com/compatibility/server/) before
rolling back across versions.

## Leftovers from the official app

`data/`, `postgres-data/`, and `postgres-backup/` siblings of `export/`
belong to the official MinIO app's optional Log Search (Postgres) feature,
which was never enabled. They moved along with the dataset rename; inspect
and delete them manually once confident nothing there is needed.

## Operation

Inspect on the TrueNAS host:

```sh
docker compose -p ix-silo ps
```

The image bundles the Silo client as `mcli` (with an `mc` compatibility
alias) for ad-hoc bucket work:

```sh
docker exec silo mcli alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
docker exec silo mcli ls local
```
