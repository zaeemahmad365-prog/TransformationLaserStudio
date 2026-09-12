"""Import an existing local backup into an empty PostgreSQL booking document."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import DATABASE_URL, STORE
from storage import StorageUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    if not DATABASE_URL:
        parser.error('Set DATABASE_URL privately in your environment or settings.env first.')
    try:
        bookings = json.loads(args.file.read_text(encoding='utf-8'))
        if not isinstance(bookings, list) or any(not isinstance(b, dict) or not b.get('id')
                                                or not isinstance(b.get('services'), list) for b in bookings):
            parser.error('Expected a bookings.json array with IDs and services.')
        if len({b['id'] for b in bookings}) != len(bookings):
            parser.error('Duplicate booking IDs found; resolve them before importing.')

        def insert(existing):
            if existing:
                raise RuntimeError('Destination already has bookings. Nothing was changed.')
            existing.extend(bookings)

        STORE.update('bookings', [], insert)
    except (OSError, ValueError, StorageUnavailable, RuntimeError) as exc:
        # Do not print connection strings or customer record contents.
        print(str(exc) if isinstance(exc, RuntimeError) else 'Import failed. Check the backup and database settings.', file=sys.stderr)
        return 1
    print(f'Imported {len(bookings)} bookings. No emails were sent.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
