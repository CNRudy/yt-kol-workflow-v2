#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新建/填充「母婴类/WEMOH推车 MCB」5 表 Base(类目归类在云盘 母婴类/ 文件夹下,Base 用产品名,表名不带前缀)。
接棒 HFBkb:复用其详情/红人表 schema + 主库 VyH0 视频/网红视频/搜索任务表 schema。
用法:
  ./.venv/bin/python publish_wagon_5table.py                 # 建 Base + 5 表(空)
  ./.venv/bin/python publish_wagon_5table.py --fill          # 建库并填充 5 表
  ./.venv/bin/python publish_wagon_5table.py --fill --base-token <TOKEN>  # 复用已有 Base 填充(不重建)
"""
import os, json, time, sys, subprocess
from collections import Counter

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
PROJ = os.path.dirname(os.path.abspath(__file__))                          # .../母婴类/WEMOH推车 MCB/scripts
CATALOG = os.path.dirname(PROJ)                                             # .../母婴类/WEMOH推车 MCB
ROOT = os.path.dirname(os.path.dirname(CATALOG))                            # .../yt-kol-workflow

# 数据源一律走类目目录（data/ scripts/），不引用根目录，避免改一处漏一处
VIDEO_CACHE = os.path.join(CATALOG, "data", "stroller_fetched_videos_20260904.json")
KEYWORDS_FILE = os.path.join(PROJ, "keywords_stroller_wagon.txt")

# schema / 数据源：统一指向「WEMOH推车 MCB」自身（Yz9GbeS）做自举。
# 原源库 HFBkb 已于 2026-09-07 被删除（note has been deleted），勿再引用。
HFBKB = "Yz9GbeSbfabon9sS1DKcNY25ngm"
TBL_DETAIL_SRC = "tblzPpg6Uj197wND"
TBL_RED_SRC = "tblIDj93S485Yn00"
VYH0 = "VyH0bJ1WBaCKIWsn9gVcBVc4nmc"
TBL_VID_SRC = "tblDQi8dEGhkjZyy"
TBL_CHVID_SRC = "tblcHBORC90WWn05"
TBL_TASK_SRC = "tblLT1yf9ioEhJOh"

BASE_NAME = "WEMOH推车 MCB"
TBL_DETAIL = "网红详情表"
TBL_RED = "红人表"
TBL_VID = "视频数据表"
TBL_CHVID = "网红视频表"
TBL_TASK = "搜索任务表"

# 路径常量见文件顶部 (VIDEO_CACHE / KEYWORDS_FILE)，勿在此重复定义
WAGON_KW = ["stroller", "wagon", "wonderfold", "veer", "momfann", "baby gear",
            "toddler", "推车", "婴儿车", "double stroller", "crib", "bassinet",
            "baby", "kids", "parent", "carrier", "diaper", "babybjorn", "ergobaby",
            "bugaboo", "britax", "evenflo", "stokke", "nuna", "chicco", "graco",
            "car seat", "playpen", "high chair", "baby trend"]


def run(args, expect_ok=True):
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"
    p = subprocess.run([LARK] + args, capture_output=True, text=True, timeout=180, cwd=ROOT, env=env)
    raw = p.stdout.strip() or p.stderr.strip()
    if raw and not raw.startswith("{"):
        i = raw.find("{"); raw = raw[i:] if i >= 0 else raw
    try:
        j = json.loads(raw)
    except Exception:
        print("  ! 非JSON:", raw[:300], p.stderr[:200]); return None
    if expect_ok and not j.get("ok"):
        print("  ! lark-cli 失败:", json.dumps(j, ensure_ascii=False)[:400]); return None
    return j


def get_fields(base, table):
    j = run(["base", "+field-list", "--as", "user", "--base-token", base,
             "--table-id", table, "--format", "json"])
    if not j:
        return None
    fields = []
    for f in j["data"]["fields"]:
        t = f.get("type", "text")
        spec = {"name": f["name"], "type": t}
        if t == "select":
            opts = [{"name": o["name"]} for o in f.get("options", []) if o.get("name")]
            spec["options"] = opts
        fields.append(spec)
    return fields


def create_base_first(name, first_table, fields):
    return run(["base", "+base-create", "--as", "user", "--name", name,
                "--table-name", first_table, "--fields", json.dumps(fields, ensure_ascii=False),
                "--format", "json"])


def create_table(base, name, fields):
    return run(["base", "+table-create", "--as", "user", "--base-token", base,
                "--name", name, "--fields", json.dumps(fields, ensure_ascii=False),
                "--format", "json"], expect_ok=False)


def find_table_id(j, name):
    if not j or not j.get("ok"):
        return None
    d = j.get("data", {})
    if "table" in d and d["table"].get("name") == name:
        return d["table"].get("table_id") or d["table"].get("id")
    t = d.get("table")
    if isinstance(t, dict):
        return t.get("table_id") or t.get("id")
    return d.get("table_id") or d.get("id")


def read_records(base, table):
    out = []
    offset = 0
    while True:
        j = run(["base", "+record-list", "--as", "user", "--base-token", base,
                 "--table-id", table, "--offset", str(offset), "--limit", "200",
                 "--format", "json"])
        if not j or not j.get("ok"):
            break
        d = j["data"]
        fields = d.get("fields", [])
        data = d.get("data", [])
        if not data:
            break
        for r in data:
            out.append(dict(zip(fields, r)))
        if len(data) < 200:
            break
        offset += 200
        time.sleep(0.3)
    return out


def batch_write(base, table, fields, records, pause=0.3):
    ok = fail = 0
    tmp = os.path.join(ROOT, "._wagon_tmp.json")
    names = [f["name"] for f in fields]
    dt_names = {f["name"] for f in fields if f.get("type") == "datetime"}
    for i in range(0, len(records), 200):
        chunk = records[i:i + 200]
        rows = []
        for r in chunk:
            row = []
            for c in names:
                v = r.get(c)
                if c in dt_names and isinstance(v, str) and len(v) == 10:
                    v = v + " 00:00:00"
                row.append(v)
            rows.append(row)
        body = {"fields": names, "rows": rows}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        j = run(["base", "+record-batch-create", "--as", "user", "--base-token", base,
                 "--table-id", table, "--json", "@._wagon_tmp.json", "--format", "json"],
                expect_ok=False)
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
        if j and j.get("ok"):
            ok += len(chunk)
        else:
            fail += len(chunk)
            print(f"  ✗ 批次失败({names[0]}): {json.dumps(j, ensure_ascii=False)[:300]}")
        if pause: time.sleep(pause)
    return ok, fail


def _scalar(x):
    if isinstance(x, list):
        return x[0] if x else ""
    return x or ""


def build_videos(detail_recs):
    d = json.load(open(VIDEO_CACHE, encoding="utf-8"))
    cid_name = {r.get("Channel ID"): r.get("Channel Name", "") for r in detail_recs}
    cid_src = {r.get("Channel ID"): _scalar(r.get("来源关键词")) for r in detail_recs}
    vid_rows, chvid_rows = [], []
    seen = set()
    for cid, vids in d.items():
        for v in vids:
            blob = " ".join(str(v.get(k, "")) for k in ("title", "tags", "description")).lower()
            if not any(k in blob for k in WAGON_KW):
                continue
            title = v.get("title", "") or ""
            key = f"{cid}_{abs(hash(title))}"   # 缓存视频无 video_id，用 cid+标题去重
            if key in seen:
                continue
            seen.add(key)
            src = cid_src.get(cid, "")
            base = {
                "Video Title": title,
                "Views": _num(v.get("view_count")),
                "互动率(%)": _num(v.get("engagement_rate")),
                "Publish Time": (v.get("published_at", "") or "")[:10],
                "Channel ID": cid,
                "视频URL": "",
                "Channel Name": cid_name.get(cid, ""),
                "Video ID": "",
                "Likes": _num(v.get("likes")),
                "Comments": _num(v.get("comments")),
                "描述": (v.get("description", "") or "")[:2000],
                "Tags": (v.get("tags", "") or "")[:500],
                "唯一键": key,
                "搜索关键词": src,
                "是否通过筛选": "通过",
                "Has Subtitles": "",
                "筛选原因": "wagon/baby 相关",
            }
            vid_rows.append({**base, "Duration (H:M:S)": "", "Duration (sec)": ""})
            chvid_rows.append({
                "Video ID": "", "Duration (sec)": "", "Tags": (v.get("tags", "") or "")[:500],
                "Duration (H:M:S)": "", "Video Title": title,
                "Channel Name": cid_name.get(cid, ""), "Publish Time": (v.get("published_at", "") or "")[:10],
                "Comments": _num(v.get("comments")), "互动率(%)": _num(v.get("engagement_rate")),
                "Likes": _num(v.get("likes")), "视频URL": "",
                "唯一键": key, "Channel ID": cid, "Views": _num(v.get("view_count")),
            })
    print(f"  视频数据表(过滤后): {len(vid_rows)} 条")
    return vid_rows, chvid_rows


def _num(x):
    try:
        if x is None or x == "":
            return None
        if isinstance(x, (int, float)):
            return x
        s = str(x).replace(",", "").strip()
        return float(s) if ("." in s) else int(s)
    except Exception:
        return None


def build_tasks(detail_recs):
    kws = [l.strip() for l in open(KEYWORDS_FILE, encoding="utf-8") if l.strip()]
    def _scalar(x):
        if isinstance(x, list):
            return x[0] if x else None
        return x
    cnt = Counter(_scalar(r.get("来源关键词")) for r in detail_recs if _scalar(r.get("来源关键词")))
    rows = []
    for kw in kws:
        n = cnt.get(kw, 0)
        rows.append({
            "地区": "US", "搜索关键词": kw, "配额消耗": "", "新增网红数": n,
            "执行状态": "已完成", "搜索结果数": 30, "排序策略": "relevance",
            "搜索时间": "2026-09-04", "筛选通过数": n, "独立频道数": n, "唯一键": kw,
        })
    print(f"  搜索任务表: {len(rows)} 行")
    return rows


def fill_if_empty(base, table_id, fields, records, label):
    existing = read_records(base, table_id)
    if existing:
        print(f"  ⏭ {label} 已有 {len(existing)} 行，跳过")
        return 0, 0
    return batch_write(base, table_id, fields, records)


def main():
    fill = "--fill" in sys.argv
    bt = None
    if "--base-token" in sys.argv:
        bt = sys.argv[sys.argv.index("--base-token") + 1]
    print(f"[目标] Base: {BASE_NAME} / 5 表")
    if not fill:
        print("(仅建库建表，加 --fill 填充)")
    elif not bt:
        print("(建库并填充)")

    print("读源表 schema ...")
    detail_fields = get_fields(HFBKB, TBL_DETAIL_SRC)
    red_fields = get_fields(HFBKB, TBL_RED_SRC)
    vid_fields = get_fields(VYH0, TBL_VID_SRC)
    chvid_fields = get_fields(VYH0, TBL_CHVID_SRC)
    task_fields = get_fields(VYH0, TBL_TASK_SRC)
    for nm, fs in (("详情", detail_fields), ("红人", red_fields), ("视频数据", vid_fields),
                   ("网红视频", chvid_fields), ("搜索任务", task_fields)):
        print(f"  {nm}表 schema: {len(fs)} 字段")

    if bt:
        base_token = bt
        print(f"复用 Base: {base_token}")
        jt = run(["base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])
        tid = {t.get("name"): (t.get("table_id") or t.get("id")) for t in jt["data"]["tables"]}
        tid_detail = tid.get(TBL_DETAIL); tid_red = tid.get(TBL_RED)
        tid_vid = tid.get(TBL_VID); tid_chvid = tid.get(TBL_CHVID); tid_task = tid.get(TBL_TASK)
        print(f"  表ID: 详情={tid_detail} 红人={tid_red} 视频={tid_vid} 网红视频={tid_chvid} 搜索={tid_task}")
    else:
        print("建 Base + 详情表 ...")
        jb = create_base_first(BASE_NAME, TBL_DETAIL, detail_fields)
        if not jb:
            print("建 Base 失败"); sys.exit(1)
        base_token = jb["data"]["base"]["base_token"]
        url = jb["data"]["base"]["url"]
        print(f"  base_token={base_token}\n  url={url}")
        tid_detail = find_table_id(jb, TBL_DETAIL)
        print("建其余 4 表 ...")
        tid_red = find_table_id(create_table(base_token, TBL_RED, red_fields), TBL_RED)
        tid_vid = find_table_id(create_table(base_token, TBL_VID, vid_fields), TBL_VID)
        tid_chvid = find_table_id(create_table(base_token, TBL_CHVID, chvid_fields), TBL_CHVID)
        tid_task = find_table_id(create_table(base_token, TBL_TASK, task_fields), TBL_TASK)
        print(f"  红人={tid_red} 视频={tid_vid} 网红视频={tid_chvid} 搜索={tid_task}")

    if not fill:
        print(f"\n✅ 阶段完成 base_token={base_token}")
        return

    print("\n[填充] 读 HFBkb 数据 ...")
    detail_recs = read_records(HFBKB, TBL_DETAIL_SRC)
    red_recs = read_records(HFBKB, TBL_RED_SRC)
    print(f"  详情表 {len(detail_recs)} 行, 红人表 {len(red_recs)} 行")

    o1, f1 = fill_if_empty(base_token, tid_detail, detail_fields, detail_recs, "详情表")
    o2, f2 = fill_if_empty(base_token, tid_red, red_fields, red_recs, "红人表")
    print(f"  详情表 写入 {o1}(失败{f1}); 红人表 写入 {o2}(失败{f2})")

    vid_rows, chvid_rows = build_videos(detail_recs)
    o3, f3 = fill_if_empty(base_token, tid_vid, vid_fields, vid_rows, "视频数据表")
    o4, f4 = fill_if_empty(base_token, tid_chvid, chvid_fields, chvid_rows, "网红视频表")
    print(f"  视频数据表 写入 {o3}(失败{f3}); 网红视频表 写入 {o4}(失败{f4})")

    task_rows = build_tasks(detail_recs)
    o5, f5 = fill_if_empty(base_token, tid_task, task_fields, task_rows, "搜索任务表")
    print(f"  搜索任务表 写入 {o5}(失败{f5})")

    print(f"\n🎉 完成！Base: https://pcn8zy4grswl.feishu.cn/base/{base_token}")
    print(f"   base_token={base_token}")


if __name__ == "__main__":
    main()
