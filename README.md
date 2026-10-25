# Webhook updater

Update file from Webhook event from GitHub.

## Features

- Catch webhook from GitHub.
- Following actions.
  - Git pull
  - Download release file and Uncompress archive file
    - tar.gz
    - **[NOT TESTED]** zip
    - Retries briefly when the release meta is temporarily unavailable or the target asset is not attached yet (for example when CI uploads assets after publishing)
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

### Automatic self-update

Configure a GitHub webhook on this repository with payload URL
`https://your-host/update/this`, content type `application/json`, and the secret
configured in `this.secret`. The template accepts `push` events.

Run with `uv run main.py` (also when using systemd). After a verified matching
webhook, the updater runs `git pull --ff-only` in its own checkout, responds,
then gracefully stops the server. A supervisor runs `uv sync --locked` and starts
the updated server. Commit `uv.lock` with dependency changes. Direct
`uvicorn main:app` startup does not support self-update and returns HTTP 503.

The checkout must have an upstream branch and Git credentials configured.
A failed pull returns HTTP 500 and leaves the server running. A failed dependency
sync stops the service and logs the error; fix or revert the checkout and run
`uv sync --locked` before starting again. Restarting briefly interrupts service.

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
