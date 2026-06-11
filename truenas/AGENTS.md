# AGENTS.md

This directory manages Docker applications intended to run on TrueNAS SCALE as
Custom Apps, following the workflow in Techno Tim's "How I Run Docker on TrueNAS
Like a Pro" post:

https://technotim.com/posts/truenas-docker-pro/

The operating model is simple: keep application definitions as clean Docker
Compose YAML, keep persistent app files in per-app TrueNAS datasets, and let the
TrueNAS Custom App point at those Compose files with an `include` wrapper.

## Directory Model

Use one directory per application:

```text
truenas/
  app-name/
    manifest.yaml
    compose.yaml
    .env.example
    README.md
```

The application directory should mirror the intended TrueNAS dataset name where
possible. For example, `truenas/home-assistant/compose.yaml` corresponds to a
dataset mounted on TrueNAS at a path such as:

```text
/mnt/<pool>/home-assistant/compose.yaml
```

Prefer lowercase, hyphenated app directory names that match the Compose service
or container name.

## App Manifest

Each managed app must include `manifest.yaml`. This file is the deployment
allowlist and contains the TrueNAS app name, Compose entrypoint, datasets to
create, optional portal metadata, optional notes, and optional icon data.

```yaml
app:
  name: app-name
  enabled: true
  compose: compose.yaml
  env_file: .env

icon:
  media_type: image/png
  base64: "<base64-encoded-image>"

datasets:
  - dataset: tank/appdata/app-name/data
    compose_env: APP_DATASET
  - dataset: tank/app-cache/app-name/cache
    compose_env: APP_CACHE_DATASET

portal:
  name: Web UI
  port: 8080
  scheme: http
  path: /

notes: |
  Operator notes shown in TrueNAS.
```

`dataset` is the ZFS dataset name, not the `/mnt/...` path. The deployer creates
missing datasets and writes generated Compose environment values such as:

```sh
APP_DATASET=/mnt/tank/appdata/app-name/data
```

`compose_env` is the environment variable that `compose.yaml` uses in bind
mounts. Keep backup-worthy datasets under a tree that is recursively backed up,
and place cache/tmp datasets under a separate tree that backup jobs skip.
`app.env_file` defaults to `.env`; when present, the deployer includes it after
the generated env file for host-specific values that should not be committed.

## Cron Deployer

Run `truenas/deploy.py` from TrueNAS cron. It pulls the repo, scans
`truenas/*/manifest.yaml`, creates missing datasets, installs missing Custom
Apps, updates changed apps, writes icon metadata, and redeploys changed apps so
Docker images are pulled and containers restart.

Use dry-run mode before enabling cron:

```text
python3 truenas/deploy.py --dry-run --skip-pull
```

The script uses `truenas_api_client.Client` for live deploys. It does not delete
apps or datasets; disabled or removed manifests require manual cleanup.

## TrueNAS Custom App Wrapper

TrueNAS Custom Apps should include the app Compose file instead of inlining all
service definitions into the UI. This keeps the Compose file, `.env` files, and
optional `Dockerfile` in a normal filesystem directory that can be edited,
tested, copied to another machine, and backed up with git.

For TrueNAS 25.10 and newer, include the empty `services` key in the Custom App
YAML:

```yaml
include:
  - /mnt/<pool>/<app-name>/compose.yaml
services: {}
```

The deployer renders this wrapper from `manifest.yaml`; do not commit generated
wrapper YAML unless the path is intentionally part of a manual deployment
contract.

The longer include form can attach one or more env files to the included Compose
file. Use this when variables must be available to the Compose interpreter
during interpolation, such as image tags, host paths, or values shared across
stacks:

```yaml
services: {}
include:
  - path: /mnt/<pool>/stacks/<app-name>/compose.yaml
    env_file:
      - /mnt/<pool>/stacks/.env.global
      - /mnt/<pool>/stacks/<app-name>/.env
```

This is different from a service-level `env_file`. Service-level `env_file` is
still useful, but it may not solve interpolation of values used by Compose
itself before services are created.

## TrueNAS Custom Config Extensions

TrueNAS accepts top-level extension keys in the Custom App YAML. These are not
normal Docker Compose service settings; they are consumed by TrueNAS for the Apps
UI.

Use this full wrapper shape when an app needs notes or a Web UI portal button:

```yaml
services: {}
include:
  - path: /mnt/<pool>/<app-name>/compose.yaml
x-notes: |
  Short operator notes for this app.
  Include URLs, credentials location hints, restore notes, or special warnings.
x-portals:
  - host: 0.0.0.0
    name: Web UI
    path: /
    port: 8080
    scheme: http
```

`x-notes` schema:

```yaml
x-notes: |
  <freeform multiline text>
```

- Type: YAML block scalar string.
- Purpose: notes shown by TrueNAS for the app.
- Keep indentation under the `|` consistent.
- Do not put secrets in notes.

`x-portals` schema:

```yaml
x-portals:
  - host: <ip-address-or-fqdn>
    name: <button-label>
    path: <url-path>
    port: <host-port>
    scheme: <http-or-https>
```

- Type: list of portal objects.
- `host`: hostname, FQDN, app IP, TrueNAS IP, or `0.0.0.0` if TrueNAS should
  resolve the host generically.
- `name`: label for the portal button, for example `Web UI`.
- `path`: URL path, usually `/`.
- `port`: port the browser should connect to. This is normally the published
  host port or dedicated app IP port, not an arbitrary container-only port.
- `scheme`: `http` or `https`.

Add `x-notes` and `x-portals` during initial app creation when possible. Forum
reports indicate that adding or changing these fields after the app already
exists may not update the portal button or notes reliably; recreating the
TrueNAS app may be required if these values were wrong at creation time.

## Compose Conventions

- Use `compose.yaml` for the app's Docker Compose file.
- Define app containers under top-level `services`.
- Prefer `restart: unless-stopped` for long-running services.
- Add healthchecks for HTTP services when practical.
- Use `.env` for local deployment values such as `PUID`, `PGID`, `TZ`, domains,
  static IPs, ports, and non-managed host paths.
- Commit `.env.example`, not real `.env` files, generated env files, or secrets.
- If a service needs a custom image, keep its `Dockerfile` beside
  `compose.yaml` and use `build.context: .` so TrueNAS builds it relative to the
  included Compose file.
- Use `${COMPOSE_ENV}` bind mounts for datasets declared in `manifest.yaml`.
- Use absolute `/mnt/<pool>/...` mounts only when a service needs a path that is
  not managed by the deployer.
- Avoid adding dashboard-specific formats unless requested; this repo's source
  of truth is Compose.

## Networking

Most apps should use normal Docker bridge networking and explicit `ports`
mappings. Use external networks only when the app actually needs them.

For LAN-native services, macvlan can be used, but document the exact interface,
subnet, gateway, and reserved container IP before adding the Compose file. Any
external network referenced in Compose must already exist on the TrueNAS host,
for example:

```yaml
networks:
  iot_macvlan:
    external: true
```

Be careful with static IP assignments. Reusing an IP already present on the LAN
can take the app or another device offline.

## TrueNAS UI And CLI Behavior

The Apps UI treats an included multi-container Compose file as one app or stack.
Stack-level actions such as stop and upgrade apply to the whole stack, not to
individual containers. The UI may show that an update exists without identifying
which container image triggered it.

Existing CLI-created stacks can be migrated into the TrueNAS Custom App flow by
pointing the wrapper `include` at the existing Compose file, but stop the old
stack before creating the TrueNAS app so TrueNAS can bring it up under its own
project name.

For CLI inspection of TrueNAS-managed apps, start with:

```text
docker compose ls
```

TrueNAS project names commonly use the form `ix-<app-name>`. Use that project
name for commands such as:

```text
docker compose -p ix-<app-name> ps
```

Some commands may still need `-f` with the runtime Compose file path, while
simple validation can often be done from the app directory with
`docker compose config` or `docker compose pull`.

## Icons And Metadata

TrueNAS Custom Apps may show blank icons by default. If icon metadata is needed,
document it in the app README rather than silently relying on UI state.

When `manifest.yaml` includes an `icon` block, the deployer writes an offline
safe data URI into TrueNAS app metadata. The metadata file lives on TrueNAS
under:

```text
/mnt/.ix-apps/app_configs/<app-name>/metadata.yaml
```

Prefer offline-safe `data:image/...;base64,...` icons when the deployment should
not depend on external image URLs.

## Validation

Before considering an app definition ready:

1. Run `docker compose config` from the app directory when Docker Compose is
   available.
2. Confirm required `.env.example` values are documented.
3. Check that manifest datasets line up with Compose bind mount environment
   variables.
4. Check that host ports and static IPs do not conflict with existing apps.
5. For TrueNAS 25.10+, confirm the Custom App wrapper includes `services: {}`.
6. If the app has a UI, include or document the intended `x-portals` entry.
7. If operator notes matter, include or document the intended `x-notes` text.

If validation depends on the actual TrueNAS host, say so clearly in the handoff.
