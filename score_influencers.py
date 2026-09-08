#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线给「网红详情表」补算 品牌匹配度 / 开发优先级 / 推荐理由（不消耗 YouTube 配额）。

读取飞书里的「网红视频表」(每频道最近 N 条视频的标题/Tags/描述) 与「网红详情表」
(亚马逊推广经验/邮箱/互动率/国家/断更)，用产品画像(product_profiles.json 的 active)
算出评分并写回「网红详情表」。适合：刚加完评分功能、想给存量红人一次性补算。

用法:
    ./.venv/bin/python score_influencers.py --dry-run   # 只算不写，先看样本
    ./.venv/bin/python score_influencers.py             # 写回飞书
"""
import json
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import write_user_base as wb
from write_user_base import BASE_TOKEN, TABLE_IDS, run, LARK
from filter.scoring import get_active_profile, score_influencer

DETAIL_TID = TABLE_IDS["网红详情表"]
VIDEO_TID = TABLE_IDS["网红视频表"]


def run_long(args, timeout=300, expect_ok=False):
    """Like run() but with a longer timeout for upsert calls that sometimes
    take >120s on the Feishu API side."""
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"
    try:
        p = subprocess.run([LARK] + args, capture_output=True, text=True,
                           timeout=timeout, cwd=wb.BASE_DIR, env=env)
        out = p.stdout.strip()
        err = p.stderr.strip()
        raw = out if out else err
        if raw and not raw.startswith("{"):
            for line in raw.split("\n"):
                if line.strip().startswith("{"):
                    raw = line.strip()
                    break
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    except subprocess.TimeoutExpired:
        return {}
    except Exception:
        return {}

# 评分写回的 6 个新列
SCORE_COLS = ["匹配产品", "品牌匹配度", "内容契合类型", "匹配关键词", "开发优先级", "推荐理由"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _str(v):
    """Coerce a Feishu cell value to a plain string.

    Single-select / multi-select cells come back as one-element lists
    (e.g. ``['US']``); plain text/number cells come back as-is.
    """
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def list_all(tid: str, fields=None):
    """Paginate record-list (offset-based, this lark-cli has no --page-token);
    return list of {record_id, fields} dicts.

    Feishu returns ``data`` as a list of value-lists aligned to ``fields``
    metadata, with ``record_id_list`` aligned by index.
    """
    out = []
    offset = 0
    while True:
        args = ["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
                "--table-id", tid, "--limit", "200", "--offset", str(offset),
                "--format", "json"]
        if fields:
            for f in fields:
                args += ["--field-id", f]
        j = run(args)
        if not j or not j.get("ok"):
            break
        d = j.get("data", {})
        field_names = d.get("fields") or []
        rows = d.get("data") or []
        rids = d.get("record_id_list") or []
        for idx, row in enumerate(rows):
            if isinstance(row, list):
                flds = dict(zip(field_names, row))
            elif isinstance(row, dict):
                flds = row.get("fields", {})
            else:
                flds = {}
            rid = rids[idx] if idx < len(rids) else (
                row.get("record_id", "") if isinstance(row, dict) else "")
            out.append({"record_id": rid, "fields": flds})
        if len(rows) < 200:
            break
        offset += 200
    return out


def ensure_score_fields():
    """Create the 6 scoring columns in Feishu if missing (no --yes flag).

    注意：不能用 wb.ensure_schema —— 它给 field-create 传 --yes，而 lark-cli
    不支持该参数，会导致字段静默创建失败、后续 upsert not_found。这里直接
    用不带 --yes 的 field-create（与 refresh_promo_lark.py 的 ensure_fields 一致）。
    """
    import json as _json
    tid = DETAIL_TID
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    items = (j.get("data", {}).get("fields") or j.get("data", {}).get("items") or [])
    existing = {f.get("name", ""): f for f in items}

    cols = [
        ("匹配产品", "text", None),
        ("品牌匹配度", "number", None),
        ("内容契合类型", "text", None),
        ("匹配关键词", "text", None),
        ("开发优先级", "select", ["S", "A", "B", "C"]),
        ("推荐理由", "text", None),
    ]
    print("[建字段] 确保网红详情表存在 6 个评分列…")
    for name, ftype, opts in cols:
        if name in existing:
            continue
        if ftype == "select":
            body = {"name": name, "type": "select", "multiple": False,
                    "options": [{"name": o} for o in opts]}
        elif ftype == "number":
            body = {"name": name, "type": "number"}
        else:
            body = {"name": name, "type": "text"}
        r = run(["base", "+field-create", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--json", _json.dumps(body, ensure_ascii=False)], expect_ok=False)
        if r and r.get("ok"):
            print(f"  ➕ 新建字段「{name}」({ftype})")
        else:
            print(f"  ⚠ 字段「{name}」创建失败: {_json.dumps(r, ensure_ascii=False)[:200] if r else 'no resp'}")


def main():
    dry = "--dry-run" in sys.argv
    profile = get_active_profile()
    if not profile:
        print("⚠ 未配置产品画像(product_profiles.json 的 active)，无法评分。")
        return

    if not dry:
        ensure_score_fields()

    print(f"[读取] 网红视频表（{VIDEO_TID}）…")
    videos_raw = list_all(VIDEO_TID)
    vmap = {}
    for rec in videos_raw:
        f = rec.get("fields", {})
        cid = str(f.get("Channel ID", "") or "")
        if not cid:
            continue
        vmap.setdefault(cid, []).append({
            "title": f.get("Video Title", ""),
            "tags": f.get("Tags", ""),
            "description": f.get("字幕内容", ""),
            "view_count": _num(f.get("Views")),
            "engagement_rate": _num(f.get("互动率(%)")),
        })
    print(f"  📹 视频记录 {len(videos_raw)} 条，覆盖频道 {len(vmap)} 个")

    print(f"[读取] 网红详情表（{DETAIL_TID}）…")
    details = list_all(DETAIL_TID)
    print(f"  👤 网红记录 {len(details)} 个")

    updated = 0
    skipped = 0
    failed = 0
    tier_counts = {}
    for idx, rec in enumerate(details, 1):
        f = rec.get("fields", {})
        cid = str(f.get("Channel ID", "") or "")

        # 断点续传：已填好「开发优先级」的跳过
        existing_tier = _str(f.get("开发优先级", ""))
        if existing_tier in ("S", "A", "B", "C"):
            skipped += 1
            continue

        detail = {
            "amazon_promo_level": _str(f.get("亚马逊推广经验", "")),
            "email_status": _str(f.get("邮箱状态", "")),
            "rep_video_engagement": _num(f.get("代表视频互动率")),
            "country": _str(f.get("国家/地区", "")),
            "activity_status": _str(f.get("断更评估", "")),
            "channel_description": _str(f.get("频道描述", "")),
            "rep_video_title": _str(f.get("代表视频标题", "")),
        }
        videos = vmap.get(cid, [])
        scored = score_influencer(detail, videos, profile)
        tier_counts[scored["dev_priority"]] = tier_counts.get(scored["dev_priority"], 0) + 1

        if dry:
            if updated < 8:
                print("  ·", json.dumps({"cid": cid, **scored}, ensure_ascii=False))
            updated += 1
            continue

        fields = {
            "匹配产品": scored["match_profile"],
            "品牌匹配度": scored["brand_fit_score"],
            "内容契合类型": scored["content_types"],
            "匹配关键词": scored["matched_keywords"],
            "开发优先级": scored["dev_priority"],
            "推荐理由": scored["recommend_reason"],
        }
        # 重试 3 次，单条失败不中断整体
        ok = False
        for attempt in range(3):
            try:
                r = run_long(["base", "+record-upsert", "--as", "user", "--base-token", BASE_TOKEN,
                         "--table-id", DETAIL_TID, "--record-id", rec.get("record_id", ""),
                         "--json", json.dumps(fields, ensure_ascii=False)])
                if r and r.get("ok"):
                    ok = True
                    break
                else:
                    time.sleep(1)
            except Exception:
                time.sleep(2)
        if ok:
            updated += 1
        else:
            failed += 1
            print(f"  ✗ [{idx}/{len(details)}] {cid} 写入失败，跳过")

        if idx % 50 == 0:
            print(f"  [{idx}/{len(details)}] 已写 {updated} | 跳过 {skipped} | 失败 {failed}")
        time.sleep(0.12)

    print(f"\n✅ 评分{'预览' if dry else '写回飞书'}完成：写入 {updated} | 跳过(已填) {skipped} | 失败 {failed} | 总计 {len(details)}")
    print("   分层分布:", " / ".join(f"{t}档={tier_counts.get(t,0)}" for t in ("S", "A", "B", "C")))


if __name__ == "__main__":
    main()
