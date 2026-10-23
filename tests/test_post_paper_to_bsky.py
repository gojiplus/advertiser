import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

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
