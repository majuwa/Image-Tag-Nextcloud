# Nextcloud Tag Sync — Agent Reference

## Entry Point
`tag_sync.py` — single CLI argument: WebDAV path to image (e.g. `/Photos/vacation/img.jpg`)

```bash
python tag_sync.py /Photos/vacation/img.jpg
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tag_sync.py /Photos/vacation/img.jpg
```

## Credentials

Stored in `.env` (gitignored). Template in `.env.example`.

Variables:
- `NEXTCLOUD_URL` — base URL of the Nextcloud instance
- `NEXTCLOUD_LOGIN_NAME` — login name returned by Login Flow v2
- `NEXTCLOUD_APP_PASSWORD` — app password returned by Login Flow v2

On first run (if any variable is missing), the script initiates **Login Flow v2**:
1. POSTs to `/index.php/login/v2` to obtain a `login` URL and a poll token
2. Opens the login URL in the browser (or prints it as fallback)
3. Polls the endpoint every 2 s until the user completes login
4. Writes the resulting credentials to `.env` for future runs

## WebDAV URL Pattern

```
{NEXTCLOUD_URL}/remote.php/dav/files/{loginName}{webdav_path}
```

## Libraries Used

| Library | Purpose |
|---|---|
| `requests` | All HTTP (WebDAV, OCS API, Memories API) via a single `Session` |
| `Pillow` | IPTC keyword extraction via `PIL.IptcImagePlugin` |
| `python-dotenv` | Load/write `.env` credentials |
| `piexif` | (available) EXIF access if needed |
| `xml.etree.ElementTree` | XMP XML parsing (stdlib, no C deps) |

## Five-Step Execution Sequence

```
1. Parse CLI → webdav_path
2. Load or acquire credentials → (base_url, login_name, app_password)
3. Download image bytes via WebDAV
4. Extract tags from IPTC Keywords ∪ XMP dc:subject
5. Resolve tag IDs (create missing tags), fetch file ID, apply tags, verify
```

## API Endpoints Used

| Purpose | Method | Path |
|---|---|---|
| Download image | GET | `/remote.php/dav/files/{loginName}{path}` |
| Get file ID | PROPFIND | `/remote.php/dav/files/{loginName}{path}` |
| List tags | GET | `/index.php/apps/systemtags/api/v1/tags` |
| Create tag | POST | `/index.php/apps/systemtags/api/v1/tags` |
| Apply tags | PATCH | `/apps/memories/api/tags/set/{fileId}` |
| Verify tags | GET | `/apps/memories/api/image/info/{fileId}` |
