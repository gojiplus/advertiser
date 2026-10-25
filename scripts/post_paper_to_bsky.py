"""Post a reviewed paper pitch with graphs, tables, or its title and abstract."""

import argparse
import hashlib
import io
import json
import os
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import pymupdf
import requests
from PIL import Image

from scripts.configuration import (
    action_outputs,
    config_path,
    load_config,
    load_history,
    write_history,
)

FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
MAX_IMAGE_BYTES = 2_000_000
MAX_POST_LENGTH = 300


def fetch_json(session, url, **kwargs):
    response = session.get(url, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json()


def catalog_papers(session, catalog_url):
    catalog = fetch_json(session, catalog_url)
    return {
        entry["id"]: entry for entry in catalog["entries"] if entry["kind"] == "paper"
    }


def resource_links(entry, catalog_url):
    links = {}
    for resource in entry["resources"]:
        if resource.get("role") in {"manuscript", "replication", "code", "data"}:
            links[resource["role"]] = urljoin(catalog_url, resource["url"])
    if "manuscript" not in links:
        raise ValueError(f"Paper {entry['id']} has no manuscript")
    return links


def format_post(paper, links):
    labels = [("Paper", links["manuscript"])]
    for role, label in (
        ("replication", "Replication code"),
        ("code", "Code"),
        ("data", "Data"),
    ):
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
        offset = end + len(" · ".encode())
    return text, facets


def prepare_figure(session, figure):
    response = session.get(figure["url"], timeout=30)
    response.raise_for_status()
    source = response.content
    if hashlib.sha256(source).hexdigest() != figure["sha256"]:
        raise ValueError(f"Figure hash changed: {figure['url']}")
    if source.startswith(b"%PDF-"):
        with pymupdf.open(stream=source, filetype="pdf") as document:
            page_number = figure.get("page", 1)
            if not isinstance(page_number, int) or not 1 <= page_number <= len(
                document
            ):
                raise ValueError("Exhibit page is outside the PDF")
            page = document[page_number - 1]
            clip = pymupdf.Rect(figure.get("clip", tuple(page.rect)))
            if clip.is_empty or not page.rect.contains(clip):
                raise ValueError("Exhibit crop is outside the PDF page")
            source = page.get_pixmap(dpi=200, clip=clip, alpha=False).tobytes("png")
    return prepare_image(source, figure["alt"])


def prepare_image(source, alt):
    with Image.open(io.BytesIO(source)) as image:
        if image.format != "PNG":
            raise ValueError("Expected PNG image")
        image.load()
        width, height = image.size
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
    content = output.getvalue()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds Bluesky's 2 MB limit")
    if not alt.strip() or len(alt) > 2000:
        raise ValueError("Image needs alt text of 1-2000 characters")
    return content, alt, width, height


def normalized_text(text):
    return re.sub(r"\W+", "", unicodedata.normalize("NFKD", text).casefold())


def prepare_title_abstract(session, paper, entry, manuscript_url):
    response = session.get(manuscript_url, timeout=30)
    response.raise_for_status()
    source = response.content
    if hashlib.sha256(source).hexdigest() != paper["manuscript_sha256"]:
        raise ValueError("Manuscript changed; review the title and abstract again")
    with pymupdf.open(stream=source, filetype="pdf") as document:
        page = document[0]
        lines = [
            ("".join(span["text"] for span in line["spans"]).strip(), line["bbox"])
            for block in page.get_textpage().extractDICT(sort=True)["blocks"]
            if block["type"] == 0
            for line in block["lines"]
        ]
        start = next(
            (i for i, (text, _) in enumerate(lines) if text.casefold() == "abstract"),
            None,
        )
        if start is None:
            raise ValueError("Cannot locate abstract; prepare a reviewed PDF crop")
        heading = re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s*(?:[A-Z].*)?|Introduction|Keywords\b.*|JEL\b.*)$"
        )
        end = next(
            (i for i in range(start + 1, len(lines)) if heading.match(lines[i][0])),
            None,
        )
        if end is None:
            raise ValueError("Cannot locate abstract end; prepare a reviewed PDF crop")
        title_area = " ".join(text for text, _ in lines[:start])
        if normalized_text(entry["title"]) not in normalized_text(title_area):
            raise ValueError("Catalog title does not match the PDF title")
        abstract = " ".join(text for text, _ in lines[start + 1 : end])
        abstract = re.sub(r"-\s+(?=[a-z])", "", abstract)
        if len(abstract) < 100 or not abstract.endswith((".", "!", "?")):
            raise ValueError("Abstract appears incomplete; prepare a reviewed PDF crop")
        bounds = [box for _, box in lines[:end]]
        clip = pymupdf.Rect(
            max(0, min(box[0] for box in bounds) - 10),
            max(0, min(box[1] for box in bounds) - 10),
            min(page.rect.width, max(box[2] for box in bounds) + 10),
            min(page.rect.height, max(box[3] for box in bounds) + 10),
        )
        source = page.get_pixmap(dpi=200, clip=clip, alpha=False).tobytes("png")
    alt = f"{entry['title']}. Abstract: {abstract}"
    if len(alt) > 2000:
        alt = alt[:1950].rsplit(" ", 1)[0] + "… Full abstract in the linked paper."
    return prepare_image(source, alt)


def recent_feed_paper_urls(session, handle, now, feed_url=FEED_URL):
    cutoff = now - timedelta(days=90)
    urls = set()
    cursor = None
    while True:
        params = {"actor": handle, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        page = fetch_json(session, feed_url, params=params)
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
            for facet in record.get("facets", []):
                for feature in facet.get("features", []):
                    if feature.get("$type") == "app.bsky.richtext.facet#link":
                        urls.add(feature["uri"])
        cursor = page.get("cursor")
        if not cursor or not page.get("feed") or oldest < cutoff:
            return urls


def choose_paper(papers, catalog, history, feed_urls, catalog_url, paper_id=None):
    posted_ids = {item["paper_id"] for item in history}
    for paper in papers:
        if paper_id and paper["id"] != paper_id:
            continue
        if paper["id"] not in catalog:
            raise ValueError(f"Paper missing from public catalog: {paper['id']}")
        links = resource_links(catalog[paper["id"]], catalog_url)
        if paper["id"] not in posted_ids and links["manuscript"] not in feed_urls:
            return paper, links
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--paper-id")
    parser.add_argument("--repost", action="store_true")
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--handle", default=os.getenv("BSKY_HANDLE"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.handle:
        parser.error("BSKY_HANDLE or --handle is required")
    if args.preview_dir and not args.dry_run:
        parser.error("--preview-dir requires --dry-run")
    if args.repost and not args.paper_id:
        parser.error("--repost requires an explicit --paper-id")

    config = load_config(args.config, "paper")
    catalog_url = config["catalog_url"]
    papers = json.loads(
        config_path(args.config, config["papers_file"]).read_text(encoding="utf-8")
    )
    history = load_history(args.history)
    now = datetime.now(UTC)
    session = requests.Session()
    catalog = catalog_papers(session, catalog_url)
    if args.paper_id and args.paper_id not in catalog:
        parser.error(f"Unknown paper ID: {args.paper_id}")
    if args.paper_id and args.paper_id not in {paper["id"] for paper in papers}:
        parser.error(
            "Paper needs a reviewed blurb and exhibits in the configured queue"
        )
    feed_urls = recent_feed_paper_urls(
        session, args.handle, now, config.get("feed_url", FEED_URL)
    )
    action_outputs()
    preview_existing = (args.dry_run and args.paper_id) or args.repost
    selected = choose_paper(
        papers,
        catalog,
        [] if preview_existing else history,
        set() if preview_existing else feed_urls,
        catalog_url,
        args.paper_id,
    )
    if selected is None:
        if args.paper_id:
            print("Paper already posted")
            return
        raise ValueError(
            "Paper queue exhausted: prepare more reviewed blurbs and exhibits"
        )
    paper, links = selected
    action_outputs(selected_id=paper["id"])
    if not paper["caption"].strip() or normalized_text(
        paper["caption"]
    ) == normalized_text(catalog[paper["id"]]["title"]):
        raise ValueError("Paper needs a substantive blurb, not just its title")
    if len(paper["figures"]) > 4:
        raise ValueError(f"Paper {paper['id']} must have at most 4 exhibits")
    text, facets = format_post(paper, links)
    figures = [prepare_figure(session, figure) for figure in paper["figures"]]
    if not figures:
        figures = [
            prepare_title_abstract(
                session, paper, catalog[paper["id"]], links["manuscript"]
            )
        ]
    print(f"Selected {paper['id']}")
    print(text)
    for index, (_, alt, width, height) in enumerate(figures, start=1):
        print(f"Image {index} ({width}x{height}): {alt}")
    if args.dry_run:
        if args.preview_dir:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            for old_image in args.preview_dir.glob("image-[1-4].png"):
                old_image.unlink()
            for index, (content, _, _, _) in enumerate(figures, start=1):
                (args.preview_dir / f"image-{index}.png").write_bytes(content)
            (args.preview_dir / "post.json").write_text(
                json.dumps(
                    {"text": text, "facets": facets, "alt": [f[1] for f in figures]},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return

    remaining = sum(
        choose_paper([item], catalog, history, feed_urls, catalog_url) is not None
        for item in papers
        if item["id"] != paper["id"]
    )
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
    write_history(args.history, history)
    action_outputs(selected_id=paper["id"], post_uri=result.uri, posted=True)
    print(f"Published {result.uri}")
    print(f"Prepared papers remaining: {remaining}")
    if remaining <= 3:
        print(
            f"::warning::Only {remaining} prepared papers remain; replenish the queue"
        )


if __name__ == "__main__":
    main()
