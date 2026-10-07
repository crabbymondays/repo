"""Local eligibility and safe diagnostics shared by keyword candidate sources."""

from .episode_lists import history_matches
from .keyword_matcher import score_candidate


def ranking_key(row, rules, preferences, analysis):
    score = score_candidate(row, rules, preferences, analysis)
    mode = rules.get("sort")
    if mode == "recent":
        return (str(row.get("released") or "%04d" % int(row.get("year") or 0)), score)
    if mode == "popular":
        return (float(row.get("popularity") or 0), score)
    if mode == "rated":
        return (float(row.get("rating") or 0), int(row.get("votes") or 0), score)
    return (score,)


class KeywordEligibility:
    @staticmethod
    def _integer(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def __init__(self, profile, hidden, rules, normalise_title):
        self.normalise_title = normalise_title
        self.excluded = set()
        self.excluded_titles = {}
        self.history = set()
        self.history_titles = {}
        self.history_required = rules.get("history_mode") in ("stale", "plays", "watched")
        self.diagnostics = {"scanned": 0, "filter_rejected": 0, "excluded": 0, "history_rejected": 0}
        for media, buckets in (("movie", ("watched", "ratings")), ("show", ("shows_watched", "show_ratings"))):
            for bucket in buckets:
                for row in profile.get(bucket) or []:
                    if not isinstance(row, dict):
                        continue
                    markers = self.markers(row, media)
                    if rules.get("exclude_watched", True):
                        self.remember(markers, self.excluded, self.excluded_titles)
                    if media == "movie" and bucket == "watched" and history_matches(
                        self._integer(row.get("playcount")), row.get("last_watched_at"), rules,
                    ):
                        self.remember(markers, self.history, self.history_titles)
        for row in hidden or []:
            if isinstance(row, dict):
                self.remember(self.markers(row, str(row.get("media_type") or "movie")), self.excluded, self.excluded_titles)

    @staticmethod
    def remember(markers, known_ids, titles):
        identities = {marker for marker in markers if marker[1] != "title"}
        known_ids.update(identities)
        for marker in markers:
            if marker[1] == "title":
                titles.setdefault(marker, []).append(identities)

    @staticmethod
    def matches_saved(markers, known_ids, titles):
        if markers.intersection(known_ids):
            return True
        sources = {marker[1] for marker in markers if marker[1] != "title"}
        for marker in markers:
            for identities in titles.get(marker, []):
                # Fall back to a title only when no provider ID can be compared.
                if not sources.intersection(identity[1] for identity in identities):
                    return True
        return False

    def markers(self, row, media):
        ids = row.get("ids") or {}
        output = set()
        for source in ("tmdb", "trakt", "imdb"):
            value = ids.get(source) or row.get(source + "_id")
            if value not in (None, "", 0):
                output.add((media, source, str(value).casefold()))
        title = self.normalise_title(row.get("title"))
        if title:
            output.add((media, "title", (title, self._integer(row.get("year")))))
        return output

    def accepts(self, row):
        markers = self.markers(row, str(row.get("media_type") or "movie"))
        if self.matches_saved(markers, self.excluded, self.excluded_titles):
            self.diagnostics["excluded"] += 1
            return False
        if self.history_required and not self.matches_saved(markers, self.history, self.history_titles):
            self.diagnostics["history_rejected"] += 1
            return False
        return True


def empty_message(rules, diagnostics):
    """Distinguish an empty source, a strict filter, and personal exclusions."""
    if diagnostics.get("excluded") and not diagnostics.get("filter_rejected"):
        return "The checked candidates were already watched, rated or hidden. Include watched items or change a filter; hidden items stay excluded."
    if diagnostics.get("history_rejected") or rules.get("history_mode") in ("stale", "plays", "watched"):
        return "No checked films matched both your viewing-history rule and the other filters. Check your connected history source or remove a filter."
    references = diagnostics.get("references") or []
    reference_text = ", ".join("%s%s" % (row["title"], " (%s)" % row["year"] if row.get("year") else "") for row in references)
    source = "TMDB's recommendations for " + reference_text if reference_text else "the checked catalogue results"
    if not diagnostics.get("scanned") and references:
        return "TMDB returned no recommendations for %s. Try another reference film; your filters have not been changed." % reference_text
    if rules.get("strategy") == "reference_people":
        return "No checked films matched both the named creator/cast credits and TMDB's recommendations for the reference film. Remove one constraint to search more broadly."
    labels = rules.get("genre_labels") or []
    if len(labels) > 1 and rules.get("genre_match") != "any":
        return "No results matched all selected genres (%s) in %s. For either genre, edit the genre tag to %s." % (" and ".join(labels), source, " or ".join(labels))
    return "Keyword Matching found no items for those filters in %s. Remove or edit a filter to search more broadly." % source
