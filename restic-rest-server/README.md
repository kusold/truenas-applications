# Restic REST Server

[Restic REST server](https://github.com/restic/rest-server) is the official
HTTP server backend for restic. Remote restic clients back up to repositories
under the server's data directory using
`rest:http://<user>:<pass>@<host>:8000/<user>/` URLs. This definition runs the
same container image the TrueNAS catalog app runs (`restic/rest-server`),
replacing the catalog Custom App with a plain Compose file.

Configuration choices in this definition:

- **Image**: `restic/rest-server:0.14.0` pinned by digest — the current stable
  release and the exact image the TrueNAS catalog app (1.1.12) pins. Renovate
  keeps the tag/digest current.
- **Port**: upstream default `8000:8000`. The TrueNAS catalog app defaulted to
  host port `30248`; see [Migration](#migration-from-the-truenas-catalog-app)
  before first deploy if existing clients use that port.
- **Data**: restic repositories live in `tank/red/Backups/restic-rest-server`
  mounted at `/data` (deployer-managed, backup payload tree).
- **Auth**: bcrypt `.htpasswd` in `tank/red/Applications/restic-rest-server`
  mounted at `/config`, referenced via `PASSWORD_FILE=/config/.htpasswd`. The
  TrueNAS catalog app likewise kept the htpasswd out of `/data` (it generated
  `/auth/htpasswd` from the users list in app config). Manage users with the
  image's `create_user` helper (bcrypt, same format).
- **User**: runs as `568:568` (`apps`), matching the TrueNAS catalog app, so
  files migrated from the old app keep working without a chown. Both datasets
  must be owned by `568:568` (the deployer creates datasets root-owned —
  chown them once, below).
- **`--private-repos`**: each authenticated user gets a repository at
  `/data/<username>` and cannot see other users' repositories. Client URLs
  must include the `/username/` suffix.
- **TLS**: plain HTTP, like the other apps on this host.
- **No portal**: the server has no real UI; `GET /` returns `401` without
  credentials, so there is no `x-portals` entry in the manifest.

## Setup

This app is managed by the cron deployer in `../deploy.py`. No `.env` file is
required — the only interpolated values are the deployer-generated dataset
paths.

1. Clone this repository on the TrueNAS host.
2. Pre-create the datasets and hand them to the `apps` user (the deployer can
   create them, but leaves them root-owned, which prevents the container from
   writing its `.htpasswd`):

   ```sh
   zfs create -p tank/red/Applications/restic-rest-server
   zfs create -p tank/red/Backups/restic-rest-server
   chown 568:568 /mnt/tank/red/Applications/restic-rest-server \
                 /mnt/tank/red/Backups/restic-rest-server
   ```

3. Run a dry run from the repository root:

   ```sh
   python3 deploy.py --dry-run --skip-pull --app restic-rest-server
   ```

4. Run the deployer without `--dry-run` on the TrueNAS host (from cron or
   manually) to create the Custom App.

5. Create the first user (bcrypt, written to `/config/.htpasswd`):

   ```sh
   docker exec -it restic-rest-server create_user myuser
   ```

6. Point restic clients at the server:

   ```sh
   restic -r rest:http://myuser:secret@<truenas-host>:8000/myuser/ init
   ```

## Ports

- `8000`: restic REST API. The only port; also what restic client URLs
  reference. (In use elsewhere in this repo: backrest 9898, versitygw
  7070/7071 — no conflict.)

## Paths

- `${RESTIC_REST_DATA_DATASET}:/data`: restic repositories (backup payload,
  `tank/red/Backups/restic-rest-server`).
- `${RESTIC_REST_CONFIG_DATASET}:/config`: `.htpasswd` and future operator
  config (`tank/red/Applications/restic-rest-server`).

Only `.htpasswd` files with bcrypt hashes are supported (upstream restriction;
`create_user` and `htpasswd -B` both produce them).

## Healthcheck

`rest-server` 0.14.0 has no unauthenticated HTTP endpoint (`GET /` returns
`401` once auth is on; `/healthz` only ships after 0.14.0), so the healthcheck
is the same process check the TrueNAS catalog app used (`pgrep rest-server`).
Switch it to an HTTP probe against `/healthz` once an image newer than 0.14.0
is pinned.

## Options not enabled

- `--append-only`: reject destructive commands (forget/prune) server-side.
- `--group-accessible-repos`: make repo files group-readable.
- `--tls` / `--tls-cert` / `--tls-key`: terminate HTTPS on the server instead
  of plain HTTP (would also require mounting cert/key files).
- `--prometheus` / `--prometheus-no-auth`: Prometheus metrics endpoint.

Add flags to the `OPTIONS` environment variable in `compose.yaml` to enable
any of these. Keep `--private-repos` unless old clients address repositories
outside their own username (dropping it keeps those URLs working).

## Migrating from the TrueNAS catalog app

The old app stored its data in an ixVolume (default) at
`/mnt/.ix-apps/app_mounts/restic-rest-server/data` and generated its
`.htpasswd` inside the container (`/auth/htpasswd`) from the users list in the
app configuration — the htpasswd is **not** in the data directory and must be
recreated. Runbook:

1. **Record the old settings** while the app still exists: the web port
   (catalog default `30248`), and whether *private repositories* or
   *append-only* were enabled (App settings, or
   `midclt call app.query | grep -A5 restic`).
2. **Stop the old app** (Apps → restic-rest-server → Stop). It holds the old
   port and the data must be at rest during the copy.
3. **Snapshot the old data** as a safety net (find the dataset with
   `zfs list | grep .ix-apps`):

   ```sh
   zfs snapshot <ix-apps-dataset>/apps/restic-rest-server@pre-compose-migration
   ```

4. **Pre-create and chown the new datasets** (see Setup step 2), or let the
   deployer create them and `chown 568:568` both afterwards.
5. **Copy the repositories** from the TrueNAS shell (root), preserving
   ownership (files are owned by `568:568`):

   ```sh
   rsync -aHAX --info=progress2 \
     /mnt/.ix-apps/app_mounts/restic-rest-server/data/ \
     /mnt/tank/red/Backups/restic-rest-server/
   ```

6. **Port check**: if old clients use the catalog default port `30248` and you
   want their URLs unchanged, edit `ports` in `compose.yaml` to
   `"30248:8000"` **before** the first deploy (with no portal there is
   nothing else to update; otherwise update client URLs to `:8000`).
7. **Deploy**: `python3 deploy.py --app restic-rest-server` (the wrapper
   notes are rendered from `manifest.yaml` at creation time; TrueNAS may not
   pick up later `x-notes` edits without recreating the app).
8. **Recreate users**: the passwords are not recoverable from the old app
   config; re-enter them per user:

   ```sh
   docker exec -it restic-rest-server create_user <name>
   ```

   or, on a host with `htpasswd`:
   `htpasswd -B -c /mnt/tank/red/Applications/restic-rest-server/.htpasswd <name>`.
9. **Verify**: container healthy, then run a client check:

   ```sh
   curl -u <name>:<pass> http://<truenas-host>:8000/   # 200
   restic -r rest:http://<name>:<pass>@<truenas-host>:8000/<name>/ snapshots
   ```

10. **Delete the old app** from the Apps UI once everything checks out. The
    deployer never deletes apps or datasets; removing the old app frees its
    ixVolume data (`/mnt/.ix-apps/app_mounts/restic-rest-server`) — keep the
    step 3 snapshot until you are confident.
