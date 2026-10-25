import json
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import configuration, run_action


@pytest.fixture
def consumer(monkeypatch, tmp_path):
    workspace = tmp_path / "consumer with spaces"
    shutil.copytree(Path(__file__).parent / "fixtures/consumer", workspace / "data")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SimpleHTTPRequestHandler, directory=str(workspace / "data")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("config.json", "papers.json"):
        path = workspace / "data" / name
        path.write_text(path.read_text().replace(":8765/", f":{server.server_port}/"))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("GITHUB_OUTPUT", str(workspace / "outputs"))
    monkeypatch.setenv("ADVERTISER_CONFIG", "data/config.json")
    monkeypatch.setenv("ADVERTISER_HISTORY", "state/history.json")
    monkeypatch.setenv("ADVERTISER_PREVIEW", "preview")
    monkeypatch.setenv("BSKY_HANDLE", "consumer.example")
    for name in (
        "BSKY_PASSWORD",
        "ADVERTISER_DRY_RUN",
        "ADVERTISER_REPOST",
        "ADVERTISER_PAPER_ID",
        "ADVERTISER_POOL",
    ):
        monkeypatch.delenv(name, raising=False)
    yield workspace
    server.shutdown()
    thread.join()
    server.server_close()


def outputs(path):
    lines = iter(path.read_text().splitlines())
    result = {}
    for line in lines:
        key, delimiter = line.split("<<")
        value = []
        for content in lines:
            if content == delimiter:
                break
            value.append(content)
        result[key] = "\n".join(value)
    return result


@pytest.mark.parametrize("live", [False, True])
def test_external_consumer_uses_own_data_and_history(monkeypatch, consumer, live):
    client = Mock()
    client.send_images.return_value = SimpleNamespace(uri="at://consumer/post/1")
    factory = Mock(return_value=client)
    monkeypatch.setitem(
        sys.modules, "atproto", SimpleNamespace(Client=factory, models=Mock())
    )
    if live:
        monkeypatch.setenv("ADVERTISER_PAPER_ID", "consumer-paper")
        monkeypatch.setenv("ADVERTISER_DRY_RUN", "false")
        monkeypatch.setenv("BSKY_PASSWORD", "test-app-password")
        monkeypatch.delenv("ADVERTISER_PREVIEW")
    run_action.main()
    result = outputs(consumer / "outputs")
    assert result["selected-id"] == "consumer-paper"
    if live:
        client.login.assert_called_once_with("consumer.example", "test-app-password")
        assert client.send_images.call_count == 1
        history = json.loads((consumer / "state/history.json").read_text())
        assert history[0]["paper_id"] == "consumer-paper"
        assert result["posted"] == "true"
        assert result["post-uri"] == history[0]["post_uri"]
        run_action.main()
        assert client.send_images.call_count == 1
        assert outputs(consumer / "outputs")["posted"] == "false"
    else:
        factory.assert_not_called()
        assert result["posted"] == "false"
        assert result["post-uri"] == ""
        assert not (consumer / "state/history.json").exists()
        preview = json.loads((consumer / "preview/post.json").read_text())
        assert "synthetic test" in preview["text"]
        assert (consumer / "preview/image-1.png").read_bytes().startswith(b"\x89PNG")
        assert preview["facets"][0]["features"][0]["uri"].endswith("/paper.pdf")


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("ADVERTISER_CONFIG", "", "CONFIG is required"),
        ("ADVERTISER_HISTORY", "", "HISTORY is required"),
        ("BSKY_HANDLE", "", "bsky-handle is required"),
        ("ADVERTISER_DRY_RUN", "yes", "must be true or false"),
        ("ADVERTISER_DRY_RUN", "false", "bsky-password is required"),
        ("ADVERTISER_POOL", "rotation", "only to repository"),
        ("ADVERTISER_HISTORY", "data/config.json", "different files"),
    ],
)
def test_invalid_action_inputs_fail_before_posting(
    monkeypatch, consumer, variable, value, message
):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=message):
        run_action.main()
    assert not (consumer / "state/history.json").exists()


def test_repository_action_routes_caller_settings(monkeypatch, consumer):
    config = consumer / "data/config.json"
    config.write_text(json.dumps({"kind": "repository", "owners": ["consumer"]}))
    main = Mock()
    monkeypatch.setattr(run_action.post_repo_to_bsky, "main", main)
    monkeypatch.setenv("ADVERTISER_POOL", "rotation")
    run_action.main()
    assert main.call_args.args[0] == [
        "--config",
        str(config),
        "--history",
        str(consumer / "state/history.json"),
        "--dry-run",
        "--preview-dir",
        str(consumer / "preview"),
        "--pool",
        "rotation",
    ]
    monkeypatch.setenv("ADVERTISER_REPOST", "true")
    with pytest.raises(ValueError, match="only to paper"):
        run_action.main()


def test_output_values_cannot_inject_other_outputs(monkeypatch, tmp_path):
    path = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    configuration.action_outputs(selected_id="example\nposted=true")
    assert outputs(path) == {
        "selected-id": "example\nposted=true",
        "posted": "false",
        "post-uri": "",
    }
