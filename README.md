# nextcloud-tagging

Syncs IPTC/XMP tags embedded in image files to the [Nextcloud Memories](https://github.com/pulsejet/memories) app via its API.

Given a folder path on your Nextcloud, the script:
1. Lists all image files recursively via WebDAV
2. Extracts tags from each image (IPTC Keywords and XMP `dc:subject`)
3. Creates any missing system tags in Nextcloud
4. Applies the tags to each file via the Memories API

## Requirements

- Nextcloud with the [Memories](https://apps.nextcloud.com/apps/memories) app installed
- Python 3.10+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the credentials template and fill it in — or let the script run the Login Flow on first start:

```bash
cp .env.example .env   # optional — script will prompt if missing
```

## Usage

```bash
python tag_sync.py /Photos/vacation
```

The argument is the **WebDAV folder path** as it appears inside your Nextcloud files (relative to your user root). The script recurses into subfolders automatically.

Supported image formats: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.heic`, `.heif`, `.webp`

### Options

| Flag | Description |
|------|-------------|
| `--url URL` | Override the Nextcloud base URL (useful for one-off runs without editing `.env`) |

### First run — authentication

If no credentials are found in `.env`, the script initiates [Nextcloud Login Flow v2](https://docs.nextcloud.com/server/latest/developer_manual/client_apis/LoginFlow/index.html#login-flow-v2):

1. A browser window opens with the Nextcloud login page
2. After you approve the app, credentials are saved to `.env` automatically

### Credentials file (`.env`)

| Variable | Description |
|----------|-------------|
| `NEXTCLOUD_URL` | Base URL, e.g. `https://cloud.example.com` |
| `NEXTCLOUD_LOGIN_NAME` | Login name returned by Login Flow |
| `NEXTCLOUD_APP_PASSWORD` | App password returned by Login Flow |

## Running the tests

```bash
python -m unittest test_tag_sync.py -v
```

No live Nextcloud instance is required — all tests use mocks.

## Project layout

| File | Purpose |
|------|---------|
| `tag_sync.py` | Main script |
| `test_tag_sync.py` | Unit tests |
| `requirements.txt` | Python dependencies |
| `tagging-api.md` | Notes on the Nextcloud / Memories API endpoints used |
| `.github/workflows/test.yml` | CI — runs tests on every push |
