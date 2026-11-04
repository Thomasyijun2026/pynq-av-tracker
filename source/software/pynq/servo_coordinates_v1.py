"""Explicit raw-servo to fixed-installation coordinates, frozen for one boot.

The servo accumulator can retain a whole-turn offset or restart at a single-turn
count. A new datum changes packet numbers, never the installation envelope.
The boundary shared by both ends is ambiguous and is not guessed on first use.
"""
import json
import os
from pathlib import Path


def validate(record, reference):
    expected = {k: reference[k] for k in ('boot_id', 'scope', 'center', 'low', 'high')}
    if record.get('reference') != expected or record.get('version') != 1:
        raise ValueError('Servo datum identity mismatch')
    offset = record.get('raw_minus_installation')
    if type(offset) is not int or offset % 4096 or abs(offset) > 24576:
        raise ValueError('Invalid servo datum offset')
    if not -28000 <= reference['low']+offset < reference['high']+offset <= 28000:
        raise ValueError('Raw servo envelope out of range')
    return offset


def resolve(raw, single, reference, existing=None):
    if any(type(x) is not int for x in (raw, single)) or not 0 <= single < 4096:
        raise ValueError('Integer servo positions required')
    if abs(raw) > 28000 or abs((raw-single+2048) % 4096-2048) > 2:
        raise ValueError('Single/full feedback mismatch')
    if existing is not None:
        offset = validate(existing, reference)
        if not reference['low'] <= raw-offset <= reference['high']:
            raise ValueError('Servo datum changed or installation envelope exceeded')
        return existing
    low, high = reference['low'], reference['high']
    # The raw accumulator's origin is independent of the installation frame.
    # Resolve it ONCE using the fresh stationary single/full pair; a saved
    # mapping is never replaced by another modulo reduction on later requests.
    candidates = [raw+4096*k for k in range(-8, 9) if low <= raw+4096*k <= high]
    if len(candidates) != 1:
        raise ValueError('Ambiguous installation endpoint at datum initialization')
    logical = candidates[0]
    record = dict(version=1, reference={k: reference[k] for k in
                  ('boot_id', 'scope', 'center', 'low', 'high')},
                  raw_minus_installation=raw-logical, initial_raw=raw,
                  initial_single=single, initial_installation=logical,
                  decision_owner='PYNQ',
                  basis='STATIONARY_SINGLE_FULL_CONGRUENCE_FIXED_ONE_TURN',
                  physical_turn_history_reconstructed=False)
    validate(record, reference)
    return record


def load_or_create(path, raw, single, reference):
    path = Path(path)
    if path.exists():
        return resolve(raw, single, reference, json.loads(path.read_text()))
    record = resolve(raw, single, reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The owning runtime already holds its exclusive hardware lock. Exclusive
    # creation still prevents a second caller from replacing a persisted datum.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
    if os.name == 'posix':
        fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    if json.loads(path.read_text()) != record:
        raise IOError('Servo datum persistence mismatch')
    return record
