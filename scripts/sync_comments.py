#!/usr/bin/env python3
"""Render GitHub issue comments into a prepared site; snapshot close events only.

Requires cmarkgfm and nh3 from scripts/requirements.txt. The optional GitHub
Actions event is read from GITHUB_EVENT_NAME/GITHUB_EVENT_PATH. Archive JSON has
an ``issue`` object, ``post_path`` and a ``comments`` array; only public fields
are copied. No issue is created or modified by this program.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import stat
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import cmarkgfm
import nh3
from cmarkgfm.cmark import Options

MARKER = "<!-- ISSUE_COMMENTS -->"
LABEL = "comment-for-post"
PATH_HEADING = "### 対象記事パス (Post Path)"
COMMENT_HEADING = "### コメント内容 (Comment)"
MAX_PAGES = 10000
BULK_COMMENT_LIMIT = 30000
JST = timezone(timedelta(hours=9), name="JST")


class SyncError(Exception):
    """A site, network, API or pagination error that must fail the build."""


class CommentMarkerParser(HTMLParser):
    """Locate exact marker comments without serializing or changing the HTML."""

    # Older Python versions only recognize script/style as raw text. These
    # HTML elements cannot contain actual comment or div nodes. SVG/MathML
    # elements with the same names must not automatically enter rawtext.
    CDATA_CONTENT_ELEMENTS = ("script", "style", "textarea", "title", "xmp",
                              "iframe", "noembed", "noframes", "noscript")
    HTML_VOID = frozenset(("area", "base", "basefont", "bgsound", "br", "col", "embed", "hr", "img",
                           "input", "keygen", "link", "meta", "param", "source", "track", "wbr"))
    # HTML's foreign-content breakout tags are reprocessed in HTML. The font
    # tag is a breakout only if it has one of the attributes checked below.
    FOREIGN_BREAKOUT = frozenset(("b", "big", "blockquote", "body", "br", "center", "code", "dd", "div",
                                  "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6",
                                  "head", "hr", "i", "img", "li", "listing", "menu", "meta", "nobr",
                                  "ol", "p", "pre", "ruby", "s", "small", "span", "strong", "strike",
                                  "sub", "sup", "table", "tt", "u", "ul", "var"))
    MATH_TEXT_POINTS = frozenset(("mi", "mo", "mn", "ms", "mtext"))

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        # HTMLParser.getpos() counts lines at LF, with columns in characters.
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", source)]
        self.div_depth = 0
        self.container_depths = []
        self.container_count = 0
        self.in_container = []
        self.all_markers = []
        # Open elements as (lowercase name, namespace, HTML integration point).
        # xmlns attributes do not change namespaces in an HTML document.
        self.elements = []
        self.suppress_foreign_rawtext = False
        self.foreign_script_or_style = None

    def parse_starttag(self, i):
        # HTMLParser itself enters CDATA mode *after* handle_starttag, without
        # considering namespaces. Undo that for foreign elements, except
        # script/style whose contents must never supply comment markers.
        self.suppress_foreign_rawtext = False
        self.foreign_script_or_style = None
        result = super().parse_starttag(i)
        if self.suppress_foreign_rawtext and self.cdata_elem:
            self.clear_cdata_mode()
        elif self.foreign_script_or_style and self.cdata_elem:
            # Foreign CDATA sections and comments may contain a literal closing
            # tag. Consume them whole before looking for the actual end tag.
            self.interesting = re.compile(r'<!\[CDATA\[|<!--|</%s(?=[\t\n\r\f />])' %
                                          self.foreign_script_or_style, re.IGNORECASE | re.ASCII)
        return result

    def pop_elements(self, index):
        for tag, namespace, _ in reversed(self.elements[index:]):
            if namespace == "html" and tag == "div":
                if self.container_depths and self.container_depths[-1] == self.div_depth:
                    self.container_depths.pop()
                self.div_depth -= 1
        del self.elements[index:]

    def start_element(self, tag, attrs, self_closing):
        parent = self.elements[-1] if self.elements else None
        in_foreign = parent and parent[1] != "html"
        integration = parent and parent[2]
        math_text = parent and parent[1] == "math" and parent[0] in self.MATH_TEXT_POINTS
        html_child = integration or (math_text and tag not in {"mglyph", "malignmark"})
        font_breakout = tag == "font" and any(name in {"color", "face", "size"} for name, _ in attrs)
        if in_foreign and not html_child and (tag in self.FOREIGN_BREAKOUT or font_breakout):
            # Pop foreign elements only up to the nearest integration point.
            while self.elements and self.elements[-1][1] != "html" and not self.elements[-1][2] and not (
                    self.elements[-1][1] == "math" and self.elements[-1][0] in self.MATH_TEXT_POINTS):
                self.pop_elements(len(self.elements) - 1)
            parent = self.elements[-1] if self.elements else None
            in_foreign = parent and parent[1] != "html"
            html_child = bool(in_foreign)

        if not in_foreign or html_child:
            namespace = "svg" if tag == "svg" else "math" if tag == "math" else "html"
        elif parent[1] == "math" and parent[0] == "annotation-xml" and tag == "svg":
            namespace = "svg"
        else:
            namespace = parent[1]

        self.suppress_foreign_rawtext = namespace != "html" and tag not in {"script", "style"}
        if namespace != "html" and tag in {"script", "style"} and not self_closing:
            self.foreign_script_or_style = tag
        if namespace == "html" and tag == "div":
            self.div_depth += 1
            # The first occurrence is the one used by HTML for duplicate attrs.
            css = next((value for name, value in attrs if name == "class"), None) or ""
            if "main-comments-list" in re.split(r"[\t\n\f\r ]+", css):
                self.container_count += 1
                self.container_depths.append(self.div_depth)

        if (namespace == "html" and tag in self.HTML_VOID) or (namespace != "html" and self_closing):
            return namespace
        encoding = next((value for name, value in attrs if name == "encoding"), None) or ""
        html_integration = ((namespace == "svg" and tag in {"foreignobject", "desc", "title"}) or
                            (namespace == "math" and tag == "annotation-xml" and
                             encoding.lower() in {"text/html", "application/xhtml+xml"}))
        self.elements.append((tag, namespace, html_integration))
        return namespace

    def handle_starttag(self, tag, attrs):
        self.start_element(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        namespace = self.start_element(tag, attrs, self_closing=True)
        if namespace == "html" and tag in self.CDATA_CONTENT_ELEMENTS:
            # In HTML, the self-closing slash on a non-void tag is ignored.
            self.set_cdata_mode(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.elements) - 1, -1, -1):
            if self.elements[index][0] == tag:
                self.pop_elements(index)
                break

    def handle_comment(self, data):
        line, column = self.getpos()
        offset = self.line_starts[line - 1] + column
        # Checking the source as well as the comment data rejects malformed
        # comment syntax and a marker embedded in a larger HTML comment.
        if data != MARKER[4:-3] or not self.source.startswith(MARKER, offset):
            return
        # Foreign SVG/MathML comment nodes are part of the embedded graphic,
        # not the HTML placeholder to be replaced with a comment section.
        if self.elements and self.elements[-1][1] != "html":
            return
        self.all_markers.append(offset)
        if self.container_depths:
            self.in_container.append(offset)


def comment_marker_offset(source: str, path: str) -> int:
    parser = CommentMarkerParser(source)
    parser.feed(source)
    parser.close()
    # A second container is ambiguous, even if only one contains a marker.
    # For older minimal fixtures with no container, accept exactly one actual
    # marker comment anywhere in the document, not a string in raw HTML.
    markers = parser.in_container if parser.container_count else parser.all_markers
    if parser.container_count > 1 or len(markers) != 1:
        raise SyncError(f"Generated article must contain exactly one {MARKER} in its comment list: {path}")
    return markers[0]


def warn(message: str) -> None:
    print(f"sync_comments: {message}", file=sys.stderr)


def repository_parts(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if (len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", parts[0])
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1]) or parts[1] in {".", ".."}):
        raise SyncError("--repository must be a valid OWNER/REPO")
    return parts[0], parts[1]


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid timestamp") from exc


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid text")
    # JSON can represent isolated UTF-16 surrogates. They cannot be written as
    # UTF-8 and must invalidate only this issue, not the entire site build.
    value.encode("utf-8", errors="strict")
    return value


def positive_int(value: object) -> bool:
    # GitHub IDs fit in an unsigned 64-bit value; unbounded JSON integers must
    # not become enormous endpoint or archive filenames.
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < (1 << 64)


def safe_path(value: str) -> str:
    """Validate a decoded article path without normalizing away traversal."""
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("unsafe path characters")
    text(value)
    parts = value.split("/")
    if (len(parts) < 4 or parts[:2] != ["data", "contents"]
            or any(part in {"", ".", ".."} for part in parts)
            or not parts[-1].endswith(".html")):
        raise ValueError("path must be an article under data/contents")
    return value


def decode(value: str) -> str:
    return unquote(value, encoding="utf-8", errors="strict")


def open_dir_chain(path: Path) -> int:
    """Open every component without following symlinks, including site root."""
    absolute = os.path.abspath(os.fspath(path))
    fd = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(absolute).parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def dir_at(root_fd: int, parts: list[str], create: bool = False) -> int:
    fd = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(root_fd: int, path: str, optional: bool = False) -> bytes | None:
    parts = path.split("/")
    try:
        parent = dir_at(root_fd, parts[:-1])
    except FileNotFoundError:
        if optional:
            return None
        raise
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            if optional:
                return None
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SyncError(f"Not a regular file: {path}")
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                return stream.read()
        finally:
            if fd != -1:
                os.close(fd)
    finally:
        os.close(parent)


def write_file(root_fd: int, path: str, content: bytes, create_dirs: bool = False) -> None:
    parts = path.split("/")
    parent = dir_at(root_fd, parts[:-1], create=create_dirs)
    temp = ".sync-comments-" + secrets.token_hex(16)
    try:
        try:
            existing = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise SyncError(f"Refusing non-regular file or symlink: {path}")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            # Rename replaces a link itself, never its target, if a race occurs.
            os.replace(temp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass
    finally:
        os.close(parent)


def load_posts(root_fd: int) -> dict[str, str]:
    try:
        index = json.loads(read_file(root_fd, "index.json"))
        posts = index["posts"]
        if not isinstance(posts, list):
            raise ValueError("posts must be a list")
        result = {}
        for item in posts:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("invalid post entry")
            decoded = safe_path(decode(item["path"]))
            if decoded in result:
                raise ValueError("duplicate article path")
            result[decoded] = item["path"]
        return result
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise SyncError(f"Invalid index.json: {exc}") from exc


def extract_form(body: object, *, preserve_path_whitespace: bool = False,
                 allow_empty_comment: bool = False) -> tuple[str, str]:
    body = text(body)
    lines = body.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.rstrip("\r\n").rstrip(" \t") == PATH_HEADING), None)
    if start is None:
        raise ValueError("missing post path heading")
    comment_index = next((i for i in range(start + 1, len(lines))
                          if lines[i].rstrip("\r\n").rstrip(" \t") == COMMENT_HEADING), None)
    if comment_index is None:
        raise ValueError("missing comment heading")
    path_lines = [line.rstrip("\r\n") for line in lines[start + 1:comment_index] if line.strip()]
    if len(path_lines) != 1:
        raise ValueError("post path must be a single line")
    # Keep every byte of the Markdown after the heading, including blank lines,
    # indentation, trailing whitespace and any subsequent headings.
    comment = "".join(lines[comment_index + 1:])
    if not allow_empty_comment and (not comment.strip() or comment.strip() == "_No response_"):
        raise ValueError("empty issue comment")
    path = path_lines[0] if preserve_path_whitespace else path_lines[0].strip()
    return path, comment


def match_post(value: str, owner: str, repo: str, posts: dict[str, str]) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("unsafe path or URL")
    is_url = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value) or value.startswith("//"))
    if is_url:
        try:
            url = urlsplit(value)
            if (url.scheme.lower() != "https" or url.username is not None or url.password is not None
                    or url.port not in (None, 443) or url.query or url.fragment):
                raise ValueError("invalid URL origin, query or fragment")
            host = (url.hostname or "").lower()
            pieces = url.path.lstrip("/").split("/")
            if host == "github.com":
                if (len(pieces) < 5 or pieces[0].lower() != owner.lower()
                        or pieces[1].lower() != repo.lower() or pieces[2] != "blob"
                        or pieces[3] not in {"main", "master"}):
                    raise ValueError("not a supported repository blob URL")
                value = "/".join(pieces[4:])
            elif host == owner.lower() + ".github.io":
                if repo.lower() != (owner + ".github.io").lower():
                    if len(pieces) < 2 or pieces[0] != repo:
                        raise ValueError("not this repository's Pages path")
                    pieces = pieces[1:]
                value = "/".join(pieces)
            else:
                raise ValueError("not this repository's GitHub or Pages origin")
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"invalid post URL: {exc}") from exc
    # An absolute URL identifies its file by decoding its path once. For
    # legacy relative paths, also accept already-decoded literal '%' names,
    # rejecting ambiguity between encoded and decoded spellings.
    candidates = set()
    try:
        decoded = decode(value)
    except UnicodeError:
        # A decoded filename may literally contain a non-UTF-8-looking %XX.
        decoded = None
    if decoded is not None:
        # Do not reinterpret encoded traversal as an innocent literal filename.
        safe_path(decoded)
    for candidate in ((decoded,) if is_url and decoded is not None else (value, decoded)):
        if candidate is None:
            continue
        try:
            candidate = safe_path(candidate)
        except ValueError:
            continue
        if candidate in posts:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError("post path is unsafe, unknown or ambiguous")
    return candidates.pop()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubAPI:
    def __init__(self, base: str, token: str, owner: str, repo: str, env: dict | None = None):
        if not token or any(ord(c) < 32 or ord(c) == 127 for c in token):
            raise SyncError("GITHUB_TOKEN is required and must not contain control characters")
        try:
            parsed = urlsplit(base)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or any(ord(c) <= 32 or ord(c) == 127 for c in base)):
                raise ValueError("invalid base URL")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HTTP is allowed only for a local test API")
            _ = parsed.port
        except ValueError as exc:
            raise SyncError(f"Invalid GITHUB_API_URL: {exc}") from exc
        self.base = base.rstrip("/")
        self.origin = self.url_origin(parsed)
        self.token = token
        self.repo_path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        env = os.environ if env is None else env
        repository_id = env.get("GITHUB_REPOSITORY_ID")
        self.repository_id = None
        if repository_id is not None:
            if (not isinstance(repository_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", repository_id)
                    or not positive_int(int(repository_id))):
                raise SyncError("GITHUB_REPOSITORY_ID must be a positive integer")
            actions_repo = env.get("GITHUB_REPOSITORY")
            if actions_repo is not None and (not isinstance(actions_repo, str)
                                             or actions_repo.lower() != f"{owner}/{repo}".lower()):
                raise SyncError("GITHUB_REPOSITORY does not match --repository")
            self.repository_id = int(repository_id)
        self.opener = build_opener(NoRedirect())

    @staticmethod
    def url_origin(parsed):
        port = parsed.port
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
                port if port is not None else (443 if parsed.scheme.lower() == "https" else 80))

    def request(self, url: str) -> Request:
        return Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "greenlog-comment-sync"})

    def trusted_repository_id(self) -> int:
        # Actions supplies the ID; outside Actions, resolve it once via the
        # trusted repository endpoint, never via the untrusted Link target.
        if self.repository_id is None:
            try:
                with self.opener.open(self.request(self.base + self.repo_path), timeout=30) as response:
                    if response.status != 200:
                        raise SyncError(f"GitHub repository API returned HTTP {response.status}")
                    data = json.load(response)
                if not isinstance(data, dict) or not positive_int(data.get("id")):
                    raise ValueError("missing or invalid repository id")
                self.repository_id = data["id"]
            except (HTTPError, URLError, OSError, ValueError, UnicodeError, RecursionError) as exc:
                raise SyncError(f"Cannot resolve trusted GitHub repository ID: {exc}") from exc
        return self.repository_id

    def pagination_path_matches(self, path: str, endpoint: str, expected_path: str) -> bool:
        if path == expected_path:
            return True
        prefix = urlsplit(self.base).path.rstrip("/")
        alias = re.fullmatch(re.escape(prefix) + r"/repositories/([1-9][0-9]{0,19})" + re.escape(endpoint), path)
        if not alias:
            return False
        number = int(alias.group(1))
        return positive_int(number) and number == self.trusted_repository_id()

    def issue_number_from_url(self, value: object) -> int:
        """Validate bulk-comment issue_url as data, never as a request target."""
        try:
            if (not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
                raise ValueError("invalid issue_url")
            parsed = urlsplit(value)
            expected_path = urlsplit(self.base + self.repo_path + "/issues/").path
            if (self.url_origin(parsed) != self.origin or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or not parsed.path.startswith(expected_path)):
                raise ValueError("unexpected issue_url origin or path")
            number_text = parsed.path[len(expected_path):]
            if not re.fullmatch(r"[1-9][0-9]{0,19}", number_text):
                raise ValueError("invalid issue_url number")
            number = int(number_text)
            if not positive_int(number):
                raise ValueError("invalid issue_url number")
            return number
        except (ValueError, UnicodeError) as exc:
            raise SyncError(f"Invalid GitHub comment issue_url: {exc}") from exc

    def get_pages(self, endpoint: str, params: dict[str, str], *, sufficient=None) -> tuple[list, bool]:
        result = []
        bulk_capped = False
        seen_pages = set()
        params = dict(params, per_page="100")
        request_base = self.base + self.repo_path + endpoint
        expected_path = urlsplit(request_base).path
        current = {"page": "1"}
        seen_cursors = {(current.get("page"), current.get("after"))}
        for page in range(1, MAX_PAGES + 1):
            url = request_base + "?" + urlencode({**params, **current})
            try:
                with self.opener.open(self.request(url), timeout=30) as response:
                    if response.status != 200:
                        raise SyncError(f"GitHub API returned HTTP {response.status}")
                    data = json.load(response)
                    link = ", ".join(response.headers.get_all("Link", []))
            except (HTTPError, URLError, OSError, ValueError, UnicodeError, RecursionError) as exc:
                raise SyncError(f"GitHub API request failed for {endpoint} page {page}: {exc}") from exc
            if not isinstance(data, list) or len(data) > 100:
                raise SyncError(f"GitHub API returned an invalid list for {endpoint} page {page}")
            if data:
                digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode("ascii")).digest()
                if digest in seen_pages:
                    raise SyncError(f"GitHub API repeated a pagination page for {endpoint}")
                seen_pages.add(digest)
            result.extend(data)
            next_cursor = None
            if link:
                # Never send credentials to a Link target. Validate its exact
                # origin and endpoint, then reconstruct from trusted components.
                links = re.findall(r'(?:^|,)\s*<([^>]*)>\s*;\s*rel="([^"]+)"\s*(?=,|$)', link)
                if not links or ",".join(f'<{u}>; rel="{r}"' for u, r in links).replace(" ", "") != link.replace(" ", ""):
                    raise SyncError(f"Invalid GitHub pagination Link for {endpoint}")
                for target, rel in links:
                    if "next" not in rel.split():
                        continue
                    if next_cursor is not None:
                        raise SyncError("Multiple next pagination links")
                    try:
                        if any(ord(c) <= 32 or ord(c) == 127 for c in target):
                            raise ValueError("invalid URL characters")
                        dest = urlsplit(urljoin(url, target))
                        query = parse_qs(dest.query, keep_blank_values=True, encoding="utf-8", errors="strict")
                        if (self.url_origin(dest) != self.origin or dest.username is not None or dest.password is not None
                                or dest.fragment
                                or set(query) - (set(params) | {"page", "after"})
                                or any(len(values) != 1 for values in query.values())
                                or any(key in query and query[key] != [val] for key, val in params.items())
                                or not self.pagination_path_matches(dest.path, endpoint, expected_path)):
                            raise ValueError("unexpected pagination link")
                        next_cursor = {key: query[key][0] for key in ("page", "after") if key in query}
                        if not next_cursor:
                            raise ValueError("missing pagination cursor")
                        if "page" in next_cursor and (not re.fullmatch(r"[1-9][0-9]{0,9}", next_cursor["page"])):
                            raise ValueError("invalid page number")
                        if "after" in next_cursor and (not next_cursor["after"] or len(next_cursor["after"]) > 4096
                                or any(ord(c) < 32 or ord(c) == 127 for c in next_cursor["after"])):
                            raise ValueError("invalid after cursor")
                        if ("after" not in next_cursor and "page" in current
                                and int(next_cursor["page"]) != int(current["page"]) + 1):
                            raise ValueError("nonsequential page")
                    except (ValueError, UnicodeError) as exc:
                        raise SyncError(f"Invalid GitHub pagination Link for {endpoint}: {exc}") from exc
            # This endpoint stops at 30,000 even if more comments exist; a
            # numbered probe beyond it returns 422. Issue-specific lists are
            # handled independently and must not inherit this restriction.
            if endpoint == "/issues/comments" and len(result) >= BULK_COMMENT_LIMIT:
                bulk_capped = True
                break
            if next_cursor is None:
                if len(data) < 100 or "after" in current:
                    break
                if sufficient is not None and sufficient(result):
                    break
                # A full page may have omitted Link. Probe a numbered page.
                next_cursor = {"page": str(int(current["page"]) + 1)}
            if not data:
                raise SyncError(f"Empty page has a next link for {endpoint}")
            cursor_key = (next_cursor.get("page"), next_cursor.get("after"))
            if cursor_key in seen_cursors:
                raise SyncError(f"GitHub API repeated a pagination cursor for {endpoint}")
            seen_cursors.add(cursor_key)
            current = next_cursor
        else:
            raise SyncError(f"GitHub pagination exceeded {MAX_PAGES} pages for {endpoint}")
        return result, bulk_capped


def labels_of(issue: dict) -> list[str]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise ValueError("missing labels")
    result = []
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else label
        result.append(text(name))
    return result


def login_of(user: object) -> str:
    if user is None:
        return "ghost"
    if not isinstance(user, dict) or not isinstance(user.get("login"), str) or not user["login"]:
        raise ValueError("invalid author")
    return text(user["login"])


def issue_public(issue: object, owner: str, repo: str, posts: dict[str, str], *, event: bool = False) -> tuple[dict, str | None, str | None]:
    if not isinstance(issue, dict) or "pull_request" in issue:
        raise ValueError("not an issue")
    number = issue.get("number")
    if not positive_int(number) or not isinstance(issue.get("title"), str):
        raise ValueError("invalid number or title")
    text(issue["title"])
    labels = labels_of(issue)
    if LABEL not in labels or issue.get("state") not in {"open", "closed"}:
        raise ValueError("issue has wrong label or state")
    if event and issue["state"] != "closed":
        raise ValueError("close event issue is not closed")
    timestamp(issue.get("created_at"))
    for key in ("updated_at", "closed_at"):
        if issue.get(key) is not None:
            timestamp(issue[key])
    if event:
        timestamp(issue.get("closed_at"))
    if event:
        # A GitHub body may be null after it is cleared. Other non-text values
        # and invalid Unicode remain invalid event data.
        if "body" not in issue:
            raise ValueError("missing issue body")
        body = issue.get("body")
        if body is not None:
            text(body)
        try:
            path, comment = extract_form(body, preserve_path_whitespace=True, allow_empty_comment=True)
        except ValueError:
            path, comment = None, None
        # Archive metadata is never used as a filesystem path. Resolve a
        # uniquely matched post to its index spelling; otherwise preserve the
        # form text, even for deleted, renamed, unsafe or ambiguous paths.
        if path is not None:
            try:
                path = posts[match_post(path.strip(), owner, repo, posts)]
            except ValueError:
                pass
    else:
        # Normal rendering must still match the current index exactly.
        path, comment = extract_form(issue.get("body"))
        path = match_post(path, owner, repo, posts)
    public = {key: issue.get(key) for key in ("number", "title", "body", "state", "created_at", "updated_at", "closed_at")}
    public["user"] = {"login": login_of(issue.get("user"))}
    public["labels"] = labels
    public["html_url"] = f"https://github.com/{owner}/{repo}/issues/{number}"
    return public, path, comment


def comments_public(items: list, issue: dict) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not positive_int(item.get("id")) or not isinstance(item.get("body"), str):
            raise ValueError("invalid issue comment")
        ident = item["id"]
        text(item["body"])
        if ident in seen:
            raise ValueError("duplicate issue comment ID")
        seen.add(ident)
        timestamp(item.get("created_at"))
        if item.get("updated_at") is not None:
            timestamp(item["updated_at"])
        result.append({"id": ident, "body": item["body"], "user": {"login": login_of(item.get("user"))},
                       "created_at": item["created_at"], "updated_at": item.get("updated_at"),
                       "html_url": f'{issue["html_url"]}#issuecomment-{ident}'})
    result.sort(key=lambda comment: (timestamp(comment["created_at"]), comment["id"]))
    return result


def render_markdown(markdown: str, prefix: str) -> str:
    # Default cmark options disable raw HTML and unsafe links; never enable UNSAFE.
    rendered = cmarkgfm.github_flavored_markdown_to_html(markdown, options=Options.CMARK_OPT_FOOTNOTES)
    attrs = deepcopy(nh3.ALLOWED_ATTRIBUTES)
    attrs["input"] = {"type", "checked", "disabled"}
    attrs.setdefault("code", set()).add("class")
    attrs.setdefault("a", set()).update({"title", "id", "class", "data-footnote-ref", "data-footnote-backref",
                                        "data-footnote-backref-idx", "aria-label"})
    attrs.setdefault("img", set()).add("title")
    attrs["li"] = {"id"}
    attrs["sup"] = {"class"}
    attrs["section"] = {"class", "data-footnotes"}

    def filter_attribute(tag, name, value):
        if name == "id":
            expected = "fnref-" if tag == "a" else "fn-" if tag == "li" else None
            return prefix + decode(value) if expected and value.startswith(expected) else None
        if tag == "a" and name == "href" and value.startswith(("#fn-", "#fnref-")):
            return "#" + prefix + decode(value[1:])
        return value

    return nh3.clean(rendered, tags=nh3.ALLOWED_TAGS | {"input", "section"}, attributes=attrs,
                     attribute_filter=filter_attribute,
                     clean_content_tags={"script", "style", "iframe", "object", "embed"},
                     tag_attribute_values={"input": {"type": {"checkbox"}}},
                     set_tag_attribute_values={"input": {"disabled": ""}},
                     url_schemes={"http", "https", "mailto"}, strip_comments=True)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def author_link(author: str) -> str:
    # GitHub users have a single, ASCII login path segment. Unknown/deleted
    # accounts and other names remain text rather than an unverified link.
    if (author.lower() == "ghost" or "--" in author or
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", author)):
        return f'<span class="issue-comment-author">{esc(author)}</span>'
    return (f'<a class="issue-comment-author" href="https://github.com/{esc(author)}">'
            f'{esc(author)}</a>')


def display_jst(value: str) -> str:
    # Keep the API value intact for the <time> element and for archive data.
    utc = timestamp(value)
    try:
        local = utc.astimezone(JST)
        return (f"{local.year:04d}-{local.month:02d}-{local.day:02d} "
                f"{local.hour:02d}:{local.minute:02d}:{local.second:02d} JST")
    except OverflowError:
        # The Python datetime range cannot represent the shifted date. Preserve
        # the original offset instead of dropping the entire comment thread.
        return value


def render_thread(issue: dict, body: str, comments: list[dict]) -> str:
    def message(author, date, url, text, prefix):
        return (f'<div class="issue-comment"><p class="issue-comment-meta">'
                f'{author_link(author)} · '
                f'<a href="{esc(url)}"><time datetime="{esc(date)}">{esc(display_jst(date))}</time></a></p>'
                f'<div class="issue-comment-body">{render_markdown(text, prefix)}</div></div>')
    result = [f'<article class="issue-comment-thread"><h3><a href="{esc(issue["html_url"])}">'
              f'{esc(issue["title"])}</a></h3>',
              message(issue["user"]["login"], issue["created_at"], issue["html_url"], body,
                      f'greenlog-issue-{issue["number"]}-')]
    for comment in comments:
        result.append(message(comment["user"]["login"], comment["created_at"], comment["html_url"], comment["body"],
                              f'greenlog-issue-{issue["number"]}-comment-{comment["id"]}-'))
    result.append("</article>")
    return "\n".join(result)


def event_issue(env: dict, owner: str, repo: str, posts: dict[str, str]):
    if env.get("GITHUB_EVENT_NAME") != "issues":
        return None
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SyncError("GITHUB_EVENT_PATH is required for an issues event")
    try:
        with open(event_path, encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("event must be an object")
    except (OSError, ValueError, UnicodeError) as exc:
        raise SyncError(f"Cannot read GitHub event payload: {exc}") from exc
    if payload.get("action") != "closed":
        return None
    # GitHub includes repository.full_name; if supplied it must match CLI.
    event_repo = payload.get("repository")
    if event_repo is not None and (not isinstance(event_repo, dict) or not isinstance(event_repo.get("full_name"), str)
                                   or event_repo["full_name"].lower() != f"{owner}/{repo}".lower()):
        raise SyncError("GitHub event repository does not match --repository")
    try:
        return (*issue_public(payload.get("issue"), owner, repo, posts, event=True),
                comment_count(payload["issue"]))
    except (TypeError, ValueError, UnicodeError) as exc:
        warn(f"Skipping invalid close event issue: {exc}")
        return None


def github_run_id(env: dict) -> int | None:
    value = env.get("GITHUB_RUN_ID")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value) or not positive_int(int(value)):
        raise SyncError("GITHUB_RUN_ID must be a positive integer")
    return int(value)


def comment_count(issue: dict) -> int | None:
    # bool is an int subclass but not a valid GitHub count.
    count = issue.get("comments")
    return count if type(count) is int and 0 <= count < (1 << 64) else None


def sync(site: Path, repository: str, token: str, api_base: str = "https://api.github.com", env: dict | None = None) -> None:
    owner, repo = repository_parts(repository)
    env = os.environ if env is None else env
    api = GitHubAPI(api_base, token, owner, repo, env)
    root_fd = open_dir_chain(site)
    try:
        posts = load_posts(root_fd)
        pages = {}
        for path in posts:
            try:
                text = read_file(root_fd, path).decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise SyncError(f"Cannot read generated article {path}: {exc}") from exc
            pages[path] = (text, comment_marker_offset(text, path))

        closed = event_issue(env, owner, repo, posts)
        raw_issues, _ = api.get_pages("/issues", {"state": "all", "labels": LABEL})
        threads = {path: [] for path in posts}
        candidates = []
        seen = set()
        for raw in raw_issues:
            if isinstance(raw, dict) and "pull_request" in raw:
                continue
            try:
                issue, path, body = issue_public(raw, owner, repo, posts)
                number = issue["number"]
                if number in seen:
                    raise ValueError("duplicate issue number")
                seen.add(number)
                candidates.append((issue, path, body, comment_count(raw)))
            except (TypeError, ValueError, UnicodeError) as exc:
                number = raw.get("number", "?") if isinstance(raw, dict) else "?"
                warn(f"Skipping invalid issue {number!r}: {exc}")

        archive_path = None
        run_id = None
        if closed:
            issue, path, _, _ = closed
            number = issue["number"]
            run_id = github_run_id(env)
            archive_path = f"data/comments/archive/issue-{number}.json"
            existing = read_file(root_fd, archive_path, optional=True)
            if existing is not None:
                try:
                    previous = json.loads(existing)
                    previous_time = timestamp(previous["issue"]["closed_at"])
                    previous_run_id = previous.get("github_run_id")
                    if "github_run_id" in previous and not positive_int(previous_run_id):
                        raise ValueError("invalid archive github_run_id")
                except (KeyError, TypeError, ValueError, UnicodeError) as exc:
                    raise SyncError(f"Invalid existing archive {archive_path}: {exc}") from exc
                current_time = timestamp(issue["closed_at"])
                should_write = (not (run_id is not None and run_id == previous_run_id) and
                                (current_time > previous_time or
                                 (current_time == previous_time and run_id is not None and
                                  run_id > (previous_run_id or 0))))
                if not should_write:
                    warn(f"Keeping same or newer archive for issue {number}")
                    closed = None
        # The event's count describes the close-time issue; the list count is
        # current. Missing/non-integer counts conservatively require a fetch.
        needs_bulk = any(count != 0 for _, _, _, count in candidates)
        if closed:
            needs_bulk = needs_bulk or closed[3] != 0

        target_numbers = {issue["number"] for issue, _, _, _ in candidates}
        if closed:
            target_numbers.add(closed[0]["number"])
        grouped = {number: [] for number in target_numbers}
        if needs_bulk:
            expected = {issue["number"]: count for issue, _, _, count in candidates}
            if closed:
                number, count = closed[0]["number"], closed[3]
                if number in expected:
                    count = None if count is None or expected[number] is None else max(count, expected[number])
                expected[number] = count

            def sufficient(items):
                # Counts are a snapshot, so only avoid an empty-page probe
                # when every targeted issue's observed IDs cover that snapshot.
                if any(count is None for count in expected.values()):
                    return False
                ids = {number: set() for number in expected}
                for item in items:
                    if not isinstance(item, dict):
                        raise SyncError("Invalid GitHub bulk comment")
                    number = api.issue_number_from_url(item.get("issue_url"))
                    if number in ids and positive_int(item.get("id")):
                        ids[number].add(item["id"])
                return all(len(ids[number]) >= count for number, count in expected.items())

            bulk, bulk_capped = api.get_pages("/issues/comments", {"sort": "created", "direction": "asc"},
                                              sufficient=sufficient)
            for item in bulk:
                if not isinstance(item, dict):
                    raise SyncError("Invalid GitHub bulk comment")
                number = api.issue_number_from_url(item.get("issue_url"))
                if number in grouped:
                    grouped[number].append(item)
            if bulk_capped:
                for number, count in expected.items():
                    ids = {item.get("id") for item in grouped[number] if positive_int(item.get("id"))}
                    if count is None or len(ids) < count:
                        # Replace rather than merge: the issue endpoint returns
                        # the complete current list and avoids duplicate IDs.
                        grouped[number], _ = api.get_pages(f"/issues/{number}/comments", {})

        fetched_comments = {}
        for issue, path, body, _ in candidates:
            number = issue["number"]
            try:
                comments = comments_public(grouped[number], issue)
                fetched_comments[number] = comments
                try:
                    markup = render_thread(issue, body, comments)
                except Exception as exc:
                    raise ValueError("cannot render issue Markdown") from exc
                threads[path].append((number, markup))
            except (TypeError, ValueError, UnicodeError) as exc:
                warn(f"Skipping invalid issue {number!r}: {exc}")

        archive = None
        if closed:
            issue, path, _, _ = closed
            number = issue["number"]
            try:
                comments = fetched_comments.get(number)
                if comments is None:
                    comments = comments_public(grouped[number], issue)
                cutoff = timestamp(issue["closed_at"])
                included = [comment for comment in comments if timestamp(comment["created_at"]) <= cutoff]
                archive_data = {"issue": issue, "post_path": path, "comments": included}
                if run_id is not None:
                    archive_data["github_run_id"] = run_id
                archive = (archive_path, (json.dumps(archive_data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
            except (TypeError, ValueError, UnicodeError) as exc:
                warn(f"Skipping invalid close event issue {number}: {exc}")
        for path, (text, offset) in pages.items():
            rendered = "\n".join(markup for _, markup in sorted(threads[path]))
            section = f'<section class="issue-comments" aria-label="GitHub コメント">\n{rendered}\n</section>' if rendered else ""
            write_file(root_fd, path, (text[:offset] + section + text[offset + len(MARKER):]).encode("utf-8"))
        if archive is not None:
            write_file(root_fd, archive[0], archive[1], create_dirs=True)
        print(f"Synchronized {sum(map(len, threads.values()))} issue threads across {len(posts)} articles" +
              (f"; saved {archive[0]}" if archive else ""))
    finally:
        os.close(root_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True, help="GitHub repository in OWNER/REPO form")
    args = parser.parse_args()
    try:
        sync(args.site_dir, args.repository, os.environ.get("GITHUB_TOKEN", ""),
             os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    except (SyncError, OSError, UnicodeError) as exc:
        warn(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
