#!/usr/bin/env python3
import os
import random
import requests
from atproto import Client
from summarize import summarize

GITHUB_API = "https://api.github.com"
USERNAME   = os.environ["GITHUB_USERNAME"]
ORGS       = os.environ.get("GITHUB_ORGS", "")
TOKEN      = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
HEADERS = {
    "Accept": "application/vnd.github+json",
    **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})
}

def fetch_repos_for_user(user: str):
    url = f"{GITHUB_API}/users/{user}/repos?per_page=100&type=public"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def choose_repo():
    # get your user's repos
    repos = fetch_repos_for_user(USERNAME)
    
    # plus any orgs you've listed
    for org in [o.strip() for o in ORGS.split(",") if o.strip()]:
        repos += fetch_repos_for_user(org)
    
    # filter to >=5 stars and not archived
    eligible = [r for r in repos if r.get("stargazers_count", 0) >= 5 and not r.get("archived", False)]
    
    if not eligible:
        raise RuntimeError("No non-archived repositories found with ≥5 stars.")
    
    return random.choice(eligible)

def make_post_text(repo: dict):
    title = repo["name"]
    url   = repo["html_url"]
    desc  = repo.get("description") or ""
    summary = summarize(url, desc)
    post_text = f"📦 {title}\n\n{summary}\n\n{url}"
    return post_text, url

def make_facets(post_text: str, url: str):
    # find character-based index
    char_start = post_text.find(url)
    if char_start < 0:
        raise ValueError(f"URL '{url}' not found in the post text")
    
    # compute byte offsets
    prefix_bytes = post_text[:char_start].encode("utf-8")
    url_bytes    = url.encode("utf-8")
    byte_start   = len(prefix_bytes)
    byte_end     = byte_start + len(url_bytes)
    
    print(f"→ link byteStart={byte_start}, byteEnd={byte_end}")
    
    return [
        {
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#link",
                    "uri": url
                }
            ]
        }
    ]

def post_to_bsky(post_text: str, facets: list):
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])
    client.send_post(post_text, facets=facets)
    print("✅ Posted to Bluesky!")

if __name__ == "__main__":
    repo = choose_repo()
    post_text, url = make_post_text(repo)
    facets = make_facets(post_text, url)
    post_to_bsky(post_text, facets)
