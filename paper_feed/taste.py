"""AI taste profile (品味画像) and per-paper taste scores, stored in SQLite.

The profile is inferred from positive samples (favorite + archived) versus
negative samples (hidden).  Every saved profile is a new row of
``taste_profiles``; the latest row is the current profile.  Inbox scores are
``paper_analyses`` rows with ``analysis_kind='taste_score'`` whose payload
carries the ``profile_version`` they were computed against, so a new profile
makes them stale.  Taste data is personal: the exporter never publishes it.
"""
import hashlib
import json

from .db import PaperRepository, connect, now

TASTE_MIN_SAMPLES = 10       # positives + hidden
TASTE_MIN_POSITIVES = 5      # favorite + archived
TASTE_LIST_FIELDS = ("likes", "dislikes", "boundaries", "methods")
TASTE_CONTENT_FIELDS = ("summary",) + TASTE_LIST_FIELDS
TASTE_SOURCES = ("ai", "user")
TASTE_MAX_ITEMS = 12
TASTE_MAX_SUMMARY_CHARS = 2000
TASTE_MAX_ITEM_CHARS = 300
TASTE_MAX_REASON_CHARS = 200
POSITIVE_STATES = ("favorite", "archived")
SAMPLE_EVENTS = ("like", "archive", "hide")


def _load(text):
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clean_text(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _clean_list(value, field):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of strings")
    items = []
    for entry in value:
        if entry is None:
            continue
        if not isinstance(entry, (str, int, float)) or isinstance(entry, bool):
            raise ValueError(f"{field} must be a list of strings")
        text = _clean_text(entry, TASTE_MAX_ITEM_CHARS).lstrip("-*•· ").strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= TASTE_MAX_ITEMS:
            break
    return items


def normalize_profile(profile):
    """The five content fields, trimmed and capped.  Raises ValueError when invalid or empty."""
    if not isinstance(profile, dict):
        raise ValueError("profile must be a JSON object")
    summary = profile.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("summary must be a string")
    content = {"summary": str(summary or "").strip()[:TASTE_MAX_SUMMARY_CHARS].strip()}
    for field in TASTE_LIST_FIELDS:
        content[field] = _clean_list(profile.get(field), field)
    if not content["summary"] and not any(content[field] for field in TASTE_LIST_FIELDS):
        raise ValueError("profile is empty")
    return content


def profile_version(content):
    """sha256[:12] of the canonical JSON of the five content fields."""
    canonical = json.dumps({field: content.get(field) for field in TASTE_CONTENT_FIELDS},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def sample_counts(database):
    """``{"favorite": n, "archived": n, "hidden": n}`` from the review state."""
    conn = connect(database)
    try:
        rows = conn.execute("SELECT state, count(*) FROM paper_review_state GROUP BY state").fetchall()
    finally:
        conn.close()
    found = {row[0]: row[1] for row in rows}
    return {state: int(found.get(state, 0)) for state in ("favorite", "archived", "hidden")}


count_samples = sample_counts  # save_profile's ``sample_counts`` argument shadows the function name


def has_enough_samples(counts):
    positives = (counts or {}).get("favorite", 0) + (counts or {}).get("archived", 0)
    return positives >= TASTE_MIN_POSITIVES and positives + (counts or {}).get("hidden", 0) >= TASTE_MIN_SAMPLES


def load_profile(database):
    """The current (latest) profile payload, or None."""
    conn = connect(database)
    try:
        row = conn.execute("SELECT version, payload_json, source, created_at FROM taste_profiles "
                           "ORDER BY profile_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        return None
    payload = _load(row["payload_json"])
    payload.update({"version": row["version"], "source": row["source"], "created_at": row["created_at"]})
    for field in TASTE_LIST_FIELDS:
        payload.setdefault(field, [])
    payload.setdefault("summary", "")
    payload.setdefault("model", "")
    payload.setdefault("sample_counts", {})
    return payload


def save_profile(database, profile, source, model="", sample_counts=None):
    """Normalize and store *profile* as the new current profile; returns the stored payload.

    Saving content identical to an older profile re-activates it (same
    version, new row), so scores computed against it remain current.
    """
    if source not in TASTE_SOURCES:
        raise ValueError("source must be 'ai' or 'user'")
    content = normalize_profile(profile)
    counts = sample_counts if sample_counts is not None else count_samples(database)
    payload = dict(content)
    payload.update({
        "version": profile_version(content), "source": source, "created_at": now(),
        "model": str(model or ""),
        "sample_counts": {state: int((counts or {}).get(state, 0) or 0) for state in ("favorite", "archived", "hidden")},
    })
    conn = connect(database)
    try:
        with PaperRepository(conn).transaction():
            conn.execute("DELETE FROM taste_profiles WHERE version=?", (payload["version"],))
            conn.execute("INSERT INTO taste_profiles(version, payload_json, source, created_at) VALUES (?,?,?,?)",
                         (payload["version"], json.dumps(payload, ensure_ascii=False), source, payload["created_at"]))
    finally:
        conn.close()
    return payload


def samples_since_profile(database, profile):
    """Review events (like/archive/hide) recorded after *profile* was created."""
    created_at = (profile or {}).get("created_at")
    conn = connect(database)
    try:
        marks = ",".join("?" for _ in SAMPLE_EVENTS)
        if created_at:
            row = conn.execute(f"SELECT count(*) FROM paper_review_events WHERE event_type IN ({marks}) AND created_at > ?",
                               (*SAMPLE_EVENTS, created_at)).fetchone()
        else:
            row = conn.execute(f"SELECT count(*) FROM paper_review_events WHERE event_type IN ({marks})",
                               SAMPLE_EVENTS).fetchone()
        return int(row[0])
    finally:
        conn.close()


def clamp_score(value):
    """An int 0-100, or None when *value* is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        score = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN
        return None
    return max(0, min(100, int(round(score))))


def save_scores(database, scores, profile_version):
    """Upsert ``taste_score`` analyses ``{paper_id: {"score", "reason"}}``; returns the number saved."""
    if not scores:
        return 0
    stamp = now()
    saved = 0
    conn = connect(database)
    try:
        with PaperRepository(conn).transaction():
            for paper_id, result in scores.items():
                result = result if isinstance(result, dict) else {}
                score = clamp_score(result.get("score"))
                if score is None or not conn.execute("SELECT 1 FROM papers WHERE paper_id=?", (paper_id,)).fetchone():
                    continue
                payload = {"score": score, "reason": _clean_text(result.get("reason"), TASTE_MAX_REASON_CHARS),
                           "profile_version": str(profile_version or ""), "updated_at": stamp}
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                  VALUES (?,'taste_score','',?,?) ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                             (paper_id, json.dumps(payload, ensure_ascii=False), stamp))
                saved += 1
        return saved
    finally:
        conn.close()


def _label_names(value):
    if not isinstance(value, list):
        value = [value] if value else []
    names = []
    for entry in value:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _paper_rows(conn, states, limit=None):
    marks = ",".join("?" for _ in states)
    sql = f"""SELECT p.paper_id, s.state, s.state_changed_at,
        COALESCE(o.title, p.title) AS title, COALESCE(o.journal, p.journal, '') AS journal,
        (SELECT payload_json FROM paper_analyses a WHERE a.paper_id=p.paper_id AND a.analysis_kind='translation' AND a.analysis_version='') AS translation,
        (SELECT payload_json FROM paper_analyses a WHERE a.paper_id=p.paper_id AND a.analysis_kind='abstract' AND a.analysis_version='') AS abstract,
        (SELECT payload_json FROM paper_user_overrides u WHERE u.paper_id=p.paper_id AND u.override_kind='user_correction') AS correction,
        (SELECT payload_json FROM paper_analyses a WHERE a.paper_id=p.paper_id AND a.analysis_kind='taste_score' AND a.analysis_version='') AS taste
        FROM papers p JOIN paper_review_state s ON s.paper_id=p.paper_id
        LEFT JOIN paper_observations o ON o.observation_id=(SELECT MAX(observation_id) FROM paper_observations WHERE paper_id=p.paper_id)
        WHERE s.state IN ({marks}) ORDER BY s.state_changed_at DESC, p.paper_id"""
    args = list(states)
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    items = []
    for row in conn.execute(sql, args):
        translation, abstract, correction = _load(row["translation"]), _load(row["abstract"]), _load(row["correction"])
        methods = _label_names(correction.get("methods") or translation.get("methods", translation.get("method")))
        topics = _label_names(correction.get("topics") or translation.get("topics", translation.get("topic")))
        raw = abstract.get("raw_abstract") or (abstract.get("abstract") if abstract.get("source") in
                                               ("user_provided", "crossref", "openalex", "semantic_scholar") else "")
        items.append({"paper_id": row["paper_id"], "state": row["state"], "state_changed_at": row["state_changed_at"],
                      "title": row["title"] or "", "journal": row["journal"] or "", "title_zh": translation.get("zh", ""),
                      "methods": methods, "topics": topics, "raw_abstract": str(raw or ""),
                      "taste": _load(row["taste"])})
    return items


def collect_samples(database, limit=150):
    """``{"positive": [...], "hidden": [...]}``, each the *limit* most recently reviewed papers."""
    conn = connect(database)
    try:
        return {"positive": _paper_rows(conn, POSITIVE_STATES, limit), "hidden": _paper_rows(conn, ("hidden",), limit)}
    finally:
        conn.close()


def inbox_items(database):
    """Inbox papers with labels, raw abstract and their stored ``taste`` payload (``{}`` if unscored)."""
    conn = connect(database)
    try:
        return _paper_rows(conn, ("inbox",))
    finally:
        conn.close()


def profile_text(profile):
    """The profile as compact Chinese text for prompts."""
    profile = profile or {}
    titles = {"likes": "偏好", "dislikes": "不感兴趣", "boundaries": "边界判断", "methods": "方法/情境偏好"}
    lines = [f"总体：{profile.get('summary') or '（无）'}"]
    for field in TASTE_LIST_FIELDS:
        entries = [entry for entry in profile.get(field) or [] if isinstance(entry, str) and entry.strip()]
        if entries:
            lines.append(f"{titles[field]}：")
            lines.extend(f"- {entry}" for entry in entries)
    return "\n".join(lines)
