# Webhook updater

Update file from Webhook event from GitHub.

> [!WARNING]
> I think it might work, but I haven't actually tested it.

## Features

- Catch webhook from GitHub.
- Following actions.
  - Git pull
  - Download release file and Uncompress archive file
    - tar.gz
    - zip
  - Webhook relation
- Verify signature at header.
- **[NOT TESTED]** GitHub API Token (for Private repo)

## How to use

### Setup

1. Install uv(https://docs.astral.sh/uv/)
2. Execute: `uv sync`
3. Copy or create `config.yml`

### Run

1. Execute: `uv run main.py`
