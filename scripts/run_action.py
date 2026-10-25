"""Run the action with caller-owned paths and explicit inputs."""

import json
import os
from pathlib import Path

from scripts import post_paper_to_bsky, post_repo_to_bsky


def boolean_input(name, default):
    value = os.getenv(f"ADVERTISER_{name}", default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def workspace_path(name, required=True):
    value = os.getenv(f"ADVERTISER_{name}", "")
    if not value:
        if required:
            raise ValueError(f"{name} is required")
        return None
    return (Path(os.environ["GITHUB_WORKSPACE"]) / value).resolve()


def main():
    config = workspace_path("CONFIG")
    history = workspace_path("HISTORY")
    preview = workspace_path("PREVIEW", required=False)
    if config is None or history is None:
        raise ValueError("Configuration and history paths are required")
    dry_run = boolean_input("DRY_RUN", "true")
    repost = boolean_input("REPOST", "false")
    if not os.getenv("BSKY_HANDLE"):
        raise ValueError("bsky-handle is required")
    if not dry_run and not os.getenv("BSKY_PASSWORD"):
        raise ValueError("bsky-password is required to publish")
    if config == history:
        raise ValueError("Configuration and history must be different files")
    settings = json.loads(config.read_text(encoding="utf-8"))
    kind = settings.get("kind") if isinstance(settings, dict) else None
    args = ["--config", str(config), "--history", str(history)]
    if dry_run:
        args.append("--dry-run")
    if preview:
        args.extend(["--preview-dir", str(preview)])
    paper_id = os.getenv("ADVERTISER_PAPER_ID", "")
    pool = os.getenv("ADVERTISER_POOL", "auto")
    if kind == "paper":
        if pool != "auto":
            raise ValueError("pool applies only to repository posts")
        if paper_id:
            args.extend(["--paper-id", paper_id])
        if repost:
            args.append("--repost")
        post_paper_to_bsky.main(args)
    elif kind == "repository":
        if paper_id or repost:
            raise ValueError("paper-id and repost apply only to paper posts")
        post_repo_to_bsky.main([*args, "--pool", pool])
    else:
        raise ValueError("Configuration kind must be paper or repository")


if __name__ == "__main__":
    main()
