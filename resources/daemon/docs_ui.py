import html as _html
import os
import re as _re

import mistune

from resources.daemon.constants import SCRIPT_DIR

DOCS_DIR = os.path.join(SCRIPT_DIR, "docs")
DOCS_TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "resources", "docs.html")
DASHBOARD_HTML_PATH = os.path.join(SCRIPT_DIR, "resources", "dashboard.html")
ADMIN_HTML_PATH = os.path.join(SCRIPT_DIR, "resources", "admin.html")

# Ordered list of doc pages: (slug, title). The slug maps to docs/<slug>.md.
# "index" maps to docs/README.md.
DOC_PAGES = [
    ("index", "Overview"),
    ("getting-started", "Getting Started"),
    ("configuration", "Configuration"),
    ("daemon", "Daemon Mode"),
    ("integrations", "Integrations"),
    ("hardware-acceleration", "Hardware Acceleration"),
    ("deployment", "Deployment"),
    ("troubleshooting", "Troubleshooting"),
    ("migration", "Migration Guide"),
]

_TAG_STRIP_RE = _re.compile(r"<[^>]+>")
_SLUG_STRIP_RE = _re.compile(r"[^\w-]")
_HEADING_SIZES = {1: "text-3xl", 2: "text-2xl", 3: "text-xl", 4: "text-lg", 5: "text-base", 6: "text-sm"}


class _DocsRenderer(mistune.HTMLRenderer):
    def heading(self, text, level, **attrs):
        plain = _TAG_STRIP_RE.sub("", text)
        slug = _SLUG_STRIP_RE.sub("", plain.lower().replace(" ", "-"))
        margin = "mt-10" if level <= 2 else "mt-6"
        size = _HEADING_SIZES.get(level, "text-base")
        return f'<h{level} id="{slug}" class="{size} {margin} font-bold text-white mb-3">{text}</h{level}>\n'

    def block_code(self, code, **attrs):
        lang = (attrs.get("info") or "").strip().split()[0] if attrs.get("info") else ""
        if lang == "mermaid":
            return f'<div class="mermaid my-4">{code}</div>\n'
        return f'<pre class="bg-gray-800 rounded-lg p-4 overflow-x-auto my-4 border border-gray-700"><code class="text-sm text-green-300">{_html.escape(code)}</code></pre>\n'

    def codespan(self, code):
        return f'<code class="bg-gray-800 text-blue-300 px-1.5 py-0.5 rounded text-xs">{_html.escape(code)}</code>'

    def link(self, text, url, title=None):
        return f'<a href="{url}" class="text-blue-400 hover:underline">{text}</a>'

    def strong(self, text):
        return f'<strong class="text-white">{text}</strong>'

    def paragraph(self, text):
        return f'<p class="text-gray-300 my-2 leading-relaxed">{text}</p>\n'

    def thematic_break(self):
        return '<hr class="border-gray-700 my-8">\n'

    def list(self, body, ordered, **attrs):
        tag = "ol" if ordered else "ul"
        cls = "list-decimal" if ordered else "list-disc"
        return f'<{tag} class="{cls} list-inside space-y-1 my-3 text-gray-300">{body}</{tag}>\n'

    def list_item(self, text, **attrs):
        return f"<li>{text.strip()}</li>\n"

    def table(self, text):
        return f'<div class="overflow-x-auto my-4"><table class="w-full text-sm">{text}</table></div>\n'

    def table_head(self, text):
        return f'<thead><tr class="border-b border-gray-700">{text}</tr></thead>\n'

    def table_body(self, text):
        return f'<tbody class="divide-y divide-gray-700/50">{text}</tbody>\n'

    def table_row(self, text):
        return f'<tr class="hover:bg-gray-800/50">{text}</tr>\n'

    def table_cell(self, text, align=None, head=False):
        if head:
            return f'<th class="text-left py-2 px-3 text-gray-400">{text}</th>\n'
        return f'<td class="py-2 px-3 text-gray-300">{text}</td>\n'


_md = mistune.create_markdown(renderer=_DocsRenderer(escape=True), plugins=["table"])


def _inline(text):
    """Render inline Markdown to HTML (no block wrapper). Preserved for backward compatibility."""
    rendered = _md(text).strip()
    if rendered.startswith("<p") and rendered.endswith("</p>"):
        rendered = rendered[rendered.index(">") + 1 : -4]
    return rendered


def _render_markdown_to_html(md_text):
    return _md(md_text)


def _load_dashboard_html():
    with open(DASHBOARD_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_admin_html():
    with open(ADMIN_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_docs_template(active_slug="index"):
    with open(DOCS_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    nav_items = []
    for slug, title in DOC_PAGES:
        href = "/docs" if slug == "index" else "/docs/" + slug
        active = ' class="bg-gray-700 text-white"' if slug == active_slug else ' class="text-gray-300 hover:text-white"'
        nav_items.append('<a href="%s"%s>%s</a>' % (href, active, title))
    return template.replace("%NAV%", "\n".join(nav_items))
