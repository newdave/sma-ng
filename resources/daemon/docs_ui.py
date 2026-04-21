import os
import re as _re

from resources.daemon.constants import SCRIPT_DIR

# Pre-compiled inline Markdown patterns (reused for every line of every page).
_BOLD_RE = _re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = _re.compile(r"\*(.+?)\*")
_CODE_RE = _re.compile(r"`([^`]+)`")
_LINK_RE = _re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Pre-compiled block-level Markdown patterns for the line renderer.
_HEADING_RE = _re.compile(r"^(#{1,6})\s+(.*)")
_HR_RE = _re.compile(r"^-{3,}$")
_UL_RE = _re.compile(r"^(\s*)[-*]\s+(.*)")
_OL_RE = _re.compile(r"^(\s*)\d+\.\s+(.*)")
_LIST_CONT_RE = _re.compile(r"^(\s*[-*]\s|^\s*\d+\.\s)")
_TABLE_SEP_RE = _re.compile(r"^[-:]+$")
_SLUG_STRIP_RE = _re.compile(r"[^\w-]")

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
]


def _inline(text):
    """Process inline Markdown formatting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _BOLD_RE.sub(r'<strong class="text-white">\1</strong>', text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _CODE_RE.sub(r'<code class="bg-gray-800 text-blue-300 px-1.5 py-0.5 rounded text-xs">\1</code>', text)
    text = _LINK_RE.sub(r'<a href="\2" class="text-blue-400 hover:underline">\1</a>', text)
    return text


def _render_markdown_to_html(md_text):
    """Minimal Markdown to HTML renderer for documentation display."""
    lines = md_text.split("\n")
    html_parts = []
    in_code = False
    in_table = False
    in_list = False
    list_type = None

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</code></pre>")
                in_code = False
            else:
                html_parts.append('<pre class="bg-gray-800 rounded-lg p-4 overflow-x-auto my-4 border border-gray-700"><code class="text-sm text-green-300">')
                in_code = True
            continue
        if in_code:
            html_parts.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        if in_table and not line.strip().startswith("|"):
            html_parts.append("</tbody></table></div>")
            in_table = False

        if in_list and line.strip() and not _LIST_CONT_RE.match(line):
            html_parts.append("</%s>" % list_type)
            in_list = False

        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</%s>" % list_type)
                in_list = False
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = _inline(heading_match.group(2))
            slug = _SLUG_STRIP_RE.sub("", heading_match.group(2).lower().replace(" ", "-"))
            sizes = {1: "text-3xl", 2: "text-2xl", 3: "text-xl", 4: "text-lg", 5: "text-base", 6: "text-sm"}
            margin_top = "mt-10" if level <= 2 else "mt-6"
            html_parts.append('<h%d id="%s" class="%s %s font-bold text-white mb-3">%s</h%d>' % (level, slug, sizes.get(level, "text-base"), margin_top, text, level))
            continue

        if _HR_RE.match(stripped):
            html_parts.append('<hr class="border-gray-700 my-8">')
            continue

        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
            if all(_TABLE_SEP_RE.match(cell) for cell in cells):
                continue
            if not in_table:
                in_table = True
                html_parts.append('<div class="overflow-x-auto my-4"><table class="w-full text-sm"><thead><tr class="border-b border-gray-700">')
                for cell in cells:
                    html_parts.append('<th class="text-left py-2 px-3 text-gray-400">%s</th>' % _inline(cell))
                html_parts.append('</tr></thead><tbody class="divide-y divide-gray-700/50">')
            else:
                html_parts.append('<tr class="hover:bg-gray-800/50">')
                for cell in cells:
                    html_parts.append('<td class="py-2 px-3 text-gray-300">%s</td>' % _inline(cell))
                html_parts.append("</tr>")
            continue

        unordered_match = _UL_RE.match(line)
        if unordered_match:
            if not in_list:
                in_list = True
                list_type = "ul"
                html_parts.append('<ul class="list-disc list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(unordered_match.group(2)))
            continue

        ordered_match = _OL_RE.match(line)
        if ordered_match:
            if not in_list:
                in_list = True
                list_type = "ol"
                html_parts.append('<ol class="list-decimal list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(ordered_match.group(2)))
            continue

        html_parts.append('<p class="text-gray-300 my-2 leading-relaxed">%s</p>' % _inline(stripped))

    if in_code:
        html_parts.append("</code></pre>")
    if in_table:
        html_parts.append("</tbody></table></div>")
    if in_list:
        html_parts.append("</%s>" % list_type)

    return "\n".join(html_parts)


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
