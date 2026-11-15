#!/usr/bin/env python3
"""Write the public site's index.json from the current source tree."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from urllib.parse import quote

from content import KINDS, UNKNOWN_DATE, article_data, article_paths
from prepare_site import MARKER

ROOT = Path(__file__).resolve().parent.parent


def thumbnail_for(root: Path, kind: str) -> str:
    """Choose one kind-level image, falling back to the site's profile photo."""
    directory = Path("data") / "sources" / kind
    for extension in ("jpg", "png", "gif"):
        image = directory / f"thumbnail.{extension}"
        if (root / image).is_file():
            return quote(image.as_posix(), safe="/")
    return "assets/junichiwatanuki.jpg"


def training_children(posts):
    """Group training posts by their generated JST date, newest first."""
    years = {}
    undated = []
    for post in posts:
        if post["type"] != "training":
            continue
        leaf = {"label": post["title"], "href": post["path"]}
        try:
            recorded = date.fromisoformat(post["date"])
        except (TypeError, ValueError):
            undated.append(leaf)
            continue
        months = years.setdefault(recorded.year, {})
        months.setdefault(recorded.month, []).append(leaf)
    result = [
        {"label": f"{year}年", "children": [
            {"label": f"{month}月", "children": months[month]}
            for month in sorted(months, reverse=True)
        ]}
        for year, months in sorted(years.items(), reverse=True)
    ]
    if undated:
        result.append({"label": "日付不明", "children": undated})
    return result


def build(root: Path, site: Path):
    root, site = root.resolve(), site.resolve()
    if not (site / MARKER).is_file():
        raise ValueError(f"Run prepare_site.py first: {site}")
    posts = []
    for kind in KINDS:
        thumbnail = thumbnail_for(root, kind)
        if not (site / thumbnail).is_file():
            raise ValueError(f"Selected thumbnail is missing from the published site: {thumbnail}")
        for source, output, iso_date in article_paths(root, kind):
            if not (site / output).is_file():
                raise ValueError(f"Generated article is missing; build {kind} first: {output}")
            title, excerpt, _ = article_data(source, kind, iso_date, root)
            posts.append({"title": title, "date": iso_date, "type": kind,
                          "path": quote(output.as_posix(), safe="/"), "excerpt": excerpt,
                          "thumbnail": thumbnail})
    posts.sort(key=lambda post: (post["date"] != UNKNOWN_DATE,
                                 post["date"] if post["date"] != UNKNOWN_DATE else "",
                                 post["path"]), reverse=True)
    def children(kind):
        return [{"label": post["title"], "href": post["path"]} for post in posts if post["type"] == kind]
    index = {
        "site": {"title": "Monolog-jwat", "description": "走ること、つくること、日々の小さな発見を綴るブログ。"},
        "menu": [
            {"label": "ホーム", "href": "index.html"},
            {"label": "ブログ", "href": "index.html#blog", "children": children("blog")},
            {"label": "トレーニング", "href": "index.html#training", "children": training_children(posts)},
            {"label": "サンプル", "href": "index.html#sample-posts", "children": children("sample")},
        ],
        "posts": posts,
    }
    (site / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(posts)} posts to {site / 'index.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, required=True)
    args = parser.parse_args()
    build(ROOT, args.site_dir)


if __name__ == "__main__":
    main()
