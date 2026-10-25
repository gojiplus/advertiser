<img src="advertiser_logo.png" alt="Advertiser logo" width="40" height="40" />

# Advertiser

Advertiser is a reusable GitHub Action for posting research papers with exhibits
and public repositories to Bluesky. You supply configuration, reviewed copy,
and posting history. The action selects and publishes one item, returns the
result, and updates your history file. Your workflow owns the schedule and
history persistence. No OpenAI key is needed.

## Use in another repository

Create `advertiser/config.json` in your repository:

```json
{
  "kind": "repository",
  "owners": ["YOUR_GITHUB_USERNAME"],
  "min_stars": 5
}
```

Then add a workflow (tested on Ubuntu runners):

```yaml
name: Advertise
on:
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: true
  schedule:
    - cron: "0 7 * * *"
      timezone: America/Los_Angeles
concurrency:
  group: advertiser
  cancel-in-progress: false
permissions:
  contents: write
jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7.0.1
      - uses: gojiplus/advertiser@main
        id: advertise
        with:
          config-file: advertiser/config.json
          history-file: advertiser/history.json
          bsky-handle: ${{ secrets.BSKY_HANDLE }}
          bsky-password: ${{ secrets.BSKY_PASSWORD }}
          dry-run: ${{ inputs.dry_run || false }}
      - name: Save history
        if: steps.advertise.outputs.posted == 'true'
        run: |
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git add advertiser/history.json
          git commit -m 'Record Bluesky post'
          git push
```

`@main` follows development; pin a reviewed commit SHA for reproducible runs.
Use a Bluesky app password. The action itself only needs read access to public
repositories; the example grants write access to persist history. If branch
protection blocks pushes, persist the file through your existing PR or storage
workflow. Restore history before invoking the action. Serialize runs for the
same account to prevent concurrent selection of the same item.

Paths supplied as inputs are relative to the caller's workspace (absolute paths
also work). Paths *inside* configuration are relative to that configuration file.
A missing history file starts an empty history and is created only after a
successful post. Dependencies use the action's frozen `uv.lock` and a separate
virtual environment under the runner's temporary directory.

| Input | Meaning / default |
| --- | --- |
| `config-file` | Required JSON configuration |
| `history-file` | Required JSON history path |
| `bsky-handle` | Required account handle |
| `bsky-password` | Required for live posts; optional for previews |
| `github-token` | Token for public repository reads; defaults to `github.token` |
| `dry-run` | `true` by default; must be `true` or `false` |
| `preview-dir` | Optional dry-run output directory |
| `paper-id` | Optional selection from the reviewed queue |
| `repost` | `false`; explicit correction override requiring `paper-id` |
| `pool` | Repository selection: `auto`, `featured`, or `rotation` |

Outputs are `posted` (`true` or `false`), `selected-id` (paper ID or repository
full name), and `post-uri` (AT Protocol URI, empty for previews/skips).
Repository cooldown exhaustion skips; an exhausted paper queue fails so that
missing editorial preparation is visible.

Repository configuration accepts `owners` and `organizations` (at least one
nonempty list), `min_stars` (default 5), `featured_count` (30), `cooldown_days`
(90), `timezone` (`UTC`), optional `rotation_weekday` (Monday=0 through Sunday=6),
and optional `blurbs_file` (JSON object mapping full repository names to copy).
Without `rotation_weekday`, automatic selection always uses the featured pool.

Paper configuration:

```json
{
  "kind": "paper",
  "catalog_url": "https://example.org/research/catalog.json",
  "papers_file": "papers.json"
}
```

The catalog contains an `entries` array with `kind: "paper"`, `id`, `title`,
and `resources` (`role` and `url`; a `manuscript` is required). Resource URLs
resolve relative to the catalog URL. The reviewed queue is a JSON array:

```json
[
  {
    "id": "my-paper",
    "caption": "Explain the question, why it matters, and the key finding.",
    "figures": [
      {
        "url": "https://example.org/research/paper.pdf",
        "sha256": "REPLACE_WITH_THE_SOURCE_FILE_SHA256",
        "page": 3,
        "alt": "Describe the evidence shown in this graph or table."
      }
    ]
  }
]
```

Both modes optionally accept `feed_url` for an alternative Bluesky AppView's
`app.bsky.feed.getAuthorFeed` endpoint. The default is the public Bluesky API.

The root `action.yml` provides the metadata used by GitHub Marketplace. The
listing is not published yet: publication requires a tagged release, an
available action name, and the repository owner's acceptance of the
[Marketplace Developer Agreement](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace).

## This repository's daily posts

The workflows in this repository consume the same action with settings in
`data/repository-config.json` and `data/paper-config.json`. These files and the
prepared queue are examples of caller data; the action never loads them unless
explicitly selected by its inputs.

## Selection

- Repositories need a description and at least one star in the scheduled
  workflow, configured by `min_stars` in `data/repository-config.json`. Private, archived, disabled, and forked repositories are excluded.
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
uv run python -m scripts.post_repo_to_bsky --dry-run --handle YOUR_HANDLE \
  --config data/repository-config.json --history data/post_history.json
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
To intentionally publish a corrected version, select its `paper_id` and enable
the manual workflow's `repost` input (locally, `--repost --paper-id ID`). Scheduled
runs never enable this override. The earlier post and its history are retained.
Workflow previews upload the text, link facets, alt text, and images as a
`paper-preview` artifact. To save a preview locally:

```sh
uv run python -m scripts.post_paper_to_bsky --dry-run --handle YOUR_HANDLE \
  --config data/paper-config.json --history data/paper_post_history.json \
  --preview-dir preview
```

## Development

This automation app follows [py-canon's toolchain](https://github.com/gojiplus/py-canon/blob/main/STANDARD.md):
`pyproject.toml`, a committed `uv.lock`, Ruff, Pyright, and strict pytest checks.
CI tests Python 3.12 and 3.14 with frozen dependencies, plus a composite-action
preview with a separate consumer checkout and synthetic catalog. The app is not a
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
