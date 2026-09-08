#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「WEMOH推车 MCB」数据并入主库 VyH0(按用户 2026-09-07 决策)。

策略(用户拍板):
  - 网红详情表: 只新增主库没有的 53 个 CID(148 个已存在的一律跳过,零覆盖)
  - 视频数据表: 766 条全部新增(已按 频道+标题 校验 0 重复)
  - 搜索任务表: 10 个关键词全部新增
  - 网红视频表: 不进飞书(主库 22129 行已触上限),并入本地 local_cache/channel_videos.json
用法:
  ./.venv/bin/python merge_mcb_to_main.py            # dry-run,只报告
  ./.venv/bin/python merge_mcb_to_main.py --apply    # 真正写入
"""
import os, json, glob, time, subprocess, sys
from datetime import datetime

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
PROJ = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(PROJ)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PROJ)))   # .../yt-kol-workflow
BACKUP = os.path.join(PRODUCT, "output", "backup")
LOCAL_CACHE = os.path.join(ROOT, "local_cache")

VYH0 = "VyH0bJ1WBaCKIWsn9gVcBVc4nmc"
T_DETAIL = "tbl4rnFUM9jJXvCQ"
T_VIDEO = "tblDQi8dEGhkjZyy"
T_TASK = "tblLT1yf9ioEhJOh"
CHVID_LOCAL = os.path.join(LOCAL_CACHE, "channel_videos.json")

APPLY = "--apply" in sys.argv


def run(args, expect_ok=True):
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"
    p = subprocess.run([LARK] + args, capture_output=True, text=True, timeout=300, env=env)
    raw = (p.stdout or "").strip() or (p.stderr or "").strip()
    if raw and not raw.startswith("{"):
        i = raw.find("{")
        raw = raw[i:] if i >= 0 else raw
    try:
        j = json.loads(raw)
    except Exception:
        print("  ! 非JSON:", raw[:200])
        return None
    if expect_ok and not j.get("ok"):
        print("  ! lark-cli 失败:", json.dumps(j, ensure_ascii=False)[:300])
        return None
    return j


def scalar(x):
    if isinstance(x, list):
        return x[0] if x else None
    return x


def fmt_dt(v):
    if not v:
        return None
    s = str(v)
    if "T" in s:
        s = s.replace("T", " ")
        if "." in s:
            s = s.split(".")[0]
    s = s.strip()
    if len(s) == 10:
        s += " 00:00:00"
    return s[:19]


def fields_of(tid):
    j = run(["base", "+field-list", "--as", "user", "--base-token", VYH0,
             "--table-id", tid, "--format", "json"])
    out = {}
    for f in j["data"]["fields"]:
        out[f["name"]] = {
            "type": f.get("type"),
            "multiple": f.get("multiple", False),
            "opts": [o["name"] for o in f.get("options", []) if o.get("name")] if f.get("type") == "select" else None,
        }
    return out


def read_main(tid):
    out, off = [], 0
    while True:
        j = run(["base", "+record-list", "--as", "user", "--base-token", VYH0,
                 "--table-id", tid, "--offset", str(off), "--limit", "200", "--format", "json"])
        if not j or not j.get("ok"):
            break
        d = j["data"]
        f, rows = d.get("fields", []), d.get("data", [])
        if not rows:
            break
        for r in rows:
            out.append(dict(zip(f, r)))
        if len(rows) < 200:
            break
        off += 200
        time.sleep(0.15)
    return out


def latest_backup(name):
    fs = sorted(glob.glob(os.path.join(BACKUP, f"{name}_备份_*.json")))
    return json.load(open(fs[-1], encoding="utf-8"))["records"] if fs else []


def ensure_options(tid, tfields, col, needed):
    """补齐主库 select 字段缺失的选项(全量 PUT,需 --yes)。"""
    meta = tfields.get(col)
    if not meta or meta["type"] != "select":
        return
    have = set(meta["opts"] or [])
    missing = sorted(set(needed) - have)
    if not missing:
        print(f"  ✓ {col} 选项齐全")
        return
    print(f"  ⚠️ {col} 缺 {len(missing)} 个选项 → 补: {missing}")
    if not APPLY:
        print("     (dry-run,未写)")
        return
    new_opts = [{"name": o} for o in (meta["opts"] or [])] + [{"name": m} for m in missing]
    spec = {"name": col, "type": "select", "multiple": meta.get("multiple", False),
            "options": new_opts}
    tmp = os.path.join(ROOT, "._opt_tmp.json")
    json.dump(spec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    j = run(["base", "+field-update", "--as", "user", "--base-token", VYH0,
             "--table-id", tid, "--json", "@._opt_tmp.json", "--yes", "--format", "json"],
            expect_ok=False)
    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"     {'✅ 已补' if j and j.get('ok') else '✗ 失败: ' + json.dumps(j, ensure_ascii=False)[:200]}")
    if j and j.get("ok"):
        tfields[col]["opts"] = [o["name"] for o in new_opts]


def build_rows(recs, tfields, drop=()):
    """按目标 schema 规整记录: 丢字段 / _scalar / select 校验 / datetime 格式化"""
    cols = [c for c in tfields if c not in drop]
    rows = []
    for r in recs:
        row = []
        for c in cols:
            v = scalar(r.get(c))
            if c not in r:
                v = None
            t = tfields[c]["type"]
            if t == "datetime":
                v = fmt_dt(v)
            elif t == "select" and v:
                if str(v) not in (tfields[c]["opts"] or []):
                    v = None          # 越界置空,避免整批失败
            elif t in ("number",) and v not in (None, ""):
                try:
                    v = float(v) if "." in str(v) else int(v)
                except Exception:
                    v = None
            row.append(v)
        rows.append(row)
    return cols, rows


def batch_insert(tid, cols, rows, label):
    print(f"\n[写入] {label}: {len(rows)} 行 / {len(cols)} 列")
    if not APPLY:
        print("  (dry-run,未写)")
        return 0, 0
    ok = fail = 0
    tmp = os.path.join(ROOT, "._merge_tmp.json")
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        json.dump({"fields": cols, "rows": chunk}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        j = run(["base", "+record-batch-create", "--as", "user", "--base-token", VYH0,
                 "--table-id", tid, "--json", "@._merge_tmp.json", "--format", "json"],
                expect_ok=False)
        if j and j.get("ok"):
            ok += len(chunk)
        else:
            fail += len(chunk)
            print(f"  ✗ 批次{i//200} 失败: {json.dumps(j, ensure_ascii=False)[:300]}")
        time.sleep(0.3)
    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"  ✅ 成功 {ok} / 失败 {fail}")
    return ok, fail


def main():
    print(f"{'[APPLY 真实写入]' if APPLY else '[DRY-RUN 仅报告]'}")
    w_detail = latest_backup("网红详情表")
    w_video = latest_backup("视频数据表")
    w_task = latest_backup("搜索任务表")
    w_chvid = latest_backup("网红视频表")
    print(f"Wagon 备份: 详情 {len(w_detail)} / 视频 {len(w_video)} / 任务 {len(w_task)} / 网红视频 {len(w_chvid)}")

    # ---------- 1. 网红详情表:只新增 53 ----------
    print("\n=== 1. 网红详情表 (只新增,跳过已存在) ===")
    tf_d = fields_of(T_DETAIL)
    m_detail = read_main(T_DETAIL)
    m_cids = {scalar(r.get("Channel ID")) for r in m_detail if scalar(r.get("Channel ID"))}
    new_recs = [r for r in w_detail if scalar(r.get("Channel ID")) not in m_cids]
    print(f"  主库现有 {len(m_cids)} CID;Wagon {len(w_detail)} → 新增 {len(new_recs)},跳过 {len(w_detail)-len(new_recs)}")
    kw_needed = [scalar(r.get("来源关键词")) for r in new_recs if scalar(r.get("来源关键词"))]
    ensure_options(T_DETAIL, tf_d, "来源关键词", kw_needed)
    cols, rows = build_rows(new_recs, tf_d, drop=("分类", "数据来源"))
    batch_insert(T_DETAIL, cols, rows, "网红详情表")

    # ---------- 2. 视频数据表:766 全新增 ----------
    print("\n=== 2. 视频数据表 ===")
    tf_v = fields_of(T_VIDEO)
    cols, rows = build_rows(w_video, tf_v)
    batch_insert(T_VIDEO, cols, rows, "视频数据表")

    # ---------- 3. 搜索任务表:10 全新增 ----------
    print("\n=== 3. 搜索任务表 ===")
    tf_t = fields_of(T_TASK)
    m_task = read_main(T_TASK)
    m_kw = {scalar(r.get("搜索关键词")) for r in m_task if scalar(r.get("搜索关键词"))}
    new_task = [r for r in w_task if scalar(r.get("搜索关键词")) not in m_kw]
    print(f"  主库 {len(m_kw)} 关键词;Wagon {len(w_task)} → 新增 {len(new_task)}")
    cols, rows = build_rows(new_task, tf_t)
    batch_insert(T_TASK, cols, rows, "搜索任务表")

    # ---------- 4. 网红视频表 → 本地 channel_videos.json ----------
    print("\n=== 4. 网红视频表 → 本地(不进飞书) ===")
    d = json.load(open(CHVID_LOCAL, encoding="utf-8"))
    recs = d["records"]
    exist = {scalar(r.get("fields", {}).get("唯一键")) for r in recs}
    to_add = [r for r in w_chvid if scalar(r.get("唯一键")) not in exist]
    print(f"  本地现有 {len(recs)} 条;Wagon {len(w_chvid)} → 新增 {len(to_add)},跳过重复 {len(w_chvid)-len(to_add)}")
    if APPLY and to_add:
        for r in to_add:
            recs.append({"record_id": "", "fields": {k: scalar(v) for k, v in r.items()}})
        d["records"] = recs
        d.setdefault("meta", {})["merged_mcb_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        d["meta"]["total_records"] = len(recs)
        json.dump(d, open(CHVID_LOCAL, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  ✅ 已并入本地 {len(to_add)} 条 → {CHVID_LOCAL}(共 {len(recs)})")
    elif not APPLY:
        print("  (dry-run,未写)")

    print("\n🎉 完成" + ("" if APPLY else " (dry-run,加 --apply 真正写入)"))


if __name__ == "__main__":
    main()
