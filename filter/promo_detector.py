#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detect Amazon / affiliate promotion footprints in YouTube video descriptions.

Why this exists
---------------
A creator who already drops Amazon affiliate links (or runs an Amazon
Influencer storefront) is dramatically easier to work with:

* they understand affiliate / commission deals,
* they usually quote a fixed rate,
* they already know how to link a product listing in a description.

The YouTube ``videos.list`` response gives us ``snippet.description`` for free
(same quota cost), and that description is where those links live.  This module
turns raw description text into a graded "promotion experience" signal.

Grading (highest value first)
-----------------------------
``amazon_storefront``  amazon.<tld>/shop/<handle>  -> Amazon Influencer Program member
``amazon_associate``   ``tag=xxxx-20`` param or an explicit Associates disclosure
``amazon_link``        any other Amazon product / short link
``other_affiliate``    non-Amazon affiliate networks (geni.us, LTK, ShopMy, ...)
``sponsored``          #ad / paid promotion / sponsored-by wording only
"""

import re
import logging
from typing import Dict, Iterable, List, Sequence

logger = logging.getLogger("kol_workflow.filter.promo_detector")

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

URL_PATTERN = re.compile(r"https?://[^\s<>\"'\)\]\},]+", re.I)

# Amazon marketplace hosts (regional TLDs included).
AMAZON_HOST_PATTERN = re.compile(
    r"(?:^|\.)amazon\.(?:com|co\.uk|co\.jp|com\.au|com\.mx|com\.br|com\.tr|"
    r"de|fr|it|es|ca|in|nl|se|pl|ae|sa|sg|eg|be|cn)$",
    re.I,
)

# Amazon official short links. ``a.co`` is the US share-sheet shortener.
AMAZON_SHORT_HOSTS = {
    "amzn.to", "amzn.eu", "amzn.asia", "amzn.com", "a.co", "amzn.in",
}

# Amazon Influencer storefront: amazon.com/shop/<handle>
STOREFRONT_PATTERN = re.compile(
    r"https?://(?:www\.)?amazon\.[a-z.]{2,8}/shop/[^\s<>\"'\)\]\},/?]+", re.I
)

# Amazon Associates tracking id, e.g. ?tag=stouchi-20 / &tag=foo-21
ASSOCIATE_TAG_PATTERN = re.compile(r"[?&]tag=([a-z0-9][a-z0-9\-_]{1,24})", re.I)

# Third-party affiliate / link-monetisation networks frequently used by
# Amazon-focused creators.
OTHER_AFFILIATE_HOSTS = {
    "geni.us", "lasso.to", "howl.link", "howl.me", "shopmy.us", "shopmy.link",
    "liketoknow.it", "ltk.app", "rstyle.me", "shopstyle.it", "go.magik.ly",
    "magiclinks.org", "shareasale.com", "skimresources.com", "go.skimresources.com",
    "impact.com", "prf.hn", "sovrn.co", "avantlink.com", "partnerize.com",
    "collabs.shop", "shopltk.com", "mavely.app.link", "levanta.io",
    "archer.affiliate", "stan.store", "beacons.ai", "linktr.ee",
}

# Explicit Amazon Associates disclosures (FTC-mandated wording).
AMAZON_DISCLOSURE_PATTERN = re.compile(
    r"(?:as an amazon associate|amazon associate|amazon influencer|"
    r"earn from qualifying purchases|amazon affiliate|amazon storefront|"
    r"amazon\s*shop\b)",
    re.I,
)

# Generic affiliate wording (no network host present).
AFFILIATE_WORD_PATTERN = re.compile(
    r"(?:affiliate link|affiliate links|commission(?:s)? (?:at no|from|earned)|"
    r"i (?:may )?earn a (?:small )?commission|使用我的链接|联盟链接)",
    re.I,
)

# Paid-sponsorship wording. Proof of brand-deal experience, weaker than a link.
SPONSOR_PATTERN = re.compile(
    r"(?:#ad\b|#sponsored\b|\bsponsored by\b|\bpaid promotion\b|"
    r"\bpaid partnership\b|\bin partnership with\b|\bthanks to .{0,40}for sponsoring|"
    r"\bsponsor(?:ed)? this video\b|\bgifted by\b|\bpr sample\b)",
    re.I,
)

# Ordered best -> worst. Drives ``promo_label`` and channel-level rollup.
LEVEL_ORDER: Sequence[str] = (
    "amazon_storefront",
    "amazon_associate",
    "amazon_link",
    "other_affiliate",
    "sponsored",
)

LEVEL_LABELS: Dict[str, str] = {
    "amazon_storefront": "Amazon Storefront",
    "amazon_associate": "Amazon 联盟客",
    "amazon_link": "挂过Amazon链接",
    "other_affiliate": "其他联盟带货",
    "sponsored": "接过赞助",
    "none": "未发现",
}

AMAZON_LEVELS = {"amazon_storefront", "amazon_associate", "amazon_link"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _host_of(url: str) -> str:
    """Extract a lowercase hostname from a URL without importing urllib."""
    text = url.split("://", 1)[-1]
    host = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # strip credentials and port
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    return host.lower().rstrip(".")


def _is_amazon_host(host: str) -> bool:
    if host in AMAZON_SHORT_HOSTS:
        return True
    return bool(AMAZON_HOST_PATTERN.search(host))


def _is_other_affiliate_host(host: str) -> bool:
    if host in OTHER_AFFILIATE_HOSTS:
        return True
    # match subdomains such as go.geni.us
    return any(host.endswith("." + h) for h in OTHER_AFFILIATE_HOSTS)


def _dedupe(items: Iterable[str], limit: int = 0) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def _best_level(levels: Iterable[str]) -> str:
    level_set = set(levels)
    for level in LEVEL_ORDER:
        if level in level_set:
            return level
    return "none"


# ---------------------------------------------------------------------------
# Video-level detection
# ---------------------------------------------------------------------------

def detect_video_promo(description: str) -> Dict:
    """Analyse a single video description.

    Returns a dict with:
        promo_levels        list[str]  every level matched
        promo_level         str        highest level matched ("none" if clean)
        promo_label         str        Chinese label of ``promo_level``
        has_amazon          bool       any Amazon link / disclosure found
        amazon_links        list[str]  deduped Amazon URLs (max 5)
        storefront_url      str        first Amazon Influencer storefront URL
        associate_tags      list[str]  Amazon Associates tracking ids
        promo_evidence      str        short human-readable proof string
    """
    empty = {
        "promo_levels": [],
        "promo_level": "none",
        "promo_label": LEVEL_LABELS["none"],
        "has_amazon": False,
        "amazon_links": [],
        "storefront_url": "",
        "associate_tags": [],
        "promo_evidence": "",
    }
    if not description:
        return empty

    levels: List[str] = []
    amazon_links: List[str] = []
    other_links: List[str] = []

    for url in URL_PATTERN.findall(description):
        url = url.rstrip(".,;:!?)")
        host = _host_of(url)
        if _is_amazon_host(host):
            amazon_links.append(url)
        elif _is_other_affiliate_host(host):
            other_links.append(url)

    storefront_match = STOREFRONT_PATTERN.search(description)
    storefront_url = storefront_match.group(0) if storefront_match else ""

    associate_tags = _dedupe(
        (m.group(1) for m in ASSOCIATE_TAG_PATTERN.finditer(description)), limit=5
    )
    # A ``tag=`` param only counts as an Associates id when it sits on an
    # Amazon URL; other sites use ``tag=`` for ordinary categories.
    amazon_tag_present = any(ASSOCIATE_TAG_PATTERN.search(u) for u in amazon_links)

    if storefront_url:
        levels.append("amazon_storefront")
    if amazon_tag_present or AMAZON_DISCLOSURE_PATTERN.search(description):
        levels.append("amazon_associate")
    if amazon_links:
        levels.append("amazon_link")
    if other_links or AFFILIATE_WORD_PATTERN.search(description):
        levels.append("other_affiliate")
    if SPONSOR_PATTERN.search(description):
        levels.append("sponsored")

    if not levels:
        return empty

    amazon_links = _dedupe(amazon_links, limit=5)
    level = _best_level(levels)

    evidence_parts: List[str] = []
    if storefront_url:
        evidence_parts.append(storefront_url)
    if associate_tags and amazon_tag_present:
        evidence_parts.append("tag=" + associate_tags[0])
    evidence_parts.extend(u for u in amazon_links[:2] if u != storefront_url)
    if not evidence_parts:
        evidence_parts.extend(_dedupe(other_links, limit=2))
    if not evidence_parts:
        marker = SPONSOR_PATTERN.search(description) or AFFILIATE_WORD_PATTERN.search(description)
        if marker:
            evidence_parts.append(marker.group(0).strip())

    return {
        "promo_levels": levels,
        "promo_level": level,
        "promo_label": LEVEL_LABELS.get(level, level),
        "has_amazon": bool(amazon_links or storefront_url),
        "amazon_links": amazon_links,
        "storefront_url": storefront_url,
        "associate_tags": associate_tags if amazon_tag_present else [],
        "promo_evidence": " | ".join(evidence_parts[:3]),
    }


# ---------------------------------------------------------------------------
# Channel-level rollup
# ---------------------------------------------------------------------------

def aggregate_channel_promo(videos: Sequence[Dict]) -> Dict:
    """Roll per-video detections up to a channel verdict.

    ``videos`` are the dicts produced by ``youtube.videos.fetch_video_details``.
    Each video is annotated in-place with its own promo fields so the
    "网红视频表" can show which specific video carried the link.
    """
    result = {
        "amazon_promo_level": LEVEL_LABELS["none"],
        "amazon_promo_video_count": 0,
        "promo_video_count": 0,
        "amazon_storefront_url": "",
        "promo_evidence": "",
        "has_amazon_experience": False,
    }
    if not videos:
        return result

    all_levels: List[str] = []
    evidence: List[str] = []
    amazon_video_count = 0
    promo_video_count = 0
    storefront_url = ""

    for video in videos:
        detected = detect_video_promo(video.get("description", ""))
        # Annotate the video record for the 网红视频表.
        video["promo_level"] = detected["promo_level"]
        video["promo_label"] = detected["promo_label"]
        video["promo_links"] = " | ".join(detected["amazon_links"][:3])
        video["has_amazon_promo"] = detected["has_amazon"]

        if detected["promo_level"] == "none":
            continue

        promo_video_count += 1
        all_levels.extend(detected["promo_levels"])
        if detected["has_amazon"]:
            amazon_video_count += 1
        if detected["storefront_url"] and not storefront_url:
            storefront_url = detected["storefront_url"]
        if detected["promo_evidence"]:
            evidence.append(detected["promo_evidence"])

    level = _best_level(all_levels)
    result.update({
        "amazon_promo_level": LEVEL_LABELS.get(level, level),
        "amazon_promo_video_count": amazon_video_count,
        "promo_video_count": promo_video_count,
        "amazon_storefront_url": storefront_url,
        "promo_evidence": " ;; ".join(_dedupe(evidence, limit=3)),
        "has_amazon_experience": level in AMAZON_LEVELS,
    })
    return result


def promo_level_options() -> List[str]:
    """Single-select options for the Feishu 「亚马逊推广经验」 field."""
    return [LEVEL_LABELS[level] for level in LEVEL_ORDER] + [LEVEL_LABELS["none"]]
