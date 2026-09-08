#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量同步：把「网红详情表」(主表) 的派生数据同步进「红人表」。

与 build_hongren_table.py（删旧表重建）不同，本脚本：
  * 不删表、不重建 → table_id 永久固定，飞书链接/视图不失效
  * 按 Channel ID 做增量 upsert：新增缺失行 + 更新有差异的字段
  * 保护人工维护列（备注/开发状态/联系邮箱/开发负责人/开发优先级）：
    红人表已有值时不被主表覆盖，避免丢失跟进记录
  * 主表为空的值不写入，绝不清除红人表已有内容
  * 字段类型以红人表实际 schema 为准（动态读取，不硬编码）

所有运行时配置（lark-cli 路径、代理、Base/表 ID）从环境变量读取，
本仓库**不硬编码任何真实 ID、路径或代理端口**。

用法:
    python sync_hongren_table.py --dry-run     # 只出计划，不写数据
    python sync_hongren_table.py               # 执行同步（新增 + 更新）
    python sync_hongren_table.py --dedupe      # 额外清理重复 Channel ID 的多余行
"""
import json
import os
import sys
import time
import types
from collections import Counter

# stub dotenv（部分依赖顶层 import，但 venv 未装 python-dotenv）
try:
    import dotenv  # noqa: F401
except ImportError:
    _m = types.ModuleType("dotenv")
    _m.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _m

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import write_user_base as wb

# lark-cli 路径从环境变量读取。**新机器/服务器必须设置 FEISHU_LARK_CLI_PATH**，
# 本仓库不给出默认值，避免绑定作者本机。
wb.LARK = os.environ.get("FEISHU_LARK_CLI_PATH", "").strip()
# 代理端口从环境变量读取；不设则不注入 HTTPS_PROXY（用户机器若有全局代理可自行设）。
_proxy = os.environ.get("FEISHU_HTTPS_PROXY", "").strip()
if _proxy:
    os.environ["HTTPS_PROXY"] = _proxy
    os.environ["HTTP_PROXY"] = _proxy
os.environ["LARK_CLI_NO_PROXY_WARN"] = "1"

from write_user_base import BASE_TOKEN, TABLE_IDS  # noqa: E402
from score_influencers import list_all  # noqa: E402

SRC_TID = TABLE_IDS["网红详情表"]
HR_TID = os.environ.get("FEISHU_HONGREN_TABLE_ID", "").strip()

# 人工维护列：红人表已有值时不覆盖（跟进状态/报价/人工补的邮箱/优先级调整）
MANUAL_COLS = {"备注", "开发状态", "联系邮箱", "开发负责人", "开发优先级"}

BATCH = 200


def norm(v):
    """归一化飞书返回值为可比较字符串。

    注意：int 与 float 必须统一按 float 格式化，否则飞书返回的 int(1140000)
    与写入的 float(1140000.0 → "1.14e+06") 会被误判为差异，导致每次重复更新。
    """
    if isinstance(v, list):
        v = v[0] if v else ""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.6g}"
    return str(v).strip()


def to_value(v, typ):
    """按目标字段类型转换主表取到的值。"""
    raw = v[0] if isinstance(v, list) else v
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if typ == "number":
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    # select / text / url 一律传字符串（select 不能传数组）
    return s


def get_schema():
    """读取红人表实际字段：名称 -> (type, options)。"""
    j = wb.run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
                "--table-id", HR_TID, "--format", "json"])
    fs = (j or {}).get("data", {}).get("fields", [])
    if not fs:
        raise RuntimeError(f"读取红人表字段失败: {json.dumps(j, ensure_ascii=False)[:300]}")
    types_ = {f["name"]: f.get("type", "text") for f in fs}
    opts = {f["name"]: [o["name"] for o in (f.get("options") or [])] for f in fs}
    order = [f["name"] for f in fs]
    return types_, opts, order


def should_write(col, exp, act):
    """判断该列是否需要写入红人表。"""
    if norm(exp) == norm(act):
        return False                      # 无差异
    if exp is None or norm(exp) == "":
        return False                      # 主表为空 → 不清除红人表已有值
    if col in MANUAL_COLS and norm(act):
        return False                      # 人工列已有值 → 保护
    return True


def sync(dry=False, dedupe=False):
    """执行增量同步，可被其他流程直接调用（如 sync-workbook 一并生成派生表）。

    dry   : True 则只输出计划、不写数据
    dedupe: True 则额外清理重复 Channel ID 的多余行
    """
    print("[schema] 读取红人表实际字段类型…")
    TYPES, OPTS, ORDER = get_schema()
    print(f"  字段数 {len(ORDER)}，人工保护列: {sorted(MANUAL_COLS)}")

    print("[读取] 主表 网红详情表 …")
    src = list_all(SRC_TID)
    print("[读取] 红人表 …")
    hr = list_all(HR_TID)
    print(f"  主表 {len(src)} 行 / 红人表 {len(hr)} 行")

    # 索引
    src_by = {}
    for r in src:
        c = norm(r["fields"].get("Channel ID"))
        if c and c not in src_by:
            src_by[c] = r
    hr_by = {}
    for r in hr:
        c = norm(r["fields"].get("Channel ID"))
        if c:
            hr_by.setdefault(c, []).append(r)

    to_add = [c for c in src_by if c not in hr_by]
    orphan = [c for c in hr_by if c not in src_by]
    dup_extra = [rs[1:] for rs in hr_by.values() if len(rs) > 1]

    # 计算更新
    updates = {}          # record_id -> {col: value}
    skipped_manual = Counter()
    skipped_empty = Counter()
    for cid, rs in hr_by.items():
        if cid not in src_by:
            continue
        sf = src_by[cid]["fields"]
        target = rs[0]
        hf = target["fields"]
        patch = {}
        for col in ORDER:
            if col == "Channel ID":
                continue
            exp = to_value(sf.get(col), TYPES.get(col, "text"))
            act = hf.get(col)
            if should_write(col, exp, act):
                patch[col] = exp
            elif norm(exp) != norm(act) and col in MANUAL_COLS and norm(act):
                skipped_manual[col] += 1
            elif norm(exp) != norm(act) and (exp is None or norm(exp) == ""):
                skipped_empty[col] += 1
        if patch:
            updates[target["record_id"]] = patch

    print("\n" + "=" * 62)
    print("同步计划")
    print("=" * 62)
    print(f"  新增行        : {len(to_add)}")
    print(f"  更新行        : {len(updates)}")
    print(f"  孤立行(主表无): {len(orphan)}")
    print(f"  重复多余行    : {sum(len(x) for x in dup_extra)}  (需 --dedupe 才清理)")
    if skipped_manual:
        print(f"\n  [保护] 人工列已跳过覆盖: {dict(skipped_manual)}")
    if skipped_empty:
        print(f"  [保护] 主表空值未覆盖  : {dict(skipped_empty)}")

    if dry:
        print("\n[dry-run] 未写入任何数据。")
        if updates:
            rid, patch = next(iter(updates.items()))
            print(f"  更新样例 {rid}: {json.dumps(patch, ensure_ascii=False)[:300]}")
        if to_add:
            sf = src_by[to_add[0]]["fields"]
            print(f"  新增样例 {to_add[0]}: { {c: to_value(sf.get(c), TYPES.get(c,'text')) for c in ORDER[:6]} }")
        return

    # ---- 新增 ----
    created = 0
    if to_add:
        rows = []
        for cid in to_add:
            sf = src_by[cid]["fields"]
            rows.append([to_value(sf.get(c), TYPES.get(c, "text")) for c in ORDER])
        print(f"\n[新增] 分 {(len(rows)+BATCH-1)//BATCH} 批写入 {len(rows)} 行…")
        for i in range(0, len(rows), BATCH):
            chunk = {"fields": ORDER, "rows": rows[i:i + BATCH]}
            r = wb.run(["base", "+record-batch-create", "--as", "user", "--base-token", BASE_TOKEN,
                        "--table-id", HR_TID,
                        "--json", json.dumps(chunk, ensure_ascii=False)], expect_ok=False)
            if r and r.get("ok"):
                created += len(chunk["rows"])
                print(f"    +{len(chunk['rows'])} 行")
            else:
                print(f"    ❌ 新增批次失败: {json.dumps(r, ensure_ascii=False)[:300]}")
            time.sleep(0.2)

    # ---- 更新 ----
    updated = 0
    if updates:
        items = list(updates.items())
        print(f"\n[更新] 分 {(len(items)+BATCH-1)//BATCH} 批更新 {len(items)} 行…")
        for i in range(0, len(items), BATCH):
            chunk = {rid: patch for rid, patch in items[i:i + BATCH]}
            r = wb.run(["base", "+record-batch-update", "--as", "user", "--base-token", BASE_TOKEN,
                        "--table-id", HR_TID,
                        "--json", json.dumps({"update_records": chunk}, ensure_ascii=False)],
                       expect_ok=False)
            if r and r.get("ok"):
                updated += len(chunk)
                print(f"    ✓{len(chunk)} 行")
            else:
                print(f"    ❌ 更新批次失败: {json.dumps(r, ensure_ascii=False)[:300]}")
            time.sleep(0.2)

    # ---- 去重 ----
    deleted = 0
    if dedupe and dup_extra:
        # 安全前置：被删行若含保留行没有的人工数据，先合并到保留行再删，
        # 否则会丢失报价/跟进状态等人工记录（不可逆）。
        merge_patch = {}
        for cid, rs in hr_by.items():
            if len(rs) < 2:
                continue
            keep = rs[0]
            patch = {}
            for d in rs[1:]:
                for col in MANUAL_COLS:
                    dv = norm(d["fields"].get(col))
                    if dv and not norm(keep["fields"].get(col)):
                        patch.setdefault(col, dv)
            if patch:
                merge_patch[keep["record_id"]] = patch

        if merge_patch:
            print(f"\n[去重-保护] {len(merge_patch)} 行含独有人工数据，先合并到保留行…")
            for rid, patch in merge_patch.items():
                brief = {k: str(v)[:28] for k, v in patch.items()}
                print(f"    {rid}: {brief}")
            r = wb.run(["base", "+record-batch-update", "--as", "user", "--base-token", BASE_TOKEN,
                        "--table-id", HR_TID,
                        "--json", json.dumps({"update_records": merge_patch}, ensure_ascii=False)],
                       expect_ok=False)
            if r and r.get("ok"):
                print(f"    ✅ 已合并 {len(merge_patch)} 行")
            else:
                print(f"    ❌ 合并失败，已中止去重: {json.dumps(r, ensure_ascii=False)[:300]}")
                return
            time.sleep(0.3)

        ids = [r["record_id"] for group in dup_extra for r in group]
        print(f"\n[去重] 删除 {len(ids)} 行重复记录…")
        for i in range(0, len(ids), BATCH):
            r = wb.run(["base", "+record-delete", "--as", "user", "--base-token", BASE_TOKEN,
                        "--table-id", HR_TID, "--yes",
                        "--json", json.dumps({"record_id_list": ids[i:i + BATCH]}, ensure_ascii=False)],
                       expect_ok=False)
            if r and r.get("ok"):
                deleted += len(ids[i:i + BATCH])
                print(f"    -{len(ids[i:i + BATCH])} 行")
            else:
                print(f"    ❌ 删除批次失败: {json.dumps(r, ensure_ascii=False)[:300]}")
            time.sleep(0.2)

    print(f"\n✅ 完成: 新增 {created} / 更新 {updated} / 删除 {deleted}")
    final = list_all(HR_TID)
    print(f"   红人表现有 {len(final)} 行（table_id 未变: {HR_TID}）")


def main():
    sync(dry="--dry-run" in sys.argv, dedupe="--dedupe" in sys.argv)


if __name__ == "__main__":
    main()
