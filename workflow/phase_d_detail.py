#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase D: Fetch influencer channel details and recent videos."""

import logging
from typing import List, Dict, Tuple

from youtube.channels import fetch_channel_details
from youtube.playlists import get_channel_uploads
from youtube.search import search_channel_videos
from youtube.videos import fetch_video_details
from filter.activity_evaluator import evaluate_channel_activity
from filter.channel_classifier import classify_channel
from filter.email_extractor import extract_contact_email
from filter.kol_name_extractor import extract_kol_name
from filter.promo_detector import aggregate_channel_promo
from filter.scoring import get_active_profile, score_influencer
from workflow.state import WorkflowState

logger = logging.getLogger("kol_workflow.workflow.phase_d")


def run_phase_d(
    api_key: str,
    new_channels: List[Dict],
    use_playlist: bool = True,
    recent_video_count: int = 25,
    min_subscribers: int = 0,
    quota_tracker=None,
    state: WorkflowState = None,
) -> Tuple[List[Dict], List[Dict], str]:
    """Execute Phase D: fetch channel details + recent videos.

    Args:
        new_channels: Output from Phase C (list of channel info dicts)
        use_playlist: If True, use playlistItems.list (cheap).
                      If False, use search.list (expensive but flexible).
        recent_video_count: Number of recent videos per channel
        quota_tracker: QuotaTracker instance
        state: WorkflowState for checkpoint/resume

    Returns:
        (influencer_details, influencer_videos, error_message)
    """
    logger.info(
        f"═══ 阶段D: 网红详情抓取 ({len(new_channels)} 个频道, "
        f"方案={'playlistItems' if use_playlist else 'search'}) ═══"
    )

    if not new_channels:
        return [], [], ""

    # ---- D-1: Channel details ----
    channel_ids = [c["channel_id"] for c in new_channels]

    # Filter out already-done channels (for resume)
    if state:
        channel_ids = state.get_remaining_channels(channel_ids)
        if not channel_ids:
            logger.info("所有频道已在之前完成，跳过阶段D")
            return [], [], ""
        logger.info(f"续传: 还有 {len(channel_ids)} 个频道待处理")

    channels_data, error = fetch_channel_details(
        api_key=api_key,
        channel_ids=channel_ids,
        quota_tracker=quota_tracker,
    )

    if error:
        logger.error(f"频道详情获取失败: {error}")
        return [], [], error

    # First-pass email extraction from the channel bio only. Video descriptions
    # are not available yet; D-2 below re-runs this with them and overwrites the
    # result, which is where most emails actually come from.
    for ch in channels_data:
        found = extract_contact_email(ch.get("channel_description", ""))
        _apply_email_fields(ch, found)
        ch["kol_name"] = extract_kol_name(
            ch.get("channel_title", ""),
            ch.get("channel_description", ""),
            ch.get("contact_email", ""),
        )

    channels_data = _filter_channels(
        channels_data,
        min_subscribers=min_subscribers,
        state=state,
    )
    if not channels_data:
        logger.info("频道级筛选后无可抓取频道")
        return [], [], ""

    # Enrich with representative video info from Phase C
    channel_to_new = {c["channel_id"]: c for c in new_channels}
    influencer_details = []
    for ch in channels_data:
        cid = ch["channel_id"]
        nc = channel_to_new.get(cid, {})
        rep = nc.get("representative_video", {})

        detail = {
            **ch,
            "rep_video_url": rep.get("video_url", ""),
            "rep_video_title": rep.get("title", ""),
            "rep_video_views": rep.get("view_count", 0),
            "rep_video_engagement": rep.get("engagement_rate", 0.0),
            "source_keyword": nc.get("source_keyword", ""),
            "dev_status": "待联系",
            "latest_published_at": "",
            "activity_status": "待确认",
            # Produce a useful description-only fallback before recent videos
            # are fetched. Successful video retrieval will refine it below.
            "channel_initial_assessment": classify_channel(
                ch.get("channel_title", ""),
                ch.get("channel_description", ""),
                [],
                nc.get("source_keyword", ""),
            ),
            # Promotion signals default to "not found" and are filled in D-2
            # once video descriptions are available.
            **aggregate_channel_promo([]),
            # Goal-oriented scoring (filled properly in D-2; safe defaults here).
            "match_profile": "",
            "brand_fit_score": 0,
            "content_types": "",
            "matched_keywords": "",
            "priority_score": 0,
            "dev_priority": "C",
            "recommend_reason": "",
        }
        influencer_details.append(detail)

    detail_by_channel = {
        detail.get("channel_id", ""): detail
        for detail in influencer_details
        if detail.get("channel_id")
    }

    # ---- D-2: Recent videos for each channel ----
    all_influencer_videos = []
    # Load the active product profile once; scoring is skipped if none configured.
    active_profile = get_active_profile()
    if active_profile:
        logger.info(f"阶段D: 启用产品画像「{active_profile.get('name', '')}」做匹配评分")

    for ch in channels_data:
        cid = ch["channel_id"]
        ctitle = ch.get("channel_title", "")
        detail = detail_by_channel.get(cid, {})

        # Get recent video IDs
        if use_playlist:
            video_ids, err = get_channel_uploads(
                api_key=api_key,
                channel_id=cid,
                uploads_playlist_id=ch.get("uploads_playlist_id", ""),
                max_results=recent_video_count,
                quota_tracker=quota_tracker,
            )
        else:
            video_ids, err = search_channel_videos(
                api_key=api_key,
                channel_id=cid,
                max_results=recent_video_count,
                order="date",
                quota_tracker=quota_tracker,
            )

        if err:
            detail.update(evaluate_channel_activity([], fetch_error=err))
            if _is_quota_error(err):
                logger.error(f"获取频道 {ctitle} 最近视频时配额耗尽: {err}")
                return influencer_details, all_influencer_videos, err
            logger.warning(f"获取频道 {ctitle} 最近视频失败: {err}")
            if state:
                state.mark_channel_done(cid)
            continue

        if not video_ids:
            logger.warning(f"频道 {ctitle} 无最近视频")
            detail.update(evaluate_channel_activity([]))
            if state:
                state.mark_channel_done(cid)
            continue

        # Fetch video details
        videos, _, err = fetch_video_details(
            api_key=api_key,
            video_ids=video_ids,
            quota_tracker=quota_tracker,
        )

        if err:
            detail.update(evaluate_channel_activity([], fetch_error=err))
            if _is_quota_error(err):
                logger.error(f"频道 {ctitle} 视频详情获取时配额耗尽: {err}")
                return influencer_details, all_influencer_videos, err
            logger.warning(f"频道 {ctitle} 视频详情获取失败: {err}")
        else:
            # Enrich with channel title
            for v in videos:
                v["channel_title"] = ctitle
            # Grade Amazon / affiliate promotion experience. This also stamps
            # per-video promo fields used by the 网红视频表.
            detail.update(aggregate_channel_promo(videos))

            # Goal-oriented scoring: product content-fit + business viability.
            if active_profile:
                detail.update(score_influencer(detail, videos, active_profile))

            # Re-run email extraction now that video descriptions exist. Most
            # creators publish their business email there rather than in the
            # channel bio (YouTube hides the bio email behind a CAPTCHA).
            found = extract_contact_email(
                ch.get("channel_description", ""),
                [v.get("description", "") for v in videos],
            )
            _apply_email_fields(detail, found)
            if found.get("contact_email") and not detail.get("kol_name"):
                detail["kol_name"] = extract_kol_name(
                    ctitle,
                    ch.get("channel_description", ""),
                    found["contact_email"],
                )

            all_influencer_videos.extend(videos)
            detail.update(evaluate_channel_activity(videos))
            detail["channel_initial_assessment"] = classify_channel(
                ctitle,
                ch.get("channel_description", ""),
                videos,
                detail.get("source_keyword", ""),
            )

        if state:
            state.mark_channel_done(cid)

        logger.debug(f"频道 {ctitle}: {len(videos)} 个最近视频")

    logger.info(
        f"阶段D完成: {len(influencer_details)} 个网红, "
        f"{len(all_influencer_videos)} 个最近视频"
    )

    return influencer_details, all_influencer_videos, ""


def _apply_email_fields(target: Dict, found: Dict) -> None:
    """Write the extract_contact_email() result onto a channel/detail dict."""
    email = found.get("contact_email", "")
    target["contact_email"] = email
    target["email_status"] = "已获取" if email else "需手动查找"
    target["email_source"] = found.get("email_source", "未找到")
    target["email_candidates"] = ", ".join(found.get("email_candidates", []))
    target["email_hit_videos"] = found.get("email_hit_videos", 0)


def _is_quota_error(error: str) -> bool:
    text = (error or "").lower()
    return (
        "quotaexceeded" in text
        or "dailylimitexceeded" in text
        or "配额已耗尽" in text
    )


def _filter_channels(
    channels_data: List[Dict],
    min_subscribers: int = 0,
    state: WorkflowState = None,
) -> List[Dict]:
    """Apply channel-level thresholds after channels.list."""
    if min_subscribers <= 0:
        return channels_data

    kept = []
    for ch in channels_data:
        reasons = []
        if min_subscribers > 0 and ch.get("subscriber_count", 0) < min_subscribers:
            reasons.append(f"订阅数{ch.get('subscriber_count', 0)}<{min_subscribers}")

        if reasons:
            ch["channel_filter_reason"] = "未达标: " + ", ".join(reasons)
            logger.info(f"频道级筛选排除 {ch.get('channel_title', '')}: {ch['channel_filter_reason']}")
            if state:
                state.mark_channel_done(ch.get("channel_id", ""))
            continue

        ch["channel_filter_reason"] = "频道达标"
        kept.append(ch)

    logger.info(f"频道级筛选: {len(kept)}/{len(channels_data)} 通过")
    return kept
