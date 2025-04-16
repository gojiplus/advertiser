import os
import random
import requests
from atproto import Client
from summarize import summarize

GITHUB_API = "https://api.github.com"

username = os.environ["GITHUB_USERNAME"]
orgs = os.environ.get("GITHUB_ORGS", "")
headers = {"Accept": "application/vnd.github+json"}
token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")

if token:
    headers["Authorization"] = f"Bearer {token}"

def fetch_repos_for_user(user):
    url = f"{GITHUB_API}/users/{user}/repos?per_page=100&type=public"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

# Get user and org repos
repos = fetch_repos_for_user(username)
for org in [o.strip() for o in orgs.split(",") if o.strip()]:
    repos += fetch_repos_for_user(org)

# Filter repos with ≥ 5 stars
eligible_repos = [r for r in repos if r.get("stargazers_count", 0) >= 5]

if not eligible_repos:
    raise Exception("No repositories found with 5+ stars.")

# Pick one randomly
repo = random.choice(eligible_repos)
title = repo["name"]
url = repo["html_url"]
desc = repo.get("description", "")

summary = summarize(url, desc)
post = f"📦 {title}\n{summary}\n{url}"

# Post to Bluesky
client = Client()
client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_PASSWORD"])
client.send_post(post)
