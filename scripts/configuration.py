"""Load caller-owned configuration and report action results."""

import json
import os
import uuid
from pathlib import Path


def load_config(path, kind):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("kind") != kind:
        raise ValueError(f"Configuration must be an object with kind={kind!r}")
    return config


def config_path(config_file, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Configuration file paths must be nonempty strings")
    return (config_file.resolve().parent / value).resolve()


def load_history(path):
    if not path.exists():
        return []
    history = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        raise ValueError("Post history must be a JSON list")
    return history


def write_history(path, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def action_outputs(*, selected_id="", post_uri="", posted=False):
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        return
    values = {
        "selected-id": selected_id,
        "post-uri": post_uri,
        "posted": str(posted).lower(),
    }
    with Path(output_file).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            delimiter = f"advertiser_{uuid.uuid4().hex}"
            output.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
