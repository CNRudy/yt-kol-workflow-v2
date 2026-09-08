#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主表(网红详情表) → MCB(WEMOH推车 MCB 网红详情表) 单向补齐同步。

原则：**只补空，不覆盖**。
  * MCB 该字段已有值 → 跳过（保住 MCB 的推车线专属评分）
  * MCB 为空 且 主表有值 → 写入
  * 类型冲突字段（内容契合类型/邮箱来源/匹配产品：主表 text、MCB select）
    按 MCB 选项映射；映射不上的跳过并统计报告，不会让整批失败。
  * datetime 统一转 `YYYY-MM-DD HH:MM:SS`

用法:
    ./.venv/bin/python sync_main_to_mcb.py             # dry-run，只出计划
    ./.venv/bin/python sync_main_to_mcb.py --apply     # 真正写入
"""
import os, json, subprocess, time, sys
from collections import Counter

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
VYH0 = "VyH0bJ1WBaCKIWsn9gVcBVc4nmc"; T_MAIN = "tbl4rnFUM9jJXvCQ"
MCB = "Yz9GbeSbfabon9sS1DKcNY25ngm";  T_MCB = "tblzPpg6Uj197wND"

# MCB 独有字段：不同步（产品名本身即代表类目，故「分类」已于 2026-09-07 删除）
SKIP = {"数据来源"}

BATCH = 200


def run(a):
    env = dict(os.environ); env["LARK_CLI_NO_PROXY_WARN"] = "1"
    p = subprocess.run([LARK] + a, capture_output=True, text=True, timeout=180, env=env)
    raw = (p.stdout or "").strip() or (p.stderr or "").strip()
    if raw and not raw.startswith("{"):
        i = raw.find("{"); raw = raw[i:] if i >= 0 else raw
    try:
        return json.loads(raw)
    except Exception:
        print("  非JSON:", raw[:200]); return None


def val(x):
    if isinstance(x, list):
        return x[0] if x else None
    return x


def read_all(bt, tid):
    out, off = [], 0
    while True:
        j = run(["base", "+record-list", "--as", "user", "--base-token", bt, "--table-id", tid,
                 "--offset", str(off), "--limit", "200", "--format", "json"])
        if not j:
            break
        d = j["data"]; f = d.get("fields", []); rows = d.get("data", []); rid = d.get("record_id_list", [])
        if not rows:
            break
        for i, r in enumerate(rows):
            rec = dict(zip(f, r)); rec["_rid"] = rid[i] if i < len(rid) else None
            out.append(rec)
        if len(rows) < 200:
            break
        off += 200; time.sleep(0.1)
    return out


def norm_dt(s):
    """ISO '2020-04-23T00:19:25.000+08:00' -> '2020-04-23 00:19:25'。"""
    if not s:
        return None
    s = str(s).strip().replace("T", " ")
    if "." in s:
        s = s.split(".")[0]
    for sign in ("+", "-"):
        # 去掉时区尾巴（保留日期时间部分）
        idx = s.find(sign, 10)
        if idx > 0:
            s = s[:idx]
            break
    return s.strip()


def cast(v, typ):
    """按目标字段类型转换值。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    if typ == "number":
        try:
            return float(s)
        except ValueError:
            return None
    if typ == "datetime":
        return norm_dt(s)
    return s


def is_empty(v):
    return v is None or str(v).strip() == ""


def main(apply=False):
    print("[读取] MCB 详情表 …")
    mcb = read_all(MCB, T_MCB)
    print("[读取] 主表 网红详情表 …")
    main_rows = read_all(VYH0, T_MAIN)
    mm = {val(r.get("Channel ID")): r for r in main_rows if val(r.get("Channel ID"))}
    print(f"  MCB {len(mcb)} 行 / 主表 {len(main_rows)} 行\n")

    # MCB 字段类型 + select 选项
    jf = run(["base", "+field-list", "--as", "user", "--base-token", MCB,
              "--table-id", T_MCB, "--format", "json"])
    FS = {f["name"]: f for f in jf["data"]["fields"]}
    TYPES = {n: f.get("type", "text") for n, f in FS.items()}
    OPTS = {n: [o["name"] for o in (f.get("options") or [])]
            for n, f in FS.items() if f.get("type") == "select"}

    # 共同字段（排除 MCB 独有）
    common = [n for n in FS if n in mm and False] or None
    # 用主表首行字段名做交集更稳
    main_cols = set()
    for r in main_rows[:1]:
        main_cols = set(k for k in r.keys() if not k.startswith("_"))
    cols = [n for n in FS if n in main_cols and n not in SKIP and n != "Channel ID"]
    print(f"待比对字段 {len(cols)} 个（已排除 MCB 独有: {sorted(SKIP)}）")

    patches = {}
    stat = Counter()
    conflict = Counter()
    unmatched = Counter()
    unmapped_samples = {}

    for r in mcb:
        cid = val(r.get("Channel ID"))
        src = mm.get(cid)
        if not src:
            stat["主表无此行"] += 1
            continue
        patch = {}
        for c in cols:
            sv = cast(val(src.get(c)), TYPES.get(c, "text"))
            cv = val(r.get(c))
            if is_empty(sv):
                continue                      # 主表无值 → 不写
            if not is_empty(cv):              # MCB 已有值 → 不覆盖
                if str(sv).strip() != str(cv).strip():
                    conflict[c] += 1
                continue
            # MCB 为 select 时校验选项
            if TYPES.get(c) == "select" and str(sv) not in (OPTS.get(c) or []):
                unmatched[c] += 1
                unmapped_samples.setdefault(c, set()).add(str(sv))
                continue
            patch[c] = sv
        if patch:
            patches[r["_rid"]] = patch
            stat["将更新行"] += 1

    print("\n" + "=" * 60)
    print("同步计划（只补空，不覆盖）")
    print("=" * 60)
    print(f"  将更新行数        : {len(patches)} / {len(mcb)}")
    print(f"  主表无此行(MCB独有): {stat['主表无此行']}")
    field_cnt = Counter()
    for p in patches.values():
        for c in p:
            field_cnt[c] += 1
    print("\n  按字段统计（将写入的单元格数）:")
    for c, n in field_cnt.most_common():
        print(f"    {c:<16} {n:>4}")
    if unmatched:
        print("\n  ⚠️ select 选项映射不上、已跳过（需人工确认是否补选项）:")
        for c, n in unmatched.most_common():
            print(f"    {c}: {n} 条, 样例 {sorted(unmapped_samples[c])[:3]}")
    if conflict:
        print("\n  ℹ️ MCB 已有值但与主表不同（本次不覆盖，仅供参考）:")
        for c, n in conflict.most_common(10):
            print(f"    {c}: {n} 处")

    if not apply:
        print("\n[dry-run] 未写入。加 --apply 执行。")
        if patches:
            rid = next(iter(patches))
            print(f"  样例 {rid}: {json.dumps(patches[rid], ensure_ascii=False)[:300]}")
        return

    items = list(patches.items())
    ok = fail = 0
    print(f"\n[写入] 分 {(len(items)+BATCH-1)//BATCH} 批更新 {len(items)} 行…")
    for i in range(0, len(items), BATCH):
        chunk = {k: v for k, v in items[i:i + BATCH]}
        j = run(["base", "+record-batch-update", "--as", "user", "--base-token", MCB,
                 "--table-id", T_MCB, "--json",
                 json.dumps({"update_records": chunk}, ensure_ascii=False), "--format", "json"])
        if j and j.get("ok"):
            ok += len(chunk); print(f"    ✓{len(chunk)} 行")
        else:
            fail += len(chunk); print(f"    ❌ 失败: {json.dumps(j, ensure_ascii=False)[:300]}")
        time.sleep(0.3)
    print(f"\n✅ 完成: 成功 {ok} 行 / 失败 {fail} 行")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
