"""A very small HTML tree for scraping, built on html.parser (standard library).

Supports the few queries the scrapers need: find elements by tag, id, class
or attribute, walk to a parent, and read the visible text.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []
        self.parent = parent

    # -- queries
    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find_all(self, tag: str | None = None, id: str | None = None, cls: str | None = None,
                 id_prefix: str | None = None, **attrs) -> list["Node"]:
        out = []
        for n in self.iter():
            if tag and n.tag != tag:
                continue
            if id is not None and n.attrs.get("id") != id:
                continue
            if id_prefix is not None and not (n.attrs.get("id") or "").startswith(id_prefix):
                continue
            if cls is not None and cls not in (n.attrs.get("class") or "").split():
                continue
            if any(n.attrs.get(k.replace("_", "-")) != v for k, v in attrs.items()):
                continue
            out.append(n)
        return out

    def find(self, *a, **k) -> "Node | None":
        r = self.find_all(*a, **k)
        return r[0] if r else None

    def closest(self, tag: str) -> "Node | None":
        p = self.parent
        while p is not None and p.tag != tag:
            p = p.parent
        return p

    def text(self, sep: str = " ") -> str:
        parts = []

        def walk(n):
            for c in n.children:
                if isinstance(c, Node):
                    if c.tag in ("script", "style"):
                        continue
                    if c.tag == "br":
                        parts.append("\n")
                    walk(c)
                else:
                    parts.append(c)
        walk(self)
        return re.sub(r"[ \t\r\f\v]+", " ", sep.join(parts)).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        p = self.cur
        while p is not None and p.tag != tag:
            p = p.parent
        if p is not None and p.parent is not None:
            self.cur = p.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root
