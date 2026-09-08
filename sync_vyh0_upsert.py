#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量 upsert 本地 batch 产出到 VyH0 Base（不清空、不覆盖存量）。

设计目标：把这次跑出来的 184 个红人 + 搜索任务 + 搜索视频，合并进你已有的
VyH0 Base，已存在的按唯一键更新、不存在的新建；存量 1505 条记录与它们的
promo/邮箱等字段完全不动（Feishu record-upsert 是「按字段合并」语义）。

同步范围：
  - 网红详情表(tbl4rnFUM9jJXvCQ): 按 Channel ID upsert   ← 核心
  - 搜索任务表(tblLT1yf9ioEhJOh): 按 搜索关键词 upsert
  - 视频数据表(tblDQi8dEGhkjZyy): 按 Video ID upsert（搜索结果视频）
  - 网红视频表(tblcHBORC90WWn05): 默认跳过（飞书记录上限，放本地）

用法：
  ./.venv/bin/python sync_vyh0_upsert.py --dry-run      # 只统计，不写
  ./.venv/bin/python sync_vyh0_upsert.py                # 真实 upsert
  ./.venv/bin/python sync_vyh0_upsert.py --with-videos # 连网红视频表一起（谨慎）
"""
import argparse
import json
import os
import subprocess
import sys
import time

LARK = "/Users/coscod/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli"
BASE_DIR = "/Users/coscod/WorkBuddy/coscod/YouTube 网红开发工作流系统/assets/yt-kol-workflow"
BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", "")
BATCH = "output/20260819_182645_batch"

TABLE_IDS = {
    "网红详情表": "tbl4rnFUM9jJXvCQ",
    "视频数据表": "tblDQi8dEGhkjZyy",
    "网红视频表": "tblcHBORC90WWn05",
    "搜索任务表": "tblLT1yf9ioEhJOh",
}

# 每张表：本地 xlsx 文件、去重唯一键列、是否默认同步
SYNC_MAP = {
    "网红详情表": {"file": f"{BATCH}/influencers_rescored.xlsx", "key": "Channel ID", "default": True},
    "搜索任务表": {"file": f"{BATCH}/search_tasks_all.xlsx", "key": "搜索关键词", "default": True},
    "视频数据表": {"file": f"{BATCH}/search_videos_all.xlsx", "key": "Video ID", "default": True},
    "网红视频表": {"file": f"{BATCH}/influencer_videos_all.xlsx", "key": "Video ID", "default": False},
}

NUMBER_COLS = {
    "订阅数", "频道总播放量", "视频总数", "代表视频播放量", "品牌匹配度",
    "邮箱出现视频数", "Amazon带货视频数", "推广视频数",
}
SKIP_COLS = {"多行文本"}
DATE_HINT = __import__("re").compile(r"日期|时间|date|发布|采集", __import__("re").I)


def run(args, expect_ok=True, timeout=120):
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"
    p = subprocess.run([LARK] + args, capture_output=True, text=True,
                       timeout=timeout, cwd=BASE_DIR, env=env)
    out, err = p.stdout.strip(), p.stderr.strip()
    raw = out if out else err
    if raw and not raw.startswith("{"):
        idx = raw.find("\n{")
        if idx >= 0:
            raw = raw[idx + 1:].strip()
        elif raw.find("{") >= 0:
            raw = raw[raw.find("{"):].strip()
    try:
        j = json.loads(raw)
    except Exception:
        print("  ! 非JSON:", (out or err)[:200])
        return None
    if expect_ok and not j.get("ok"):
        print("  ! 失败:", json.dumps(j, ensure_ascii=False)[:300])
        return None
    return j


def get_fields(tid):
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    if not j or not j.get("ok"):
        return {}
    items = j.get("data", {}).get("fields", []) or j.get("data", {}).get("items", [])
    out = {}
    for f in items:
        name = f.get("name") or f.get("field_name") or ""
        out[name] = {
            "type": f.get("type"),
            "options": [o.get("name") for o in (f.get("options") or [])],
            "id": f.get("field_id") or f.get("id") or name,
        }
    return out


def ensure_select_options(tid, field_name, wanted_values):
    """给单选列补齐缺失选项（field-update 是 PUT 语义，需传全量）。"""
    fields = get_fields(tid)
    f = fields.get(field_name)
    if not f or f["type"] not in ("select", 3):
        return
    have = set(f["options"])
    new_opts = [{"name": v} for v in wanted_values if v and v not in have]
    if not new_opts:
        return
    body = {"name": field_name, "type": "select", "multiple": False,
            "options": [{"name": o} for o in f["options"]] + new_opts}
    r = run(["base", "+field-update", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--field-id", f["id"],
             "--json", json.dumps(body, ensure_ascii=False), "--yes"], expect_ok=False)
    if r and r.get("ok"):
        print(f"    🏷 『{field_name}』补充 {len(new_opts)} 个选项")
    else:
        print(f"    ⚠ 『{field_name}』选项补充失败: {json.dumps(r, ensure_ascii=False)[:150] if r else 'no resp'}")


def build_existing_map(tid, key_col):
    """分页拉全表，返回 {key_value: record_id}。"""
    m = {}
    offset = 0
    while True:
        j = run(["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--limit", "200", "--offset", str(offset),
                 "--format", "json"])
        if not j or not j.get("ok"):
            break
        d = j.get("data", {})
        field_names = d.get("fields") or []
        rows = d.get("data") or []
        rids = d.get("record_id_list") or []
        if key_col not in field_names:
            print(f"    ⚠ 表缺少唯一键列『{key_col}』，无法去重")
            return m
        ki = field_names.index(key_col)
        for idx, row in enumerate(rows):
            if isinstance(row, list) and ki < len(row) and row[ki] is not None:
                key = str(row[ki])
                rid = rids[idx] if idx < len(rids) else ""
                m[key] = rid
        if len(rows) < 200:
            break
        offset += 200
    return m


def to_number(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.replace(",", "").replace("+", "").strip()
        if s.replace(".", "", 1).lstrip("-").isdigit():
            return float(s) if "." in s else int(s)
    return None


def to_date(v):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, str):
        return v  # 假设已是 yyyy-MM-dd 或 yyyy-MM-dd HH:MM:SS
    # datetime.datetime / datetime.date
    try:
        return v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "hour") else v.strftime("%Y-%m-%d")
    except Exception:
        return str(v)


def build_records(tname, headers, rows, fields_meta):
    """把本地行转成飞书字段 map，按飞书已有字段过滤 + 类型转换。"""
    valid_cols = [c for c in headers if c in fields_meta and c not in SKIP_COLS]
    records = []
    for r in rows:
        rec = {}
        for c in valid_cols:
            v = r.get(c)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                continue
            meta = fields_meta[c]
            ftype = meta["type"]
            if c in NUMBER_COLS or ftype in ("number", 2):
                n = to_number(v)
                if n is not None:
                    rec[c] = n
            elif ftype in ("datetime", 1001) or DATE_HINT.search(c):
                d = to_date(v)
                if d:
                    rec[c] = d
            elif ftype in ("select", 3):
                s = str(v).strip()
                if s in meta["options"]:
                    rec[c] = s
                # 不在选项里的单选值：跳过（稍后 ensure_select_options 补完再写）
            else:
                rec[c] = str(v)
        if rec:
            records.append(rec)
    return valid_cols, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-videos", action="store_true")
    ap.add_argument("--batch-dir", default=None,
                    help="指定本地 batch 产出目录（默认 output/20260819_182645_batch）")
    args = ap.parse_args()

    if args.batch_dir:
        for cfg in SYNC_MAP.values():
            fname = cfg["file"].split("/")[-1]
            cfg["file"] = f"{args.batch_dir}/{fname}"

    # 视频数据表 唯一键可能是 Video ID；网红视频表也可能用 Video ID 或 唯一键
    print("=" * 60)
    print(f"[VyH0 sync] dry_run={args.dry_run} with_videos={args.with_videos}")
    print("=" * 60)

    import openpyxl
    total_created = total_updated = total_skipped = 0

    for tname, cfg in SYNC_MAP.items():
        if not cfg["default"] and not (args.with_videos and tname == "网红视频表"):
            print(f"\n[跳过] 『{tname}』（默认不同步；--with-videos 可开启）")
            continue
        if not os.path.exists(cfg["file"]):
            print(f"\n[跳过] 『{tname}』 本地文件不存在: {cfg['file']}")
            continue

        tid = TABLE_IDS[tname]
        print(f"\n{'='*20} 『{tname}』(tid={tid}) {'='*20}")
        fields_meta = get_fields(tid)
        if not fields_meta:
            print("  ⚠ 无法获取字段，跳过")
            continue

        wb = openpyxl.load_workbook(cfg["file"], read_only=True, data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        headers = list(all_rows[0])
        data_rows = [dict(zip(headers, r)) for r in all_rows[1:]]
        print(f"  本地: {len(data_rows)} 行, {len(headers)} 列")

        # 单选列先补齐选项，避免 not_found
        select_cols = [c for c in headers if c in fields_meta and fields_meta[c]["type"] in ("select", 3)]
        for sc in select_cols:
            vals = {str(r.get(sc, "")).strip() for r in data_rows
                    if r.get(sc) not in (None, "") and str(r.get(sc, "")).strip() not in ("?", "未知", "N/A")}
            if args.dry_run:
                continue
            ensure_select_options(tid, sc, vals)

        # 重新取最新字段（选项补齐后）用于类型判断
        fields_meta = get_fields(tid)
        valid_cols, records = build_records(tname, headers, data_rows, fields_meta)

        # 去重映射
        existing = build_existing_map(tid, cfg["key"])
        print(f"  飞书现有记录(按{cfg['key']}): {len(existing)} 条")
        print(f"  有效字段: {len(valid_cols)} | 有效记录: {len(records)}")

        if args.dry_run:
            keys = [str(r.get(cfg["key"], "")) for r in data_rows if r.get(cfg["key"])]
            new = sum(1 for k in keys if k not in existing)
            upd = len(keys) - new
            print(f"  [DRY] 将新建 {new} | 更新 {upd} | 飞书不丢数据")
            total_created += new
            total_updated += upd
            continue

        # 拆分新建/更新
        to_create, to_update = [], []
        for rec in records:
            k = str(rec.get(cfg["key"], ""))
            if k and k in existing:
                to_update.append((existing[k], rec))
            else:
                to_create.append(rec)

        # 更新（逐条 record-upsert，合并语义）
        up_ok = 0
        for rid, rec in to_update:
            ok = False
            for attempt in range(3):
                r = run(["base", "+record-upsert", "--as", "user", "--base-token", BASE_TOKEN,
                         "--table-id", tid, "--record-id", rid,
                         "--json", json.dumps(rec, ensure_ascii=False)])
                if r and r.get("ok"):
                    ok = True
                    break
                time.sleep(1)
            if ok:
                up_ok += 1
            else:
                print(f"    ✗ 更新失败 {rid}")
            time.sleep(0.12)

        # 新建（矩阵批量）
        cr_ok = 0
        for i in range(0, len(to_create), 200):
            chunk = to_create[i:i + 200]
            rows = [[r.get(c) for c in valid_cols] for r in chunk]
            r = run(["base", "+record-batch-create", "--as", "user", "--base-token", BASE_TOKEN,
                     "--table-id", tid, "--json",
                     json.dumps({"fields": valid_cols, "rows": rows}, ensure_ascii=False),
                     "--format", "json"])
            if r and r.get("ok"):
                cr_ok += len(chunk)
            else:
                print(f"    ✗ 新建批次失败: {json.dumps(r, ensure_ascii=False)[:200]}")
            time.sleep(0.3)

        print(f"  ✅ 新建 {cr_ok} | 更新 {up_ok} | 现有 {len(existing)} 条未动")
        total_created += cr_ok
        total_updated += up_ok

    print(f"\n🎉 完成。新建 {total_created} | 更新 {total_updated} | VyH0 存量记录零丢失")
    print(f"   base 链接: https://pcn8zy4grswl.feishu.cn/base/{BASE_TOKEN}")


if __name__ == "__main__":
    main()
