# streamcreator2026

Python automation utility for creating a YouTube Live broadcast, generating its RTMP stream key, and pushing that key into an ATEM switcher through the local helper script at `src/atem-streamkey`.

This README documents the current Python workflow only.

## What it does

The Python entrypoint at `src/automate-stream.py` can:

1. Authenticate against the YouTube Data API.
2. Validate the broadcast metadata payload before creating anything.
3. Create a YouTube Live broadcast.
4. Create and bind an RTMP live stream to that broadcast.
5. Rename the YouTube ingestion stream to include the final broadcast ID.
6. Update the ATEM switcher with the generated YouTube stream key by invoking `src/atem-streamkey`.
7. Scan for stale, unbound YouTube live stream resources in cleanup mode.

## Requirements

- Python 3.10+
- A Google Cloud OAuth client for YouTube Data API access
- Network access to YouTube for API calls
- A Node.js runtime able to execute `src/atem-streamkey`
- Access to the `atem-connection` package required by `src/atem-streamkey`

Python dependencies are listed in `requirements.txt`:

- `python-dotenv`
- `google-auth`
- `google-auth-oauthlib`
- `google-api-python-client`

## Local files

The Python workflow relies on these local files:

- `src/automate-stream.py`: main Python entrypoint
- `src/atem-streamkey`: ATEM helper used for all ATEM read/write operations
- `client_secrets.json`: Google OAuth client credentials
- `token.pickle`: cached OAuth token created after authentication
- `config/broadcast.json`: optional local fallback broadcast metadata
- `.env`: optional local environment file loaded through `python-dotenv`

In the current repo state, `client_secrets.json`, `token.pickle`, `.env`, and `config/` are ignored by Git.

## Environment variables

The Python script loads environment variables from the current shell, `.env`, and `/etc/streamcreator2026/config.env`.

It expects these variables to be present at startup:

- `CHANNEL_ID`
- `ATEM_IP`
- `BROADCAST_META_URL`
- `THUMBNAIL_URL`

Current behavior is strict: if any one of those values is missing, the script exits before handling `--auth-only` or `--cleanup`.

## Setup

Create the virtual environment and install dependencies:

```bash
make venv
make install
```

The Make targets use `.venv/bin/python` directly, so activation is optional.

## Authentication

Run the one-time OAuth flow:

```bash
make auth
```

That runs:

```bash
.venv/bin/python src/automate-stream.py --auth-only
```

Authentication behavior:

- The script reads and writes cached credentials in `token.pickle`
- If the cached token is expired and has a refresh token, it refreshes it in place
- If no valid token exists, it starts a browser-based OAuth flow on `127.0.0.1`
- After auth, channel verification uses `CHANNEL_ID` from the environment

## Broadcast metadata

For normal run mode, the script loads broadcast metadata in this order:

1. Fetch JSON from `BROADCAST_META_URL`
2. If that fails or returns no usable payload, read `config/broadcast.json`

After loading metadata, the script overwrites these fields from environment-derived values:

- `atem_ip`
- `thumbnail_url`
- `channel_id`

Current expected metadata shape:

```json
{
  "title": "Weekend Service",
  "description": "Morning livestream",
  "start": "2026-05-24T14:00:00Z",
  "privacy": "public"
}
```

Current validation rules:

- `title` is required
- `start` is required
- `start` must parse as ISO 8601
- `start` must be in the future
- `privacy` defaults to `public`

Current broadcast construction behavior:

- `description` defaults to an empty string
- `scheduledStartTime` is taken from `start`
- No scheduled end time is sent
- `enableAutoStart` and `enableAutoStop` are both set to `true`
- The thumbnail is downloaded from `THUMBNAIL_URL`, not from the metadata payload

## Stream naming

After creating the ingestion stream, the Python code renames it twice conceptually:

1. Initial creation title: `Stream <timestamp>`
2. Final title: `<timestamp> (<broadcast_id>)`

That means the final YouTube broadcast/video ID is already known and is used in the stream title.

## Run the deployment

Run the full Python flow with:

```bash
make run
```

That target runs:

```bash
.venv/bin/python src/automate-stream.py
```

On success, the script prints:

- The YouTube watch URL
- The YouTube Studio livestream URL
- The final ATEM streaming service state as JSON

## Cleanup mode

Run cleanup with:

```bash
make cleanup
```

That target runs:

```bash
.venv/bin/python src/automate-stream.py --cleanup
```

Current cleanup behavior:

- It scans all live stream resources on the account
- It protects streams currently bound to any active, upcoming, or recent broadcast
- It identifies unbound streams older than 24 hours
- In the current code, the actual delete API call is commented out, so cleanup reports removals without deleting the streams on YouTube

## Make targets

- `make venv`: create the local virtual environment at `.venv`
- `make install`: install Python dependencies
- `make auth`: run OAuth bootstrap only
- `make get`: print the current ATEM streaming service JSON through `src/atem-streamkey`
- `make run`: run the broadcast creation and ATEM update flow
- `make cleanup`: run the Python cleanup flow
- `make check`: verify that `client_secrets.json` and `config/broadcast.json` exist
- `make scp`: copy the project files to `fbcr2:apps/streamcreator2026/`

## ATEM helper

The helper supports these direct invocations:

```bash
ATEM_IP=192.168.1.50 ./src/atem-streamkey
ATEM_IP=192.168.1.50 ./src/atem-streamkey get
ATEM_IP=192.168.1.50 ./src/atem-streamkey set <streamkey>
```

It prints the current streaming service JSON for reads and after writes.

## Notes

- Run commands from the repository root so the script can find `client_secrets.json`, `token.pickle`, and `config/broadcast.json`.
- Treat `token.pickle`, `client_secrets.json`, `.env`, and local config files as secrets.
- `make check` requires `config/broadcast.json`, even though the Python script can also fetch metadata from `BROADCAST_META_URL`.
