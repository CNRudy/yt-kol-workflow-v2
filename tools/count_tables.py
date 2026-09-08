import os
#!/usr/bin/env python3
"""统计 VyH0 主库所有数据表的记录数 / 字段数 / 表头，用于检查主库现状。"""
import subprocess, json, os, time

LARK = "/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
BASE = os.environ.get("FEISHU_APP_TOKEN", "")
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["LARK_CLI_NO_PROXY_WARN"] = "1"

TABLES = [
    ("网红详情表", "tbl4rnFUM9jJXvCQ"),
    ("视频数据表", "tblDQi8dEGhkjZyy"),
    ("网红视频表", "tblcHBORC90WWn05"),
    ("搜索任务表", "tblLT1yf9ioEhJOh"),
    ("红人表", "tblPKFENcpk8xnZH"),
    ("红人表 maa 全表匹配", "tblyy7XAfBhFdw5y"),
]

def list_once(tid, offset, limit=100):
    for attempt in range(3):
        try:
            r = subprocess.run(
                [LARK, "base", "+record-list", "--base-token", BASE,
                 "--table-id", tid, "--limit", str(limit), "--offset", str(offset), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            out = (r.stdout or "").strip()
            if not out:
                if attempt < 2:
                    time.sleep(2); continue
                return None
            return json.loads(out)
        except Exception as e:
            if attempt < 2:
                time.sleep(2); continue
            print(f"    [解析失败 offset={offset}] {e}")
            return None
    return None

for name, tid in TABLES:
    cnt = 0
    offset = 0
    fields = []
    has_more = True
    pages = 0
    while has_more:
        d = list_once(tid, offset)
        if d is None:
            print(f"{name} | {tid}\n  !! 分页拉取中断\n")
            break
        data = d.get("data", {})
        recs = data.get("data") or []
        cnt += len(recs)
        if not fields:
            fields = data.get("fields") or []
        has_more = bool(data.get("has_more"))
        if not has_more:
            break
        offset += 100
        pages += 1
        if pages > 400:
            print(f"{name}: 超过 400 页安全阀，停止")
            break
    else:
        pass
    print(f"{name} | {tid}")
    print(f"  记录数: {cnt}   字段数: {len(fields)}")
    print(f"  表头(前14): {fields[:14]}")
    print()
