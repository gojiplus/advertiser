"""Select a public repository and post it to Bluesky."""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from scripts.configuration import (
    action_outputs,
    config_path,
    load_config,
    load_history,
    write_history,
)

GITHUB_API = "https://api.github.com"
BLUESKY_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
MAX_POST_LENGTH = 300
REPO_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)


def get_json(session, url, **kwargs):
    response = session.get(url, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json(), response


def fetch_repositories(session, owners, organizations, token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repositories = {}
    for account_type, accounts in (("users", owners), ("orgs", organizations)):
        for owner in accounts:
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


def eligible_repositories(repositories, min_stars=5):
    eligible = [
        repo
        for repo in repositories
        if not repo.get("private")
        and not repo.get("archived")
        and not repo.get("disabled")
        and not repo.get("fork")
        and repo.get("stargazers_count", 0) >= min_stars
        and str(repo.get("description") or "").strip()
    ]
    return sorted(
        eligible,
        key=lambda repo: (-repo["stargazers_count"], repo["full_name"].lower()),
    )


def recent_history_ids(history, now, cooldown_days=90):
    cutoff = now - timedelta(days=cooldown_days)
    return {
        entry["repo_id"]
        for entry in history
        if datetime.fromisoformat(entry["posted_at"]) >= cutoff
    }


def recent_feed_names(session, handle, now, cooldown_days=90, feed_url=BLUESKY_API):
    cutoff = now - timedelta(days=cooldown_days)
    names = set()
    cursor = None
    while True:
        params = {"actor": handle, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        page, _ = get_json(session, feed_url, params=params)
        oldest = now
        for item in page.get("feed", []):
            if "reason" in item:
                continue
            post = item["post"]
            if post["author"]["handle"].lower() != handle.lower():
                continue
            record = post["record"]
            created = datetime.fromisoformat(record["createdAt"])
            oldest = min(oldest, created)
            if created < cutoff:
                continue
            for match in REPO_URL.finditer(record.get("text", "")):
                names.add(f"{match.group(1)}/{match.group(2)}".lower())
        cursor = page.get("cursor")
        if not cursor or not page.get("feed") or oldest < cutoff:
            return names


def choose_repository(
    repositories, excluded_ids, excluded_names, pool, min_stars=5, featured_count=30
):
    ranked = eligible_repositories(repositories, min_stars)
    if not ranked:
        raise RuntimeError("No eligible repositories found")
    featured = ranked[:featured_count]
    rotation = ranked[featured_count:]
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


def record_post(history, repo, uri, now, path):
    history.append(
        {
            "repo_id": repo["id"],
            "full_name": repo["full_name"],
            "posted_at": now.isoformat(),
            "post_uri": uri,
        }
    )
    write_history(path, history)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--pool", choices=("auto", "featured", "rotation"), default="auto"
    )
    parser.add_argument("--handle", default=os.getenv("BSKY_HANDLE"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args(argv)

    if not args.handle:
        parser.error("BSKY_HANDLE or --handle is required")
    if args.preview_dir and not args.dry_run:
        parser.error("--preview-dir requires --dry-run")
    config = load_config(args.config, "repository")
    owners, organizations = config.get("owners", []), config.get("organizations", [])
    if not owners and not organizations:
        parser.error("Repository configuration needs owners or organizations")
    if any(
        not isinstance(items, list)
        or not all(isinstance(item, str) and item for item in items)
        for items in (owners, organizations)
    ):
        parser.error("owners and organizations must be lists of nonempty names")
    min_stars = config.get("min_stars", 5)
    featured_count = config.get("featured_count", 30)
    cooldown_days = config.get("cooldown_days", 90)
    for name, value, minimum in (
        ("min_stars", min_stars, 0),
        ("featured_count", featured_count, 1),
        ("cooldown_days", cooldown_days, 0),
    ):
        if type(value) is not int or value < minimum:
            parser.error(f"{name} must be an integer >= {minimum}")
    now = datetime.now(UTC)
    rotation_weekday = config.get("rotation_weekday")
    if rotation_weekday is not None and (
        type(rotation_weekday) is not int or not 0 <= rotation_weekday <= 6
    ):
        parser.error("rotation_weekday must be an integer from 0 to 6")
    weekday = now.astimezone(ZoneInfo(config.get("timezone", "UTC"))).weekday()
    pool = (
        "rotation" if args.pool == "auto" and weekday == rotation_weekday else args.pool
    )
    if pool == "auto":
        pool = "featured"

    session = requests.Session()
    repositories = fetch_repositories(
        session, owners, organizations, os.getenv("GITHUB_TOKEN")
    )
    history = load_history(args.history)
    blurbs = (
        json.loads(
            config_path(args.config, config["blurbs_file"]).read_text(encoding="utf-8")
        )
        if config.get("blurbs_file")
        else {}
    )
    excluded_ids = recent_history_ids(history, now, cooldown_days)
    excluded_names = recent_feed_names(
        session, args.handle, now, cooldown_days, config.get("feed_url", BLUESKY_API)
    )
    repo = choose_repository(
        repositories, excluded_ids, excluded_names, pool, min_stars, featured_count
    )
    action_outputs()
    if repo is None:
        print(f"No repository is available outside the {cooldown_days}-day cooldown")
        return
    text, facets = format_post(repo, blurbs)
    action_outputs(selected_id=repo["full_name"])
    print(
        f"Selected {repo['full_name']} ({repo['stargazers_count']} stars, {pool} pool)"
    )
    print(text)
    if args.dry_run:
        if args.preview_dir:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            (args.preview_dir / "post.json").write_text(
                json.dumps({"text": text, "facets": facets}, indent=2) + "\n",
                encoding="utf-8",
            )
        return

    password = os.getenv("BSKY_PASSWORD")
    if not password:
        parser.error("BSKY_PASSWORD is required to publish")
    from atproto import Client, models

    client = Client()
    client.login(args.handle, password)
    result = client.send_post(
        text, facets=[models.AppBskyRichtextFacet.Main(**facet) for facet in facets]
    )
    record_post(history, repo, result.uri, now, args.history)
    action_outputs(selected_id=repo["full_name"], post_uri=result.uri, posted=True)
    print(f"Published {result.uri}")


if __name__ == "__main__":
    main()
