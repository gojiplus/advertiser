<img src="advertiser_logo.png" alt="Advertiser logo" width="40" height="40" />

# Advertiser

Advertiser posts public repositories from `soodoku` and the organizations listed in
[`scripts/post_repo_to_bsky.py`](scripts/post_repo_to_bsky.py) to Bluesky. It uses
reviewed blurbs or GitHub descriptions, so posting does not require an OpenAI key.

## Selection

- Repositories need a description and at least one star in the scheduled
  workflow. The cutoff is set by `GITHUB_STAR_CUTOFF` and defaults to five for
  local runs. Private, archived, disabled, and forked repositories are excluded.
- Monday and Wednesday select the highest-starred available repository from the
  top 30. Friday selects the highest-starred available repository from the rest.
  If the chosen pool is empty, the other pool is used.
- Five prominent repositories have reviewed descriptions in
  `data/featured_blurbs.json`. Other posts use their GitHub descriptions.
- Repositories posted in the last 90 days are excluded. The workflow commits
  successful posts to `data/post_history.json` and also checks recent posts on
  the Bluesky account before choosing, which protects against a retry after a
  history commit fails.

The workflow runs at 9:17 a.m. Pacific time on Monday, Wednesday, and Friday.
GitHub may delay scheduled runs. If the repository has no activity for 60 days,
GitHub may disable its schedule; re-enable the workflow in the Actions tab.

## Setup and preview

Set repository Actions secrets `BSKY_HANDLE` and `BSKY_PASSWORD`. Use a Bluesky
app password for `BSKY_PASSWORD`. The workflow uses its built-in `GITHUB_TOKEN`
to read public GitHub repositories and save post history. Its `contents: write`
permission is limited to the posting job.

Use **Actions → Post GitHub Repo to Bluesky → Run workflow**. The default manual
run is a dry run: it fetches repositories and recent Bluesky posts, prints the
candidate and final text, and publishes nothing. Select `featured` or `rotation`
to preview a particular pool. Set `dry_run` to false for a live manual post.

To preview locally:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/post_repo_to_bsky.py --dry-run --handle YOUR_HANDLE
```

The public GitHub API can be rate limited without a token. Set `GITHUB_TOKEN`
locally if needed. No Bluesky password is required for a dry run.

## Development checks

```sh
black --check scripts tests
isort --check-only scripts tests
flake8 scripts tests
python -m pytest -q
actionlint .github/workflows/post-to-bsky.yml
```
