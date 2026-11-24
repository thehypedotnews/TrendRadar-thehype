# coding=utf-8
"""
Pull title, link and time out of an HTML page fetched with Web Unlocker.

Uses only the standard library (html.parser) to avoid a new dependency.

Two modes:
  article  - the page is one article: title from og:title/<title>, link from
             og:url/canonical, time from article:published_time, JSON-LD,
             itemprop=datePublished or the first <time datetime>.
  listing  - the page lists many articles (blog index, newsroom): every
             <article> block becomes one item; if there are none, links whose
             URL matches `link_pattern` are used.
"""

import json
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urljoin

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text_parts")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["Node"]):
        self.tag = tag
        self.attrs = attrs
        self.children: List["Node"] = []
        self.parent = parent
        self.text_parts: List[str] = []

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def find_all(self, *tags: str) -> List["Node"]:
        return [n for n in self.iter() if n.tag in tags]

    def find(self, *tags: str) -> Optional["Node"]:
        for n in self.iter():
            if n.tag in tags:
                return n
        return None

    def text(self) -> str:
        parts = []
        for n in self.iter():
            if n.tag in _SKIP_TEXT:
                continue
            parts.extend(n.text_parts)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.raw_scripts: List[str] = []
        self._in_ldjson = False

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag == "script" and node.attrs.get("type", "").lower() == "application/ld+json":
            self._in_ldjson = True
            self.raw_scripts.append("")
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_ldjson = False
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if self._in_ldjson:
            self.raw_scripts[-1] += data
        self.cur.text_parts.append(data)


def parse_html(html: str) -> _TreeBuilder:
    b = _TreeBuilder()
    try:
        b.feed(html or "")
        b.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    return b


def _meta(root: Node, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for m in root.find_all("meta"):
        key = (m.attrs.get("property") or m.attrs.get("name") or m.attrs.get("itemprop") or "").lower()
        if key in wanted and m.attrs.get("content"):
            return m.attrs["content"].strip()
    return ""


def _ld_values(scripts: List[str], key: str) -> List[str]:
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], str):
                found.append(obj[key])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for raw in scripts:
        try:
            walk(json.loads(raw))
        except ValueError:
            continue
    return found


def extract_article(html: str, page_url: str) -> Dict[str, str]:
    """Return {"title", "url", "time"} for a single-article page (values may be empty)."""
    b = parse_html(html)
    root = b.root
    title = _meta(root, "og:title", "twitter:title")
    if not title:
        t = root.find("title")
        title = t.text() if t else ""
    if not title:
        h1 = root.find("h1")
        title = h1.text() if h1 else ""

    url = _meta(root, "og:url")
    if not url:
        for link in root.find_all("link"):
            if "canonical" in link.attrs.get("rel", "").lower() and link.attrs.get("href"):
                url = link.attrs["href"]
                break
    url = urljoin(page_url, url) if url else page_url

    time_value = _meta(root, "article:published_time", "og:published_time", "datePublished",
                       "pubdate", "publish-date", "date", "dc.date")
    if not time_value:
        vals = _ld_values(b.raw_scripts, "datePublished")
        time_value = vals[0] if vals else ""
    if not time_value:
        t = root.find("time")
        if t is not None:
            time_value = t.attrs.get("datetime") or t.text()
    return {"title": title.strip(), "url": url, "time": time_value.strip()}


def extract_listing(html: str, page_url: str, link_pattern: str = "", max_items: int = 30) -> List[Dict[str, str]]:
    """Return [{"title", "url", "time"}] for a page that lists articles."""
    b = parse_html(html)
    root = b.root
    pattern = re.compile(link_pattern) if link_pattern else None
    items: List[Dict[str, str]] = []
    seen = set()

    def add(title: str, href: str, time_value: str):
        if not href or not title:
            return
        full = urljoin(page_url, href)
        if pattern and not pattern.search(full):
            return
        if full in seen or len(title) < 8:
            return
        seen.add(full)
        items.append({"title": title, "url": full, "time": time_value})

    for art in root.find_all("article"):
        heading = art.find("h1", "h2", "h3", "h4")
        link = None
        if heading is not None:
            link = heading.find("a")
            if link is None:
                # heading inside a link: <a><h2>…</h2></a>
                p = heading.parent
                while p is not None and p is not art and p.tag != "a":
                    p = p.parent
                link = p if p is not None and p.tag == "a" else None
        if link is None:
            link = art.find("a")
        if link is None:
            continue
        title = (heading.text() if heading is not None else "") or link.text()
        t = art.find("time")
        time_value = (t.attrs.get("datetime") or t.text()) if t is not None else ""
        add(title, link.attrs.get("href", ""), time_value)
        if len(items) >= max_items:
            return items

    if not items and pattern:
        for a in root.find_all("a"):
            title = a.text()
            t = a.find("time")
            add(title, a.attrs.get("href", ""), (t.attrs.get("datetime") if t is not None else "") or "")
            if len(items) >= max_items:
                break
    return items
