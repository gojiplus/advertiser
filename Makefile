.PHONY: check ci-docker

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen pyright
	uv run --frozen pytest
	actionlint .github/workflows/*.yml

ci-docker:
	docker run --rm -v "$(CURDIR):/workspace" -w /workspace \
		-e UV_PROJECT_ENVIRONMENT=/tmp/advertiser-venv \
		python:3.14-slim sh -c 'pip install uv && uv sync --frozen && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pyright && uv run --frozen pytest'
