#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对【WEMOH推车 MCB】的 201 个红人跑 stroller_wagon 画像评分 + Amazon推广经验 + 邮箱 + 断更,写回两张表。

- 135 个原库红人：复用 local_cache/videos.json 的视频（无 YouTube 配额消耗）
- 58 个新爬红人（66 新爬中 8 个已在缓存）：现抓最近 25 条视频（走 Clash 代理）
- 评分画像：product_profiles.json 的 "stroller_wagon"（按名字指定，不改 active）

用法:
  HTTPS_PROXY=http://127.0.0.1:7890 ./.venv/bin/python score_stroller.py --dry-run   # 只算不写
  HTTPS_PROXY=http://127.0.0.1:7890 ./.venv/bin/python score_stroller.py             # 写回飞书
"""
import os, json, time, datetime, subprocess, sys, re
import requests
import openpyxl

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
from filter.scoring import score_influencer
from filter.promo_detector import aggregate_channel_promo
from filter.email_extractor import extract_contact_email
from filter.activity_evaluator import evaluate_channel_activity

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
KEY = open(os.path.join(PROJ, ".env")).read().split("YOUTUBE_API_KEY=")[1].split("\n")[0].strip()
API = "https://www.googleapis.com/youtube/v3"
PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890")
DATE = "20260904"
XLSX_DETAIL = os.path.join(PROJ, "output", f"stroller_wagon_网红详情表_{DATE}.xlsx")
BASE_TOKEN = "HFBkbxyQNa4pjYsrXOJcRwU5nHb"
TID_DETAIL = "tblLhOXxrlq4r8VF"
TID_RED = "tbllyO5EJiLsfT6E"

PROFILE_NAME = "stroller_wagon"
# 飞书「匹配产品」单选字段已存在的精确选项值（须一字不差匹配）
MATCH_LABEL = "WEMOH 2-Seater Stroller Wagon (MCB001-WE)"
# 单选字段值与已有选项的映射（不在表选项集会被拒）
PRIORITY_MAP = {"S": "A"}  # 红人表 schema 不允许加 S，把 S 档并入 A 档
CONTENT_TYPE_FALLBACK = "使用体验/开箱/种草/评测"  # 内容契合类型的通用兜底

_VALID_CONTENT_TYPES = {
    "使用体验", "使用体验/开箱/种草", "使用体验/开箱/种草/评测",
    "使用体验/种草", "使用体验/种草/评测", "使用体验/评测",
    "开箱/种草", "开箱/种草/评测", "开箱/评测",
    "种草", "种草/评测", "评测",
}


def _map_content_type(raw):
    """把 scoring 返回的 content_types（可能含不在集合里的单字）映射到合法选项。"""
    if isinstance(raw, str):
        s = {raw}
    else:
        s = set(raw or [])
    for c in s:
        if c in _VALID_CONTENT_TYPES:
            return c
    joined = "/".join(sorted(s))
    if joined in _VALID_CONTENT_TYPES:
        return joined
    def has(pieces): return all(p in s for p in pieces)
    if has(["使用体验", "开箱", "种草", "评测"]): return "使用体验/开箱/种草/评测"
    if has(["使用体验", "种草", "评测"]):       return "使用体验/种草/评测"
    if has(["使用体验", "开箱", "种草"]):       return "使用体验/开箱/种草"
    if has(["开箱", "种草", "评测"]):           return "开箱/种草/评测"
    if has(["使用体验", "种草"]):               return "使用体验/种草"
    if has(["使用体验", "评测"]):               return "使用体验/评测"
    if has(["开箱", "评测"]):                   return "开箱/评测"
    if has(["种草", "评测"]):                   return "种草/评测"
    if has(["开箱", "种草"]):                   return "开箱/种草"
    if has(["使用体验"]):                       return "使用体验"
    if has(["评测"]):                           return "评测"
    return CONTENT_TYPE_FALLBACK


# 邮箱来源单选字段只有「视频描述」一个选项，把所有来源统一映射到它
EMAIL_SOURCE_MAP = {
    "频道简介": "视频描述",
    "视频描述": "视频描述",
    "未找到":   "视频描述",
}
FETCH_CACHE = os.path.join(PROJ, "output", f"stroller_fetched_videos_{DATE}.json")
sess = requests.Session()
sess.proxies = {"https": PROXY, "http": PROXY}

# 写回飞书的列（按各表实际字段取交集）
WRITE_COLS = ["匹配产品", "品牌匹配度", "内容契合类型", "匹配关键词", "开发优先级",
              "推荐理由", "亚马逊推广经验", "推广证据", "Amazon Storefront",
              "联系邮箱", "邮箱来源", "代表视频互动率", "断更评估", "最新发布日期"]


def yt_get(endpoint, params, retries=3):
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


def norm_cache_video(f):
    """把 local_cache/videos.json 的中文键视频归一化为评分模块期望的英文键。"""
    def num(v):
        try:
            return float(str(v).replace("%", "").replace(",", "").strip() or 0)
        except Exception:
            return 0.0
    return {
        "title": str(f.get("Video Title", "") or ""),
        "tags": str(f.get("Tags", "") or ""),
        "description": str(f.get("描述", "") or ""),
        "view_count": num(f.get("Views")),
        "engagement_rate": num(f.get("互动率(%)")),
        "published_at": str(f.get("Publish Time", "") or ""),
        "published_at_raw": "",
        "likes": num(f.get("Likes")),
        "comments": num(f.get("Comments")),
    }


def parse_video_item(item):
    """把 videos.list 单条响应解析为归一化视频字典（与 norm_cache_video 同构）。"""
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    vc = num_or(st.get("viewCount", 0))
    lc = num_or(st.get("likeCount", 0))
    cc = num_or(st.get("commentCount", 0))
    eng = round((lc + cc) / vc * 100, 2) if vc > 0 else 0.0
    return {
        "title": sn.get("title", ""),
        "tags": ",".join(sn.get("tags", []) or []),
        "description": sn.get("description", ""),
        "view_count": vc,
        "engagement_rate": eng,
        "published_at_raw": sn.get("publishedAt", ""),
        "published_at": sn.get("publishedAt", "").replace("Z", "").replace("T", " ")[:19] if sn.get("publishedAt") else "",
        "likes": lc, "comments": cc,
    }


def num_or(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def fetch_channel_videos(cid):
    """现抓某频道最近 25 条视频（uploads playlist + videos.list），返回归一化视频列表。"""
    jc = yt_get("channels", {"part": "contentDetails", "id": cid, "maxResults": 1})
    if not jc or "items" not in jc or not jc["items"]:
        return []
    uploads = jc["items"][0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    if not uploads:
        return []
    jp = yt_get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": 25})
    if not jp or "items" not in jp:
        return []
    vids = [it["contentDetails"]["videoId"] for it in jp["items"] if it.get("contentDetails", {}).get("videoId")]
    if not vids:
        return []
    out = []
    for i in range(0, len(vids), 50):
        jv = yt_get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(vids[i:i + 50])})
        if jv and "items" in jv:
            for it in jv["items"]:
                out.append(parse_video_item(it))
    return out


def load_cache_videos():
    vj = json.load(open(os.path.join(PROJ, "local_cache", "videos.json"), encoding="utf-8"))
    recs = vj.get("records", vj) if isinstance(vj, dict) else vj
    cache = {}
    for rec in recs:
        f = rec.get("fields", rec) if isinstance(rec, dict) else {}
        cid = str(f.get("Channel ID", "") or "")
        if cid:
            cache.setdefault(cid, []).append(norm_cache_video(f))
    return cache


def load_detail_rows():
    wb = openpyxl.load_workbook(XLSX_DETAIL, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    out = {}
    for r in rows[1:]:
        rec = {hdr[i]: (r[i] if i < len(r) else None) for i in range(len(hdr))}
        cid = str(rec.get("Channel ID", "") or "")
        if cid:
            out[cid] = rec
    return hdr, out


def run_lark(args, expect_ok=True):
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"
    p = subprocess.run([LARK] + args, capture_output=True, text=True, timeout=120, cwd=PROJ, env=env)
    raw = p.stdout.strip() or p.stderr.strip()
    if raw and not raw.startswith("{"):
        i = raw.find("{"); raw = raw[i:] if i >= 0 else raw
    try:
        j = json.loads(raw)
    except Exception:
        print("  ! 非JSON:", raw[:300]); return None
    if expect_ok and not j.get("ok"):
        print("  ! lark-cli 失败:", json.dumps(j, ensure_ascii=False)[:400]); return None
    return j


def fetch_record_ids(tid):
    """返回 Channel ID -> record_id 映射。"""
    m = {}
    offset = 0
    while True:
        j = run_lark(["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
                      "--table-id", tid, "--limit", "200", "--offset", str(offset),
                      "--field-id", "Channel ID", "--format", "json"], expect_ok=False)
        if not j or not j.get("ok"):
            break
        d = j["data"]
        rows = d.get("data") or []
        rids = d.get("record_id_list") or []
        fnames = d.get("fields") or []
        for idx, row in enumerate(rows):
            cid = str(row[0]) if isinstance(row, list) and row else (row.get("Channel ID") if isinstance(row, dict) else "")
            rid = rids[idx] if idx < len(rids) else ""
            if cid:
                m[cid] = rid
        if len(rows) < 200:
            break
        offset += 200
    return m


def fetch_field_names(tid):
    j = run_lark(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
                  "--table-id", tid, "--format", "json"], expect_ok=False)
    items = (j.get("data", {}).get("fields") or []) if j else []
    return {f.get("name", "") for f in items}


def main():
    dry = "--dry-run" in sys.argv
    profile = json.load(open(os.path.join(PROJ, "product_profiles.json"), encoding="utf-8"))["profiles"][PROFILE_NAME]
    print(f"[画像] {profile['name']}")

    _, details = load_detail_rows()
    print(f"[详情表] {len(details)} 个频道")

    cache = load_cache_videos()
    print(f"[视频缓存] 覆盖 {len(cache)} 个频道")

    # 现抓不在缓存的（优先读本地抓取缓存，避免重复消耗配额）
    fetched = {}
    if os.path.exists(FETCH_CACHE):
        try:
            fetched = json.load(open(FETCH_CACHE, encoding="utf-8"))
            print(f"[抓取缓存] 复用 {len(fetched)} 个已抓频道")
        except Exception:
            fetched = {}
    fresh_cids = [c for c in details if c not in cache and c not in fetched]
    print(f"[现抓] {len(fresh_cids)} 个新频道需 YouTube API 抓取…")
    for i, cid in enumerate(fresh_cids, 1):
        vids = fetch_channel_videos(cid)
        fetched[cid] = vids
        if i % 10 == 0:
            print(f"  已抓 {i}/{len(fresh_cids)}")
        time.sleep(0.2)
    if fresh_cids:
        json.dump(fetched, open(FETCH_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  已持久化抓取结果 -> {FETCH_CACHE}")

    # 评分
    results = {}  # cid -> write dict
    tier = {}
    for cid, rec in details.items():
        vids = cache.get(cid) or fetched.get(cid) or []
        promo = aggregate_channel_promo(vids)
        email = extract_contact_email(str(rec.get("频道描述", "") or ""),
                                      [v.get("description", "") for v in vids])
        act = evaluate_channel_activity(vids)
        # 代表视频互动率：取播放最高视频的 engagement
        rep_eng = 0.0
        if vids:
            top = max(vids, key=lambda v: v.get("view_count", 0))
            rep_eng = top.get("engagement_rate", 0.0)
        detail = {
            "amazon_promo_level": promo["amazon_promo_level"],
            "email_status": "已获取" if email["contact_email"] else "需手动查找",
            "rep_video_engagement": rep_eng,
            "country": str(rec.get("国家/地区", "") or ""),
            "activity_status": act["activity_status"],
            "channel_description": str(rec.get("频道描述", "") or ""),
            "rep_video_title": str(rec.get("代表视频标题", "") or ""),
        }
        scored = score_influencer(detail, vids, profile)
        tier[scored["dev_priority"]] = tier.get(scored["dev_priority"], 0) + 1
        # 单选字段安全化：所有值必须落在飞书字段选项集内
        priority = PRIORITY_MAP.get(scored["dev_priority"], scored["dev_priority"])
        ctype = _map_content_type(scored.get("content_types"))
        esource = EMAIL_SOURCE_MAP.get(email.get("email_source", ""), "视频描述")
        results[cid] = {
            "匹配产品": MATCH_LABEL,
            "品牌匹配度": int(scored["brand_fit_score"]),
            "内容契合类型": ctype,
            "匹配关键词": scored["matched_keywords"],
            "开发优先级": priority,
            "推荐理由": scored["recommend_reason"],
            "亚马逊推广经验": promo["amazon_promo_level"],
            "推广证据": promo["promo_evidence"],
            "Amazon Storefront": promo["amazon_storefront_url"],
            "联系邮箱": email["contact_email"],
            "邮箱来源": esource,
            "代表视频互动率": rep_eng,
            "断更评估": act["activity_status"],
            "最新发布日期": act["latest_published_at"],
        }

    print(f"\n[分层] " + " / ".join(f"{t}档={tier.get(t,0)}" for t in ("S", "A", "B", "C")))
    if dry:
        print("\n=== 预览（前 12 个）===")
        for i, (cid, w) in enumerate(results.items()):
            if i >= 12:
                break
            print(f"  {details[cid].get('Channel Name','')[:28]:28s} | {w['开发优先级']} | 匹配{w['品牌匹配度']} | {w['亚马逊推广经验']} | {w['匹配关键词'][:40]}")
        print(f"\n✅ dry-run 完成，共 {len(results)} 个，未写飞书")
        return

    # 写回两张表
    for tid, label in [(TID_DETAIL, "网红详情表"), (TID_RED, "红人表")]:
        fields_avail = fetch_field_names(tid)
        cols = [c for c in WRITE_COLS if c in fields_avail]
        rid_map = fetch_record_ids(tid)
        upd = {}
        for cid, w in results.items():
            rid = rid_map.get(cid)
            if not rid:
                continue
            upd[rid] = {c: w[c] for c in cols}
        # 批量写（≤200/批）
        ok = fail = 0
        items = list(upd.items())
        for i in range(0, len(items), 200):
            chunk = dict(items[i:i + 200])
            body = json.dumps({"update_records": chunk}, ensure_ascii=False)
            tmp = os.path.join(PROJ, "._score_tmp.json")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            j = run_lark(["base", "+record-batch-update", "--as", "user", "--base-token", BASE_TOKEN,
                          "--table-id", tid, "--json", "@._score_tmp.json", "--format", "json"], expect_ok=False)
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            if j and j.get("ok"):
                ok += len(chunk)
            else:
                fail += len(chunk)
                print(f"  ✗ {label} 批次失败: {json.dumps(j, ensure_ascii=False)[:300]}")
            time.sleep(0.3)
        print(f"[写回] {label}: 成功 {ok} | 失败 {fail} | 列={cols}")

    print("\n🎉 评分写回完成")


if __name__ == "__main__":
    main()
