# applications

This repository stores app definitions that can be deployed to TrueNAS SCALE as
Custom Apps.

The TrueNAS workflow lives under `truenas/`. Each app has a `manifest.yaml` and
`compose.yaml`; `truenas/deploy.py` is intended to run from cron on the TrueNAS
host.

Dry-run locally or on TrueNAS:

```sh
python3 truenas/deploy.py --dry-run --skip-pull
```
