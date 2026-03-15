"""Unit tests for tag_sync.py — no live Nextcloud required."""

import io
import struct
import unittest
from unittest.mock import MagicMock, patch

import tag_sync


# ---------------------------------------------------------------------------
# Helpers to build synthetic JPEG bytes with IPTC / XMP payloads
# ---------------------------------------------------------------------------

def make_jpeg_with_iptc(keywords: list[str]) -> bytes:
    """Build a minimal JPEG that PIL can read with IPTC keyword records."""
    iptc_data = b""
    for kw in keywords:
        raw = kw.encode("utf-8")
        iptc_data += b"\x1c\x02\x19" + struct.pack(">H", len(raw)) + raw

    ps_header = b"Photoshop 3.0\x00"
    bim = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(iptc_data)) + iptc_data
    app13_payload = ps_header + bim
    app13 = b"\xff\xed" + struct.pack(">H", len(app13_payload) + 2) + app13_payload

    return b"\xff\xd8" + app13 + b"\xff\xd9"


def make_jpeg_with_xmp(subjects: list[str]) -> bytes:
    """Build a minimal JPEG with an XMP APP1 segment containing dc:subject items."""
    li_items = "".join(f"<rdf:li>{s}</rdf:li>" for s in subjects)
    xmp_xml = f"""<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
    <rdf:Description xmlns:dc='http://purl.org/dc/elements/1.1/'>
      <dc:subject>
        <rdf:Bag>
          {li_items}
        </rdf:Bag>
      </dc:subject>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>""".encode("utf-8")

    xmp_header = b"http://ns.adobe.com/xap/1.0/\x00"
    payload = xmp_header + xmp_xml
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


def make_jpeg_with_both(iptc_keywords, xmp_subjects) -> bytes:
    """JPEG with both IPTC and XMP segments."""
    iptc_data = b""
    for kw in iptc_keywords:
        raw = kw.encode("utf-8")
        iptc_data += b"\x1c\x02\x19" + struct.pack(">H", len(raw)) + raw
    ps_header = b"Photoshop 3.0\x00"
    bim = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(iptc_data)) + iptc_data
    app13_payload = ps_header + bim
    app13 = b"\xff\xed" + struct.pack(">H", len(app13_payload) + 2) + app13_payload

    li_items = "".join(f"<rdf:li>{s}</rdf:li>" for s in xmp_subjects)
    xmp_xml = f"""<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
    <rdf:Description xmlns:dc='http://purl.org/dc/elements/1.1/'>
      <dc:subject>
        <rdf:Bag>
          {li_items}
        </rdf:Bag>
      </dc:subject>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>""".encode("utf-8")
    xmp_header = b"http://ns.adobe.com/xap/1.0/\x00"
    payload = xmp_header + xmp_xml
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    return b"\xff\xd8" + app13 + app1 + b"\xff\xd9"


def make_systemtags_propfind_xml(tags: list[dict]) -> bytes:
    """Build a WebDAV multistatus response for /remote.php/dav/systemtags PROPFIND."""
    responses = ""
    for tag in tags:
        responses += (
            f"  <d:response>"
            f"    <d:href>/remote.php/dav/systemtags/{tag['id']}</d:href>"
            f"    <d:propstat><d:prop>"
            f"      <oc:id>{tag['id']}</oc:id>"
            f"      <oc:display-name>{tag['name']}</oc:display-name>"
            f"    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
            f"  </d:response>"
        )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        f"{responses}"
        "</d:multistatus>"
    ).encode("utf-8")


def make_files_propfind_xml(login_name: str, entries: list[dict]) -> bytes:
    """
    Build a WebDAV multistatus for /remote.php/dav/files/{login_name}/... PROPFIND.
    Each entry: {"path": "/Photos/img.jpg", "is_collection": False}
    """
    responses = ""
    for entry in entries:
        from urllib.parse import quote
        href = f"/remote.php/dav/files/{quote(login_name)}{quote(entry['path'])}"
        if entry.get("is_collection"):
            resourcetype = "<d:resourcetype><d:collection/></d:resourcetype>"
        else:
            resourcetype = "<d:resourcetype/>"
        responses += (
            f"<d:response>"
            f"  <d:href>{href}</d:href>"
            f"  <d:propstat><d:prop>{resourcetype}</d:prop>"
            f"  <d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
            f"</d:response>"
        )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:">'
        f"{responses}"
        "</d:multistatus>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadIptcKeywords(unittest.TestCase):
    def _call_with_iptc(self, iptc_data):
        """Call read_iptc_keywords with mocked PIL returning the given IPTC dict."""
        with patch("tag_sync.Image.open"), \
             patch("tag_sync.IptcImagePlugin.getiptcinfo", return_value=iptc_data):
            return tag_sync.read_iptc_keywords(b"fake")

    def test_single_keyword(self):
        result = self._call_with_iptc({(2, 25): [b"beach"]})
        self.assertEqual(result, {"beach"})

    def test_multiple_keywords(self):
        result = self._call_with_iptc({(2, 25): [b"beach", b"vacation", b"summer"]})
        self.assertEqual(result, {"beach", "vacation", "summer"})

    def test_single_value_as_bytes(self):
        # PIL returns a plain bytes object (not a list) when there is only one keyword
        result = self._call_with_iptc({(2, 25): b"beach"})
        self.assertEqual(result, {"beach"})

    def test_no_iptc(self):
        result = self._call_with_iptc(None)
        self.assertEqual(result, set())

    def test_missing_keyword_field(self):
        result = self._call_with_iptc({(2, 120): [b"caption"]})
        self.assertEqual(result, set())

    def test_unicode_keyword(self):
        result = self._call_with_iptc({(2, 25): ["été".encode("utf-8"), "München".encode("utf-8")]})
        self.assertEqual(result, {"été", "München"})


class TestReadXmpSubjects(unittest.TestCase):
    def test_single_subject(self):
        data = make_jpeg_with_xmp(["nature"])
        self.assertEqual(tag_sync.read_xmp_subjects(data), {"nature"})

    def test_multiple_subjects(self):
        data = make_jpeg_with_xmp(["nature", "forest", "hiking"])
        self.assertEqual(tag_sync.read_xmp_subjects(data), {"nature", "forest", "hiking"})

    def test_no_xmp(self):
        self.assertEqual(tag_sync.read_xmp_subjects(b"\xff\xd8\xff\xd9"), set())

    def test_malformed_xmp_returns_empty(self):
        header = b"http://ns.adobe.com/xap/1.0/\x00"
        payload = header + b"<not valid xml <<<"
        app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        data = b"\xff\xd8" + app1 + b"\xff\xd9"
        self.assertEqual(tag_sync.read_xmp_subjects(data), set())


class TestExtractAllTags(unittest.TestCase):
    def test_union_and_dedup(self):
        with patch("tag_sync.read_iptc_keywords", return_value={"beach", "shared"}):
            data = make_jpeg_with_xmp(["vacation", "shared"])
            self.assertEqual(tag_sync.extract_all_tags(data), sorted({"beach", "shared", "vacation"}))

    def test_empty_image_returns_empty(self):
        self.assertEqual(tag_sync.extract_all_tags(b"\xff\xd8\xff\xd9"), [])

    def test_whitespace_stripped_and_empty_filtered(self):
        data = make_jpeg_with_xmp(["  trimmed  ", "", "  "])
        self.assertEqual(tag_sync.extract_all_tags(data), ["trimmed"])


class TestMakeSession(unittest.TestCase):
    def test_auth_and_headers(self):
        session = tag_sync.make_session("alice", "secret")
        self.assertEqual(session.auth, ("alice", "secret"))
        self.assertEqual(session.headers["OCS-APIREQUEST"], "true")
        self.assertEqual(session.headers["Accept"], "application/json")


class TestDownloadImage(unittest.TestCase):
    def test_constructs_correct_url_and_returns_content(self):
        session = MagicMock()
        session.get.return_value = MagicMock(content=b"IMAGEDATA")

        result = tag_sync.download_image(session, "https://cloud.example.com", "alice", "/Photos/img.jpg")

        session.get.assert_called_once_with(
            "https://cloud.example.com/remote.php/dav/files/alice/Photos/img.jpg"
        )
        self.assertEqual(result, b"IMAGEDATA")

    def test_url_encodes_spaces(self):
        session = MagicMock()
        session.get.return_value = MagicMock(content=b"data")
        tag_sync.download_image(session, "https://cloud.example.com", "alice", "/My Photos/img.jpg")
        self.assertIn("/My%20Photos/", session.get.call_args[0][0])


class TestListImageFiles(unittest.TestCase):
    def _mock_session(self, login_name, entries):
        session = MagicMock()
        resp = MagicMock()
        resp.content = make_files_propfind_xml(login_name, entries)
        session.request.return_value = resp
        return session

    def test_returns_image_paths(self):
        session = self._mock_session("alice", [
            {"path": "/Photos", "is_collection": True},
            {"path": "/Photos/img.jpg", "is_collection": False},
            {"path": "/Photos/photo.png", "is_collection": False},
        ])
        result = tag_sync.list_image_files(session, "https://nc.example.com", "alice", "/Photos")
        self.assertIn("/Photos/img.jpg", result)
        self.assertIn("/Photos/photo.png", result)

    def test_excludes_non_image_files(self):
        session = self._mock_session("alice", [
            {"path": "/Photos/readme.txt", "is_collection": False},
            {"path": "/Photos/img.jpg", "is_collection": False},
        ])
        result = tag_sync.list_image_files(session, "https://nc.example.com", "alice", "/Photos")
        self.assertEqual(result, ["/Photos/img.jpg"])

    def test_excludes_collections(self):
        session = self._mock_session("alice", [
            {"path": "/Photos/sub", "is_collection": True},
            {"path": "/Photos/img.jpg", "is_collection": False},
        ])
        result = tag_sync.list_image_files(session, "https://nc.example.com", "alice", "/Photos")
        self.assertNotIn("/Photos/sub", result)

    def test_case_insensitive_extension(self):
        # Build manually with uppercase extension
        login_name = "alice"
        href = f"/remote.php/dav/files/{login_name}/Photos/IMG.JPG"
        xml = (
            '<?xml version="1.0"?>'
            '<d:multistatus xmlns:d="DAV:">'
            f'<d:response><d:href>{href}</d:href>'
            '<d:propstat><d:prop><d:resourcetype/></d:prop>'
            '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            '</d:multistatus>'
        ).encode("utf-8")
        session = MagicMock()
        session.request.return_value = MagicMock(content=xml)
        result = tag_sync.list_image_files(session, "https://nc.example.com", "alice", "/Photos")
        self.assertEqual(result, ["/Photos/IMG.JPG"])

    def test_url_decoding_of_encoded_paths(self):
        login_name = "alice"
        # href contains %20 (URL-encoded space)
        href = f"/remote.php/dav/files/{login_name}/Photos/My%20Image.jpg"
        xml = (
            '<?xml version="1.0"?>'
            '<d:multistatus xmlns:d="DAV:">'
            f'<d:response><d:href>{href}</d:href>'
            '<d:propstat><d:prop><d:resourcetype/></d:prop>'
            '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            '</d:multistatus>'
        ).encode("utf-8")
        session = MagicMock()
        session.request.return_value = MagicMock(content=xml)
        result = tag_sync.list_image_files(session, "https://nc.example.com", "alice", "/Photos")
        # Path must be decoded — no %20 remaining
        self.assertEqual(result, ["/Photos/My Image.jpg"])


class TestListExistingTags(unittest.TestCase):
    def test_returns_name_to_id_mapping(self):
        session = MagicMock()
        session.request.return_value = MagicMock(
            content=make_systemtags_propfind_xml([
                {"id": 10, "name": "beach"},
                {"id": 20, "name": "vacation"},
            ])
        )
        result = tag_sync._list_existing_tags(session, "https://nc.example.com")
        self.assertEqual(result, {"beach": 10, "vacation": 20})

    def test_empty_response(self):
        session = MagicMock()
        session.request.return_value = MagicMock(
            content=make_systemtags_propfind_xml([])
        )
        result = tag_sync._list_existing_tags(session, "https://nc.example.com")
        self.assertEqual(result, {})

    def test_uses_correct_endpoint(self):
        session = MagicMock()
        session.request.return_value = MagicMock(
            content=make_systemtags_propfind_xml([])
        )
        tag_sync._list_existing_tags(session, "https://nc.example.com")
        call_args = session.request.call_args
        self.assertEqual(call_args[0][0], "PROPFIND")
        self.assertIn("/remote.php/dav/systemtags", call_args[0][1])


class TestResolveTagIds(unittest.TestCase):
    def _mock_session(self, existing_tags):
        """Return a session mock whose PROPFIND returns the given tags as XML."""
        session = MagicMock()
        session.request.return_value = MagicMock(
            content=make_systemtags_propfind_xml(existing_tags)
        )
        return session

    def test_all_tags_exist(self):
        session = self._mock_session([{"name": "beach", "id": 10}, {"name": "vacation", "id": 20}])
        result = tag_sync.resolve_tag_ids(session, "https://nc.example.com", ["beach", "vacation"])
        self.assertEqual(result, {"beach": 10, "vacation": 20})
        session.post.assert_not_called()

    def test_missing_tag_is_created(self):
        session = self._mock_session([{"name": "beach", "id": 10}])
        session.post.return_value = MagicMock(
            headers={"Content-Location": "/remote.php/dav/systemtags/99"}
        )
        result = tag_sync.resolve_tag_ids(session, "https://nc.example.com", ["beach", "newone"])
        self.assertEqual(result["beach"], 10)
        self.assertEqual(result["newone"], 99)
        session.post.assert_called_once()

    def test_empty_tag_list(self):
        session = self._mock_session([])
        result = tag_sync.resolve_tag_ids(session, "https://nc.example.com", [])
        self.assertEqual(result, {})


class TestCreateTag(unittest.TestCase):
    def test_returns_id_from_content_location(self):
        session = MagicMock()
        session.post.return_value = MagicMock(
            headers={"Content-Location": "/remote.php/dav/systemtags/42"}
        )
        tag_id = tag_sync.create_tag(session, "https://nc.example.com", "mytag")
        self.assertEqual(tag_id, 42)
        session.post.assert_called_once_with(
            "https://nc.example.com/remote.php/dav/systemtags",
            json={"name": "mytag", "userVisible": True, "userAssignable": True},
        )


class TestGetFileId(unittest.TestCase):
    def test_parses_fileid_from_propfind_response(self):
        xml_response = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:propstat>
      <d:prop>
        <oc:fileid>12345</oc:fileid>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
        session = MagicMock()
        session.request.return_value = MagicMock(content=xml_response)

        file_id = tag_sync.get_file_id(session, "https://nc.example.com", "alice", "/Photos/img.jpg")
        self.assertEqual(file_id, 12345)
        call_args = session.request.call_args
        self.assertEqual(call_args[0][0], "PROPFIND")
        self.assertIn("Depth", call_args[1]["headers"])

    def test_raises_on_missing_fileid(self):
        xml_response = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:propstat><d:prop/></d:propstat></d:response>
</d:multistatus>"""
        session = MagicMock()
        session.request.return_value = MagicMock(content=xml_response)
        with self.assertRaises(ValueError):
            tag_sync.get_file_id(session, "https://nc.example.com", "alice", "/Photos/img.jpg")


class TestApplyTags(unittest.TestCase):
    def test_sends_patch_with_json_body(self):
        session = MagicMock()
        session.patch.return_value = MagicMock()

        tag_sync.apply_tags(session, "https://nc.example.com", 12345, [10, 20, 30])

        session.patch.assert_called_once_with(
            "https://nc.example.com/apps/memories/api/tags/set/12345",
            json={"add": [10, 20, 30]},
        )
        session.patch.return_value.raise_for_status.assert_called_once()


class TestVerifyTags(unittest.TestCase):
    def test_prints_tags(self):
        session = MagicMock()
        session.get.return_value = MagicMock()
        session.get.return_value.json.return_value = {"tags": ["beach", "vacation"]}

        with patch("builtins.print") as mock_print:
            tag_sync.verify_tags(session, "https://nc.example.com", 12345)

        session.get.assert_called_once_with(
            "https://nc.example.com/apps/memories/api/image/info/12345",
            params={"tags": "true"},
        )
        mock_print.assert_called_once_with("Tags on file 12345: ['beach', 'vacation']")


if __name__ == "__main__":
    unittest.main(verbosity=2)
