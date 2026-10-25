<img src="advertiser_logo.png" alt="Advertiser logo" width="40" height="40" />

# Advertiser

Advertiser posts public repositories from `soodoku` and the organizations listed in
[`scripts/post_repo_to_bsky.py`](scripts/post_repo_to_bsky.py) to Bluesky. It uses
reviewed blurbs or GitHub descriptions, so posting does not require an OpenAI key.

## Selection

- Repositories need a description and at least one star in the scheduled
  workflow. The cutoff is set by `GITHUB_STAR_CUTOFF` and defaults to five for
  local runs. Private, archived, disabled, and forked repositories are excluded.
- Daily runs select the highest-starred available repository from the top 30,
  except Fridays, which use the rotation pool starting below the top 30.
  Manual runs can also choose the rotation pool.
  If the chosen pool is empty, the other pool is used.
- Five prominent repositories have reviewed descriptions in
  `data/featured_blurbs.json`. Other posts use their GitHub descriptions.
- Repositories posted in the last 90 days are excluded. The workflow commits
  successful posts to `data/post_history.json` and also checks recent posts on
  the Bluesky account before choosing, which protects against a retry after a
  history commit fails.

The repository workflow is scheduled daily at 7:00 a.m. Pacific time.
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
uv sync --frozen
uv run scripts/post_repo_to_bsky.py --dry-run --handle YOUR_HANDLE
```

The public GitHub API can be rate limited without a token. Set `GITHUB_TOKEN`
locally if needed. No Bluesky password is required for a dry run.

## Paper posts

The separate **Post Paper to Bluesky** workflow is scheduled daily at 7:00 a.m.
Pacific time. GitHub can delay scheduled runs; this is not a guaranteed posting
time. It uses the same Bluesky account and Actions secrets. The public paper
catalog at `https://gsood.com/research/catalog.json` supplies manuscript,
replication, and data links.

Each entry in `data/papers.json` needs a reviewed blurb that explains the
research question, why it matters, and a concrete takeaway. Preserve material
qualifications, including whether evidence comes from simulations.

Every post includes images. Select graphs or tables that support its takeaway,
keeping captions, axis labels, uncertainty, and table notes visible. The queue's
`figures` list accepts PNG sources or PDF exhibits. PDF entries specify a
one-based `page` and an optional `clip` rectangle `[left, top, right, bottom]`
in PDF points from the top-left corner. Each source has a SHA-256 checksum
and descriptive alt text. The bot renders PDF crops at 200 DPI, removes image
metadata, preserves aspect ratios, and enforces the 2 MB limit per image.

When no graph or table is selected, an empty `figures` list uses a screenshot
of the manuscript's **title and complete abstract**, alongside the reviewed
blurb. This requires `manuscript_sha256`. The bot verifies that checksum and
the title, locates the abstract and its end, and crops the original PDF.
Unrecognized layouts require a manually reviewed PDF crop. Source changes,
failed renders, missing blurbs, and oversized images stop publication; there
is no title-only or image-free posting path.

Papers are posted once. The workflow commits successful posts to
`data/paper_post_history.json` and checks recent Bluesky posts for a manuscript
link before publishing. Catalog entries do not enter the posting queue until
their blurb and exhibit choices have been prepared. An exhausted queue fails
the workflow so it is visible, rather than reporting a successful empty run.

Use **Actions → Post Paper to Bluesky → Run workflow** to preview (the default)
or publish. A `paper_id` selects a prepared paper. Dry runs can preview an
already-posted paper without changing history; live runs still refuse duplicates.
Workflow previews upload the text, link facets, alt text, and images as a
`paper-preview` artifact. To save a preview locally:

```sh
uv run scripts/post_paper_to_bsky.py --dry-run --handle YOUR_HANDLE \
  --preview-dir preview
```

## Development

This automation app follows [py-canon's toolchain](https://github.com/gojiplus/py-canon/blob/main/STANDARD.md):
`pyproject.toml`, a committed `uv.lock`, Ruff, Pyright, and strict pytest checks.
CI tests Python 3.12 and 3.14 with frozen dependencies. The app is not a
distributable package, so it uses a local reusable CI workflow instead of
py-canon's package workflow, which always builds a wheel. Package publishing,
Sphinx, and library docstring checks do not apply to these operator scripts.

The only warning exception covers MuPDF's native SWIG import warnings, which
can crash the interpreter when raised as errors ([upstream issue](https://github.com/pymupdf/PyMuPDF/issues/4392)).
Remove it when fixed wheels are available. Other warnings remain errors.

Install uv and actionlint, then run:

```sh
uv sync --frozen
make check
make ci-docker
```

`ci-docker` runs the Python checks in the standard `python:3.14-slim` image.
`make check` also lints the GitHub workflows with actionlint. Dependency and
action updates are managed by Dependabot.
