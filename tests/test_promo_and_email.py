#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for Amazon promo detection and multi-source email extraction."""

from filter.promo_detector import (
    aggregate_channel_promo,
    detect_video_promo,
    promo_level_options,
)
from filter.email_extractor import extract_contact_email, extract_email


# ---------------------------------------------------------------------------
# promo_detector
# ---------------------------------------------------------------------------

def test_storefront_is_top_grade():
    desc = "Shop my faves here: https://www.amazon.com/shop/techwithmike"
    r = detect_video_promo(desc)
    assert r["promo_level"] == "amazon_storefront"
    assert r["promo_label"] == "Amazon Storefront"
    assert r["storefront_url"].endswith("/shop/techwithmike")
    assert r["has_amazon"] is True


def test_associate_tag_on_amazon_link():
    desc = "Get it here https://www.amazon.com/dp/B0GJ54TC4V?tag=stouchi-20 thanks!"
    r = detect_video_promo(desc)
    assert r["promo_level"] == "amazon_associate"
    assert r["associate_tags"] == ["stouchi-20"]


def test_associate_disclosure_without_tag():
    desc = "As an Amazon Associate I earn from qualifying purchases."
    r = detect_video_promo(desc)
    assert r["promo_level"] == "amazon_associate"


def test_plain_amazon_short_link():
    desc = "Dock: https://amzn.to/3xYzAbc\nCable: https://a.co/d/abc123"
    r = detect_video_promo(desc)
    assert r["promo_level"] == "amazon_link"
    assert len(r["amazon_links"]) == 2


def test_tag_param_on_non_amazon_host_is_not_associate():
    desc = "Read more https://example.com/post?tag=hello-20"
    r = detect_video_promo(desc)
    assert r["promo_level"] == "none"


def test_other_affiliate_and_sponsored():
    assert detect_video_promo("Gear: https://geni.us/abc")["promo_level"] == "other_affiliate"
    assert detect_video_promo("This video is #ad")["promo_level"] == "sponsored"


def test_clean_description_scores_none():
    r = detect_video_promo("Just a vlog about my cat. Subscribe!")
    assert r["promo_level"] == "none"
    assert r["has_amazon"] is False
    assert r["promo_evidence"] == ""


def test_channel_rollup_takes_best_and_counts():
    videos = [
        {"description": "https://amzn.to/aaa"},
        {"description": "https://www.amazon.com/shop/janedoe"},
        {"description": "nothing here"},
        {"description": "#sponsored by SomeBrand"},
    ]
    agg = aggregate_channel_promo(videos)
    assert agg["amazon_promo_level"] == "Amazon Storefront"
    assert agg["amazon_promo_video_count"] == 2
    assert agg["promo_video_count"] == 3
    assert agg["has_amazon_experience"] is True
    assert "janedoe" in agg["amazon_storefront_url"]
    # per-video annotations are written back for the 网红视频表
    assert videos[0]["promo_label"] == "挂过Amazon链接"
    assert videos[2]["promo_label"] == "未发现"


def test_empty_rollup_is_safe():
    agg = aggregate_channel_promo([])
    assert agg["amazon_promo_level"] == "未发现"
    assert agg["has_amazon_experience"] is False


def test_promo_level_options_cover_all_labels():
    opts = promo_level_options()
    assert "Amazon Storefront" in opts and "未发现" in opts
    assert len(opts) == len(set(opts))


# ---------------------------------------------------------------------------
# email_extractor
# ---------------------------------------------------------------------------

def test_channel_bio_email_wins_over_video():
    found = extract_contact_email(
        "For business inquiries: biz@creator.com",
        ["contact me at other@video.com"],
    )
    assert found["contact_email"] == "biz@creator.com"
    assert found["email_source"] == "频道简介"


def test_falls_back_to_video_descriptions():
    videos = [
        "Business: mike@mikereviews.com",
        "Business: mike@mikereviews.com",
        "Music from Epidemic Sound",
    ]
    found = extract_contact_email("Welcome to my channel!", videos)
    assert found["contact_email"] == "mike@mikereviews.com"
    assert found["email_source"] == "视频描述"
    assert found["email_hit_videos"] == 2


def test_repeated_email_beats_one_off_sponsor_email():
    videos = [
        "sponsor: deals@randombrand.io",
        "business inquiries: me@myshow.com",
        "business inquiries: me@myshow.com",
        "business inquiries: me@myshow.com",
    ]
    found = extract_contact_email("", videos)
    assert found["contact_email"] == "me@myshow.com"
    assert "deals@randombrand.io" in found["email_candidates"]


def test_platform_noise_is_filtered():
    found = extract_contact_email(
        "",
        ["questions? support@amazon.com", "noreply@youtube.com", "hi@realcreator.net"],
    )
    assert found["contact_email"] == "hi@realcreator.net"


def test_image_filename_is_not_an_email():
    found = extract_contact_email("banner logo@2x.png here", [])
    assert found["contact_email"] == ""
    assert found["email_source"] == "未找到"


def test_no_email_anywhere():
    found = extract_contact_email("no contact info", ["still nothing"])
    assert found == {
        "contact_email": "",
        "email_source": "未找到",
        "email_candidates": [],
        "email_hit_videos": 0,
    }


def test_legacy_extract_email_still_works():
    assert extract_email("business: a@b.com") == "a@b.com"
    assert extract_email("") is None
