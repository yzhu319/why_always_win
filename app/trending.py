"""实时时事话题管道（V1.1）。

多免费信源聚合 → 归一化 → 去重 → 10 分钟缓存 → 常青争议话题兜底。
全部信源免 key；Vercel 数据中心 IP 可达（Reddit 常被拦，作 best-effort）。
"""

import asyncio
import time
import xml.etree.ElementTree as ET

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (compatible; windian-ai/0.2; +https://whyalwayswin.vercel.app)"}
_TTL = 600
_cache: dict = {"ts": 0.0, "items": []}

# 常青争议话题：保证冷启动/断网时首页永远有可点的种子话题
SEED_TOPICS = [
    "AI会不会大规模取代白领工作",
    "大厂裁员潮：该卷还是该躺平",
    "预制菜该不该进校园食堂",
    "油车 vs 电车：现在买车怎么选",
    "35岁职场门槛合理吗",
    "国产大模型和硅谷的差距还有多大",
    "年轻人不结婚不生娃是问题吗",
    "调休式放假该不该取消",
    "直播带货是消费升级还是智商税",
    "学历贬值：现在读研还值不值",
    "远程办公该不该全面推广",
    "芯片自主化：卡脖子还能卡多久",
    "月薪多少才能在一线城市体面生活",
    "短视频正在让人变笨吗",
    "外卖骑手困在系统里，平台该担责吗",
    "全球变暖议题被夸大了吗",
    "彩礼是陋习还是保障",
    "马斯克的火星移民计划是认真的吗",
]

_GN = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
_RSS_SOURCES = [
    ("Google新闻·要闻", f"https://news.google.com/rss?{_GN}"),
    ("Google新闻·国际", f"https://news.google.com/rss/headlines/section/topic/WORLD?{_GN}"),
    ("Google新闻·科技", f"https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?{_GN}"),
    ("BBC中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
]


def _parse_rss(text: str, source: str, limit: int = 10) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(text)
        for rank, item in enumerate(root.iter("item")):
            if rank >= limit:
                break
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                items.append({"title": title, "source": source, "url": link,
                              "heat": limit - rank, "kind": "news"})
    except ET.ParseError:
        pass
    return items


async def _fetch_rss(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    r = await client.get(url)
    r.raise_for_status()
    return _parse_rss(r.text, source)


async def _fetch_hn(client: httpx.AsyncClient) -> list[dict]:
    """Hacker News 头版（Algolia 免费 API），海外科技圈热议。"""
    r = await client.get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=8")
    r.raise_for_status()
    return [{"title": h["title"], "source": "HackerNews",
             "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "heat": h.get("points", 0) // 20, "kind": "news"}
            for h in r.json().get("hits", []) if h.get("title")]


async def _fetch_reddit(client: httpx.AsyncClient) -> list[dict]:
    """Reddit 热帖（数据中心 IP 常被拦，best-effort）。"""
    r = await client.get("https://www.reddit.com/r/worldnews/hot.json?limit=8")
    r.raise_for_status()
    return [{"title": c["data"]["title"], "source": "Reddit r/worldnews",
             "url": "https://www.reddit.com" + c["data"].get("permalink", ""),
             "heat": c["data"].get("score", 0) // 1000, "kind": "news"}
            for c in r.json()["data"]["children"] if not c["data"].get("stickied")]


async def get_trending(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and now - _cache["ts"] < _TTL and _cache["items"]:
        return _cache["items"]

    tasks = []
    async with httpx.AsyncClient(timeout=8, headers=_UA, follow_redirects=True) as client:
        for source, url in _RSS_SOURCES:
            tasks.append(_fetch_rss(client, source, url))
        tasks.append(_fetch_hn(client))
        tasks.append(_fetch_reddit(client))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # 各源内部按热度排序，跨源轮询交织：中文源优先，混合多样，避免单源刷屏
    buckets = [sorted(res, key=lambda x: -x["heat"]) for res in results if isinstance(res, list) and res]
    seen: set[str] = set()
    deduped: list[dict] = []
    i = 0
    while any(buckets):
        bucket = buckets[i % len(buckets)]
        i += 1
        if not bucket:
            continue
        it = bucket.pop(0)
        key = it["title"][:12]
        if key not in seen:
            seen.add(key)
            deduped.append(it)

    if deduped:
        _cache["ts"] = now
        _cache["items"] = deduped[:20]
    return _cache["items"]


def seed_topics() -> list[dict]:
    return [{"title": t, "source": "常辩常新", "url": "", "heat": 0, "kind": "seed"}
            for t in SEED_TOPICS]
