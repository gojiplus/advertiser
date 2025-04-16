import random
import json
import os
from atproto import Client
from summarize import summarize

# Load curated list of repos
with open("data/repos.json", "r") as f:
    repos = [r for r in json.load(f) if r.get("stars", 0) >= 5]

repo = random.choice(repos)
title = repo["name"]
url = repo["html_url"]
desc = repo.get("description", "")

summary = summarize(url, desc)
post = f"🔍 {title}\n{summary}\n{url}"

# Auth & post to bsky
client = Client()
client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])
client.send_post(post)
