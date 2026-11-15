"""Shared, deterministic source-to-article conversion helpers."""
from __future__ import annotations

import csv
import html
import os
import re
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from custom_embeds import remove_fence_marks

KINDS = {"blog": ".md", "training": ".fit", "sample": ".csv"}
UNKNOWN_DATE = "日付不明"
DATE_NAME = re.compile(r"^([0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})(.*)$")
TITLE_COMMENT = re.compile(r"\A\ufeff?[ \t]*<!--[ \t]*title[ \t]*:[ \t]*(.*?)[ \t]*-->[ \t]*(?:\r?\n|$)", re.I)
URL_ATTRIBUTE = re.compile(r"(?P<attr>\b(?:href|src)\s*=\s*)(?P<quote>['\"])(?P<url>.*?)(?P=quote)", re.I | re.S)
HEADING = re.compile(r"<h([1-6])>(.*?)</h\1>", re.I | re.S)
HTML_START_TAG = re.compile(r'''<[A-Za-z](?:[^<>"']|"[^"]*"|'[^']*')*>''', re.S)
HTML_ATTRIBUTE = re.compile(r'''(?P<space>\s+)(?P<name>[^\s=/>]+)(?P<equals>\s*=\s*)(?P<quote>["'])(?P<url>.*?)(?P=quote)''', re.S)


def article_paths(root: Path, kind: str):
    """Return source/output pairs; reject unsafe paths and ambiguous outputs."""
    source_root = root / "data" / "sources" / kind
    if not source_root.is_dir():
        raise ValueError(f"Missing source directory: {source_root}")
    for path in (root / "data", root / "data" / "sources", source_root):
        if path.is_symlink():
            raise ValueError(f"Symlink in source path is not allowed: {path}")
    # All kinds can refer to shared media, so validate the whole source tree.
    for path in sorted(source_root.parent.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in source tree is not allowed: {path}")
        if "\\" in path.name or any(ord(ch) < 32 or ord(ch) == 127 for ch in path.name):
            raise ValueError(f"Unsafe character in source path: {path!s}")
    seen = {}
    seen_json = {}
    result = []
    # build_content and prepare_site also treat file extensions case-insensitively.
    for source in sorted(source_root.rglob("*")):
        if not source.is_file() or source.suffix.lower() != KINDS[kind]:
            continue
        relative = source.relative_to(source_root)
        if kind == "training":
            from fit_training import created_at, decode_fit

            timestamp = created_at(decode_fit(source))
            iso_date = timestamp.date().isoformat()
            output_stem = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
            json_path = relative.parent / f"{source.stem}.json"
            if json_path in seen_json:
                raise ValueError(f"FIT JSON files would overwrite each other: {seen_json[json_path]} and {source}")
            seen_json[json_path] = source
        else:
            match = DATE_NAME.fullmatch(source.stem)
            iso_date = UNKNOWN_DATE
            output_stem = source.stem
            if match:
                try:
                    iso_date = date.fromisoformat(match.group(1)).isoformat()
                    output_stem = iso_date + match.group(2)
                except ValueError:
                    # An unparseable date prefix is simply an undated filename.
                    pass
        output = Path("data") / "contents" / kind / relative.parent / f"{output_stem}.html"
        if output in seen:
            raise ValueError(f"Articles would overwrite each other: {seen[output]} and {source}")
        seen[output] = source
        result.append((source, output, iso_date))
    return result


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_h1 = False
        self.in_p = False
        self.h1 = []
        self.paragraphs = []
        self.current_p = []

    def handle_starttag(self, tag, attrs):
        if tag == "h1" and not self.h1:
            self.in_h1 = True
        elif tag == "p" and not self.in_p:
            self.in_p = True
            self.current_p = []
        if tag == "img":
            alt = dict(attrs).get("alt", "")
            if self.in_h1:
                self.h1.append(alt)
            if self.in_p:
                self.current_p.append(alt)

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_h1 = False
        elif tag == "p" and self.in_p:
            self.paragraphs.append("".join(self.current_p))
            self.in_p = False

    def handle_data(self, data):
        if self.in_h1:
            self.h1.append(data)
        if self.in_p:
            self.current_p.append(data)


class FragmentText(HTMLParser):
    """Get rendered heading text, including inline markup and image alt text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.parts.append(dict(attrs).get("alt", ""))


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value is not None:
                self.ids.append(value)


def compact(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def render_blog(source: Path, iso_date: str):
    # Imported lazily so training conversion itself needs only Python's stdlib.
    import cmarkgfm
    import nh3
    from cmarkgfm.cmark import Options
    from github_slugger import GithubSlugger

    markdown = source.read_text(encoding="utf-8-sig")
    match = TITLE_COMMENT.match(markdown)
    explicit_title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    if match:
        markdown = markdown[match.end():]
    rendered = cmarkgfm.github_flavored_markdown_to_html(
        markdown, options=Options.CMARK_OPT_UNSAFE | Options.CMARK_OPT_FOOTNOTES)
    # Preserve the renderer's footnote structure while retaining nh3's usual
    # safety rules. Untrusted raw HTML cannot introduce arbitrary IDs.
    attrs = deepcopy(nh3.ALLOWED_ATTRIBUTES)
    attrs.setdefault("code", set()).add("class")
    attrs.setdefault("a", set()).update({"title", "id", "class", "data-footnote-ref", "data-footnote-backref", "data-footnote-backref-idx", "aria-label"})
    attrs.setdefault("img", set()).add("title")
    attrs.setdefault("li", set()).add("id")
    attrs["sup"] = {"class"}
    attrs["section"] = {"class", "data-footnotes"}
    attrs["input"] = {"type", "checked", "disabled"}

    def filter_attribute(tag, name, value):
        if name == "id":
            prefix = "fnref-" if tag == "a" else "fn-" if tag == "li" else None
            if not prefix or not value.startswith(prefix):
                return None
            # cmark percent-encodes IDs as well as fragment links. Browsers
            # decode fragments before matching IDs, so decode IDs exactly once.
            value = unquote(value)
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                raise ValueError(f"Unsafe footnote ID in {source}")
        return value

    rendered = nh3.clean(rendered, tags=nh3.ALLOWED_TAGS | {"input", "section"}, attributes=attrs,
                         attribute_filter=filter_attribute, clean_content_tags={"script", "style"},
                         tag_attribute_values={"input": {"type": {"checkbox"}}},
                         set_tag_attribute_values={"input": {"disabled": ""}})
    slugger = GithubSlugger()
    fence_marks = getattr(source, "_custom_fence_marks", None)

    def add_heading_id(match):
        text = FragmentText()
        text.feed(match.group(2))
        # A possible fence may actually be text inside an HTML heading. Keep
        # its mark in the rendered body until fence extraction, but never let
        # it affect the slugger's duplicate counters or generated IDs.
        heading_text = remove_fence_marks("".join(text.parts), fence_marks)
        slug = slugger.slug(heading_text)
        return f'<h{match.group(1)} id="{html.escape(slug, quote=True)}">{match.group(2)}</h{match.group(1)}>'
    rendered = HEADING.sub(add_heading_id, rendered)
    extractor = TextExtractor()
    extractor.feed(rendered)
    fallback = source.stem if iso_date == UNKNOWN_DATE else f"{iso_date} のブログ"
    title = explicit_title or re.sub(r"\s+", " ", "".join(extractor.h1)).strip() or fallback
    excerpt = next((compact(p) for p in extractor.paragraphs if compact(p)), "")
    return title, excerpt or "ブログの記事です。", rendered


def render_training(source: Path, iso_date: str, label: str = "トレーニング"):
    with source.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, strict=True))
    if not rows or not rows[0]:
        raise ValueError(f"CSV must include a header row: {source}")
    width = len(rows[0])
    for number, row in enumerate(rows[1:], 2):
        if len(row) != width:
            raise ValueError(f"CSV row {number} has {len(row)} cells, expected {width}: {source}")
    esc = lambda value: html.escape(value, quote=True)
    header = "".join(f'<th scope="col">{esc(cell)}</th>' for cell in rows[0])
    body = "\n".join("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
    markup = f'<div class="main-training-table-wrap"><table>\n<thead><tr>{header}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table></div>'
    if iso_date == UNKNOWN_DATE:
        title = f"{source.stem} の{label}"
    else:
        d = date.fromisoformat(iso_date)
        title = f"{d.year}年{d.month}月{d.day}日の{label}"
    activities = "、".join(row[0] for row in rows[1:4] if row and row[0].strip())
    excerpt = compact(f"{label}の記録：{activities}" if activities else f"{label}の記録です。")
    return title, excerpt, markup


def article_data(source: Path, kind: str, iso_date: str, root: Path | None = None):
    if kind == "blog":
        return render_blog(source, iso_date)
    if kind == "training":
        if root is None:
            raise ValueError("The repository root is required for FIT article links")
        from fit_training import decode_fit, training_article

        relative = source.relative_to(root / "data" / "sources" / "training")
        json_relative = Path("data") / "contents" / "training" / relative.parent / f"{source.stem}.json"
        return training_article(source, decode_fit(source), root, json_relative)
    return render_training(source, iso_date, "サンプル")


def root_relative(site_dir: Path, dest: Path) -> str:
    return Path(os.path.relpath(site_dir, dest.parent)).as_posix() + "/"


def fix_template(template: str, kind: str, site_dir: Path, dest: Path) -> str:
    if template.count("<!-- ARTICLE_CONTENT -->") != 1:
        raise ValueError(f"{kind} template must contain exactly one <!-- ARTICLE_CONTENT -->")
    for token in ("__ARTICLE_TITLE__", "__ARTICLE_DATE__"):
        if token not in template:
            raise ValueError(f"{kind} template is missing {token}")
    template = template.replace("../../../", root_relative(site_dir, dest))
    kind_dir = site_dir / "data" / "contents" / kind
    prefix = Path(os.path.relpath(kind_dir, dest.parent)).as_posix()
    prefix = "" if prefix == "." else prefix + "/"

    def fix_asset(match):
        value = match.group("url")
        if re.fullmatch(rf"(?:\./)?{kind}\.(?:css|js)(?:[?#].*)?", value, re.I):
            value = prefix + re.sub(r"^\./", "", value)
        return match.group("attr") + match.group("quote") + value + match.group("quote")
    return URL_ATTRIBUTE.sub(fix_asset, template)


def fix_content_urls(markup: str, root: Path, site_dir: Path, source: Path, dest: Path, mappings: dict[Path, Path]):
    """Preserve relative media; translate internal source links and site-absolute URLs."""
    # Reject ID collisions rather than generating fragment links that land on
    # a different footnote, heading, or template element.
    ids = IdCollector()
    ids.feed(markup)
    template_ids = IdCollector()
    kind = dest.relative_to(site_dir).parts[2]
    template_ids.feed((root / "data" / "sources" / kind / "index.html").read_text(encoding="utf-8"))
    seen = set(template_ids.ids)
    for ident in ids.ids:
        if ident in seen:
            raise ValueError(f"Duplicate article or template ID {ident!r}: {source}")
        seen.add(ident)

    def replace(match):
        if match.group("name").lower() not in ("href", "src"):
            return match.group(0)
        original = html.unescape(match.group("url"))
        parts = urlsplit(original)
        if parts.scheme or parts.netloc or not parts.path:
            return match.group(0)
        path = parts.path
        decoded_path = unquote(path)
        candidate = ((root / decoded_path.lstrip("/")) if path.startswith("/")
                     else (source.parent / decoded_path)).resolve()
        if candidate in mappings:
            path = quote(Path(os.path.relpath(site_dir / mappings[candidate], dest.parent)).as_posix(), safe="/")
        elif path.startswith("/"):
            path = root_relative(site_dir, dest) + quote(decoded_path.lstrip("/"), safe="/")
        else:
            path = quote(decoded_path, safe="/")
        value = urlunsplit(("", "", path, parts.query, parts.fragment))
        return (match.group("space") + match.group("name") + match.group("equals") +
                match.group("quote") + html.escape(value, quote=True) + match.group("quote"))
    return HTML_START_TAG.sub(lambda tag: HTML_ATTRIBUTE.sub(replace, tag.group(0)), markup)
