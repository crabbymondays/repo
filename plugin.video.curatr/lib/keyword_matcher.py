"""Deterministic prompt parsing and ranking for curatr's no-AI list mode."""

import re
import time

from .list_request import REFRESH_CADENCE, parse_history, parse_request, release_matches, request_summary, without_history
from .request_text import mask_literals, restore_literals

PARSER_VERSION = 16
MAX_REFERENCES = 3


class NoKeywordMatches(RuntimeError):
    """A recoverable empty result; the interactive creator keeps its draft."""

GENRES = {
    "action": (28, "Action"), "adventure": (12, "Adventure"),
    "animation": (16, "Animation"), "animated": (16, "Animation"), "anime": (16, "Animation"),
    "comedy": (35, "Comedy"), "comedies": (35, "Comedy"), "funny": (35, "Comedy"),
    "crime": (80, "Crime"), "documentary": (99, "Documentary"),
    "documentaries": (99, "Documentary"), "drama": (18, "Drama"),
    "family": (10751, "Family"), "fantasy": (14, "Fantasy"),
    "history": (36, "History"), "historical": (36, "History"),
    "horror": (27, "Horror"), "music": (10402, "Music"),
    "musical": (10402, "Music"), "mystery": (9648, "Mystery"),
    "romance": (10749, "Romance"), "romantic": (10749, "Romance"),
    "science fiction": (878, "Sci-Fi"), "sci fi": (878, "Sci-Fi"),
    "sci-fi": (878, "Sci-Fi"), "thriller": (53, "Thriller"),
    "thrillers": (53, "Thriller"), "war": (10752, "War"),
    "western": (37, "Western"), "westerns": (37, "Western"),
}

THEMES = {
    "atmospheric": ("atmospheric", "Atmospheric"), "bleak": ("bleak", "Bleak"),
    "dark": ("dark", "Dark"), "darkly funny": ("dark humour", "Dark humour"),
    "dark humor": ("dark humour", "Dark humour"), "dark humour": ("dark humour", "Dark humour"),
    "dystopian": ("dystopian", "Dystopian"), "feel good": ("feel-good", "Feel-good"),
    "feel-good": ("feel-good", "Feel-good"), "heist": ("heist", "Heist"),
    "mind bending": ("mind-bending", "Mind-bending"), "mind-bending": ("mind-bending", "Mind-bending"),
    "psychological": ("psychological", "Psychological"), "revenge": ("revenge", "Revenge"),
    "serial killer": ("serial killer", "Serial killer"), "supernatural": ("supernatural", "Supernatural"),
    "suspenseful": ("suspense", "Suspenseful"), "tense": ("tense", "Tense"),
    "uplifting": ("uplifting", "Uplifting"), "coming of age": ("coming of age", "Coming of age"),
}

LANGUAGES = {
    "english": ("en", "English"), "french": ("fr", "French"), "spanish": ("es", "Spanish"),
    "german": ("de", "German"), "italian": ("it", "Italian"), "japanese": ("ja", "Japanese"),
    "korean": ("ko", "Korean"), "chinese": ("zh", "Chinese"), "hindi": ("hi", "Hindi"),
    "swedish": ("sv", "Swedish"), "danish": ("da", "Danish"), "norwegian": ("no", "Norwegian"),
}

COUNTRIES = {
    "british": ("GB", "British"), "uk": ("GB", "British"), "american": ("US", "American"),
    "us": ("US", "American"), "french": ("FR", "French"), "spanish": ("ES", "Spanish"),
    "german": ("DE", "German"), "italian": ("IT", "Italian"), "japanese": ("JP", "Japanese"),
    "korean": ("KR", "Korean"), "canadian": ("CA", "Canadian"), "australian": ("AU", "Australian"),
}

SOURCE_ALIASES = {
    "imdb": ("imdb", "IMDb"),
    "rotten tomatoes": ("tomatoes", "Rotten Tomatoes"),
    "rottentomatoes": ("tomatoes", "Rotten Tomatoes"),
    "tomatometer": ("tomatoes", "Rotten Tomatoes"),
    "popcornmeter": ("popcorn", "Rotten Tomatoes Audience"),
    "mdb list": ("mdblist", "MDBList"),
    "mdblist": ("mdblist", "MDBList"),
}

_REFERENCE_FILTER_ALIASES = sorted(
    set(GENRES) | set(THEMES) | set(LANGUAGES) | set(COUNTRIES) | set(SOURCE_ALIASES)
    | {"top", "best"},
    key=len,
    reverse=True,
)
_REFERENCE_FILTER_PATTERN = "|".join(re.escape(alias) for alias in _REFERENCE_FILTER_ALIASES)
_GENRE_PATTERN = "|".join(re.escape(alias) for alias in sorted(GENRES, key=len, reverse=True))
_CREATOR_CONTEXT_PATTERN = "|".join(re.escape(alias) for alias in sorted(
    set(GENRES) | set(THEMES) | set(LANGUAGES) | set(COUNTRIES)
    | {"titles", "items", "recommendations", "anything", "best", "popular", "latest"},
    key=len, reverse=True,
))
_PERSON_MARKERS = r"(?:directed\s+by|written\s+by|films?\s+by|movies?\s+by|starring|featuring|with\s+(?:the\s+)?actors?|from\s+(?:the\s+)?directors?)"
_REFERENCE_BOUNDARY = (r"(?:\s*[,;]\s*|\s+)(?:(?:and|but)\s+)?" + _PERSON_MARKERS + r"\b|"
                       r"(?:\s*[,;]\s*|\s+and\s+)(?:like|similar\s+to)\b")
_PERSON_BOUNDARY = r"(?:\s*[,;]\s*|\s+)(?:(?:and|but)\s+)?(?:(?:films?|movies?)\s+)?(?:similar\s+to|like|" + _PERSON_MARKERS + r")\b"


def _contains(text, phrase):
    return bool(re.search(r"(?<!\w)%s(?!\w)" % re.escape(phrase), text, re.I))


def _clean_reference(value, person=False):
    value = re.split(_PERSON_BOUNDARY if person else _REFERENCE_BOUNDARY, str(value or ""), maxsplit=1, flags=re.I)[0]
    value = re.sub(
        r"\s+(?:(?:and|but)\s+)?(?:(?:films?|movies?)\s+)?(?:releas(?:ed|ing)\b|airing\b|(?:sort(?:ed)?|order(?:ed)?)\s+(?:by\s+)?(?:release date|date|year|newest|latest|popularity|popular|rating|highest rated)\b|(?:rated|rating|score|with a rating)\s+(?:(?:of|above|over|at least)\s+)?\d|(?:from|between|since|before|after|until|up to)\s+(?:19|20)\d{2}\b|(?:under|over|less than|more than|shorter than|longer than)\s+\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)\b|with (?:good reviews|high ratings)\b|(?:that|which) (?:are|aren't|are not)\b|but\b).*$",
        "", value, flags=re.I,
    )
    for match in re.finditer(r"\s+(?:(?:and|with|in)\s+)(?:%s)\b" % _REFERENCE_FILTER_PATTERN, value, re.I):
        value = value[:match.start()]
        break
    if person:
        suffix = re.search(r"\s+(?:%s)\b" % _GENRE_PATTERN, value, re.I)
        if suffix and len(value[:suffix.start()].split()) >= 2:
            value = value[:suffix.start()]
    return re.sub(r"\s+(?:and|but)\s*$", "", value, flags=re.I).strip(" ,.;:-")


def _split_references(value, maximum=MAX_REFERENCES):
    value = _clean_reference(value)
    if not value:
        return []
    pieces, seen = [], set()
    for part in re.split(r"\s*(?:,|\band\b|&)\s*", value, flags=re.I):
        part = part.strip()
        if part and part.casefold() not in seen:
            pieces.append(part); seen.add(part.casefold())
    return pieces if maximum is None else pieces[:maximum]


def _capture(text, patterns, person=True):
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _clean_reference(match.group(1), person=person)
    return ""


def _looks_like_cast_reference(value, literals=None):
    """Accept short natural person names without treating ordinary qualities as cast."""
    candidate = _clean_reference(value)
    pieces = _split_references(candidate)
    if pieces and literals and all(part in literals and literals[part] for part in pieces):
        return True
    words = candidate.split()
    if not words or len(words) > 8 or any(any(char.isdigit() for char in word) for word in words):
        return False
    lowered = candidate.casefold()
    if words[0].casefold() in ("a", "an"):
        return False
    if any(_contains(lowered, alias) for alias in GENRES) or any(_contains(lowered, alias) for alias in THEMES):
        return False
    blocked = (
        "rating", "score", "runtime", "subtitles", "dubbing", "twist", "ending",
        "violence", "action scenes", "good reviews", "high ratings", "low ratings",
        "strong reviews", "great reviews", "positive reviews", "dark atmosphere",
        "happy ending", "sad ending", "fast pace", "slow pace",
    )
    return not any(_contains(lowered, phrase) for phrase in blocked)


def _normalise_creator_clauses(source):
    """Allow a bare 'by' after catalogue filters without changing reference titles."""
    reference = re.search(r"\b(?:like|similar\s+to)\s+", source, re.I)
    def replace(match):
        prefix = source[:match.start()]
        previous = prefix.rstrip().rsplit(None, 1)[-1].casefold() if prefix.strip() else ""
        if previous in ("directed", "written", "film", "films", "movie", "movies", "used",
                        "sort", "sorted", "order", "ordered", "rank", "ranked", "filter", "filtered", "group", "grouped",
                        "not", "except", "excluding", "exclude", "without"):
            return match.group(0)
        candidate = _clean_reference(source[match.end():], person=True).casefold()
        if candidate in ("rating", "score", "year", "date", "runtime", "votes", "popularity", "title", "genre", "country", "language"):
            return match.group(0)
        if reference and match.start() > reference.start():
            return match.group(0)
        if re.search(r"(?<!\w)(?:%s)(?!\w)" % _CREATOR_CONTEXT_PATTERN, prefix, re.I):
            return "films by "
        return match.group(0)
    return re.sub(r"\bby\s+", replace, source, flags=re.I)


def _identity_filters(source, rules, literals):
    """Read names/titles first, so their words cannot become catalogue filters."""
    source = _normalise_creator_clauses(source)
    reverse_by = re.search(r"\b(?:like|similar\s+to)\s+.+?\s+(by)\s+(.+)$", source, re.I)
    by_prefix = source[:reverse_by.start(1)].rstrip().rsplit(None, 1)[-1].casefold() if reverse_by else ""
    if reverse_by and by_prefix not in ("sort", "sorted", "order", "ordered", "rank", "ranked", "filter", "filtered", "group", "grouped", "not", "except", "excluding", "exclude", "without") and not re.search(_PERSON_MARKERS, source, re.I):
        name = _clean_reference(reverse_by.group(2), person=True)
        literal_name = name in literals and bool(literals[name])
        if (literal_name or len(name.split()) >= 2) and not re.match(r"(?:a|an|the)\b", name, re.I) and _looks_like_cast_reference(name, literals):
            source = source[:reverse_by.start(1)] + "films by" + source[reverse_by.end(1):]
    recurring = _capture(source, [
        r"\b(?:actors?|cast|collaborators?)\s+(?:often|frequently|commonly)\s+(?:used by|working with)\s+(.+)$",
        r"\b(?:recurring|frequent)\s+(?:actors?|cast|collaborators?)\s+(?:of|for|with)\s+(.+)$",
    ])
    similar = (
        (_capture(source, [
            r"\b(?:similar to|like)\s+(?:films?|movies?)\s+by\s+(.+)$",
            r"\b(?:films?|movies?)\s+by\s+(?:directors?|filmmakers?)\s+(?:similar to|like)\s+(.+)$",
            r"\b(?:directors?|filmmakers?)\s+(?:similar to|like)\s+(.+)$",
            r"\b(?:films?|movies?)\s+in the style of\s+(.+)$",
        ]), "director"),
        (_capture(source, [r"\b(?:actors?|performers?)\s+(?:similar to|like)\s+(.+)$"]), "cast"),
        (_capture(source, [r"\b(?:cinematography|screenplays?|writing|creative work)\s+(?:similar to|like)\s+(.+)$"]), "crew"),
    )
    if recurring:
        rules["people"] = [{"query": name, "role": "director"} for name in _split_references(recurring, maximum=None)]
        rules["strategy"] = "recurring_collaborators"
    else:
        for value, role in similar:
            if value:
                rules["people"] = [{"query": name, "role": role} for name in _split_references(value, maximum=None)]
                rules["strategy"] = "similar_people"
                break
        if not rules["people"]:
            marker = r"(?:\b(directed\s+by|from\s+(?:the\s+)?directors?|written\s+by|films?\s+by|movies?\s+by|starring|featuring|with\s+(?:the\s+)?actors?)|^\s*(by))\s+"
            for match in re.finditer(marker, source, re.I):
                marker_text = match.group(1) or match.group(2)
                role = "cast" if re.match(r"starring|featuring|with", marker_text, re.I) else ("director" if re.match(r"directed|from", marker_text, re.I) else ("writer" if re.match(r"written", marker_text, re.I) else "crew"))
                value = _clean_reference(source[match.end():], person=True)
                rules["people"].extend({"query": name, "role": role} for name in _split_references(value, maximum=None))
            if not rules["people"]:
                natural = _capture(source, [r"\bwith\s+(.+)$"])
                if _looks_like_cast_reference(natural, literals):
                    rules["people"] = [{"query": name, "role": "cast"} for name in _split_references(natural)]
            if rules["people"]:
                rules["strategy"] = "exact_people"
    if rules["strategy"] not in ("similar_people", "recurring_collaborators"):
        references, consumed = [], 0
        for match in re.finditer(r"\b(?:like|similar\s+to)\s+", source, re.I):
            if match.start() < consumed:
                continue
            value = _clean_reference(source[match.end():], person=False)
            consumed = match.end() + len(value)
            references.extend(_split_references(value, maximum=None))
        rules["reference_movies"] = [{"title": title, "year": 0} for title in references]
        if references:
            rules["strategy"] = "reference_people" if rules["people"] else "similar_films"
    collection = _capture(source, [
        r"\b(?:from|in)\s+(?:the\s+)?(.+?)\s+(?:collection|franchise|saga|trilogy)\b",
        r"^\s*(?:the\s+)?(.+?)\s+(?:collection|franchise|saga|trilogy)\b",
        r"^\s*all\s+(.+?)\s+(?:films?|movies?)\s*$",
    ], person=False)
    if collection and not rules["people"] and not rules["reference_movies"]:
        lowered = collection.casefold()
        if not any(_contains(lowered, alias) for alias in GENRES) and not any(_contains(lowered, alias) for alias in THEMES) and not any(_contains(lowered, alias) for alias in COUNTRIES):
            rules.update(collection_query=collection, collection_name=collection, strategy="collection")
    names = [person["query"] for person in rules["people"]]
    titles = [movie["title"] for movie in rules["reference_movies"]]
    return names + titles + ([rules["collection_query"]] if rules["collection_query"] else [])


def _without_identities(source, identities):
    for identity in sorted(set(identities), key=len, reverse=True):
        source = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(identity), " ", source, flags=re.I)
    return source


def _restore_identities(rules, literals):
    for key, field in (("people", "query"), ("reference_movies", "title")):
        rows, seen = [], set()
        for row in rules[key]:
            if field == "title":
                year = re.search(r"\s+\(((?:19|20)\d{2})\)$", row[field])
                if year:
                    row["year"] = int(year.group(1))
                    row[field] = row[field][:year.start()]
            row[field] = restore_literals(row[field], literals).strip()
            marker = (row[field].casefold(), row.get("role") if field == "query" else row.get("year", 0))
            if row[field] and marker not in seen:
                rows.append(row); seen.add(marker)
        rules[key] = rows[:MAX_REFERENCES]
    if rules["people"]:
        rules["person_query"] = rules["people"][0]["query"]
        rules["person_role"] = rules["people"][0]["role"]
    if rules["reference_movies"]:
        rules["reference_title"] = rules["reference_movies"][0]["title"]
    for key in ("collection_query", "collection_name"):
        rules[key] = restore_literals(rules[key], literals)


def _genre_filters(text, rules):
    negative = r"\b(?:without|except|excluding|exclude|no|avoid)\s+(?:any\s+)?"
    group = r"(?:%s)\b(?:\s*(?:,|and|or|&)\s*(?:%s)\b)*" % (_GENRE_PATTERN, _GENRE_PATTERN)
    excluded_text = " ".join(match.group(0) for match in re.finditer(negative + group, text, re.I))
    positive_text = re.sub(negative + group, " ", text, flags=re.I)
    for alias, (genre_id, label) in sorted(GENRES.items(), key=lambda row: -len(row[0])):
        if _contains(excluded_text, alias):
            if genre_id not in rules["excluded_genres"]:
                rules["excluded_genres"].append(genre_id); rules["excluded_genre_labels"].append(label)
        elif _contains(positive_text, alias) and genre_id not in rules["genres"]:
            rules["genres"].append(genre_id); rules["genre_labels"].append(label)
    if len(rules["genres"]) > 1 and re.search(r"\b(?:%s)\s+or\s+(?:%s)\b" % (_GENRE_PATTERN, _GENRE_PATTERN), positive_text):
        rules["genre_match"] = "any"


def _rating_value(value, unit, percent_scale=False):
    value = float(value)
    if unit == "%" and not percent_scale:
        value /= 10
    elif unit == "/10" and percent_scale:
        value *= 10
    return max(0.0, min(100.0 if percent_scale else 10.0, value))


def validate_filters(rules):
    for prefix, label in (("year", "release year"), ("runtime", "runtime")):
        lower, upper = rules.get(prefix + "_min") or 0, rules.get(prefix + "_max") or 0
        if lower and upper and lower > upper:
            raise ValueError("The minimum %s is higher than the maximum. Edit or remove that filter." % label)


def parse_prompt(prompt, current_year=None):
    original, literals = mask_literals(prompt)
    original = " ".join(original.replace("_", " ").split())
    person_source = without_history(original)
    person_source = re.sub(
        REFRESH_CADENCE,
        " ", person_source, flags=re.I,
    ).strip(" ,.;")
    person_source = re.sub(r"(?:\s+and\s*)+$", "", person_source, flags=re.I).strip(" ,.;")
    current_year = int(current_year or time.localtime().tm_year)
    rules = {
        "version": PARSER_VERSION, "strategy": "filtered_discover",
        "genres": [], "genre_labels": [], "themes": [], "theme_labels": [],
        "year_min": 0, "year_max": 0, "runtime_min": 0, "runtime_max": 0,
        "rating_min": 0.0, "language": "", "language_label": "",
        "country": "", "country_label": "", "people": [], "reference_movies": [],
        "person_query": "", "person_role": "", "reference_title": "",
        "sort": "balanced", "exclude_watched": True,
        "avoid_mainstream": False, "prefer_blockbusters": False,
        "external_source": "", "external_source_label": "",
        "external_chart_limit": 0, "external_rating_min": 0.0,
        "collection_query": "", "collection_name": "",
        "history_mode": "", "history_days": 0,
        "history_plays": 0, "history_comparison": "",
        "excluded_genres": [], "excluded_genre_labels": [], "genre_match": "all",
        "request_rules": {},
    }

    identities = _identity_filters(person_source, rules, literals)
    text = _without_identities(person_source, identities).casefold()
    instruction_source = _without_identities(original, identities)
    rules["request_rules"] = parse_request(instruction_source)
    rules.update(parse_history(instruction_source))

    _genre_filters(text, rules)
    for alias, (term, label) in sorted(THEMES.items(), key=lambda row: -len(row[0])):
        if _contains(text, alias) and term not in rules["themes"]:
            rules["themes"].append(term); rules["theme_labels"].append(label)

    decade = re.search(r"\b((?:19|20)\d)0s\b", text)
    short_decade = re.search(r"\b([4-9]0)s\b", text)
    year_range = re.search(r"\b((?:19|20)\d{2})\s*(?:-|to|through|and)\s*((?:19|20)\d{2})\b", text)
    if year_range:
        rules["year_min"], rules["year_max"] = sorted(map(int, year_range.groups()))
    elif decade:
        rules["year_min"] = int(decade.group(1) + "0"); rules["year_max"] = rules["year_min"] + 9
    elif short_decade:
        rules["year_min"] = 1900 + int(short_decade.group(1)); rules["year_max"] = rules["year_min"] + 9
    else:
        match = re.search(r"\b(since|after|from)\s+((?:19|20)\d{2})\b", text)
        if match: rules["year_min"] = int(match.group(2)) + (match.group(1) == "after")
        match = re.search(r"\b(before|until|up to)\s+((?:19|20)\d{2})\b", text)
        if match: rules["year_max"] = int(match.group(2)) - (match.group(1) == "before")
        recent = re.search(r"\b(?:last|past)\s+(\d{1,2})\s+years?\b", text)
        if recent:
            rules["year_min"] = max(1900, current_year - int(recent.group(1)) + 1); rules["year_max"] = current_year

    runtime_range = re.search(r"\bbetween\s+(\d{1,5}(?:\.\d{1,3})?)\s+and\s+(\d{1,5}(?:\.\d{1,3})?)\s*(hours?|hrs?|minutes?|mins?)\b", text)
    if runtime_range:
        scale = 60 if runtime_range.group(3).startswith(("hour", "hr")) else 1
        rules["runtime_min"], rules["runtime_max"] = sorted(int(float(value) * scale) for value in runtime_range.group(1, 2))
    runtime = re.search(r"\b(?:under|less than|shorter than|up to)\s+(\d{1,5}(?:\.\d{1,3})?)\s*(hours?|hrs?|minutes?|mins?)\b", text)
    if runtime:
        value = float(runtime.group(1)); rules["runtime_max"] = int(value * 60 if runtime.group(2).startswith(("hour", "hr")) else value)
    runtime = re.search(r"\b(?:over|more than|longer than|at least)\s+(\d{1,5}(?:\.\d{1,3})?)\s*(hours?|hrs?|minutes?|mins?)\b", text)
    if runtime:
        value = float(runtime.group(1)); rules["runtime_min"] = int(value * 60 if runtime.group(2).startswith(("hour", "hr")) else value)

    source_pattern = "|".join(re.escape(value) for value in sorted(SOURCE_ALIASES, key=len, reverse=True))
    chart = re.search(
        r"\b(?:top|best)\s+(\d{1,3})\s+(?:films?|movies?)?\s*(?:on|from|according to)?\s*(%s)\b" % source_pattern,
        text,
    )
    if not chart:
        chart = re.search(r"\b(%s)\s+(?:top|best)\s+(\d{1,3})\b" % source_pattern, text)
        if chart:
            source_name, chart_size = chart.group(1), chart.group(2)
        else:
            source_name, chart_size = "", ""
    else:
        chart_size, source_name = chart.group(1), chart.group(2)
    if source_name:
        source, source_label = SOURCE_ALIASES[source_name]
        rules["external_source"], rules["external_source_label"] = source, source_label
        rules["external_chart_limit"] = max(1, min(250, int(chart_size)))

    source_rating = re.search(
        r"\b(%s)\s*(?:rating|score)?\s*(?:of|above|over|at least|rated)?\s*(\d{1,5}(?:\.\d{1,3})?)(?![\d.])\s*(%%|/10)?" % source_pattern,
        text,
    )
    if source_rating and not rules["external_source"]:
        source, source_label = SOURCE_ALIASES[source_rating.group(1)]
        value = _rating_value(source_rating.group(2), source_rating.group(3), source in ("tomatoes", "popcorn"))
        rules["external_source"], rules["external_source_label"] = source, source_label
        rules["external_rating_min"] = value

    rating_text = text
    if source_rating:
        rating_text = rating_text[:source_rating.start()] + rating_text[source_rating.end():]
    rating = re.search(r"\b(?:rated|rating|score)\s*(?:of|above|over|at least)?\s*(\d{1,5}(?:\.\d{1,3})?)(?![\d.])\s*(%|/10|\+)?", rating_text)
    if rating: rules["rating_min"] = _rating_value(rating.group(1), rating.group(2))
    elif "highly rated" in text or "best rated" in text: rules["rating_min"] = 7.0

    for label, (code, display) in LANGUAGES.items():
        if _contains(text, label) and ("language" in text or "in %s" % label in text):
            rules["language"], rules["language_label"] = code, display; break
    country_text = re.sub(r"\bin\s+(?:%s)\b" % "|".join(LANGUAGES), " ", text)
    for label, (code, display) in COUNTRIES.items():
        if label == "us" and not re.search(r"\b(?:from|country)\s+(?:the\s+)?us\b|\bus\s+(?:films?|movies?|shows?)\b", country_text):
            continue
        if _contains(country_text, label): rules["country"], rules["country_label"] = code, display; break

    _restore_identities(rules, literals)

    avoid_terms = (
        "less mainstream", "not mainstream", "avoid blockbusters", "not huge blockbusters",
        "aren't huge blockbusters", "are not huge blockbusters", "not blockbusters", "smaller films",
    )
    rules["avoid_mainstream"] = any(term in text for term in avoid_terms)
    rules["prefer_blockbusters"] = any(term in text for term in ("blockbusters", "blockbuster", "big budget")) and not rules["avoid_mainstream"]
    ordering = re.search(r"\b(?:sort(?:ed)?|order(?:ed)?)\s+(?:by\s+)?(release date|date|year|newest|latest|popularity|popular|rating|highest rated)\b", text)
    if ordering: rules["sort"] = {"popularity": "popular", "popular": "popular", "rating": "rated", "highest rated": "rated"}.get(ordering.group(1), "recent")
    elif any(term in text for term in ("newest", "latest", "recent")): rules["sort"] = "recent"
    elif rules["prefer_blockbusters"] or any(term in text for term in ("popular", "well known", "mainstream")): rules["sort"] = "popular"
    elif rules["avoid_mainstream"]: rules["sort"] = "less_mainstream"
    elif rules["rating_min"] or any(term in text for term in ("best", "greatest", "acclaimed")): rules["sort"] = "rated"
    meaningful = sum(bool(rules[key]) for key in (
        "genres", "themes", "year_min", "year_max", "runtime_min", "runtime_max", "rating_min",
        "language", "country", "people", "reference_movies", "avoid_mainstream", "prefer_blockbusters",
        "external_source", "external_chart_limit", "external_rating_min",
        "collection_query", "history_mode", "excluded_genres",
    ))
    meaningful += bool(request_summary(rules["request_rules"], include_refresh=False))
    meaningful += rules["sort"] != "balanced"
    rules["confidence"] = min(1.0, meaningful / 3.0)
    return rules


def confirmation_parts(rules):
    parts = []
    if rules.get("external_source_label"):
        if rules.get("external_chart_limit"):
            value = "%s Top %d" % (rules["external_source_label"], int(rules["external_chart_limit"]))
        elif rules.get("external_rating_min"):
            suffix = "%" if rules.get("external_source") in ("tomatoes", "popcorn") else "+"
            value = "%s %.1f%s" % (rules["external_source_label"], float(rules["external_rating_min"]), suffix)
        else:
            value = rules["external_source_label"]
        parts.append({"text": value.replace(".0+", "+").replace(".0%", "%"), "kind": "number", "connector": "from", "field": "external"})
    if rules.get("collection_query"):
        parts.append({"text": rules.get("collection_name") or rules.get("collection_query"), "kind": "film", "connector": "collection", "field": "collection"})
    if rules.get("country_label"):
        parts.append({"text": rules["country_label"], "kind": "place", "connector": "", "field": "country"})
    genre_text = (" or " if rules.get("genre_match") == "any" else " and ").join(rules.get("genre_labels") or [])
    genre_text = ", ".join(list(rules.get("theme_labels") or []) + ([genre_text] if genre_text else []))
    if genre_text: parts.append({"text": genre_text, "kind": "genre", "connector": "", "field": "genres"})
    people = rules.get("people") or []
    if people:
        strategy, previous_role = rules.get("strategy"), ""
        for index, person in enumerate(people):
            role = person.get("role")
            if index and role == previous_role:
                connector = "and"
            elif strategy == "similar_people":
                connector = "similar to films by" if role == "director" else ("similar to work by" if role == "crew" else "similar to")
            elif strategy == "recurring_collaborators":
                connector = "using recurring collaborators of"
            else:
                connector = "directed by" if role == "director" else ("written by" if role == "writer" else ("by" if role == "crew" else "starring"))
            parts.append({"text": person.get("name") or person.get("query") or "", "kind": "person", "connector": connector, "field": "person", "index": index})
            previous_role = role
    for index, movie in enumerate(rules.get("reference_movies") or []):
        parts.append({"text": movie.get("title") or "", "kind": "film", "connector": "similar to" if index == 0 else "and", "field": "reference", "index": index})
    if rules.get("language_label"):
        parts.append({"text": rules["language_label"], "kind": "place", "connector": "in", "field": "language"})
    if rules.get("year_min") or rules.get("year_max"):
        if rules.get("year_min") and rules.get("year_max"): value, connector = "%s-%s" % (rules["year_min"], rules["year_max"]), "from"
        elif rules.get("year_min"): value, connector = "%s onwards" % rules["year_min"], "from"
        else: value, connector = "up to %s" % rules["year_max"], "released"
        parts.append({"text": value, "kind": "year", "connector": connector, "field": "year"})
    if rules.get("rating_min"): parts.append({"text": "%.1f+" % float(rules["rating_min"]), "kind": "number", "connector": "rated", "field": "rating"})
    if rules.get("runtime_min") and rules.get("runtime_max"): parts.append({"text": "%d and %d minutes" % (rules["runtime_min"], rules["runtime_max"]), "kind": "runtime", "connector": "between", "field": "runtime"})
    elif rules.get("runtime_max"): parts.append({"text": "%d minutes" % int(rules["runtime_max"]), "kind": "runtime", "connector": "under", "field": "runtime"})
    elif rules.get("runtime_min"): parts.append({"text": "%d minutes" % int(rules["runtime_min"]), "kind": "runtime", "connector": "over", "field": "runtime"})
    if rules.get("avoid_mainstream"): parts.append({"text": "less mainstream", "kind": "genre", "connector": "favouring", "field": "mainstream"})
    elif rules.get("prefer_blockbusters"): parts.append({"text": "major blockbusters", "kind": "genre", "connector": "favouring", "field": "mainstream"})
    if rules.get("sort") in ("recent", "popular", "rated"):
        parts.append({"text": {"recent": "release date", "popular": "popularity", "rated": "rating"}[rules["sort"]], "kind": "number", "connector": "sorted by", "field": "sort"})
    if rules.get("history_mode") == "stale":
        days = int(rules.get("history_days") or 0)
        if days % 365 == 0: value = "over %d years ago" % max(1, days // 365)
        elif days % 30 == 0: value = "over %d months ago" % max(1, days // 30)
        elif days % 7 == 0: value = "over %d weeks ago" % max(1, days // 7)
        else: value = "over %d days ago" % days
        parts.append({"text": value, "kind": "year", "connector": "last watched", "field": "history"})
    elif rules.get("history_mode") == "plays":
        comparison = {"gt": "more than", "exact": "exactly", "lte": "at most", "lt": "fewer than"}.get(rules.get("history_comparison"), "at least")
        parts.append({"text": "%s %d times" % (comparison, int(rules.get("history_plays") or 0)), "kind": "number", "connector": "watched", "field": "history"})
    elif rules.get("history_mode") == "never":
        parts.append({"text": "before", "kind": "year", "connector": "not watched", "field": "history"})
    elif rules.get("history_mode") in ("watched", "include"):
        parts.append({"text": "watched items", "kind": "year", "connector": "only" if rules["history_mode"] == "watched" else "including", "field": "history"})
    if rules.get("excluded_genre_labels"):
        parts.append({"text": ", ".join(rules["excluded_genre_labels"]), "kind": "genre", "connector": "without", "field": "excluded_genres"})
    parts.extend(request_summary(rules.get("request_rules") or {}, include_refresh=False))
    return [part for part in parts if part.get("text")]


def format_rules(rules):
    fragments = ["%s %s" % (part.get("connector") or "", part.get("text") or "") for part in confirmation_parts(rules)]
    sentence = " ".join(fragment.strip() for fragment in fragments if fragment.strip()).strip()
    if sentence: sentence = sentence[0].upper() + sentence[1:]
    exclusions = "Watched, rated and hidden items will be excluded." if rules.get("exclude_watched", True) else "Hidden items will be excluded; watched titles are allowed."
    return (sentence or "Your recognised filters") + "\n\n" + exclusions + "\nNo AI will be used."


def confirmation_confidence(rules):
    """An episode calendar remains usable after removing its optional filters."""
    meaningful = len(confirmation_parts(rules))
    if (rules.get("request_rules") or {}).get("content_type") == "episodes":
        meaningful = max(1, meaningful)
    return min(1.0, meaningful / 3.0)


def preferred_genre_ids(profile):
    label_to_id = {label.casefold(): genre_id for genre_id, label in GENRES.values()}
    weights = {}
    for row in (profile or {}).get("strong_likes", []):
        if isinstance(row, dict):
            weight = max(1, int(row.get("rating") or 8) - 6)
            for label in row.get("genres") or []:
                genre_id = label_to_id.get(str(label).casefold())
                if genre_id: weights[genre_id] = weights.get(genre_id, 0) + weight
    return weights


def score_candidate(movie, rules, preference_weights=None, analysis=None):
    overview = (str(movie.get("title") or "") + " " + str(movie.get("overview") or "")).lower()
    theme_hits = sum(1 for term in rules.get("themes", []) if term.replace("-", " ") in overview)
    rating, votes, popularity = float(movie.get("rating") or 0), max(0, int(movie.get("votes") or 0)), float(movie.get("popularity") or 0)
    genre_ids = movie.get("genre_ids") or []
    preference_bonus = sum((preference_weights or {}).get(genre_id, 0) for genre_id in genre_ids)
    analysis_bonus = sum(((analysis or {}).get("genre_weights") or {}).get(str(genre_id), 0) for genre_id in genre_ids)
    mainstream = min(18.0, popularity / 8.0 + min(votes, 10000) / 1500.0)
    mainstream_adjustment = -mainstream if rules.get("avoid_mainstream") else (mainstream if rules.get("prefer_blockbusters") else 0)
    return theme_hits * 25.0 + min(preference_bonus, 20) + min(analysis_bonus, 25) + rating * 2.0 + min(votes, 5000) / 1000.0 + min(popularity, 100) / 50.0 + mainstream_adjustment


def candidate_matches(movie, rules):
    year, rating, genres = int(movie.get("year") or 0), float(movie.get("rating") or 0), set(movie.get("genre_ids") or [])
    if rules.get("year_min") and (not year or year < int(rules["year_min"])): return False
    if rules.get("year_max") and (not year or year > int(rules["year_max"])): return False
    if rules.get("rating_min") and rating < float(rules["rating_min"]): return False
    wanted = set(rules.get("genres") or [])
    if wanted and not (bool(wanted.intersection(genres)) if rules.get("genre_match") == "any" else wanted.issubset(genres)): return False
    if set(rules.get("excluded_genres") or []).intersection(genres): return False
    if rules.get("language") and movie.get("original_language") != rules["language"]: return False
    if rules.get("country") and rules["country"] not in (movie.get("origin_country") or []): return False
    runtime = int(movie.get("runtime") or 0)
    if rules.get("runtime_min") and (not runtime or runtime < int(rules["runtime_min"])): return False
    if rules.get("runtime_max") and (not runtime or runtime > int(rules["runtime_max"])): return False
    if not release_matches(movie.get("released"), rules.get("request_rules") or {}): return False
    return True
