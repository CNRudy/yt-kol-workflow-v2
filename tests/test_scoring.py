#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the goal-oriented scoring module."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filter.scoring import (
    detect_content_types,
    get_active_profile,
    score_business,
    score_content_fit,
    score_influencer,
)

PROFILE = {
    "name": "Stouchi Mac mini M4 扩展坞",
    "keywords": ["mac mini", "m4", "dock", "扩展坞", "usb-c hub", "hub", "desktop setup"],
    "content_types": ["评测", "开箱", "使用体验", "种草"],
    "markets": ["US", "CA"],
    "min_views": 50000,
    "min_engagement": 2.0,
}


def _video(title, desc="", tags="", views=80000, eng=4.0):
    return {
        "title": title,
        "description": desc,
        "tags": tags,
        "view_count": views,
        "engagement_rate": eng,
    }


def test_content_type_detection():
    assert "开箱" in detect_content_types("iPhone unboxing first look")
    assert "评测" in detect_content_types("MacBook Pro review vs Air")
    assert "使用体验" in detect_content_types("my daily setup workflow 使用体验")
    assert "种草" in detect_content_types("best usb-c hub 推荐 必买")
    # unrelated text yields nothing
    assert detect_content_types("cute cat compilation") == set()


def test_content_fit_high_for_on_topic_high_perf():
    videos = [
        _video("Mac mini M4 dock review", "best usb-c hub for desktop setup", "mac mini, dock", 120000, 5.0),
        _video("Unboxing the new hub", "mac mini m4 expansion", "扩展坞", 90000, 4.0),
    ]
    res = score_content_fit(videos, PROFILE)
    assert res["fit_score"] >= 70
    assert "mac mini" in res["matched_keywords"]
    assert res["coverage"] == 1.0
    assert bool(res["detected_types"] & {"评测", "开箱"})


def test_content_fit_zero_for_off_topic():
    videos = [_video("cooking pasta recipe", "yummy dinner", "food", 200000, 6.0)]
    res = score_content_fit(videos, PROFILE)
    assert res["fit_score"] == 0
    assert res["matched_keywords"] == set()


def test_business_priority_s_tier():
    detail = {
        "amazon_promo_level": "Amazon Storefront",
        "email_status": "已获取",
        "rep_video_engagement": 4.5,
        "country": "US",
        "activity_status": "持续更新",
    }
    biz = score_business(detail, 85, {"评测", "开箱"}, PROFILE)
    assert biz["dev_priority"] == "S"
    assert "Amazon Storefront" in biz["recommend_reason"]


def test_business_priority_c_tier_low_signal():
    detail = {
        "amazon_promo_level": "未发现",
        "email_status": "需手动查找",
        "rep_video_engagement": 0.5,
        "country": "JP",
        "activity_status": "有断更风险",
    }
    biz = score_business(detail, 10, set(), PROFILE)
    assert biz["dev_priority"] == "C"


def test_score_influencer_offline_fallback():
    detail = {"channel_description": "I review mac mini docks and usb-c hubs", "rep_video_title": ""}
    out = score_influencer(detail, [], PROFILE)
    # Fallback still extracts keywords from channel description.
    assert "mac mini" in out["matched_keywords"] or "hub" in out["matched_keywords"]
    assert out["dev_priority"] in ("S", "A", "B", "C")


def test_no_profile_returns_neutral():
    out = score_influencer({"amazon_promo_level": "未发现"}, [], {})
    assert out["dev_priority"] == "C"
    assert out["brand_fit_score"] == 0


def test_active_profile_loads():
    profiles = {
        "active": "mac_mini_dock",
        "profiles": {"mac_mini_dock": PROFILE},
    }
    assert get_active_profile(profiles) == PROFILE
    assert get_active_profile(profiles, name="mac_mini_dock") == PROFILE
    assert get_active_profile({}) == {}
