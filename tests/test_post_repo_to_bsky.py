import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import post_repo_to_bsky as bot

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)


def repo(number, stars=10, **overrides):
    result = {
        "id": number,
        "full_name": f"example/repo-{number}",
        "html_url": f"https://github.com/example/repo-{number}",
        "description": f"Useful repository {number}",
        "stargazers_count": stars,
        "private": False,
        "archived": False,
        "disabled": False,
        "fork": False,
    }
    result.update(overrides)
    return result


class Response:
    def __init__(self, data, next_url=None):
        self.data = data
        self.links = {"next": {"url": next_url}} if next_url else {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_fetch_repositories_follows_all_pages(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", ("example",))
    monkeypatch.setattr(bot, "ORGS", ())
    next_url = "https://api.github.com/users/example/repos?page=2"
    session = Session([Response([repo(1)], next_url), Response([repo(2)])])

    result = bot.fetch_repositories(session, "token")

    assert [item["id"] for item in result] == [1, 2]
    assert session.calls[0][1]["params"]["per_page"] == 100
    assert session.calls[1][0] == next_url
    assert session.calls[1][1]["params"] is None


def test_eligibility_and_featured_rotation():
    repositories = [repo(i, stars=100 - i) for i in range(35)]
    repositories += [
        repo(100, stars=1000, archived=True),
        repo(101, stars=1000, fork=True),
        repo(102, stars=1000, description=""),
        repo(103, stars=4),
    ]
    featured = bot.choose_repository(repositories, set(), set(), "featured")
    rotation = bot.choose_repository(repositories, set(), set(), "rotation")

    assert featured["id"] == 0
    assert rotation["id"] == 30
    assert bot.choose_repository(repositories, {0}, set(), "featured")["id"] == 1


def test_star_cutoff_is_configurable(monkeypatch):
    monkeypatch.setattr(bot, "MIN_STARS", 1)

    assert [item["id"] for item in bot.eligible_repositories([repo(1, stars=1)])] == [1]


def test_cooldown_and_fallback():
    history = [
        {"repo_id": 1, "posted_at": (NOW - timedelta(days=89)).isoformat()},
        {"repo_id": 2, "posted_at": (NOW - timedelta(days=91)).isoformat()},
    ]
    assert bot.recent_history_ids(history, NOW) == {1}
    assert (
        bot.choose_repository([repo(1), repo(2)], {1}, {"example/repo-2"}, "featured")
        is None
    )


def test_recent_feed_names_paginates_and_skips_old_posts():
    def item(text, days_ago, author="me.bsky.social", reason=None):
        result = {
            "post": {
                "author": {"handle": author},
                "record": {
                    "createdAt": (NOW - timedelta(days=days_ago)).isoformat(),
                    "text": text,
                },
            }
        }
        if reason is not None:
            result["reason"] = reason
        return result

    session = Session(
        [
            Response(
                {
                    "cursor": "next",
                    "feed": [
                        item("https://github.com/example/repo-1", 1),
                        item("https://github.com/example/repo-2", 2, reason={}),
                    ],
                }
            ),
            Response(
                {
                    "cursor": "older",
                    "feed": [item("https://github.com/example/repo-3", 91)],
                }
            ),
        ]
    )
    names = bot.recent_feed_names(session, "me.bsky.social", NOW)

    assert names == {"example/repo-1"}
    assert session.calls[1][1]["params"]["cursor"] == "next"


def test_format_post_respects_length_and_utf8_facet():
    chosen = repo(1, description="é" * 400)
    text, facets = bot.format_post(chosen)
    index = facets[0]["index"]

    assert len(text) <= 300
    assert text.endswith(chosen["html_url"])
    assert (
        text.encode("utf-8")[index["byteStart"] : index["byteEnd"]].decode("utf-8")
        == chosen["html_url"]
    )


def test_featured_blurb_overrides_stale_description():
    chosen = repo(1, description="Old GitHub description")
    text, _ = bot.format_post(chosen, {chosen["full_name"]: "Reviewed blurb"})

    assert "Reviewed blurb" in text
    assert "Old GitHub description" not in text


def test_record_post_writes_history(tmp_path):
    path = tmp_path / "data" / "post_history.json"
    bot.record_post([], repo(1), "at://post", NOW, path)

    assert json.loads(path.read_text())[0] == {
        "repo_id": 1,
        "full_name": "example/repo-1",
        "posted_at": NOW.isoformat(),
        "post_uri": "at://post",
    }


def test_api_failure_stops_selection():
    class ErrorResponse(Response):
        def raise_for_status(self):
            raise RuntimeError("API unavailable")

    with pytest.raises(RuntimeError, match="API unavailable"):
        bot.recent_feed_names(Session([ErrorResponse({})]), "me.bsky.social", NOW)


def test_dry_run_does_not_publish_or_record(monkeypatch, tmp_path, capsys):
    blurbs_path = tmp_path / "blurbs.json"
    blurbs_path.write_text("{}")
    monkeypatch.setattr(bot, "BLURBS_PATH", blurbs_path)
    monkeypatch.setattr(bot, "fetch_repositories", lambda *_: [repo(1)])
    monkeypatch.setattr(bot, "recent_feed_names", lambda *_: set())
    monkeypatch.setattr(bot, "load_history", list)
    monkeypatch.setattr(
        bot,
        "record_post",
        lambda *_: pytest.fail("A dry run must not record a post"),
    )
    monkeypatch.setattr(
        sys, "argv", ["post_repo_to_bsky.py", "--dry-run", "--handle", "me.bsky.social"]
    )

    bot.main()

    assert "https://github.com/example/repo-1" in capsys.readouterr().out


def test_live_post_records_returned_uri(monkeypatch, tmp_path):
    from atproto import models

    blurbs_path = tmp_path / "blurbs.json"
    blurbs_path.write_text("{}")
    monkeypatch.setattr(bot, "BLURBS_PATH", blurbs_path)
    monkeypatch.setattr(bot, "fetch_repositories", lambda *_: [repo(1)])
    monkeypatch.setattr(bot, "recent_feed_names", lambda *_: set())
    monkeypatch.setattr(bot, "load_history", list)
    monkeypatch.setenv("BSKY_PASSWORD", "test-password")
    monkeypatch.setattr(
        sys, "argv", ["post_repo_to_bsky.py", "--handle", "me.bsky.social"]
    )
    recorded = []
    monkeypatch.setattr(bot, "record_post", lambda *args: recorded.append(args))

    class FakeClient:
        def login(self, handle, password):
            assert (handle, password) == ("me.bsky.social", "test-password")

        def send_post(self, text, facets):
            assert "https://github.com/example/repo-1" in text
            assert facets[0].features[0].uri == repo(1)["html_url"]
            return SimpleNamespace(uri="at://posted")

    monkeypatch.setitem(
        sys.modules, "atproto", SimpleNamespace(Client=FakeClient, models=models)
    )
    bot.main()

    assert recorded[0][1:3] == (repo(1), "at://posted")
