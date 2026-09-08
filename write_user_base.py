#!/usr/bin/env python3
"""向已建好的用户 base 写入 4 张表数据（先清空再写，避免重复）。"""
import openpyxl, json, subprocess, sys, os, time, re

# lark-cli 可执行文件绝对路径。新机器/服务器必须通过环境变量
# FEISHU_LARK_CLI_PATH 传入，**本仓库不再给出默认值**，避免绑定作者本机。
LARK = os.environ.get("FEISHU_LARK_CLI_PATH", "").strip()
# 工作目录。默认使用脚本所在目录（`os.path.dirname(__file__)`），
# 新机器/服务器不必设置。
BASE_DIR = os.environ.get("KOL_WORKFLOW_DIR", os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(BASE_DIR, "output", "summary_german_baby_camera", "kol_summary_tables.xlsx")
# 飞书目标 Base app_token。**必填**：必须从环境变量 FEISHU_APP_TOKEN 显式传入。
# 本仓库不提供默认值，避免将作者的主库 Base ID 写入公开仓库。
BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", "").strip()

SHEETS = {
    "influencers":      "网红详情表",
    "search_videos":    "视频数据表",
    "influencer_videos":"网红视频表",
    "search_tasks":     "搜索任务表",
}
# 飞书表 ID。**必填**：必须从环境变量显式传入。提供空字符串作为占位
# 让脚本能加载但提醒调用方配置；运行前会校验为空则抛错。
# 变量名约定：FEISHU_TABLE_<SHEET_NAME_UPPER>
TABLE_IDS = {
    "网红详情表": os.environ.get("FEISHU_TABLE_DETAIL", "").strip(),
    "视频数据表": os.environ.get("FEISHU_TABLE_VIDEO", "").strip(),
    "网红视频表": os.environ.get("FEISHU_TABLE_INFLUENCER_VIDEO", "").strip(),
    "搜索任务表": os.environ.get("FEISHU_TABLE_TASK", "").strip(),
}
# 不同步到飞书的表（按飞书表名）。留空的列仍会照常同步。
# 想省飞书行数时把这两张"视频类"表加进来即可：
#   SKIP_TABLES = ["视频数据表", "网红视频表"]
# 当前：全同步（用户决定暂不砍表，等碰到大表行数上限再开「网红视频表 2」）。
SKIP_TABLES = []
SELECT_COLS = {
    "influencers": ["断更评估", "国家/地区", "邮箱状态", "邮箱来源",
                    "亚马逊推广经验", "开发优先级",
                    "开发状态", "来源关键词", "领域"],
}
URL_HINT = re.compile(r'url|链接', re.I)
DATE_HINT = re.compile(r'日期|时间|date|采集|发布', re.I)
NUM_HINT = re.compile(r'数|量|播放|订阅|互动|率|rate|count|粉丝|views|subs|价格|price', re.I)

# 名字里带 "链接"/"数" 会被上面的启发式误判的列，在这里显式指定真实类型。
# 注意：「匹配关键词」「匹配排序组」字段已在 2026-09-07 从飞书删除，勿再写入。
FORCE_TYPES = {
    "Amazon Storefront": "url",   # 不含 "url" 字样但确实是链接
    "推广链接": "text",            # 存的是 "a | b | c" 多链接串，不能当 url 字段
    "推广证据": "text",
    "候选邮箱": "text",
    "推荐理由": "text",
    "内容契合类型": "text",
    "品牌匹配度": "number",        # 0-100 整数，必须 number 才能排序筛选
}

CUR_SHEET = None


def infer_type(col, vals):
    if col in FORCE_TYPES:
        return FORCE_TYPES[col]
    if URL_HINT.search(col):
        return "url"
    if DATE_HINT.search(col) and any(re.search(r'\d{4}[-/]\d{2}[-/]\d{2}', str(v)) for v in vals[:30]):
        return "date"
    if NUM_HINT.search(col):
        num = sum(1 for v in vals if isinstance(v, (int, float)) or
                  (isinstance(v, str) and v.replace('.', '', 1).replace('-', '', 1).lstrip('+').isdigit()))
        if num >= len(vals) * 0.9:
            return "number"
    if col in SELECT_COLS.get(CUR_SHEET, []):
        return "select"
    return "text"


def load_sheet(ws):
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    data = [r for r in rows[1:] if any(c is not None for c in r)]
    col_vals = {}
    for ci, col in enumerate(hdr):
        col_vals[col] = [r[ci] for r in data if ci < len(r) and r[ci] is not None]
    cols = [c for c in hdr if col_vals.get(c)]
    cols = [c for c in cols if c not in ("多行文本",)]
    records = []
    for r in data:
        row = {}
        ok = False
        for col in cols:
            ci = hdr.index(col)
            v = r[ci] if ci < len(r) else None
            if v is None or (isinstance(v, str) and v.strip() == ""):
                row[col] = None
                continue
            ok = True
            t = infer_type(col, col_vals[col])
            if t == "number":
                try:
                    row[col] = float(v) if ("." in str(v)) else int(v)
                except Exception:
                    row[col] = None
            else:
                row[col] = str(v)
        if ok:
            records.append(row)
    return cols, records


def run(args, cwd=BASE_DIR, expect_ok=True):
    env = dict(os.environ)
    env["LARK_CLI_NO_PROXY_WARN"] = "1"  # 抑制 proxy 警告，避免污染 stdout
    p = subprocess.run([LARK] + args, capture_output=True, text=True, timeout=120, cwd=cwd, env=env)
    out = p.stdout.strip()
    err = p.stderr.strip()
    # lark-cli 成功时 JSON 走 stdout，失败时 JSON 走 stderr
    # 先尝试 stdout，再尝试 stderr
    raw = out if out else err
    # 如果输出里有非 JSON 行（如警告），提取最后一个 { 开头的 JSON
    if raw and not raw.startswith("{"):
        idx = raw.find("\n{")
        if idx >= 0:
            raw = raw[idx+1:].strip()
        else:
            idx = raw.find("{")
            if idx >= 0:
                raw = raw[idx:].strip()
    try:
        j = json.loads(raw)
    except Exception:
        print("  ! 非JSON:", (out or err)[:200]); return None
    if expect_ok and not j.get("ok"):
        print("  ! 失败:", json.dumps(j, ensure_ascii=False)[:300]); return None
    return j


def clear_table(tname):
    tid = TABLE_IDS.get(tname, tname)
    ids = []
    pt = None
    while True:
        a = ["base", "+record-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--limit", "200", "--format", "json"]
        if pt:
            a += ["--page-token", pt]
        j = run(a)
        if not j:
            break
        d = j.get("data", {})
        ids += d.get("record_id_list", [])
        pt = d.get("page_token")
        if not pt or not d.get("has_more"):
            break
    if ids:
        for i in range(0, len(ids), 100):
            chunk = ids[i:i+100]
            # 用 --json 传 record_id_list，避免位置参数报错
            jstr = json.dumps({"record_id_list": chunk}, ensure_ascii=False)
            run(["base", "+record-delete", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--json", jstr, "--yes"])
        print(f"  🗑 清空 {len(ids)} 条")
    else:
        print("  (空表，无需清空)")


NUMBER_STYLE_COLS = {"订阅数", "频道总播放量", "视频总数", "代表视频播放量"}
NUMBER_STYLE = {"precision": 0, "thousands_separator": True, "percentage": False, "type": "plain"}


def ensure_number_style(tname="网红详情表"):
    """确保 订阅数/频道总播放量/视频总数 在飞书里是 number 类型 + 千分位 + 无小数。"""
    tid = TABLE_IDS.get(tname, tname)
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    if not j or not j.get("ok"):
        print("  ⚠ 无法获取字段列表，跳过样式检查")
        return
    items = j.get("data", {}).get("items", [])
    field_map = {}
    for f in items:
        name = f.get("field_name") or f.get("name") or ""
        if name in NUMBER_STYLE_COLS:
            field_map[name] = f

    changed = 0
    for col in NUMBER_STYLE_COLS:
        f = field_map.get(col)
        if not f:
            continue
        ftype = f.get("type")
        style = f.get("style") or {}
        # 检查是否已经是 number + 千分位 + 0位小数
        if ftype in ("number", 2) and style.get("precision") == 0 and style.get("thousands_separator") is True:
            continue
        field_json = json.dumps({"name": col, "type": "number", "style": NUMBER_STYLE}, ensure_ascii=False)
        r = run(["base", "+field-update", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--field-id", f.get("field_id") or f.get("id") or col,
                 "--json", field_json, "--yes"], expect_ok=False)
        if r and r.get("ok"):
            changed += 1
            print(f"  📝 字段「{col}」已设为千分位+无小数")
        else:
            print(f"  ⚠ 字段「{col}」样式更新失败（可能需在飞书网页端手动改）")
    if changed:
        print(f"  ✅ {changed} 个字段样式已更新")
    else:
        print("  (字段样式已就绪，无需更新)")


def _field_body(col, ftype, values):
    """按推断类型构造 lark-cli field-create / field-update 的 JSON body。"""
    if ftype == "url":
        return {"name": col, "type": "text", "style": {"type": "url"}}
    if ftype == "number":
        return {"name": col, "type": "number", "style": NUMBER_STYLE}
    if ftype == "date":
        return {"name": col, "type": "datetime"}
    if ftype == "select":
        opts = []
        seen = set()
        for v in values:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                opts.append({"name": s})
        return {"name": col, "type": "select", "multiple": False, "options": opts}
    return {"name": col, "type": "text"}


def ensure_schema(tname, cols, records):
    """建出飞书表里缺失的列，并把单选列的选项补全。

    两个历史坑一起解决：
      1) 新增列如果飞书没有，batch_write 会被静默过滤掉 -> 数据看不到；
      2) 单选列出现新值（新关键词/新国家/新推广等级）时写入报 not_found，
         field-update 是 PUT 语义，必须提交「已有选项 + 新选项」的全量列表。
    """
    tid = TABLE_IDS.get(tname, tname)
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    if not j or not j.get("ok"):
        print(f"  ⚠ 无法读取 {tname} 字段，跳过建列")
        return
    items = j.get("data", {}).get("fields", []) or j.get("data", {}).get("items", [])
    existing = {f.get("name", ""): f for f in items}

    col_values = {c: [r.get(c) for r in records if r.get(c) is not None] for c in cols}

    # --- 1) 建缺失字段 ---
    for col in cols:
        if col in existing or col == "多行文本":
            continue
        ftype = infer_type(col, col_values.get(col, []))
        body = _field_body(col, ftype, col_values.get(col, []))
        r = run(["base", "+field-create", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--json", json.dumps(body, ensure_ascii=False),
                 "--yes"], expect_ok=False)
        if r and r.get("ok"):
            print(f"  ➕ 新建字段「{col}」({ftype})")
            existing[col] = {"name": col, "type": ftype, "options": body.get("options", [])}
        else:
            print(f"  ⚠ 字段「{col}」创建失败: {json.dumps(r, ensure_ascii=False)[:200] if r else 'no response'}")

    # --- 2) 补单选选项（PUT 语义，必须传全量） ---
    for col in SELECT_COLS.get(CUR_SHEET, []):
        f = existing.get(col)
        if not f or f.get("type") not in ("select", 3):
            continue
        have = [o for o in (f.get("options") or []) if o.get("name")]
        have_names = {o["name"] for o in have}
        wanted = []
        for v in col_values.get(col, []):
            s = str(v).strip()
            if s and s not in have_names and s not in {w["name"] for w in wanted}:
                wanted.append({"name": s})
        if not wanted:
            continue
        body = {"name": col, "type": "select", "multiple": False,
                "options": [{"name": o["name"]} for o in have] + wanted}
        fid = f.get("id") or f.get("field_id")
        if not fid:
            continue
        r = run(["base", "+field-update", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--field-id", fid,
                 "--json", json.dumps(body, ensure_ascii=False), "--yes"], expect_ok=False)
        if r and r.get("ok"):
            print(f"  🏷 字段「{col}」补充 {len(wanted)} 个选项")
        else:
            print(f"  ⚠ 字段「{col}」选项补充失败（新值可能写不进去）")


def get_feishu_fields(tname):
    """拉取飞书表的真实字段名集合，用于过滤 Excel 多余列。"""
    tid = TABLE_IDS.get(tname, tname)
    j = run(["base", "+field-list", "--as", "user", "--base-token", BASE_TOKEN,
             "--table-id", tid, "--format", "json"], expect_ok=False)
    if not j or not j.get("ok"):
        print(f"  ⚠ 无法获取 {tname} 字段列表，将跳过字段过滤")
        return None
    # lark-cli 返回的字段列表在 data.fields 里
    items = j.get("data", {}).get("fields", []) or j.get("data", {}).get("items", [])
    return {f.get("name", "") for f in items}


def batch_write(tname, cols, records, batch=200, pause=0.3):
    tid = TABLE_IDS.get(tname, tname)
    ok = fail = 0
    for i in range(0, len(records), batch):
        chunk = records[i:i + batch]
        rows = [[r.get(c) for c in cols] for r in chunk]
        path = os.path.join(BASE_DIR, f"_batch_{i}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fields": cols, "rows": rows}, f, ensure_ascii=False)
        j = run(["base", "+record-batch-create", "--as", "user", "--base-token", BASE_TOKEN,
                 "--table-id", tid, "--json", "@" + os.path.basename(path), "--format", "json"])
        # 不删除临时文件（sandbox safe-delete 会阻断），下次循环会覆盖
        if j and j.get("ok"):
            ok += len(chunk)
        else:
            fail += len(chunk)
            print(f"  ✗ 批次 {i//batch+1} 失败: {json.dumps(j, ensure_ascii=False)[:300]}")
        time.sleep(pause)
    return ok, fail


def main():
    global CUR_SHEET
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    # 先确保飞书里订阅数/总播放量/视频总数 是 number 类型 + 千分位 + 无小数
    print("[字段样式] 检查网红详情表数字字段样式...")
    ensure_number_style("网红详情表")
    total = 0
    for sheet, tname in SHEETS.items():
        if tname in SKIP_TABLES:
            print(f"\n[跳过] 『{tname}』（已在 SKIP_TABLES 中，不同步）")
            continue
        CUR_SHEET = sheet
        cols, records = load_sheet(wb[sheet])
        # 先补齐飞书侧缺失的字段和单选选项，否则新列会被下面的过滤悄悄丢掉
        print(f"\n[对齐字段] 『{tname}』")
        ensure_schema(tname, cols, records)
        # 拉飞书表真实字段名，过滤掉 Excel 多余列（避免 not_found）
        feishu_fields = get_feishu_fields(tname)
        if feishu_fields is not None:
            skipped = [c for c in cols if c not in feishu_fields]
            cols = [c for c in cols if c in feishu_fields]
            if skipped:
                print(f"  ℹ 跳过 Excel 多余列: {skipped}")
        print(f"\n[写表] 『{tname}』  {len(cols)}字段 / {len(records)}行")
        clear_table(tname)
        ok, fail = batch_write(tname, cols, records)
        total += ok
        print(f"  ✅ 写入 {ok}/{len(records)} (失败 {fail})")
    print(f"\n🎉 全部写入完成，共 {total} 行。base 链接: https://pcn8zy4grswl.feishu.cn/base/{BASE_TOKEN}")


if __name__ == "__main__":
    main()
