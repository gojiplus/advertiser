#!/usr/bin/env python3
"""Select a public repository and post it to Bluesky."""

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

GITHUB_API = "https://api.github.com"
BLUESKY_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
HISTORY_PATH = Path(__file__).resolve().parents[1] / "data" / "post_history.json"
BLURBS_PATH = Path(__file__).resolve().parents[1] / "data" / "featured_blurbs.json"
OWNERS = ("soodoku",)
ORGS = (
    "appeler",
    "recite",
    "gojiplus",
    "themains",
    "outside-edge",
    "matmulai",
    "in-rolls",
    "notnews",
    "geosensing",
)
MIN_STARS = 5
FEATURED_COUNT = 30
COOLDOWN_DAYS = 90
MAX_POST_LENGTH = 300
REPO_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)", re.I)


def get_json(session, url, **kwargs):
    response = session.get(url, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json(), response


def fetch_repositories(session, token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repositories = {}
    for account_type, owners in (("users", OWNERS), ("orgs", ORGS)):
        for owner in owners:
            url = f"{GITHUB_API}/{account_type}/{owner}/repos"
            repo_type = "owner" if account_type == "users" else "public"
            params = {"per_page": 100, "type": repo_type}
            while url:
                page, response = get_json(session, url, headers=headers, params=params)
                for repo in page:
                    repositories[repo["id"]] = repo
                url = response.links.get("next", {}).get("url")
                params = None
    return list(repositories.values())


def eligible_repositories(repositories):
    eligible = [
        repo
        for repo in repositories
        if not repo.get("private")
        and not repo.get("archived")
        and not repo.get("disabled")
        and not repo.get("fork")
        and repo.get("stargazers_count", 0) >= MIN_STARS
        and str(repo.get("description") or "").strip()
    ]
    return sorted(
        eligible,
        key=lambda repo: (-repo["stargazers_count"], repo["full_name"].lower()),
    )


def load_history(path=HISTORY_PATH):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Post history must be a JSON list")
    return data


def recent_history_ids(history, now):
    cutoff = now - timedelta(days=COOLDOWN_DAYS)
    return {
        entry["repo_id"]
        for entry in history
        if datetime.fromisoformat(entry["posted_at"]) >= cutoff
    }


def recent_feed_names(session, handle, now):
    cutoff = now - timedelta(days=COOLDOWN_DAYS)
    names = set()
    cursor = None
    while True:
        params = {"actor": handle, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        page, _ = get_json(session, BLUESKY_API, params=params)
        oldest = now
        for item in page.get("feed", []):
            if "reason" in item:
                continue
            post = item["post"]
            if post["author"]["handle"].lower() != handle.lower():
                continue
            record = post["record"]
            created = datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00"))
            oldest = min(oldest, created)
            if created < cutoff:
                continue
            for match in REPO_URL.finditer(record.get("text", "")):
                names.add(f"{match.group(1)}/{match.group(2)}".lower())
        cursor = page.get("cursor")
        if not cursor or not page.get("feed") or oldest < cutoff:
            return names


def choose_repository(repositories, excluded_ids, excluded_names, pool):
    ranked = eligible_repositories(repositories)
    if not ranked:
        raise RuntimeError("No eligible repositories found")
    featured = ranked[:FEATURED_COUNT]
    rotation = ranked[FEATURED_COUNT:]
    pools = (featured, rotation) if pool == "featured" else (rotation, featured)
    for candidates in pools:
        available = [
            repo
            for repo in candidates
            if repo["id"] not in excluded_ids
            and repo["full_name"].lower() not in excluded_names
        ]
        if available:
            return available[0]
    return None


def format_post(repo, blurbs=None):
    url = repo["html_url"]
    base = f"📦 {repo['full_name']}\n\n"
    suffix = f"\n\n{url}"
    available = MAX_POST_LENGTH - len(base) - len(suffix)
    if available < 1:
        raise ValueError("Repository name and URL exceed the post limit")
    description = " ".join(
        (blurbs or {}).get(repo["full_name"], repo["description"]).split()
    )
    if len(description) > available:
        description = description[: available - 1].rsplit(" ", 1)[0].rstrip() + "…"
    text = base + description + suffix
    start = len(text[: text.index(url)].encode("utf-8"))
    end = start + len(url.encode("utf-8"))
    facets = [
        {
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        }
    ]
    return text, facets


def record_post(history, repo, uri, now, path=HISTORY_PATH):
    history.append(
        {
            "repo_id": repo["id"],
            "full_name": repo["full_name"],
            "posted_at": now.isoformat(),
            "post_uri": uri,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--pool", choices=("auto", "featured", "rotation"), default="auto"
    )
    parser.add_argument("--handle", default=os.getenv("BSKY_HANDLE"))
    args = parser.parse_args()

    if not args.handle:
        parser.error("BSKY_HANDLE or --handle is required")
    now = datetime.now(timezone.utc)
    weekday = now.astimezone(ZoneInfo("America/Los_Angeles")).weekday()
    pool = "rotation" if args.pool == "auto" and weekday == 4 else args.pool
    if pool == "auto":
        pool = "featured"

    session = requests.Session()
    repositories = fetch_repositories(session, os.getenv("GITHUB_TOKEN"))
    history = load_history()
    blurbs = json.loads(BLURBS_PATH.read_text(encoding="utf-8"))
    excluded_ids = recent_history_ids(history, now)
    excluded_names = recent_feed_names(session, args.handle, now)
    repo = choose_repository(repositories, excluded_ids, excluded_names, pool)
    if repo is None:
        print("No repository is available outside the 90-day cooldown")
        return
    text, facets = format_post(repo, blurbs)
    print(
        f"Selected {repo['full_name']} ({repo['stargazers_count']} stars, {pool} pool)"
    )
    print(text)
    if args.dry_run:
        return

    password = os.getenv("BSKY_PASSWORD")
    if not password:
        parser.error("BSKY_PASSWORD is required to publish")
    from atproto import Client

    client = Client()
    client.login(args.handle, password)
    result = client.send_post(text, facets=facets)
    record_post(history, repo, result.uri, now)
    print(f"Published {result.uri}")


if __name__ == "__main__":
    main()
