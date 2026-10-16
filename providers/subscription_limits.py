"""Parse bounded subscription reset hints without retaining raw CLI diagnostics."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from providers.errors import ProviderFailure

LIMIT_FAILURES = {ProviderFailure.QUOTA_EXHAUSTED, ProviderFailure.RATE_LIMITED}
MAX_RESET_SECONDS = 32 * 86400


def limit_delay(kind: ProviderFailure) -> float:
    """Unknown deadlines are probed conservatively, never every service poll."""
    return 3600 if kind == ProviderFailure.QUOTA_EXHAUSTED else 300


def _timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        if number > 1e12:
            number /= 1000
        return datetime.fromtimestamp(number, UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    if isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
            return result if result.tzinfo else None
        except ValueError:
            pass
    return None


def reset_delay(detail: str, *, now: datetime | None = None) -> float | None:
    """Return a safe probe delay, preferring explicit epoch/offset timestamps.

    A time-only hint means the next local occurrence, not a claimed weekly date.
    If that probe still encounters a limit it schedules another wait automatically.
    Unknown/invalid hints use the caller's bounded fallback instead.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('Reset parsing requires an aware current time')
    detail = detail[:64000]
    try:
        payload, _ = json.JSONDecoder().raw_decode(detail.lstrip())
    except ValueError:
        payload = None
    candidates = []

    def inspect(item, depth=0):
        if depth > 6:
            return
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {'resets_at', 'reset_at', 'resetsAt', 'resetAt', 'reset_timestamp'}:
                    stamp = _timestamp(value)
                    if stamp:
                        candidates.append(stamp)
                elif key in {'retry_after_seconds', 'retryAfterSeconds'}:
                    if (isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and 0 <= value <= MAX_RESET_SECONDS):
                        candidates.append(now + timedelta(seconds=value))
                elif isinstance(value, (dict, list)):
                    inspect(value, depth + 1)
        elif isinstance(item, list):
            for value in item[:100]:
                inspect(value, depth + 1)

    inspect(payload)
    # JSON string escaping must not hide the CLI's human-readable reset notice.
    text = detail if payload is None else json.dumps(payload, ensure_ascii=False)
    iso = re.search(r'resets?\s+(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}))', text, re.IGNORECASE)
    if not candidates and iso:
        stamp = _timestamp(iso[1])
        if stamp:
            candidates.append(stamp)
    # Supported local hints: "resets 5am (America/New_York)",
    # "resets Oct 23 at 5am (...)" and "resets Fri at 5am (...)".
    hint = re.search(
        r'resets?\s+(?:(?P<date>[A-Za-z]{3,9}(?:\s+\d{1,2}(?:,?\s+\d{4})?)?)\s+(?:at\s+)?)?'
        r'(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\s*'
        r'\((?P<zone>[A-Za-z0-9_+\-/]+)\)', text, re.IGNORECASE)
    if not candidates and hint:
        try:
            zone = ZoneInfo(hint['zone'])
            local = now.astimezone(zone)
            hour, minute = int(hint['hour']), int(hint['minute'] or 0)
            if not 1 <= hour <= 12 or not 0 <= minute <= 59:
                return None
            hour = hour % 12 + (12 if hint['ampm'].lower() == 'pm' else 0)
            day = local.date()
            date = hint['date']
            if date:
                weekdays = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
                if date[:3].lower() in weekdays and ' ' not in date:
                    day += timedelta(days=(weekdays.index(date[:3].lower()) - local.weekday()) % 7)
                else:
                    parsed = None
                    for fmt in ('%b %d %Y', '%B %d %Y', '%b %d', '%B %d'):
                        try:
                            parsed = datetime.strptime(date.replace(',', ''), fmt)
                            break
                        except ValueError:
                            continue
                    if parsed is None:
                        return None
                    year = parsed.year if parsed.year != 1900 else local.year
                    day = parsed.replace(year=year).date()
                    if day < local.date() and parsed.year == 1900 and local.month == 12:
                        day = parsed.replace(year=year + 1).date()
            stamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=1)
            if stamp.astimezone(UTC) <= now and (not date or ' ' not in date):
                stamp += timedelta(days=1 if not date else 7)
            # Reject nonexistent local times at spring-forward transitions.
            if stamp.astimezone(UTC).astimezone(zone).replace(fold=1) != stamp:
                return None
            candidates.append(stamp)
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            return None
    if not candidates:
        return None
    # Some CLIs expose both rolling and weekly limits. The later deadline is safe
    # when both apply; after it expires a new error can extend the wait again.
    delay = (max(candidates) - now).total_seconds()
    if not -300 <= delay <= MAX_RESET_SECONDS:
        return None
    return max(60, delay + 30)
