# Backrest

Backrest is a Web UI and scheduler for restic backups. This TrueNAS Custom App
definition follows the upstream Docker Compose example while using the minimal
scratch image.

## Setup

This app is managed by the cron deployer in `../deploy.py`.

1. Clone this repository on the TrueNAS host.
2. Edit `manifest.yaml` if your pool or dataset layout differs from the
   default `tank/appdata` and `tank/app-cache` layout.
3. Create a local `.env` file beside `compose.yaml` with values that are not
   managed by the dataset manifest. Start from `.env.example`:

```sh
BACKREST_USERDATA_PATH=/mnt/tank/path-to-back-up
```

4. Run a dry run from the repository root:

```sh
python3 truenas/deploy.py --dry-run --skip-pull --app backrest
```

5. Run the deployer from cron without `--dry-run` on the TrueNAS host.

If you change the published port in `compose.yaml`, update `manifest.yaml`
before creating the app so the TrueNAS portal is created correctly.

## Paths

- `${BACKREST_DATASET}:/data`: Backrest data directory.
- `${BACKREST_CONFIG_DATASET}:/config`: Backrest config file location.
- `${BACKREST_CACHE_DATASET}:/cache`: XDG cache directory.
- `${BACKREST_TMP_DATASET}:/tmp`: Temporary files.
- `${BACKREST_RCLONE_DATASET}:/root/.config/rclone`: rclone config location if
  you switch to an image that includes rclone.
- `${BACKREST_USERDATA_PATH}:/userdata:ro`: files available for backup.
- `${BACKREST_REPOS_DATASET}:/repos`: local restic repositories.

The default manifest keeps backup-worthy datasets under `tank/appdata`:

```text
tank/appdata/backrest/data
tank/appdata/backrest/config
tank/appdata/backrest/repos
tank/appdata/backrest/rclone
```

Disposable datasets live under `tank/app-cache`:

```text
tank/app-cache/backrest/cache
tank/app-cache/backrest/tmp
```

## Scratch Image Note

The upstream Docker image `ghcr.io/garethgeorge/backrest:latest` includes rclone
and common Unix utilities. This app intentionally uses
`ghcr.io/garethgeorge/backrest:scratch`, which is minimal. Use the default image
instead if you need rclone remotes or hook scripts that require shell tools.

After first launch, open `http://<truenas-host>:9898` and create the initial
Backrest user.
