"""Every script or stylesheet loaded from another site carries an integrity hash.

Session ids (bearer credentials) and the invite code live in localStorage,
which any script on the page can read. Leaflet came from unpkg with no
Subresource Integrity, so a tampered CDN file would have run with that
access. With integrity= the browser refuses a file whose hash differs.
Hashes are Leaflet 1.9.4's published values, rechecked against unpkg 10-25.
"""
import glob
import re

import pytest

PAGES = ["static/index.html", *glob.glob("static/pages/*.html")]
TAG = re.compile(r"<(script|link)\b[^>]*>", re.IGNORECASE)


def external_tags(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for tag in TAG.finditer(html):
        text = tag.group(0)
        url = re.search(r'\b(?:src|href)="(https?://[^"]+)"', text)
        if url and (tag.group(1).lower() == "script" or 'rel="stylesheet"' in text):
            yield url.group(1), text


def test_the_app_page_loads_leaflet_from_a_cdn():
    """Teeth: the check below must actually be looking at something."""
    assert len(list(external_tags("static/index.html"))) == 2


@pytest.mark.parametrize("page", PAGES)
def test_external_scripts_and_styles_are_pinned_by_hash(page):
    for url, tag in external_tags(page):
        assert re.search(r'integrity="sha(256|384|512)-[A-Za-z0-9+/=]+"', tag), url
        assert 'crossorigin=""' in tag or 'crossorigin="anonymous"' in tag, url
