import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from scripts import post_paper_to_bsky as bot

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
PAPER_ID = "sample-paper"
PAPER_URL = "https://gsood.com/research/papers/sample.pdf"


class Response:
    def __init__(self, content=None, data=None):
        self.content = content
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, url, **kwargs):
        return next(self.responses)


def test_catalog_links_and_utf8_facets():
    entry = {
        "id": PAPER_ID,
        "resources": [
            {"role": "manuscript", "url": "papers/sample.pdf"},
            {"role": "replication", "url": "https://github.com/example/sample"},
            {"role": "data", "url": "https://github.com/example/data"},
        ],
    }
    links = bot.resource_links(entry)
    post, facets = bot.format_post(
        {"id": PAPER_ID, "caption": "Cynicism − 6.2 points."}, links
    )

    assert links["manuscript"] == PAPER_URL
    assert len(post) <= 300
    for facet, (label, url) in zip(
        facets,
        (
            ("Paper", PAPER_URL),
            ("Replication code", "https://github.com/example/sample"),
            ("Data", "https://github.com/example/data"),
        ),
    ):
        start = facet["index"]["byteStart"]
        end = facet["index"]["byteEnd"]
        assert post.encode("utf-8")[start:end].decode("utf-8") == label
        assert facet["features"][0]["uri"] == url


def test_prepared_figure_checks_hash_and_removes_metadata():
    source = io.BytesIO()
    image = Image.new("RGB", (8, 4), "white")
    metadata = PngInfo()
    metadata.add_text("private_note", "remove me")
    image.save(source, format="PNG", pnginfo=metadata)
    payload = source.getvalue()
    figure = {
        "url": "https://example.com/chart.png",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "alt": "A chart",
    }
    content, alt, width, height = bot.prepare_figure(
        Session([Response(content=payload)]), figure
    )

    assert (alt, width, height) == ("A chart", 8, 4)
    assert content.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(content)) as clean:
        assert "private_note" not in clean.info
    with pytest.raises(ValueError, match="hash changed"):
        bot.prepare_figure(
            Session([Response(content=payload)]), {**figure, "sha256": "0" * 64}
        )


def test_selection_never_reposts_and_keeps_queue_order():
    papers = [{"id": "first"}, {"id": "second"}]
    catalog = {
        "first": {
            "id": "first",
            "resources": [{"role": "manuscript", "url": "papers/first.pdf"}],
        },
        "second": {
            "id": "second",
            "resources": [{"role": "manuscript", "url": "papers/second.pdf"}],
        },
    }
    history = [{"paper_id": "first"}]

    assert bot.choose_paper(papers, catalog, history, set())[0]["id"] == "second"
    assert (
        bot.choose_paper(
            papers, catalog, history, {"https://gsood.com/research/papers/second.pdf"}
        )
        is None
    )


def test_recent_feed_links_protect_against_failed_history_commit():
    def item(days_ago, url):
        return {
            "post": {
                "author": {"handle": "me.bsky.social"},
                "record": {
                    "createdAt": (NOW - timedelta(days=days_ago)).isoformat(),
                    "facets": [
                        {
                            "features": [
                                {"$type": "app.bsky.richtext.facet#link", "uri": url}
                            ]
                        }
                    ],
                },
            }
        }

    session = Session(
        [
            Response(
                data={"feed": [item(1, PAPER_URL), item(91, "old")], "cursor": "older"}
            )
        ]
    )

    assert bot.recent_feed_paper_urls(session, "me.bsky.social", NOW) == {PAPER_URL}


def test_reviewed_queue_has_valid_caption_and_figures():
    papers = json.loads(bot.PAPERS_PATH.read_text(encoding="utf-8"))

    assert papers
    for paper in papers:
        assert 1 <= len(paper["figures"]) <= 4
        assert len(bot.format_post(paper, {"manuscript": PAPER_URL})[0]) <= 300
        assert all(
            figure["alt"] and len(figure["sha256"]) == 64 for figure in paper["figures"]
        )


def test_catalog_fills_exhausted_queue_and_respects_duplicate_checks():
    papers = [{"id": "reviewed", "caption": "Reviewed caption", "figures": []}]
    catalog = {
        name: {
            "id": name,
            "title": f"Title of {name}",
            "resources": [{"role": "manuscript", "url": f"papers/{name}.pdf"}],
        }
        for name in ("first", "reviewed", "last")
    }
    assert bot.choose_paper(papers, catalog, [], set())[0] == papers[0]
    history = [{"paper_id": "reviewed"}]
    paper, links = bot.choose_paper(papers, catalog, history, set())
    assert paper == {"id": "first", "caption": "Title of first", "figures": []}
    assert links["manuscript"].endswith("/first.pdf")
    feed_urls = {links["manuscript"]}
    assert bot.choose_paper(papers, catalog, history, feed_urls)[0]["id"] == "last"
    history.append({"paper_id": "last"})
    assert bot.choose_paper(papers, catalog, history, feed_urls) is None
    assert bot.choose_paper(papers, catalog, [], set(), "last")[0]["id"] == "last"


@pytest.mark.parametrize("dry_run", [True, False])
def test_catalog_post_and_preview(monkeypatch, tmp_path, dry_run):
    history_path = tmp_path / "history.json"
    history_path.write_text("[]\n")
    papers_path = tmp_path / "papers.json"
    papers_path.write_text("[]\n")
    monkeypatch.setattr(bot, "HISTORY_PATH", history_path)
    monkeypatch.setattr(bot, "PAPERS_PATH", papers_path)
    monkeypatch.setattr(
        bot,
        "catalog_papers",
        lambda session: {
            PAPER_ID: {
                "id": PAPER_ID,
                "title": "A catalog paper",
                "resources": [{"role": "manuscript", "url": PAPER_URL}],
            }
        },
    )
    monkeypatch.setattr(bot, "recent_feed_paper_urls", lambda *args: set())
    client = Mock()
    client.send_post.return_value = SimpleNamespace(uri="at://example/post/1")
    factory = Mock(return_value=client)
    monkeypatch.setitem(
        sys.modules, "atproto", SimpleNamespace(Client=factory, models=Mock())
    )
    monkeypatch.setenv("BSKY_PASSWORD", "test-password")
    argv = ["bot", "--handle", "me.bsky.social", "--paper-id", PAPER_ID]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)

    bot.main()

    if dry_run:
        factory.assert_not_called()
        assert json.loads(history_path.read_text()) == []
    else:
        client.send_post.assert_called_once()
        assert client.send_post.call_args.kwargs["text"] == "A catalog paper\n\nPaper"
        client.send_images.assert_not_called()
        history = json.loads(history_path.read_text())
        assert history[0]["paper_id"] == PAPER_ID
        assert history[0]["post_uri"] == "at://example/post/1"
