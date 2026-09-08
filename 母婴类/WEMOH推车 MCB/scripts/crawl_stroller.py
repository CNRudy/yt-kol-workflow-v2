#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚焦爬取推车 wagon 线红人：对指定关键词搜 YouTube 视频 -> 取作者频道 -> 拉频道统计。
输出 output/stroller_crawl_<date>.json（频道级去重），供后续整合进独立网红详情表。
用法: HTTPS_PROXY=... ./.venv/bin/python crawl_stroller.py
"""
import os, json, time, datetime, sys
import requests

PROJ = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(PROJ, ".env")).read().split("YOUTUBE_API_KEY=")[1].split("\n")[0].strip()
API = "https://www.googleapis.com/youtube/v3"

# 本次爬取的 5 个关键词（10 个里的核心 5 个：通用测评 + 竞品红人 + 妈妈受众）
KEYWORDS = [
    "stroller wagon review",
    "WonderFold W4 review",
    "Veer Cruiser review",
    "double stroller review",
    "baby gear must haves 2026",
]

# 官方品牌频道排除（避免把竞品自营号当红人）
BRAND_EXCLUDE = ["wonderfold", "veer", "momfann", "evenflo", "radio flyer",
                 "keenz", "baby trend", "graco", "inglesina", "bob gear", "jeep"]

sess = requests.Session()
sess.proxies = {"https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890"),
                "http": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890")}


def yt(endpoint, params, retries=3):
    params["key"] = KEY
    for i in range(retries):
        try:
            r = sess.get(f"{API}/{endpoint}", params=params, timeout=25)
            j = r.json()
            if "error" in j:
                print(f"  ! API error: {j['error'].get('message')}")
                if j['error'].get('code') in (403, 429):
                    return None
            return j
        except Exception as e:
            print(f"  ! {endpoint} 重试{i+1}: {e}")
            time.sleep(2)
    return None


def main():
    channels = {}  # cid -> rec
    for kw in KEYWORDS:
        print(f"\n[搜] {kw}")
        j = yt("search", {"part": "snippet", "q": kw, "type": "video",
                          "maxResults": 30, "order": "relevance", "regionCode": "US"})
        if not j or "items" not in j:
            print("  跳过(无结果/限流)")
            continue
        vid_items = j["items"]
        print(f"  视频结果 {len(vid_items)} 条")
        cids = []
        for it in vid_items:
            sn = it.get("snippet", {})
            cid = sn.get("channelId")
            if cid and cid not in cids:
                cids.append(cid)
                # 暂存视频标题/播放(搜索不返回播放，留空)
                channels.setdefault(cid, {"_videos": []})
                channels[cid]["_videos"].append(sn.get("title", ""))
        # 拉频道统计
        for i in range(0, len(cids), 50):
            batch = cids[i:i+50]
            jc = yt("channels", {"part": "snippet,statistics,contentDetails",
                                 "id": ",".join(batch)})
            if not jc or "items" not in jc:
                continue
            for ch in jc["items"]:
                cid = ch["id"]
                sn = ch.get("snippet", {})
                st = ch.get("statistics", {})
                title = sn.get("title", "")
                desc = sn.get("description", "")
                low = (title + " " + desc).lower()
                if any(b in low for b in BRAND_EXCLUDE) and "review" not in low:
                    print(f"  - 排除官方品牌频道: {title}")
                    channels.pop(cid, None)
                    continue
                rec = channels.setdefault(cid, {"_videos": []})
                rec.update({
                    "Channel ID": cid,
                    "Channel Name": title,
                    "频道URL": f"https://www.youtube.com/channel/{cid}",
                    "订阅数": int(st.get("subscriberCount", 0) or 0),
                    "频道总播放量": int(st.get("viewCount", 0) or 0),
                    "视频总数": int(st.get("videoCount", 0) or 0),
                    "国家/地区": sn.get("country", ""),
                    "频道描述": desc[:2000],
                    "频道创建日期": sn.get("publishedAt", "")[:10],
                    "来源关键词": kw,
                    "_videos": rec.get("_videos", []),
                })
            time.sleep(0.3)
        time.sleep(0.5)

    # 清洗 _videos，取代表视频标题
    out = []
    for cid, rec in channels.items():
        vids = rec.pop("_videos", [])
        rec["代表视频标题"] = vids[0] if vids else ""
        rec["代表视频播放量"] = ""
        rec["代表视频互动率"] = ""
        rec["代表视频URL"] = ""
        out.append(rec)

    date = datetime.date.today().strftime("%Y%m%d")
    os.makedirs(os.path.join(PROJ, "output"), exist_ok=True)
    path = os.path.join(PROJ, "output", f"stroller_crawl_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 爬取完成：{len(out)} 个频道 -> {path}")


if __name__ == "__main__":
    main()
