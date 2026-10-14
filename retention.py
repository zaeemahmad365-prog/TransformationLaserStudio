"""Booking-only retention; treatment and financial records live elsewhere."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RETENTION_DAYS = 90
LEGACY_REVIEW_DAYS = 7
LONDON = ZoneInfo('Europe/London')


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (AttributeError, TypeError, ValueError):
        return None


def deletion_due(booking):
    if booking.get('retentionHold'):
        return None
    if booking.get('status') in ('declined', 'cancelled'):
        anchor = timestamp(booking.get('closedAt') or booking.get('statusUpdatedAt'))
    elif booking.get('status') in ('pending', 'confirmed'):
        try:
            anchor = datetime.strptime(
                f"{booking['date']} {booking['time']}", '%Y-%m-%d %H:%M').replace(tzinfo=LONDON)
        except (KeyError, TypeError, ValueError):
            anchor = None
    else:
        anchor = None
    # Incomplete legacy data needs a manual decision, never a guessed deletion.
    if anchor is None:
        return None
    due = anchor + timedelta(days=RETENTION_DAYS)
    review_until = timestamp(booking.get('retentionNotBefore'))
    return max(due, review_until) if review_until else due


def prune_bookings(bookings, now=None):
    """Runs inside the same storage transaction as reads/writes."""
    now = now or datetime.now(timezone.utc)
    retained, deleted = [], []
    for booking in bookings:
        if booking.get('retentionVersion') != 1:
            # Give the studio time to flag existing complaints before rollout.
            booking['retentionVersion'] = 1
            booking['retentionNotBefore'] = (now + timedelta(days=LEGACY_REVIEW_DAYS)).isoformat()
        due = deletion_due(booking)
        if due is not None and due <= now:
            deleted.append(booking.get('id', ''))
        else:
            retained.append(booking)
    bookings[:] = retained
    return deleted


def booking_view(booking):
    result = dict(booking)
    due = deletion_due(booking)
    result['deleteAfter'] = due.isoformat() if due else None
    result['revision'] = booking.get('revision', 0)
    return result
