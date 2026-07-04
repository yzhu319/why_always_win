"""实时时事话题管道（V1.3）。

多免费信源聚合 → 归一化 → 去政治化过滤 → 去重 → 10 分钟缓存 → 常青争议话题兜底。
全部信源免 key；Vercel 数据中心 IP 可达（Reddit 常被拦，作 best-effort）。

选题范围：先做科技/财经/科学/文娱/体育/生活等低敏板块，不碰直接的政治新闻，
尤其过滤任何提及领导人姓名的条目。政治板块待合规能力完善后再扩展。
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


def _gn_topic(topic: str) -> str:
    return f"https://news.google.com/rss/headlines/section/topic/{topic}?{_GN}"


# 只取低敏板块（科技/财经/科学/文娱/体育/健康）。刻意不接入「要闻/国际/WORLD」
# 与 BBC 中文等强政治源——这些留给二期政治板块。
_RSS_SOURCES = [
    ("Google新闻·科技", _gn_topic("TECHNOLOGY")),
    ("Google新闻·财经", _gn_topic("BUSINESS")),
    ("Google新闻·科学", _gn_topic("SCIENCE")),
    ("Google新闻·文娱", _gn_topic("ENTERTAINMENT")),
    ("Google新闻·体育", _gn_topic("SPORTS")),
    ("Google新闻·健康", _gn_topic("HEALTH")),
]

# 去政治化过滤：命中即丢弃该条。以领导人姓名为首要目标，兼顾硬政治高危词。
# 低敏板块本就少有政治条目，此表是防止个别政治外溢的兜底安全网。
_POLITICS_BLOCK = [
    # —— 中国领导人（现任 + 近现代最高层），首要过滤对象 ——
    "习近平", "李克强", "李强", "胡锦涛", "温家宝", "江泽民", "毛泽东", "邓小平",
    "赵乐际", "王沪宁", "蔡奇", "丁薛祥", "李希", "韩正", "王岐山",
    "总书记", "国家主席", "政治局", "中南海", "中央政治局", "国务院总理",
    # —— 常见外国领导人（多出现在政治新闻）——
    "拜登", "特朗普", "川普", "普京", "泽连斯基", "金正恩", "内塔尼亚胡",
    "马克龙", "朔尔茨", "尹锡悦", "岸田", "莫迪", "埃尔多安", "石破茂",
    # —— 硬政治 / 高危议题 ——
    "台独", "港独", "疆独", "藏独", "法轮", "六四", "政变", "大选", "弹劾",
    "军演", "台海", "战争", "核武器", "示威", "抗议", "白宫", "克里姆林宫",
    "外交部", "国台办", "两会", "党代会", "反送中", "颜色革命",
]


def is_political(title: str) -> bool:
    return any(w in title for w in _POLITICS_BLOCK)


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
    """Reddit 科技热帖（避开 r/worldnews 政治源；数据中心 IP 常被拦，best-effort）。"""
    r = await client.get("https://www.reddit.com/r/technology/hot.json?limit=8")
    r.raise_for_status()
    return [{"title": c["data"]["title"], "source": "Reddit r/technology",
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
        if is_political(it["title"]):   # 去政治化：丢弃提及领导人/硬政治议题的条目
            continue
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
