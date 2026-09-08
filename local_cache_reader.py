#!/usr/bin/env python3
"""
本地缓存读取器 — 从 local_cache/ 下的 JSON 文件秒级读取飞书表数据。

用法:
    from local_cache_reader import load_influencers, load_channel_videos

    # 读网红详情表（秒级，不用 30+ 次 API 调用）
    details = load_influencers()  # [{record_id, fields: {...}}, ...]

    # 读网红视频表
    videos = load_channel_videos()

    # 读全部表
    from local_cache_reader import load_all
    data = load_all()  # {influencers: [...], videos: [...], ...}

如需刷新缓存: python export_local_cache.py
"""
import json
import os

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_cache")


def _load(table_key):
    """加载本地 JSON 缓存文件"""
    path = os.path.join(_CACHE_DIR, f"{table_key}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"本地缓存不存在: {path}\n"
            f"请先运行: python export_local_cache.py {table_key}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_records(table_key):
    """返回 records 列表"""
    data = _load(table_key)
    return data.get("records", [])


def _get_fields_list(table_key):
    """返回字段名列表（飞书返回顺序）"""
    data = _load(table_key)
    return data.get("meta", {}).get("field_names", [])


def get_meta(table_key):
    """返回缓存元信息（导出时间、总条数、字段名）"""
    return _load(table_key).get("meta", {})


def load_influencers():
    """网红详情表 (497 条, 29 字段)"""
    return _get_records("influencers")


def load_channel_videos():
    """网红视频表 (5984 条) — 每条含频道ID、视频标题、描述等"""
    return _get_records("channel_videos")


def load_videos():
    """视频数据表 (2974 条)"""
    return _get_records("videos")


def load_search_tasks():
    """搜索任务表"""
    return _get_records("search_tasks")


def load_hongren():
    """红人表（派生精简表, 25 字段）"""
    return _get_records("hongren")


def load_all():
    """加载全部本地缓存"""
    tables = {}
    for key in ("influencers", "videos", "channel_videos",
                "search_tasks", "hongren"):
        path = os.path.join(_CACHE_DIR, f"{key}.json")
        if os.path.exists(path):
            tables[key] = _get_records(key)
    return tables


def video_map_by_channel():
    """返回 {channel_id: [video_dict, ...]} 的映射，方便评分脚本用"""
    records = load_channel_videos()
    vmap = {}
    for rec in records:
        f = rec.get("fields", {})
        cid = f.get("Channel ID") or f.get("频道ID") or ""
        if isinstance(cid, list):
            cid = cid[0] if cid else ""
        cid = str(cid)
        if cid:
            vmap.setdefault(cid, []).append(f)
    return vmap


if __name__ == "__main__":
    # 快速检查
    for key in ("influencers", "videos", "channel_videos",
                "search_tasks", "hongren"):
        path = os.path.join(_CACHE_DIR, f"{key}.json")
        if os.path.exists(path):
            meta = get_meta(key)
            print(f"  {key:20s}  {meta.get('total_records', '?'):>5} 条  "
                  f"({meta.get('table_name', '')})  "
                  f"[{meta.get('exported_at', '')}]")
        else:
            print(f"  {key:20s}  不存在")
