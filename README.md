# Webhook updater

Update file from Webhook event from GitHub.

## Features

- Catch webhook from GitHub.
- Following actions.
  - Git pull
  - Download release file and Uncompress archive file
    - tar.gz
    - **[NOT TESTED]** zip
  - **[NOT TESTED]** Webhook relation
- Verify signature at header.
- **[NOT TESTED]** GitHub API Token (for Private repo)

## How to use

### Setup

1. Install uv(https://docs.astral.sh/uv/)
2. Execute: `uv sync`
3. Copy or create `config.yml`

### Run

1. Execute: `uv run main.py`

### Service sample (systemd)

```
[Unit]
Description=WebHook Updater

[Service]
Type=simple
WorkingDirectory=/path/to/webhook-updater
ExecStart=/path/to/uv run main.py
Restart=yes
User=<Running User> #IF NEEDED

[Install]
WantedBy=multi-user.target
```
