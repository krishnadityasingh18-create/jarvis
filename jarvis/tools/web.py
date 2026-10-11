"""Web search and page reading. Everything returned is untrusted outside text."""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from ddgs import DDGS

from jarvis.tools import tool

PAGE_CHARS = 8000
MAX_BODY = 20 * 2**20


def _client():
    import primp
    # Chrome impersonation: plain HTTP clients get 403 from Wikipedia and others here.
    return primp.Client(impersonate="chrome", follow_redirects=False, timeout=20)


def _safe_url(url: str) -> str:
    """Only public http(s) addresses: never this laptop or the local network."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http and https web addresses")
    # browsers (and primp) treat \ as /, so "http://127.0.0.1\@example.com" really goes to
    # 127.0.0.1 while urlparse sees example.com; user@host is never needed either
    if "\\" in url or u.username or u.password:
        raise ValueError("that web address has a disguised host")
    try:
        ips = [ipaddress.ip_address(u.hostname)]   # a literal address needs no DNS
    except ValueError:
        try:
            ips = [ipaddress.ip_address(i[4][0].split("%")[0])
                   for i in socket.getaddrinfo(u.hostname, None)]
        except socket.gaierror:
            raise ValueError(f"{u.hostname} doesn't resolve") from None
    for ip in ips:
        if not ip.is_global:
            raise ValueError("I don't open local or private network addresses")
    # ponytail: the fetch resolves again (DNS rebinding window); pin the IP if that matters
    return url


def _fetch(url: str):
    c = _client()
    for _ in range(5):  # follow redirects ourselves so each hop is checked
        r = c.get(_safe_url(url))
        loc = r.headers.get("location")
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            url = urljoin(url, loc)
            continue
        return r, url
    raise ValueError("too many redirects")


@tool("Search the web. Returns numbered results with title, URL and snippet.", readonly=True,
      untrusted=True, exports=True, query="string: what to search for", n="?integer: results, at most 8")
def web_search(ctx, query, n=5):
    n = max(1, min(int(n), 8))
    results = [r for r in DDGS().text(query, max_results=n + 2)
               if not any(ad in r["href"] for ad in ("bing.com/aclick", "duckduckgo.com/y.js"))][:n]
    if not results:
        return f"No web results for '{query}'.", None
    return "\n\n".join(f"[{i}] {r['title']}\n{r['href']}\n{r['body']}"
                       for i, r in enumerate(results, 1)), None


@tool("Read a web page or online PDF and return its main text.", readonly=True, untrusted=True,
      exports=True,
      max_chars=PAGE_CHARS + 600, url="string: the page's full http(s) address")
def read_page(ctx, url):
    r, final = _fetch(url)
    if r.status_code >= 400:
        raise ValueError(f"the site answered {r.status_code}")
    body, ctype = r.content, r.headers.get("content-type", "").lower()
    if len(body) > MAX_BODY:
        raise ValueError("that page is too large")
    if "pdf" in ctype or final.lower().endswith(".pdf"):
        import pymupdf
        with pymupdf.open(stream=body, filetype="pdf") as d:
            text = "\n".join(p.get_text() for p in d)
    elif "html" in ctype or "xml" in ctype:
        import trafilatura
        text = trafilatura.extract(body.decode("utf-8", errors="replace"), url=final) or ""
    elif ctype.startswith("text/"):
        text = body.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"that isn't a web page or PDF ({ctype or 'unknown type'})")
    if not text.strip():
        raise ValueError("no readable text on that page")
    more = "\n(truncated)" if len(text) > PAGE_CHARS else ""
    return f"{final}\n{text[:PAGE_CHARS]}{more}", None
