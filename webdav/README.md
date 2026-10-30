# WebDAV

WebDAV server built on [Caddy](https://caddyserver.com/) with the
[`webdav`](https://caddyserver.com/docs/modules/http.handlers.webdav) handler
module ([`mholt/caddy-webdav`](https://github.com/mholt/caddy-webdav)) — a thin
wrapper around `golang.org/x/net/webdav`, the same WebDAV engine family
rclone's `rclone serve webdav` uses. It serves one existing host path over
WebDAV with HTTP basic auth.

Configuration choices in this definition:

- **Image**: `ghcr.io/kusold/caddy-webdav:2`, built in
  [kusold/containers](https://github.com/kusold/containers)
  (`apps/caddy-webdav`): upstream Caddy on distroless/static with the webdav
  module compiled in. Pinned by digest; Renovate keeps the tag and digest
  current.
- **Auth**: Caddy `basic_auth` with bcrypt hashes. The whole Caddyfile —
  credentials included — lives in the deployer-managed dataset
  `tank/red/Applications/webdav` (mounted read-only at `/etc/caddy`), so
  credentials never touch this repo. Anonymous access is possible by deleting
  the `basic_auth` block.
- **Served data**: `WEBDAV_SHARE_PATH` from `.env`, an existing host path not
  managed by the deployer, mounted at `/srv/share1` and served at
  `http://<host>:30035/`.
- **Class 2**: the module provisions an in-memory lock system, so `LOCK` /
  `UNLOCK` work (WebDAV Class 2, like Apache's `mod_dav`). Locks live only in
  the process and are lost on restart.
- **TLS**: plain HTTP, like the other apps on this host. The catalog app's
  optional HTTPS listener (port 30036) is not configured.

## Setup

This app is managed by the cron deployer in `../deploy.py`.

1. Clone this repository on the TrueNAS host.

2. Create a local `.env` file beside `compose.yaml` with the path to serve
   (see `.env.example`):

   ```sh
   WEBDAV_SHARE_PATH=/mnt/tank/path-to-serve
   ```

3. Make sure the served path is readable (and writable, unless read-only) by
   uid/gid 568 (or change `user:` in `compose.yaml` to match its owner):

   ```sh
   chown -R 568:568 /mnt/tank/path-to-serve
   ```

4. Create the Caddyfile in the config dataset (created automatically by the
   deployer, or ahead of time). TrueNAS does not ship `caddy`, so use the
   official image to hash the password — `hash-password` is core Caddy, so
   this works before `ghcr.io/kusold/caddy-webdav` exists:

   ```sh
   docker run --rm caddy:2 caddy hash-password --plaintext 'my-password'
   # $2a$14$...
   ```

   Then write the Caddyfile (the raw bcrypt output goes in verbatim):

   ```sh
   sudo install -d -m 755 /mnt/tank/red/Applications/webdav
   sudo tee /mnt/tank/red/Applications/webdav/Caddyfile >/dev/null <<'EOF'
   {
   	order webdav before file_server
   }

   :30035 {
   	root * /srv/share1
   	basic_auth {
   		# docker run --rm caddy:2 caddy hash-password --plaintext 'my-password'
   		myuser $2a$14$...
   	}
   	webdav
   }
   EOF
   sudo chmod 644 /mnt/tank/red/Applications/webdav/Caddyfile
   ```

   Two lines in there are load-bearing:

   - `order webdav before file_server` is required. `webdav` is a plugin
     directive with no default position, and Caddy refuses to start without
     it ("directive 'webdav' is not an ordered HTTP handler").
   - `root * /srv/share1` is the container path; `webdav` picks it up from
     the site root.

   Add more users by adding `username $2a$14$...` lines inside the
   `basic_auth` block.

5. Run a dry run from the repository root:

   ```sh
   python3 deploy.py --dry-run --skip-pull --app webdav
   ```

6. Run the deployer from cron without `--dry-run` on the TrueNAS host.

## Image and updates

The image is built in [kusold/containers](https://github.com/kusold/containers)
(`apps/caddy-webdav`, added in
[kusold/containers#645](https://github.com/kusold/containers/pull/645)) and
was first published on 2026-10-30. The compose file pins it by digest;
Renovate keeps the tag and digest current, and a merged Renovate PR deploys
on the next cron run. Nothing in this repo builds or publishes the image —
bump the plugin pin in the Dockerfile over there (it is pinned to a commit
SHA and bumped by hand, like the tailscale plugin).

## Paths

| Host | Container | Purpose |
|------|-----------|---------|
| `WEBDAV_SHARE_PATH` (`.env`) | `/srv/share1` | The served data (existing host path). |
| `tank/red/Applications/webdav` | `/etc/caddy` (ro) | Caddyfile incl. bcrypt hashes. Deployer-managed. |
| tmpfs (16 MiB, mode 0777) | `/data` | Caddy autosave/instance scratch (`XDG_DATA_HOME`; the image's dirs are owned by its own user 65532, this app runs as 568). |
| tmpfs (16 MiB, mode 0777) | `/config` | Caddy config scratch (`XDG_CONFIG_HOME`, same reason). |

The image's `CMD` reads the Caddyfile from `/etc/caddy/Caddyfile`; mounting
the whole dataset over `/etc/caddy` (read-only) replaces the image's default
Caddyfile. Caddy exits immediately if the file is missing — that is the
intended loud failure for "forgot step 4".

## Ports

- `30035`: WebDAV over HTTP (container and host port, matching the catalog
  app default). This is the TrueNAS portal target. No conflict with backrest
  (9898) or versitygw (7070/7071).

There is no container healthcheck: the distroless image ships no shell,
curl, or any other binary to run a probe with, and an HTTP probe would fail
behind basic auth anyway. TrueNAS stack-level health therefore only ever
shows "running"; use the curl checks below (or a real client) to verify the
server actually answers.

## Verifying

```sh
# Unauthenticated request must be rejected:
curl -s -o /dev/null -w '%{http_code}\n' http://<host>:30035/        # 401
# Authenticated PROPFIND must list the share:
curl -s -u myuser:my-password -X PROPFIND -H 'Depth: 0' \
  -o /dev/null -w '%{http_code}\n' http://<host>:30035/              # 207
```

## Tweaks

All of these go inside the `:30035` site block of the Caddyfile.

Read-only share (the catalog app's "Read Only" share option) — reject the
write methods and let everything else through (PUT and DELETE answer 403,
GET and PROPFIND keep working):

```caddyfile
	@write method PUT MKCOL COPY MOVE DELETE PROPPATCH POST PATCH
	respond @write 403
```

Serve under the old catalog path prefix (`http://<host>:30035/<share-name>`)
instead of `/`. The `prefix` option matters: `x/net/webdav` builds absolute
hrefs, so without it clients see responses pointing outside the prefix:

```caddyfile
	webdav /share1/* {
		prefix /share1
	}
```

Browser directory listings (like Apache's `Indexes`), keeping WebDAV methods
on the same path. `GET`/`HEAD` get the listing; everything else falls through
to the webdav handler:

```caddyfile
	@get method GET HEAD
	route {
		file_server @get browse
		webdav
	}
```

Upload size cap (the catalog app's 1 GiB `LimitRequestBody` default; there
is no limit unless you add one):

```caddyfile
	request_body {
		max_size 1GiB
	}
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

4. **Recreate credentials**: hash the old `webdav.username` /
   `webdav.password` with `caddy hash-password` (Setup step 4) and write the
   Caddyfile into the config dataset. (If the old app used
   `auth_type: none` and you want to keep it anonymous, delete the
   `basic_auth` block from the template instead.)
5. **Check ownership**: if the old app's `run_as` was not 568/568, either
   `chown -R 568:568` the share or set `user:` in `compose.yaml` to the old
   uid/gid so caddy can read (and write) the existing files.
6. **Old client URLs**: the catalog app served each share under
   `/<share-name>` (for example `http://host:30035/share1`). This definition
   serves the share at `/`. If clients depend on the old path, use the
   `prefix` tweak above.
7. **Deploy**: `python3 deploy.py --app webdav`. Note the caveat in
   `../AGENTS.md`: portal and notes metadata must be correct at app creation
   time; changing them later may require recreating the TrueNAS app.
8. **Verify** with the curl checks above (401 unauthenticated, 207 for an
   authenticated PROPFIND), and from a real WebDAV client.
9. **Delete the old TrueNAS app** (Apps → webdav → delete), and remove the
   leftover `webdavhtbasic` htpasswd file from
   `/mnt/tank/red/Applications/webdav` if this app's previous Apache version
   created one. The deployer never deletes apps or datasets.

## Differences from the catalog app

- The share is served at `/` instead of `/<share-name>` (see step 6 above).
- HTTPS (the catalog app's optional 30036 listener) is not configured.
- Basic auth is always on; the catalog app defaulted to no authentication.
- No request-size cap: Apache's 1 GiB `LimitRequestBody` default is gone, so
  uploads are unlimited unless you add the `request_body` tweak above.
- Locks are in-memory (per-process) instead of Apache's on-disk
  `DavLockDB`, so `LOCK` state does not survive a container restart.
- Read-only serving is a Caddyfile snippet rather than a UI toggle.

## Icon

The manifest icon is the TrueNAS catalog app icon
(`https://media.sys.truenas.net/apps/webdav/icons/icon.png`). The URL ends
in `.png` but the bytes served are JPEG, so the manifest declares
`image/jpeg`.
