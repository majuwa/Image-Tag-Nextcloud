#!/usr/bin/env python3
"""Sync IPTC/XMP tags from a Nextcloud image to the Memories app."""

import argparse
import io
import os
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, quote, unquote

import requests
from dotenv import load_dotenv, set_key
from PIL import Image
from PIL import IptcImagePlugin

ENV_FILE = Path(__file__).parent / ".env"


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif", ".heic", ".heif", ".png", ".webp"}


def main():
    parser = argparse.ArgumentParser(
        description="Sync IPTC/XMP tags from all images in a Nextcloud folder to the Memories app."
    )
    parser.add_argument("webdav_folder", help="WebDAV path to folder, e.g. /Photos/vacation")
    parser.add_argument("--url", metavar="URL", help="Nextcloud base URL (overrides .env and NEXTCLOUD_URL)")
    args = parser.parse_args()

    try:
        base_url, login_name, app_password = load_or_acquire_credentials(url_override=args.url)
        session = make_session(login_name, app_password)

        image_paths = list_image_files(session, base_url, login_name, args.webdav_folder)
        if not image_paths:
            print("No image files found in folder.")
            sys.exit(0)

        print(f"Found {len(image_paths)} image(s) to process.")

        errors = []
        for webdav_path in image_paths:
            print(f"\n--- {webdav_path} ---")
            try:
                image_bytes = download_image(session, base_url, login_name, webdav_path)

                tag_names = extract_all_tags(image_bytes)
                if not tag_names:
                    print("  No tags found, skipping.")
                    continue

                print(f"  Tags: {tag_names}")

                tag_ids = resolve_tag_ids(session, base_url, tag_names)
                file_id = get_file_id(session, base_url, login_name, webdav_path)

                apply_tags(session, base_url, file_id, list(tag_ids.values()))
                print(f"  Applied tags to file {file_id}")

                verify_tags(session, base_url, file_id)
            except (requests.HTTPError, requests.ConnectionError, ValueError) as exc:
                print(f"  Error: {exc}", file=sys.stderr)
                errors.append((webdav_path, exc))

        if errors:
            print(f"\n{len(errors)} file(s) failed.", file=sys.stderr)
            sys.exit(1)

    except (requests.HTTPError, requests.ConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def load_or_acquire_credentials(url_override=None):
    """Load credentials from .env or run Login Flow v2 to obtain them.

    url_override: if provided, takes priority over .env / NEXTCLOUD_URL env var.
    """
    load_dotenv(ENV_FILE)
    base_url = (url_override or os.getenv("NEXTCLOUD_URL", "")).rstrip("/")
    login_name = os.getenv("NEXTCLOUD_LOGIN_NAME", "")
    app_password = os.getenv("NEXTCLOUD_APP_PASSWORD", "")

    if base_url and login_name and app_password:
        return base_url, login_name, app_password

    if not base_url:
        base_url = input("Nextcloud URL (e.g. https://cloud.example.com): ").strip().rstrip("/")

    base_url, login_name, app_password = run_login_flow_v2(base_url)

    ENV_FILE.touch(mode=0o600)
    set_key(str(ENV_FILE), "NEXTCLOUD_URL", base_url)
    set_key(str(ENV_FILE), "NEXTCLOUD_LOGIN_NAME", login_name)
    set_key(str(ENV_FILE), "NEXTCLOUD_APP_PASSWORD", app_password)
    print(f"Credentials saved to {ENV_FILE}")

    return base_url, login_name, app_password


def run_login_flow_v2(base_url):
    """Perform Nextcloud Login Flow v2 and return (base_url, login_name, app_password)."""
    init_url = f"{base_url}/index.php/login/v2"
    resp = requests.post(init_url, headers={"OCS-APIREQUEST": "true"})
    resp.raise_for_status()
    data = resp.json()

    poll_token = data["poll"]["token"]
    poll_endpoint = data["poll"]["endpoint"]
    login_url = data["login"]

    print(f"\nOpen the following URL in your browser to log in:\n  {login_url}\n")
    webbrowser.open(login_url)
    print("Waiting for login...", end="", flush=True)

    while True:
        time.sleep(2)
        poll_resp = requests.post(poll_endpoint, data={"token": poll_token})
        if poll_resp.status_code == 200:
            creds = poll_resp.json()
            print(" done.")
            return (
                creds["server"].rstrip("/"),
                creds["loginName"],
                creds["appPassword"],
            )
        print(".", end="", flush=True)


def make_session(login_name, app_password):
    """Create a requests.Session pre-configured for Nextcloud API calls."""
    session = requests.Session()
    session.auth = (login_name, app_password)
    session.headers.update({
        "OCS-APIREQUEST": "true",
        "Accept": "application/json",
    })
    return session


def list_image_files(session, base_url, login_name, folder_path):
    """Return list of WebDAV paths for all image files under folder_path (recursive)."""
    encoded_path = quote(folder_path)
    url = f"{base_url}/remote.php/dav/files/{quote(login_name)}{encoded_path}"
    propfind_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:propfind xmlns:d="DAV:">'
        "  <d:prop><d:resourcetype/></d:prop>"
        "</d:propfind>"
    )
    resp = session.request(
        "PROPFIND",
        url,
        data=propfind_body,
        headers={"Depth": "infinity", "Content-Type": "text/xml; charset=\"utf-8\""},
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    dav_ns = "DAV:"
    prefix = f"/remote.php/dav/files/{quote(login_name)}"

    paths = []
    for response in root.iter(f"{{{dav_ns}}}response"):
        href_elem = response.find(f"{{{dav_ns}}}href")
        if href_elem is None or not href_elem.text:
            continue
        href = href_elem.text
        # Skip collections (directories)
        resourcetype = response.find(f".//{{{dav_ns}}}resourcetype")
        if resourcetype is not None and resourcetype.find(f"{{{dav_ns}}}collection") is not None:
            continue
        # Strip the WebDAV prefix to get the plain path
        if href.startswith(prefix):
            file_path = unquote(href[len(prefix):])
        else:
            file_path = unquote(href)
        ext = Path(file_path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            paths.append(file_path)

    return paths


def download_image(session, base_url, login_name, webdav_path):
    """Download image bytes from Nextcloud via WebDAV."""
    encoded_path = quote(webdav_path)
    url = f"{base_url}/remote.php/dav/files/{quote(login_name)}{encoded_path}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.content


def read_iptc_keywords(image_bytes):
    """Return set of IPTC Keywords (field 2:25) from image bytes."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        iptc = IptcImagePlugin.getiptcinfo(image)
        if not iptc:
            return set()
        raw = iptc.get((2, 25), [])
        if isinstance(raw, bytes):
            raw = [raw]
        return {kw.decode("utf-8", errors="replace") for kw in raw}
    except Exception:
        return set()


def read_xmp_subjects(image_bytes):
    """Return set of XMP dc:subject values by scanning raw bytes for APP1 XMP block."""
    XMP_MARKER = b"http://ns.adobe.com/xap/1.0/\x00"
    APP1_MARKER = b"\xff\xe1"

    subjects = set()
    data = image_bytes
    pos = 0

    while True:
        idx = data.find(APP1_MARKER, pos)
        if idx == -1:
            break
        if idx + 4 > len(data):
            break
        segment_length = int.from_bytes(data[idx + 2:idx + 4], "big")
        segment_end = idx + 2 + segment_length
        segment = data[idx + 4:segment_end]

        if segment.startswith(XMP_MARKER):
            xmp_xml = segment[len(XMP_MARKER):]
            try:
                root = ET.fromstring(xmp_xml)
                ns = {
                    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                    "dc": "http://purl.org/dc/elements/1.1/",
                }
                for li in root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li"):
                    parent = _find_parent_tag(root, li, ns)
                    if parent == "subject":
                        if li.text:
                            subjects.add(li.text.strip())
            except ET.ParseError:
                pass

        pos = segment_end

    return subjects


def _find_parent_tag(root, target_li, ns):
    """Walk the XMP tree to determine if an rdf:li is inside dc:subject."""
    for subject_elem in root.iter("{http://purl.org/dc/elements/1.1/}subject"):
        for bag in subject_elem.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Bag"):
            for li in bag:
                if li is target_li:
                    return "subject"
    return None


def extract_all_tags(image_bytes):
    """Return sorted list of unique tags from IPTC Keywords ∪ XMP dc:subject."""
    iptc = read_iptc_keywords(image_bytes)
    xmp = read_xmp_subjects(image_bytes)
    combined = {t.strip() for t in iptc | xmp if t.strip()}
    return sorted(combined)


def _list_existing_tags(session, base_url):
    """Return {name: id} for all system tags via WebDAV PROPFIND."""
    url = f"{base_url}/remote.php/dav/systemtags"
    propfind_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        "  <d:prop>"
        "    <oc:id/>"
        "    <oc:display-name/>"
        "  </d:prop>"
        "</d:propfind>"
    )
    resp = session.request(
        "PROPFIND",
        url,
        data=propfind_body,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    resp.raise_for_status()

    existing = {}
    root = ET.fromstring(resp.content)
    oc_ns = "http://owncloud.org/ns"
    for response in root.iter("{DAV:}response"):
        id_elem = response.find(f".//{{{oc_ns}}}id")
        name_elem = response.find(f".//{{{oc_ns}}}display-name")
        if id_elem is not None and name_elem is not None and id_elem.text and name_elem.text:
            existing[name_elem.text] = int(id_elem.text)
    return existing


def resolve_tag_ids(session, base_url, tag_names):
    """Return {name: id} for all tag_names, creating missing tags as needed."""
    existing = _list_existing_tags(session, base_url)

    result = {}
    for name in tag_names:
        if name in existing:
            print(f"  Tag '{name}' already exists (ID {existing[name]})")
            result[name] = existing[name]
        else:
            tag_id = create_tag(session, base_url, name)
            print(f"  Created tag '{name}' with ID {tag_id}")
            result[name] = tag_id

    return result


def create_tag(session, base_url, name):
    """Create a new system tag and return its ID."""
    url = f"{base_url}/remote.php/dav/systemtags"
    resp = session.post(url, json={
        "name": name,
        "userVisible": True,
        "userAssignable": True,
    })
    resp.raise_for_status()
    content_location = resp.headers.get("Content-Location", "")
    return int(content_location.rstrip("/").split("/")[-1])


def get_file_id(session, base_url, login_name, webdav_path):
    """Return the oc:fileid for a file via PROPFIND."""
    encoded_path = quote(webdav_path)
    url = f"{base_url}/remote.php/dav/files/{quote(login_name)}{encoded_path}"
    propfind_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        "  <d:prop>"
        "    <oc:fileid/>"
        "  </d:prop>"
        "</d:propfind>"
    )
    resp = session.request(
        "PROPFIND",
        url,
        data=propfind_body,
        headers={"Depth": "0", "Content-Type": "text/xml; charset=\"utf-8\""},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    fileid_elem = root.find(".//{http://owncloud.org/ns}fileid")
    if fileid_elem is None or not fileid_elem.text:
        raise ValueError(f"Could not find oc:fileid in PROPFIND response for {webdav_path}")
    return int(fileid_elem.text)


def apply_tags(session, base_url, file_id, tag_ids):
    """Apply tag IDs to a file via the Memories API."""
    url = f"{base_url}/apps/memories/api/tags/set/{file_id}"
    resp = session.patch(url, json={"add": tag_ids})
    resp.raise_for_status()


def verify_tags(session, base_url, file_id):
    """Fetch and print tags currently assigned to the file."""
    url = f"{base_url}/apps/memories/api/image/info/{file_id}"
    resp = session.get(url, params={"tags": "true"})
    resp.raise_for_status()
    info = resp.json()
    tags = info.get("tags", [])
    print(f"Tags on file {file_id}: {tags}")


if __name__ == "__main__":
    main()
