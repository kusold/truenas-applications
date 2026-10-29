# WebDAV

WebDAV server built on the official [`httpd`](https://hub.docker.com/_/httpd)
(Apache) image with `mod_dav` — the same engine the TrueNAS community
**webdav** catalog app runs (catalog 1.3.9 pins `httpd:2.4.68`). It serves one
existing host path over WebDAV with HTTP basic auth.

Configuration choices in this definition:

- **Image**: official `library/httpd`, pinned by digest. Actively maintained;
  no third-party webdav image is involved.
- **Auth**: basic auth via an htpasswd file (bcrypt). The htpasswd file lives
  in the deployer-managed dataset `tank/red/Applications/webdav` (mounted
  read-only at `/etc/apache2/auth`), so credentials never touch this repo.
  Anonymous access is possible by deleting the auth lines (see
  `compose.yaml`).
- **Served data**: `WEBDAV_SHARE_PATH` from `.env`, an existing host path not
  managed by the deployer, mounted at `/shares/share1` and served at
  `http://<host>:30035/`.
- **Apache config**: the full `httpd.conf` replacement and the WebDAV vhost
  are flattened from the catalog app's 1.3.9 templates and shipped as inline
  compose `configs` — the same mechanism the TrueNAS app itself uses. Ports
  stay above 1024 and the pid/`DavLockDB` files live on a world-writable
  tmpfs, so httpd runs fully non-root as `568:568`.
- **TLS**: plain HTTP, like the other apps on this host. The catalog app's
  optional HTTPS listener (port 30036) is not configured.

## Setup

This app is managed by the cron deployer in `../deploy.py`.

1. Clone this repository on the TrueNAS host.
2. Create a local `.env` file beside `compose.yaml` with the path to serve:

   ```sh
   WEBDAV_SHARE_PATH=/mnt/tank/path-to-serve
   ```

3. Make sure the served path is readable (and writable, unless read-only) by
   uid/gid 568 (or change `user:` in `compose.yaml` to match its owner):

   ```sh
   chown -R 568:568 /mnt/tank/path-to-serve
   ```

4. Create the htpasswd file in the config dataset (created automatically by
   the deployer, or ahead of time). TrueNAS does not ship `htpasswd`, so use
   the image itself:

   ```sh
   docker run --rm httpd:2.4.69 htpasswd -nbB -C 12 myuser 'my-password' \
     | sudo tee /mnt/tank/red/Applications/webdav/webdavhtbasic
   sudo chmod 644 /mnt/tank/red/Applications/webdav/webdavhtbasic
   ```

   Add more users by appending without `-c` (`htpasswd -nbB ... >> ...`).
   bcrypt (`$2y$`) hashes are what the TrueNAS app generated and validate
   fine in Apache 2.4.

5. Run a dry run from the repository root:

   ```sh
   python3 deploy.py --dry-run --skip-pull --app webdav
   ```

6. Run the deployer from cron without `--dry-run` on the TrueNAS host.

Renovate keeps the image tag and digest current; merges deploy on the next
cron run.

## Paths

| Host | Container | Purpose |
|------|-----------|---------|
| `WEBDAV_SHARE_PATH` (`.env`) | `/shares/share1` | The served data (existing host path). |
| `tank/red/Applications/webdav` | `/etc/apache2/auth` (ro) | htpasswd store (`webdavhtbasic`). Deployer-managed. |
| tmpfs (100 MiB, mode 0777) | `/usr/local/apache2/var` | pid file + `DavLockDB` (writable scratch for non-root httpd). |
| inline config | `/usr/local/apache2/conf/httpd.conf` | Full Apache config (from the catalog 1.3.9 template). |
| inline config | `/usr/local/apache2/conf/Includes/webdav-http.conf` | WebDAV vhost: auth, `Dav On`, limits (from the catalog templates). |

## Ports

- `30035`: WebDAV over HTTP (container and host port, matching the catalog
  app default). This is the TrueNAS portal target and the healthcheck probe.
  No conflict with backrest (9898) or versitygw (7070/7071).

The container healthcheck is a raw TCP probe (`bash` `/dev/tcp`): the image
ships no curl/wget, and an HTTP probe would always return 401 behind basic
auth.

## Verifying

```sh
# Unauthenticated request must be rejected:
curl -s -o /dev/null -w '%{http_code}\n' http://<host>:30035/        # 401
# Authenticated PROPFIND must list the share:
curl -s -u myuser:my-password -X PROPFIND -H 'Depth: 0' \
  -o /dev/null -w '%{http_code}\n' http://<host>:30035/              # 207
```

## Migrating from the TrueNAS webdav catalog app

The old app (TrueNAS "WebDAV" community app, version 1.3.9) keeps no files
you can reuse directly: its htpasswd and Apache configs are regenerated
inline on every deploy, and it stores no ix-volume data
(`/mnt/.ix-apps/app_mounts/webdav/` is unused). All of its state worth
recovering is in its app values file — including the WebDAV password in
plaintext:

```sh
sudo cat /mnt/.ix-apps/app_configs/webdav/versions/1.3.9/user_config.yaml
```

Note down `webdav.username`, `webdav.password`, `webdav.auth_type`, the
`run_as` uid/gid, and every share's `host_path`, `name`, and `read_only`.

Then:

1. **Stop the old app** (it holds port 30035): Apps → webdav → stop, or
   `midclt call app.stop webdav`.
2. **Snapshot/backup** the dataset(s) backing each share's `host_path`:
   `zfs snapshot tank/<pool-path>@pre-webdav-migration`.
3. **Data does not need to move.** The old app served each share's host path
   directly; point the new app at the same path (`WEBDAV_SHARE_PATH` in
   `.env`) and the files are served in place. If you do want to relocate the
   data, rsync it preserving ownership (run as root on the TrueNAS host):

   ```sh
   rsync -aHAXv --numeric-ids /mnt/old/path/ /mnt/tank/new/path/
   ```

4. **Recreate credentials**: generate the htpasswd file from the old
   `webdav.username` / `webdav.password` using the command in Setup step 4.
   (If the old app used `auth_type: none` and you want to keep it anonymous,
   delete the auth lines in `compose.yaml`'s `webdav-http-conf` instead and
   skip the htpasswd file.)
5. **Check ownership**: if the old app's `run_as` was not 568/568, either
   `chown -R 568:568` the share or set `user:` in `compose.yaml` to the old
   uid/gid so httpd can read (and write) the existing files.
6. **Old client URLs**: the catalog app served each share under
   `/<share-name>` (for example `http://host:30035/share1`). This definition
   serves the share at `/`. If clients depend on the old path, add an
   `Alias /<share-name> "/shares/share1"` line in `webdav-http-conf` (a
   commented example is included).
7. **Deploy**: `python3 deploy.py --app webdav`. Note the caveat in
   `../AGENTS.md`: portal and notes metadata must be correct at app creation
   time; changing them later may require recreating the TrueNAS app.
8. **Verify** with the curl checks above (401 unauthenticated, 207 for an
   authenticated PROPFIND), and from a real WebDAV client.
9. **Delete the old TrueNAS app** (Apps → webdav → delete). The deployer
   never deletes apps or datasets.

## Differences from the catalog app

- The share is served at `/` instead of `/<share-name>` (see step 6 above).
- HTTPS (the catalog app's optional 30036 listener) is not configured.
- Basic auth is always on; the catalog app defaulted to no authentication.
- `LimitRequestBody` keeps the catalog default of 1 GiB per request; set it
  to `0` in `webdav-http-conf` for unlimited upload size.
- Read-only serving and per-share browser matches from the catalog UI have
  commented examples in `webdav-http-conf`.

## Icon

The manifest icon is the TrueNAS catalog app icon
(`https://media.sys.truenas.net/apps/webdav/icons/icon.png`). The URL ends
in `.png` but the bytes served are JPEG, so the manifest declares
`image/jpeg`.
