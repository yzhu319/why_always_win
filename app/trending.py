"""实时时事赢面：轻量抓取海外公开热点（V1.0 仅 Reddit 公开 JSON，10 分钟缓存）。"""

import time

import httpx

_SOURCES = [
    ("Reddit r/worldnews", "https://www.reddit.com/r/worldnews/hot.json?limit=8"),
    ("Reddit r/technology", "https://www.reddit.com/r/technology/hot.json?limit=6"),
]
_UA = {"User-Agent": "windian-ai-alpha/0.1 (internal test)"}

_cache: dict = {"ts": 0.0, "items": []}
_TTL = 600  # 秒


async def get_trending() -> list[dict]:
    now = time.time()
    if now - _cache["ts"] < _TTL and _cache["items"]:
        return _cache["items"]

    items: list[dict] = []
    async with httpx.AsyncClient(timeout=10, headers=_UA, follow_redirects=True) as client:
        for source_name, url in _SOURCES:
            try:
                r = await client.get(url)
                r.raise_for_status()
                for child in r.json()["data"]["children"]:
                    d = child["data"]
                    if d.get("stickied"):
                        continue
                    items.append({
                        "title": d["title"],
                        "source": source_name,
                        "score": d.get("score", 0),
                        "url": "https://www.reddit.com" + d.get("permalink", ""),
                    })
            except Exception:
                continue  # 单源失败不影响其余源

    items.sort(key=lambda x: -x["score"])
    if items:
        _cache["ts"] = now
        _cache["items"] = items[:12]
    return _cache["items"]
