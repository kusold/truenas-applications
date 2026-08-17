# VersityGW

[VersityGW](https://github.com/versity/versitygw/) is an S3-compatible gateway.
This app runs it with the `posix` backend, exposing one existing host path as
an S3 endpoint. Files written over S3 land directly in that path and stay
accessible via normal POSIX tools, and vice versa.

Configuration choices in this definition:

- **Backend**: `posix`, serving `VGW_DATA_PATH` (from `.env`) at `/data`.
- **Auth**: single-user mode. One root account (`ROOT_ACCESS_KEY` /
  `ROOT_SECRET_KEY` in `.env`) is used for both S3 requests and Web UI login.
  There is no IAM service, so the Web UI shows only the Explorer.
- **Web UI**: enabled on port 7071 (`VGW_WEBUI_PORT`).
- **TLS**: plain HTTP, like the other apps on this host.
- **State**: none. The gateway is stateless, so `manifest.yaml` declares no
  datasets; all data lives in `VGW_DATA_PATH`.

## Setup

This app is managed by the cron deployer in `../deploy.py`.

1. Clone this repository on the TrueNAS host.
2. Create a local `.env` file beside `compose.yaml`. Start from
   `.env.example` and set the path to serve and the root credentials:

   ```sh
   VGW_DATA_PATH=/mnt/tank/some-dataset
   ROOT_ACCESS_KEY=<access-key>
   ROOT_SECRET_KEY=<secret-key>
   ```

3. Run a dry run from the repository root:

   ```sh
   python3 deploy.py --dry-run --skip-pull --app versitygw
   ```

4. Run the deployer from cron without `--dry-run` on the TrueNAS host.

Renovate keeps the image tag and digest current; merges deploy on the next
cron run.

## Ports

- `7070`: S3 API endpoint.
- `7071`: Web UI (Explorer). This is the TrueNAS portal target, and the
  container healthcheck probes it because the S3 port rejects unauthenticated
  requests.

## Using the S3 endpoint

Point any S3 client at `http://<truenas-host>:7070` with the root credentials
from `.env`. For example, with the AWS CLI:

```sh
aws --endpoint-url http://<truenas-host>:7070 s3 ls
aws --endpoint-url http://<truenas-host>:7070 s3 mb s3://my-bucket
aws --endpoint-url http://<truenas-host>:7070 s3 cp file.txt s3://my-bucket/
```

Buckets are directories under `VGW_DATA_PATH`; objects are files.

## Options not enabled

- **Versioning**: add a second mount and set `VGW_ARGS` to include
  `--versioning-dir /versioning` if object version retention is needed.
- **IAM service**: switching from single-user to an IAM directory
  (`VGW_IAM_DIR` on a persistent dataset) enables multiple accounts, roles,
  and the Web UI admin dashboard.
- **TLS**: `--cert`/`--cert-key` (or the `VGW_CERT`/`VGW_CERT_KEY` env vars)
  can terminate HTTPS on the gateway instead.

See the [versitygw wiki](https://github.com/versity/versitygw/wiki) for the
full option list.
