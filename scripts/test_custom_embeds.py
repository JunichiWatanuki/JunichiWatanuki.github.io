#!/usr/bin/env python3
"""Tests for trusted-blog custom fences; all fixtures and builds are local."""
from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import custom_embeds
from build_content import build, syntax_highlight_key
from content import render_blog
from custom_embeds import extract_custom_fences, insert_custom_fences, mark_custom_fences
from prepare_site import MARKER
from sync_comments import render_markdown


PREVIEW_BLOCK = re.compile(
    r'<section class="main-code-preview" data-preview="(mermaid|stl|svg)">'
    r'<div class="main-code-preview-display">(.*?)</div>'
    r'<p class="main-code-preview-status" role="status" hidden></p>'
    r'<details class="main-code-preview-source"><summary>コードを表示</summary>'
    r'<pre><code>(.*?)</code></pre></details></section>', re.S,
)


def previews(page):
    return [(match.group(1), match.group(2), html.unescape(match.group(3)))
            for match in PREVIEW_BLOCK.finditer(page)]


E2D3_BLOCK = re.compile(
    r'<section class="main-e2d3" data-e2d3-spec="([A-Za-z0-9+/]*={0,2})">'
    r'<iframe class="main-e2d3-frame" src="([^"]*)" title="([^"]*)" loading="lazy"></iframe>'
    r'<details class="main-e2d3-source"><summary>データを表示</summary>'
    r'<pre><code>(.*?)</code></pre></details></section>', re.S,
)


def e2d3_blocks(page):
    return list(E2D3_BLOCK.finditer(page))


def syntax_key(page):
    match = re.search(r'<span hidden data-greenlog-syntax-key="(glsyntax[0-9a-f]{64})"></span>', page)
    if not match:
        raise AssertionError('Article syntax key not found')
    return match.group(1)


class TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CustomEmbedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.site = Path(self.tmp.name) / "site"
        self.write("index.html", "<html><body>home</body></html>")
        for kind in ("blog", "sample", "training"):
            self.write(f"data/sources/{kind}/index.html", (
                '<html><head><title>__ARTICLE_TITLE__</title></head>'
                '<body id="template-id"><time>__ARTICLE_DATE__</time>'
                '<aside>__COMMENT_POST_PATH__</aside>'
                '<main><!-- ARTICLE_CONTENT --></main></body></html>'
            ))
            self.write(f"data/sources/{kind}/{kind}.css", "/* css */")
            self.write(f"data/sources/{kind}/{kind}.js", "// js")
        self.site.mkdir()
        (self.site / MARKER).write_text("ready", encoding="utf-8")

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def page(self, markdown):
        source = self.write("data/sources/blog/nest/20250102.md", markdown)
        build(self.root, self.site, "blog")
        page = (self.site / "data/contents/blog/nest/2025-01-02.html").read_text(encoding="utf-8")
        return source, page

    def render_held(self, markdown):
        source = self.write("data/sources/blog/demo.md", markdown)
        marked_source, mark = mark_custom_fences(source)
        title, excerpt, body = render_blog(marked_source, "2025-01-02")
        body, embeds = extract_custom_fences(body, mark)
        return source, title, excerpt, body, embeds

    def test_end_to_end_late_unescape_preserves_raw_html(self):
        raw = ('<div id="template-id" data-token="__COMMENT_POST_PATH__">\n'
               '<script id="keep-script">if (a < b) { x = "&amp; &lt;"; }</script>\n'
               '<a href="/absolute path/x?one=1&amp;two=2">relative &amp; link</a>\n'
               '<img src="../../../assets/picture a.png">\n'
               '__ARTICLE_TITLE__ __ARTICLE_DATE__ <!-- ARTICLE_CONTENT -->\n'
               '</div>\n')
        source, page = self.page('# Heading\n\n[normal](/images/photo.png)\n\n```rawhtml\n' + raw + '```\n')
        self.assertIn(raw, page)
        self.assertIn('href="../../../../images/photo.png"', page)
        self.assertIn('<body id="template-id">', page)  # duplicate raw ID is not rewritten or checked
        self.assertIn('<aside>data/contents/blog/nest/2025-01-02.html</aside>', page)
        self.assertIn('<time>2025-01-02</time>', page)
        self.assertNotIn('<pre lang="rawhtml">', page)
        self.assertEqual(source.read_text(encoding="utf-8").count(raw), 1)

    def test_gfm_nested_quote_list_fence_styles_and_multiple_blocks(self):
        markdown = ("# T\n\n"
                    "> ~~~rawhtml\n> <i id=\"quote\">Q</i>\n> ~~~\n\n"
                    "- Item\n\n  ````rawhtml\n  <b>List</b>\n  ````\n\n"
                    "> - ```rawhtml\n>   <em>Both</em>\n>   ```\n\n"
                    "~~~ rawhtml extra info\n<p>End</p>\n~~~\n")
        _, page = self.page(markdown)
        self.assertIn('<blockquote>\n<i id="quote">Q</i>\n\n</blockquote>', page)
        self.assertIn('<li>\n<p>Item</p>\n<b>List</b>\n\n</li>', page)
        self.assertIn('<em>Both</em>\n', page)
        self.assertIn('<p>End</p>\n', page)
        self.assertNotIn('<pre lang="rawhtml">', page)

    def test_gfm_character_references_and_case_in_info_strings(self):
        markdown = ('```r&#97;whtml&#32;ignored\n<b>one</b>\n```\n\n'
                    '~~~RAWHTML\n<b>two</b>\n~~~\n\n'
                    '```you&#116;ube\nhttps://example.test/\n```\n\n'
                    '```r&#97whtml\n<b>not-a-custom-fence</b>\n```\n')
        with patch('custom_embeds.render_service_embed', return_value=None) as mock:
            source, page = self.page(markdown)
        self.assertIn('<b>one</b>\n', page)
        self.assertIn('<b>two</b>\n', page)
        mock.assert_called_once_with('youtube', 'https://example.test/\n')
        self.assertIn('<pre lang="youtube"><code>https://example.test/\n</code></pre>', page)
        self.assertIn('<pre lang="r&amp;#97whtml"><code>&lt;b&gt;not-a-custom-fence&lt;/b&gt;', page)
        self.assertIn('<pre lang="r&amp;#97whtml">', render_blog(source, '2025-01-02')[2])

    def test_entities_are_unescaped_exactly_once(self):
        raw = '<span title="&quot;">&lt;b&gt; &amp; &#x3C; &#60; &copy;</span>\n'
        _, page = self.page('```rawhtml\n' + raw + '```\n')
        self.assertIn(raw, page)
        self.assertNotIn('<span title="\"">', page)
        self.assertNotIn('<span title="&quot;"><b>', page)

    def test_original_raw_newlines_and_unclosed_final_line_are_preserved(self):
        source = self.write('data/sources/blog/nest/20250102.md', '')
        source.write_bytes(b'# Title\r\n\r\n> ~~~rawhtml\r\n'
                           b'> <b>CRLF</b>\r\n> <i>LF</i>\n> ~~~\r\n\n'
                           b'```rawhtml\r\n<p>no final newline</p>')
        build(self.root, self.site, 'blog')
        page = (self.site / 'data/contents/blog/nest/2025-01-02.html').read_bytes()
        self.assertIn(b'<b>CRLF</b>\r\n<i>LF</i>\n', page)
        self.assertIn(b'<p>no final newline</p>\n</main>', page)
        marked, mark = mark_custom_fences(source)
        _, _, rendered = render_blog(marked, '2025-01-02')
        _, embeds = extract_custom_fences(rendered, mark)
        self.assertEqual(embeds[-1].content, '<p>no final newline</p>')

    def test_e2d3_sample_multiple_duplicate_and_nested_paths(self):
        one = '# template\nBar Chart\n\n# data\n年,値\n2025,42\n'
        two = '# template\n折れ線図\n# data\n月,値\n1,2\n'
        empty = '# template\nEmpty Data\n# data\n'
        fences = ''.join('```e2d3\n' + content + '```\n\n'
                         for content in (one, two, one, empty))
        self.write('data/sources/blog/20250101.md', '# Root\n\n' + fences)
        self.write('data/sources/blog/level/deep/20250102.md', '# Nested\n\n' + fences)
        build(self.root, self.site, 'blog')
        root_path = self.site / 'data/contents/blog/2025-01-01.html'
        nested_path = self.site / 'data/contents/blog/level/deep/2025-01-02.html'
        for path, prefix in ((root_path, '_e2d3/'), (nested_path, '../../_e2d3/')):
            page = path.read_text(encoding='utf-8')
            blocks = e2d3_blocks(page)
            self.assertEqual(len(blocks), 4)
            for match, template, data, source in zip(blocks,
                    ('Bar Chart', '折れ線図', 'Bar Chart', 'Empty Data'),
                    ('年,値\n2025,42\n', '月,値\n1,2\n', '年,値\n2025,42\n', ''),
                    (one, two, one, empty)):
                expected = json.dumps({'template': template, 'data': data}, ensure_ascii=False,
                                      sort_keys=True, separators=(',', ':')).encode('utf-8')
                self.assertEqual(base64.b64decode(match.group(1), validate=True), expected)
                digest = hashlib.sha256(expected).hexdigest()
                self.assertEqual(match.group(2), prefix + digest + '.html')
                self.assertEqual((path.parent / match.group(2)).resolve(),
                                 self.site / 'data/contents/blog/_e2d3' / (digest + '.html'))
                self.assertEqual(html.unescape(match.group(3)), 'E2D3: ' + template)
                self.assertEqual(html.unescape(match.group(4)), source)
            self.assertEqual(blocks[0].group(1), blocks[2].group(1))
            self.assertEqual(blocks[0].group(2), blocks[2].group(2))
            self.assertNotIn(' sandbox=', page)
        first = (root_path.read_bytes(), nested_path.read_bytes())
        build(self.root, self.site, 'blog')
        self.assertEqual(first, (root_path.read_bytes(), nested_path.read_bytes()))

    def test_e2d3_preserves_exact_utf8_line_endings_data_and_empty_data(self):
        source = self.write('data/sources/blog/nest/20250102.md', '')
        raw = (b'# T\r\n\r\n> - ```e2d3\r\n>   # template\r\n'
               b'>   \xe5\x9b\xb3 Chart\n>   \r\n>   # data\r\n'
               b'>   text,value\r\n>   \xe6\xbc\xa2\xe5\xad\x97,\x00\xef\xbf\xbd\n'
               b'>   \tquote,"\xc3\xa9"\r\n>   ```\r\n\r\n'
               b'```e2d3\n# template\nEmpty\n# data')
        source.write_bytes(raw)
        build(self.root, self.site, 'blog')
        page = (self.site / 'data/contents/blog/nest/2025-01-02.html').read_bytes().decode('utf-8')
        blocks = e2d3_blocks(page)
        self.assertEqual(len(blocks), 2)
        spec = json.loads(base64.b64decode(blocks[0].group(1)).decode('utf-8'))
        self.assertEqual(spec, {'template': '図 Chart',
                                'data': 'text,value\r\n漢字,\x00\ufffd\n\tquote,"é"\r\n'})
        spec = json.loads(base64.b64decode(blocks[1].group(1)).decode('utf-8'))
        self.assertEqual(spec, {'template': 'Empty', 'data': ''})
        self.assertEqual(html.unescape(blocks[1].group(4)), '# template\nEmpty\n# data')

    def test_e2d3_escapes_title_and_full_source_without_injection(self):
        template = 'Chart " onload="evil & <img>'
        data = ('name,value\n</code></pre></details><script>alert(1)</script>,&amp;\n'
                '<img src=x onerror=alert(2)>,"quoted"\n')
        content = '# template\n' + template + '\n# data\n' + data
        _, page = self.page('```e2d3\n' + content + '```\n')
        blocks = e2d3_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(base64.b64decode(blocks[0].group(1))),
                         {'template': template, 'data': data})
        self.assertEqual(html.unescape(blocks[0].group(4)), content)
        self.assertEqual(html.unescape(blocks[0].group(3)), 'E2D3: ' + template)
        parser = TagCollector()
        parser.feed(page)
        self.assertNotIn('script', [tag for tag, _ in parser.tags])
        self.assertNotIn('img', [tag for tag, _ in parser.tags])
        frames = [attrs for tag, attrs in parser.tags if tag == 'iframe']
        self.assertEqual(len(frames), 1)
        self.assertEqual(set(frames[0]), {'class', 'src', 'title', 'loading'})
        self.assertEqual(frames[0]['title'], 'E2D3: ' + template)

    def test_e2d3_csv_does_not_reinterpret_section_lines_or_fences(self):
        data = ('# template\nnot another section\n# data\n'
                '```e2d3\n# template\nInner\n# data\nx,y\n```\n'
                '```youtube\nhttps://youtu.be/B74K2b7IZDw\n```\n')
        content = '# template\n  Chart With Spaces  \n# data\n' + data
        _, page = self.page('````e2d3\n' + content + '````\n')
        blocks = e2d3_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(base64.b64decode(blocks[0].group(1))),
                         {'template': '  Chart With Spaces  ', 'data': data})
        self.assertEqual(html.unescape(blocks[0].group(4)), content)
        self.assertNotIn('youtube.com/embed', page)
        self.assertNotIn('glcustom', page)

    def test_e2d3_rejects_malformed_sections_and_unsafe_template_paths(self):
        invalid = [
            '', '# template\n\n# data\na,b\n', '# template\n   \n# data\n',
            '# template \nchart\n# data\na,b\n', '# Template\nchart\n# data\na,b\n',
            '# template\nchart\n# data \na,b\n', '# template\nchart\n# datax\na,b\n',
            '# data\na,b\n# template\nchart\n', '# template\nchart\nextra\n# data\n',
            '# template\nchart\n', '# template\n.\n# data\n',
            '# template\n..\n# data\n', '# template\n../chart\n# data\n',
            '# template\nchart/other\n# data\n', '# template\nchart\\other\n# data\n',
            '# template\nC:chart\n# data\n', '# template\nz:\n# data\n',
            '# template\nchart..other\n# data\n', '# template\nchart\tname\n# data\n',
            '# template\nchart\x7fname\n# data\n', '# template\nchart\x85name\n# data\n',
            '# template\nchart\x00name\n# data\n',
        ]
        for content in invalid:
            with self.subTest(content=repr(content)):
                self.assertIsNone(custom_embeds.parse_e2d3(content))
        markdown = '\n'.join('```e2d3\n' + content + ('\n' if content and not content.endswith('\n') else '')
                             + '```\n' for content in invalid)
        source, page = self.page(markdown)
        self.assertIn(render_blog(source, '2025-01-02')[2], page)
        self.assertEqual(page.count('<pre lang="e2d3">'), len(invalid))
        self.assertNotIn('class="main-e2d3"', page)
        self.assertNotIn('<iframe', page)
        self.assertIsNone(custom_embeds.render_e2d3('# template\n\ud800\n# data\n'))

    def test_e2d3_only_real_trusted_fences_and_not_issue_comments(self):
        content = '# template\nChart\n# data\na,b\n1,2\n'
        raw = ('<pre lang="e2d3"><code>not an embed</code></pre>\n'
               '<section class="main-e2d3" data-e2d3-spec="not!base64">'
               '<p>author HTML</p></section>\n'
               '```e2d3\n' + content + '```\n')
        markdown = ('<pre lang="e2d3"><code>handwritten</code></pre>\n\n'
                    '````python\n```e2d3\n' + content + '```\n````\n\n'
                    '<div>\n```e2d3\n' + content + '```\n</div>\n\n'
                    '~~~~rawhtml\n' + raw + '~~~~\n\n'
                    '> - ~~~e&#50;d3 extra\n>   # template\n>   Chart\n'
                    '>   # data\n>   a,b\n>   1,2\n>   ~~~\n')
        source, page = self.page(markdown)
        blocks = e2d3_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(base64.b64decode(blocks[0].group(1))),
                         {'template': 'Chart', 'data': 'a,b\n1,2\n'})
        self.assertIn(raw, page)
        manifest = json.loads((self.site / '.greenlog-e2d3-input.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['format'], 'greenlog-e2d3-input-v1')
        self.assertEqual(manifest['specs'], [blocks[0].group(1)])
        self.assertIn('<pre lang="e2d3"><code>handwritten</code></pre>', page)
        baseline = render_blog(source, '2025-01-02')[2]
        start = baseline.index('<pre lang="python">')
        outer = baseline[start:baseline.index('</code></pre>', start) + len('</code></pre>')]
        self.assertIn(outer, page)
        self.assertNotIn('glcustom', page)
        with patch('custom_embeds.render_e2d3') as renderer:
            comment = render_markdown('```e2d3\n' + content + '<script>evil</script>\n```', 'comment-1-')
        renderer.assert_not_called()
        self.assertIn('<pre lang="e2d3">', comment)
        self.assertIn('&lt;script&gt;evil&lt;/script&gt;', comment)
        self.assertNotIn('<iframe', comment)
        self.assertNotIn('class="main-e2d3"', comment)

    def test_invalid_services_preserve_exact_rendered_blocks(self):
        parts = []
        for kind in sorted(custom_embeds.SERVICES):
            parts.append(f'```{kind}\nhttps://evil.example/<script>?x=1&y=2\n```\n')
        markdown = '\n'.join(parts) + '\n```unknown\n<script>also code</script>\n```\n'
        source, page = self.page(markdown)
        original = render_blog(source, "2025-01-02")[2]
        self.assertIn(original, page)
        self.assertEqual(page.count('<pre lang='), 6)
        self.assertNotIn('<iframe', page)
        self.assertNotIn('<script>', page)
        for kind in custom_embeds.SERVICES:
            self.assertIsNone(custom_embeds.render_service_embed(kind, 'javascript:alert(1)'))

    def test_all_sample_services_and_raw_html(self):
        markdown = ('# Embeds\n\n'
                    '```youtube\nhttps://www.youtube.com/watch?v=B74K2b7IZDw\n```\n\n'
                    '```instagram\nhttps://www.instagram.com/p/DS4OOh9CXo0/\n```\n\n'
                    '```instagram\nhttps://www.instagram.com/junichi.watanuki/p/DdN-lNLCWtm/?img_index=7\n```\n\n'
                    '```twitter\nhttps://x.com/j_watanuki/status/2107102704682344583\n```\n\n'
                    '```garmin\nhttps://connect.garmin.com/app/activity/25084785219\n```\n\n'
                    '```slideshare\nhttps://www.slideshare.net/slideshow/ss-41306350/41306350\n```\n\n'
                    '```rawhtml\n<button id="myButton">押す</button>\n'
                    '<script>document.getElementById("myButton").onclick = () => alert(1);</script>\n```\n')
        _, page = self.page(markdown)
        self.assertIn('src="https://www.youtube.com/embed/B74K2b7IZDw"', page)
        self.assertIn('src="https://www.instagram.com/p/DS4OOh9CXo0/embed/"', page)
        self.assertIn('src="https://www.instagram.com/p/DdN-lNLCWtm/embed/"', page)
        self.assertIn('href="https://www.instagram.com/junichi.watanuki/p/DdN-lNLCWtm/?img_index=7"', page)
        self.assertIn('<blockquote class="twitter-tweet">', page)
        self.assertIn('href="https://x.com/j_watanuki/status/2107102704682344583"', page)
        self.assertIn('src="https://platform.twitter.com/widgets.js"', page)
        self.assertIn('src="https://connect.garmin.com/embed/activity/25084785219"', page)
        self.assertIn('src="https://www.slideshare.net/slideshow/embed_code/41306350"', page)
        self.assertIn('<button id="myButton">押す</button>\n'
                      '<script>document.getElementById("myButton").onclick = () => alert(1);</script>\n', page)
        self.assertNotIn('<pre lang="youtube">', page)
        self.assertEqual(page.count('class="main-embed-source"'), 6)

    def test_alternate_valid_service_urls(self):
        examples = {
            'youtube': [('https://youtu.be/B74K2b7IZDw?t=10', 'youtube.com/embed/B74K2b7IZDw?start=10'),
                        ('https://youtube.com/watch?v=B74K2b7IZDw&t=1m30s', 'youtube.com/embed/B74K2b7IZDw?start=90'),
                        ('https://youtube.com/watch?v=B74K2b7IZDw&start=90', 'youtube.com/embed/B74K2b7IZDw?start=90'),
                        ('https://www.youtube.com/shorts/B74K2b7IZDw', 'youtube.com/embed/B74K2b7IZDw')],
            'instagram': [('https://instagram.com/reel/DdN-lNLCWtm/', 'instagram.com/reel/DdN-lNLCWtm/embed/')],
            'twitter': [('https://twitter.com/j_watanuki/statuses/2107102704682344583', 'twitter-tweet')],
            'garmin': [('https://connect.garmin.com/modern/activity/embed/25084785219', 'connect.garmin.com/embed/activity/25084785219')],
            'slideshare': [('https://www.slideshare.net/slideshow/embed_code/key/tWHUZcwySlUKK6', 'slideshare.net/slideshow/embed_code/key/tWHUZcwySlUKK6')],
        }
        for kind, cases in examples.items():
            for url, expected in cases:
                with self.subTest(kind=kind, url=url):
                    result = custom_embeds.render_service_embed(kind, url + '\n')
                    self.assertIsNotNone(result)
                    self.assertIn(expected, result)

    def test_service_urls_reject_wrong_hosts_paths_and_multiline(self):
        cases = {
            'youtube': ['https://youtube.com/watch?v=B74K2b7IZDw&v=XXXXXXXXXXX',
                        'https://youtube.com.evil.test/watch?v=B74K2b7IZDw',
                        'https://www.youtube.com/watch?v=B74K2b7IZDw\nhttps://evil.test/'],
            'instagram': ['https://evil.test/p/DdN-lNLCWtm/', 'https://www.instagram.com/stories/user/123'],
            'twitter': ['https://x.com/j_watanuki/status/1/photo/1', 'https://x.com/j_watanuki/status/not-a-number'],
            'garmin': ['https://connect.garmin.com/app/course/123', 'https://connect.garmin.com.evil.test/app/activity/123'],
            'slideshare': ['https://www.slideshare.net/slideshow/embed_code/key/../../x', 'https://www.slideshare.net/profile/1'],
        }
        for kind, values in cases.items():
            for value in values:
                with self.subTest(kind=kind, value=value):
                    self.assertIsNone(custom_embeds.render_service_embed(kind, value))
            for suffix in ['@evil.test', ':444', '\\evil.test']:
                invalid = 'https://www.youtube.com' + suffix + '/watch?v=B74K2b7IZDw'
                with self.subTest(kind=kind, invalid=invalid):
                    self.assertIsNone(custom_embeds.render_service_embed(kind, invalid))

    def test_malicious_service_urls_are_not_embedded(self):
        candidates = [
            'javascript:alert(1)', 'data:text/html,<script>alert(1)</script>',
            'https://youtube.com.evil.test/watch?v=one',
            'https://youtube.com@evil.test/watch?v=one',
            'https://evil.test/youtube.com/watch?v=one',
        ]
        markdown = '\n'.join(f'```{kind}\n{url}\n```\n'
                             for kind in sorted(custom_embeds.SERVICES) for url in candidates)
        _, page = self.page(markdown)
        self.assertEqual(page.count('<pre lang='), len(custom_embeds.SERVICES) * len(candidates))
        self.assertNotIn('<iframe', page)
        self.assertNotIn('<script>', page)

    def test_all_previews_coexist_with_services_rawhtml_and_plain_code(self):
        mermaid = 'flowchart TD\n  A["日本語 & <表示>"] --> B["__ARTICLE_TITLE__"]\n'
        stl = ('solid STLB ATF 2.0.0.9000 COLOR=����  \n'
               ' facet normal 0 0 1\n  outer loop\n'
               '   vertex 0 0 0\n   vertex 1 0 0\n   vertex 0 1 0\n'
               '  endloop\n endfacet\nendsolid\n')
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>日本語 &amp; test</text></svg>\n'
        url = 'https://example.test/models/a.stl?x=1&y=2\n'
        relative = '../models/a%20b.stl?x=1&y=2#part\n'
        markdown = ('# Title\n\n'
                    '```rawmermaid\n' + mermaid + '```\n\n'
                    '```rawstl\n' + stl + '```\n\n'
                    '```rawstl\n' + url + '```\n\n'
                    '```rawstl\n' + relative + '```\n\n'
                    '```rawsvg\n' + svg + '```\n\n'
                    '```mermaid\n<script>plain mermaid</script>\n```\n\n'
                    '```stl\n<script>plain stl</script>\n```\n\n'
                    '```svg\n<script>plain svg</script>\n```\n\n'
                    '```youtube\nhttps://youtu.be/B74K2b7IZDw\n```\n\n'
                    '```rawhtml\n<button>Trusted HTML</button>\n```\n')
        _, page = self.page(markdown)
        blocks = previews(page)
        self.assertEqual([(kind, code) for kind, _, code in blocks], [
            ('mermaid', mermaid), ('stl', stl), ('stl', url),
            ('stl', relative), ('svg', svg),
        ])
        self.assertEqual(page.count('class="main-code-preview"'), 5)
        self.assertTrue(all(display == '' for _, display, _ in blocks[:-1]))
        data = base64.b64encode(svg.encode('utf-8')).decode('ascii')
        self.assertEqual(blocks[-1][1], f'<img src="data:image/svg+xml;base64,{data}" '
                                      'alt="SVG プレビュー" loading="lazy">')
        self.assertIn('A[&quot;日本語 &amp; &lt;表示&gt;&quot;]', page)
        self.assertIn('__ARTICLE_TITLE__', blocks[0][2])  # no late template expansion
        self.assertIn('src="https://www.youtube.com/embed/B74K2b7IZDw"', page)
        self.assertIn('<button>Trusted HTML</button>', page)
        key = syntax_key(page)
        for kind in ('mermaid', 'stl', 'svg'):
            self.assertIn(f'<pre lang="{kind}" data-syntax-highlight="{key}"><code>&lt;script&gt;plain {kind}&lt;/script&gt;\n'
                          '</code></pre>', page)
        self.assertNotIn('<script>plain', page)

    def test_trusted_rawhtml_code_is_not_marked_for_highlighting(self):
        raw = ('<pre lang="svg"><code><button id="keep">実行</button></code></pre>\n'
               '<pre lang="stl"><code>solid trusted</code></pre>\n'
               '<script>document.getElementById("keep").onclick = () => { window.clicked = true; };</script>\n')
        markdown = ('```rawhtml\n' + raw + '```\n\n'
                    '```svg\n<svg/>\n```\n\n```stl\nsolid s\nendsolid s\n```\n\n'
                    '```mermaid\ngraph TD\nA --> B\n```\n')
        _, page = self.page(markdown)
        self.assertIn(raw, page)
        self.assertNotIn('<pre lang="svg" data-syntax-highlight><code><button', page)
        self.assertNotIn('<pre lang="stl" data-syntax-highlight><code>solid trusted', page)
        key = syntax_key(page)
        for kind in ('svg', 'stl', 'mermaid'):
            self.assertIn(f'<pre lang="{kind}" data-syntax-highlight="{key}"><code>', page)

    def test_only_real_normal_fences_receive_the_article_key(self):
        raw = ('<pre lang=svg data-syntax-highlight><code><button>keep</button></code></pre>\n'
               '<pre lang=stl data-syntax-highlight="manual"><code>solid raw</code></pre>\n'
               '```svg\n<svg>raw example</svg>\n```\n')
        markdown = ('<h1>\n```svg\n</h1>\n\n# svg\n\n'
                    '<pre lang="svg"><code><a href="#manual">manual</a></code></pre>\n\n'
                    '````python\n```mermaid\ngraph TD\n```\n'
                    '```stl\nsolid example\n```\n'
                    '```svg\n<svg>example</svg>\n```\n````\n\n'
                    '~~~~rawhtml\n' + raw + '~~~~\n\n'
                    '> - ~~~S&#86;G&#32;extra\n>   <svg>nested</svg>\n>   ~~~\n\n'
                    '```Mermaid\ngraph TD\nA --> B\n```\n\n'
                    '~~~stl\nsolid s\nendsolid s\n~~~\n')
        source, page = self.page(markdown)
        key = syntax_key(page)
        baseline = render_blog(source, '2025-01-02')[2]
        self.assertNotIn(key, markdown)
        self.assertNotIn(key, html.unescape(markdown))
        self.assertIn(raw, page)  # rawhtml, including candidate lines, is byte-for-byte intact
        self.assertIn('<pre lang="svg"><code><a href="#manual"', page)
        outer = baseline[baseline.index('<pre lang="python">'):]
        outer = outer[:outer.index('</code></pre>') + len('</code></pre>')]
        self.assertIn(outer, page)
        self.assertIn('<h1 id="svg">', page)
        self.assertIn('<h1 id="svg-1">', page)
        for kind in ('SVG', 'Mermaid', 'stl'):
            self.assertIn(f'<pre lang="{kind}" data-syntax-highlight="{key}"><code>', page)
        self.assertEqual(page.count(f'data-syntax-highlight="{key}"'), 3)
        self.assertNotIn('class="main-code-preview"', page)
        self.assertNotIn('glcustom', page)

    def test_syntax_key_is_stable_article_specific_and_avoids_collisions(self):
        one = 'glsyntax' + 'a' * 64
        two = 'glsyntax' + 'b' * 64
        source = '<pre data-syntax-highlight="&#103;' + one[1:] + '"></pre>'
        for markdown, template in ((source, ''), ('', one), ('```rawhtml\n' + one + '\n```', '')):
            with self.subTest(markdown=markdown, template=template), \
                    patch('build_content._syntax_key_candidate', side_effect=[one, two]) as candidate:
                self.assertEqual(syntax_highlight_key(markdown, 'post.html', template), two)
                self.assertEqual(candidate.call_count, 2)
        self.assertNotEqual(syntax_highlight_key('same', 'one.html', 'template'),
                            syntax_highlight_key('same', 'two.html', 'template'))
        markdown = ('```rawhtml\n<pre lang=svg data-syntax-highlight>'
                    '<code><button>unchanged</button></code></pre>\n```\n\n'
                    '```svg\n<svg/>\n```\n')
        source_path, first = self.page(markdown)
        key = syntax_key(first)
        template = (self.root / 'data/sources/blog/index.html').read_text(encoding='utf-8')
        self.assertNotIn(key, source_path.read_text(encoding='utf-8'))
        self.assertNotIn(key, template)
        output = self.site / 'data/contents/blog/nest/2025-01-02.html'
        first_bytes = output.read_bytes()
        build(self.root, self.site, 'blog')
        self.assertEqual(output.read_bytes(), first_bytes)

    def test_svg_scripts_are_only_in_base64_image_and_escaped_source(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" onload="parent.x=1">'
               '<script>parent.x=2; &lt;/code&gt;</script>'
               '<foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
               '<img src="x" onerror="parent.x=3"/></body></foreignObject>'
               '<text>日本語 &amp; &#x3C; &quot; &#39;</text></svg>\n')
        _, page = self.page('```rawsvg\n' + svg + '```\n')
        blocks = previews(page)
        self.assertEqual(len(blocks), 1)
        kind, image, source = blocks[0]
        self.assertEqual(kind, 'svg')
        self.assertEqual(source, svg)
        match = re.fullmatch(r'<img src="data:image/svg\+xml;base64,([A-Za-z0-9+/]*={0,2})" '
                             r'alt="SVG プレビュー" loading="lazy">', image)
        self.assertIsNotNone(match)
        self.assertEqual(base64.b64decode(match.group(1), validate=True), svg.encode('utf-8'))
        self.assertIn('&lt;script&gt;parent.x=2;', page)
        self.assertIn('&amp;lt;/code&amp;gt;', page)
        parser = TagCollector()
        parser.feed(page)
        names = [tag for tag, _ in parser.tags]
        self.assertNotIn('script', names)
        self.assertNotIn('svg', names)
        self.assertNotIn('foreignobject', names)
        images = [attrs for tag, attrs in parser.tags if tag == 'img']
        self.assertEqual(len(images), 1)
        self.assertEqual(set(images[0]), {'src', 'alt', 'loading'})
        self.assertNotIn('onerror', images[0])

    @unittest.skipUnless(importlib.util.find_spec('playwright') and
                         (shutil.which('chromium') or shutil.which('chromium-browser') or
                          shutil.which('google-chrome')),
                         'Playwright and a local Chromium are required')
    def test_browser_highlights_only_real_fences_and_preserves_interactive_dom(self):
        from playwright.sync_api import sync_playwright

        # The module import can use the actual CDN library supplied by the test
        # runner; the fallback is deterministic and keeps CI network-independent.
        library_path = os.environ.get('GREENLOG_TEST_HIGHLIGHT_JS')
        if library_path:
            library = Path(library_path).read_text(encoding='utf-8')
        else:
            library = r'''const hljs = {
              QUOTE_STRING_MODE: {}, registerLanguage() {},
              getLanguage(name) { return ['mermaid', 'stl', 'xml'].includes(name); },
              highlight(text) { const escaped = text.replace(/&/g, '&amp;')
                .replace(/</g, '&lt;').replace(/>/g, '&gt;');
                return {value: '<span class="hljs-keyword">' + escaped + '</span>'}; }
            }; export default hljs;'''
        script = (Path(custom_embeds.__file__).resolve().parent.parent /
                  'data/sources/blog/preview.js').read_text(encoding='utf-8')
        template = ('<!doctype html><html><head><title>__ARTICLE_TITLE__</title>'
                    '<style>.hljs span { color: rgb(18, 52, 86); }</style>'
                    '<script type="module">import {initPreviews} from "/preview.js";'
                    'initPreviews();</script></head><body><time>__ARTICLE_DATE__</time>'
                    '<main class="main-article-body"><!-- ARTICLE_CONTENT --></main></body></html>')
        self.write('data/sources/blog/index.html', template)
        raw = ('<pre lang=svg data-syntax-highlight><code><button id="raw-button">raw</button></code></pre>\n'
               '<pre lang=svg data-syntax-highlight="manual"><code><button id="same-key-button">same</button></code></pre>\n'
               '<pre id="raw-text" lang=mermaid data-syntax-highlight><code>graph TD\nA --&gt; B</code></pre>\n'
               '<script>\n'
               'window.rawRef = document.getElementById("raw-button");\n'
               'window.sameKeyRef = document.getElementById("same-key-button");\n'
               'window.manualRef = document.querySelector("pre:not([data-syntax-highlight]) code a");\n'
               'window.clicks = 0;\n'
               'window.rawRef.onclick = window.sameKeyRef.onclick = () => { window.clicks++; };\n'
               'window.manualRef.onclick = e => { e.preventDefault(); window.clicks++; };\n'
               'window.sameKeyRef.closest("pre").setAttribute("data-syntax-highlight",\n'
               '  document.querySelector(".main-article-body > span[data-greenlog-syntax-key]")'
               '.getAttribute("data-greenlog-syntax-key"));\n'
               '</script>\n')
        markdown = ('<pre lang=svg><code><a href="#manual">manual link</a></code></pre>\n\n'
                    '```rawhtml\n' + raw + '```\n\n'
                    '```mermaid\ngraph TD\nA --> B\n```\n\n'
                    '```stl\nsolid s\nvertex 0 1 2\nendsolid s\n```\n\n'
                    '```svg\n<svg width="10"><circle r="1"/></svg>\n```\n')
        _, built = self.page(markdown)
        self.assertIn(raw, built)
        key = syntax_key(built)
        self.assertNotIn(key, raw)
        binary = (shutil.which('chromium') or shutil.which('chromium-browser') or
                  shutil.which('google-chrome'))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=binary, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                def respond(route):
                    url = route.request.url
                    if url == 'https://greenlog.test/article':
                        route.fulfill(status=200, content_type='text/html', body=built)
                    elif url == 'https://greenlog.test/preview.js':
                        route.fulfill(status=200, content_type='text/javascript', body=script)
                    elif url == 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.12.0/es/highlight.min.js':
                        route.fulfill(status=200, content_type='text/javascript', body=library,
                                      headers={'Access-Control-Allow-Origin': '*'})
                    else:
                        route.abort()
                page.route('**/*', respond)
                page.goto('https://greenlog.test/article', wait_until='load')
                page.wait_for_function('''() => ['mermaid', 'stl', 'svg'].every(lang =>
                  document.querySelector(`pre[lang="${lang}"] > code.hljs span`))''', timeout=10000)
                for kind, text in (('mermaid', 'graph TD\nA --> B\n'),
                                   ('stl', 'solid s\nvertex 0 1 2\nendsolid s\n'),
                                   ('svg', '<svg width="10"><circle r="1"/></svg>\n')):
                    with self.subTest(kind=kind):
                        code = page.locator(f'pre[lang="{kind}"] > code.hljs')
                        self.assertEqual(code.count(), 1)
                        self.assertEqual(code.text_content(), text)
                        self.assertEqual(code.locator('span').first.evaluate(
                            '(span) => getComputedStyle(span).color'), 'rgb(18, 52, 86)')
                self.assertEqual(page.locator('#same-key-button').locator('..').locator('..')
                                 .get_attribute('data-syntax-highlight'), key)
                self.assertEqual(page.locator('#raw-text > code').text_content(), 'graph TD\nA --> B')
                self.assertEqual(page.locator('#raw-text > code span').count(), 0)
                self.assertNotIn('hljs', page.locator('#raw-text > code').get_attribute('class') or '')
                self.assertEqual(page.locator('pre:not([data-syntax-highlight]) code a').count(), 1)
                page.locator('#raw-button').click()
                page.locator('#same-key-button').click()
                page.locator('pre:not([data-syntax-highlight]) code a').click()
                self.assertEqual(page.evaluate('''() => ({clicks: window.clicks,
                  raw: window.rawRef === document.getElementById('raw-button'),
                  same: window.sameKeyRef === document.getElementById('same-key-button'),
                  manual: window.manualRef === document.querySelector('pre:not([data-syntax-highlight]) code a')})'''),
                    {'clicks': 3, 'raw': True, 'same': True, 'manual': True})
            finally:
                browser.close()

    @unittest.skipUnless(importlib.util.find_spec('playwright') and
                         (shutil.which('chromium') or shutil.which('chromium-browser') or
                          shutil.which('google-chrome')),
                         'Playwright and a local Chromium are required')
    def test_svg_script_does_not_execute_in_browser_image_context(self):
        from playwright.sync_api import sync_playwright

        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
               'onload="top.postMessage(\'SVG_ATTACK\', \'*\')">'
               '<script>top.postMessage("SVG_ATTACK", "*")</script>'
               '<rect width="10" height="10" fill="red"/></svg>\n')
        _, built = self.page('```rawsvg\n' + svg + '```\n')
        block = PREVIEW_BLOCK.search(built)
        self.assertIsNotNone(block)
        harness = ('<!doctype html><html><body><script>window.svgAttacks = 0;'
                   'window.addEventListener("message", e => {'
                   'if (e.data === "SVG_ATTACK") window.svgAttacks++;});</script>'
                   + block.group(0) + '</body></html>')
        binary = (shutil.which('chromium') or shutil.which('chromium-browser') or
                  shutil.which('google-chrome'))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=binary, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                page.set_content(harness, wait_until='load')
                page.evaluate('document.querySelector("img").decode()')
                self.assertGreater(page.locator('img').evaluate('(img) => img.naturalWidth'), 0)
                self.assertEqual(page.locator('.main-code-preview-source code').text_content(), svg)
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate('window.svgAttacks'), 0)
            finally:
                browser.close()

    def test_preview_nested_fences_markdown_entities_and_fake_pre(self):
        inner = ('flowchart LR\nA --> B\n```rawsvg\n'
                 '<svg>nested example</svg>\n```\n')
        markdown = ('<pre lang="rawsvg"><code>&lt;svg&gt;fake&lt;/svg&gt;</code></pre>\n\n'
                    '````python\n```rawmermaid\n<script>example</script>\n```\n'
                    '```rawstl\nsolid fake\n```\n````\n\n'
                    '> - ~~~R&#65;WSTL&#32;extra\n>   solid nested\n'
                    '>    facet normal 0 0 1\n>   endsolid\n>   ~~~\n\n'
                    '~~~~rawmermaid\n' + inner + '~~~~\n\n'
                    '```RaWsVg\n<svg xmlns="http://www.w3.org/2000/svg"/>\n```\n')
        source, page = self.page(markdown)
        blocks = previews(page)
        self.assertEqual([(kind, code) for kind, _, code in blocks], [
            ('stl', 'solid nested\n facet normal 0 0 1\nendsolid\n'),
            ('mermaid', inner),
            ('svg', '<svg xmlns="http://www.w3.org/2000/svg"/>\n'),
        ])
        self.assertIn('<pre lang="rawsvg"><code>&lt;svg&gt;fake&lt;/svg&gt;</code></pre>', page)
        baseline = render_blog(source, '2025-01-02')[2]
        start = baseline.index('<pre lang="python">')
        outer_code = baseline[start:baseline.index('</code></pre>', start) + len('</code></pre>')]
        self.assertIn(outer_code, page)
        self.assertNotIn('glcustom', page)
        self.assertNotIn('<script>example</script>', page)

    def test_invalid_preview_syntax_and_unsupported_values_preserve_source(self):
        # Mermaid/STL parsing belongs to the browser: invalid or empty input is
        # not rewritten or discarded; the source remains available there.
        svg = '<svg><script>broken SVG & bad entity</script>\n'
        markdown = ('```rawmermaid\nthis is not a diagram <script>x</script>\n```\n\n'
                    '```rawstl\nnot STL <img src=x onerror=x>\n```\n\n'
                    '```rawstl\n```\n\n'
                    '```rawsvg\n' + svg + '```\n')
        _, page = self.page(markdown)
        blocks = previews(page)
        self.assertEqual([(kind, code) for kind, _, code in blocks], [
            ('mermaid', 'this is not a diagram <script>x</script>\n'),
            ('stl', 'not STL <img src=x onerror=x>\n'), ('stl', ''), ('svg', svg),
        ])
        data = blocks[-1][1].split('base64,', 1)[1].split('"', 1)[0]
        self.assertEqual(base64.b64decode(data, validate=True), svg.encode('utf-8'))
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img src=x', page)
        self.assertIsNone(custom_embeds.render_code_preview('svg', '<svg/>'))
        for kind in custom_embeds.PREVIEWS:
            self.assertIsNone(custom_embeds.render_code_preview(kind, '\ud800'))
        _, _, _, held, embeds = self.render_held('```rawsvg\n<svg/>\n```\n')
        unsupported = replace(embeds[0], content='\ud800')
        self.assertEqual(insert_custom_fences(held, [unsupported]), embeds[0].original + '\n')

    def test_rebuilding_with_fences_is_deterministic(self):
        _, first = self.page('~~~rawhtml\n<b>Same</b>\n~~~\n\n```youtube\ninvalid\n```\n\n'
                             '```rawmermaid\ngraph TD\n A --> B\n```\n\n'
                             '```rawstl\nsolid cube\nendsolid\n```\n\n'
                             '```rawsvg\n<svg xmlns="http://www.w3.org/2000/svg"/>\n```\n')
        build(self.root, self.site, 'blog')
        second = (self.site / 'data/contents/blog/nest/2025-01-02.html').read_text(encoding='utf-8')
        self.assertEqual(first, second)

    def test_service_dispatch_is_separate_and_receives_decoded_content(self):
        markdown = ('```youtube\nhttps://example.test/?a=1&b=2\n```\n\n'
                    '```instagram\n<script>not HTML input</script>\n```\n')
        def renderer(kind, content):
            return f'<aside id="template-id" data-url="/never rewrite" data-k="{kind}">' \
                   '__COMMENT_POST_PATH__</aside>' if kind == 'youtube' else None
        with patch('custom_embeds.render_service_embed', side_effect=renderer) as mock:
            _, page = self.page(markdown)
        self.assertEqual([c.args for c in mock.call_args_list], [
            ('youtube', 'https://example.test/?a=1&b=2\n'),
            ('instagram', '<script>not HTML input</script>\n'),
        ])
        self.assertIn('<aside id="template-id" data-url="/never rewrite" data-k="youtube">'
                      '__COMMENT_POST_PATH__</aside>', page)
        self.assertIn('<pre lang="instagram"><code>&lt;script&gt;not HTML input&lt;/script&gt;\n'
                      '</code></pre>', page)

    def test_ordinary_code_and_raw_pre_tags_are_not_mistaken_for_fences(self):
        markdown = ('<pre lang="rawhtml"><code>&lt;b&gt;fake&lt;/b&gt;</code></pre>\n\n'
                    '<pre lang="youtube"><code>https://fake.example</code></pre>\n\n'
                    '````python\n```rawhtml\n<script>example</script>\n```\n'
                    '```youtube\nhttps://example.test\n```\n````\n\n'
                    '<div>\n```rawhtml\n&lt;em&gt;HTML block&lt;/em&gt;\n```\n</div>\n\n'
                    '<script>alert(9)</script>\n\n'
                    '```rawhtml\n<strong>Real</strong>\n```\n')
        source, _, _, body, embeds = self.render_held(markdown)
        with patch('custom_embeds.render_service_embed') as mock:
            body = insert_custom_fences(body, embeds)
        mock.assert_not_called()
        self.assertEqual(len(embeds), 1)
        self.assertIn('<pre lang="rawhtml"><code>&lt;b&gt;fake&lt;/b&gt;</code></pre>', body)
        self.assertIn('<pre lang="youtube"><code>https://fake.example</code></pre>', body)
        self.assertNotIn('<script>alert(9)</script>', body)
        self.assertIn('<strong>Real</strong>\n', body)
        original = render_blog(source, "2025-01-02")[2]
        self.assertEqual(body.replace('<strong>Real</strong>\n',
                                      '<pre lang="rawhtml"><code>&lt;strong&gt;Real&lt;/strong&gt;\n</code></pre>'),
                         original)

    def test_raw_html_and_service_results_are_not_reexpanded(self):
        raw = ('<pre lang="youtube"><code>https://example.test/</code></pre>\n'
               '<pre lang="rawhtml"><code>&lt;script&gt;x&lt;/script&gt;</code></pre>\n'
               '<pre lang="rawsvg"><code>&lt;svg/&gt;</code></pre>\n'
               '```rawmermaid\ngraph LR\n```\n'
               '```instagram\nhttps://example.test/\n```\n')
        markdown = '~~~~rawhtml\n' + raw + '~~~~\n\n```youtube\nx\n```\n'
        service_result = ('<pre lang="rawhtml"><code>&lt;b&gt;not expanded&lt;/b&gt;</code></pre>'
                          '<pre lang="rawstl"><code>solid test</code></pre>')
        with patch('custom_embeds.render_service_embed', return_value=service_result) as mock:
            _, page = self.page(markdown)
        self.assertIn(raw, page)
        self.assertIn(service_result, page)
        self.assertNotIn('class="main-code-preview"', page)
        mock.assert_called_once_with('youtube', 'x\n')

    def test_no_custom_language_keeps_original_rendering(self):
        markdown = ('# Hello\n\n```python\nprint("hello")\n```\n\n'
                    '~~~ruby\nputs "hi"\n~~~\n\n```other\n<a>example</a>\n```\n')
        source, _, _, body, embeds = self.render_held(markdown)
        self.assertEqual(embeds, [])
        self.assertEqual(body, render_blog(source, '2025-01-02')[2])

    def test_ordinary_quote_and_large_candidate_scans_finish_promptly(self):
        # A subprocess timeout makes even exponential regressions fail rather
        # than hanging the test suite or CI. The first input was a 9s case.
        program = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from custom_embeds import mark_custom_fences, remove_fence_marks
source = Path(sys.argv[2])
cases = [
    ('> ' * 24 + '普通の本文です\n', None),
    ('> ' * 120000 + '普通の本文です\n', None),
    (' ' * 200000 + '- ' * 30000 + '本文\n', None),
    ('> ' * 50000 + '````rawhtml\n', {0: 0}),
    (('> ' * 32 + '普通の本文です\n') * 8000 +
     '> - 99. ~~~~r&#97;whtml&#32;info\n', {0: 8000}),
]
for markdown, lines in cases:
    source.write_text(markdown, encoding='utf-8')
    marked, marks = mark_custom_fences(source)
    if lines is None:
        assert marks is None and marked == source
    else:
        assert marks.source_lines == lines
        assert remove_fence_marks(marked.read_text(encoding='utf-8'), marks) == markdown
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', program,
             str(Path(custom_embeds.__file__).resolve().parent), str(Path(self.tmp.name) / 'long.md')],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        markdown = '> ' * 24 + '普通の本文です\n'
        source, page = self.page(markdown)
        self.assertIn(render_blog(source, '2025-01-02')[2], page)

    def test_non_fence_heading_marks_do_not_change_ids_or_duplicates(self):
        cases = [
            ('<h1>\n```rawhtml\n</h1>\n\n# rawhtml\n',
             ('rawhtml', 'rawhtml-1')),
            ('# rawhtml\n\n<h1>\n```rawhtml\n</h1>\n\n# rawhtml\n',
             ('rawhtml', 'rawhtml-1', 'rawhtml-2')),
            ('<h2>\n~~~~R&#65;WHTML&#32;example\n</h2>\n\n# rawhtml example\n',
             ('rawhtml-example', 'rawhtml-example-1')),
            ('<h1><img alt="\n```rawhtml\n"></h1>\n\n# rawhtml\n',
             ('rawhtml', 'rawhtml-1')),
            ('<h1>\n```rawmermaid\n</h1>\n\n# rawmermaid\n',
             ('rawmermaid', 'rawmermaid-1')),
            ('<h2>\n~~~R&#65;WSVG&#32;example\n</h2>\n\n# rawsvg example\n',
             ('rawsvg-example', 'rawsvg-example-1')),
        ]
        for markdown, ids in cases:
            with self.subTest(markdown=markdown):
                source, page = self.page(markdown)
                baseline = render_blog(source, '2025-01-02')[2]
                marked, marks = mark_custom_fences(source)
                self.assertIsNotNone(marks)
                marked_body = render_blog(marked, '2025-01-02')[2]
                self.assertIn(marks.prefix, marked_body)  # needed for later extraction
                body, embeds = extract_custom_fences(marked_body, marks)
                self.assertEqual(embeds, [])
                self.assertEqual(body, baseline)
                self.assertIn(baseline, page)  # full build must not reject duplicate IDs
                for ident in ids:
                    self.assertEqual(body.count(f'id="{ident}"'), 1)
                self.assertNotIn(marks.prefix, body)

    def test_non_fence_heading_and_real_rawhtml_keep_separate_marks(self):
        markdown = ('<h1>\n```rawhtml\n</h1>\n\n# rawhtml\n\n'
                    '```rawhtml\n<script>window.example = 1;</script>\n```\n')
        source, page = self.page(markdown)
        original = render_blog(source, '2025-01-02')[2]
        marked, marks = mark_custom_fences(source)
        _, _, body = render_blog(marked, '2025-01-02')
        self.assertEqual(body.count(marks.prefix), 2)
        body, embeds = extract_custom_fences(body, marks)
        self.assertEqual(len(embeds), 1)
        self.assertIn('<h1 id="rawhtml">', body)
        self.assertIn('<h1 id="rawhtml-1">', body)
        self.assertIn(original.split('<pre lang="rawhtml">')[0], page)
        self.assertIn('<script>window.example = 1;</script>\n', page)
        self.assertNotIn('glcustom', page)

    def test_issue_comments_remain_untrusted(self):
        text = ('```rawhtml\n<script id="evil">alert(1)</script>\n```\n\n'
                '```youtube\njavascript:alert(1)\n```\n\n'
                '```rawmermaid\n<script>alert(3)</script>\n```\n\n'
                '```rawstl\n<img src=x onerror=alert(4)>\n```\n\n'
                '```rawsvg\n<svg xmlns="http://www.w3.org/2000/svg">'
                '<script>alert(5)</script></svg>\n```\n\n<script>alert(2)</script>\n')
        with patch('custom_embeds.render_service_embed') as service, \
                patch('custom_embeds.render_code_preview') as preview:
            rendered = render_markdown(text, 'comment-1-')
        service.assert_not_called()
        preview.assert_not_called()
        self.assertIn('&lt;script id="evil"&gt;', rendered)
        self.assertNotIn('<script', rendered)
        self.assertNotIn('<iframe', rendered)
        self.assertIn('<pre lang="youtube">', rendered)
        for kind in ('rawmermaid', 'rawstl', 'rawsvg'):
            self.assertIn(f'<pre lang="{kind}">', rendered)
        self.assertIn('&lt;svg ', rendered)
        self.assertIn('&lt;img src=x onerror=alert(4)&gt;', rendered)
        self.assertNotIn('class="main-code-preview"', rendered)
        self.assertNotIn('data:image/svg+xml', rendered)

    def test_raw_html_survives_comment_sync_and_comments_stay_sanitized(self):
        from sync_comments import sync
        from test_comments import API, REPOSITORY, TOKEN, issue

        template = ('<html><head><title>__ARTICLE_TITLE__</title></head>'
                    '<body><time>__ARTICLE_DATE__</time><main><!-- ARTICLE_CONTENT --></main>'
                    '<div class="main-comments-list"><!-- ISSUE_COMMENTS --></div></body></html>')
        self.write('data/sources/blog/index.html', template)
        raw = ('<script>const marker = "<!-- ISSUE_COMMENTS -->";</script>\n'
               '<!-- ISSUE_COMMENTS -->\n<button id="raw-button">click</button>\n')
        _, original_page = self.page('# Title\n\n```rawhtml\n' + raw + '```\n')
        article = 'data/contents/blog/nest/2025-01-02.html'
        (self.site / 'index.json').write_text(json.dumps({'posts': [{'path': article}]}), encoding='utf-8')
        api = API()
        self.addCleanup(api.close)
        api.issues = [issue(1, article, '```rawhtml\n<script>alert(1)</script>\n```\n\n'
                            '```rawmermaid\n<script>alert(2)</script>\n```\n\n'
                            '```rawstl\n<img src=x onerror=alert(3)>\n```\n\n'
                            '```rawsvg\n<svg><script>alert(4)</script></svg>\n```')]
        sync(self.site, REPOSITORY, TOKEN, api_base=api.base, env={})
        page = (self.site / article).read_text(encoding='utf-8')
        self.assertIn(raw, original_page)
        self.assertIn(raw, page)
        comments = page.split('<div class="main-comments-list">', 1)[1].split('</div></body>', 1)[0]
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', comments)
        self.assertNotIn('<script>', comments)
        self.assertNotIn('class="main-code-preview"', comments)
        self.assertNotIn('data:image/svg+xml', comments)
        self.assertIn('&lt;svg&gt;&lt;script&gt;alert(4)&lt;/script&gt;&lt;/svg&gt;', comments)
        self.assertIn('issue-comment-thread', comments)

    def test_placeholder_collision_fails_closed(self):
        _, _, _, body, embeds = self.render_held('```rawhtml\n<b>x</b>\n```\n')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            insert_custom_fences(body + body, embeds)
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            insert_custom_fences('', embeds)


if __name__ == '__main__':
    unittest.main()
