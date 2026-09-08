#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线补算「亚马逊推广经验 + 联系邮箱」，走 lark-cli 用户身份写回飞书。

与 workflow/refresh_influencers.py 计算逻辑完全一致，但：
- 抓取视频描述走 YouTube Data API（需 SOCKS5 代理 + YOUTUBE_API_KEY）；
- 读/写飞书走 lark-cli（用户身份，已验证可用），绕开 BitableClient 应用鉴权
  （该 base 对 .env 里的 app_id/secret 报 10014 unauthorized）。
用法:
  ./.venv/bin/python refresh_promo_lark.py --limit 3 --dry-run   # 烟雾测试
  ./.venv/bin/python refresh_promo_lark.py                       # 全量写回
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import write_user_base as wb
from write_user_base import BASE_TOKEN, TABLE_IDS, run
from filter.promo_detector import aggregate_channel_promo
from filter.email_extractor import extract_contact_email, extract_email
from youtube.channels import fetch_channel_details
from youtube.playlists import get_channel_uploads
from youtube.videos import fetch_video_details

# YouTube API 需翻墙。当前用户用 Clash HTTP 代理 (127.0.0.1:7890)。
# 优先用环境变量 HTTPS_PROXY（Clash 已设置），否则回退到显式 HTTP 代理。
# 注意：之前的 socks5h://127.0.0.1:10808 已废弃（端口未开放）。
import requests as _req
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "http://127.0.0.1:7890"
print(f"[代理] YouTube 走 {_PROXY}")
_orig_get = _req.get
def _pget(*a, **k):
    if "proxies" not in k:
        k["proxies"] = {"http": _PROXY, "https": _PROXY}
    return _orig_get(*a, **k)
_req.get = _pget


DETAIL_TID = TABLE_IDS["网红详情表"]
RECENT = 25


def _str(v):
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def list_all(tid, fields=None):
    out = []
    offset = 0
    while True:
        args = ["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
                "--table-id", tid, "--limit", "200", "--offset", str(offset), "--format", "json"]
        if fields:
            for f in fields:
                args += ["--field-id", f]
        j = run(args)
        if not j or not j.get("ok"):
            break
        d = j.get("data", {})
        fns = d.get("fields") or []
        rows = d.get("data") or []
        rids = d.get("record_id_list") or []
        for i, row in enumerate(rows):
            flds = dict(zip(fns, row)) if isinstance(row, list) else row.get("fields", {})
            rid = rids[i] if i < len(rids) else (row.get("record_id", "") if isinstance(row, dict) else "")
            out.append({"record_id": rid, "fields": flds})
        if len(rows) < 200:
            break
        offset += 200
    return out


def ensure_fields():
    """自建 promo/邮箱 字段（lark-cli field-create，不带 --yes）。
    飞书表当前只有 21 个原始字段，promo 相关 8 列需先建好，否则 upsert not_found。"""
    import json as _json
    tid = TABLE_IDS["网红详情表"]
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    items = (j.get("data", {}).get("fields") or j.get("data", {}).get("items") or [])
    existing = {f.get("name", ""): f for f in items}

    cols = [
        ("亚马逊推广经验", "select", ["Amazon Storefront", "Amazon 联盟客", "挂过Amazon链接", "其他联盟带货", "接过赞助", "未发现"]),
        ("Amazon带货视频数", "number", None),
        ("推广视频数", "number", None),
        ("推广证据", "text", None),
        ("Amazon Storefront", "url", None),
        ("邮箱来源", "text", None),
        ("邮箱出现视频数", "number", None),
        ("候选邮箱", "text", None),
    ]
    for name, ftype, opts in cols:
        if name in existing:
            continue
        if ftype == "select":
            body = {"name": name, "type": "select", "multiple": False,
                    "options": [{"name": o} for o in opts]}
        elif ftype == "number":
            body = {"name": name, "type": "number"}
        elif ftype == "url":
            body = {"name": name, "type": "text", "style": {"type": "url"}}
        else:
            body = {"name": name, "type": "text"}
        r = run(["base", "+field-create", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--json", _json.dumps(body, ensure_ascii=False)], expect_ok=False)
        if r and r.get("ok"):
            print(f"  ➕ 新建字段「{name}」({ftype})")
            existing[name] = {"name": name, "type": ftype}
        else:
            print(f"  ⚠ 字段「{name}」创建失败: {_json.dumps(r, ensure_ascii=False)[:200] if r else 'no resp'}")

    # 补「邮箱状态」单选选项（已存在字段，但可能缺「已获取/待找邮箱」）
    f = existing.get("邮箱状态")
    if f and f.get("type") in ("select", 3):
        have = [o.get("name") for o in (f.get("options") or []) if o.get("name")]
        changed = False
        for need in ["已获取", "待找邮箱"]:
            if need not in have:
                have.append(need)
                changed = True
        if changed:
            fid = f.get("id") or f.get("field_id")
            if fid:
                body = {"name": "邮箱状态", "type": "select", "multiple": False,
                        "options": [{"name": o} for o in have]}
                run(["base", "+field-update", "--as", "user", "--base-token", BASE_TOKEN,
                     "--table-id", tid, "--field-id", fid,
                     "--json", _json.dumps(body, ensure_ascii=False)], expect_ok=False)
                print(f"  🏷 字段「邮箱状态」补充选项: {have}")


def main():
    dry = "--dry-run" in sys.argv
    limit = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        elif a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print("⚠ 未找到 YOUTUBE_API_KEY（请确认 .env 已加载）")
        return

    # —— 先确保飞书表存在 promo/邮箱 相关字段（含单选选项），否则 upsert 会 not_found ——
    print("[建字段] 确保 promo/邮箱 字段存在(含单选选项)...")
    ensure_fields()

    recs = list_all(DETAIL_TID)
    print(f"[读取] 网红详情表 {len(recs)} 条")
    if limit:
        recs = recs[:limit]

    updated = 0
    skipped = 0
    promo_counts = {}
    email_found = 0
    for idx, rec in enumerate(recs, 1):
        f = rec["fields"]
        cid = _str(f.get("Channel ID"))
        if not cid:
            continue
        existing_email = _str(f.get("联系邮箱"))
        existing_promo = _str(f.get("亚马逊推广经验"))
        # 断点续传：已填好的频道跳过（重跑不会重复消耗 YouTube 配额）
        if existing_promo and existing_promo not in ("None",):
            skipped += 1
            continue
        ch_desc = _str(f.get("频道描述"))

        channels, err = fetch_channel_details(api_key, [cid])
        channel = channels[0] if channels else {}
        recent = []
        if not err and channel.get("uploads_playlist_id"):
            vids, verr = get_channel_uploads(
                api_key=api_key, channel_id=cid,
                uploads_playlist_id=str(channel.get("uploads_playlist_id", "") or ""),
                max_results=RECENT)
            if not verr and vids:
                recent, _, _ = fetch_video_details(api_key, vids)
        if err:
            print(f"  ! [{idx}/{len(recs)}] {cid} 频道资料失败(仅用简介填邮箱): {err}")
        print(f"  · [{idx}/{len(recs)}] {cid} 拉到视频 {len(recent)} 条")

        promo = aggregate_channel_promo(recent)
        email = extract_contact_email(ch_desc, [v.get("description", "") for v in recent])

        fields = {
            "亚马逊推广经验": promo["amazon_promo_level"],
            "Amazon带货视频数": promo["amazon_promo_video_count"],
            "推广视频数": promo["promo_video_count"],
            "推广证据": promo["promo_evidence"][:1000],
        }
        if promo["amazon_storefront_url"]:
            fields["Amazon Storefront"] = promo["amazon_storefront_url"]
        if email["contact_email"] and not existing_email:
            fields["联系邮箱"] = email["contact_email"]
            fields["邮箱状态"] = "已获取"
            fields["邮箱来源"] = email["email_source"]
            fields["邮箱出现视频数"] = email["email_hit_videos"]
            email_found += 1
        if email["email_candidates"]:
            fields["候选邮箱"] = ", ".join(email["email_candidates"])[:500]

        promo_counts[promo["amazon_promo_level"]] = promo_counts.get(promo["amazon_promo_level"], 0) + 1
        updated += 1
        if dry:
            print(f"  · {cid} promo={promo['amazon_promo_level']} email={email['contact_email'] or '(无/已有)'}")
            continue
        res = run(["base", "+record-upsert", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", DETAIL_TID, "--record-id", rec["record_id"],
             "--json", json.dumps(fields, ensure_ascii=False)], expect_ok=False)
        print("  upsert 返回:", str(res)[:300])
        time.sleep(0.12)

    print(f"\n{'[DRY] ' if dry else ''}处理 {updated} 个频道 | 跳过已处理 {skipped} | 邮箱新增 {email_found} | promo分布: {promo_counts}")


if __name__ == "__main__":
    main()
