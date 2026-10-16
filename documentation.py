"""Public documentation from an explicit catalogue of repository Markdown files.

No submitted path is opened. Raw HTML is disabled; relative links are resolved
against the catalogue, and headings receive stable, Unicode-friendly anchors.
"""
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path
import math
import re
import textwrap
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import Blueprint, abort, render_template, request, send_file, url_for
from markdown_it import MarkdownIt
from markupsafe import Markup

ROOT = Path(__file__).resolve().parent
blueprint = Blueprint("documentation", __name__, url_prefix="/docs")


@dataclass(frozen=True)
class Document:
    slug: str
    title: str
    description: str
    group: str
    filename: str
    icon: str

    @property
    def path(self):
        return ROOT / self.filename


DOCUMENTS = (
    Document("campaign-guide", "Кампания, боссы и билды", "PvE, ветки развития карт, сундуки за OIL и сбор до полного склада.", "Игроку", "docs/CAMPAIGN.md", "♛"),
    Document("islands-guide", "Остров, карты и PvP", "Автоматическая база, трубы, сундуки, советники и бои двух игроков онлайн.", "Игроку", "docs/ISLANDS.md", "⚔"),
    Document("economy-guide", "Рынок и экономика", "Торговля за OIL, резерв товара, комиссии, источники ресурсов и ограничения инфляции.", "Игроку", "docs/ECONOMY.md", "⇄"),
    Document("farm-guide", "Как играть: база", "Первые действия, выбор улучшений, производство и рейтинги.", "Игроку", "docs/FARM_GUIDE.md", "⚑"),
    Document("player-guide", "Кошелёк и OIL", "Майнинг, переводы, подписи, комиссии и оборудование кошелька.", "Игроку", "docs/USER_GUIDE.md", "01"),
    Document("faq", "Вопросы и ответы", "Почему перевод ждёт, куда делись монеты и что даёт улучшение.", "Игроку", "docs/FAQ.md", "?"),
    Document("features", "Возможности игры", "Что уже работает и где проходят границы учебной модели.", "Игроку", "docs/FEATURES.md", "◈"),
    Document("blockchain", "Блокчейн по шагам", "От ключей и подписи до mempool, PoW, проверки истории и подтверждения игровых оплат.", "Устройство проекта", "docs/BLOCKCHAIN.md", "⛓"),
    Document("architecture", "Архитектура приложения", "Модули, база данных, игровой слой и границы доверия.", "Устройство проекта", "docs/ARCHITECTURE.md", "⌘"),
    Document("deployment", "Запуск и сервер", "Полный README: Docker, настройки, VPS, HTTPS и резервные копии.", "Разработчику и администратору", "README.md", "↗"),
    Document("api", "HTTP API", "Маршруты, форматы ответов, формы и коды ошибок.", "Разработчику и администратору", "docs/API.md", "⇄"),
    Document("contents", "Карта документации", "Исходный указатель всех руководств проекта.", "О проекте", "docs/README_DOCS.md", "≡"),
)
BY_SLUG = {doc.slug: doc for doc in DOCUMENTS}
BY_PATH = {doc.path.resolve(): doc for doc in DOCUMENTS}


def groups():
    return [{"title": group, "documents": [doc for doc in DOCUMENTS if doc.group == group]}
            for group in dict.fromkeys(doc.group for doc in DOCUMENTS)]


def heading_slug(text):
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"\s+", "-", text) or "section"


def plain_inline(token):
    return "".join(child.content for child in token.children or []
                   if child.type in {"text", "code_inline", "image"}) or token.content


def rewrite_url(value, source):
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return value
    path = (source.parent / unquote(parts.path)).resolve()
    if path in BY_PATH:
        target = url_for("documentation.page", slug=BY_PATH[path].slug)
    else:
        # Do not make arbitrary repository files publicly accessible.
        return "#"
    return urlunsplit(("", "", target, parts.query, parts.fragment))


def render_diagram(source, identifier):
    """Render the simple labelled DAG used in the architecture, without a CDN.

    Unsupported Mermaid syntax stays available as an ordinary code fence.
    Labels are escaped, IDs are restricted, and no SVG supplied by users runs.
    """
    lines = [line.strip() for line in source.strip().splitlines() if line.strip()]
    if not lines or lines[0] not in {"flowchart LR", "flowchart TD", "flowchart TB"}:
        return None
    nodes, edges = {}, []
    node = r"([A-Za-z][A-Za-z0-9_]*)(?:\[([^\]\n]+)\])?"
    for line in lines[1:]:
        match = re.fullmatch(node + r"\s*-->\s*" + node, line)
        if not match:
            return None
        left, left_label, right, right_label = match.groups()
        nodes[left] = left_label or nodes.get(left, left)
        nodes[right] = right_label or nodes.get(right, right)
        edges.append((left, right))
    if not nodes or len(nodes) > 24:
        return None
    remaining, rank = set(nodes), {}
    while remaining:
        ready = [node for node in nodes if node in remaining
                 and all(parent in rank for parent, child in edges if child == node)]
        if not ready:
            return None
        for node in ready:
            rank[node] = max([rank[parent] + 1 for parent, child in edges if child == node] or [0])
            remaining.remove(node)
    levels = [[node for node in nodes if rank[node] == level] for level in range(max(rank.values()) + 1)]
    horizontal = lines[0] == "flowchart LR"
    width = max(560, len(levels) * 270 + 12) if horizontal else max(560, max(map(len, levels)) * 270)
    height = max(map(len, levels)) * 106 + 12 if horizontal else len(levels) * 106 + 12
    positions = {node: (row * 270 + 136, (index + 0.5) * height / len(level) - 34)
                 if horizontal else ((index + 0.5) * width / len(level), row * 106 + 12)
                 for row, level in enumerate(levels) for index, node in enumerate(level)}
    svg = [f'<svg class="docs-diagram" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{identifier}-title">',
           f'<title id="{identifier}-title">Схема взаимодействия компонентов OilCoin</title>',
           f'<defs><marker id="{identifier}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10Z" fill="#64d9c8"/></marker></defs>']
    for left, right in edges:
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        if horizontal:
            x1 += 124; x2 -= 127; y1 += 34; y2 += 34
            mid = (x1 + x2) / 2
            curve = f'M{x1} {y1} C{mid} {y1} {mid} {y2} {x2} {y2}'
        else:
            y1 += 68
            mid = (y1 + y2) / 2
            curve = f'M{x1} {y1} C{x1} {mid} {x2} {mid} {x2} {y2 - 3}'
        svg.append(f'<path d="{curve}" fill="none" stroke="#64d9c8" stroke-width="1.5" marker-end="url(#{identifier}-arrow)"/>')
    for node, label in nodes.items():
        x, y = positions[node]
        svg.append(f'<rect x="{x-124}" y="{y}" width="248" height="68" rx="12" fill="#162232" stroke="#3b536a"/>')
        wrapped = textwrap.wrap(label, width=28)
        for index, line in enumerate(wrapped):
            baseline = y + 37 - (len(wrapped) - 1) * 9 + index * 18
            svg.append(f'<text x="{x}" y="{baseline}" text-anchor="middle" fill="#f1f4f8" font-size="13" font-family="sans-serif">{escape(line)}</text>')
    svg.append('</svg>')
    return '<figure class="docs-diagram-wrap"><div class="docs-diagram-scroll" tabindex="0" aria-label="Схема компонентов; на узком экране прокручивается по горизонтали">' + ''.join(svg) + '</div><figcaption>Путь запроса и связь компонентов<span class="docs-diagram-hint">Прокрутите схему вбок, чтобы увидеть все ветви →</span></figcaption></figure>'


@lru_cache(maxsize=32)
def _read_source(path, modified_ns, size):
    return Path(path).read_text(encoding="utf-8")


def document_source(doc):
    stat = doc.path.stat()
    return _read_source(str(doc.path), stat.st_mtime_ns, stat.st_size)


def render_document(doc, source=None):
    source = document_source(doc) if source is None else source
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(source)
    headings, sections, used = [], [], set()
    title, current = doc.title, None
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            label = plain_inline(inline)
            slug = heading_slug(label)
            anchor, count = slug, 0
            while anchor in used:
                count += 1
                anchor = f"{slug}-{count}"
            used.add(anchor)
            token.attrSet("id", anchor)
            if token.tag == "h1" and index == 0:
                title = label
            elif token.tag in {"h2", "h3"}:
                headings.append({"title": label, "id": anchor, "level": int(token.tag[1])})
            current = {"heading": label, "anchor": anchor, "text": ""}
            sections.append(current)
        elif token.type in {"inline", "fence", "code_block"}:
            if current is None:
                current = {"heading": doc.title, "anchor": "", "text": ""}
                sections.append(current)
            current["text"] += " " + (plain_inline(token) if token.type == "inline" else token.content)
        for child in token.children or []:
            if child.type == "link_open":
                child.attrSet("href", rewrite_url(child.attrGet("href") or "", doc.path))
                child.attrSet("rel", "noopener noreferrer")
            elif child.type == "image":
                child.attrSet("src", rewrite_url(child.attrGet("src") or "", doc.path))
                child.attrSet("loading", "lazy")
    # The first H1 is presented in the article header; preserve its anchor there.
    title_anchor = None
    if tokens and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
        title_anchor = tokens[0].attrGet("id")
        tokens = tokens[3:]
    default_fence = parser.renderer.rules["fence"]
    def fence(items, index, options, env):
        token = items[index]
        if token.info.strip() == "mermaid":
            diagram = render_diagram(token.content, f"diagram-{index}")
            if diagram:
                return diagram + '<details class="docs-source-diagram"><summary>Исходная схема Mermaid</summary>' + default_fence(items, index, options, env) + '</details>'
        return default_fence(items, index, options, env)
    parser.renderer.rules["fence"] = fence
    return {"title": title, "title_anchor": title_anchor, "html": Markup(parser.renderer.render(tokens, parser.options, {})),
            "toc": headings, "sections": sections, "minutes": max(1, math.ceil(len(source.split()) / 180))}


def search_documents(query):
    normalize = lambda value: value.casefold().replace("ё", "е")
    terms = normalize(query).split()
    if not terms:
        return []
    results = []
    for doc in DOCUMENTS:
        rendered = render_document(doc)
        for section in rendered["sections"]:
            content = " ".join(section["text"].split())
            haystack = normalize(doc.title + " " + section["heading"] + " " + content)
            if all(term in haystack for term in terms):
                position = normalize(content).find(terms[0])
                start = max(0, position - 65)
                excerpt = ("…" if start else "") + content[start:start + 220]
                if start + 220 < len(content):
                    excerpt += "…"
                results.append({"document": doc, "heading": section["heading"], "anchor": section["anchor"], "excerpt": excerpt,
                                "score": sum(term in normalize(section["heading"]) for term in terms)})
    return sorted(results, key=lambda result: -result["score"])


@blueprint.get("")
@blueprint.get("/")
def index():
    query = request.args.get("q", "").strip()
    if len(query) > 120:
        abort(400)
    results = search_documents(query) if query else []
    return render_template("docs/index.html", groups=groups(), documents=DOCUMENTS, query=query, results=results)


@blueprint.get("/<slug>")
def page(slug):
    doc = BY_SLUG.get(slug)
    if doc is None:
        abort(404)
    position = DOCUMENTS.index(doc)
    return render_template("docs/article.html", document=doc, groups=groups(), article=render_document(doc),
                           previous=DOCUMENTS[position - 1] if position else None,
                           next=DOCUMENTS[position + 1] if position + 1 < len(DOCUMENTS) else None)


@blueprint.get("/<slug>/source")
def source(slug):
    doc = BY_SLUG.get(slug)
    if doc is None:
        abort(404)
    return send_file(doc.path, mimetype="text/markdown; charset=utf-8", as_attachment=True, download_name=doc.path.name)

