"""The app page can be used with a screen reader.

The page named no language, so readers guessed at pronunciation; the
request box and the start address had only placeholders, which aren't
reliably read as names; and the banner -- where a finished request's
summary and every error appear -- changed silently, so a rider who
couldn't see it never heard that the route was ready or what went wrong.
"""
from html.parser import HTMLParser
import os

import pytest

INDEX = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, dict[str, str | None]] = {}
        self.tags: dict[str, str] = {}
        self.labelled: set[str] = set()
        self.lang: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "label" and a.get("for"):
            self.labelled.add(a["for"])
        if a.get("id"):
            self.elements[a["id"]] = a
            self.tags[a["id"]] = tag


@pytest.fixture(scope="module")
def page():
    p = Page()
    with open(INDEX, encoding="utf-8") as f:
        p.feed(f.read())
    return p


def test_the_page_names_its_language(page):
    assert page.lang == "en"


def test_every_field_a_rider_fills_in_has_a_name(page):
    fields = [i for i, tag in page.tags.items()
              if tag in ("input", "textarea", "select") and "hidden" not in page.elements[i]]
    assert {"ask", "start", "intent"} <= set(fields)
    unnamed = [i for i in fields
               if i not in page.labelled and not page.elements[i].get("aria-label")]
    assert not unnamed


def test_the_banner_is_announced_when_it_changes(page):
    banner = page.elements["banner"]
    assert banner.get("role") == "status" or banner.get("aria-live") in ("polite", "assertive")
