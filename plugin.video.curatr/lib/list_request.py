"""Shared, bounded list instructions independent of recommendation providers."""

import calendar
import re
from datetime import date, timedelta

from .request_text import mask_literals


CONTENT_TYPES = ("movies", "shows", "both", "episodes")
CONTENT_LABELS = ("Movies", "TV Shows", "Movies & TV Shows", "Episodes")
NUMBER_WORDS = dict(zip(("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"), range(1, 13)))
NUMBER = r"(\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
REFRESH_VERB = r"(?:updat(?:e[ds]?|ing)|refresh(?:ed|es|ing)?|regenerat(?:e[ds]?|ing))"
REFRESH_CADENCE = (r"\b" + REFRESH_VERB + r"(?:\s+\w+){0,2}\s+(?:daily|weekly|hourly|"
                   r"(?:every|each)\s+(?:" + NUMBER + r"\s+)?(?:hours?|days?|weeks?))\b")
HISTORY_KEYS = ("history_mode", "history_days", "history_plays", "history_comparison", "exclude_watched")
REQUEST_KEYS = ("content_type", "source", "window", "window_days", "refresh_mode", "refresh_hours", "hide_watched")
STALE_PATTERNS = (
    r"(?:haven't|have not|not)\s+(?:watched|seen|viewed)(?:\s+(?:it|them|these|this|anything))?\s+"
    r"(?:in|for|within)\s+(?:(?:the\s+)?(?:last|past)\s+)?" + NUMBER + r"\s+(days?|weeks?|months?|years?)",
    r"last\s+(?:watched|seen|viewed)\s+(?:over|more than|at least)?\s*" + NUMBER + r"\s+(days?|weeks?|months?|years?)\s+ago",
)


def without_history(value):
    """Keep history durations out of release dates and captured title/name text."""
    text = mask_literals(value)[0].replace("’", "'")
    prefix = r"\b(?:(?:that|which)\s+)?(?:(?:i|we)(?:'ve|\s+have)?\s+)?"
    patterns = STALE_PATTERNS + (
        r"(?:watched|seen|viewed)\s+(?:(?:it|them)\s+)?(?:(?:at least|more than|over|exactly|only|at most|fewer than|less than)\s+)?"
        + NUMBER + r"\s+(?:times?|plays?|viewings?)",
        r"(?:watched|seen|viewed)\s+(?:once|twice)\b",
        r"(?:unwatched|unseen|never\s+(?:watched|seen|viewed)|new to me|previously watched|already watched|watched before|seen before|rewatch(?:es)?|only watched)\b",
        r"(?:include|including|show|allow|hide|exclude|skip)\s+(?:already\s+)?(?:watched|seen)(?:\s+(?:items|films|movies|episodes))?\b",
        r"(?:haven't|have not|not)\s+(?:watched|seen|viewed)\s+(?:(?:it|them)\s+)?(?:before|yet)\b",
    )
    for pattern in patterns:
        text = re.sub(prefix + pattern, " ", text, flags=re.I)
    return " ".join(text.split()).strip(" ,.;")


def number(value):
    return int(value) if str(value).isdigit() else NUMBER_WORDS.get(str(value), 0)


def normal_text(value):
    return " ".join(str(value or "").casefold().replace("’", "'").replace("_", " ").split())


def parse_history(value):
    text = normal_text(mask_literals(value)[0])
    text = re.sub(r"\bonce\b", "one time", text)
    text = re.sub(r"\btwice\b", "two times", text)
    rules = dict(history_mode="", history_days=0, history_plays=0, history_comparison="", exclude_watched=True)
    stale = re.search(STALE_PATTERNS[0], text) or re.search(STALE_PATTERNS[1], text)
    if stale:
        unit = stale.group(2).rstrip("s")
        days = number(stale.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        if 0 < days <= 36500:
            rules.update(history_mode="stale", history_days=days, exclude_watched=False)
    plays = re.search(
        r"(?:watched|seen|viewed)\s+(?:(?:it|them)\s+)?(at least|more than|over|exactly|only|at most|fewer than|less than)?\s*"
        + NUMBER + r"\s+(?:times?|plays?|viewings?)", text,
    )
    if plays:
        count = number(plays.group(2))
        comparison = plays.group(1) or ("only" if not count or re.search(r"\bonly\s+(?:watched|seen|viewed)", text) else "at least")
        rules.update(history_mode="plays", history_plays=number(plays.group(2)),
                     history_comparison={"exactly": "exact", "only": "exact", "more than": "gt", "over": "gt", "at most": "lte", "fewer than": "lt", "less than": "lt"}.get(comparison, "gte"), exclude_watched=False)
        count, comparison = rules["history_plays"], rules["history_comparison"]
        if (count == 0 and comparison in ("exact", "lte")) or (count == 1 and comparison == "lt"):
            rules.update(history_mode="never", history_plays=0, history_comparison="", exclude_watched=True)
        elif count == 0 and comparison == "gte":
            rules.update(history_mode="include", history_plays=0, history_comparison="", exclude_watched=False)
    if not rules["history_mode"] and re.search(r"\b(?:previously watched|already watched|watched before|seen before|rewatch(?:es)?|only watched)\b", text):
        rules.update(history_mode="watched", exclude_watched=False)
    if re.search(r"\b(?:include|including|show|allow)\s+(?:already\s+)?(?:watched|seen)(?:\s+(?:items|films|movies))?\b", text):
        rules.update(history_mode="include", exclude_watched=False)
    if re.search(r"\b(?:unwatched|unseen|never\s+(?:watched|seen|viewed)|new to me)\b|\b(?:haven't|have not|not)\s+(?:watched|seen|viewed)\s+(?:(?:it|them)\s+)?(?:before|yet)\b|\b(?:hide|exclude|skip)\s+(?:already\s+)?watched\b", text):
        rules.update(history_mode="never", history_days=0, history_plays=0, history_comparison="", exclude_watched=True)
    return rules


def parse_request(value):
    text = normal_text(without_history(value))
    result = dict(content_type="", source="", window="", window_days=0,
                  refresh_mode="", refresh_hours=0, hide_watched=True)
    episodes = bool(re.search(r"\bepisodes?\b", text))
    if episodes:
        result.update(content_type="episodes", source="trakt", window="next_days", window_days=30)
    if episodes and re.search(r"\b(?:my )?kodi (?:library|shows)|\bfrom (?:my |the )?library\b", text):
        result["source"] = "kodi"
    elif episodes and re.search(r"\ball (?:shows|episodes)|\bevery (?:show|episode)\b", text):
        result["source"] = "all"
    elif episodes and re.search(r"\b(?:my shows|my episodes|followed shows|watchlist|trakt)\b", text):
        result["source"] = "trakt"
    amount = re.search(r"\b(?:next|past|last)\s+" + NUMBER + r"\s+(days?|weeks?|months?)", text)
    if amount:
        days = number(amount.group(1)) * {"day": 1, "week": 7, "month": 30}[amount.group(2).rstrip("s")]
        if days:
            result.update(window="past_days" if amount.group(0).startswith(("past", "last")) else "next_days", window_days=min(93, days))
    elif re.search(r"\bnext calendar month\b|\bduring next month\b", text):
        result.update(window="next_calendar_month", window_days=0)
    elif re.search(r"\b(?:next|coming) month\b", text):
        result.update(window="next_days", window_days=30)
    elif re.search(r"\b(?:this|current) (?:calendar )?month\b", text):
        result.update(window="this_calendar_month", window_days=0)
    elif re.search(r"\b(?:next|coming) week\b", text):
        result.update(window="next_days", window_days=7)
    if re.search(r"\b" + REFRESH_VERB + r"(?:\s+\w+){0,2}\s+(?:daily|every day|each day)\b", text):
        result.update(refresh_mode="on", refresh_hours=24)
    elif re.search(r"\b" + REFRESH_VERB + r"(?:\s+\w+){0,2}\s+weekly\b", text):
        result.update(refresh_mode="on", refresh_hours=168)
    elif re.search(r"\b" + REFRESH_VERB + r"(?:\s+\w+){0,2}\s+hourly\b", text):
        result.update(refresh_mode="on", refresh_hours=1)
    else:
        refresh = re.search(r"\b" + REFRESH_VERB + r"\s+(?:every|each)\s+" + NUMBER + r"\s+(hours?|days?|weeks?)", text)
        if refresh and number(refresh.group(1)):
            result.update(refresh_mode="on", refresh_hours=min(720, number(refresh.group(1)) * {"hour": 1, "day": 24, "week": 168}[refresh.group(2).rstrip("s")]))
    if re.search(r"\b(?:no|disable|without)\s+(?:auto(?:matic)?\s+)?(?:updates?|refresh)|\b(?:manual refresh|don't (?:update|refresh) automatically|(?:auto(?:matic)?\s+)?refresh\s+(?:off|disabled))\b", text):
        result.update(refresh_mode="off", refresh_hours=0)
    history = parse_history(value)
    result["hide_watched"] = history["exclude_watched"]
    return result


def operational_request(value):
    return bool(re.search(r"\b(?:episodes?|airing|unwatched|rewatch|" + REFRESH_VERB + r"|watchlist|next month|next week)\b", normal_text(mask_literals(value)[0])))


def validate_request(value):
    result = parse_request("")
    if not isinstance(value, dict):
        raise ValueError("The list instructions could not be understood.")
    enums = {"content_type": ("",) + CONTENT_TYPES, "source": ("", "trakt", "kodi", "all"),
             "window": ("", "next_days", "past_days", "next_calendar_month", "this_calendar_month"), "refresh_mode": ("", "on", "off")}
    for key, allowed in enums.items():
        if value.get(key, "") not in allowed:
            raise ValueError("Unsupported list instruction: %s" % key)
        result[key] = value.get(key, "")
    for key, maximum in (("window_days", 93), ("refresh_hours", 720)):
        amount = value.get(key, 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= maximum:
            raise ValueError("Invalid list instruction: %s" % key)
        result[key] = amount
    if not isinstance(value.get("hide_watched", True), bool):
        raise ValueError("Invalid watched-item instruction.")
    result["hide_watched"] = value.get("hide_watched", True)
    if result["content_type"] == "episodes":
        if result["window"] in ("next_days", "past_days"):
            result["window_days"] = result["window_days"] or 30
    elif result["content_type"]:
        result["source"] = ""
    if result["refresh_mode"] == "on" and not result["refresh_hours"]:
        result["refresh_hours"] = 24
    return result


def date_window(rules, today=None):
    today = today or date.today()
    mode = rules.get("window") or "next_days"
    days = max(1, min(93, int(rules.get("window_days") or 30)))
    if mode == "past_days":
        return today - timedelta(days=days - 1), today + timedelta(days=1)
    if mode in ("next_calendar_month", "this_calendar_month"):
        year, month = today.year, today.month
        if mode == "next_calendar_month":
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        start = date(year, month, 1)
        return start, start + timedelta(days=calendar.monthrange(year, month)[1])
    return today, today + timedelta(days=days)


def request_summary(rules, include_refresh=True):
    parts = []
    episodes = rules.get("content_type") == "episodes"
    if episodes and rules.get("source"):
        parts.append({"text": {"trakt": "Trakt watched/watchlisted shows", "kodi": "Kodi library shows", "all": "all shows"}[rules["source"]], "kind": "place", "connector": "from", "field": "episode_source"})
    if rules.get("window"):
        start, end = date_window(rules)
        parts.append({"text": "%s to %s" % (start.isoformat(), (end - timedelta(days=1)).isoformat()), "kind": "year", "connector": "airing" if episodes else "released", "field": "airing_window"})
    if include_refresh and rules.get("refresh_mode"):
        parts.append({"text": "Off" if rules["refresh_mode"] == "off" else "every %d hours" % rules["refresh_hours"], "kind": "number", "connector": "refresh", "field": "refresh"})
    return parts


def release_matches(value, rules):
    if not rules.get("window"):
        return True
    try:
        released = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return False
    start, end = date_window(rules)
    return start <= released < end
