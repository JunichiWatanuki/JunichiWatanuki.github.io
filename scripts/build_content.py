#!/usr/bin/env python3
"""Rebuild one content kind, including its template-derived pages and assets."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from content import KINDS, article_data, article_paths, fix_content_urls, fix_template
from custom_embeds import encode_e2d3, extract_custom_fences, insert_custom_fences, mark_custom_fences, remove_fence_marks
from prepare_site import MARKER

ROOT = Path(__file__).resolve().parent.parent


def _syntax_key_candidate(seed: bytes, counter: int) -> str:
    return "glsyntax" + hashlib.sha256(seed + counter.to_bytes(8, "big")).hexdigest()


def syntax_highlight_key(markdown: str, article: str, *templates: str) -> str:
    """Derive a stable per-article key absent from source and HTML-decoded source.

    Include the complete original Markdown (and thus every rawhtml block) and
    both original and synchronized templates. Checking decoded text also avoids
    accidental HTML character-reference collisions in attribute values.
    """
    seed = hashlib.sha256(b"greenlog-syntax-key-v1\0")
    for value in (article, markdown, *templates):
        data = value.encode("utf-8")
        seed.update(len(data).to_bytes(8, "big"))
        seed.update(data)
    forbidden = (markdown, *templates)
    forbidden += tuple(html.unescape(value) for value in forbidden)
    counter = 0
    while True:
        key = _syntax_key_candidate(seed.digest(), counter)
        if all(key not in value for value in forbidden):
            return key
        counter += 1


def synchronize_chrome(template: str, root_html: str) -> str:
    """Use the root page as the source of truth for shared header and footer."""
    for tag, css_class in (("header", "main-header"), ("footer", "main-footer")):
        pattern = rf'<{tag}\s+class="{css_class}"[^>]*>.*?</{tag}>'
        root_part = re.search(pattern, root_html, flags=re.S)
        if not root_part or not re.search(pattern, template, flags=re.S):
            continue

        def fix_root_link(match):
            attribute, quote, value = match.groups()
            if value == "#top" or value.startswith(("https:", "http:", "//", "mailto:", "tel:", "data:")):
                return match.group(0)
            if value.startswith("#"):
                value = "../../../index.html" + value
            elif value.startswith("/"):
                value = "../../../" + value.lstrip("/")
            else:
                value = "../../../" + value.removeprefix("./")
            return f"{attribute}={quote}{value}{quote}"

        part = re.sub(r'\b(href|src)=(["\'])(.*?)\2', fix_root_link, root_part.group(0))
        template = re.sub(pattern, lambda _: part, template, count=1, flags=re.S)
    return template


def build(root: Path, site: Path, kind: str):
    root, site = root.resolve(), site.resolve()
    if not (site / MARKER).is_file():
        raise ValueError(f"Run prepare_site.py first: {site}")
    source_root = root / "data" / "sources" / kind
    template_path = source_root / "index.html"
    for name in ("index.html", f"{kind}.css", f"{kind}.js"):
        if not (source_root / name).is_file():
            raise ValueError(f"Missing required template/asset: {source_root / name}")
    original_template = template_path.read_text(encoding="utf-8")
    root_html = (root / "index.html").read_text(encoding="utf-8")
    template = synchronize_chrome(original_template, root_html)
    articles = article_paths(root, kind)
    # Build mappings for all kinds to support links between Markdown/CSV/FIT sources.
    mappings = {src.resolve(): output for k in KINDS for src, output, _ in article_paths(root, k)}
    target_root = site / "data" / "contents" / kind
    e2d3_input = site / ".greenlog-e2d3-input.json"
    if kind == "blog" and (e2d3_input.exists() or e2d3_input.is_symlink()):
        try:
            if e2d3_input.is_symlink() or not e2d3_input.is_file():
                raise ValueError("not a regular file")
            previous = json.loads(e2d3_input.read_text(encoding="utf-8"))
            if previous.get("format") != "greenlog-e2d3-input-v1" or not isinstance(previous.get("specs"), list):
                raise ValueError("not a generated e2d3 input")
        except (OSError, ValueError, AttributeError) as error:
            raise ValueError(f"Reserved e2d3 build input is invalid: {e2d3_input}") from error
    e2d3_specs = set()
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True)
    for source in sorted(source_root.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(source_root)
        if kind == "training" and source.suffix.lower() == ".csv":
            print(f"Skipping old training CSV (FIT is required): {source}")
            continue
        if rel == Path("index.html") or source.suffix.lower() == KINDS[kind]:
            continue
        dest = target_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    for source, relative, iso_date in articles:
        dest = site / relative
        if dest.exists():
            raise ValueError(f"An asset would be overwritten by a generated article: {dest}")
        embeds = []
        if kind == "training":
            from fit_training import created_at, decode_fit, training_article

            source_relative = source.relative_to(source_root)
            json_dest = target_root / source_relative.parent / f"{source.stem}.json"
            if json_dest.exists():
                raise ValueError(f"An asset would be overwritten by generated FIT JSON: {json_dest}")
            json_dest.parent.mkdir(parents=True, exist_ok=True)
            json_dest.write_text(json.dumps(decode_fit(source), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                 encoding="utf-8")
            messages = json.loads(json_dest.read_text(encoding="utf-8"))
            if dest.stem != created_at(messages).strftime("%Y-%m-%d_%H-%M-%S"):
                raise ValueError(f"Generated FIT JSON and HTML timestamps differ: {source}")
            json_relative = json_dest.relative_to(site)
            title, _, body = training_article(source, messages, root, json_relative)
        elif kind == "blog":
            # Retain original newlines for collision checks, including rawhtml.
            with source.open(encoding="utf-8-sig", newline="") as file:
                markdown = file.read()
            syntax_key = syntax_highlight_key(markdown, relative.as_posix(),
                                              original_template, template, root_html)
            marked_source, mark = mark_custom_fences(source)
            title, _, body = article_data(marked_source, kind, iso_date, root)
            body, embeds = extract_custom_fences(body, mark, syntax_key)
            for embed in embeds:
                if embed.kind == "e2d3":
                    spec = encode_e2d3(embed.content)
                    if spec is not None:
                        e2d3_specs.add(base64.b64encode(spec).decode("ascii"))
            # The marker is part of generated content, not an article template.
            # It precedes even trusted rawhtml inserted in the final step.
            body = f'<span hidden data-greenlog-syntax-key="{syntax_key}"></span>' + body
            title = remove_fence_marks(title, mark)
        else:
            title, _, body = article_data(source, kind, iso_date, root)
        body = fix_content_urls(body, root, site, source, dest, mappings)
        page = fix_template(template, kind, site, dest)
        page = page.replace("__ARTICLE_TITLE__", html.escape(title, quote=True))
        page = page.replace("__ARTICLE_DATE__", html.escape(iso_date, quote=True))
        page = page.replace("<!-- ARTICLE_CONTENT -->", body)
        page = page.replace("__COMMENT_POST_PATH__", html.escape(quote(relative.as_posix(), safe="/"), quote=True))
        e2d3_dir = site / "data" / "contents" / "blog" / "_e2d3"
        e2d3_prefix = Path(os.path.relpath(e2d3_dir, dest.parent)).as_posix() + "/"
        page = insert_custom_fences(page, embeds, e2d3_prefix=e2d3_prefix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8")
    if kind == "blog":
        e2d3_input.write_text(json.dumps({"format": "greenlog-e2d3-input-v1", "specs": sorted(e2d3_specs)},
                                          separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Built {len(articles)} {kind} articles in {target_root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--site-dir", required=True, type=Path)
    args = parser.parse_args()
    build(ROOT, args.site_dir, args.kind)


if __name__ == "__main__":
    main()
