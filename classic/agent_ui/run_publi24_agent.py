#!/usr/bin/env python3
import json
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

STATE_PATH = Path(__file__).with_name("state.json")
QUERY = os.environ.get("AGENT_QUERY", "Samsung Galaxy S25 Plus 2026")
OLX_SEARCH_URL = "https://www.olx.ro/q/samsung+s25+plus/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "status": "idle",
        "logs": [],
        "results": [],
        "stats": {},
        "started_at": None,
        "finished_at": None,
    }


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def log(state, msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    state["logs"].append(f"[{ts}] {msg}")
    state["logs"] = state["logs"][-300:]
    save_state(state)


def fetch(url):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_price_ron(text):
    # Match values like 2.999 lei / 2999 lei / 2 999 RON
    m = re.search(r"(\d{1,2}(?:[\s\.]\d{3})+|\d{3,5})\s*(?:lei|RON)", text, flags=re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1)
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    value = int(digits)
    if value < 500 or value > 10000:
        return None
    return value


def ddg_html_search(query):
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    html = fetch(url)

    results = []
    # Basic parser for DDG html page
    for block in re.findall(r'<div class="result__body".*?</div>\s*</div>', html, flags=re.DOTALL):
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, flags=re.DOTALL)
        link_m = re.search(r'class="result__a" href="([^"]+)"', block)
        snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', block, flags=re.DOTALL)

        title = re.sub(r"<.*?>", "", title_m.group(1)).strip() if title_m else ""
        link = link_m.group(1).strip() if link_m else ""
        snippet_raw = ""
        if snippet_m:
            snippet_raw = snippet_m.group(1) or snippet_m.group(2) or ""
        snippet = re.sub(r"<.*?>", "", snippet_raw).strip()

        if "publi24.ro" not in link:
            continue

        price = parse_price_ron(snippet)
        results.append({"title": title, "link": link, "snippet": snippet, "price": price})

    return results


def olx_search():
    try:
        html = fetch(OLX_SEARCH_URL)
        results = []

        # Match OLX listing links
        for m in re.finditer(r'<a[^>]*/ad/([^/]+)/"[^>]*>(.*?)</a>', html, flags=re.DOTALL | re.IGNORECASE):
            listing_id = m.group(1)
            title = re.sub(r"<.*?>", "", m.group(2)).strip()
            if not title:
                continue
            if "s25" not in title.lower() and "samsung" not in title.lower():
                continue
            
            link = f"https://www.olx.ro/ad/{listing_id}/"
            results.append({"title": title, "link": link, "snippet": "", "price": None})

        # De-duplicate
        seen = set()
        dedup = []
        for r in results:
            key = (r["title"], r["link"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(r)

        return dedup[:50]
    except Exception as e:
        return []


def enrich_with_page_price(items, state):
    out = []
    for idx, item in enumerate(items[:20], start=1):
        link = item["link"]
        title = item["title"]
        price = item.get("price")
        log(state, f"Open listing {idx}/{min(len(items), 20)}: {title[:70]}")
        if price is None:
            try:
                page = fetch(link)
                # Try multiple common price patterns
                for pat in [
                    r'"price"\s*:\s*"?(\d{3,5})"?',
                    r"(\d{1,2}(?:[\s\.]\d{3})+|\d{3,5})\s*(?:lei|RON)",
                ]:
                    m = re.search(pat, page, flags=re.IGNORECASE)
                    if m:
                        price = parse_price_ron(m.group(0))
                        if price:
                            break
            except Exception as e:
                log(state, f"Listing fetch failed: {e}")

        out.append({"title": title, "link": link, "price": price})
    return out


def compute_stats(values):
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
    }


def run_once(state):
    log(state, "Search started on DDG HTML endpoint")
    items = ddg_html_search(QUERY)
    log(state, f"Found {len(items)} candidates from DDG")

    if not items:
        log(state, "DDG returned no results, trying OLX search")
        items = olx_search()
        log(state, f"Found {len(items)} candidates from OLX")

    if not items:
        state["results"] = []
        state["stats"] = {"count": 0}
        save_state(state)
        return

    enriched = enrich_with_page_price(items, state)

    # Keep only relevant listings mentioning S25 Plus
    filtered = []
    for x in enriched:
        t = (x.get("title") or "").lower()
        if "s25" in t and "plus" in t:
            filtered.append(x)

    if not filtered:
        filtered = enriched

    prices = [x["price"] for x in filtered if isinstance(x.get("price"), int)]
    stats = compute_stats(prices)

    state["results"] = filtered
    state["stats"] = stats
    save_state(state)


def main():
    state = load_state()
    state["status"] = "running"
    state["started_at"] = datetime.utcnow().isoformat() + "Z"
    state["finished_at"] = None
    state["results"] = []
    state["stats"] = {}
    state["logs"] = []
    save_state(state)
    log(state, f"Query: {QUERY}")

    try:
        for i in range(1, 4):
            log(state, f"Agent iteration {i}/3")
            run_once(state)
            if state.get("stats", {}).get("count", 0) > 0:
                log(state, "Sufficient results found, stopping loop")
                break
            log(state, "No priced results yet, retrying in 5s")
            time.sleep(5)

        state["status"] = "done"
        state["finished_at"] = datetime.utcnow().isoformat() + "Z"
        save_state(state)
    except Exception as e:
        log(state, f"Agent error: {e}")
        state["status"] = "error"
        state["finished_at"] = datetime.utcnow().isoformat() + "Z"
        save_state(state)


if __name__ == "__main__":
    main()
