#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Goal-oriented influencer scoring for the Amazon off-site promotion workflow.

What this module adds on top of the existing "initial filter" signals
(断更评估 / 频��初步判断 / 亚马逊推广经验 / 邮箱):

1. 品牌匹配度 (content fit, 0-100) — how well the influencer's *recent content*
   matches a specific product the user wants to promote. Driven by a per-product
   profile (product link + keywords + desired content types) that the user supplies
   in ``product_profiles.json``. Keyword hits are scanned across every recent
   video's title / tags / description, exactly the data we already pull.

2. 开发优先级 (business viability, S/A/B/C) — combines content fit with the
   practical signals that determine whether outreach will actually convert:
   Amazon promotion experience, reachable email, engagement, target-market geo,
   and activity. This is the single column you sort the 网红详情表 by.

3. 推荐理由 — a one-line, human-readable rationale for the priority.

The product profile is user-controlled: different categories / brands get their
own keyword sets, so the same influencer can score high for one product and low
for another.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger("kol_workflow.filter.scoring")

PROFILE_PATH = Path(__file__).resolve().parents[1] / "product_profiles.json"

# Content-type detection. Applied to the combined title+tags+description text of
# each recent video. These are the content formats the user cares about when
# deciding whether an influencer can produce the asset they need.
CONTENT_TYPE_PATTERNS: Dict[str, List[str]] = {
    "开箱": [r"unboxing", r"开箱", r"first look", r"hands[- ]?on", r"上手"],
    "评测": [r"review", r"测评", r"评测", r"\btest\b", r"\bvs\b", r"对比", r"横评"],
    "使用体验": [
        r"使用", r"体验", r"usage", r"how to", r"setup", r"设置", r"教程",
        r"日常", r"daily", r"workflow", r"实测", r"感受",
    ],
    "种草": [
        r"\bbest\b", r"\btop\b", r"推荐", r"worth", r"favorite", r"my setup",
        r"setup tour", r"必买", r"好物", r"盘点", r"分享", r"种草",
    ],
}

# Amazon promotion experience → points for the business-viability score.
PROMO_POINTS = {
    "Amazon Storefront": 20,
    "Amazon 联盟客": 16,
    "挂过Amazon链接": 12,
    "其他联盟带货": 8,
    "接过赞助": 6,
    "未发现": 0,
}

PRIORITY_TIERS = ["S", "A", "B", "C"]


def load_profiles(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the product-profiles document. Returns {} on any failure."""
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        logger.warning("产品画像文件不存在: %s", p)
        return {}
    try:
        with p.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # scoring must never break Phase D.
        logger.warning("产品画像读取失败，评分将返回空: %s", exc)
        return {}


def get_active_profile(profiles: Optional[Dict[str, Any]] = None,
                       name: Optional[str] = None) -> Dict[str, Any]:
    """Return the active product profile dict, or {} if none configured.

    ``name`` overrides the ``active`` key in the file.
    """
    if profiles is None:
        profiles = load_profiles()
    if not profiles:
        return {}
    target = name or profiles.get("active", "")
    profile = profiles.get("profiles", {}).get(target, {})
    if not profile:
        logger.warning("未找到产品画像: %s", target)
        return {}
    return profile


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _video_text(video: Mapping[str, Any]) -> str:
    """Combine a video's title + tags + description into one searchable string."""
    parts: List[str] = []
    parts.append(str(video.get("title") or ""))
    tags = video.get("tags") or ""
    if isinstance(tags, str):
        parts.append(tags)
    elif isinstance(tags, (list, tuple, set)):
        parts.extend(str(t) for t in tags)
    parts.append(str(video.get("description") or ""))
    return _normalize(" ".join(parts))


def detect_content_types(text: str) -> Set[str]:
    """Return the set of content types present in a piece of text."""
    found: Set[str] = set()
    for ctype, patterns in CONTENT_TYPE_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            found.add(ctype)
    return found


def score_content_fit(videos: Sequence[Mapping[str, Any]],
                      profile: Mapping[str, Any]) -> Dict[str, Any]:
    """Compute 0-100 content-fit against a product profile.

    Returns keys: fit_score, matched_keywords (set), detected_types (set),
    coverage (0-1), video_count.
    """
    keywords = [k.lower() for k in profile.get("keywords", [])]
    min_views = float(profile.get("min_views", 50000) or 50000)
    min_eng = float(profile.get("min_engagement", 2.0) or 2.0)
    target_types = set(profile.get("content_types", []))

    result = {
        "fit_score": 0,
        "matched_keywords": set(),
        "detected_types": set(),
        "coverage": 0.0,
        "video_count": len(videos or []),
    }
    if not videos or not keywords:
        return result

    matched_videos = 0
    keyword_total = 0
    quality_bonus = 0
    matched_kw: Set[str] = set()
    detected: Set[str] = set()

    for video in videos:
        text = _video_text(video)
        hits = [kw for kw in keywords if kw and kw in text]
        if hits:
            matched_videos += 1
            keyword_total += min(len(hits), 3)
            matched_kw.update(hits)
            views = float(video.get("view_count", 0) or 0)
            eng = float(video.get("engagement_rate", 0.0) or 0.0)
            if views >= min_views and eng >= min_eng:
                quality_bonus += 1
        detected |= detect_content_types(text)

    coverage = matched_videos / max(1, len(videos))
    cov_score = min(1.0, matched_videos / min(len(videos), 5)) * 40
    kw_score = min(keyword_total, 10) / 10 * 30
    qual_score = min(quality_bonus, 5) / 5 * 20
    type_score = 10 if (detected & target_types) else 0

    fit = round(min(100, cov_score + kw_score + qual_score + type_score))
    result.update({
        "fit_score": fit,
        "matched_keywords": matched_kw,
        "detected_types": detected,
        "coverage": round(coverage, 3),
    })
    return result


def score_business(detail: Mapping[str, Any],
                   fit: int,
                   detected_types: Set[str],
                   profile: Mapping[str, Any]) -> Dict[str, Any]:
    """Combine content fit with outreach-viability signals → S/A/B/C + reason."""
    level = str(detail.get("amazon_promo_level", "未发现") or "未发现")
    p_promo = PROMO_POINTS.get(level, 0)

    email_status = str(detail.get("email_status", "") or "")
    p_email = 10 if email_status == "已获取" else 0

    eng = float(detail.get("rep_video_engagement", 0.0) or 0.0)
    p_eng = 10 if eng >= 4 else (6 if eng >= 2 else (3 if eng >= 1 else 0))

    markets = {str(m).upper() for m in profile.get("markets", [])}
    country = str(detail.get("country", "") or "").upper()
    p_market = 10 if country in markets else (5 if country in ("US", "CA", "DE") else 0)

    activity = str(detail.get("activity_status", "") or "")
    p_active = 10 if activity == "持续更新" else (0 if activity == "有断更风险" else 5)

    raw = min(100, round(fit * 0.40 + p_promo + p_email + p_eng + p_market + p_active))
    if raw >= 80:
        tier = "S"
    elif raw >= 60:
        tier = "A"
    elif raw >= 40:
        tier = "B"
    else:
        tier = "C"

    wanted = set(profile.get("content_types", []))
    shown_types = detected_types & wanted or detected_types
    type_label = "/".join(sorted(shown_types)) if shown_types else "未识别"

    reason = (
        f"匹配度{fit}·{level}·"
        f"{'已获取邮箱' if p_email else '待找邮箱'}·"
        f"互动{eng}%·{country or '地区?'}·{activity}·类型{type_label} → {tier}"
    )
    return {
        "priority_score": raw,
        "dev_priority": tier,
        "recommend_reason": reason,
    }


def score_influencer(detail: Mapping[str, Any],
                     videos: Sequence[Mapping[str, Any]],
                     profile: Mapping[str, Any]) -> Dict[str, Any]:
    """Full scoring for one influencer. Returns the fields written to 网红详情表.

    Falls back to a single pseudo-video (channel description + representative
    title) when no recent-video list is available, so the function is safe to call
    from either Phase D (videos present) or an offline re-score (videos from a
    different source).
    """
    if not profile:
        return {
            "match_profile": "",
            "brand_fit_score": 0,
            "content_types": "",
            "matched_keywords": "",
            "priority_score": 0,
            "dev_priority": "C",
            "recommend_reason": "未配置产品画像",
        }

    scan_videos: List[Dict[str, Any]] = list(videos or [])
    if not scan_videos:
        # Offline fallback: treat the channel description + rep title as one doc.
        pseudo = {
            "title": str(detail.get("rep_video_title", "") or ""),
            "tags": "",
            "description": str(detail.get("channel_description", "") or ""),
            "view_count": 0,
            "engagement_rate": 0.0,
        }
        scan_videos = [pseudo]

    fit_res = score_content_fit(scan_videos, profile)
    biz = score_business(detail, fit_res["fit_score"], fit_res["detected_types"], profile)

    wanted = set(profile.get("content_types", []))
    shown = fit_res["detected_types"] & wanted or fit_res["detected_types"]
    type_label = "/".join(sorted(shown)) if shown else ""

    return {
        "match_profile": str(profile.get("name", "")),
        "brand_fit_score": fit_res["fit_score"],
        "content_types": type_label,
        "matched_keywords": ", ".join(sorted(fit_res["matched_keywords"])[:12]),
        "priority_score": biz["priority_score"],
        "dev_priority": biz["dev_priority"],
        "recommend_reason": biz["recommend_reason"],
    }


__all__ = [
    "CONTENT_TYPE_PATTERNS",
    "PRIORITY_TIERS",
    "detect_content_types",
    "get_active_profile",
    "load_profiles",
    "score_business",
    "score_content_fit",
    "score_influencer",
]
