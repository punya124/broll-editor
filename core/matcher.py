import math


def cosine_similarity(a, b) -> float:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return 0.0
    if len(a) == 0 or len(b) == 0 or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def score_action(action_text: str, clip_meta: dict, embed_text_fn, cache=None) -> float:
    """0-100 match score between a plain action description and a clip's
    embedding. Uses the same floor/ceiling stretch we validated earlier:
    real-world SEMANTIC_SIMILARITY cosine scores for "same idea, different
    words" typically land between ~0.3 (unrelated) and ~0.9 (near-paraphrase)."""
    if not action_text:
        return 0.0
    cache = cache if cache is not None else {}
    if action_text in cache:
        action_embedding = cache[action_text]
    else:
        action_embedding = embed_text_fn(action_text)
        cache[action_text] = action_embedding

    clip_embedding = clip_meta.get("embedding")
    if not isinstance(clip_embedding, (list, tuple)) or len(clip_embedding) == 0:
        return 0.0

    similarity = cosine_similarity(action_embedding, clip_embedding)
    floor, ceiling = 0.3, 0.9
    normalized = (similarity - floor) / (ceiling - floor)
    return max(0.0, min(1.0, normalized)) * 100.0


def find_best_action_matches(action_text, clips_with_meta, embed_text_fn, cache=None, top_n=5):
    cache = cache if cache is not None else {}
    scored = [
        (path, meta, score_action(action_text, meta, embed_text_fn, cache))
        for path, meta in clips_with_meta
    ]
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_n]