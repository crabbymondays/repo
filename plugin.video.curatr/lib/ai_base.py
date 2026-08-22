import json


class AIError(Exception):
    pass


class BaseAIClient:
    provider_id = "ai"
    provider_name = "AI"

    def __init__(self, api_key, model, session=None, usage_callback=None):
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.session = session
        self.usage_callback = usage_callback

    def _require_api_key(self):
        if not self.api_key:
            raise AIError("Add your %s API key in Settings before creating a list." % self.provider_name)

    def build_taste_fingerprint(self, profile):
        self._require_api_key()
        signals = self._fingerprint_signals(profile)
        schema = self.fingerprint_schema()
        instructions = (
            "You are building a durable taste fingerprint for a personal movie curator. "
            "Infer stable preferences from the supplied Trakt rating signals rather than merely listing genres. "
            "Look for recurring patterns in direction, tone, pacing, themes, visual style, performances, story "
            "structure, era, country and filmmaking sensibility. Distinguish strong evidence from isolated ratings, "
            "and do not overfit one film. Treat low ratings as useful negative evidence. Keep the result concise, "
            "specific and useful for future recommendation requests. Do not recommend new movies in this step."
        )
        user_text = "TRAKT RATING SIGNALS:\n%s" % json.dumps(
            signals, ensure_ascii=False, separators=(",", ":")
        )
        result = self._structured_request(
            instructions,
            user_text,
            schema,
            "movie_taste_fingerprint",
            usage_kind="taste_fingerprint",
        )
        return self.validate_fingerprint(result)

    def recommend(self, prompt, taste_context, count):
        self._require_api_key()
        count = max(1, min(60, int(count)))
        schema = self.recommendation_schema(count)
        instructions = (
            "You are the recommendation brain for a personal Kodi movie curator. "
            "Use the reusable taste fingerprint as evidence, not as rigid genre filters. Infer patterns across films "
            "collectively: directors, themes, tone, pacing, cinematography, performances, story structure, era, "
            "country and other meaningful qualities. Follow the natural-language request precisely. "
            "The addon will independently verify every movie against Trakt and will reject anything already watched "
            "or rated. Avoid the recent-watch examples, previous recommendations and any never-recommend entries supplied "
            "in the context so candidate slots are not wasted and refreshed lists produce genuinely fresh choices. "
            "When a VERIFIED CANDIDATE POOL is supplied, strongly prefer suitable films from it because those titles and "
            "years were grounded by an external catalogue. You may go outside it when the user's request clearly needs it; "
            "never recommend an unsuitable film merely because it appears in that pool. "
            "Prefer genuinely strong personal matches over generic popular picks unless the request calls for them. "
            "Return only real feature films and use their original release year."
        )
        taste_text = "REUSABLE TASTE CONTEXT:\n%s" % json.dumps(
            taste_context, ensure_ascii=False, separators=(",", ":")
        )
        request_text = (
            "USER REQUEST:\n%s\n\nReturn up to %d strong candidate movies. Give enough variety for Trakt "
            "verification to discard ambiguous or already-seen matches." % (prompt, count)
        )
        result = self._structured_request(
            instructions,
            taste_text,
            schema,
            "movie_recommendations",
            usage_kind="recommendation",
            extra_input=[{"role": "user", "content": request_text}],
        )
        return self.validate_recommendations(result, count)

    def _structured_request(
        self,
        instructions,
        user_text,
        schema,
        schema_name,
        usage_kind,
        extra_input=None,
    ):
        raise NotImplementedError

    def _emit_usage(
        self,
        kind,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
    ):
        if not self.usage_callback:
            return
        event = {
            "kind": str(kind or "request"),
            "provider": self.provider_id,
            "provider_name": self.provider_name,
            "model": self.model,
            "input_tokens": self._safe_int(input_tokens),
            "cached_input_tokens": self._safe_int(cached_input_tokens),
            "output_tokens": self._safe_int(output_tokens),
            "reasoning_tokens": self._safe_int(reasoning_tokens),
            "total_tokens": self._safe_int(total_tokens),
        }
        try:
            self.usage_callback(event)
        except Exception:
            pass

    @staticmethod
    def fingerprint_schema():
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "core_preferences": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "avoidances": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "director_affinities": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "representative_likes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                        "required": ["title", "year"],
                        "additionalProperties": False,
                    },
                    "maxItems": 20,
                },
                "representative_dislikes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                        "required": ["title", "year"],
                        "additionalProperties": False,
                    },
                    "maxItems": 10,
                },
                "exploration_directions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            },
            "required": [
                "summary",
                "core_preferences",
                "avoidances",
                "director_affinities",
                "representative_likes",
                "representative_dislikes",
                "exploration_directions",
            ],
            "additionalProperties": False,
        }

    @staticmethod
    def recommendation_schema(count):
        return {
            "type": "object",
            "properties": {
                "list_name": {"type": "string"},
                "description": {"type": "string"},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "year": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["title", "year", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["list_name", "description", "items"],
            "additionalProperties": False,
        }

    @staticmethod
    def _fingerprint_signals(profile):
        ratings = []
        for row in (profile or {}).get("ratings", []):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            try:
                year = int(row.get("year"))
                rating = int(row.get("rating"))
            except (TypeError, ValueError):
                continue
            if not title:
                continue
            ratings.append({"title": title, "year": year, "rating": rating})

        high = sorted(
            [r for r in ratings if r["rating"] >= 8],
            key=lambda r: (r["rating"], r["year"]),
            reverse=True,
        )[:100]
        low = sorted(
            [r for r in ratings if r["rating"] <= 5],
            key=lambda r: (r["rating"], -r["year"]),
        )[:60]
        middle = [r for r in ratings if 6 <= r["rating"] <= 7][:40]

        directors = []
        for item in (profile or {}).get("favourite_directors", [])[:20]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            directors.append({
                "name": name,
                "liked_movies": BaseAIClient._safe_int(item.get("liked_movies")),
                "average_rating": item.get("average_rating"),
            })

        return {
            "liked_rating_threshold": BaseAIClient._safe_int(
                (profile or {}).get("liked_rating_threshold"), 8
            ),
            "high_ratings": high,
            "low_ratings": low,
            "middle_ratings_sample": middle,
            "favourite_directors": directors,
            "total_ratings_available": len(ratings),
        }

    @staticmethod
    def _safe_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def validate_recommendations(result, count):
        if not isinstance(result, dict):
            raise AIError("The AI provider returned an unexpected recommendation structure.")
        cleaned = []
        seen = set()
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            reason = str(item.get("reason") or "").strip()
            try:
                year = int(item.get("year"))
            except (TypeError, ValueError):
                continue
            if not title or year < 1880 or year > 2100:
                continue
            key = (title.casefold(), year)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({"title": title, "year": year, "reason": reason})
            if len(cleaned) >= count:
                break
        if not cleaned:
            raise AIError("The AI provider returned no valid movie recommendations.")
        return {
            "list_name": str(result.get("list_name") or "").strip(),
            "description": str(result.get("description") or "").strip(),
            "items": cleaned,
        }

    @staticmethod
    def validate_fingerprint(result):
        if not isinstance(result, dict):
            raise AIError("The AI provider returned an unexpected taste-fingerprint structure.")

        def strings(key, maximum):
            values = []
            for value in result.get(key, []):
                text = str(value or "").strip()
                if text and text not in values:
                    values.append(text)
                if len(values) >= maximum:
                    break
            return values

        def movies(key, maximum):
            values = []
            seen = set()
            for item in result.get(key, []):
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                try:
                    year = int(item.get("year"))
                except (TypeError, ValueError):
                    continue
                if not title or year < 1880 or year > 2100:
                    continue
                marker = (title.casefold(), year)
                if marker in seen:
                    continue
                seen.add(marker)
                values.append({"title": title, "year": year})
                if len(values) >= maximum:
                    break
            return values

        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise AIError("The AI provider returned an empty taste fingerprint.")
        return {
            "summary": summary,
            "core_preferences": strings("core_preferences", 12),
            "avoidances": strings("avoidances", 8),
            "director_affinities": strings("director_affinities", 10),
            "representative_likes": movies("representative_likes", 20),
            "representative_dislikes": movies("representative_dislikes", 10),
            "exploration_directions": strings("exploration_directions", 8),
        }
