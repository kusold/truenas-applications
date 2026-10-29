# Syncthing

[Syncthing](https://syncthing.net/) is a continuous file synchronization
program. This app replaces the TrueNAS catalog app (`syncthing` catalog
version 1.3.14, which shipped Syncthing 2.1.5) with the official upstream
image `syncthing/syncthing`, pinned by digest and kept current by Renovate.

Configuration choices in this definition:

- **Image**: official `syncthing/syncthing` (Alpine-based), digest-pinned.
- **Ports**: upstream defaults instead of the catalog app's host ports —
  GUI `8384`, sync protocol `22000/tcp` and `22000/udp` (QUIC), local
  discovery announcements `21027/udp`. See [Migration](#migration-from-the-truenas-catalog-app)
  before changing them.
- **GUI address**: `STGUIADDRESS=0.0.0.0:8384` is set explicitly. The stock
  `config.xml` default (`127.0.0.1:8384`) is unreachable through a published
  port, so the environment override pins the listen address the same way the
  catalog app did.
- **Process user**: the image entrypoint starts as root, `chown`s
  `/var/syncthing`, then drops to `PUID:PGID` (from `.env`; defaults 568/568,
  the TrueNAS catalog defaults).
- **Upgrades**: `STNOUPGRADE=true`. Image updates come from Renovate digest
  bumps plus the cron deployer, never from inside the container.
- **Dataset layout**: one backup-worthy dataset,
  `tank/red/Applications/syncthing`, mounted at `/var/syncthing`. It holds
  `config/` (`config.xml`, `cert.pem`/`key.pem` device keys, the index
  database) and the default `Sync` folder. No separate cache dataset:
  Syncthing keeps its database inside the config directory and its transfer
  temp files (`.syncthing.*.tmp`) inside each folder, so there is no distinct
  cache tree to split off; the database is also what preserves folder index
  state across restarts, so treating it as disposable is not worth the
  re-scan cost.
- **Synced data**: `SYNCTHING_DATA_PATH` (from `.env`) is mounted at
  `/data`. Add more host-path volumes in `compose.yaml` for folders on other
  datasets; Syncthing folder paths are container-side paths.

## Setup

This app is managed by the cron deployer in `../deploy.py`.

1. Clone this repository on the TrueNAS host.
2. Create a local `.env` file beside `compose.yaml`. Start from
   `.env.example` and set the path containing the folders to sync:

   ```sh
   PUID=568
   PGID=568
   TZ=Etc/UTC
   SYNCTHING_DATA_PATH=/mnt/tank/path-to-sync
   ```

3. Run a dry run from the repository root:

   ```sh
   python3 deploy.py --dry-run --skip-pull --app syncthing
   ```

4. Run the deployer from cron without `--dry-run` on the TrueNAS host. It
   creates the dataset (`tank/red/Applications/syncthing`) automatically.

Renovate keeps the image tag and digest current; merges deploy on the next
cron run.

## Ports

- `8384`: Web GUI. This is the TrueNAS portal target and the healthcheck
  probe (`GET /rest/noauth/health`).
- `22000/tcp`: sync protocol between devices.
- `22000/udp`: sync protocol over QUIC.
- `21027/udp`: local discovery announcements. Note that with Docker bridge
  networking, discovery broadcasts do not cross the bridge, so LAN peers
  locate this device through global discovery or explicit addresses — the
  same limitation the catalog app had (it did not publish this port at all).

If you change the published ports in `compose.yaml`, update `manifest.yaml`
before creating the app so the TrueNAS portal is created correctly.

## Paths

| Mount | Host path | Purpose |
|-------|-----------|---------|
| `/var/syncthing` | `tank/red/Applications/syncthing` (deployer-managed) | `config/` with `config.xml`, device keys, database; default `Sync` folder |
| `/data` | `SYNCTHING_DATA_PATH` from `.env` | Folders to sync |
| other paths | additional volumes in `compose.yaml` | Folders on other datasets |

## Migration from the TrueNAS catalog app

The catalog app (1.3.14) stores everything in one ix-volume mounted at
`/var/syncthing`:

```text
/mnt/.ix-apps/app_mounts/syncthing/config        <- entire /var/syncthing
/mnt/.ix-apps/app_mounts/syncthing/config/config <- syncthing config dir
```

Device identity lives in `config/cert.pem` and `config/key.pem`; folders,
devices, and acceptances live in `config/config.xml`; the folder index
database under `config/` avoids full re-hashes on first start. Copying the
whole volume preserves all of it.

Runbook:

1. Record the old app's actual settings. Defaults are host ports
   `20910` (GUI), `20978` -> `22000/tcp`, `20970` -> `22000/udp`, and
   `PUID/PGID 568/568`, but the install may differ — check:

   ```sh
   grep -A5 -E 'web_port|tcp_port|udp_port|user|group' \
     /mnt/.ix-apps/app_configs/syncthing/versions/*/user_config.yaml
   ```

   Also note any additional storage entries (host paths and their container
   mount paths) — those folder paths live in `config.xml` and must keep
   working after the migration (step 8).
2. Stop the old TrueNAS app (Apps -> syncthing -> stop). It holds the ports.
3. Back up the old config:

   ```sh
   tar -C /mnt/.ix-apps/app_mounts/syncthing -cf /root/syncthing-pre-migration.tar config
   ```

   Keep this until the migration is verified.
4. Create `syncthing/.env` from `.env.example` (set `SYNCTHING_DATA_PATH`,
   and `PUID`/`PGID` to the values found in step 1).
5. Deploy and then stop the container so the dataset exists but nothing
   writes to it:

   ```sh
   python3 deploy.py --app syncthing
   docker compose -p ix-syncthing stop syncthing
   ```

6. Copy the old volume over the new dataset, preserving ownership and
   timestamps, then fix ownership to the run IDs:

   ```sh
   rsync -aHAX --info=progress2 \
     /mnt/.ix-apps/app_mounts/syncthing/config/ \
     /mnt/tank/red/Applications/syncthing/
   chown -R 568:568 /mnt/tank/red/Applications/syncthing   # or your PUID:PGID
   ```

   (The dataset root corresponds to the old ix-volume root: both are
   container `/var/syncthing`.)
7. GUI address: the old app forced `STGUIADDRESS=0.0.0.0:20910`. This
   compose file pins `0.0.0.0:8384`, so the GUI moves to
   `http://<truenas-host>:8384` regardless of what `config.xml` says.
8. Folder paths: `config.xml` stores container-side paths. The default
   folder (`/var/syncthing/Sync`) keeps working unchanged. Folders that used
   the old app's additional-storage mounts need their container paths to
   exist again — either add matching volume mounts in `compose.yaml` or
   edit the folder path in the GUI (Folder -> Edit -> Folder Path) to a path
   under `/data`.
9. Start the app and verify:

   ```sh
   docker compose -p ix-syncthing start syncthing
   ```

   Open `http://<truenas-host>:8384`, confirm the device ID is unchanged
   (Actions -> Show ID), and wait for remote devices to reconnect.
10. Update remote peers that dial this host with a **static address** (for
    example `tcp://truenas-host:20978`) to the new sync port
    `tcp://truenas-host:22000`. Peers relying on global discovery pick the
    new address up on their own.
11. Only after verifying: delete the old TrueNAS app manually (Apps ->
    syncthing -> delete). The deployer never deletes apps or datasets. Keep
    the tar backup from step 3 for a while longer.

Per `AGENTS.md`, the portal button and notes are rendered at app creation
time; if the portal is wrong, delete and recreate the app rather than
editing it in the UI.
