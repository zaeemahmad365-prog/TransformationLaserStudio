"""Shared validation for public requests and authenticated edits."""
from datetime import datetime
from zoneinfo import ZoneInfo


class InvalidBooking(ValueError):
    pass


def text_field(data, key, limit):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise InvalidBooking(f'Please enter a valid {key} (up to {limit} characters).')
    return value.strip()


def validate_booking(data, allowed, valid_email, *, previous=None, require_future=True):
    fields = {key: text_field(data, key, limit) for key, limit in
              [('name', 80), ('email', 120), ('phone', 30), ('date', 10), ('time', 5), ('notes', 800)]}
    fields['email'] = fields['email'].lower()
    if not all(fields[key] for key in ('name', 'email', 'phone', 'date', 'time')) or not valid_email(fields['email']):
        raise InvalidBooking('Please complete the name, email, phone, date and time.')
    try:
        requested = datetime.strptime(f"{fields['date']} {fields['time']}", '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('Europe/London'))
        if requested.strftime('%Y-%m-%d %H:%M') != f"{fields['date']} {fields['time']}":
            raise ValueError()
    except ValueError as exc:
        raise InvalidBooking('Please choose a valid date and time.') from exc
    if require_future and requested < datetime.now(ZoneInfo('Europe/London')):
        raise InvalidBooking('Please choose a future date and time.')

    raw_services = data.get('services')
    if not isinstance(raw_services, list) or not raw_services or len(raw_services) > 20:
        raise InvalidBooking('Please choose between one and twenty treatments.')
    # Unchanged historic treatments/prices remain editable if the catalogue changes.
    services_unchanged = previous is not None and raw_services == previous.get('services')
    services, seen = [], set()
    for raw in raw_services:
        if not isinstance(raw, dict):
            raise InvalidBooking('Invalid treatment selection.')
        try:
            sig = (text_field(raw, 'category', 80), text_field(raw, 'name', 120),
                   text_field(raw, 'variant', 80), round(float(raw.get('price', 0)), 2))
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidBooking('Invalid treatment selection.') from exc
        if not services_unchanged and sig not in allowed:
            raise InvalidBooking('A treatment or price did not match the current price list. Refresh and try again.')
        if sig in seen:
            continue
        seen.add(sig)
        services.append(dict(zip(('category', 'name', 'variant', 'price'), sig)))
    appointment_changed = previous is None or any(fields[key] != previous.get(key) for key in ('date', 'time')) or not services_unchanged
    if appointment_changed or require_future:
        opening = 11 * 60 if requested.weekday() == 6 else 10 * 60
        if not opening <= requested.hour * 60 + requested.minute <= 19 * 60:
            hours = '11am and 7pm on Sunday' if requested.weekday() == 6 else '10am and 7pm, Monday to Saturday'
            raise InvalidBooking(f'Please choose a time between {hours}.')
        if requested.weekday() == 6 and any(s['category'] != 'Nails' for s in services):
            raise InvalidBooking('Sundays are available for nail treatments only. Please choose another day.')
    fields['services'] = services
    fields['total'] = round(sum(s['price'] for s in services), 2)
    return fields
