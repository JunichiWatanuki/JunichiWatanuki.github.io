#!/usr/bin/env python3
"""Offline integration tests for comment sync; no real GitHub issues are made."""
from __future__ import annotations

import copy
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import sync_comments as sync

SCRIPT = Path(__file__).with_name("sync_comments.py")
OWNER = "Octo-user"
REPO = "MyRepo"
REPOSITORY = f"{OWNER}/{REPO}"
REPOSITORY_ID = 123456
TOKEN = "offline-token"
POST = "data/contents/blog/日本語 #?% 完全.html"
POST2 = "data/contents/sample/second.html"


def date(day: int) -> str:
    return (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat().replace("+00:00", "Z")


def form(path: str, comment: str) -> str:
    return f"### 対象記事パス (Post Path)\n\n{path}\n\n### コメント内容 (Comment)\n\n{comment}"


def issue(number: int, path: str = POST, body: str = "First **comment**.", state: str = "open") -> dict:
    return {"number": number, "title": f"Thread {number}", "body": form(path, body),
            "state": state, "created_at": date(0), "updated_at": date(1),
            "closed_at": date(3) if state == "closed" else None,
            "user": {"login": f"user{number}"}, "labels": [{"name": "comment-for-post"}],
            "html_url": "https://evil.invalid/unsafe"}


def comment(ident: int, body: str, day: int = 1) -> dict:
    return {"id": ident, "body": body, "created_at": date(day), "updated_at": date(day),
            "user": {"login": f"author{ident}"}, "html_url": "https://evil.invalid/unsafe"}


class MetadataParser(HTMLParser):
    """Inspect each rendered metadata paragraph without conflating its links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = []
        self.current = None
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.current is None:
            if tag != "p" or "issue-comment-meta" not in attrs.get("class", "").split():
                return
            self.current = []
        parent = self.stack[-1] if self.stack else None
        node = {"tag": tag, "attrs": attrs, "text": "",
                "parent_tag": parent["tag"] if parent else None,
                "parent_href": parent["attrs"].get("href") if parent else None}
        self.current.append(node)
        self.stack.append(node)

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if not self.stack or self.stack[-1]["tag"] != tag:
            raise AssertionError(f"Malformed comment metadata HTML at </{tag}>")
        self.stack.pop()
        if not self.stack:
            self.metadata.append(self.current)
            self.current = None


class API:
    def __init__(self):
        self.issues = []
        self.comments = {}
        self.bulk_entries = None
        self.bulk_limit_pages = None
        self.alias_links = False
        self.repository_metadata = {"id": REPOSITORY_ID}
        self.requests = []
        self.page_size = 2
        self.fail_path = None
        self.fail_page = None
        self.malformed_path = None
        self.bad_link = None
        self.bad_link_path = None
        self.omit_link = False
        self.repeat_page = False
        self.cursor_mode = None
        self.cursor_suffix = ""
        self.omit_link_filters = False
        self.repeat_cursor = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                query = parse_qs(urlsplit(self.path).query)
                outer.requests.append((path, query, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self.send_error(401)
                    return
                prefix = f"/repos/{REPOSITORY}"
                if path == prefix:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(outer.repository_metadata).encode("utf-8"))
                    return
                if query.get("per_page") != ["100"]:
                    self.send_error(400)
                    return
                if path == outer.fail_path and (outer.fail_page is None or query.get("page") == [str(outer.fail_page)]):
                    self.send_error(503)
                    return
                if path == outer.malformed_path:
                    payload = b"not json"
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if path == prefix + "/issues":
                    # The endpoint represents GitHub's label/state filter.
                    entries = outer.issues
                    if query.get("labels") != ["comment-for-post"] or query.get("state") != ["all"]:
                        self.send_error(400)
                        return
                elif path == prefix + "/issues/comments":
                    if query.get("sort") != ["created"] or query.get("direction") != ["asc"]:
                        self.send_error(400)
                        return
                    entries = outer.bulk_entries
                    if entries is None:
                        entries = []
                        for number, comments in outer.comments.items():
                            for item in comments:
                                item = copy.deepcopy(item)
                                if isinstance(item, dict):
                                    item.setdefault("issue_url", f"http://127.0.0.1:{self.server.server_port}{prefix}/issues/{number}")
                                entries.append(item)
                elif re.fullmatch(re.escape(prefix) + r"/issues/[1-9][0-9]*/comments", path):
                    entries = outer.comments.get(int(path.split("/")[-2]), [])
                else:
                    self.send_error(404)
                    return
                try:
                    if outer.cursor_mode == "only" and "after" in query:
                        page = int(query["after"][0].removeprefix("cursor-").removesuffix(outer.cursor_suffix))
                    else:
                        page = int(query.get("page", ["1"])[0])
                    if outer.cursor_mode and page >= 11 and query.get("after") != [f"cursor-{page}{outer.cursor_suffix}"]:
                        self.send_error(400)
                        return
                except ValueError:
                    self.send_error(400)
                    return
                if (path == prefix + "/issues/comments" and outer.bulk_limit_pages is not None
                        and page > outer.bulk_limit_pages):
                    self.send_error(422)
                    return
                size = outer.page_size
                part = entries[(page - 1) * size:page * size]
                if outer.repeat_page and page > 1:
                    part = entries[:size]
                payload = json.dumps(part, ensure_ascii=True).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if (outer.bad_link is not None and page == 1 and
                        (outer.bad_link_path is None or outer.bad_link_path == path)):
                    self.send_header("Link", outer.bad_link)
                elif (len(entries) > page * size and not outer.omit_link
                      and not (path == prefix + "/issues/comments" and outer.bulk_limit_pages is not None
                               and page >= outer.bulk_limit_pages)):
                    query["page"] = [str(page + 1)]
                    if outer.cursor_mode and (outer.cursor_mode == "only" or page + 1 >= 11):
                        query["after"] = [f"cursor-{page + 1}{outer.cursor_suffix}"]
                        if outer.cursor_mode == "only":
                            del query["page"]
                    if outer.omit_link_filters:
                        query = {k: v for k, v in query.items() if k in ("page", "after")}
                    if outer.repeat_cursor and page == 2:
                        query = {"after": ["cursor-2"]}
                    next_query = urlencode([(k, v) for k, vals in query.items() for v in vals])
                    link_path = path
                    if outer.alias_links:
                        link_path = f"/repositories/{REPOSITORY_ID}" + path[len(prefix):]
                    next_url = f"http://127.0.0.1:{self.server.server_port}{link_path}?{next_query}"
                    self.send_header("Link", f'<{next_url}>; rel="next"')
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class CommentsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = self.root / "site"
        self.site.mkdir()
        self.post_paths = [POST, POST2]
        self.reset_pages()
        self.api = API()
        self.addCleanup(self.api.close)

    def reset_pages(self):
        (self.site / "index.json").write_text(json.dumps({"posts": [{"path": quote(path, safe="/")} for path in self.post_paths]}), encoding="utf-8")
        for path in self.post_paths:
            target = self.site / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("<!doctype html><p>article</p>\n<!-- ISSUE_COMMENTS -->\nEND\n", encoding="utf-8")

    def run_sync(self, event=None, event_name="issues", ok=True, token=TOKEN, run_id=None, extra_env=None):
        env = dict(os.environ, GITHUB_TOKEN=token, GITHUB_API_URL=self.api.base)
        env.pop("GITHUB_EVENT_NAME", None)
        env.pop("GITHUB_EVENT_PATH", None)
        env.pop("GITHUB_RUN_ID", None)
        env.pop("GITHUB_REPOSITORY_ID", None)
        env.pop("GITHUB_REPOSITORY", None)
        if extra_env:
            env.update(extra_env)
        if run_id is not None:
            env["GITHUB_RUN_ID"] = str(run_id)
        if event is not None:
            event_path = self.root / "event.json"
            event_path.write_text(json.dumps(event, ensure_ascii=True), encoding="utf-8")
            env.update(GITHUB_EVENT_NAME=event_name, GITHUB_EVENT_PATH=str(event_path))
        process = subprocess.run([sys.executable, str(SCRIPT), "--site-dir", str(self.site), "--repository", REPOSITORY],
                                 env=env, capture_output=True, text=True, timeout=30)
        if ok:
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        else:
            self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
        return process

    def page(self, path=POST):
        return (self.site / path).read_text(encoding="utf-8")

    def archive(self, number):
        return self.site / "data" / "comments" / "archive" / f"issue-{number}.json"

    def event(self, event_issue, action="closed"):
        return {"action": action, "issue": copy.deepcopy(event_issue), "repository": {"full_name": REPOSITORY}}

    def metadata(self):
        parser = MetadataParser()
        parser.feed(self.page())
        parser.close()
        self.assertIsNone(parser.current, "Unclosed comment metadata")
        return parser.metadata

    def assert_metadata(self, nodes, author, author_href, original_time, displayed_time, time_href):
        authors = [node for node in nodes if "issue-comment-author" in node["attrs"].get("class", "").split()]
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0]["text"], author)
        if author_href is None:
            self.assertNotEqual(authors[0]["tag"], "a")
            self.assertNotEqual(authors[0]["parent_tag"], "a")
        else:
            self.assertEqual(authors[0]["tag"], "a")
            self.assertEqual(authors[0]["attrs"].get("href"), author_href)
        times = [node for node in nodes if node["tag"] == "time"]
        self.assertEqual(len(times), 1)
        self.assertEqual(times[0]["attrs"].get("datetime"), original_time)
        self.assertEqual(times[0]["text"], displayed_time)
        self.assertEqual(times[0]["parent_tag"], "a")
        self.assertEqual(times[0]["parent_href"], time_href)
        links = [node["attrs"].get("href") for node in nodes if node["tag"] == "a"]
        self.assertEqual(links, ([author_href] if author_href else []) + [time_href])

    def test_author_links_jst_and_unmodified_archive_timestamps(self):
        item = issue(71, state="closed")
        item["user"] = {"login": "JunichiWatanuki"}
        item["created_at"] = "2026-11-15T12:56:09Z"
        item["updated_at"] = "2026-11-16T03:04:05+02:00"
        item["closed_at"] = "2026-11-17T01:02:03+02:00"
        first = comment(711, "first reply")
        first["user"] = {"login": "reply-author"}
        first["created_at"] = "2026-11-15T15:00:00Z"
        first["updated_at"] = "2026-11-15T15:01:02Z"
        second = comment(712, "second reply")
        second["user"] = {"login": "OtherUser2"}
        second["created_at"] = "2026-11-16T01:22:33+02:00"
        second["updated_at"] = "2026-11-16T02:23:34+02:00"
        replies = [first, second]

        # Rendering is a presentation operation, not a change to source data.
        public_issue, _, body = sync.issue_public(item, OWNER, REPO, {POST: quote(POST, safe="/")})
        public_replies = sync.comments_public(replies, public_issue)
        before = copy.deepcopy((item, replies, public_issue, public_replies))
        sync.render_thread(public_issue, body, public_replies)
        self.assertEqual((item, replies, public_issue, public_replies), before)

        self.api.issues = [item]
        self.api.comments[71] = replies
        self.run_sync(self.event(item))
        metadata = self.metadata()
        self.assertEqual(len(metadata), 3)
        base = f"https://github.com/{REPOSITORY}/issues/71"
        expected = [
            ("JunichiWatanuki", item["created_at"], "2026-11-15 21:56:09 JST", base),
            ("reply-author", first["created_at"], "2026-11-16 00:00:00 JST", base + "#issuecomment-711"),
            ("OtherUser2", second["created_at"], "2026-11-16 08:22:33 JST", base + "#issuecomment-712"),
        ]
        for nodes, (author, original, displayed, link) in zip(metadata, expected):
            with self.subTest(author=author):
                self.assert_metadata(nodes, author, f"https://github.com/{author}", original, displayed, link)
        self.assertIn('<a class="issue-comment-author" href="https://github.com/JunichiWatanuki">'
                      'JunichiWatanuki</a>', self.page())
        self.assertNotIn("https://evil.invalid/unsafe", self.page())

        archived = self.archive(71).read_bytes()
        saved = json.loads(archived)
        for key in ("created_at", "updated_at", "closed_at"):
            self.assertEqual(saved["issue"][key], item[key])
        self.assertEqual([c["id"] for c in saved["comments"]], [711, 712])
        for archived_reply, original_reply in zip(saved["comments"], replies):
            for key in ("created_at", "updated_at"):
                self.assertEqual(archived_reply[key], original_reply[key])
        self.reset_pages()
        self.run_sync()
        self.assertEqual(self.archive(71).read_bytes(), archived)
        self.reset_pages()
        self.run_sync(self.event(item))
        self.assertEqual(self.archive(71).read_bytes(), archived)

    def test_untrusted_or_missing_authors_are_escaped_and_never_linked(self):
        # Exercise both issue authors and reply authors, including a GitHub App
        # login whose profile URL cannot be derived reliably from its login.
        values = [None, "ghost", "Ghost", "dependabot[bot]", '<img src=x onerror="alert(1)">',
                  '\"><svg onload="alert(2)">', "x&y", "name/path", "name?query", "name#fragment",
                  "name%2Fpath", "name@example.com", "name with spaces", "-name", "name-", "name--name",
                  "a" * 40]
        self.api.page_size = 100
        for number, value in enumerate(values, 1):
            item = issue(number)
            reply = comment(1000 + number, "reply")
            item["user"] = None if value is None else {"login": value}
            reply["user"] = None if value is None else {"login": value}
            self.api.issues.append(item)
            self.api.comments[number] = [reply]
        self.run_sync()
        metadata = self.metadata()
        raw_metadata = re.findall(r'<p class="issue-comment-meta">.*?</p>', self.page(), flags=re.DOTALL)
        self.assertEqual(len(metadata), 2 * len(values))
        self.assertEqual(len(raw_metadata), len(metadata))
        for index, value in enumerate(values):
            expected_author = "ghost" if value is None else value
            base = f"https://github.com/{REPOSITORY}/issues/{index + 1}"
            for offset, time_link in enumerate((base, base + f"#issuecomment-{1001 + index}")):
                with self.subTest(author=value, reply=bool(offset)):
                    position = 2 * index + offset
                    nodes = metadata[position]
                    original = date(offset)
                    displayed = "2025-01-01 09:00:00 JST" if offset == 0 else "2025-01-02 09:00:00 JST"
                    self.assert_metadata(nodes, expected_author, None, original, displayed, time_link)
                    self.assertIn(html.escape(expected_author, quote=True), raw_metadata[position])
                    self.assertFalse(any(node["tag"] in {"script", "img", "svg", "iframe"} for node in nodes))
                    self.assertFalse(any(key.lower().startswith("on") for node in nodes for key in node["attrs"]))

    def test_jst_overflow_preserves_original_timestamp_and_comments(self):
        item = issue(98)
        reply = comment(981, "boundary reply")
        original = "9999-12-31T23:59:59Z"
        item["created_at"] = original
        reply["created_at"] = original
        self.api.issues = [item]
        self.api.comments[98] = [reply]
        self.run_sync()
        metadata = self.metadata()
        self.assertEqual(len(metadata), 2)
        base = f"https://github.com/{REPOSITORY}/issues/98"
        self.assert_metadata(metadata[0], "user98", "https://github.com/user98", original, original, base)
        self.assert_metadata(metadata[1], "author981", "https://github.com/author981", original, original,
                             base + "#issuecomment-981")
        self.assertIn("boundary reply", self.page())

    def test_jst_uses_four_digit_year_at_lower_datetime_boundary(self):
        item = issue(97)
        reply = comment(971, "early boundary reply")
        original = "0001-01-01T00:00:00Z"
        item["created_at"] = original
        reply["created_at"] = original
        self.api.issues = [item]
        self.api.comments[97] = [reply]
        self.run_sync()
        metadata = self.metadata()
        self.assertEqual(len(metadata), 2)
        base = f"https://github.com/{REPOSITORY}/issues/97"
        displayed = "0001-01-01 09:00:00 JST"
        self.assert_metadata(metadata[0], "user97", "https://github.com/user97", original, displayed, base)
        self.assert_metadata(metadata[1], "author971", "https://github.com/author971", original, displayed,
                             base + "#issuecomment-971")

    def test_multiple_pages_gfm_xss_and_stable_order(self):
        one = issue(1, quote(POST, safe="/"), """hello **strong** ~~strike~~

| H | B |
|---|---|
| 1 | 2 |

- [x] done

[js](javascript:alert(1)) [data](data:text/html,abc) [web](https://example.com)

<script>alert('script')</script> <img src=x onerror=alert(1)>

### 対象記事パス (Post Path)

    preserve indented code
""")
        one["title"] = '<svg onload="alert(1)"> & thread'
        one["user"]["login"] = '<img src=x onerror="alert(2)">'
        other = issue(5, POST2)
        three = issue(3, f"https://github.com/{OWNER}/{REPO}/blob/main/" + quote(POST, safe="/"))
        pr = issue(9)
        pr["pull_request"] = {"url": "ignored"}
        self.api.issues = [three, pr, other, one]
        self.api.comments[1] = [comment(21, "third", 2), comment(19, "first <script>bad</script>", 1),
                                comment(20, "**second**[^x]\n\n[^x]: footnote", 1)]
        self.api.comments[3] = [comment(88, "last")]
        self.run_sync()
        page = self.page()
        self.assertEqual(page.count('class="issue-comment-thread"'), 2)
        self.assertLess(page.index("issues/1\""), page.index("issues/3\""))
        self.assertLess(page.index("#issuecomment-19"), page.index("#issuecomment-20"))
        self.assertLess(page.index("#issuecomment-20"), page.index("#issuecomment-21"))
        self.assertIn("<strong>strong</strong>", page)
        self.assertIn("<del>strike</del>", page)
        self.assertIn("<table>", page)
        self.assertIn('type="checkbox"', page)
        self.assertIn('id="greenlog-issue-1-comment-20-fn-x"', page)
        self.assertIn('href="#greenlog-issue-1-comment-20-fn-x"', page)
        self.assertIn("preserve indented code", page)
        self.assertIn("&lt;svg onload=&quot;alert(1)&quot;&gt;", page)
        self.assertIn("&lt;img src=x onerror=&quot;alert(2)&quot;&gt;", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<svg", page.lower())
        self.assertNotIn("javascript:", page.lower())
        self.assertNotIn("data:text", page.lower())
        self.assertNotIn("https://evil.invalid", page)
        self.assertIn("https://github.com/Octo-user/MyRepo/issues/1#issuecomment-20", page)
        self.assertIn("Thread 5", self.page(POST2))
        requests = self.api.requests
        self.assertTrue(any(path.endswith("/issues") and q.get("page") == ["2"] for path, q, _ in requests))
        self.assertTrue(any(path.endswith("/issues/comments") and q.get("page") == ["2"] for path, q, _ in requests))
        self.assertFalse(any("/issues/9/comments" in path for path, _, _ in requests))
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, _, auth in requests))
        old = self.page()
        self.reset_pages()
        self.api.issues.reverse()
        self.api.comments[1].reverse()
        self.run_sync()
        self.assertEqual(self.page(), old)

    def test_path_forms_and_origins(self):
        posts = {POST: quote(POST, safe="/"), POST2: POST2}
        encoded = quote(POST, safe="/")
        accepted = [POST, encoded,
                    f"https://github.com/{OWNER}/{REPO}/blob/master/{encoded}",
                    f"https://{OWNER.lower()}.github.io/{REPO}/{encoded}"]
        for value in accepted:
            with self.subTest(value=value):
                self.assertEqual(sync.match_post(value, OWNER, REPO, posts), POST)
        user_repo = OWNER + ".github.io"
        self.assertEqual(sync.match_post(f"https://{OWNER}.github.io/{encoded}", OWNER, user_repo, posts), POST)
        invalid = ["/" + POST, "../" + POST, "data/contents/blog/../../sample/second.html",
                   "data/contents/blog/%2e%2e/%2e%2e/sample/second.html", "data/contents/blog/..%2fsample/second.html",
                   "data\\contents\\sample\\second.html", "data/contents/sample/no.html",
                   f"https://github.com/{OWNER}/OTHER/blob/main/{encoded}",
                   f"https://github.com/{OWNER}/{REPO}/blob/develop/{encoded}",
                   f"https://{OWNER}.github.io/OTHER/{encoded}",
                   f"https://{OWNER}.github.io.evil.test/{REPO}/{encoded}",
                   f"https://evil@github.com/{OWNER}/{REPO}/blob/main/{encoded}",
                   f"https://github.com/{OWNER}/{REPO}/blob/main/{encoded}?x=1",
                   f"http://{OWNER}.github.io/{REPO}/{encoded}", "javascript:alert(1)"]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    sync.match_post(value, OWNER, REPO, posts)
        # Ambiguous encoded-vs-decoded spelling must not select the wrong post.
        ambiguous = {"data/contents/x/%41.html": "", "data/contents/x/A.html": ""}
        with self.assertRaises(ValueError):
            sync.match_post("data/contents/x/%41.html", OWNER, REPO, ambiguous)
        percent_names = {"data/contents/blog/%.html": "", "data/contents/blog/%25.html": ""}
        pages_origin = f"https://{OWNER.lower()}.github.io/"
        self.assertEqual(sync.match_post(pages_origin + "data/contents/blog/%25.html",
                                         OWNER, user_repo, percent_names), "data/contents/blog/%.html")
        self.assertEqual(sync.match_post(pages_origin + "data/contents/blog/%2525.html",
                                         OWNER, user_repo, percent_names), "data/contents/blog/%25.html")
        with self.assertRaises(ValueError):
            sync.match_post("data/contents/blog/%25.html", OWNER, user_repo, percent_names)
        literal_percent = "data/contents/x/%FF.html"
        self.assertEqual(sync.match_post(literal_percent, OWNER, REPO, {literal_percent: ""}), literal_percent)
        traversal_name = "data/contents/x/%2e%2e/a.html"
        with self.assertRaises(ValueError):
            sync.match_post(traversal_name, OWNER, REPO, {traversal_name: ""})

    def test_percent_encoded_urls_select_the_correct_article(self):
        first = "data/contents/blog/%.html"
        second = "data/contents/blog/%25.html"
        self.post_paths = [first, second]
        self.reset_pages()
        origin = f"https://{OWNER.lower()}.github.io/{REPO}/"
        self.api.issues = [
            issue(1, origin + quote(first, safe="/"), "first-page-comment"),
            issue(2, origin + quote(second, safe="/"), "second-page-comment"),
        ]
        self.run_sync()
        self.assertIn("first-page-comment", self.page(first))
        self.assertNotIn("second-page-comment", self.page(first))
        self.assertIn("second-page-comment", self.page(second))
        self.assertNotIn("first-page-comment", self.page(second))

    def test_invalid_issues_are_skipped_without_executing_input(self):
        good = issue(1)
        bad = issue(2, "data/contents/blog/../../outside.html")
        missing_heading = issue(3)
        missing_heading["body"] = "not a form"
        malformed = issue(4)
        malformed["title"] = {"bad": "type"}
        duplicate = copy.deepcopy(good)
        huge_number = issue(2**100)
        shell_text = f"$(touch {self.root}/executed)"
        good["body"] = form(POST, shell_text)
        malformed_comment = issue(5)
        self.api.comments[5] = [{"id": "bad"}]
        self.api.issues = [bad, good, malformed, missing_heading, malformed_comment, duplicate, huge_number]
        result = self.run_sync()
        self.assertGreaterEqual(result.stderr.count("Skipping invalid issue"), 4)
        self.assertIn(shell_text, self.page())
        self.assertFalse((self.root / "executed").exists())
        self.assertEqual(self.page().count('class="issue-comment-thread"'), 1)

    def test_unpaired_surrogates_only_skip_bad_issue(self):
        good = issue(1)
        bad_title = issue(2)
        bad_title["title"] = "broken\ud800"
        bad_body = issue(3, body="broken\ud800")
        bad_author = issue(4)
        bad_author["user"]["login"] = "broken\ud800"
        bad_comment = issue(5)
        self.api.comments[5] = [comment(1, "broken\ud800")]
        self.api.issues = [good, bad_title, bad_body, bad_author, bad_comment]
        result = self.run_sync()
        self.assertEqual(result.stderr.count("Skipping invalid issue"), 4)
        self.assertEqual(self.page().count('class="issue-comment-thread"'), 1)

    def test_full_page_without_link_is_probed_and_repeat_fails(self):
        self.api.page_size = 100
        self.api.omit_link = True
        self.api.issues = []
        for number in range(1, 102):
            pr = issue(number)
            pr["pull_request"] = {"url": "ignored"}
            self.api.issues.append(pr)
        self.run_sync()
        self.assertTrue(any(path.endswith("/issues") and q.get("page") == ["2"]
                            for path, q, _ in self.api.requests))
        self.reset_pages()
        self.api.repeat_page = True
        self.run_sync(ok=False)
        self.assertIn(sync.MARKER, self.page())

    def test_cursor_pagination_beyond_1000_for_issues_and_comments(self):
        self.api.page_size = 100
        self.api.omit_link_filters = True
        self.api.cursor_suffix = "+/=%&"
        relevant = issue(1)
        prs = []
        for n in range(2, 1003):
            pr = issue(n)
            pr["pull_request"] = {"url": "ignored"}
            prs.append(pr)
        self.api.issues = [relevant] + prs
        self.api.comments[1] = [comment(n, f"bulk-comment-{n}") for n in range(1, 1102)]
        for mode in ("both", "only"):
            with self.subTest(mode=mode):
                self.api.cursor_mode = mode
                self.api.requests.clear()
                self.reset_pages()
                self.run_sync()
                self.assertIn("bulk-comment-1101", self.page())
                for endpoint in ("/issues", "/issues/comments"):
                    requests = [(q, auth) for p, q, auth in self.api.requests if p.endswith(endpoint)]
                    self.assertTrue(any("after" in q for q, _ in requests))
                    self.assertTrue(all(q["after"][0].endswith(self.api.cursor_suffix)
                                        for q, _ in requests if "after" in q))
                    self.assertTrue(all(q.get("per_page") == ["100"] for q, _ in requests))
                    self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, auth in requests))
                    if endpoint == "/issues":
                        self.assertTrue(all(q.get("state") == ["all"] and q.get("labels") == ["comment-for-post"]
                                            for q, _ in requests))
                    else:
                        self.assertTrue(all(q.get("sort") == ["created"] and q.get("direction") == ["asc"]
                                            for q, _ in requests))

    def test_repeated_cursor_fails_without_publishing(self):
        self.api.cursor_mode = "only"
        self.api.repeat_cursor = True
        self.api.issues = [issue(n) for n in range(1, 7)]
        before = self.page()
        result = self.run_sync(ok=False)
        self.assertIn("repeated a pagination cursor", result.stderr)
        self.assertEqual(self.page(), before)
        self.assertEqual(len(self.api.requests), 2)

    def test_real_style_alias_links_keep_page_and_after_with_actions_id(self):
        self.api.page_size = 100
        self.api.cursor_mode = "both"
        self.api.cursor_suffix = "+/=%&"
        self.api.alias_links = True
        first = issue(1)
        first["comments"] = 1002
        prs = [issue(n) for n in range(2, 1003)]
        for pr in prs:
            pr["pull_request"] = {"url": "ignored"}
        self.api.issues = [first] + prs
        self.api.comments[1] = [comment(n, f"reply-{n}") for n in range(1, 1003)]
        self.run_sync(extra_env={"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID), "GITHUB_REPOSITORY": REPOSITORY})
        self.assertIn("reply-1002", self.page())
        self.assertTrue(all(path.startswith(f"/repos/{REPOSITORY}/") for path, _, _ in self.api.requests))
        self.assertFalse(any(path == f"/repos/{REPOSITORY}" for path, _, _ in self.api.requests))
        for endpoint in ("/issues", "/issues/comments"):
            requests = [q for path, q, _ in self.api.requests if path.endswith(endpoint)]
            self.assertTrue(any("page" in q and "after" in q for q in requests), endpoint)
            self.assertTrue(all(q["after"][0].endswith(self.api.cursor_suffix) for q in requests if "after" in q))

    def test_alias_without_actions_id_resolves_trusted_repository_once(self):
        self.api.alias_links = True
        self.api.issues = [issue(1), issue(2), issue(3)]
        self.api.comments[1] = [comment(n, f"reply-{n}") for n in range(1, 5)]
        self.run_sync()
        self.assertIn("reply-4", self.page())
        self.assertEqual(sum(path == f"/repos/{REPOSITORY}" for path, _, _ in self.api.requests), 1)
        self.assertTrue(all(not path.startswith("/repositories/") for path, _, _ in self.api.requests))
        self.reset_pages()
        before = self.page()
        self.api.requests.clear()
        self.api.bad_link_path = f"/repos/{REPOSITORY}/issues"
        self.api.bad_link = f'<{self.api.base}/repositories/{REPOSITORY_ID + 1}/issues?page=2>; rel="next"'
        self.run_sync(ok=False)
        self.assertEqual(sum(path == f"/repos/{REPOSITORY}" for path, _, _ in self.api.requests), 1)
        self.assertTrue(all(not path.startswith("/repositories/") for path, _, _ in self.api.requests))
        self.assertEqual(self.page(), before)
        self.api.bad_link = None
        self.api.repository_metadata = {"id": True}
        result = self.run_sync(ok=False)
        self.assertIn("repository ID", result.stderr)
        self.assertEqual(self.page(), before)

    def test_alias_rejects_other_id_endpoint_origin_credentials_and_bad_env(self):
        self.api.issues = [issue(1)]
        self.api.comments[1] = [comment(n, "reply") for n in range(1, 4)]
        endpoint = f"/repos/{REPOSITORY}/issues"
        alias = self.api.base + f"/repositories/{REPOSITORY_ID}"
        bad_links = [
            (endpoint, self.api.base + f"/repositories/{REPOSITORY_ID + 1}/issues?page=2"),
            (endpoint, alias + "/issues/comments?page=2"),
            (endpoint, alias + "/issues/1/comments?page=2"),
            (endpoint, self.api.base.replace("127.0.0.1", "localhost") + f"/repositories/{REPOSITORY_ID}/issues?page=2"),
            (endpoint, alias.replace("127.0.0.1", "user:pass@127.0.0.1") + "/issues?page=2"),
            (endpoint, alias + "/issues?page=2#fragment"),
            (endpoint, alias + "/issues?page=2&labels=other"),
            (endpoint, alias + "/issues?page=2&page=3"),
            (endpoint, self.api.base + f"/repositories/0{REPOSITORY_ID}/issues?page=2"),
            (endpoint + "/comments", alias + "/issues?page=2"),
            (endpoint + "/comments", alias + "/issues/1/comments?page=2"),
            (endpoint + "/comments", self.api.base + f"/repositories/{REPOSITORY_ID + 1}/issues/comments?page=2"),
        ]
        before = self.page()
        for target_endpoint, url in bad_links:
            with self.subTest(url=url):
                self.api.requests.clear()
                self.api.bad_link_path = target_endpoint
                self.api.bad_link = f'<{url}>; rel="next"'
                result = self.run_sync(ok=False, extra_env={"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID)})
                self.assertIn("Invalid GitHub pagination Link", result.stderr)
                self.assertNotIn(TOKEN, result.stderr)
                self.assertEqual(self.page(), before)
                self.assertTrue(all(path.startswith(f"/repos/{REPOSITORY}/") for path, _, _ in self.api.requests))
        self.api.bad_link = None
        for value in ("", "0", "-1", "01", str(2**64), "1\n"):
            with self.subTest(repository_id=value):
                self.api.requests.clear()
                self.run_sync(ok=False, extra_env={"GITHUB_REPOSITORY_ID": value})
                self.assertFalse(self.api.requests)
        self.run_sync(ok=False, extra_env={"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID),
                                           "GITHUB_REPOSITORY": "other/repository"})
        # Issue-specific aliases must not be accepted for another issue either.
        self.api.requests.clear()
        self.api.bad_link_path = endpoint + "/1/comments"
        self.api.bad_link = f'<{alias}/issues/2/comments?page=2>; rel="next"'
        api = sync.GitHubAPI(self.api.base, TOKEN, OWNER, REPO, {"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID)})
        with self.assertRaises(sync.SyncError):
            api.get_pages("/issues/1/comments", {})
        self.assertEqual([path for path, _, _ in self.api.requests], [endpoint + "/1/comments"])
        enterprise = sync.GitHubAPI(self.api.base + "/api/v3", TOKEN, OWNER, REPO,
                                    {"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID)})
        self.assertTrue(enterprise.pagination_path_matches(f"/api/v3/repositories/{REPOSITORY_ID}/issues", "/issues", "unused"))
        self.assertFalse(enterprise.pagination_path_matches(f"/repositories/{REPOSITORY_ID}/issues", "/issues", "unused"))

    def test_edited_or_empty_event_body_archives_without_rendering(self):
        variants = [("not a form", None), ("", None), (None, None),
                    ("### 対象記事パス (Post Path)\n\n\n### コメント内容 (Comment)\n\nhello", None),
                    (form("first\nsecond", "hello"), None),
                    (f"### 対象記事パス (Post Path)\n\n{POST}\n\n", None),
                    (form(POST, ""), quote(POST, safe="/")),
                    (form(POST, "_No response_"), quote(POST, safe="/"))]
        for number, (body, expected_path) in enumerate(variants, 101):
            with self.subTest(body=body):
                self.reset_pages()
                item = issue(number, state="closed")
                item["body"] = body
                self.api.issues = [item]
                self.api.comments = {number: [comment(number, "reply", 2)]}
                self.run_sync(self.event(item))
                saved = json.loads(self.archive(number).read_text(encoding="utf-8"))
                self.assertEqual(saved["issue"]["body"], body)
                self.assertEqual(saved["post_path"], expected_path)
                self.assertEqual(saved["comments"][0]["body"], "reply")
                self.assertNotIn(f"Thread {number}", self.page())
                self.assertEqual(self.archive(number).name, f"issue-{number}.json")

    def test_same_second_reclose_run_id_and_legacy_archive(self):
        item = issue(4, state="closed", body="first")
        self.api.issues = [item]
        self.api.comments[4] = [comment(1, "reply")]
        self.run_sync(self.event(item))
        legacy = self.archive(4).read_bytes()
        item["body"] = form(POST, "second")
        self.reset_pages()
        self.run_sync(self.event(item), run_id=100)
        second = self.archive(4).read_bytes()
        self.assertNotEqual(legacy, second)
        self.assertEqual(json.loads(second)["github_run_id"], 100)
        item["body"] = form(POST, "third")
        reopened = copy.deepcopy(item)
        reopened["state"] = "open"
        reopened["closed_at"] = None
        self.reset_pages()
        self.run_sync(self.event(reopened, action="reopened"), run_id=101)
        self.assertEqual(self.archive(4).read_bytes(), second)
        for run_id in (100, 99, None):
            self.reset_pages()
            self.run_sync(self.event(item), run_id=run_id)
            self.assertEqual(self.archive(4).read_bytes(), second)
        self.reset_pages()
        self.run_sync(self.event(item), run_id=101)
        third = self.archive(4).read_bytes()
        self.assertEqual(json.loads(third)["issue"]["body"], item["body"])
        self.assertEqual(json.loads(third)["github_run_id"], 101)
        old = copy.deepcopy(item)
        old["closed_at"] = date(2)
        self.reset_pages()
        self.run_sync(self.event(old), run_id=999)
        self.assertEqual(self.archive(4).read_bytes(), third)
        new = copy.deepcopy(item)
        new["closed_at"] = date(4)
        self.reset_pages()
        self.run_sync(self.event(new), run_id=101)
        self.assertEqual(self.archive(4).read_bytes(), third)
        self.reset_pages()
        self.run_sync(self.event(new), run_id=1)
        self.assertEqual(json.loads(self.archive(4).read_bytes())["issue"]["closed_at"], date(4))
        saved = self.archive(4).read_bytes()
        for bad in ("0", "-1", "invalid", str(2**64)):
            self.reset_pages()
            self.run_sync(self.event(new), run_id=bad, ok=False)
            self.assertEqual(self.archive(4).read_bytes(), saved)

    def test_close_reopen_edits_reclose_and_stale_event(self):
        item = issue(4, state="closed", body="original")
        self.api.issues = [item]
        self.api.comments[4] = [comment(1, "at close", 2)]
        # A closed issue observed on a normal push is NOT snapshotted.
        self.run_sync()
        self.assertFalse(self.archive(4).exists())
        self.reset_pages()
        first_close = self.event(item)
        self.run_sync(first_close)
        first = self.archive(4).read_bytes()
        saved = json.loads(first)
        self.assertEqual(saved["issue"]["title"], "Thread 4")
        self.assertEqual(saved["issue"]["body"], item["body"])
        self.assertEqual(saved["comments"][0]["body"], "at close")
        self.assertEqual(saved["issue"]["html_url"], f"https://github.com/{REPOSITORY}/issues/4")
        # Same event is byte-preserving even if the live comment was edited.
        self.api.comments[4][0]["body"] = "edited after first close"
        self.reset_pages()
        self.run_sync(first_close)
        self.assertEqual(self.archive(4).read_bytes(), first)
        item["state"] = "open"
        item["closed_at"] = None
        item["title"] = "updated title"
        item["body"] = form(POST, "updated issue")
        self.reset_pages()
        self.run_sync(self.event(item, action="reopened"))
        self.assertEqual(self.archive(4).read_bytes(), first)
        self.assertIn("updated issue", self.page())
        # Edits, including an edit on a closed issue, never rewrite the archive.
        item["state"] = "closed"
        item["closed_at"] = date(7)
        self.reset_pages()
        self.run_sync(self.event(item, action="edited"))
        self.assertEqual(self.archive(4).read_bytes(), first)
        self.reset_pages()
        self.run_sync(self.event(item))
        second = self.archive(4).read_bytes()
        self.assertNotEqual(first, second)
        saved2 = json.loads(second)
        self.assertEqual(saved2["issue"]["closed_at"], date(7))
        self.assertEqual(saved2["issue"]["title"], "updated title")
        self.assertEqual(saved2["comments"][0]["body"], "edited after first close")
        self.reset_pages()
        self.run_sync(first_close)
        self.assertEqual(self.archive(4).read_bytes(), second)

    def test_delayed_close_uses_event_and_filters_new_comments(self):
        old = issue(2, body="text at close", state="closed")
        old["title"] = "title at close"
        live = issue(2, body="text now", state="open")
        live["title"] = "title now"
        self.api.issues = [live]
        self.api.comments[2] = [comment(1, "before", 2), comment(2, "exact cutoff", 3), comment(3, "after", 4)]
        self.run_sync(self.event(old))
        archive = json.loads(self.archive(2).read_text(encoding="utf-8"))
        self.assertEqual(archive["issue"]["title"], "title at close")
        self.assertEqual(archive["issue"]["body"], old["body"])
        self.assertEqual([c["id"] for c in archive["comments"]], [1, 2])
        self.assertIn("title now", self.page())
        self.assertIn("text now", self.page())
        self.assertIn("after", self.page())

    def test_delayed_close_missing_from_current_label_listing(self):
        old = issue(6, state="closed")
        self.api.issues = []
        self.api.comments[6] = [comment(1, "retained")]
        self.run_sync(self.event(old))
        self.assertEqual(json.loads(self.archive(6).read_text())["comments"][0]["body"], "retained")
        self.assertNotIn("Thread 6", self.page())
        self.assertTrue(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))

    def test_bulk_grouping_excludes_unrelated_issue_and_pr(self):
        first, second = issue(1), issue(2, POST2)
        pr = issue(3)
        pr["pull_request"] = {"url": "ignored"}
        self.api.issues = [first, second, pr]
        self.api.comments = {1: [comment(11, "first reply")], 2: [comment(22, "second reply")],
                             3: [comment(33, "pull request reply")], 999: [comment(99, "other issue reply")]}
        self.run_sync()
        self.assertIn("first reply", self.page())
        self.assertNotIn("second reply", self.page())
        self.assertIn("second reply", self.page(POST2))
        self.assertNotIn("first reply", self.page(POST2))
        for path in self.post_paths:
            self.assertNotIn("pull request reply", self.page(path))
            self.assertNotIn("other issue reply", self.page(path))
        self.assertFalse(any(p.endswith(("/issues/1/comments", "/issues/2/comments"))
                             for p, _, _ in self.api.requests))

    def test_comment_counts_skip_bulk_only_when_all_known_zero(self):
        item = issue(1, state="closed")
        item["comments"] = 0
        self.api.issues = [item]
        self.run_sync(self.event(item))
        self.assertEqual(json.loads(self.archive(1).read_text())["comments"], [])
        self.assertFalse(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        # No count in an old fixture or in a close event is not assumed zero.
        self.reset_pages()
        self.api.requests.clear()
        del item["comments"]
        self.run_sync()
        self.assertTrue(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        self.reset_pages()
        self.api.requests.clear()
        self.api.issues = []
        item["closed_at"] = date(4)
        self.run_sync(self.event(item))
        self.assertTrue(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        # GitHub's nullable body is valid even when the closed issue vanished
        # from the current list, and event comments=0 avoids the bulk API.
        self.reset_pages()
        self.api.requests.clear()
        item["closed_at"] = date(5)
        item["comments"] = 0
        item["body"] = None
        self.run_sync(self.event(item))
        self.assertFalse(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        self.assertIsNone(json.loads(self.archive(1).read_text())["post_path"])
        # False must not be treated as integer zero.
        self.reset_pages()
        self.api.requests.clear()
        item["comments"] = False
        self.api.issues = [item]
        item["body"] = form(POST, "valid")
        self.run_sync()
        self.assertTrue(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))

    def test_100_threads_and_100_comments_use_three_requests(self):
        self.api.page_size = 100
        self.api.issues = [issue(n) for n in range(1, 101)]
        for item in self.api.issues:
            item["comments"] = 1
            self.api.comments[item["number"]] = [comment(item["number"], f'reply {item["number"]}')]
        self.run_sync()
        self.assertLessEqual(len(self.api.requests), 3, self.api.requests)
        self.assertEqual(self.page().count('class="issue-comment-thread"'), 100)
        self.assertEqual(self.page().count('class="issue-comment-body"'), 200)
        self.assertTrue(all(p.endswith(("/issues", "/issues/comments")) for p, _, _ in self.api.requests))

    def test_30000_bulk_cap_falls_back_only_for_incomplete_or_unknown_targets(self):
        self.api.page_size = 100
        self.api.bulk_limit_pages = 300
        self.api.alias_links = True
        self.api.cursor_mode = "both"
        live = issue(1)
        live["comments"] = 1
        closed = issue(1, state="closed")
        closed["comments"] = 2  # event and live counts must be combined using the larger value
        complete = issue(2, POST2)
        complete["comments"] = 1
        unknown = issue(3)
        zero = issue(4)
        zero["comments"] = 0
        self.api.issues = [live, complete, unknown, zero]
        before_close = comment(1, "before cap and close", 2)
        at_close = comment(30001, "after cap exactly at close", 3)
        after_close = comment(30002, "after cap and close", 4)
        second = comment(2, "already complete", 1)
        self.api.comments = {1: [before_close, at_close, after_close], 2: [second],
                             3: [comment(40000 + n, f"unknown-{n}") for n in range(1, 102)], 4: []}
        issue_base = f"{self.api.base}/repos/{REPOSITORY}/issues/"
        entry1 = dict(before_close, issue_url=issue_base + "1")
        entry2 = dict(second, issue_url=issue_base + "2")
        unrelated = [{"id": n, "issue_url": issue_base + "999"} for n in range(3, 30001)]
        self.api.bulk_entries = [entry1, entry2] + unrelated + [
            dict(at_close, issue_url=issue_base + "1"), dict(after_close, issue_url=issue_base + "1")]
        self.run_sync(self.event(closed), extra_env={"GITHUB_REPOSITORY_ID": str(REPOSITORY_ID)})
        bulk_requests = [q for p, q, _ in self.api.requests if p.endswith("/issues/comments")]
        self.assertEqual(len(bulk_requests), 300)
        self.assertEqual(bulk_requests[-1]["page"], ["300"])
        self.assertIn("after", bulk_requests[-1])
        issue_requests = [p for p, _, _ in self.api.requests if re.search(r"/issues/[0-9]+/comments$", p)]
        self.assertEqual(issue_requests.count(f"/repos/{REPOSITORY}/issues/1/comments"), 1)
        self.assertEqual(issue_requests.count(f"/repos/{REPOSITORY}/issues/3/comments"), 2)
        self.assertFalse(any(p.endswith(("/2/comments", "/4/comments", "/999/comments")) for p in issue_requests))
        self.assertIn("after cap exactly at close", self.page())
        self.assertIn("after cap and close", self.page())
        self.assertIn("unknown-101", self.page())
        self.assertIn("already complete", self.page(POST2))
        saved = json.loads(self.archive(1).read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in saved["comments"]], [1, 30001])
        self.assertTrue(all(not p.startswith("/repositories/") for p, _, _ in self.api.requests))

    def test_bulk_cap_known_complete_and_fallback_failure_are_safe(self):
        self.api.page_size = 100
        self.api.bulk_limit_pages = 2
        item = issue(1)
        item["comments"] = 1
        self.api.issues = [item]
        entry = comment(1, "complete")
        entry["issue_url"] = f"{self.api.base}/repos/{REPOSITORY}/issues/1"
        other_url = f"{self.api.base}/repos/{REPOSITORY}/issues/999"
        self.api.bulk_entries = [entry] + [{"id": n, "issue_url": other_url} for n in range(2, 202)]
        with patch.object(sync, "BULK_COMMENT_LIMIT", 200):
            sync.sync(self.site, REPOSITORY, TOKEN, self.api.base, {})
            self.assertIn("complete", self.page())
            self.assertFalse(any(p.endswith("/issues/1/comments") for p, _, _ in self.api.requests))
            self.reset_pages()
            before = self.page()
            self.api.requests.clear()
            item["comments"] = 2
            closed = issue(1, state="closed")
            closed["comments"] = 1
            event_path = self.root / "cap-event.json"
            event_path.write_text(json.dumps(self.event(closed)), encoding="utf-8")
            self.api.fail_path = f"/repos/{REPOSITORY}/issues/1/comments"
            with self.assertRaises(sync.SyncError):
                sync.sync(self.site, REPOSITORY, TOKEN, self.api.base,
                          {"GITHUB_EVENT_NAME": "issues", "GITHUB_EVENT_PATH": str(event_path)})
            self.assertEqual(self.page(), before)
            self.assertFalse(self.archive(1).exists())
            self.assertTrue(any(p == self.api.fail_path for p, _, _ in self.api.requests))
            self.assertFalse(any(q.get("page") == ["3"] for p, q, _ in self.api.requests
                                 if p.endswith("/issues/comments")))

    def test_bulk_issue_url_must_be_exact_repo_origin_and_issue_path(self):
        item = issue(1)
        self.api.issues = [item]
        expected = f"{self.api.base}/repos/{REPOSITORY}/issues/1"
        values = [None, "", "https://attacker.invalid/repos/Octo-user/MyRepo/issues/1",
                  self.api.base + f"/repos/{REPOSITORY}/issues/1/comments",
                  self.api.base + "/repos/other/MyRepo/issues/1",
                  self.api.base.replace("127.0.0.1", "localhost") + f"/repos/{REPOSITORY}/issues/1",
                  self.api.base + f"/repos/{REPOSITORY}/issues/0",
                  self.api.base + f"/repos/{REPOSITORY}/issues/01",
                  self.api.base + f"/repos/{REPOSITORY}/issues/1?x=1",
                  expected + "#fragment", expected + "%2Fsecret", expected + "\n"]
        before = self.page()
        for value in values:
            with self.subTest(value=value):
                self.api.requests.clear()
                bad = comment(1, "invalid")
                bad["issue_url"] = value
                self.api.comments = {1: [bad]}
                result = self.run_sync(ok=False)
                self.assertIn("Invalid GitHub comment issue_url", result.stderr)
                self.assertNotIn(TOKEN, result.stderr + result.stdout)
                self.assertEqual(self.page(), before)
                self.assertTrue(all(p.endswith(("/issues", "/issues/comments")) for p, _, _ in self.api.requests))

    def test_late_bulk_page_failure_keeps_all_output_untouched(self):
        item = issue(1, state="closed")
        self.api.issues = [item]
        self.api.comments[1] = [comment(n, f"reply {n}") for n in range(1, 4)]
        self.api.fail_path = f"/repos/{REPOSITORY}/issues/comments"
        self.api.fail_page = 2
        before = {path: self.page(path) for path in self.post_paths}
        self.run_sync(self.event(item), ok=False)
        self.assertEqual({path: self.page(path) for path in self.post_paths}, before)
        self.assertFalse(self.archive(1).exists())

    def test_unknown_post_close_archives_via_api_without_rendering(self):
        # A deleted or renamed post is no longer in the current index. Its
        # original form value, including surrounding whitespace, is metadata.
        removed = "  data/contents/blog/removed 日本語 #?%25.html  "
        original = removed.strip()
        self.post_paths.append(original)
        self.reset_pages()
        (self.site / original).unlink()
        self.post_paths.remove(original)
        self.reset_pages()
        closed = issue(41, removed, "snapshot of removed post", state="closed")
        self.api.issues = [closed, issue(42, removed), issue(43, removed, state="closed"),
                           issue(44, POST2, "visible current post")]
        self.api.comments[41] = [comment(101, "before close", 2), comment(102, "at close", 3),
                                 comment(103, "after close", 4)]
        self.run_sync(self.event(closed))
        saved = json.loads(self.archive(41).read_text(encoding="utf-8"))
        self.assertEqual(saved["post_path"], removed)
        self.assertEqual(saved["issue"]["body"], closed["body"])
        self.assertEqual(saved["issue"]["html_url"], f"https://github.com/{REPOSITORY}/issues/41")
        self.assertEqual([entry["id"] for entry in saved["comments"]], [101, 102])
        self.assertEqual([entry["body"] for entry in saved["comments"]], ["before close", "at close"])
        self.assertTrue(any(p.endswith("/issues/comments") and q.get("page") == ["2"]
                            for p, q, _ in self.api.requests))
        self.assertFalse(any(p.endswith(("/issues/42/comments", "/issues/43/comments"))
                             for p, _, _ in self.api.requests))
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, _, auth in self.api.requests))
        for path in self.post_paths:
            for number in (41, 42, 43):
                self.assertNotIn(f"Thread {number}", self.page(path))
        self.assertIn("visible current post", self.page(POST2))
        self.assertFalse((self.site / original).exists())
        self.assertEqual([p.name for p in self.archive(41).parent.iterdir()], ["issue-41.json"])

    def test_unknown_close_comment_api_failure_leaves_files_untouched(self):
        item = issue(45, "data/contents/blog/deleted.html", state="closed")
        self.api.issues = [item]
        endpoint = f"/repos/{REPOSITORY}/issues/comments"
        for failure in ("fail_path", "malformed_path"):
            with self.subTest(failure=failure):
                self.reset_pages()
                before = {path: self.page(path) for path in self.post_paths}
                self.api.requests.clear()
                setattr(self.api, failure, endpoint)
                self.run_sync(self.event(item), ok=False)
                setattr(self.api, failure, None)
                self.assertTrue(any(p == endpoint for p, _, _ in self.api.requests))
                self.assertFalse(self.archive(45).exists())
                self.assertEqual({path: self.page(path) for path in self.post_paths}, before)
        self.reset_pages()
        self.api.comments[45] = [comment(145, "old archive")]
        self.run_sync(self.event(item))
        first = self.archive(45).read_bytes()
        newer = copy.deepcopy(item)
        newer["closed_at"] = date(7)
        self.reset_pages()
        before = {path: self.page(path) for path in self.post_paths}
        self.api.fail_path = endpoint
        self.run_sync(self.event(newer), ok=False)
        self.assertEqual(self.archive(45).read_bytes(), first)
        self.assertEqual({path: self.page(path) for path in self.post_paths}, before)

    def test_unknown_close_paths_are_only_archive_data(self):
        outside = self.root / "outside.json"
        outside.write_text("DO NOT TOUCH", encoding="utf-8")
        values = ["  ../../outside.json  ", "data/contents/blog/%2e%2e/%2e%2e/outside.json",
                  f"https://github.com/other/repo/blob/main/{quote(POST, safe='/')}",
                  "javascript:alert(1)", "data\\contents\\blog\\removed.html"]
        for number, value in enumerate(values, 51):
            with self.subTest(value=value):
                self.reset_pages()
                item = issue(number, value, state="closed")
                self.api.issues = [item]
                self.api.comments[number] = [comment(number, "archived reply")]
                self.run_sync(self.event(item))
                saved = json.loads(self.archive(number).read_text(encoding="utf-8"))
                self.assertEqual(saved["post_path"], value)
                self.assertEqual(saved["comments"][0]["body"], "archived reply")
                self.assertNotIn(f"Thread {number}", self.page())
                self.assertNotIn(f"Thread {number}", self.page(POST2))
                self.assertTrue(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        self.assertEqual(outside.read_text(encoding="utf-8"), "DO NOT TOUCH")
        self.assertEqual({p.name for p in self.archive(51).parent.iterdir()},
                         {f"issue-{number}.json" for number in range(51, 56)})

    def test_unknown_close_reopen_and_timestamp_protection(self):
        old = issue(61, "data/contents/blog/old.html", state="closed")
        self.api.issues = [old]
        self.api.comments[61] = [comment(201, "original", 2)]
        first_close = self.event(old)
        self.run_sync(first_close)
        first = self.archive(61).read_bytes()
        self.api.comments[61][0]["body"] = "edited"
        self.reset_pages()
        reopened = issue(61, "data/contents/blog/other-missing.html", state="open")
        self.api.issues = [reopened]
        self.api.requests.clear()
        self.run_sync(self.event(reopened, action="reopened"))
        self.assertEqual(self.archive(61).read_bytes(), first)
        self.assertFalse(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        self.reset_pages()
        self.api.requests.clear()
        self.run_sync(first_close)
        self.assertEqual(self.archive(61).read_bytes(), first)
        self.assertFalse(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        newer = issue(61, "data/contents/blog/renamed.html", state="closed")
        newer["closed_at"] = date(7)
        self.reset_pages()
        self.run_sync(self.event(newer))
        second = self.archive(61).read_bytes()
        self.assertNotEqual(first, second)
        saved = json.loads(second)
        self.assertEqual(saved["post_path"], "data/contents/blog/renamed.html")
        self.assertEqual(saved["comments"][0]["body"], "edited")
        self.reset_pages()
        self.api.requests.clear()
        self.run_sync(first_close)
        self.assertEqual(self.archive(61).read_bytes(), second)
        self.assertFalse(any(p.endswith("/issues/comments") for p, _, _ in self.api.requests))
        self.assertNotIn("Thread 61", self.page())
        self.assertNotIn("Thread 61", self.page(POST2))

    def test_close_path_canonicalization_and_ambiguous_fallback(self):
        literal = "data/contents/x/%41.html"
        plain = "data/contents/x/A.html"
        self.post_paths = [POST, POST2, literal, plain]
        self.reset_pages()
        known_url = f"  https://github.com/{OWNER}/{REPO}/blob/main/{quote(POST, safe='/')}  "
        known = issue(71, known_url, state="closed")
        ambiguous = issue(72, literal, state="closed")
        self.api.issues = [known, ambiguous]
        self.run_sync(self.event(known))
        self.assertEqual(json.loads(self.archive(71).read_text())["post_path"], quote(POST, safe="/"))
        self.assertIn("Thread 71", self.page())
        self.assertNotIn("Thread 72", self.page(literal))
        self.assertNotIn("Thread 72", self.page(plain))
        self.reset_pages()
        self.run_sync(self.event(ambiguous))
        # Do not turn a failed/ambiguous match into a canonical path by lookup.
        self.assertEqual(json.loads(self.archive(72).read_text())["post_path"], literal)
        self.assertNotIn("Thread 72", self.page(literal))
        self.assertNotIn("Thread 72", self.page(plain))

    def test_unknown_close_still_requires_valid_event_issue(self):
        cases = [("labels", [{"name": "other"}]), ("state", "open"),
                 ("pull_request", {"url": "ignored"}), ("created_at", "invalid"),
                 ("closed_at", None), ("title", {"invalid": "title"}),
                 ("user", {"login": ""}), ("number", True),
                 ("body", {"bad": "type"}), ("body", "broken\ud800")]
        for number, (key, value) in enumerate(cases, 81):
            with self.subTest(key=key, value=value):
                self.reset_pages()
                item = issue(number, "data/contents/blog/deleted.html", state="closed")
                item[key] = value
                self.api.requests.clear()
                result = self.run_sync(self.event(item))
                self.assertIn("Skipping invalid close event issue", result.stderr)
                self.assertFalse(self.archive(number).exists())
                self.assertFalse(any("/comments" in p for p, _, _ in self.api.requests))

    def test_unlabelled_other_event_does_not_archive(self):
        old = issue(7, state="closed")
        old["labels"] = [{"name": "other"}]
        self.run_sync(self.event(old))
        self.assertFalse(self.archive(7).exists())
        self.reset_pages()
        old["labels"] = [{"name": "comment-for-post"}]
        self.run_sync(self.event(old), event_name="push")
        self.assertFalse(self.archive(7).exists())

    def test_missing_articles_and_marker_fail_before_network(self):
        (self.site / POST).unlink()
        result = self.run_sync(ok=False)
        self.assertIn("Cannot read generated article", result.stderr)
        self.assertEqual(self.api.requests, [])
        self.reset_pages()
        (self.site / POST).write_text("no placeholder", encoding="utf-8")
        self.run_sync(ok=False)
        self.assertEqual(self.api.requests, [])
        self.reset_pages()
        (self.site / POST).write_text(sync.MARKER * 2, encoding="utf-8")
        self.run_sync(ok=False)

    def test_comment_marker_preserves_rawhtml_and_close_archive(self):
        rawhtml = (
            '<article data-label="日本語 &amp; 原文">\r\n'
            '<script>const marker = "<!-- ISSUE_COMMENTS -->";\n'
            'const fake = \'<div class="main-comments-list"><!-- ISSUE_COMMENTS --></div>\';</script>\r\n'
            '<!-- ISSUE_COMMENTS -->\r\n'
            '<p data-marker="<!-- ISSUE_COMMENTS -->">Same name &amp; text</p>\r\n'
            '<textarea><!-- ISSUE_COMMENTS --></textarea>\r\n'
            '</article>\r\n'
        )
        before = ('<!DOCTYPE html>\r\n<html><body>\r\n' + rawhtml +
                  '<div class="other main-comments-list last">\r\n<div><em>Keep me</em>')
        after = '</div>\r\n</div>\r\n<footer>閉じタグ &amp; intact</footer></body></html>\r\n'
        source = before + sync.MARKER + after
        (self.site / POST).write_bytes(source.encode("utf-8"))

        item = issue(72, body='Unsafe <script>alert("issue-xss")</script> **safe**', state="closed")
        reply = comment(721, 'Reply <img src=x onerror="alert(1)">')
        self.api.issues = [item]
        self.api.comments[72] = [reply]
        self.run_sync(self.event(item))

        public, _, body = sync.issue_public(item, OWNER, REPO, {POST: quote(POST, safe="/")})
        thread = sync.render_thread(public, body, sync.comments_public([reply], public))
        section = f'<section class="issue-comments" aria-label="GitHub コメント">\n{thread}\n</section>'
        result = (self.site / POST).read_bytes().decode("utf-8")
        self.assertEqual(result, before + section + after)
        self.assertIn(rawhtml, result)
        self.assertNotIn("<script", section.lower())
        self.assertNotIn("<img", section.lower())
        self.assertNotIn("onerror", section.lower())
        self.assertIn("<strong>safe</strong>", section)
        saved = json.loads(self.archive(72).read_text(encoding="utf-8"))
        self.assertEqual(saved["issue"]["body"], item["body"])
        self.assertEqual(saved["comments"][0]["body"], reply["body"])

        # With no threads the same single marker is removed, not any raw HTML.
        self.reset_pages()
        (self.site / POST).write_bytes(source.encode("utf-8"))
        self.api.issues = []
        self.run_sync()
        self.assertEqual((self.site / POST).read_bytes(), (before + after).encode("utf-8"))

    def test_svg_rawhtml_preserved_with_comment_sync_and_close_archive(self):
        rawhtml = (
            '<svg xmlns="http://www.w3.org/2000/svg"><title/><rect width="5" height="5"/></svg>\r\n'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<title>図のタイトル</title><desc/><g><svg><title/><rect width="1" height="2"/></svg></g>'
            '<!-- ISSUE_COMMENTS -->'
            '<script>const fake = "<!-- ISSUE_COMMENTS -->";'
            "const div = '<div class=\"main-comments-list\"><!-- ISSUE_COMMENTS --></div>';"
            '</script><style>/* <!-- ISSUE_COMMENTS --> */</style>'
            '</svg>\r\n'
            '<svg><foreignObject><div><title/>ignored <!-- ISSUE_COMMENTS -->'
            '</title><p>HTML inside SVG</p></div></foreignObject></svg>\r\n'
        )
        before = '<!doctype html><html><body>\r\n' + rawhtml + '<div class="main-comments-list"><div>前'
        after = '後</div></div></body></html>\r\n'
        source = before + sync.MARKER + after
        (self.site / POST).write_bytes(source.encode("utf-8"))
        item = issue(74, body='Good **comment** <svg onload="alert(1)">', state="closed")
        reply = comment(741, '[bad](javascript:alert(1)) <script>alert(2)</script>')
        self.api.issues = [item]
        self.api.comments[74] = [reply]
        self.run_sync(self.event(item))

        public, _, body = sync.issue_public(item, OWNER, REPO, {POST: quote(POST, safe="/")})
        thread = sync.render_thread(public, body, sync.comments_public([reply], public))
        section = f'<section class="issue-comments" aria-label="GitHub コメント">\n{thread}\n</section>'
        result = (self.site / POST).read_bytes().decode("utf-8")
        self.assertEqual(result, before + section + after)
        self.assertIn(rawhtml, result)
        self.assertIn('<strong>comment</strong>', section)
        self.assertNotIn('<svg', section.lower())
        self.assertNotIn('<script', section.lower())
        self.assertNotIn('javascript:', section.lower())
        saved = json.loads(self.archive(74).read_text(encoding="utf-8"))
        self.assertEqual(saved["issue"]["body"], item["body"])
        self.assertEqual(saved["comments"][0]["body"], reply["body"])

        # The empty-thread path must also preserve every byte of the rawhtml.
        self.reset_pages()
        (self.site / POST).write_bytes(source.encode("utf-8"))
        self.api.issues = []
        self.run_sync()
        self.assertEqual((self.site / POST).read_bytes(), (before + after).encode("utf-8"))

    def test_marker_namespace_and_self_closing_rules(self):
        m = sync.MARKER
        variants = {
            "svg title and nested svg": '<svg><title/><g><svg><title/><rect/></svg></g></svg>',
            "svg nonempty title and desc": '<svg><title>Title</title><desc>Description</desc><rect/></svg>',
            "svg self-closing script and style": '<svg><script/><style/><title/><rect/></svg>',
            "foreign cdata": '<svg><![CDATA[' + m + ' <title/>]]><rect/></svg>',
            "mathml nested svg integration": '<math><mtext><svg><title/><rect/></svg></mtext></math>',
            "mathml annotation xml svg": '<math><annotation-xml><svg><title/><rect/></svg></annotation-xml></math>',
            "mathml html annotation": '<math><annotation-xml encoding="TEXT/HTML"><div>'
                                       '<title/>' + m + '</title></div></annotation-xml></math>',
            "svg foreignobject html": '<svg><foreignObject><div><title/>' + m + '</title>'
                                      '<svg><title/><rect/></svg></div></foreignObject></svg>',
            "svg title integration": '<svg><title><b>html</b><svg><title/></svg></title></svg>',
            "html xmlns does not switch namespace": '<div xmlns="http://www.w3.org/2000/svg">'
                                                    '<title/>' + m + '</title></div>',
            "svg ignores misleading xmlns": '<svg xmlns="http://www.w3.org/1999/xhtml"><title/><rect/></svg>',
            "svg foreign comment": '<svg>' + m + '<title/><rect/></svg>',
            "svg script and style": '<svg><script>const x = "' + m + '";'
                                    'const y = \'<div class="main-comments-list">' + m + '</div>\';'
                                    '</script><style>/* ' + m + ' */</style><title/></svg>',
            "svg script cdata": '<svg><script><![CDATA[const close = "</script>";'
                                'const fake = \'<div class="main-comments-list">' + m + '</div>\';'
                                ']]></script><title/></svg>',
            "svg style cdata": '<svg><style><![CDATA[/* </style> <div class="main-comments-list">'
                               + m + '</div> */]]></style><title/></svg>',
            "svg script comment": '<svg><script><!-- </script><div class="main-comments-list">'
                                  'x</div> --></script><title/></svg>',
            "svg style comment": '<svg><style><!-- </style><div class="main-comments-list">'
                                 'x</div> --></style><title/></svg>',
        }
        for name, prefix in variants.items():
            with self.subTest(name=name):
                source = prefix + '<div class="main-comments-list"><div>' + m + '</div></div>'
                self.assertEqual(sync.comment_marker_offset(source, POST), source.rindex(m))
                # The legacy no-container path must also ignore foreign comments
                # and script/HTML-rawtext strings, preserving the unique marker.
                legacy = prefix + m
                self.assertEqual(sync.comment_marker_offset(legacy, POST), legacy.rindex(m))

    def test_self_closing_html_rawtext_and_foreign_fake_markers_are_not_placeholders(self):
        m = sync.MARKER
        html_raw = ("script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript")
        for tag in html_raw:
            with self.subTest(html_tag=tag):
                # The slash on an HTML non-void element is ignored by browsers.
                prefix = f'<{tag}/>' + m + f'</{tag}>'
                self.assertEqual(sync.comment_marker_offset(prefix + m, POST), len(prefix))
                with self.assertRaises(sync.SyncError):
                    sync.comment_marker_offset(prefix, POST)
        fake_only = [
            '<svg><script>let x = "' + m + '";</script></svg>',
            '<svg><style>/* ' + m + ' */</style></svg>',
            '<svg>' + m + '<title/></svg>',
            '<math>' + m + '</math>',
            '<svg><foreignObject><script/>let x = "' + m + '";</script></foreignObject></svg>',
            '<math><mtext><title/>' + m + '</title></mtext></math>',
        ]
        for source in fake_only:
            with self.subTest(source=source), self.assertRaises(sync.SyncError):
                sync.comment_marker_offset(source, POST)

    def test_svg_with_invalid_markers_preserves_existing_archive_before_network(self):
        item = issue(75, state="closed")
        self.api.issues = [item]
        self.api.comments[75] = [comment(751, "archived reply")]
        self.run_sync(self.event(item), run_id=1)
        archived = self.archive(75).read_bytes()
        self.reset_pages()
        other_page = (self.site / POST2).read_bytes()
        m = sync.MARKER
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><title/><rect width="5" height="5"/></svg>'
        invalid = {
            "missing": svg + '<div class="main-comments-list">none</div>' + m,
            "multiple": svg + '<div class="main-comments-list">' + m + m + '</div>',
            "two containers": svg + '<div class="main-comments-list">' + m + '</div>'
                                     '<div class="main-comments-list"></div>',
            "foreign script only": '<div class="main-comments-list">' + svg +
                                   '<svg><script>const x = "' + m + '";</script></svg></div>' + m,
            "foreign comment only": '<div class="main-comments-list">' + svg +
                                    '<svg>' + m + '</svg></div>' + m,
            "larger comment": svg + '<div class="main-comments-list"><!-- outer ' + m + '</div>' + m,
        }
        for name, source in invalid.items():
            with self.subTest(name=name):
                self.api.requests.clear()
                (self.site / POST).write_bytes(source.encode("utf-8"))
                result = self.run_sync(self.event(item), run_id=2, ok=False)
                self.assertIn("exactly one", result.stderr)
                self.assertEqual((self.site / POST).read_bytes(), source.encode("utf-8"))
                self.assertEqual((self.site / POST2).read_bytes(), other_page)
                self.assertEqual(self.archive(75).read_bytes(), archived)
                self.assertEqual(self.api.requests, [])

    def test_legacy_marker_must_be_a_unique_html_comment(self):
        prefix = ('<script>let marker = "<!-- ISSUE_COMMENTS -->";</script>\n'
                  '<script/>let another = "<!-- ISSUE_COMMENTS -->";</script>\n'
                  '<style>/* <!-- ISSUE_COMMENTS --> */</style>\n'
                  '<textarea><!-- ISSUE_COMMENTS --></textarea>\n'
                  '<p title="<!-- ISSUE_COMMENTS -->">文</p>\n'
                  '<!-- outer <!-- ISSUE_COMMENTS -->\n')
        suffix = '\n<p>after</p></body></html>'
        source = prefix + sync.MARKER + suffix
        (self.site / POST).write_text(source, encoding="utf-8")
        self.run_sync()
        self.assertEqual(self.page(), prefix + suffix)

    def test_comment_list_marker_validation_fails_before_network_or_writes(self):
        m = sync.MARKER
        cases = {
            "missing inside despite outside": m + '<div class="main-comments-list">empty</div>',
            "multiple inside": '<div class="main-comments-list">' + m + '<div>' + m + '</div></div>',
            "script string inside": '<div class="main-comments-list"><script>let x = "' + m + '";</script></div>' + m,
            "attribute string inside": '<div class="main-comments-list"><p title="' + m + '">x</p></div>' + m,
            "larger html comment inside": '<div class="main-comments-list"><!-- prefix ' + m + '</div>' + m,
            "two containers": '<div class="main-comments-list">' + m + '</div><div class="main-comments-list"></div>',
            "nested containers": '<div class="main-comments-list"><div class="main-comments-list">' + m + '</div></div>',
            "legacy script only": '<script>let x = "' + m + '";</script>',
            "legacy multiple comments": m + '<div>' + m + '</div>',
            "class is not a token": '<div class="main-comments-list-extra">' + m + '</div>' + m,
            "non ascii space is not class separator": '<div class="other\u00a0main-comments-list">' + m + '</div>' + m,
        }
        item = issue(73, state="closed")
        self.api.issues = [item]
        second_before = (self.site / POST2).read_bytes()
        for name, source in cases.items():
            with self.subTest(name=name):
                self.api.requests.clear()
                (self.site / POST).write_text(source, encoding="utf-8")
                result = self.run_sync(self.event(item), ok=False)
                self.assertIn("exactly one", result.stderr)
                self.assertEqual((self.site / POST).read_text(encoding="utf-8"), source)
                self.assertEqual((self.site / POST2).read_bytes(), second_before)
                self.assertEqual(self.api.requests, [])
                self.assertFalse(self.archive(73).exists())

    def test_comment_list_class_tokens_and_raw_text_elements(self):
        m = sync.MARKER
        raw = ''.join(f'<{tag}>{m}<div class="main-comments-list">{m}</div></{tag}>'
                      for tag in ("script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript"))
        source = (raw + '<div CLASS="extra\tmain-comments-list\nlast" class="ignored">'
                  '<div>inner</div>' + m + '</div>' + m)
        offset = sync.comment_marker_offset(source, POST)
        expected = source.index(m, len(raw))
        self.assertEqual(offset, expected)
        self.assertEqual(source[:offset] + "RENDERED" + source[offset + len(m):],
                         raw + '<div CLASS="extra\tmain-comments-list\nlast" class="ignored">'
                         '<div>inner</div>RENDERED</div>' + m)

    def test_api_and_pagination_errors_fail_without_modifying_pages(self):
        self.api.issues = [issue(1)]
        before = self.page()
        issue_endpoint = f"/repos/{REPOSITORY}/issues"
        comment_endpoint = issue_endpoint + "/comments"
        for failing in [issue_endpoint, comment_endpoint]:
            with self.subTest(failing=failing):
                self.api.fail_path = failing
                self.run_sync(ok=False)
                self.assertEqual(self.page(), before)
        self.api.fail_path = None
        self.api.malformed_path = issue_endpoint
        self.run_sync(ok=False)
        self.assertEqual(self.page(), before)
        self.api.malformed_path = None
        for bad_link in ['<https://attacker.invalid/?page=2>; rel="next"',
                         '<' + self.api.base + '/x?page=2>; rel="next"',
                         '<broken>; rel="next"', 'not a link']:
            with self.subTest(link=bad_link):
                self.api.bad_link = bad_link
                self.run_sync(ok=False)
                self.assertEqual(self.page(), before)

    def test_pagination_next_exact_path_origin_and_filters(self):
        self.api.issues = [issue(1)]
        before = self.page()
        endpoint = f"/repos/{REPOSITORY}/issues"
        filters = "state=all&labels=comment-for-post&per_page=100&page=2&after=cursor"
        urls = [self.api.base + f"/repos/{REPOSITORY}/issues/1/comments?" + filters,
                self.api.base + "/repos/attacker/repo/issues?" + filters,
                self.api.base.replace("http://", "https://") + endpoint + "?" + filters,
                self.api.base.replace("127.0.0.1", "localhost") + endpoint + "?" + filters,
                f"http://127.0.0.1:{self.api.server.server_port + 1}" + endpoint + "?" + filters,
                self.api.base.replace("127.0.0.1", "user@127.0.0.1") + endpoint + "?" + filters,
                self.api.base + endpoint + "?state=open&labels=comment-for-post&per_page=100&page=2",
                self.api.base + endpoint + "?state=all&labels=other&per_page=100&page=2",
                self.api.base + endpoint + "?state=all&labels=comment-for-post&per_page=1&page=2",
                self.api.base + endpoint + "?state=all&labels=comment-for-post&per_page=100&page=2&page=3"]
        for url in urls:
            with self.subTest(url=url):
                self.api.requests.clear()
                self.api.bad_link = f'<{url}>; rel="next"'
                self.api.bad_link_path = endpoint
                result = self.run_sync(ok=False)
                self.assertEqual(len(self.api.requests), 1)
                self.assertEqual(self.api.requests[0][0], endpoint)
                self.assertNotIn(TOKEN, result.stderr + result.stdout)
                self.assertEqual(self.page(), before)

        self.api.requests.clear()
        bulk = endpoint + "/comments"
        self.api.bad_link_path = bulk
        self.api.bad_link = (f'<{self.api.base}{endpoint}/1/comments?'
                             'sort=created&direction=asc&per_page=100&page=2&after=cursor>; rel="next"')
        self.run_sync(ok=False)
        self.assertEqual([p for p, _, _ in self.api.requests], [endpoint, bulk])
        self.assertEqual(self.page(), before)

    def test_authentication_and_late_page_failure_are_fatal(self):
        self.api.issues = [issue(1), issue(2), issue(3)]
        before = self.page()
        self.run_sync(ok=False, token="")
        self.assertEqual(self.api.requests, [])
        self.run_sync(ok=False, token="wrong-token")
        self.assertEqual(self.page(), before)
        self.api.fail_path = f"/repos/{REPOSITORY}/issues"
        self.api.fail_page = 2
        self.run_sync(ok=False)
        self.assertEqual(self.page(), before)

    def test_unsafe_index_is_fatal_without_reading_outside_site(self):
        outside = self.root / "outside.html"
        outside.write_text("OUTSIDE", encoding="utf-8")
        for path in ("../outside.html", "data/contents/../../outside.html", "data/contents/%2e%2e/%2e%2e/outside.html"):
            with self.subTest(path=path):
                (self.site / "index.json").write_text(json.dumps({"posts": [{"path": path}]}), encoding="utf-8")
                self.run_sync(ok=False)
        self.assertEqual(outside.read_text(), "OUTSIDE")

    def test_symlinks_cannot_escape_archive_or_article_paths(self):
        item = issue(3, state="closed")
        self.api.issues = [item]
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "issue-3.json").write_text("DO NOT TOUCH", encoding="utf-8")
        comments_dir = self.site / "data" / "comments"
        comments_dir.mkdir()
        (comments_dir / "archive").symlink_to(outside, target_is_directory=True)
        self.run_sync(self.event(item), ok=False)
        self.assertEqual((outside / "issue-3.json").read_text(), "DO NOT TOUCH")
        (comments_dir / "archive").unlink()
        (comments_dir / "archive").mkdir()
        (comments_dir / "archive" / "issue-3.json").symlink_to(outside / "issue-3.json")
        self.run_sync(self.event(item), ok=False)
        self.assertEqual((outside / "issue-3.json").read_text(), "DO NOT TOUCH")
        (comments_dir / "archive" / "issue-3.json").unlink()
        (self.site / POST).unlink()
        (self.site / POST).symlink_to(outside / "issue-3.json")
        self.run_sync(ok=False)
        self.assertEqual((outside / "issue-3.json").read_text(), "DO NOT TOUCH")

    def test_bad_existing_archive_is_not_destroyed(self):
        item = issue(10, state="closed")
        self.api.issues = [item]
        path = self.archive(10)
        path.parent.mkdir(parents=True)
        path.write_text('{"broken": true}', encoding="utf-8")
        self.run_sync(self.event(item), ok=False)
        self.assertEqual(path.read_text(), '{"broken": true}')

    def test_form_preserves_markdown(self):
        markdown = "\n    indented\n\n### 対象記事パス (Post Path)\n\nline  \n\n"
        path, extracted = sync.extract_form(form(POST, markdown))
        self.assertEqual(path, POST)
        self.assertEqual(extracted, "\n" + markdown)


if __name__ == "__main__":
    unittest.main()
