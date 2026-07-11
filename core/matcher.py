import math


def cosine_similarity(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _requirement_text(shot: dict) -> str:
    parts = []
    for bucket in ("required", "preferred", "fallback"):
        block = shot.get(bucket) or {}
        for values in block.values():
            if isinstance(values, list):
                parts.append(" ".join(str(v) for v in values))
            else:
                parts.append(str(values))
    parts.append(shot.get("purpose", ""))
    return " | ".join(p for p in parts if p)


def _tag_overlap_bonus(shot: dict, meta: dict) -> float:
    """Small rule-based nudge on top of semantic similarity when a clip's own tags
    literally match a shot's required/preferred/fallback terms."""
    clip_tags = set()
    for key in ("communicates", "themes", "use_cases", "works_for", "keywords", "mood"):
        clip_tags.update(t.lower() for t in (meta.get(key) or []))
    clip_tags.add((meta.get("primary_action") or "").lower())
    clip_tags.update(t.lower() for t in (meta.get("secondary_actions") or []))

    bonus = 0.0
    weights = {"required": 4.0, "preferred": 2.0, "fallback": 1.0}
    for bucket, weight in weights.items():
        block = shot.get(bucket) or {}
        for values in block.values():
            values = values if isinstance(values, list) else [values]
            for v in values:
                if str(v).lower() in clip_tags:
                    bonus += weight
    return bonus


def score_clip(shot: dict, meta: dict, embed_text_fn, embedding_cache=None) -> float:
    """Returns a 0-100 match score for a shot against one clip's metadata."""
    embedding_cache = embedding_cache if embedding_cache is not None else {}
    req_text = _requirement_text(shot)

    if req_text in embedding_cache:
        req_embedding = embedding_cache[req_text]
    else:
        req_embedding = embed_text_fn(req_text)
        embedding_cache[req_text] = req_embedding

    clip_embedding = meta.get("embedding")
    if not clip_embedding:
        return 0.0

    similarity = cosine_similarity(req_embedding, clip_embedding)  # roughly -1..1
    base = max(0.0, similarity) * 100.0
    bonus = _tag_overlap_bonus(shot, meta)
    return min(100.0, base * 0.75 + bonus)


def find_best_matches(shot, clips_with_meta, embed_text_fn, embedding_cache=None, top_n=5):
    scored = []
    for path, meta in clips_with_meta:
        s = score_clip(shot, meta, embed_text_fn, embedding_cache)
        scored.append((path, meta, s))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_n]
