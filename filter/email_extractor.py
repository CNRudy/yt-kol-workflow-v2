#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract contact emails from YouTube channel descriptions AND video descriptions.

Channel "about" text alone yields very few emails: YouTube hides the business
email behind a CAPTCHA, so the API never returns it.  In practice most creators
paste the same business email into every video description instead.  We already
pull ``snippet.description`` for each recent video at zero extra quota cost, so
those descriptions are by far the richest email source available.

Ranking strategy (best first):
1. Channel description, email sitting next to business wording.
2. Channel description, any email.
3. Video descriptions, business wording nearby AND repeated across videos.
4. Video descriptions, most frequently repeated email.

Repetition matters: a sponsor's or a tool vendor's address shows up once, the
creator's own business address shows up in nearly every description.
"""

import re
import logging
from collections import Counter
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("kol_workflow.filter.email_extractor")

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

BUSINESS_KEYWORDS = [
    "business", "collab", "collaborat", "sponsor", "partner",
    "inquiry", "inquiries", "contact", "pr ", "marketing",
    "press", "brand", "work with", "cooperation", "promo",
    "advertis", "booking", "management", "代理", "合作",
    "reach me", "reach out", "email me", "get in touch", "for any",
]

# Domains that are never the creator's own contact address.  Sponsor read-outs
# and boilerplate links drag these in constantly.
PLATFORM_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.de", "youtube.com", "google.com",
    "gstatic.com", "googlemail.net", "ebay.com", "aliexpress.com", "temu.com",
    "walmart.com", "shopify.com", "squarespace.com", "wixpress.com",
    "wordpress.com", "patreon.com", "paypal.com", "stripe.com",
    "epidemicsound.com", "artlist.io", "musicbed.com", "soundstripe.com",
    "instagram.com", "tiktok.com", "facebook.com", "twitter.com", "x.com",
    "discord.com", "discord.gg", "twitch.tv", "linktr.ee", "beacons.ai",
    "bit.ly", "geni.us", "sentry.io", "example.com", "domain.com",
    "nordvpn.com", "expressvpn.com", "skillshare.com", "honey.com",
}

# The regex happily matches things like "logo@2x.png"; reject file extensions.
FILE_EXT_TLDS = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mp3", "wav", "pdf",
    "html", "htm", "php", "js", "css", "json", "zip", "ico", "txt", "xml",
}

NON_BUSINESS_PREFIXES = (
    "noreply@", "no-reply@", "donotreply@", "support@google", "abuse@",
    "postmaster@", "webmaster@", "test@", "email@example", "your@", "name@",
    "youremail@", "sample@",
)

SOURCE_CHANNEL = "频道简介"
SOURCE_VIDEO = "视频描述"
SOURCE_NONE = "未找到"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def _tld_of(email: str) -> str:
    return _domain_of(email).rsplit(".", 1)[-1]


def _is_non_business_email(email: str) -> bool:
    """Check if email is likely not a usable creator contact."""
    e = email.lower()
    if any(e.startswith(p) or p in e for p in NON_BUSINESS_PREFIXES):
        return True
    if _tld_of(e) in FILE_EXT_TLDS:
        return True
    domain = _domain_of(e)
    if domain in PLATFORM_DOMAINS:
        return True
    return any(domain.endswith("." + d) for d in PLATFORM_DOMAINS)


def _find_emails(text: str) -> List[str]:
    """Return deduped, noise-filtered emails preserving first-seen order."""
    if not text:
        return []
    seen = set()
    ordered: List[str] = []
    for raw in EMAIL_PATTERN.findall(text):
        email = raw.strip(".,;:")
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        if _is_non_business_email(email):
            continue
        ordered.append(email)
    return ordered


def _has_business_context(text: str, email: str, window: int = 150) -> bool:
    """True when business wording appears within ``window`` chars of the email."""
    lowered = text.lower()
    pos = lowered.find(email.lower())
    if pos < 0:
        return False
    start = max(0, pos - window)
    end = min(len(lowered), pos + len(email) + window)
    context = lowered[start:end]
    return any(kw in context for kw in BUSINESS_KEYWORDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_email(description: str) -> Optional[str]:
    """Extract the most likely business email from a single block of text.

    Kept for backwards compatibility with callers that only have the channel
    description available.
    """
    emails = _find_emails(description)
    if not emails:
        return None
    for email in emails:
        if _has_business_context(description, email):
            logger.debug(f"找到商务邮箱: {email}")
            return email
    logger.debug(f"未找到商务关键词上下文，使用第一个邮箱: {emails[0]}")
    return emails[0]


def extract_contact_email(
    channel_description: str = "",
    video_descriptions: Sequence[str] = (),
) -> Dict:
    """Pick the best contact email across the channel bio and video descriptions.

    Returns:
        {
            "contact_email":   str,   # "" when nothing usable was found
            "email_source":    str,   # 频道简介 / 视频描述 / 未找到
            "email_candidates": list, # other plausible addresses, best first
            "email_hit_videos": int,  # how many videos contained the winner
        }
    """
    result = {
        "contact_email": "",
        "email_source": SOURCE_NONE,
        "email_candidates": [],
        "email_hit_videos": 0,
    }

    # --- Tier 1 & 2: channel description ---
    channel_emails = _find_emails(channel_description)

    # --- Video descriptions: count how many distinct videos mention each ---
    video_counter: Counter = Counter()
    video_business: Counter = Counter()
    canonical: Dict[str, str] = {}
    for desc in video_descriptions or ():
        for email in _find_emails(desc):
            key = email.lower()
            canonical.setdefault(key, email)
            video_counter[key] += 1
            if _has_business_context(desc, email):
                video_business[key] += 1

    def _rank_video_emails() -> List[str]:
        # Sort by: business-context hits, then raw frequency, then alphabetically
        # so the ordering is deterministic for tests.
        keys = sorted(
            video_counter,
            key=lambda k: (-video_business[k], -video_counter[k], k),
        )
        return [canonical[k] for k in keys]

    ranked_video_emails = _rank_video_emails()

    winner = ""
    source = SOURCE_NONE

    # Tier 1: channel description with business wording.
    for email in channel_emails:
        if _has_business_context(channel_description, email):
            winner, source = email, SOURCE_CHANNEL
            break

    # Tier 2: channel description, any email.
    if not winner and channel_emails:
        winner, source = channel_emails[0], SOURCE_CHANNEL

    # Tier 3/4: fall back to video descriptions.
    if not winner and ranked_video_emails:
        winner, source = ranked_video_emails[0], SOURCE_VIDEO

    if not winner:
        return result

    candidates = [e for e in channel_emails if e.lower() != winner.lower()]
    candidates += [
        e for e in ranked_video_emails
        if e.lower() != winner.lower()
        and e.lower() not in {c.lower() for c in candidates}
    ]

    result.update({
        "contact_email": winner,
        "email_source": source,
        "email_candidates": candidates[:5],
        "email_hit_videos": video_counter.get(winner.lower(), 0),
    })
    logger.debug(
        f"邮箱命中 [{source}] {winner} (视频命中 {result['email_hit_videos']} 次, "
        f"候选 {len(result['email_candidates'])} 个)"
    )
    return result
