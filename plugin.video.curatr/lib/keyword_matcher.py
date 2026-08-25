"""Deterministic prompt parsing and ranking for curatr's no-AI list mode."""

import re
import time

PARSER_VERSION = 6
MAX_REFERENCES = 3

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


def _contains(text, phrase):
    return bool(re.search(r"(?<!\w)%s(?!\w)" % re.escape(phrase), text, re.I))


def _clean_reference(value):
    # Person/reference captures deliberately accept natural wording and run to
    # the end of the prompt.  Trim any recognised discovery filters before
    # splitting on "and", otherwise a prompt such as "directed by the Coen
    # brothers thrillers and crime" turns those genres into extra people.
    value = re.sub(
        r"\s+(?:(?:in|and)\s+)?(?:(?:films?|movies?)\s+)?(?:released|rated|from|between|before|after|under|over|with a rating|that (?:are|aren't|are not)|which (?:are|aren't|are not)|but)\b.*$",
        "", str(value or ""), flags=re.I,
    )
    value = re.sub(
        r"\s+(?:(?:and|with|in)\s+)?(?:%s)\b.*$" % _REFERENCE_FILTER_PATTERN,
        "", value, flags=re.I,
    )
    return value.strip(" ,.;:-")


def _split_references(value, maximum=MAX_REFERENCES):
    value = _clean_reference(value)
    if not value:
        return []
    pieces = [part.strip() for part in re.split(r"\s*(?:,|\band\b|&)\s*", value, flags=re.I) if part.strip()]
    return pieces[:maximum]


def _capture(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _clean_reference(match.group(1))
    return ""


def _looks_like_cast_reference(value):
    """Accept short natural person names without treating ordinary qualities as cast."""
    candidate = _clean_reference(value)
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
    )
    return not any(_contains(lowered, phrase) for phrase in blocked)


def parse_prompt(prompt, current_year=None):
    original = " ".join(str(prompt or "").replace("_", " ").split())
    text = original.casefold()
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
    }
    for alias, (genre_id, label) in sorted(GENRES.items(), key=lambda row: -len(row[0])):
        if _contains(text, alias) and genre_id not in rules["genres"]:
            rules["genres"].append(genre_id); rules["genre_labels"].append(label)
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
        match = re.search(r"\b(?:since|after|from)\s+((?:19|20)\d{2})\b", text)
        if match: rules["year_min"] = int(match.group(1))
        match = re.search(r"\b(?:before|until|up to)\s+((?:19|20)\d{2})\b", text)
        if match: rules["year_max"] = int(match.group(1))
        recent = re.search(r"\b(?:last|past)\s+(\d{1,2})\s+years?\b", text)
        if recent:
            rules["year_min"] = max(1900, current_year - int(recent.group(1)) + 1); rules["year_max"] = current_year

    runtime = re.search(r"\b(?:under|less than|shorter than)\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b", text)
    if runtime:
        value = float(runtime.group(1)); rules["runtime_max"] = int(value * 60 if runtime.group(2).startswith(("hour", "hr")) else value)
    runtime = re.search(r"\b(?:over|more than|longer than)\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b", text)
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
        r"\b(%s)\s*(?:rating|score)?\s*(?:of|above|over|at least|rated)?\s*(\d+(?:\.\d+)?)\s*(%%|/10)?" % source_pattern,
        text,
    )
    if source_rating and not rules["external_source"]:
        source, source_label = SOURCE_ALIASES[source_rating.group(1)]
        value = float(source_rating.group(2))
        value = max(0.0, min(100.0 if source in ("tomatoes", "popcorn") else 10.0, value))
        rules["external_source"], rules["external_source_label"] = source, source_label
        rules["external_rating_min"] = value

    rating_text = text
    if source_rating:
        rating_text = rating_text[:source_rating.start()] + rating_text[source_rating.end():]
    rating = re.search(r"\b(?:rated|rating|score)\s*(?:of|above|over|at least)?\s*(\d+(?:\.\d+)?)\s*(?:\+|/10)?", rating_text)
    if rating: rules["rating_min"] = max(0.0, min(10.0, float(rating.group(1))))
    elif "highly rated" in text or "best rated" in text: rules["rating_min"] = 7.0

    for label, (code, display) in LANGUAGES.items():
        if _contains(text, label) and ("language" in text or "in %s" % label in text):
            rules["language"], rules["language_label"] = code, display; break
    for label, (code, display) in COUNTRIES.items():
        if _contains(text, label): rules["country"], rules["country_label"] = code, display; break

    recurring = _capture(original, [
        r"\b(?:actors?|cast|collaborators?)\s+(?:often|frequently|commonly)\s+(?:used by|working with)\s+(.+)$",
        r"\b(?:recurring|frequent)\s+(?:actors?|cast|collaborators?)\s+(?:of|for|with)\s+(.+)$",
    ])
    similar_directors = _capture(original, [
        r"\b(?:similar to|like)\s+(?:films?|movies?)\s+by\s+(.+)$",
        r"\b(?:films?|movies?)\s+(?:similar to|like)\s+(?:films?|movies?)\s+by\s+(.+)$",
        r"\b(?:films?|movies?)\s+by\s+(?:directors?|filmmakers?)\s+(?:similar to|like)\s+(.+)$",
        r"\b(?:directors?|filmmakers?)\s+(?:similar to|like)\s+(.+)$",
        r"\b(?:films?|movies?)\s+(?:in the style of)\s+(.+)$",
    ])
    similar_actors = _capture(original, [r"\b(?:actors?|performers?)\s+(?:similar to|like)\s+(.+)$"])
    similar_creatives = _capture(original, [
        r"\b(?:cinematography|screenplays?|writing|creative work)\s+(?:similar to|like)\s+(.+)$",
    ])
    exact_directors = _capture(original, [r"\b(?:directed by|from (?:the )?directors?|films? by|movies? by)\s+(.+)$"])
    exact_cast = _capture(original, [r"\b(?:starring|featuring|with (?:the )?actors?)\s+(.+)$"])
    if not exact_cast:
        natural_cast = _capture(original, [r"\b(?:films?|movies?)\s+with\s+(.+)$"])
        if _looks_like_cast_reference(natural_cast):
            exact_cast = natural_cast
    names, role, strategy = [], "", ""
    if recurring: names, role, strategy = _split_references(recurring), "director", "recurring_collaborators"
    elif similar_directors: names, role, strategy = _split_references(similar_directors), "director", "similar_people"
    elif similar_actors: names, role, strategy = _split_references(similar_actors), "cast", "similar_people"
    elif similar_creatives: names, role, strategy = _split_references(similar_creatives), "crew", "similar_people"
    elif exact_directors: names, role, strategy = _split_references(exact_directors), "director", "exact_people"
    elif exact_cast: names, role, strategy = _split_references(exact_cast), "cast", "exact_people"
    if names:
        rules["people"] = [{"query": name, "role": role} for name in names]
        rules["person_query"], rules["person_role"], rules["strategy"] = names[0], role, strategy
    else:
        reference = _capture(original, [r"\b(?:films?|movies?)?\s*(?:like|similar to)\s+(.+)$"])
        references = _split_references(reference)
        if references:
            rules["reference_movies"] = [{"title": title, "year": 0} for title in references]
            rules["reference_title"], rules["strategy"] = references[0], "similar_films"

    avoid_terms = (
        "less mainstream", "not mainstream", "avoid blockbusters", "not huge blockbusters",
        "aren't huge blockbusters", "are not huge blockbusters", "not blockbusters", "smaller films",
    )
    rules["avoid_mainstream"] = any(term in text for term in avoid_terms)
    rules["prefer_blockbusters"] = any(term in text for term in ("blockbusters", "blockbuster", "big budget")) and not rules["avoid_mainstream"]
    if any(term in text for term in ("newest", "latest", "recent")): rules["sort"] = "recent"
    elif rules["prefer_blockbusters"] or any(term in text for term in ("popular", "well known", "mainstream")): rules["sort"] = "popular"
    elif rules["avoid_mainstream"]: rules["sort"] = "less_mainstream"
    elif rules["rating_min"] or any(term in text for term in ("best", "greatest", "acclaimed")): rules["sort"] = "rated"
    meaningful = sum(bool(rules[key]) for key in (
        "genres", "themes", "year_min", "year_max", "runtime_min", "runtime_max", "rating_min",
        "language", "country", "people", "reference_movies", "avoid_mainstream", "prefer_blockbusters",
        "external_source", "external_chart_limit", "external_rating_min",
    ))
    rules["confidence"] = min(1.0, meaningful / 3.0)
    rules["display_parts"] = confirmation_parts(rules)
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
        parts.append({"text": value.replace(".0+", "+").replace(".0%", "%"), "kind": "number", "connector": "from"})
    if rules.get("country_label"):
        parts.append({"text": rules["country_label"], "kind": "place", "connector": ""})
    genre_text = ", ".join((rules.get("theme_labels") or []) + (rules.get("genre_labels") or []))
    if genre_text: parts.append({"text": genre_text, "kind": "genre", "connector": ""})
    people = rules.get("people") or []
    if people:
        strategy, role = rules.get("strategy"), people[0].get("role")
        if strategy == "similar_people":
            connector = "similar to films by" if role == "director" else ("similar to work by" if role == "crew" else "similar to")
        elif strategy == "recurring_collaborators": connector = "using recurring collaborators of"
        else: connector = "directed by" if role == "director" else ("by" if role == "crew" else "starring")
        for index, person in enumerate(people):
            parts.append({"text": person.get("name") or person.get("query") or "", "kind": "person", "connector": connector if index == 0 else "and"})
    for index, movie in enumerate(rules.get("reference_movies") or []):
        parts.append({"text": movie.get("title") or "", "kind": "film", "connector": "similar to" if index == 0 else "and"})
    if rules.get("language_label") and not rules.get("country_label"):
        parts.append({"text": rules["language_label"], "kind": "place", "connector": "in"})
    if rules.get("year_min") or rules.get("year_max"):
        if rules.get("year_min") and rules.get("year_max"): value, connector = "%s–%s" % (rules["year_min"], rules["year_max"]), "from"
        elif rules.get("year_min"): value, connector = "%s onwards" % rules["year_min"], "from"
        else: value, connector = "before %s" % rules["year_max"], "released"
        parts.append({"text": value, "kind": "year", "connector": connector})
    if rules.get("rating_min"): parts.append({"text": "%.1f+" % float(rules["rating_min"]), "kind": "number", "connector": "rated"})
    if rules.get("runtime_max"): parts.append({"text": "%d minutes" % int(rules["runtime_max"]), "kind": "runtime", "connector": "under"})
    elif rules.get("runtime_min"): parts.append({"text": "%d minutes" % int(rules["runtime_min"]), "kind": "runtime", "connector": "over"})
    if rules.get("avoid_mainstream"): parts.append({"text": "less mainstream", "kind": "genre", "connector": "favouring"})
    elif rules.get("prefer_blockbusters"): parts.append({"text": "major blockbusters", "kind": "genre", "connector": "favouring"})
    return [part for part in parts if part.get("text")]


def format_rules(rules):
    fragments = ["%s %s" % (part.get("connector") or "", part.get("text") or "") for part in confirmation_parts(rules)]
    sentence = " ".join(fragment.strip() for fragment in fragments if fragment.strip()).strip()
    if sentence: sentence = sentence[0].upper() + sentence[1:]
    return (sentence or "Your recognised filters") + "\n\nWatched, rated and hidden films will be excluded.\nNo AI will be used."


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
    if rules.get("genres") and not set(rules["genres"]).issubset(genres): return False
    if rules.get("language") and movie.get("original_language") != rules["language"]: return False
    if rules.get("country") and movie.get("origin_country") and rules["country"] not in movie["origin_country"]: return False
    return True
