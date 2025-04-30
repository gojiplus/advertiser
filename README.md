<img src="advertiser_logo.png" alt="Repo Logo" width="34" height="64" />

# Advertiser: Post a GenAI-Summarized GitHub Repo to Bluesky

This GitHub Action picks a GitHub repository (with at least five stars) from a curated list, summarizes it using OpenAI, and posts the summary to your Bluesky feed.

Perfect for showcasing open-source gems to your followers — automatically or on demand.

---

## ✨ What It Does

- 🔀 Randomly selects a repository from a curated list
- 🧠 Uses GenAI (OpenAI) to summarize it into a crisp 1-liner
- 🔗 Posts the summary, repo name, and link to your [Bluesky](https://bsky.app/) feed

---

## 🚀 Usage

### `.github/workflows/post.yml`

```yaml
name: Post to Bluesky

on:
  workflow_dispatch:  # Manual trigger

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: appeler/post-to-bsky@v1
        with:
          bsky_handle: ${{ secrets.BSKY_HANDLE }}
          bsky_password: ${{ secrets.BSKY_PASSWORD }}
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
