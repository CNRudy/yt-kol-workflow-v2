#!/usr/bin/env python3
"""
将飞书 4 张表 + 红人表全量导出到 local_cache/ 本地 JSON 缓存。
后续评分/分析脚本优先读本地，避免每次 30+ 次 API 分页调用。

用法:
    python export_local_cache.py              # 全量导出所有表
    python export_local_cache.py influencers  # 只导出网红详情表
    python export_local_cache.py --list       # 列出可用表
"""
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import write_user_base as wb
from write_user_base import BASE_TOKEN, TABLE_IDS, run

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_cache")

# 表名 -> table_id 映射（含红人表）
ALL_TABLES = {
    "influencers":  ("网红详情表", TABLE_IDS["网红详情表"]),
    "videos":       ("视频数据表", TABLE_IDS["视频数据表"]),
    "channel_videos": ("网红视频表", TABLE_IDS["网红视频表"]),
    "search_tasks":   ("搜索任务表", TABLE_IDS["搜索任务表"]),
}
# 红人表 / 足球评分表不在 TABLE_IDS 里，直接用已知 ID
EXTRA_TABLES = {
    "hongren": ("红人表", "tblPKFENcpk8xnZH"),
    "soccer_scores": ("足球红人匹配评分", "tble06acWNOPB6vX"),
}
for k, (name, tid) in EXTRA_TABLES.items():
    ALL_TABLES[k] = (name, tid)


def _str(v):
    """归一化单选字段（飞书返回 list 取第一个）"""
    if isinstance(v, list):
        return v[0] if v else ""
    if v is None:
        return ""
    return str(v)


def export_table(table_key, table_name, table_id, page_size=200):
    """从飞书分页拉取全表数据，保存为 JSON"""
    print(f"  [{table_key}] {table_name} ({table_id}) ...", end=" ", flush=True)
    all_records = []
    offset = 0
    field_names = None

    while True:
        j = run([
            "base", "+record-list", "--as", "user",
            "--base-token", BASE_TOKEN, "--table-id", table_id,
            "--limit", str(page_size), "--offset", str(offset),
            "--format", "json"
        ])
        d = j.get("data", {})
        fns = d.get("fields") or []
        rows = d.get("data") or []
        rids = d.get("record_id_list") or []

        if not rows:
            break
        if field_names is None:
            field_names = fns

        for i, row in enumerate(rows):
            if isinstance(row, list):
                fields = dict(zip(fns, row))
            else:
                fields = row.get("fields", {})
            # 归一化单选字段
            normalized = {}
            for k, v in fields.items():
                if isinstance(v, list) and v and isinstance(v[0], str):
                    normalized[k] = v[0]
                else:
                    normalized[k] = v
            all_records.append({
                "record_id": rids[i] if i < len(rids) else "",
                "fields": normalized,
            })

        if len(rows) < page_size:
            break
        offset += page_size

    out_file = os.path.join(CACHE_DIR, f"{table_key}.json")
    meta = {
        "table_name": table_name,
        "table_id": table_id,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_records": len(all_records),
        "field_names": field_names or [],
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "records": all_records}, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(out_file) / 1024
    print(f"{len(all_records)} 条, {size_kb:.0f}KB")
    return len(all_records)


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    args = sys.argv[1:]
    if "--list" in args:
        print("可用表:")
        for key, (name, tid) in ALL_TABLES.items():
            print(f"  {key:20s}  {name}  ({tid})")
        return

    # 指定表 or 全量
    targets = [a for a in args if not a.startswith("-")]
    if not targets:
        targets = list(ALL_TABLES.keys())

    print(f"导出到: {CACHE_DIR}/")
    total = 0
    for key in targets:
        if key not in ALL_TABLES:
            print(f"  ! 未知表: {key} (用 --list 查看可用表)")
            continue
        name, tid = ALL_TABLES[key]
        try:
            total += export_table(key, name, tid)
        except Exception as e:
            print(f"  ! {key} 导出失败: {e}")

    # 写一份索引
    index_file = os.path.join(CACHE_DIR, "_index.json")
    index = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tables": {k: {"name": v[0], "tid": v[1]} for k, v in ALL_TABLES.items()},
        "total_records": total,
    }
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\n完成: 共 {total} 条记录, 缓存目录 {CACHE_DIR}/")


if __name__ == "__main__":
    main()
