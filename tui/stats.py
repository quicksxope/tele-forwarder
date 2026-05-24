import sqlite3
from datetime import datetime, timedelta


def per_rule_stats(db_path: str, rules: list) -> list[dict]:
    """
    For each rule, return:
      {uuid, name, total_ok, ok_24h, error_count, last_activity}
    Rules without a uuid are keyed by their index.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    results = []
    cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')

    for i, rule in enumerate(rules):
        key = rule.get('uuid') or str(i)
        source_chat_id = rule.get('chat_id')
        if not source_chat_id:
            results.append({'uuid': key, 'name': rule.get('name', '?'), 'total_ok': 0, 'ok_24h': 0, 'error_count': 0, 'last_activity': None})
            continue

        row = conn.execute(
            "SELECT COUNT(*) FROM message_map WHERE source_chat_id=?",
            (source_chat_id,)
        ).fetchone()
        total_ok = row[0] if row else 0

        row = conn.execute(
            "SELECT COUNT(*) FROM forward_events WHERE source_chat_id=? AND status='ok' AND ts >= ?",
            (source_chat_id, cutoff)
        ).fetchone()
        ok_24h = row[0] if row else 0

        row = conn.execute(
            "SELECT COUNT(*) FROM forward_events WHERE source_chat_id=? AND status='error'",
            (source_chat_id,)
        ).fetchone()
        error_count = row[0] if row else 0

        row = conn.execute(
            "SELECT MAX(ts) FROM forward_events WHERE source_chat_id=?",
            (source_chat_id,)
        ).fetchone()
        last_activity = row[0] if row else None

        results.append({
            'uuid': key,
            'name': rule.get('name', f'Rule {i}'),
            'total_ok': total_ok,
            'ok_24h': ok_24h,
            'error_count': error_count,
            'last_activity': last_activity,
        })

    conn.close()
    return results


def recent_errors(db_path: str, limit: int = 20) -> list[dict]:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    rows = conn.execute(
        "SELECT ts, source_chat_id, source_msg_id, error_text FROM forward_events "
        "WHERE status='error' ORDER BY ts DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [{'ts': r[0], 'source_chat_id': r[1], 'source_msg_id': r[2], 'error_text': r[3]} for r in rows]
