#!/usr/bin/env python3
"""Deploy repo-managed TrueNAS Custom Apps from cron."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - TrueNAS is Unix-like.
    fcntl = None


APP_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
DATASET_RE = re.compile(r"^[A-Za-z0-9_.:-]+(/[A-Za-z0-9_.:-]+)+$")
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class DeployError(Exception):
    pass


@dataclass(frozen=True)
class DatasetMount:
    dataset: str
    compose_env: str

    @property
    def mount_path(self) -> str:
        return f"/mnt/{self.dataset}"


@dataclass(frozen=True)
class AppManifest:
    app_dir: Path
    app_name: str
    enabled: bool
    compose_path: Path
    env_file_path: Path
    datasets: list[DatasetMount]
    portal: dict[str, Any] | None
    notes: str | None
    icon: dict[str, str] | None

    @property
    def generated_env_path(self) -> Path:
        return self.app_dir / ".truenas-generated.env"

    @property
    def metadata_path(self) -> Path:
        return Path("/mnt/.ix-apps/app_configs") / self.app_name / "metadata.yaml"


def run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise DeployError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except ModuleNotFoundError:
        yq = shutil.which("yq")
        if yq is None:
            raise DeployError(
                f"Cannot parse {path}: install PyYAML or provide yq in PATH"
            )
        commands = ([yq, "-o=json", str(path)], [yq, ".", str(path)])
        errors: list[str] = []
        for command in commands:
            proc = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout)
            errors.append(proc.stderr.strip())
        raise DeployError(f"Cannot parse {path}: {'; '.join(errors)}")


def yaml_quote(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.match(r"^[A-Za-z0-9_./:@+-]+$", text):
        return text
    return json.dumps(text)


def validate_manifest(path: Path, raw: Any) -> AppManifest:
    if not isinstance(raw, dict):
        raise DeployError(f"{path}: manifest must be a mapping")

    app = raw.get("app")
    if not isinstance(app, dict):
        raise DeployError(f"{path}: app must be a mapping")

    app_name = app.get("name")
    if not isinstance(app_name, str) or not APP_NAME_RE.match(app_name):
        raise DeployError(f"{path}: app.name must match {APP_NAME_RE.pattern}")
    if len(app_name) > 40:
        raise DeployError(f"{path}: app.name must be at most 40 characters")

    enabled = app.get("enabled", True)
    if not isinstance(enabled, bool):
        raise DeployError(f"{path}: app.enabled must be a boolean")

    compose = app.get("compose", "compose.yaml")
    if (
        not isinstance(compose, str)
        or compose.startswith("/")
        or ".." in Path(compose).parts
    ):
        raise DeployError(f"{path}: app.compose must be a relative path")
    compose_path = path.parent / compose
    if not compose_path.is_file():
        raise DeployError(f"{path}: app.compose does not exist: {compose}")

    env_file = app.get("env_file", ".env")
    if (
        not isinstance(env_file, str)
        or env_file.startswith("/")
        or ".." in Path(env_file).parts
    ):
        raise DeployError(f"{path}: app.env_file must be a relative path")
    env_file_path = path.parent / env_file

    raw_datasets = raw.get("datasets", [])
    if not isinstance(raw_datasets, list):
        raise DeployError(f"{path}: datasets must be a list")
    datasets: list[DatasetMount] = []
    seen_env: set[str] = set()
    for index, entry in enumerate(raw_datasets, start=1):
        if not isinstance(entry, dict):
            raise DeployError(f"{path}: datasets[{index}] must be a mapping")
        dataset = entry.get("dataset")
        compose_env = entry.get("compose_env")
        if not isinstance(dataset, str) or not DATASET_RE.match(dataset):
            raise DeployError(f"{path}: datasets[{index}].dataset is invalid")
        if dataset.startswith("/mnt/") or dataset.startswith("/"):
            raise DeployError(f"{path}: datasets[{index}].dataset must be a ZFS name")
        if not isinstance(compose_env, str) or not ENV_RE.match(compose_env):
            raise DeployError(f"{path}: datasets[{index}].compose_env is invalid")
        if compose_env in seen_env:
            raise DeployError(f"{path}: duplicate compose_env {compose_env}")
        seen_env.add(compose_env)
        datasets.append(DatasetMount(dataset=dataset, compose_env=compose_env))

    portal = raw.get("portal")
    if portal is not None:
        if not isinstance(portal, dict):
            raise DeployError(f"{path}: portal must be a mapping")
        for key in ("name", "port", "scheme", "path"):
            if key not in portal:
                raise DeployError(f"{path}: portal.{key} is required")
        if portal["scheme"] not in ("http", "https"):
            raise DeployError(f"{path}: portal.scheme must be http or https")
        if not isinstance(portal["port"], int):
            raise DeployError(f"{path}: portal.port must be an integer")

    notes = raw.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise DeployError(f"{path}: notes must be a string")

    icon = raw.get("icon")
    if icon is not None:
        if not isinstance(icon, dict):
            raise DeployError(f"{path}: icon must be a mapping")
        media_type = icon.get("media_type")
        encoded = icon.get("base64")
        if not isinstance(media_type, str) or not media_type.startswith("image/"):
            raise DeployError(f"{path}: icon.media_type must start with image/")
        if not isinstance(encoded, str):
            raise DeployError(f"{path}: icon.base64 must be a string")
        try:
            base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise DeployError(f"{path}: icon.base64 is invalid: {exc}") from exc

    return AppManifest(
        app_dir=path.parent,
        app_name=app_name,
        enabled=enabled,
        compose_path=compose_path,
        env_file_path=env_file_path,
        datasets=datasets,
        portal=portal,
        notes=notes,
        icon=icon,
    )


def discover_manifests(repo_root: Path) -> list[AppManifest]:
    manifests: list[AppManifest] = []
    for manifest_path in sorted(repo_root.glob("*/manifest.yaml")):
        manifests.append(validate_manifest(manifest_path, load_yaml(manifest_path)))
    return manifests


def render_env(manifest: AppManifest) -> str:
    lines = [
        "# Generated by truenas/deploy.py. Do not edit by hand.",
        f"TRUENAS_APP_NAME={manifest.app_name}",
    ]
    for dataset in manifest.datasets:
        lines.append(f"{dataset.compose_env}={dataset.mount_path}")
    return "\n".join(lines) + "\n"


def render_wrapper(manifest: AppManifest) -> str:
    compose_path = manifest.compose_path.resolve()
    env_path = manifest.generated_env_path.resolve()
    lines = [
        "services: {}",
        "include:",
        f"  - path: {yaml_quote(compose_path)}",
        "    env_file:",
        f"      - {yaml_quote(env_path)}",
    ]
    if manifest.env_file_path.exists():
        lines.append(f"      - {yaml_quote(manifest.env_file_path.resolve())}")

    if manifest.notes:
        lines.append("x-notes: |")
        lines.extend(f"  {line}" if line else "" for line in manifest.notes.splitlines())

    if manifest.portal:
        portal = manifest.portal
        lines.extend(
            [
                "x-portals:",
                f"  - host: {yaml_quote(portal.get('host', '0.0.0.0'))}",
                f"    name: {yaml_quote(portal['name'])}",
                f"    path: {yaml_quote(portal['path'])}",
                f"    port: {yaml_quote(portal['port'])}",
                f"    scheme: {yaml_quote(portal['scheme'])}",
            ]
        )

    return "\n".join(lines) + "\n"


def render_metadata(manifest: AppManifest, existing: dict[str, Any] | None = None) -> str | None:
    if not manifest.icon:
        return None
    import ast

    data = dict(existing or {})
    # TrueNAS custom apps store metadata as a string repr of a dict;
    # official apps use a proper nested YAML mapping. Handle both.
    meta = data.get("metadata")
    if isinstance(meta, str):
        try:
            meta = ast.literal_eval(meta)
        except (ValueError, SyntaxError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta["icon"] = (
        f"data:{manifest.icon['media_type']};base64,{manifest.icon['base64']}"
    )
    data["metadata"] = meta
    # Remove stale top-level icon from previous deploy runs.
    data.pop("icon", None)
    try:
        import yaml  # type: ignore

        return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except ModuleNotFoundError:
        yq = shutil.which("yq")
        if yq is None:
            raise DeployError("Cannot write metadata: install PyYAML or provide yq in PATH")
        proc = subprocess.run(
            [yq, "-P", json.dumps(data)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise DeployError(f"yq failed to format metadata: {proc.stderr.strip()}")
        return proc.stdout


def git_revision(repo_root: Path) -> str | None:
    try:
        return run(["git", "rev-parse", "HEAD"], repo_root)
    except DeployError:
        return None


def git_pull(
    repo_root: Path, dry_run: bool, skip_pull: bool
) -> tuple[str | None, str | None]:
    before = git_revision(repo_root)
    if skip_pull:
        return before, before
    if dry_run:
        print("DRY-RUN git pull --ff-only")
        return before, before
    run(["git", "pull", "--ff-only"], repo_root)
    return before, git_revision(repo_root)


def changed_app_dirs(
    repo_root: Path,
    before: str | None,
    after: str | None,
    manifests: list[AppManifest],
) -> set[Path]:
    changed: set[Path] = set()
    paths: list[str] = []
    status_paths: list[str] = []
    if before and after and before != after:
        paths.extend(
            run(
                ["git", "diff", "--name-only", before, after],
                repo_root,
            ).splitlines()
        )
    try:
        status_paths.extend(
            run(["git", "status", "--porcelain"], repo_root).splitlines()
        )
    except DeployError:
        pass
    paths.extend(raw[3:] for raw in status_paths if len(raw) > 3)

    manifest_dirs = {m.app_dir.resolve() for m in manifests}
    for raw in paths:
        if not raw:
            continue
        rel = raw.strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        parts = Path(rel).parts
        if len(parts) >= 2:
            candidate = (repo_root / parts[0] / parts[1]).resolve()
            if candidate in manifest_dirs:
                changed.add(candidate)
    return changed


def write_text(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        print(f"DRY-RUN write {path}")
        print(content.rstrip())
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as fh:
        fh.write(content)
        tmp_name = fh.name
    os.replace(tmp_name, path)


def read_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if ENV_RE.match(key):
            keys.add(key)
    return keys


def check_compose_vars(manifest: AppManifest, dry_run: bool) -> None:
    compose = manifest.compose_path.read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose))
    generated = {dataset.compose_env for dataset in manifest.datasets}
    local = read_env_keys(manifest.env_file_path)
    missing = sorted(required - generated - local)
    if not missing:
        return
    message = (
        f"{manifest.compose_path}: missing values for {', '.join(missing)}; "
        f"add them to {manifest.env_file_path}"
    )
    if dry_run:
        print(f"DRY-RUN warning: {message}")
    else:
        raise DeployError(message)


def load_existing_apps(client: Any | None) -> dict[str, dict[str, Any]]:
    if client is None:
        return {}
    apps = client.call("app.query", [], {"extra": {"retrieve_config": True}})
    return {app["name"]: app for app in apps}


def dataset_exists(client: Any, dataset: str) -> bool:
    return bool(client.call("pool.dataset.query", [["id", "=", dataset]], {"limit": 1}))


def ensure_datasets(client: Any | None, manifest: AppManifest, dry_run: bool) -> None:
    for dataset in manifest.datasets:
        if client is None:
            print(f"DRY-RUN ensure dataset {dataset.dataset}")
            continue
        if dataset_exists(client, dataset.dataset):
            print(f"dataset exists: {dataset.dataset}")
            continue
        payload = {
            "name": dataset.dataset,
            "type": "FILESYSTEM",
            "create_ancestors": True,
            "share_type": "APPS",
        }
        if dry_run:
            print(f"DRY-RUN pool.dataset.create {json.dumps(payload, sort_keys=True)}")
        else:
            print(f"creating dataset {dataset.dataset}")
            client.call("pool.dataset.create", payload)


def apply_app(
    client: Any | None,
    manifest: AppManifest,
    installed_apps: dict[str, dict[str, Any]],
    changed_dirs: set[Path],
    dry_run: bool,
) -> None:
    env_content = render_env(manifest)
    wrapper = render_wrapper(manifest)

    check_compose_vars(manifest, dry_run)
    write_text(manifest.generated_env_path, env_content, dry_run)

    installed = manifest.app_name in installed_apps
    changed = manifest.app_dir in changed_dirs

    if client is None:
        action = "create" if not installed else ("update+redeploy" if changed else "skip")
        print(f"DRY-RUN app {manifest.app_name}: {action}")
        print(wrapper.rstrip())
    elif not installed:
        print(f"creating app {manifest.app_name}")
        client.call(
            "app.create",
            {
                "app_name": manifest.app_name,
                "custom_app": True,
                "custom_compose_config_string": wrapper,
            },
            job=True,
        )
    elif changed:
        print(f"updating app {manifest.app_name}")
        client.call(
            "app.update",
            manifest.app_name,
            {"custom_compose_config_string": wrapper},
            job=True,
        )
        print(f"redeploying app {manifest.app_name}")
        client.call("app.redeploy", manifest.app_name, job=True)
    else:
        print(f"unchanged app {manifest.app_name}; skipping redeploy")

    existing_metadata = None
    if manifest.icon and manifest.metadata_path.exists():
        loaded_metadata = load_yaml(manifest.metadata_path)
        if isinstance(loaded_metadata, dict):
            existing_metadata = loaded_metadata
    metadata = render_metadata(manifest, existing_metadata)
    if metadata is not None:
        write_text(manifest.metadata_path, metadata, dry_run)


def with_lock(lock_path: Path) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = lock_path.open("w", encoding="utf-8")
    if fcntl is not None:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeployError(f"another deploy is already running: {lock_path}") from exc
    return lock_fh


def main() -> int:
    default_repo = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=default_repo)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-pull", action="store_true")
    parser.add_argument("--app", action="append", help="only process this app name")
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("/tmp/truenas-app-deploy.lock"),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    lock_fh = with_lock(args.lock_file)
    _ = lock_fh

    before, after = git_pull(repo_root, args.dry_run, args.skip_pull)
    manifests = discover_manifests(repo_root)
    if args.app:
        selected = set(args.app)
        manifests = [manifest for manifest in manifests if manifest.app_name in selected]
    changed_dirs = changed_app_dirs(repo_root, before, after, manifests)

    client = None
    client_cm = None
    if not args.dry_run:
        try:
            from truenas_api_client import Client
        except ModuleNotFoundError as exc:
            raise DeployError("truenas_api_client is required for live deploys") from exc
        client_cm = Client()
        client = client_cm.__enter__()

    try:
        installed_apps = load_existing_apps(client)
        for manifest in manifests:
            if not manifest.enabled:
                print(f"disabled app {manifest.app_name}; skipping")
                continue
            ensure_datasets(client, manifest, args.dry_run)
            apply_app(
                client,
                manifest,
                installed_apps,
                changed_dirs,
                args.dry_run,
            )
    finally:
        if client_cm is not None:
            client_cm.__exit__(None, None, None)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
