#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：主表(网红详情表) vs 派生「红人表」(tblPKFENcpk8xnZH) 的差距。

输出增量同步计划：新增 / 待更新 / 孤立 / 重复，不写任何数据。
用法:
    /path/to/python diff_hongren.py
"""
import os
import sys
import types

# stub dotenv（部分依赖顶层 import，venv 未装）
try:
    import dotenv  # noqa: F401
except ImportError:
    _m = types.ModuleType("dotenv")
    _m.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _m

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import write_user_base as wb

wb.LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["LARK_CLI_NO_PROXY_WARN"] = "1"

from write_user_base import TABLE_IDS  # noqa: E402
from score_influencers import list_all  # noqa: E402
from build_hongren_table import TARGET, _str, _num, _fmt_sub, _empty  # noqa: E402

SRC_TID = TABLE_IDS["网红详情表"]
HONGREN_TID = "tblPKFENcpk8xnZH"


def norm(v):
    """归一化飞书返回值为可比较字符串。"""
    if isinstance(v, list):
        v = v[0] if v else ""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v).strip()


def expected_value(f, name, typ):
    """按 build_hongren_table 的转换逻辑，算出该列在主表派生后的期望值。"""
    v = f.get(name)
    if name == "订阅数":
        return _fmt_sub(v)
    if typ == "number":
        return _num(v)
    if typ == "select":
        return _str(v) if not _empty(v) else None
    s = _str(v)
    return s if s else None


def main():
    print(f"[读取] 主表 网红详情表 ({SRC_TID}) …")
    src = list_all(SRC_TID)
    print(f"[读取] 派生 红人表 ({HONGREN_TID}) …")
    hr = list_all(HONGREN_TID)
    print(f"\n主表行数   : {len(src)}")
    print(f"红人表行数 : {len(hr)}")

    # 主表按 Channel ID 建索引
    src_by = {}
    for r in src:
        cid = norm(r["fields"].get("Channel ID"))
        if cid:
            src_by[cid] = r

    # 红人表按 Channel ID 建索引（保留重复）
    hr_by = {}
    for r in hr:
        cid = norm(r["fields"].get("Channel ID"))
        if cid:
            hr_by.setdefault(cid, []).append(r)

    dup_cids = {c: rs for c, rs in hr_by.items() if len(rs) > 1}
    dup_extra = sum(len(rs) - 1 for rs in dup_cids.values())

    to_add = [c for c in src_by if c not in hr_by]
    orphan = [c for c in hr_by if c not in src_by]

    # 逐字段比对，找出确实需要更新的行
    to_update = []
    for cid, rs in hr_by.items():
        if cid not in src_by:
            continue
        r = rs[0]  # 以第一条为准
        src_f = src_by[cid]["fields"]
        hr_f = r["fields"]
        diff_cols = []
        for name, typ in TARGET:
            exp = expected_value(src_f, name, typ)
            act = hr_f.get(name)
            if norm(exp) != norm(act):
                diff_cols.append(name)
        if diff_cols:
            to_update.append((cid, diff_cols))

    print("\n" + "=" * 60)
    print("增量同步计划")
    print("=" * 60)
    print(f"主表唯一 Channel ID     : {len(src_by)}")
    print(f"红人表唯一 Channel ID   : {len(hr_by)}")
    print(f"红人表重复 Channel ID   : {len(dup_cids)} 个（多余 {dup_extra} 行）")
    print(f"【新增】主表有/红人表无 : {len(to_add)}")
    print(f"【更新】字段有差异      : {len(to_update)}")
    print(f"【孤立】红人表有/主表无 : {len(orphan)}  ← 需确认是否删除")

    if dup_cids:
        print(f"\n重复 Channel ID 示例（前10）:")
        for c, rs in list(dup_cids.items())[:10]:
            print(f"  {c}  ×{len(rs)}")

    if to_update:
        print(f"\n待更新行示例（前10，含差异列）:")
        for cid, cols in to_update[:10]:
            print(f"  {cid}  差异列: {cols}")

    if orphan:
        print(f"\n孤立 Channel ID 示例（前10）:")
        for c in orphan[:10]:
            print(f"  {c}")

    # 统计各差异列出现频次，便于判断是"真差异"还是"格式差异"
    from collections import Counter
    col_cnt = Counter()
    for _, cols in to_update:
        for c in cols:
            col_cnt[c] += 1
    if col_cnt:
        print("\n差异列频次（判断是否为格式差异）:")
        for c, n in col_cnt.most_common(30):
            print(f"  {n:>5}  {c}")


if __name__ == "__main__":
    main()
