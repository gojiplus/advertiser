import hashlib
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image, ImageChops
from PIL.PngImagePlugin import PngInfo

from scripts import post_paper_to_bsky as bot

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
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
        {"id": PAPER_ID, "caption": "Cynicism \N{MINUS SIGN} 6.2 points."}, links
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
        strict=True,
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
        assert len(paper["figures"]) <= 4
        if not paper["figures"]:
            assert len(paper["manuscript_sha256"]) == 64
        assert len(bot.format_post(paper, {"manuscript": PAPER_URL})[0]) <= 300
        assert all(
            figure["alt"] and len(figure["sha256"]) == 64 for figure in paper["figures"]
        )


def test_unprepared_catalog_papers_cannot_bypass_editorial_queue():
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
    assert bot.choose_paper(papers, catalog, history, set()) is None
    assert bot.choose_paper(papers, catalog, [], set(), "last") is None


def sample_pdf(*, heading="Abstract", complete=True):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), "A catalog paper", fontsize=18)
        page.insert_text((50, 100), heading, fontsize=12)
        page.insert_text(
            (50, 130),
            "An accurate summary explains the research question and its main finding.\n"
            "This abstract has two sentences and includes the result's limitations.",
            fontsize=11,
        )
        if complete:
            page.insert_text((50, 220), "1 Introduction", color=(1, 0, 0))
        return doc.tobytes()


def test_title_abstract_screenshot_includes_title_but_excludes_introduction():
    payload = sample_pdf()
    paper = {"manuscript_sha256": hashlib.sha256(payload).hexdigest()}
    content, alt, width, height = bot.prepare_title_abstract(
        Session([Response(content=payload)]),
        paper,
        {"title": "A catalog paper"},
        PAPER_URL,
    )
    assert alt.startswith("A catalog paper. Abstract: An accurate summary")
    assert "limitations." in alt
    assert "Introduction" not in alt
    assert width > 1000
    assert 200 < height < 400
    with Image.open(io.BytesIO(content)) as image:
        red, green, blue = image.split()
        assert ImageChops.difference(red, green).getbbox() is None
        assert ImageChops.difference(green, blue).getbbox() is None


@pytest.mark.parametrize(
    ("heading", "complete"), [("Summary", True), ("Abstract", False)]
)
def test_unrecognized_abstract_cannot_produce_a_partial_screenshot(heading, complete):
    payload = sample_pdf(heading=heading, complete=complete)
    paper = {"manuscript_sha256": hashlib.sha256(payload).hexdigest()}
    with pytest.raises(ValueError, match="Cannot locate abstract"):
        bot.prepare_title_abstract(
            Session([Response(content=payload)]),
            paper,
            {"title": "A catalog paper"},
            PAPER_URL,
        )


def test_abstract_requires_reviewed_manuscript_and_matching_title():
    payload = sample_pdf()
    for digest, title, message in [
        ("0" * 64, "A catalog paper", "Manuscript changed"),
        (hashlib.sha256(payload).hexdigest(), "Another paper", "does not match"),
    ]:
        with pytest.raises(ValueError, match=message):
            bot.prepare_title_abstract(
                Session([Response(content=payload)]),
                {"manuscript_sha256": digest},
                {"title": title},
                PAPER_URL,
            )


def test_pdf_table_crop_and_invalid_page_bounds():
    payload = sample_pdf()
    figure = {
        "url": PAPER_URL,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "alt": "A table from the paper",
        "page": 1,
        "clip": [40, 40, 400, 160],
    }
    content, alt, width, height = bot.prepare_figure(
        Session([Response(content=payload)]), figure
    )
    assert content.startswith(b"\x89PNG")
    assert alt == figure["alt"]
    assert width / height == pytest.approx(3, abs=0.02)
    for invalid in ({"page": 0}, {"page": 2}, {"clip": [0, 0, 1000, 1000]}):
        with pytest.raises(ValueError, match="outside"):
            bot.prepare_figure(
                Session([Response(content=payload)]), {**figure, **invalid}
            )


@pytest.mark.parametrize(
    ("dry_run", "repost"), [(True, False), (False, False), (False, True)]
)
def test_image_post_and_preview(monkeypatch, tmp_path, dry_run, repost):
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps([{"paper_id": PAPER_ID}] if repost else []))
    papers_path = tmp_path / "papers.json"
    papers_path.write_text(
        json.dumps(
            [
                {
                    "id": PAPER_ID,
                    "caption": "A useful finding with a concrete implication.",
                    "figures": [],
                }
            ]
        )
    )
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
    monkeypatch.setattr(
        bot, "recent_feed_paper_urls", lambda *args: {PAPER_URL} if repost else set()
    )
    monkeypatch.setattr(
        bot, "prepare_title_abstract", lambda *args: (b"image", "alt", 800, 500)
    )
    client = Mock()
    client.send_images.return_value = SimpleNamespace(uri="at://example/post/1")
    factory = Mock(return_value=client)
    monkeypatch.setitem(
        sys.modules, "atproto", SimpleNamespace(Client=factory, models=Mock())
    )
    monkeypatch.setenv("BSKY_PASSWORD", "test-password")
    argv = ["bot", "--handle", "me.bsky.social", "--paper-id", PAPER_ID]
    if dry_run:
        argv.extend(["--dry-run", "--preview-dir", str(tmp_path / "preview")])
    if repost:
        argv.append("--repost")
    monkeypatch.setattr(sys, "argv", argv)

    bot.main()

    if dry_run:
        factory.assert_not_called()
        assert json.loads(history_path.read_text()) == []
        assert (tmp_path / "preview" / "image-1.png").read_bytes() == b"image"
    else:
        client.send_images.assert_called_once()
        assert client.send_images.call_args.kwargs["images"] == [b"image"]
        client.send_post.assert_not_called()
        history = json.loads(history_path.read_text())
        assert history[-1]["paper_id"] == PAPER_ID
        assert history[-1]["post_uri"] == "at://example/post/1"
        assert len(history) == (2 if repost else 1)


def test_repost_requires_explicit_paper(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bot", "--handle", "me.bsky.social", "--repost"])
    with pytest.raises(SystemExit) as error:
        bot.main()
    assert error.value.code == 2


@pytest.mark.parametrize("failure", ["blank", "title", "image", "exhausted"])
def test_quality_failures_stop_before_login(monkeypatch, tmp_path, failure):
    history_path = tmp_path / "history.json"
    history_path.write_text("[]\n")
    papers_path = tmp_path / "papers.json"
    caption = {"blank": " ", "title": "A catalog paper"}.get(failure, "A real finding.")
    papers = [{"id": PAPER_ID, "caption": caption, "figures": []}]
    papers_path.write_text(json.dumps([] if failure == "exhausted" else papers))
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
    monkeypatch.setattr(
        bot, "prepare_title_abstract", Mock(side_effect=ValueError("Bad image"))
    )
    factory = Mock()
    monkeypatch.setitem(sys.modules, "atproto", SimpleNamespace(Client=factory))
    monkeypatch.setattr(sys, "argv", ["bot", "--handle", "me.bsky.social"])
    with pytest.raises(ValueError, match=r"blurb|Bad image|queue exhausted"):
        bot.main()
    factory.assert_not_called()
    assert json.loads(history_path.read_text()) == []
