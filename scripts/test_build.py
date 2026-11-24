#!/usr/bin/env python3
"""Integration tests using disposable fixture templates, never project templates."""
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from html.parser import HTMLParser

from garmin_fit_sdk import Decoder, Encoder, Profile, Stream

from build_content import build as build_content
from build_index import build as build_index
from content import KINDS
from prepare_site import prepare


def fit_bytes(created, *, session=None, laps=None, include_created=True):
    """Make synthetic FIT messages with the official encoder, never real data.

    SESSION starts seven minutes after FILE_ID.time_created deliberately: the
    article filename must be derived from FILE_ID, not SESSION or the filename.
    Supplying dictionaries lets missing-value cases omit fields altogether.
    """
    if created.utcoffset() is None:
        raise ValueError("The synthetic FIT timestamp must be timezone-aware")
    start = created + timedelta(minutes=7)
    file_id = {"type": "activity", "manufacturer": "development", "product": 1}
    if include_created:
        file_id["time_created"] = created
    if session is None:
        session = {
            "sport": "running", "start_time": start, "timestamp": start,
            "total_elapsed_time": 1872, "total_timer_time": 1800,
            "total_distance": 5000, "total_calories": 347,
            "avg_heart_rate": 145, "max_heart_rate": 173,
        }
    if laps is None:
        laps = [
            {"message_index": 0, "intensity": "active", "start_time": start,
             "total_distance": 1000, "total_timer_time": 300, "avg_heart_rate": 130},
            {"message_index": 1, "intensity": "rest", "start_time": start,
             "total_distance": 100, "total_timer_time": 100, "avg_heart_rate": 131},
            {"message_index": 2, "intensity": "active", "start_time": start,
             "total_distance": 2000, "total_timer_time": 600, "avg_heart_rate": 132},
        ]
    encoder = Encoder()
    numbers = Profile["mesg_num"]
    encoder.on_mesg(numbers["FILE_ID"], file_id)
    encoder.on_mesg(numbers["SESSION"], session)
    for lap in laps:
        encoder.on_mesg(numbers["LAP"], lap)
    return bytes(encoder.close())


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, val in attrs:
            if key in ("href", "src") and val:
                self.urls.append(val)


class Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.site = Path(self.tmp.name) / "site"
        self.root.mkdir()
        self.write("index.html", '<html><body>home<div id="sample-posts"></div></body></html>')
        self.write("index.css", "/* root */")
        self.write("js/common.js", "// common")
        self.write("assets/junichiwatanuki.jpg", "default image")
        self.write("images/outside.svg", "outside")
        self.write("data/sources/shared/photo.svg", "shared")
        for kind in ("blog", "training", "sample"):
            self.write(f"data/sources/{kind}/index.html", f'''<!doctype html><html><head>
<title>__ARTICLE_TITLE__ | 海と森</title><link rel="stylesheet" href="./{kind}.css">
<script src="{kind}.js"></script></head><body><a href="../../../index.html">home</a>
<img src="../../../images/outside.svg"><h1>__ARTICLE_TITLE__</h1><time>__ARTICLE_DATE__</time>
<!-- ARTICLE_CONTENT --></body></html>''')
            self.write(f"data/sources/{kind}/{kind}.css", "/* css */")
            self.write(f"data/sources/{kind}/{kind}.js", "// js")
        self.write("data/sources/blog/nest/deep/20250124.md", '''<!-- title: 物語 <春> & 海 -->

春が来ました。

| A | B |
| --- | --- |
| 一 | 二 |

- [x] 終了

~~古い~~ <kbd>新しい</kbd>

![共有](../../../shared/photo.svg)
![外部](../../../../../images/outside.svg)
![絶対](/images/outside.svg)
[練習](../../../training/20250101.fit#details)
''')
        self.write("data/sources/blog/20250105.md", "# 最初の日\n\n本文です。\n")
        self.write_fit("data/sources/training/20250101.fit", datetime(2025, 1, 1, 0, 1, 2, tzinfo=timezone.utc))
        self.write_fit("data/sources/training/20250312.FIT", datetime(2025, 3, 12, 1, 2, 3, tzinfo=timezone.utc))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_fit(self, rel, created, **kwargs):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fit_bytes(created, **kwargs))
        return path

    def run_build(self):
        prepare(self.root, self.site)
        for kind in ("blog", "training", "sample"):
            build_content(self.root, self.site, kind)
        build_index(self.root, self.site)

    def test_social_profile_is_copied_and_shared_footer_is_built(self):
        profile = '{"social":{"github":"https://github.com/example"}}\n'
        footer = ('<footer class="main-footer"><section data-social-section hidden>'
                  '<nav data-social-links aria-label="SNSアカウント"></nav>'
                  '</section></footer>')
        self.write("assets/profile.json", profile)
        self.write("index.html", f'<html><body>home{footer}</body></html>')
        for kind in ("blog", "training", "sample"):
            path = self.root / "data" / "sources" / kind / "index.html"
            template = path.read_text(encoding="utf-8")
            path.write_text(template.replace('</body>', '<footer class="main-footer">old</footer></body>'),
                            encoding="utf-8")
        self.write("data/sources/sample/20250102.csv", "name,value\na,1\n")
        self.run_build()
        self.assertEqual((self.site / "assets/profile.json").read_text(encoding="utf-8"), profile)
        pages = list((self.site / "data/contents").rglob("*.html"))
        self.assertGreaterEqual(len(pages), 5)
        self.assertEqual({p.relative_to(self.site / "data/contents").parts[0] for p in pages},
                         {"blog", "training", "sample"})
        for page in pages:
            rendered = page.read_text(encoding="utf-8")
            self.assertEqual(rendered.count('data-social-section'), 1)
            self.assertEqual(rendered.count('data-social-links'), 1)
            self.assertNotIn('<footer class="main-footer">old</footer>', rendered)

    def test_full_build_paths_contents_and_clean_rebuild(self):
        self.write("package.json", "{}")
        self.write("package-lock.json", "{}")
        self.write(".gitignore", "node_modules/\n")
        self.run_build()
        for name in ("package.json", "package-lock.json", ".gitignore"):
            self.assertFalse((self.site / name).exists())
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([p["date"] for p in index["posts"]], ["2025-03-12", "2025-01-24", "2025-01-05", "2025-01-01"])
        self.assertEqual(index["posts"][1]["title"], "物語 <春> & 海")
        self.assertEqual(index["menu"][1]["children"][0]["href"], "data/contents/blog/nest/deep/2025-01-24.html")
        nested = self.site / index["posts"][1]["path"]
        page = nested.read_text(encoding="utf-8")
        self.assertIn("物語 &lt;春&gt; &amp; 海", page)
        self.assertIn('<link rel="stylesheet" href="../../blog.css">', page)
        self.assertIn('<script src="../../blog.js">', page)
        self.assertIn('href="../../../../../index.html"', page)
        self.assertIn('<table>', page)
        self.assertIn('type="checkbox" checked="" disabled=""', page)
        self.assertIn('<del>古い</del>', page)
        self.assertIn('<kbd>新しい</kbd>', page)
        self.assertNotIn('ARTICLE_CONTENT', page)
        self.assertFalse((self.site / "data/contents/blog/index.html").exists())
        self.assertTrue((self.site / "data/contents/shared/photo.svg").is_file())
        self.assertTrue((self.site / "data/sources/shared/photo.svg").is_file())
        links = Links()
        links.feed(page)
        for url in links.urls:
            parsed = urlsplit(url)
            if not parsed.scheme and parsed.path:
                self.assertTrue((nested.parent / unquote(parsed.path)).is_file(), (url, nested))
        fit_link = next(url for url in links.urls if urlsplit(url).fragment == "details")
        self.assertEqual((nested.parent / unquote(urlsplit(fit_link).path)).resolve(),
                         (self.site / "data/contents/training/2025-01-01_09-01-02.html").resolve())
        training = self.site / "data/contents/training/2025-01-01_09-01-02.html"
        self.assertTrue(training.is_file())
        self.assertTrue((self.site / "data/contents/training/20250101.json").is_file())
        self.assertEqual(next(p for p in index["posts"] if p["path"].endswith("2025-01-01_09-01-02.html"))["type"], "training")
        (self.root / "data/sources/blog/20250105.md").unlink()
        self.run_build()
        self.assertFalse((self.site / "data/contents/blog/2025-01-05.html").exists())
        self.assertEqual(len(json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]), 3)
        (self.root / "data/sources/training/20250101.fit").unlink()
        self.run_build()
        self.assertFalse(training.exists())
        self.assertFalse((self.site / "data/contents/training/20250101.json").exists())
        self.assertEqual(len(json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]), 2)
        self.assertFalse((self.root / "index.json").exists())

    def test_blog_source_rename_updates_generated_page_and_index(self):
        old_source = self.root / "data/sources/blog/e2d3-recollection.md"
        self.write("data/sources/blog/e2d3-recollection.md", "# なつかしいE2D3\n\n本文です。\n")
        old_path = "data/contents/blog/e2d3-recollection.html"
        new_path = "data/contents/blog/2026-11-24.html"
        self.run_build()
        before = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        self.assertIn(old_path, [post["path"] for post in before["posts"]])
        self.assertTrue((self.site / old_path).is_file())

        old_source.rename(old_source.with_name("20261124.md"))
        self.run_build()
        after = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        posts = [post["path"] for post in after["posts"]]
        blog = next(item for item in after["menu"] if item["label"] == "ブログ")
        links = [item["href"] for item in blog["children"]]
        self.assertIn(new_path, posts)
        self.assertIn(new_path, links)
        self.assertNotIn(old_path, posts)
        self.assertNotIn(old_path, links)
        self.assertTrue((self.site / new_path).is_file())
        self.assertFalse((self.site / old_path).exists())

    def test_training_titles_and_year_month_navigation(self):
        self.write_fit("data/sources/training/nested/swim.fit",
                       datetime(2024, 12, 31, 16, 0, tzinfo=timezone.utc),
                       session={"sport": "swimming"}, laps=[])
        self.write_fit("data/sources/training/new-year.fit",
                       datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc),
                       session={"sport": "running"}, laps=[])
        self.run_build()
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        years = index["menu"][2]["children"]
        self.assertEqual([year["label"] for year in years], ["2026年", "2025年"])
        months_2025 = years[1]["children"]
        self.assertEqual([month["label"] for month in months_2025], ["3月", "1月"])
        swim = next(post for post in index["posts"] if "/nested/" in post["path"])
        self.assertEqual(swim["date"], "2025-01-01")  # JST crosses the year boundary.
        self.assertEqual(swim["title"], "Swim 2025-01-01 01:00:00 JST")
        self.assertIn({"label": swim["title"], "href": swim["path"]}, months_2025[1]["children"])
        jog = next(post for post in index["posts"] if post["date"] == "2026-02-01")
        self.assertEqual(jog["title"], "Jog 2026-02-01 09:00:00 JST")
        self.assertEqual(years[0]["children"][0]["label"], "2月")
        self.assertIn({"label": jog["title"], "href": jog["path"]}, years[0]["children"][0]["children"])
        for post in (swim, jog):
            page = (self.site / unquote(post["path"])).read_text(encoding="utf-8")
            self.assertIn(f'<title>{post["title"]} | 海と森</title>', page)
            self.assertIn(f'<h1>{post["title"]}</h1>', page)
        before = (self.site / "index.json").read_bytes()
        self.run_build()
        self.assertEqual((self.site / "index.json").read_bytes(), before)

    def test_training_sport_title_for_other_and_missing_sports(self):
        from fit_training import _sport_title

        self.assertEqual(_sport_title([{"sport": "running"}]), "Jog")
        self.assertEqual(_sport_title([{"sport": "swimming"}]), "Swim")
        self.assertEqual(_sport_title([{"sport": "cycling"}]), "cycling")
        self.assertEqual(_sport_title([]), "Activity")
        self.assertEqual(_sport_title([{}, {"sport": "unknown"}]), "Activity")
        self.assertEqual(_sport_title([{"sport": "running"}, {"sport": "swimming"},
                                       {"sport": "running"}]), "Jog / Swim")
        self.assertEqual(_sport_title([{"sport": "running"}, {}]), "Jog / Activity")

    def test_dated_collisions_and_ragged_csv_fail(self):
        self.write("data/sources/blog/2025-01-05.md", "# 重複\n")
        prepare(self.root, self.site)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            build_content(self.root, self.site, "blog")
        (self.root / "data/sources/blog/2025-01-05.md").unlink()
        self.write("data/sources/sample/20250312.csv", "種目,メモ\nラン\n")
        with self.assertRaisesRegex(ValueError, "expected 2"):
            build_content(self.root, self.site, "sample")

    def test_headings_footnotes_gfm_and_sanitization(self):
        self.write("data/sources/blog/20250105.md", '''# Hello, *World*!

## Hello, **World**!
## Hello, World!
## 日本語の見出し

[日本語](#日本語の見出し) 参照[^注] と再参照[^注]。 https://example.com

[^注]: 注釈 **強調**。

| 列 | 値 |
| --- | --- |
| A | B |

- [x] 完了
- [ ] 未完

`href="20250105.md" id="fn-%E6%B3%A8"`

<a title="href='20250105.md'" href="20250105.md">raw link</a>

<script>alert('bad')</script><img src="javascript:alert(1)" onerror="alert(2)">
<a id="top" href="javascript:alert(1)" onclick="alert(3)">unsafe</a>
''')
        self.run_build()
        page = (self.site / "data/contents/blog/2025-01-05.html").read_text(encoding="utf-8")
        parsed = Tags()
        parsed.feed(page)
        headings = [attrs.get("id") for tag, attrs in parsed.tags if tag in ("h1", "h2") and "id" in attrs]
        self.assertEqual(headings, ["hello-world", "hello-world-1", "hello-world-2", "日本語の見出し"])
        ids = {attrs["id"] for _, attrs in parsed.tags if "id" in attrs}
        self.assertIn("fn-注", ids)
        self.assertIn("fnref-注", ids)
        self.assertIn("fnref-注-2", ids)
        footnote_hrefs = [attrs["href"] for tag, attrs in parsed.tags
                          if tag == "a" and attrs.get("href", "").startswith(("#fn-", "#fnref-"))]
        self.assertGreaterEqual(len(footnote_hrefs), 4)
        for href in footnote_hrefs:
            self.assertIn(unquote(href[1:]), ids)
        self.assertIn('<table>', page)
        self.assertIn('type="checkbox" checked="" disabled=""', page)
        self.assertIn('type="checkbox" disabled=""', page)
        self.assertIn('href="https://example.com"', page)
        self.assertIn('<strong>強調</strong>', page)
        self.assertIn('<code>href="20250105.md" id="fn-%E6%B3%A8"</code>', page)
        self.assertIn('title="href=\'20250105.md\'"', page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("onerror", page)
        self.assertNotIn("onclick", page)
        self.assertIn("&lt;script&gt;alert('bad')&lt;/script&gt;", page)

    def test_undated_encoded_paths_and_relative_links(self):
        folder = "特殊 #?"
        name = "冒険 #?%"
        photo = "画像 #?%.svg"
        self.write(f"data/sources/blog/{folder}/{name}.MD", "本文だけです。\n\n[戻る](../20250105.md#links)\n")
        self.write(f"data/sources/blog/{folder}/{photo}", "media")
        self.write("images/root #?.svg", "root media")
        self.write("data/sources/blog/20250230.md", "日付として存在しません。\n")
        target = quote(f"{folder}/{name}.MD", safe="/")
        image = quote(f"{folder}/{photo}", safe="/")
        root_image = quote("/images/root #?.svg", safe="/")
        self.write("data/sources/blog/20250105.md", f'''# リンク元

[記事](<{target}?view=1&mode=full#日本語>)
![画像](<{image}>)
![ルート画像](<{root_image}>)
''')
        self.run_build()
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        dated = [p for p in index["posts"] if p["date"] != "日付不明"]
        undated = [p for p in index["posts"] if p["date"] == "日付不明"]
        self.assertEqual(index["posts"], dated + undated)
        self.assertEqual([p["path"] for p in undated], sorted((p["path"] for p in undated), reverse=True))
        self.assertEqual(len(undated), 2)
        self.assertIn("20250230", [p["title"] for p in undated])
        post = next(p for p in undated if p["title"] == name)
        self.assertEqual(post["path"], quote(f"data/contents/blog/{folder}/{name}.html", safe="/"))
        for p in index["posts"]:
            self.assertTrue((self.site / unquote(urlsplit(p["path"]).path)).is_file(), p["path"])
        self.assertIn(post["path"], [child["href"] for child in index["menu"][1]["children"]])
        undated_path = self.site / unquote(post["path"])
        undated_page = undated_path.read_text(encoding="utf-8")
        self.assertIn('<time>日付不明</time>', undated_page)
        undated_links = Links()
        undated_links.feed(undated_page)
        back = next(url for url in undated_links.urls if urlsplit(url).fragment == "links")
        self.assertEqual((undated_path.parent / unquote(urlsplit(back).path)).resolve(),
                         (self.site / "data/contents/blog/2025-01-05.html").resolve())
        page_path = self.site / "data/contents/blog/2025-01-05.html"
        parser = Links()
        parser.feed(page_path.read_text(encoding="utf-8"))
        article_url = next(url for url in parser.urls if urlsplit(url).query == "view=1&mode=full")
        self.assertEqual(urlsplit(article_url).fragment, "%E6%97%A5%E6%9C%AC%E8%AA%9E")
        self.assertEqual((page_path.parent / unquote(urlsplit(article_url).path)).resolve(), (self.site / unquote(post["path"])).resolve())
        media_url = next(url for url in parser.urls if url.endswith(".svg") and "%25" in url)
        self.assertEqual((page_path.parent / unquote(urlsplit(media_url).path)).read_text(encoding="utf-8"), "media")
        root_url = next(url for url in parser.urls if "root%20%23%3F.svg" in url)
        self.assertEqual((page_path.parent / unquote(urlsplit(root_url).path)).read_text(encoding="utf-8"), "root media")
        index_before = (self.site / "index.json").read_bytes()
        page_before = undated_path.read_bytes()
        self.run_build()
        self.assertEqual((self.site / "index.json").read_bytes(), index_before)
        self.assertEqual(undated_path.read_bytes(), page_before)

    def test_duplicate_undated_names_unsafe_sources_and_ids_fail(self):
        self.write("data/sources/blog/同名.md", "body")
        self.write("data/sources/blog/同名.MD", "body")
        prepare(self.root, self.site)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            build_content(self.root, self.site, "blog")
        (self.root / "data/sources/blog/同名.MD").unlink()
        unsafe = self.root / "data/sources/blog/bad\\name.md"
        unsafe.write_text("body", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsafe character"):
            build_content(self.root, self.site, "blog")
        unsafe.unlink()
        link = self.root / "data/sources/blog/external.md"
        link.symlink_to(self.root / "index.html")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            build_content(self.root, self.site, "blog")
        link.unlink()
        shared_link = self.root / "data/sources/shared/external.svg"
        shared_link.symlink_to(self.root / "index.html")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            build_content(self.root, self.site, "blog")
        shared_link.unlink()
        self.write("data/sources/blog/同名.html", "asset")
        with self.assertRaisesRegex(ValueError, "asset would be overwritten"):
            build_content(self.root, self.site, "blog")
        (self.root / "data/sources/blog/同名.html").unlink()
        # cmark's repeated reference can otherwise collide with another label.
        self.write("data/sources/blog/20250105.md", "a[^x] b[^x] c[^x-2]\n\n[^x]: first\n[^x-2]: second\n")
        with self.assertRaisesRegex(ValueError, "Duplicate .* ID"):
            build_content(self.root, self.site, "blog")
        template = self.root / "data/sources/blog/index.html"
        template.write_text(template.read_text(encoding="utf-8").replace("<body>", '<body id="top">'), encoding="utf-8")
        self.write("data/sources/blog/20250105.md", "# top\n")
        with self.assertRaisesRegex(ValueError, "Duplicate .* ID"):
            build_content(self.root, self.site, "blog")

    def test_ten_dated_sources_keep_existing_outputs(self):
        expected = {
            "blog": ("2025-01-05", "2025-01-12", "2025-01-24", "2025-01-30", "2025-03-10"),
            "training": ("2025-01-01", "2025-01-10", "2025-01-23", "2025-01-31", "2025-03-12"),
        }
        for kind, extension in (("blog", ".md"), ("training", ".fit")):
            for source in (self.root / "data/sources" / kind).rglob("*"):
                if source.is_file() and source.suffix.lower() == extension:
                    source.unlink()
        for day in expected["blog"]:
            self.write(f"data/sources/blog/{day.replace('-', '')}.md", "# ブログ\n\n本文\n")
        for day in expected["training"]:
            created = datetime.fromisoformat(day).replace(hour=0, minute=1, second=2, tzinfo=timezone.utc)
            self.write_fit(f"data/sources/training/activity-{day}.fit", created)
        self.run_build()
        posts = json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]
        self.assertEqual(len(posts), 10)
        for kind, dates in expected.items():
            for day in dates:
                suffix = "_09-01-02" if kind == "training" else ""
                path = f"data/contents/{kind}/{day}{suffix}.html"
                post = next(p for p in posts if p["path"] == path)
                self.assertEqual(post["date"], day)
                self.assertEqual(post["type"], kind)
                self.assertTrue((self.site / path).is_file())

    def test_sample_csv_index_menu_nested_assets_and_cross_kind_link(self):
        csv = '種目,メモ\nラン,"<script>alert(1)</script> & 気分"\n'
        self.write("data/sources/sample/nest/deep/20250101.CSV", csv)
        self.write("data/sources/blog/20250105.md", "# 練習\n\n[サンプル](../sample/nest/deep/20250101.CSV)\n")
        self.run_build()
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        sample = next(post for post in index["posts"] if post["type"] == "sample")
        self.assertEqual(sample["path"], "data/contents/sample/nest/deep/2025-01-01.html")
        self.assertEqual(sample["title"], "2025年1月1日のサンプル")
        self.assertEqual(sample["excerpt"], "サンプルの記録：ラン")
        menu = next(item for item in index["menu"] if item["label"] == "サンプル")
        self.assertEqual(menu["href"], "index.html#sample-posts")
        self.assertEqual(menu["children"], [{"label": sample["title"], "href": sample["path"]}])
        self.assertIn('id="sample-posts"', (self.site / "index.html").read_text(encoding="utf-8"))
        page_path = self.site / sample["path"]
        page = page_path.read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="../../sample.css">', page)
        self.assertIn('<script src="../../sample.js">', page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; 気分", page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('<div class="main-training-table-wrap">', page)
        self.assertIn('<td>ラン</td>', page)
        self.assertTrue((self.site / "data/contents/sample/sample.css").is_file())
        self.assertTrue((self.site / "data/contents/sample/sample.js").is_file())
        blog = (self.site / "data/contents/blog/2025-01-05.html").read_text(encoding="utf-8")
        self.assertIn('href="../sample/nest/deep/2025-01-01.html"', blog)
        self.assertFalse((self.site / "data/contents/sample/nest/deep/20250101.CSV").exists())

    def test_sample_invalid_csv_and_kind_rebuild_isolation(self):
        self.write("data/sources/sample/20250101.csv", "種目,メモ\nラン,朝\n")
        self.run_build()
        training_path = self.site / "data/contents/training/2025-01-01_09-01-02.html"
        before = training_path.read_bytes()
        self.write("data/sources/sample/20250101.csv", "種目,メモ\nラン\n")
        with self.assertRaisesRegex(ValueError, "expected 2"):
            build_content(self.root, self.site, "sample")
        self.assertEqual(training_path.read_bytes(), before)
        self.write("data/sources/sample/20250101.csv", "種目,メモ\nラン,朝\n")
        build_content(self.root, self.site, "sample")
        self.assertEqual(training_path.read_bytes(), before)
        self.assertTrue((self.site / "data/contents/sample/2025-01-01.html").is_file())

    def test_official_fit_fixture_and_decoded_json_summary_and_active_laps(self):
        self.assertEqual(KINDS["training"], ".fit")
        source = self.root / "data/sources/training/20250101.fit"
        data = source.read_bytes()
        self.assertEqual(data[8:12], b".FIT")
        with self.assertRaises(UnicodeDecodeError):
            data.decode("utf-8")
        messages, errors = Decoder(Stream.from_byte_array(data)).read()
        self.assertEqual(errors, [])
        self.assertEqual(messages["file_id_mesgs"][0]["time_created"],
                         datetime(2025, 1, 1, 0, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(messages["session_mesgs"][0]["total_distance"], 5000)
        self.assertEqual([lap["intensity"] for lap in messages["lap_mesgs"]],
                         ["active", "rest", "active"])

        # Observe the SDK decoder during the actual build, not just the fixture
        # round trip: an alternate ad-hoc parser must not satisfy this test.
        sdk_read = Decoder.read
        with patch.object(Decoder, "read", autospec=True, side_effect=sdk_read) as read_spy:
            self.run_build()
        self.assertGreater(read_spy.call_count, 0)
        contents = self.site / "data/contents/training"
        decoded = json.loads((contents / "20250101.json").read_text(encoding="utf-8"))
        self.assertEqual(datetime.fromisoformat(decoded["file_id_mesgs"][0]["time_created"].replace("Z", "+00:00")),
                         datetime(2025, 1, 1, 0, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(decoded["session_mesgs"][0]["total_distance"], 5000)
        self.assertEqual(decoded["session_mesgs"][0]["avg_heart_rate"], 145)
        self.assertEqual([lap["intensity"] for lap in decoded["lap_mesgs"]],
                         ["active", "rest", "active"])
        later_json = json.loads((contents / "20250312.json").read_text(encoding="utf-8"))
        self.assertEqual(datetime.fromisoformat(later_json["file_id_mesgs"][0]["time_created"].replace("Z", "+00:00")),
                         datetime(2025, 3, 12, 1, 2, 3, tzinfo=timezone.utc))
        self.assertEqual(later_json["session_mesgs"][0]["total_distance"], 5000)
        later_page = (contents / "2025-03-12_10-02-03.html").read_text(encoding="utf-8")
        self.assertIn("サマリー", later_page)
        self.assertIn("5 km", later_page)
        self.assertEqual(later_page.split("<tbody>", 1)[1].split("</tbody>", 1)[0].count("<tr>"), 2)
        page = (contents / "2025-01-01_09-01-02.html").read_text(encoding="utf-8")
        self.assertIn("サマリー", page)
        self.assertIn("5 km", page)
        self.assertIn("145 bpm", page)
        self.assertIn("347 kcal", page)
        self.assertIn("ラップ", page)
        table = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertEqual(table.count("<tr>"), 2)
        self.assertIn("1 km", table)
        self.assertIn("2 km", table)
        self.assertNotIn("0.1 km", table)
        self.assertIn("130 bpm", table)
        self.assertIn("132 bpm", table)
        links = Links()
        links.feed(page)
        github = [url for url in links.urls if urlsplit(url).netloc == "github.com" and "/blob/" in urlsplit(url).path]
        self.assertTrue(any(unquote(urlsplit(url).path).endswith("/data/sources/training/20250101.fit") for url in github))
        self.assertTrue(any(unquote(urlsplit(url).path).endswith("/data/contents/training/20250101.json") for url in github))

    def test_nested_fit_with_encoded_filenames_cross_link_and_jst_date_boundary(self):
        folder = "練習 #?%"
        filename = '朝 <script> & " #?%走.FIT'
        relative_fit = f"{folder}/深い/{filename}"
        created = datetime(2025, 1, 1, 15, 30, 45, tzinfo=timezone.utc)
        self.write_fit(f"data/sources/training/{relative_fit}", created)
        source_url = quote(f"../training/{relative_fit}", safe="/")
        self.write("data/sources/blog/20250105.md", f"# 練習\n\n[走る](<{source_url}?view=1#details>)\n")
        self.run_build()

        relative_html = f"data/contents/training/{folder}/深い/2025-01-02_00-30-45.html"
        relative_json = f"data/contents/training/{folder}/深い/{Path(filename).stem}.json"
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        post = next(p for p in index["posts"] if p["path"] == quote(relative_html, safe="/"))
        self.assertEqual((post["date"], post["type"]), ("2025-01-02", "training"))
        self.assertTrue((self.site / relative_html).is_file())
        self.assertTrue((self.site / relative_json).is_file())
        self.assertEqual(json.loads((self.site / relative_json).read_text(encoding="utf-8"))["session_mesgs"][0]["total_distance"], 5000)
        year = next(node for node in index["menu"][2]["children"] if node["label"] == "2025年")
        month = next(node for node in year["children"] if node["label"] == "1月")
        self.assertIn(post["path"], [child["href"] for child in month["children"]])

        blog_path = self.site / "data/contents/blog/2025-01-05.html"
        blog_links = Links()
        blog_links.feed(blog_path.read_text(encoding="utf-8"))
        cross_link = next(url for url in blog_links.urls if urlsplit(url).fragment == "details")
        self.assertEqual(urlsplit(cross_link).query, "view=1")
        self.assertEqual((blog_path.parent / unquote(urlsplit(cross_link).path)).resolve(), (self.site / relative_html).resolve())
        self.assertIn("%23", cross_link)
        self.assertIn("%3F", cross_link)
        self.assertIn("%25", cross_link)

        html_path = self.site / relative_html
        page = html_path.read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="../../training.css">', page)
        self.assertIn("2025-01-02 00:30:45", page)
        self.assertIn("&lt;script&gt; &amp; &quot;", page)
        self.assertNotIn('<script> & "', page)
        links = Links()
        links.feed(page)
        github = [url for url in links.urls if urlsplit(url).netloc == "github.com" and "/blob/" in urlsplit(url).path]
        for expected in (f"data/sources/training/{relative_fit}", relative_json):
            url = next(url for url in github if unquote(urlsplit(url).path).endswith("/" + expected))
            self.assertEqual(urlsplit(url).fragment, "")
            self.assertEqual(urlsplit(url).query, "")
            self.assertIn("%20", url)
            self.assertIn("%23", url)
            self.assertIn("%3F", url)
            self.assertIn("%25", url)

    def test_invalid_and_truncated_fit_are_rejected(self):
        source = self.root / "data/sources/training/bad.fit"
        valid = (self.root / "data/sources/training/20250101.fit").read_bytes()
        prepare(self.root, self.site)
        for invalid in (b"", b"not a FIT file", valid[:-5]):
            with self.subTest(data=repr(invalid[:16])):
                source.write_bytes(invalid)
                try:
                    with self.assertRaises(ValueError):
                        build_content(self.root, self.site, "training")
                finally:
                    source.unlink()

    def test_tampered_fit_crc_rejected_and_generated_json_is_not_authoritative(self):
        self.run_build()
        json_path = self.site / "data/contents/training/20250101.json"
        expected = json.loads(json_path.read_text(encoding="utf-8"))
        json_path.write_text('{"tampered": true}', encoding="utf-8")
        build_content(self.root, self.site, "training")
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), expected)
        index_before = (self.site / "index.json").read_bytes()
        source = self.root / "data/sources/training/20250101.fit"
        corrupted = bytearray(source.read_bytes())
        corrupted[-1] ^= 1
        source.write_bytes(corrupted)
        self.assertTrue(Decoder(Stream.from_byte_array(corrupted)).is_fit())
        self.assertFalse(Decoder(Stream.from_byte_array(corrupted)).check_integrity())
        with self.assertRaises(ValueError):
            build_content(self.root, self.site, "training")
        with self.assertRaises(ValueError):
            build_index(self.root, self.site)
        self.assertEqual((self.site / "index.json").read_bytes(), index_before)

    def test_fit_timestamp_and_json_name_collisions_are_rejected(self):
        created = datetime(2025, 1, 1, 0, 1, 2, tzinfo=timezone.utc)
        collision = self.write_fit("data/sources/training/other.fit", created)
        prepare(self.root, self.site)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            build_content(self.root, self.site, "training")
        collision.unlink()
        # Different timestamps do not prevent a same-stem JSON collision when
        # the extension differs only in case.
        collision = self.write_fit("data/sources/training/20250101.FIT", created + timedelta(hours=1))
        with self.assertRaisesRegex(ValueError, "overwrite"):
            build_content(self.root, self.site, "training")
        collision.unlink()
        # The same instant in a different folder has a different output path.
        self.write_fit("data/sources/training/nested/other.fit", created)
        build_content(self.root, self.site, "training")
        self.assertTrue((self.site / "data/contents/training/2025-01-01_09-01-02.html").is_file())
        self.assertTrue((self.site / "data/contents/training/nested/2025-01-01_09-01-02.html").is_file())

    def test_existing_json_asset_is_not_silently_overwritten(self):
        asset = "data/sources/training/20250101.json"
        self.write(asset, '{"original": true}\n')
        prepare(self.root, self.site)
        with self.assertRaisesRegex(ValueError, "overwrit"):
            build_content(self.root, self.site, "training")
        self.assertEqual((self.root / asset).read_text(encoding="utf-8"), '{"original": true}\n')

    def test_missing_fit_values_and_required_file_created_time(self):
        created = datetime(2025, 1, 3, 1, 0, 0, tzinfo=timezone.utc)
        self.write_fit("data/sources/training/sparse.fit", created,
                       session={"sport": "running", "total_distance": 0, "total_timer_time": 0},
                       laps=[{"intensity": "active", "total_distance": 0, "total_timer_time": 0}])
        self.run_build()
        contents = self.site / "data/contents/training"
        sparse = json.loads((contents / "sparse.json").read_text(encoding="utf-8"))
        self.assertEqual(sparse["session_mesgs"][0]["total_distance"], 0)
        self.assertNotIn("avg_heart_rate", sparse["session_mesgs"][0])
        page = (contents / "2025-01-03_10-00-00.html").read_text(encoding="utf-8")
        self.assertIn("0 km", page)
        self.assertIn("00:00:00", page)
        self.assertIn("—", page)
        self.assertNotIn(">None<", page)
        self.write_fit("data/sources/training/no-laps.fit", created + timedelta(days=1),
                       session={"sport": "running"}, laps=[])
        self.run_build()
        page = (contents / "2025-01-04_10-00-00.html").read_text(encoding="utf-8")
        self.assertIn("ラップ", page)
        self.assertNotIn("<tbody>", page)
        self.write_fit("data/sources/training/missing-created.fit", created, include_created=False)
        with self.assertRaisesRegex(ValueError, "time_created"):
            build_content(self.root, self.site, "training")

    def test_legacy_training_csv_is_not_converted_but_sample_csv_still_is(self):
        self.write("data/sources/training/legacy.csv", "列1,列2\n不正な行\n")
        self.write("data/sources/training/20250401.CSV", "種目,メモ\nラン,朝\n")
        self.write("data/sources/sample/20250401.csv", "種目,メモ\nラン,朝\n")
        self.run_build()
        index = json.loads((self.site / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(len([post for post in index["posts"] if post["type"] == "training"]), 2)
        self.assertFalse((self.site / "data/contents/training/legacy.html").exists())
        self.assertFalse((self.site / "data/contents/training/2025-04-01.html").exists())
        self.assertTrue((self.site / "data/contents/sample/2025-04-01.html").is_file())

    def test_relative_fit_timestamp_is_not_treated_as_a_calendar_date(self):
        from fit_training import created_at

        with self.assertRaisesRegex(ValueError, "time_created"):
            created_at({"file_id_mesgs": [{"time_created": "1990-01-01T00:00:00+00:00"}]})

    def test_pool_swimming_active_laps_exclude_inactive_lengths(self):
        from fit_training import training_article

        source = self.root / "data/sources/training/swim.fit"
        messages = {
            "file_id_mesgs": [{"time_created": "2025-01-01T00:00:00+00:00"}],
            "session_mesgs": [{"sport": "swimming", "sub_sport": "lap_swimming"}],
            "lap_mesgs": [
                {"num_active_lengths": 4},
                {"num_active_lengths": 0},
                {"num_active_lengths": 2},
            ],
        }
        _, _, body = training_article(source, messages, self.root,
                                      Path("data/contents/training/swim.json"))
        rows = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertEqual(rows.count("<tr>"), 2)
        self.assertIn("アクティブ長数が 0", body)

    def test_unclassified_laps_are_not_reported_as_active(self):
        from fit_training import training_article

        source = self.root / "data/sources/training/running.fit"
        messages = {
            "file_id_mesgs": [{"time_created": "2025-01-01T00:00:00+00:00"}],
            "session_mesgs": [{"sport": "running"}],
            "lap_mesgs": [{}, {"intensity": "rest"}, {"intensity": "active"}],
        }
        _, _, body = training_article(source, messages, self.root,
                                      Path("data/contents/training/running.json"))
        rows = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertEqual(rows.count("<tr>"), 1)
        self.assertIn("活動の判定に必要な情報がないラップ 1 件", body)

    def test_kind_thumbnail_selection_and_fallback_after_removal(self):
        self.write("assets/junichiwatanuki.jpg", "default image")
        self.write("data/sources/sample/20250101.csv", "種目,メモ\nラン,朝\n")
        self.run_build()
        posts = json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]
        self.assertEqual({post["thumbnail"] for post in posts}, {"assets/junichiwatanuki.jpg"})
        self.assertTrue((self.site / "assets/junichiwatanuki.jpg").is_file())

        for kind, extensions in (("blog", ("jpg", "png", "gif")),
                                 ("sample", ("png", "gif")),
                                 ("training", ("gif",))):
            for extension in extensions:
                self.write(f"data/sources/{kind}/thumbnail.{extension}", f"{kind}-{extension}")
        self.run_build()
        posts = json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]
        for kind, extension in (("blog", "jpg"), ("sample", "png"), ("training", "gif")):
            expected = f"data/sources/{kind}/thumbnail.{extension}"
            self.assertTrue(any(post["type"] == kind for post in posts))
            self.assertTrue(all(post["thumbnail"] == expected for post in posts if post["type"] == kind))
            self.assertEqual((self.site / expected).read_text(encoding="utf-8"), f"{kind}-{extension}")

        for removed, next_extension in (("jpg", "png"), ("png", "gif"), ("gif", None)):
            (self.root / f"data/sources/blog/thumbnail.{removed}").unlink()
            self.run_build()
            posts = json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]
            expected = (f"data/sources/blog/thumbnail.{next_extension}" if next_extension
                        else "assets/junichiwatanuki.jpg")
            self.assertTrue(all(post["thumbnail"] == expected for post in posts if post["type"] == "blog"))
            self.assertFalse((self.site / f"data/sources/blog/thumbnail.{removed}").exists())

    def test_thumbnail_uses_kind_root_and_must_be_published(self):
        self.write("data/sources/blog/nest/thumbnail.jpg", "nested image")
        self.write("data/sources/blog/thumbnail.JPG", "not a matching filename")
        self.run_build()
        posts = json.loads((self.site / "index.json").read_text(encoding="utf-8"))["posts"]
        self.assertTrue(all(post["thumbnail"] == "assets/junichiwatanuki.jpg"
                            for post in posts if post["type"] == "blog"))
        self.write("data/sources/blog/thumbnail.png", "new image")
        self.run_build()
        (self.site / "data/sources/blog/thumbnail.png").unlink()
        with self.assertRaisesRegex(ValueError, "thumbnail is missing"):
            build_index(self.root, self.site)

    def test_refuse_to_remove_unowned_output(self):
        self.site.mkdir()
        (self.site / "important.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Refusing"):
            prepare(self.root, self.site)
        self.assertTrue((self.site / "important.txt").is_file())


if __name__ == "__main__":
    unittest.main()
