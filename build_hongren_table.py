#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从飞书「网红详情表」派生一张精简的「红人表」(25 列)。

- 删掉空主键/系统日期/冗余播放数/诊断计数/被覆盖的关键词等 11 列
- 再删掉 KOL Name / 邮箱状态 / 候选邮箱 3 列
- 订阅数转成文本列并带千分位符（如 1,234,567），无小数点
- 保留 备注（开发过程中随手记）
- 单选字段的选项从源数据动态收集，避免写值时 not_found
- 输出目标：飞书 VyH0 base 内新建一张「红人表」

用法:
    ./.venv/bin/python build_hongren_table.py --dry-run   # 只建表结构 + 预览首行
    ./.venv/bin/python build_hongren_table.py             # 删旧表 + 建新表并写入全部数据
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from write_user_base import BASE_TOKEN, TABLE_IDS, run
from score_influencers import list_all
from filter.promo_detector import promo_level_options

SRC_TID = TABLE_IDS["网红详情表"]
NEW_NAME = "红人表"

# (列名, 类型)  —— 25 列精简版
TARGET = [
    ("Channel ID", "text"),
    ("Channel Name", "text"),
    ("频道URL", "url"),
    ("最新发布日期", "text"),
    ("断更评估", "select"),
    ("订阅数", "text"),   # 文本列，带千分位符（如 1,234,567），无小数点
    ("国家/地区", "text"),
    ("频道初步判断", "text"),
    ("联系邮箱", "text"),
    ("邮箱来源", "select"),
    ("亚马逊推广经验", "select"),
    ("Amazon Storefront", "url"),
    ("推广证据", "text"),
    ("匹配产品", "text"),
    ("品牌匹配度", "number"),
    ("内容契合类型", "text"),
    ("开发优先级", "select"),
    ("推荐理由", "text"),
    ("代表视频URL", "url"),
    ("代表视频标题", "text"),
    ("代表视频互动率", "number"),
    ("来源关键词", "text"),
    ("开发状态", "select"),
    ("开发负责人", "text"),
    ("备注", "text"),
]

# 标准单选的固定选项（与源表一致）
KNOWN_SELECT = {
    "断更评估": ["持续更新", "有断更风险", "待确认"],
    "邮箱来源": ["频道简介", "视频描述", "未找到"],
    "亚马逊推广经验": promo_level_options(),
    "开发优先级": ["S", "A", "B", "C"],
}


def _empty(v):
    if v is None:
        return True
    if isinstance(v, str) and v.strip() in ("", "<缺失>", "<空>", "None", "nan"):
        return True
    return False


def _str(v):
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_sub(v):
    """订阅数转文本 + 千分位符，无小数点；空值返回 None。"""
    n = _num(v)
    if n is None:
        return None
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return None


def find_table_id(j):
    """从 table-create 响应里递归找 table_id。"""
    if isinstance(j, dict):
        if "table_id" in j and isinstance(j["table_id"], str):
            return j["table_id"]
        for v in j.values():
            r = find_table_id(v)
            if r:
                return r
    elif isinstance(j, list):
        for v in j:
            r = find_table_id(v)
            if r:
                return r
    return None


def table_exists():
    """返回已存在的「红人表」table_id，否则 False。"""
    j = run(["base", "+table-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--format", "json"])
    if not j or not j.get("ok"):
        return False
    items = j.get("data", {}).get("items") or j.get("data", {}).get("tables") or []
    for it in items:
        if (it.get("name") or it.get("table_name")) == NEW_NAME:
            return it.get("table_id") or it.get("id")
    return False


def resolve_tid(j):
    """从 table-create 响应取 id，取不到就回退查 table-list。"""
    tid = find_table_id(j)
    if tid:
        return tid
    return table_exists()


def main():
    dry = "--dry-run" in sys.argv

    print(f"[读取] 网红详情表（{SRC_TID}）全量…")
    rows = list_all(SRC_TID)
    print(f"  👤 共 {len(rows)} 个红人")

    # 收集每个 select 列的真实取值（合并固定选项 + 源数据去重）
    select_vals = {name: set(KNOWN_SELECT.get(name, [])) for name, _ in TARGET
                   if _ == "select"}
    for rec in rows:
        f = rec["fields"]
        for name in select_vals:
            v = f.get(name)
            s = _str(v)
            if s:
                select_vals[name].add(s)

    # 构建建表字段 JSON
    fields_json = []
    for name, typ in TARGET:
        if typ == "select":
            opts = [{"name": o} for o in sorted(select_vals[name])]
            fields_json.append({"name": name, "type": "select", "options": opts})
        else:
            fields_json.append({"name": name, "type": typ})

    print(f"[建表] 新建「{NEW_NAME}」，字段数={len(fields_json)}")
    for fj in fields_json:
        extra = f" 选项={[o['name'] for o in fj.get('options',[])]}" if fj["type"] == "select" else ""
        print(f"    - {fj['name']:10s} [{fj['type']}]{extra}")

    if dry:
        print("\n[dry-run] 不实际建表/写数据。首行样本预览：")
        if rows:
            f0 = rows[0]["fields"]
            for name, typ in TARGET:
                print(f"    {name:10s} = {_str(f0.get(name))[:40]!r}")
        return

    # 若已存在则先删除，再按新结构重建（红人表是派生表，源数据在网红详情表，重建无损）
    existing = table_exists()
    if existing:
        print(f"  🗑 删除已存在的「{NEW_NAME}」（{existing}），按新结构重建…")
        run(["base", "+table-delete", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", existing, "--yes"], expect_ok=False)

    j = run(["base", "+table-create", "--as", "user", "--base-token", BASE_TOKEN,
             "--name", NEW_NAME, "--fields", json.dumps(fields_json, ensure_ascii=False),
             "--format", "json"], expect_ok=False)
    if not j or not j.get("ok"):
        print("❌ 建表失败：", json.dumps(j, ensure_ascii=False)[:500])
        return
    new_tid = resolve_tid(j)
    print(f"  ✅ 建表成功，table_id={new_tid}")

    # 构建紧凑批量写入格式 {"fields":[...], "rows":[[...]]}
    names = [n for n, _ in TARGET]
    data_rows = []
    for rec in rows:
        f = rec["fields"]
        row = []
        for name, typ in TARGET:
            v = f.get(name)
            if name == "订阅数":
                row.append(_fmt_sub(v))
            elif typ == "number":
                n = _num(v)
                row.append(n)
            elif typ == "select":
                row.append(_str(v) if not _empty(v) else None)
            else:  # text / url
                s = _str(v)
                row.append(s if s else None)
        data_rows.append(row)

    payload = {"fields": names, "rows": data_rows}
    print(f"[写入] 分 {(len(data_rows)+199)//200} 批写入 {len(data_rows)} 行…")
    ok = 0
    for i in range(0, len(data_rows), 200):
        chunk = {"fields": names, "rows": data_rows[i:i + 200]}
        r = run(["base", "+record-batch-create", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", new_tid,
                 "--json", json.dumps(chunk, ensure_ascii=False)], expect_ok=False)
        if r and r.get("ok"):
            ok += len(chunk["rows"])
            print(f"    批次 {i//200+1}: +{len(chunk['rows'])} 行")
        else:
            print(f"    ❌ 批次 {i//200+1} 失败：{json.dumps(r, ensure_ascii=False)[:300]}")
        time.sleep(0.2)

    # 校验
    final = list_all(new_tid)
    print(f"\n✅ 完成：「{NEW_NAME}」写入 {ok} 行，飞书实查 {len(final)} 行")


if __name__ == "__main__":
    main()
