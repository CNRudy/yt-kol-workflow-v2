#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「WEMOH推车 MCB」5 表 Base 全量备份到本地(JSON + xlsx)。

用法:
  ./.venv/bin/python backup_mcb_local.py
输出:
  output/backup/<表名>_备份_<YYYYMMDD_HHMMSS>.{json,xlsx}
"""
import os, json, time, subprocess, sys
from datetime import datetime

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
PROJ = os.path.dirname(os.path.abspath(__file__))                          # .../母婴类/WEMOH推车 MCB/scripts
PRODUCT = os.path.dirname(PROJ)                                            # .../母婴类/WEMOH推车 MCB
OUT_DIR = os.path.join(PRODUCT, "output", "backup")

BASE_TOKEN = "Yz9GbeSbfabon9sS1DKcNY25ngm"
TABLES = ["网红详情表", "红人表", "视频数据表", "网红视频表", "搜索任务表"]


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
        print("  ! 非JSON:", raw[:300])
        return None
    if expect_ok and not j.get("ok"):
        print("  ! lark-cli 失败:", json.dumps(j, ensure_ascii=False)[:300])
        return None
    return j


def table_map():
    j = run(["base", "+table-list", "--as", "user", "--base-token", BASE_TOKEN, "--format", "json"])
    return {t["name"]: (t.get("table_id") or t.get("id")) for t in j["data"]["tables"]}


def read_all(tid):
    """读全表 → list[dict],保留字段名"""
    out, offset = [], 0
    while True:
        j = run(["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--offset", str(offset), "--limit", "200", "--format", "json"])
        if not j:
            break
        d = j.get("data", {})
        fields, rows = d.get("fields", []), d.get("data", [])
        if not rows:
            break
        for r in rows:
            out.append(dict(zip(fields, r)))
        if len(rows) < 200:
            break
        offset += 200
        time.sleep(0.2)
    return out


def to_xlsx(recs, path):
    try:
        from openpyxl import Workbook
    except Exception as e:
        print(f"  (跳过 xlsx: openpyxl 不可用 {e})")
        return False
    if not recs:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    cols = list(recs[0].keys())
    ws.append(cols)
    for r in recs:
        row = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            row.append(v)
        ws.append(row)
    wb.save(path)
    return True


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmap = table_map()
    print(f"[备份] Base {BASE_TOKEN} → {OUT_DIR}")
    summary = {}
    for name in TABLES:
        tid = tmap.get(name)
        if not tid:
            print(f"  ! 找不到表 {name}")
            continue
        recs = read_all(tid)
        base = os.path.join(OUT_DIR, f"{name}_备份_{ts}")
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump({"table": name, "table_id": tid, "base_token": BASE_TOKEN,
                       "exported_at": ts, "count": len(recs), "records": recs},
                      f, ensure_ascii=False, indent=2)
        ok_x = to_xlsx(recs, base + ".xlsx")
        summary[name] = len(recs)
        print(f"  ✓ {name:<8} {len(recs):>4} 行 → {os.path.basename(base)}.json"
              + (f" + .xlsx" if ok_x else ""))
    with open(os.path.join(OUT_DIR, f"_manifest_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump({"base_token": BASE_TOKEN, "base_name": "WEMOH推车 MCB",
                   "exported_at": ts, "counts": summary}, f, ensure_ascii=False, indent=2)
    print(f"\n🎉 备份完成,共 {sum(summary.values())} 行 / {len(summary)} 表\n   {OUT_DIR}")


if __name__ == "__main__":
    main()
