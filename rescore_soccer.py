#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「网红详情表」中匹配产品为空的记录补算足球护膝(Shin Guards)匹配评分。

背景：足球关键词批次（soccer mom / youth soccer equipment …）的红人此前被
8 月全表匹配按婴儿车画像算过、后来还原清空，导致匹配产品一直空着。
本脚本用 youth_soccer_shin_guard 画像重新给他们算一遍并写回。

只处理「匹配产品为空」的记录，不会动已有匹配结果（WEMOH C1 / CFC1）。

用法:
    python rescore_soccer.py --dry-run   # 只算不写，先看样本与分布
    python rescore_soccer.py             # 写回「网红详情表」
"""
import json
import os
import sys
import time
import types
from collections import Counter

try:
    import dotenv  # noqa: F401
except ImportError:
    _m = types.ModuleType("dotenv")
    _m.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _m

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import write_user_base as wb

wb.LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["LARK_CLI_NO_PROXY_WARN"] = "1"

from write_user_base import BASE_TOKEN, TABLE_IDS  # noqa: E402
from score_influencers import list_all  # noqa: E402
from filter.scoring import score_influencer  # noqa: E402

DETAIL_TID = TABLE_IDS["网红详情表"]
VIDEO_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "local_cache", "channel_videos.json")
PROFILE_KEY = "youth_soccer_shin_guard"
BATCH = 200

# 主表字段类型（+field-list 实测）
TYPES = {
    "匹配产品": "text",
    "品牌匹配度": "number",
    "内容契合类型": "text",
    "匹配关键词": "text",
    "开发优先级": "select",
    "推荐理由": "text",
    "匹配排序组": "text",
}
PRIORITY_OPTS = {"S", "A", "B", "C"}


def _str(v):
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def _num(v):
    try:
        if isinstance(v, list):
            v = v[0] if v else None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_profile():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "product_profiles.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return data["profiles"].get(PROFILE_KEY)


def load_video_map():
    """Channel ID -> [video dict]，用于内容契合检测。"""
    if not os.path.exists(VIDEO_CACHE):
        return {}
    with open(VIDEO_CACHE, encoding="utf-8") as fh:
        data = json.load(fh)
    vmap = {}
    for rec in data.get("records", []):
        f = rec.get("fields", {})
        cid = _str(f.get("Channel ID", ""))
        if not cid:
            continue
        vmap.setdefault(cid, []).append({
            "title": _str(f.get("Video Title", "")),
            "tags": _str(f.get("Tags", "")),
            "description": _str(f.get("字幕内容", "")),
            "view_count": _num(f.get("Views")),
            "engagement_rate": _num(f.get("互动率(%)")),
        })
    return vmap


def main():
    dry = "--dry-run" in sys.argv

    profile = load_profile()
    if not profile:
        print(f"❌ 未找到产品画像 {PROFILE_KEY}")
        return
    print(f"[画像] {profile.get('name')}")
    print(f"       市场={profile.get('markets')} min_views={profile.get('min_views')} "
          f"min_eng={profile.get('min_engagement')}")

    print("[拉取] 网红详情表 …")
    details = list_all(DETAIL_TID)
    targets = [r for r in details if not _str(r["fields"].get("匹配产品")).strip()]
    print(f"  总记录 {len(details)} / 匹配产品为空 {len(targets)} 条")

    if not targets:
        print("✅ 没有需要补算的记录")
        return

    print("[加载] 视频缓存 …")
    vmap = load_video_map()
    covered = sum(1 for r in targets
                  if _str(r["fields"].get("Channel ID")) in vmap)
    print(f"  覆盖频道 {len(vmap)} 个 / 待算 {len(targets)} 条中命中 {covered} 条")

    updates = {}
    stats = Counter()
    fit_scores = []
    samples = []

    for rec in targets:
        f = rec["fields"]
        cid = _str(f.get("Channel ID", ""))
        email_raw = _str(f.get("联系邮箱", "")) or _str(f.get("候选邮箱", ""))
        email_status = _str(f.get("邮箱状态", ""))
        promo = _str(f.get("亚马逊推广经验", ""))

        has_email = (email_status == "已获取") or bool(email_raw.strip())
        has_promo = promo not in ("", "未发现", "未发现推广经验")
        group = "1-有邮箱+推广" if (has_email and has_promo) else "2-其余"

        detail = {
            "amazon_promo_level": promo,
            "email_status": email_status,
            "rep_video_engagement": _num(f.get("代表视频互动率")),
            "country": _str(f.get("国家/地区", "")),
            "activity_status": _str(f.get("断更评估", "")),
            "channel_description": _str(f.get("频道描述", "")),
            "rep_video_title": _str(f.get("代表视频标题", "")),
        }
        videos = vmap.get(cid, [])
        scored = score_influencer(detail, videos, profile)

        prio = scored.get("dev_priority", "C")
        if prio not in PRIORITY_OPTS:
            prio = "C"

        patch = {
            "匹配产品": profile["name"],
            "品牌匹配度": _num(scored.get("brand_fit_score")),
            "内容契合类型": _str(scored.get("content_types")),
            "匹配关键词": _str(scored.get("matched_keywords")),
            "开发优先级": prio,
            "推荐理由": _str(scored.get("recommend_reason")),
            "匹配排序组": group,
        }
        # select 不能传 None；number None 直接传
        if not patch["开发优先级"]:
            patch["开发优先级"] = "C"

        updates[rec["record_id"]] = patch
        stats[prio] += 1
        fit_scores.append(_num(scored.get("brand_fit_score")) or 0)
        if len(samples) < 5:
            samples.append({
                "cid": cid,
                "name": _str(f.get("Channel Name", ""))[:22],
                "subs": _str(f.get("订阅数", "")),
                "fit": patch["品牌匹配度"],
                "prio": prio,
                "types": patch["内容契合类型"][:24],
                "kw": patch["匹配关键词"][:40],
                "reason": patch["推荐理由"][:60],
            })

    print("\n" + "=" * 62)
    print("补算结果")
    print("=" * 62)
    print(f"  待写入记录 : {len(updates)}")
    print(f"  开发优先级 : {dict(stats)}")
    valid = [s for s in fit_scores]
    if valid:
        print(f"  品牌匹配度 : 平均 {sum(valid)/len(valid):.1f} / "
              f"最高 {max(valid):.0f} / 最低 {min(valid):.0f}")
        zero = sum(1 for s in valid if s == 0)
        print(f"             0 分记录 {zero} 条（{zero/len(valid)*100:.1f}%）")

    print("\n  样本（前5）:")
    for s in samples:
        print(f"    {s['name']:22s} 订阅={s['subs']:>8s} 匹配度={s['fit']:>5} "
              f"优先级={s['prio']} 类型={s['types']}")
        print(f"       关键词: {s['kw']}")
        print(f"       理由  : {s['reason']}")

    if dry:
        print("\n[dry-run] 未写入飞书。")
        return

    print(f"\n[写入] 分 {(len(updates)+BATCH-1)//BATCH} 批更新 {len(updates)} 条…")
    items = list(updates.items())
    ok = 0
    for i in range(0, len(items), BATCH):
        chunk = {rid: p for rid, p in items[i:i + BATCH]}
        r = wb.run(["base", "+record-batch-update", "--as", "user", "--base-token", BASE_TOKEN,
                    "--table-id", DETAIL_TID,
                    "--json", json.dumps({"update_records": chunk}, ensure_ascii=False)],
                   expect_ok=False)
        if r and r.get("ok"):
            ok += len(chunk)
            print(f"    ✓{len(chunk)} 条")
        else:
            print(f"    ❌ 批次失败: {json.dumps(r, ensure_ascii=False)[:300]}")
        time.sleep(0.2)

    print(f"\n✅ 已写入 {ok} 条")
    final = list_all(DETAIL_TID)
    c = Counter(_str(x["fields"].get("匹配产品")) or "(空)" for x in final)
    print("   主表匹配产品分布:")
    for k, v in c.most_common():
        print(f"     {v:>5}  {k[:56]}")


if __name__ == "__main__":
    main()
