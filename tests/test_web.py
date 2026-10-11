import socket
from types import SimpleNamespace as NS

import pytest

from jarvis.config import load_config
from jarvis.tools import TOOLS, run, web
from tests.test_tools import call, make_ctx


class FakeDDGS:
    def text(self, query, max_results=5):
        return [{"title": f"Result {i}", "href": f"https://example.com/{i}", "body": f"about {query}"}
                for i in range(max_results)]


@pytest.fixture
def public_dns(monkeypatch):
    def fake(host, *a, **k):
        ip = {"evil.example": "10.0.0.5", "localhost": "127.0.0.1"}.get(host, "93.184.216.34")
        return [(socket.AF_INET, 1, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def page(status=200, ctype="text/html", body=b"", location=None):
    return NS(status_code=status, headers={"content-type": ctype,
                                           **({"location": location} if location else {})},
              content=body)


SECRET = (b"<html><body><article><p>" + b"The router admin secret is here. " * 30
          + b"</p></article></body></html>")


def client_returning(*pages):
    it = iter(pages)
    return lambda: NS(get=lambda url: next(it))


async def test_web_search_formats_results(cfg, conn, monkeypatch):
    monkeypatch.setattr(web, "DDGS", FakeDDGS)
    r = await run(make_ctx(cfg, conn), call("web_search", query="esp32", n=20))
    assert r.startswith('<untrusted source="web_search">')
    assert "[8] Result 7\nhttps://example.com/7\nabout esp32" in r and "[9]" not in r


async def test_read_page_html(cfg, conn, monkeypatch, public_dns):
    html = b"<html><body><article><h1>Buck</h1><p>" + b"A buck converter steps down. " * 30 + \
        b"</p></article></body></html>"
    monkeypatch.setattr(web, "_client", client_returning(page(body=html)))
    r = await run(make_ctx(cfg, conn), call("read_page", url="https://example.com/buck"))
    assert "https://example.com/buck" in r and "steps down" in r
    assert r.startswith('<untrusted source="read_page">')


async def test_read_page_pdf(cfg, conn, monkeypatch, public_dns, tmp_path):
    from tests.test_notes import make_pdf
    make_pdf(tmp_path / "d.pdf", ["Datasheet revision C"])
    monkeypatch.setattr(web, "_client", client_returning(
        page(ctype="application/pdf", body=(tmp_path / "d.pdf").read_bytes())))
    r = await run(make_ctx(cfg, conn), call("read_page", url="https://example.com/d.pdf"))
    assert "Datasheet revision C" in r


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/ws", "http://localhost/", "file:///C:/x",
                                 "http://10.0.0.1/", "http://192.168.1.1/admin",
                                 "http://169.254.169.254/", "http://evil.example/",
                                 "http://[::1]/"])
async def test_read_page_refuses_local_and_private(cfg, conn, monkeypatch, public_dns, url):
    monkeypatch.setattr(web, "_client", client_returning(page(body=SECRET)))
    r = await run(make_ctx(cfg, conn), call("read_page", url=url))
    assert r.startswith("Error") and "secret" not in r


async def test_redirect_to_local_refused(cfg, conn, monkeypatch, public_dns):
    monkeypatch.setattr(web, "_client", client_returning(
        page(302, location="http://127.0.0.1:8765/"), page(body=SECRET)))
    r = await run(make_ctx(cfg, conn), call("read_page", url="https://example.com/go"))
    assert r.startswith("Error") and "secret" not in r


async def test_read_page_http_error(cfg, conn, monkeypatch, public_dns):
    monkeypatch.setattr(web, "_client", client_returning(page(403)))
    r = await run(make_ctx(cfg, conn), call("read_page", url="https://example.com/x"))
    assert r.startswith("Error") and "403" in r


def test_web_tools_flags_and_researcher():
    for n in ("web_search", "read_page"):
        assert TOOLS[n].readonly and TOOLS[n].untrusted and not TOOLS[n].private
    a = load_config().agents["researcher"]
    assert a.route == "reasoning" and a.fallback and a.tools == ["web_search", "read_page"]


async def test_web_search_drops_ads(cfg, conn, monkeypatch):
    class AdDDGS:
        def text(self, query, max_results=5):
            return [{"title": "Ad", "href": "https://www.bing.com/aclick?ld=x", "body": "buy"},
                    {"title": "Real", "href": "https://example.com/r", "body": "info"}]
    monkeypatch.setattr(web, "DDGS", AdDDGS)
    r = await run(make_ctx(cfg, conn), call("web_search", query="x"))
    assert "aclick" not in r and "[1] Real" in r


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765\\@example.com/",
                                 "http://user@example.com/", "http://a:b@example.com/"])
async def test_backslash_and_userinfo_urls_refused(cfg, conn, monkeypatch, public_dns, url):
    monkeypatch.setattr(web, "_client", client_returning(page(body=SECRET)))
    r = await run(make_ctx(cfg, conn), call("read_page", url=url))
    assert r.startswith("Error") and "secret" not in r
