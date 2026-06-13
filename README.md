# applications

This repository stores app definitions that can be deployed to TrueNAS SCALE as
Custom Apps.

Each app is a top-level directory containing a `manifest.yaml` and
`compose.yaml`. The `deploy.py` script is intended to run from cron on the
TrueNAS host.

Dry-run locally or on TrueNAS:

```sh
python3 deploy.py --dry-run --skip-pull
```
