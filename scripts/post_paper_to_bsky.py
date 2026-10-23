#!/usr/bin/env python3
"""Post a reviewed paper with figures and source links to Bluesky."""

import argparse
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from PIL import Image

CATALOG_URL = "https://gsood.com/research/catalog.json"
FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
ROOT = Path(__file__).resolve().parents[1]
PAPERS_PATH = ROOT / "data" / "papers.json"
HISTORY_PATH = ROOT / "data" / "paper_post_history.json"
MAX_IMAGE_BYTES = 2_000_000
MAX_POST_LENGTH = 300


def fetch_json(session, url, **kwargs):
    response = session.get(url, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json()


def catalog_papers(session):
    catalog = fetch_json(session, CATALOG_URL)
    return {
        entry["id"]: entry for entry in catalog["entries"] if entry["kind"] == "paper"
    }


def resource_links(entry):
    links = {}
    for resource in entry["resources"]:
        if resource.get("role") in {"manuscript", "replication", "data"}:
            links[resource["role"]] = urljoin(CATALOG_URL, resource["url"])
    if "manuscript" not in links:
        raise ValueError(f"Paper {entry['id']} has no manuscript")
    return links


def format_post(paper, links):
    labels = [("Paper", links["manuscript"])]
    for role, label in (("replication", "Replication code"), ("data", "Data")):
        if role in links:
            labels.append((label, links[role]))
    caption = paper["caption"].strip()
    text = caption + "\n\n" + " · ".join(label for label, _ in labels)
    if len(text) > MAX_POST_LENGTH:
        raise ValueError(f"Paper {paper['id']} exceeds the Bluesky post limit")
    facets = []
    offset = len(caption.encode("utf-8")) + 2
    for label, url in labels:
        end = offset + len(label.encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": offset, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
        offset = end + len(" · ".encode("utf-8"))
    return text, facets


def prepare_figure(session, figure):
    response = session.get(figure["url"], timeout=30)
    response.raise_for_status()
    source = response.content
    if hashlib.sha256(source).hexdigest() != figure["sha256"]:
        raise ValueError(f"Figure hash changed: {figure['url']}")
    with Image.open(io.BytesIO(source)) as image:
        if image.format != "PNG":
            raise ValueError(f"Expected PNG: {figure['url']}")
        image.load()
        width, height = image.size
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
    content = output.getvalue()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError(f"Figure exceeds Bluesky's 2 MB limit: {figure['url']}")
    if not figure.get("alt"):
        raise ValueError(f"Figure has no alt text: {figure['url']}")
    return content, figure["alt"], width, height


def recent_feed_paper_urls(session, handle, now):
    cutoff = now - timedelta(days=90)
    urls = set()
    cursor = None
    while True:
        params = {"actor": handle, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        page = fetch_json(session, FEED_URL, params=params)
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
            for facet in record.get("facets", []):
                for feature in facet.get("features", []):
                    if feature.get("$type") == "app.bsky.richtext.facet#link":
                        urls.add(feature["uri"])
        cursor = page.get("cursor")
        if not cursor or not page.get("feed") or oldest < cutoff:
            return urls


def load_history():
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        raise ValueError("Paper history must be a JSON list")
    return history


def choose_paper(papers, catalog, history, feed_urls, paper_id=None):
    posted_ids = {item["paper_id"] for item in history}
    for paper in papers:
        if paper_id and paper["id"] != paper_id:
            continue
        if paper["id"] not in catalog:
            raise ValueError(f"Paper missing from public catalog: {paper['id']}")
        links = resource_links(catalog[paper["id"]])
        if paper["id"] not in posted_ids and links["manuscript"] not in feed_urls:
            return paper, links
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--paper-id")
    parser.add_argument("--handle", default=os.getenv("BSKY_HANDLE"))
    args = parser.parse_args()
    if not args.handle:
        parser.error("BSKY_HANDLE or --handle is required")

    papers = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
    if args.paper_id and args.paper_id not in {paper["id"] for paper in papers}:
        parser.error(f"Unknown paper ID: {args.paper_id}")
    history = load_history()
    now = datetime.now(timezone.utc)
    session = requests.Session()
    catalog = catalog_papers(session)
    feed_urls = recent_feed_paper_urls(session, args.handle, now)
    selected = choose_paper(papers, catalog, history, feed_urls, args.paper_id)
    if selected is None:
        print("No reviewed paper remains to post")
        return
    paper, links = selected
    if not 1 <= len(paper["figures"]) <= 4:
        raise ValueError(f"Paper {paper['id']} must have 1–4 figures")
    text, facets = format_post(paper, links)
    figures = [prepare_figure(session, figure) for figure in paper["figures"]]
    print(f"Selected {paper['id']}")
    print(text)
    for index, (_, alt, width, height) in enumerate(figures, start=1):
        print(f"Figure {index} ({width}×{height}): {alt}")
    if args.dry_run:
        return

    password = os.getenv("BSKY_PASSWORD")
    if not password:
        parser.error("BSKY_PASSWORD is required to publish")
    from atproto import Client, models

    client = Client()
    client.login(args.handle, password)
    result = client.send_images(
        text=text,
        images=[content for content, _, _, _ in figures],
        image_alts=[alt for _, alt, _, _ in figures],
        image_aspect_ratios=[
            models.AppBskyEmbedDefs.AspectRatio(width=width, height=height)
            for _, _, width, height in figures
        ],
        facets=facets,
    )
    history.append(
        {"paper_id": paper["id"], "posted_at": now.isoformat(), "post_uri": result.uri}
    )
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"Published {result.uri}")


if __name__ == "__main__":
    main()
