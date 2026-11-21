"""Post-render handling of custom fences in *trusted blog articles only*.

The normal Markdown/nh3 pipeline is still used.  We mark possible fence info
strings before rendering so that a hand-written ``<pre lang=...>`` cannot be
mistaken for a Markdown fence.  Marks inside other blocks are removed again.
Custom fenced blocks are held outside the page until URL, ID and template
processing has finished; normal highlightable fences remain ordinary code.
Do not use this module for untrusted issue comments.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


SERVICES = frozenset({"youtube", "instagram", "twitter", "garmin", "slideshare"})
PREVIEWS = {"rawmermaid": "mermaid", "rawstl": "stl", "rawsvg": "svg"}
HIGHLIGHT_LANGUAGES = frozenset({"mermaid", "stl", "svg"})
_KINDS = r"rawhtml|rawmermaid|rawstl|rawsvg|youtube|instagram|twitter|garmin|slideshare|mermaid|stl|svg|e2d3"
# This deliberately marks possible openers rather than parsing Markdown itself:
# cmark is the authority on whether a marked line really is a fenced block.
# Prefixes cover nested GFM block quotes, list markers and list continuation
# indentation. Any mark that cmark leaves as ordinary text is removed afterward.
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_CUSTOM_KINDS = SERVICES | {"rawhtml", "e2d3"} | frozenset(PREVIEWS) | HIGHLIGHT_LANGUAGES


@dataclass(frozen=True)
class _Marks:
    prefix: str
    source_lines: dict[int, int]
    line_endings: tuple[str, ...]
    original_lines: tuple[str, ...]


def _info_kind_end(info: str) -> int | None:
    """Locate the end of a supported language name in a GFM info string.

    cmark resolves semicolon-terminated character references before splitting
    the language at ASCII whitespace. Returning the offset in the *original*
    text lets us add and later remove a mark without changing code examples.
    """
    word = ""
    pos = 0
    while pos < len(info):
        entity = _ENTITY.match(info, pos)
        end = entity.end() if entity else pos + 1
        part = html.unescape(info[pos:end]) if entity else info[pos:end]
        for ch in part:
            if ch in " \t\n\r\f\v":
                return pos if word.lower() in _CUSTOM_KINDS else None
            word += ch
            if not any(kind.startswith(word.lower()) for kind in _CUSTOM_KINDS):
                return None
        pos = end
    return pos if word.lower() in _CUSTOM_KINDS else None


def _possible_custom_fences(markdown: str):
    """Yield (insertion offset, line number) for possible supported fences.

    This is deliberately a broad candidate scan, not a Markdown parser. Each
    character is visited a bounded number of times, even on a very long line
    containing many nested quote/list prefixes. cmark decides which candidates
    are actually fences; the rest are restored after rendering.
    """
    start = 0
    line_number = 0
    while start < len(markdown):
        end = markdown.find("\n", start)
        if end < 0:
            end = len(markdown)
        pos = start
        while True:
            while pos < end and markdown[pos] in " \t":
                pos += 1
            if pos >= end:
                break
            if markdown[pos] == ">":
                pos += 1
                continue
            marker_end = pos
            if markdown[pos] in "-+*":
                marker_end += 1
            elif "0" <= markdown[pos] <= "9":
                while (marker_end < end and marker_end - pos < 9 and
                       "0" <= markdown[marker_end] <= "9"):
                    marker_end += 1
                if marker_end < end and markdown[marker_end] in ".)":
                    marker_end += 1
                else:
                    marker_end = pos
            if marker_end > pos and marker_end < end and markdown[marker_end] in " \t":
                pos = marker_end + 1
                continue
            if markdown[pos] not in "`~":
                break
            fence_char = markdown[pos]
            fence_start = pos
            while pos < end and markdown[pos] == fence_char:
                pos += 1
            if pos - fence_start < 3:
                break
            while pos < end and markdown[pos] in " \t":
                pos += 1
            info_start = pos
            while pos < end and markdown[pos] not in " \t\r":
                pos += 1
            kind_end = _info_kind_end(markdown[info_start:pos])
            if kind_end is not None:
                yield info_start + kind_end, line_number
            break
        start = end + 1
        line_number += 1


class _MarkdownSource:
    """Path-like proxy supplying marked text to the existing blog renderer."""
    def __init__(self, path: Path, text: str, marks: _Marks):
        self._path = path
        self._text = text
        self._custom_fence_marks = marks

    def read_text(self, encoding=None, errors=None):
        return self._text

    def __getattr__(self, name):
        return getattr(self._path, name)

    def __fspath__(self):
        return str(self._path)

    def __str__(self):
        return str(self._path)


def mark_custom_fences(source: Path):
    """Return a read-only source proxy and a private, collision-free mark.

    The proxy does not modify the source file. The mark is appended only to
    possible fence language names; the renderer decides which are real fences.
    """
    # read_text() would discard CRLF/CR distinctions before we can retain
    # rawhtml's original line endings. cmark itself still sees normalised LF,
    # just as in the existing renderer.
    with source.open(encoding="utf-8-sig", newline="") as file:
        original = file.read()
    endings = tuple(match.group() for match in re.finditer(r"\r\n|\r|\n", original))
    markdown = re.sub(r"\r\n|\r|\n", "\n", original)
    candidates = list(_possible_custom_fences(markdown))
    if not candidates:
        return source, None
    while True:
        prefix = "glcustom" + secrets.token_hex(16) + "z"
        if prefix not in markdown:
            break
    source_lines = {index: line for index, (_, line) in enumerate(candidates)}
    pieces = []
    previous = 0
    for index, (offset, _) in enumerate(candidates):
        pieces.extend((markdown[previous:offset], f"{prefix}{index}z"))
        previous = offset
    pieces.append(markdown[previous:])
    marks = _Marks(prefix, source_lines, endings, tuple(markdown.split("\n")))
    return _MarkdownSource(source, "".join(pieces), marks), marks


def remove_fence_marks(text: str, marks: _Marks | None) -> str:
    """Restore marks in ordinary text, including nested code examples."""
    if marks is None:
        return text
    return re.sub(re.escape(marks.prefix) + r"[0-9]+z", "", text)


def _restore_raw_newlines(content: str, marks: _Marks, index: int) -> str:
    """Map cmark's code-line LFs to the original source line endings."""
    parts = content.split("\n")
    if len(parts) == 1:
        return content
    first_line = marks.source_lines[index] + 1
    result = []
    for offset, part in enumerate(parts[:-1]):
        line = first_line + offset
        result.append(part)
        # A final line without a line ending gets a synthetic LF from cmark.
        result.append(marks.line_endings[line] if line < len(marks.line_endings) else "")
    result.append(parts[-1])
    return "".join(result)


def _restore_e2d3_content(content: str, marks: _Marks, index: int) -> str | None:
    """Reverse cmark's NUL replacement as well as its newline normalization.

    Markdown containers and fence indentation can remove the beginning of a
    source line. The remainder is a suffix; only restore NULs that can be
    matched unambiguously against that suffix. If a NUL cannot be accounted
    for, retain the ordinary code block rather than silently corrupt CSV.
    """
    lines = content.split("\n")
    first_line = marks.source_lines[index] + 1
    for offset, line in enumerate(lines):
        source_index = first_line + offset
        if source_index >= len(marks.original_lines):
            break
        source = marks.original_lines[source_index]
        if "\0" not in source:
            continue
        normalized = source.replace("\0", "\ufffd")
        suffix = 0
        while (suffix < min(len(line), len(normalized)) and
               line[-suffix - 1] == normalized[-suffix - 1]):
            suffix += 1
        prefix_length = len(source) - suffix
        if "\0" in source[:prefix_length]:
            return None
        chars = list(line)
        for pos in range(prefix_length, len(source)):
            if source[pos] == "\0":
                chars[len(line) - len(source) + pos] = "\0"
        lines[offset] = "".join(chars)
    return _restore_raw_newlines("\n".join(lines), marks, index)


@dataclass(frozen=True)
class _Embed:
    placeholder: str
    kind: str
    content: str
    original: str


def extract_custom_fences(rendered: str, mark: _Marks | None, syntax_key: str | None = None):
    """Replace recognized rendered fences with opaque, one-use placeholders.

    Code contents are unescaped exactly once, reversing the renderer's HTML
    escaping. Ordinary Mermaid/STL/SVG fences are not previews: their rendered
    code is retained and, when supplied, marked with the article's syntax key.
    Hand-written pre tags and examples inside other code blocks never match.
    """
    if mark is None:
        return rendered, []
    pattern = re.compile(
        rf'<pre lang="(?P<kind>(?i:{_KINDS})){re.escape(mark.prefix)}(?P<index>[0-9]+)z">'
        r'<code>(?P<content>.*?)</code></pre>', re.S
    )
    embeds = []
    while True:
        placeholder_prefix = "GREENLOGCUSTOM" + secrets.token_hex(16) + "X"
        if placeholder_prefix not in rendered and mark.prefix not in placeholder_prefix:
            break

    def capture(match):
        kind = match.group("kind").lower()
        if kind in HIGHLIGHT_LANGUAGES:
            original = remove_fence_marks(match.group(0), mark)
            if syntax_key is None:
                return original
            # The key is generated by the build, never taken from Markdown.
            start = f'<pre lang="{match.group("kind")}"'
            return original.replace(start, start + f' data-syntax-highlight="{html.escape(syntax_key, quote=True)}"', 1)
        placeholder = f"{placeholder_prefix}{len(embeds)}END"
        content = html.unescape(remove_fence_marks(match.group("content"), mark))
        if kind == "rawhtml":
            content = _restore_raw_newlines(content, mark, int(match.group("index")))
        elif kind == "e2d3":
            content = _restore_e2d3_content(content, mark, int(match.group("index")))
            if content is None:
                return remove_fence_marks(match.group(0), mark)
        embeds.append(_Embed(
            placeholder=placeholder,
            kind=kind,
            content=content,
            original=remove_fence_marks(match.group(0), mark),
        ))
        return placeholder

    body = pattern.sub(capture, rendered)
    # Remove marks cmark treated as ordinary content, e.g. an example fence
    # inside a Python code block. Placeholders use a separate random prefix.
    body = remove_fence_marks(body, mark)
    return body, embeds


def render_service_embed(kind: str, content: str) -> str | None:
    """Render a supported HTTPS URL, or preserve the original code on failure.

    Never fetch the supplied URL at build time. Only allow known host/path
    combinations and derive each iframe URL from a validated ID; retain a link
    to the original for inaccessible, deleted or non-public content.
    """
    url = content.strip()
    if not url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url) or "\\" in url:
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        if parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    host = parts.hostname.lower()
    source = html.escape(url, quote=True)

    def wrap(label: str, element: str, extra_class: str = "") -> str:
        classes = f"main-embed{(' ' + extra_class) if extra_class else ''}"
        return (f'<div class="{classes}">{element}'
                f'<p class="main-embed-source"><a href="{source}" target="_blank" '
                f'rel="noopener noreferrer">{label}で開く ↗</a></p></div>')

    def frame(src: str, title: str, attributes: str = "") -> str:
        return (f'<iframe src="{html.escape(src, quote=True)}" title="{title}" '
                f'loading="lazy" referrerpolicy="strict-origin-when-cross-origin"{attributes}></iframe>')

    if kind == "youtube":
        youtube_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
        video = None
        query = parse_qs(parts.query, keep_blank_values=True)
        if host in youtube_hosts and parts.path == "/watch":
            values = query.get("v", [])
            video = values[0] if len(values) == 1 else None
        elif host == "youtu.be":
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parts.path)
            video = match.group(1) if match else None
        elif host in youtube_hosts | {"www.youtube-nocookie.com"}:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", parts.path)
            video = match.group(1) if match else None
        if not video or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            return None
        # Carry common explicit start times across to the documented player
        # parameter. Invalid/ambiguous times are ignored; the source URL stays
        # available below the embed.
        start = None
        values = query.get("start", query.get("t", []))
        if len(values) == 1:
            value = values[0]
            if re.fullmatch(r"[0-9]{1,7}", value):
                start = int(value)
            else:
                time = re.fullmatch(r"(?:(\d{1,4})h)?(?:(\d{1,2})m)?(?:(\d{1,2})s)?", value)
                if time and any(time.groups()):
                    hours, minutes, seconds = (int(part or 0) for part in time.groups())
                    if minutes < 60 and seconds < 60:
                        start = hours * 3600 + minutes * 60 + seconds
        player_url = f"https://www.youtube.com/embed/{video}"
        if start is not None:
            player_url += f"?start={start}"
        player = frame(player_url, "YouTube 動画",
                       ' allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen')
        return wrap("YouTube", f'<div class="main-embed-frame">{player}</div>')

    if kind == "instagram":
        if host not in {"instagram.com", "www.instagram.com"}:
            return None
        match = re.fullmatch(r"/(?:[A-Za-z0-9._]+/)?(p|reel)/([A-Za-z0-9_-]+)/?", parts.path)
        if not match:
            return None
        player = frame(f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/embed/", "Instagram 投稿")
        return wrap("Instagram", player, "main-embed-instagram")

    if kind == "twitter":
        if host not in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
            return None
        match = re.fullmatch(r"/([A-Za-z0-9_]{1,15})/(?:status|statuses)/([0-9]{1,20})/?", parts.path)
        if not match:
            return None
        # The official widget discovers a post from the link; it replaces the
        # blockquote with an iframe and leaves the link if the script is blocked.
        post = html.escape(f"https://x.com/{match.group(1)}/status/{match.group(2)}", quote=True)
        widget = (f'<blockquote class="twitter-tweet"><a href="{post}">Xの投稿を表示</a></blockquote>'
                  '<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>')
        return wrap("X", widget)

    if kind == "garmin":
        if host != "connect.garmin.com":
            return None
        match = re.fullmatch(r"/(?:app/activity|modern/activity|activity|embed/activity|app/activity/embed|modern/activity/embed)/([0-9]{1,20})/?", parts.path)
        if not match:
            return None
        player = frame(f"https://connect.garmin.com/embed/activity/{match.group(1)}", "Garmin Connect アクティビティ")
        return wrap("Garmin Connect", player, "main-embed-garmin")

    if kind == "slideshare":
        if host not in {"slideshare.net", "www.slideshare.net"}:
            return None
        match = re.fullmatch(r"/slideshow/([A-Za-z0-9._~%-]+)/([0-9]{1,20})/?", parts.path)
        if match:
            player_url = f"https://www.slideshare.net/slideshow/embed_code/{match.group(2)}"
        else:
            match = re.fullmatch(r"/slideshow/embed_code/key/([A-Za-z0-9_-]{1,100})/?", parts.path)
            if not match:
                return None
            player_url = f"https://www.slideshare.net/slideshow/embed_code/key/{match.group(1)}"
        player = frame(player_url, "SlideShare スライド", " allowfullscreen")
        return wrap("SlideShare", f'<div class="main-embed-frame">{player}</div>')

    return None


def render_code_preview(kind: str, content: str) -> str | None:
    """Hold original code as text; only SVG gets an inert image at build time.

    Do not interpret or change Mermaid/STL (including URLs, relative paths,
    whitespace, or invalid syntax): the browser-side renderer decides whether
    it can display them. An invalid SVG likewise remains accessible as source,
    even if the browser cannot display the image. No source text is inserted
    into an HTML attribute, and SVG is never inserted as live inline markup.
    """
    preview = PREVIEWS.get(kind)
    if preview is None:
        return None
    try:
        encoded = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        # An unrepresentable value must fall back to its original code block.
        return None
    display = ""
    if preview == "svg":
        svg = base64.b64encode(encoded).decode("ascii")
        display = (f'<img src="data:image/svg+xml;base64,{svg}" '
                   'alt="SVG プレビュー" loading="lazy">')
    return (f'<section class="main-code-preview" data-preview="{preview}">'
            f'<div class="main-code-preview-display">{display}</div>'
            '<p class="main-code-preview-status" role="status" hidden></p>'
            '<details class="main-code-preview-source"><summary>コードを表示</summary>'
            f'<pre><code>{html.escape(content, quote=True)}</code></pre></details></section>')


def parse_e2d3(content: str) -> tuple[str, str] | None:
    """Parse exact section lines and retain the template name and CSV verbatim.

    A template is one safe directory component, never a path. Neither section
    names nor the directory name are trimmed, and the CSV may be empty. Blank
    lines between the template name and the data section are permitted.
    """
    match = re.fullmatch(r"# template(?:\r\n|\r|\n)([^\r\n]+)(?:\r\n|\r|\n)"
                         r"(?:[ \t]*(?:\r\n|\r|\n))*# data(?:(?:\r\n|\r|\n)(.*)|$)", content, re.S)
    if not match:
        return None
    template, data = match.group(1), match.group(2) or ""
    if (not template.strip() or template == "." or ".." in template or
            re.match(r"^[A-Za-z]:", template) or
            any(ch in "/\\" or unicodedata.category(ch) == "Cc" for ch in template)):
        return None
    return template, data


def encode_e2d3(content: str) -> bytes | None:
    """Encode a recognized fence exactly as both build stages expect."""
    spec = parse_e2d3(content)
    if spec is None:
        return None
    template, data = spec
    try:
        return json.dumps({"template": template, "data": data}, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        return None


def render_e2d3(content: str, prefix: str = "_e2d3/") -> str | None:
    """Render a safe reference; the Node build generates the linked HTML later."""
    encoded = encode_e2d3(content)
    if encoded is None:
        return None
    template, _ = parse_e2d3(content)
    digest = hashlib.sha256(encoded).hexdigest()
    serialized = base64.b64encode(encoded).decode("ascii")
    src = (prefix.rstrip("/") + "/" if prefix else "") + digest + ".html"
    title = html.escape(f"E2D3: {template}", quote=True)
    return (f'<section class="main-e2d3" data-e2d3-spec="{serialized}">'
            f'<iframe class="main-e2d3-frame" src="{html.escape(src, quote=True)}" '
            f'title="{title}" loading="lazy"></iframe>'
            '<details class="main-e2d3-source"><summary>データを表示</summary>'
            f'<pre><code>{html.escape(content, quote=True)}</code></pre></details></section>')


def insert_custom_fences(page: str, embeds, e2d3_prefix: str = "_e2d3/") -> str:
    """Insert once, after every other transformation, without re-expansion."""
    if not embeds:
        return page
    if any(page.count(embed.placeholder) != 1 for embed in embeds):
        raise ValueError("Custom fence placeholder missing or duplicated")
    by_placeholder = {embed.placeholder: embed for embed in embeds}
    pattern = re.compile("|".join(re.escape(p) for p in by_placeholder))

    def insert(match):
        embed = by_placeholder[match.group(0)]
        if embed.kind == "rawhtml":
            return embed.content
        if embed.kind in PREVIEWS:
            replacement = render_code_preview(embed.kind, embed.content)
        elif embed.kind == "e2d3":
            replacement = render_e2d3(embed.content, e2d3_prefix)
        else:
            replacement = render_service_embed(embed.kind, embed.content)
        return embed.original if replacement is None else replacement

    # re.sub scans only the original page: embedded raw HTML, service output,
    # and preview source cannot be reinterpreted as further custom fences.
    return pattern.sub(insert, page)
