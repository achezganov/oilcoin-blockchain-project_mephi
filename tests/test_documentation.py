from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from conftest import post, register
from database import connect
from documentation import BY_SLUG, DOCUMENTS, ROOT, render_document, render_diagram


class PageLinks(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.references = set(), []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            assert attrs["id"] not in self.ids, attrs["id"]
            self.ids.add(attrs["id"])
        if tag in {"a", "img", "script", "link"}:
            self.references.append(attrs.get("href") or attrs.get("src") or "")


def test_all_documentation_is_published_and_links_resolve(client):
    expected = {ROOT / "README.md", *ROOT.glob("docs/*.md")}
    assert {doc.path for doc in DOCUMENTS} == expected
    assert client.get("/docs/teacher-guide").status_code == 404
    assert client.get("/docs/teacher-guide/source").status_code == 404
    for path in ('/docs/development', '/docs/review', '/docs/changelog',
                 '/to_me/.env', '/static/art/prompts.json', '/static/art/bosses-prompts.json'):
        assert client.get(path).status_code == 404, path
    pages = {}
    for doc in DOCUMENTS:
        path = f"/docs/{doc.slug}"
        response = client.get(path)
        assert response.status_code == 200, path
        pages[path] = PageLinks(response.get_data(as_text=True))
        download = client.get(path + "/source")
        assert download.status_code == 200
        assert download.data == doc.path.read_bytes()
        assert "attachment" in download.headers["Content-Disposition"]
    pages["/docs/"] = PageLinks(client.get("/docs/").get_data(as_text=True))
    for path, parsed in pages.items():
        for reference in parsed.references:
            assert reference != "#", (path, "Unresolved local link")
            target = urlsplit(urljoin(path, reference))
            if target.scheme or target.netloc:
                continue
            assert client.get(target.path).status_code in {200, 302}, (path, reference)
            if target.path in pages and target.fragment:
                assert unquote(target.fragment) in pages[target.path].ids, (path, reference)


def test_search_returns_section_links_and_handles_untrusted_queries(client):
    response = client.get("/docs", query_string={"q": "комиссия"})
    assert response.status_code == 200
    parsed = PageLinks(response.get_data(as_text=True))
    assert any("/docs/player-guide#" in reference for reference in parsed.references)
    response = client.get("/docs/", query_string={"q": "ПЕРЕВОД ВСЕ ЕЩЕ"})
    assert "Почему перевод всё ещё в очереди" in response.get_data(as_text=True)
    response = client.get("/docs", query_string={"q": '<script>alert("x")</script>'})
    assert '<script>alert("x")</script>' not in response.get_data(as_text=True)
    assert "Пока не нашли такой ответ" in response.get_data(as_text=True)
    assert client.get("/docs", query_string={"q": "я" * 121}).status_code == 400


def test_markdown_does_not_execute_html_or_javascript_links(application):
    source = '# Title\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n## Повтор\n\n## Повтор\n'
    with application.test_request_context():
        rendered = render_document(BY_SLUG["faq"], source)
    assert "<script>" not in rendered["html"]
    assert "&lt;script&gt;" in rendered["html"]
    assert 'href="javascript:' not in rendered["html"]
    assert [heading["id"] for heading in rendered["toc"]] == ["повтор", "повтор-1"]


def test_heading_suffixes_do_not_collide_with_literal_headings(application):
    with application.test_request_context():
        rendered = render_document(BY_SLUG['faq'], '# Title\n\n## Повтор\n\n## Повтор-1\n\n## Повтор\n')
    assert [heading['id'] for heading in rendered['toc']] == ['повтор', 'повтор-1', 'повтор-2']
    PageLinks(str(rendered['html']))


def test_diagram_obeys_horizontal_and_vertical_directions():
    import re
    def positions(direction):
        diagram = render_diagram(direction+'\nA[First] --> B[Second]', 'test')
        return [tuple(map(float, position)) for position in re.findall(r'<rect x="([\d.]+)" y="([\d.]+)"', diagram)]
    left, right = positions('flowchart LR')
    assert right[0] > left[0] and right[1] == left[1]
    top, bottom = positions('flowchart TD')
    assert bottom[1] > top[1] and bottom[0] == top[0]


def test_documentation_cannot_read_arbitrary_files(client):
    for path in ("/docs/.env/source", "/docs/requirements.txt", "/docs/assets/.env",
                 "/docs/assets/../.env", "/docs/assets/%2e%2e%2f.env", "/docs/unknown"):
        assert client.get(path).status_code == 404, path
    assert client.post("/docs/player-guide").status_code == 405


def test_help_stays_readable_when_running_ledger_is_damaged(client, application):
    with connect(application.config["DATABASE_PATH"]) as conn:
        conn.execute("UPDATE blocks SET hash=? WHERE height=0", ("f" * 64,))
    assert client.get("/health").status_code == 503
    assert client.get("/docs/faq").status_code == 200
    assert client.get("/docs", query_string={"q": "базы"}).status_code == 200


def test_architecture_diagram_is_local_and_accessible(client):
    page = client.get("/docs/architecture").get_data(as_text=True)
    assert '<svg class="docs-diagram"' in page
    assert 'role="img" aria-labelledby=' in page
    assert "Схема взаимодействия компонентов OilCoin" in page
    assert "Исходная схема Mermaid" in page
    parsed = PageLinks(page)
    assert not any("cdn" in reference for reference in parsed.references)


def test_first_ten_minutes_matches_player_guide(client):
    address = register(client)
    post(client, "/mine/quick")
    assert client.get("/api/game").json["xp"] == 75
    post(client, "/game/upgrade")
    assert client.get("/api/game").json["rig_level"] == 0
    assert client.get(f"/api/balance/{address}").json["available_balance"] == 40
    post(client, "/mine/quick")
    progress = client.get("/api/game").json
    assert (progress["xp"], progress["level"], progress["rig_level"]) == (275, 3, 1)
    assert all(mission["completed"] for mission in progress["missions"])
    assert client.get(f"/api/balance/{address}").json["confirmed_balance"] == 90
    post(client, "/mine/quick")
    assert client.get("/api/game").json["xp"] == 305
