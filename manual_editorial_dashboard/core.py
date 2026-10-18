"""Day-scoped editorial record normalization with archive-compatible identities."""
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Budapest')
SECTIONS = (
    'live_events', 'event_groups', 'high_confidence_events',
    'influence_signals', 'early_warning_signals', 'crossing_access_signals',
    'access_signals', 'top_crossing_access_posts', 'high_priority_intelligence',
)
TIME_KEYS = ('collected_at', 'first_collected_at', 'first_detected_at', 'created_at', 'first_seen')
ID_KEYS = ('group_id', 'event_id', 'id', 'post_id', 'signal_id')


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Match the archiver: timestamps without a timezone cannot be assigned a day reliably.
        if dt.tzinfo is None:
            return None
        return dt.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def first_date(record):
    for field in TIME_KEYS:
        dt = parse_time(record.get(field))
        if dt is not None:
            return dt.date()
    return None


def key_for(section, record):
    """Exact same stable identity as publish_private_editorial_archive.stable_id."""
    for field in ID_KEYS:
        value = record.get(field)
        if value is not None and str(value).strip():
            return f'{section}:{field}:{value}'
    return None


def legacy_key_for(section, record):
    """Previous UI key; used only to flag possible existing reviews, not auto-merge them."""
    if section in ('event_groups', 'high_confidence_events'):
        kind = 'group'
    elif section == 'high_priority_intelligence' or 'signal' in section or section == 'top_crossing_access_posts':
        kind = 'signal'
    else:
        kind = 'post'
    rid = record.get('group_id') if kind == 'group' else record.get('id')
    rid = rid or record.get('event_id') or record.get('id')
    return f'{kind}:{rid}' if rid is not None else None


def collect(data, chosen_date):
    records = {}
    for section in SECTIONS:
        items = data.get(section, [])
        if not isinstance(items, list):
            continue
        for record in items:
            if not isinstance(record, dict) or first_date(record) != chosen_date:
                continue
            key = key_for(section, record)
            if key is None:
                continue
            # Deduplicate only within the exact same source section and identifier.
            if key not in records:
                records[key] = {
                    'key': key, 'record': record, 'section': section,
                    'legacy_key': legacy_key_for(section, record),
                }
    return sorted(records.values(), key=lambda e: (e['section'], e['key']))

