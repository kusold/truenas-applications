# VersityGW

[VersityGW](https://github.com/versity/versitygw/) is an S3-compatible gateway.
This app runs it with the `posix` backend, exposing one existing host path as
an S3 endpoint. Files written over S3 land directly in that path and stay
accessible via normal POSIX tools, and vice versa.

Configuration choices in this definition:

- **Backend**: `posix`, serving `VGW_DATA_PATH` (from `.env`) at `/data`.
- **Versioning**: `VGW_VERSIONING_DIR` points at `/versioning`, backed by
  `VGW_VERSIONING_PATH` from `.env` (see below).
- **Auth**: multi-tenant mode via the internal (file-based) IAM service.
  `VGW_IAM_DIR` points at `/iam`, backed by the deployer-managed dataset
  `tank/red/Applications/versitygw`. The root account
  (`ROOT_ACCESS_KEY` / `ROOT_SECRET_KEY` in `.env`) is defined at gateway
  runtime and never stored in the IAM directory; every other account (admin,
  userplus, user) lives in `users.json` there. See
  [Multi-tenancy](#multi-tenancy).
- **Web UI**: enabled on port 7071 (`VGW_WEBUI_PORT`). Root and admin
  logins get the admin dashboard (users, buckets, explorer); other accounts
  get the Explorer only. The browser calls the
  S3 API on 7070 directly, so the endpoint the UI uses must be reachable from
  LAN clients: `VGW_WEBUI_GATEWAYS` pins it to `http://truenas.local:7070`.
  The gateway's auto-detected default is its own interface IPs, which inside
  the container are unroutable Docker bridge addresses. CORS is not an issue:
  with the Web UI enabled the gateway defaults `Access-Control-Allow-Origin`
  to `*` (see the startup warning), and the UI does not send credentials.
- **TLS**: plain HTTP, like the other apps on this host.
- **State**: IAM accounts only. Object data lives in `VGW_DATA_PATH` and
  `VGW_VERSIONING_PATH` (operator-chosen host paths, not managed by the
  deployer); the IAM account store is the one deployer-managed dataset,
  `tank/red/Applications/versitygw`, mounted at `/iam`.

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

4. Run the deployer from cron without `--dry-run` on the TrueNAS host. It
   creates the IAM dataset (`tank/red/Applications/versitygw`) automatically.

Renovate keeps the image tag and digest current; merges deploy on the next
cron run.

## Ports

- `7070`: S3 API endpoint (the admin API is served on this port too).
- `7071`: Web UI (Explorer for everyone; admin dashboard for root/admin).
  This is the TrueNAS portal target, and the container healthcheck probes it
  because the S3 port rejects unauthenticated requests.

## Using the S3 endpoint

Point any S3 client at `http://<truenas-host>:7070` with the root credentials
from `.env`. For example, with the AWS CLI:

```sh
aws --endpoint-url http://<truenas-host>:7070 s3 ls
aws --endpoint-url http://<truenas-host>:7070 s3 mb s3://my-bucket
aws --endpoint-url http://<truenas-host>:7070 s3 cp file.txt s3://my-bucket/
```

Buckets are directories under `VGW_DATA_PATH`; objects are files.

## Multi-tenancy

The gateway runs with the internal IAM service, following the
[upstream multi-tenant setup](https://github.com/versity/versitygw/wiki/Multi-Tenant):
`VGW_IAM_DIR` → `/iam`, backed by the deployer-managed dataset
`tank/red/Applications/versitygw`. Accounts and roles:

| Role     | Capabilities |
|----------|--------------|
| root     | Everything; can manage accounts and buckets and sees all buckets. Defined by `ROOT_ACCESS_KEY`/`ROOT_SECRET_KEY` in `.env`, never stored in the IAM directory. |
| admin    | Create/delete admin+user accounts, create buckets, see all buckets, assign bucket ownership. |
| userplus | Like `user`, but may also create buckets. |
| user     | Access only the buckets they own. |

### Managing accounts and buckets

The Web UI admin dashboard (port 7071, log in with root or an admin account)
covers the whole workflow: create/delete users, create buckets with a chosen
owner, and change bucket ownership. Regular `user` accounts cannot create
buckets — an admin (or root) creates the bucket and assigns it.

The same operations are available from the `versitygw admin` CLI, served on
the S3 port:

```sh
docker exec versitygw versitygw admin \
  --access "$ROOT_ACCESS_KEY" --secret "$ROOT_SECRET_KEY" \
  --endpoint-url http://127.0.0.1:7070 \
  create-user --access myuser --secret '<user-secret>' --role user

docker exec versitygw versitygw admin \
  --access "$ROOT_ACCESS_KEY" --secret "$ROOT_SECRET_KEY" \
  --endpoint-url http://127.0.0.1:7070 \
  change-bucket-owner --bucket my-bucket --owner myuser
```

Other subcommands: `list-users`, `update-user`, `delete-user`,
`list-buckets`, `create-bucket`, `delete-bucket`.

Tenant clients point at the same endpoint with their own credentials:
`http://<truenas-host>:7070`.

### Caveats

- `users.json` is plain text JSON **including secret keys** (written 0600,
  root-owned). The dataset sits under the recursively backed-up Applications
  tree — treat its snapshots and replication accordingly.
- Buckets that existed before multi-tenancy was enabled stay owned by root;
  reassign them with `change-bucket-owner` as needed. Changing a bucket's
  owner **removes its existing ACLs and bucket policies** (by design, for
  security) — copy out anything you want to keep first.
- Bucket policies apply to `user`/`userplus` accounts only; they are ignored
  for `admin` and unavailable for root.
- The internal IAM service is single-writer. Fine for this one-container
  deployment; do not point a second gateway instance at the same IAM
  directory and expect coordinated writes.

## Versioning

The gateway starts with a versioning directory (`VGW_VERSIONING_PATH` on the
host, `/versioning` in the container). Versioning is then enabled **per
bucket** through the S3 API:

```sh
aws --endpoint-url http://<truenas-host>:7070 s3api put-bucket-versioning \
  --bucket my-bucket --versioning-configuration Status=Enabled
```

On overwrite or delete, the previous object version is copied into the
versioning directory (keyed by a hash of the object name), and
`list-object-versions` / `--version-id` restores work as in S3.

Placement rules, enforced or recommended by versitygw itself:

- The versioning path **must be outside `VGW_DATA_PATH`** — the gateway fails
  to start if the versioning directory is inside the gateway root.
- Keep it on the **same ZFS dataset** as the data path (a sibling directory
  works well). Version copies can then use the kernel same-filesystem copy
  path; a separate filesystem still works but copies are slower.
- Do **not** point it at a separate child dataset unless you accept the slower
  copy path — child datasets are distinct filesystems for this purpose.

## Options not enabled

- **Other IAM backends**: the internal file-based IAM store is used; LDAP,
  Vault, FreeIPA, and S3-backed IAM are available upstream if directory
  integration is ever needed.
- **TLS**: `--cert`/`--cert-key` (or the `VGW_CERT`/`VGW_CERT_KEY` env vars)
  can terminate HTTPS on the gateway instead.

See the [versitygw wiki](https://github.com/versity/versitygw/wiki) for the
full option list.
