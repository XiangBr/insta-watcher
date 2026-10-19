"""Follow gate rules in one place: the button's payload, and how patient the gate is.

Flow: the first DM invites the person to follow and holds a postback button. A tap asks Meta
whether they follow. Yes releases the link. No waits a little and asks again, because a follow
takes a moment to show up, then asks the person once more. Meta not knowing releases the link:
holding it back forever from someone who already follows is worse than a rare early release.

These are our own choices, not Meta limits, so they live here and not in meta/limits.py.
"""

import re
from datetime import timedelta

PAYLOAD_PREFIX = "bw:gate:"
_PAYLOAD = re.compile(r"^bw:gate:(\d{1,9})$")

# After a tap that finds the person not following: check again after each of these waits.
RECHECK_DELAYS = (timedelta(seconds=20), timedelta(seconds=40))
CHECKS_PER_TAP = 1 + len(RECHECK_DELAYS)

# A person who keeps tapping without following gets asked this many times, then nothing more.
MAX_TAPS = 5

# The profile lookup itself failed (network, Meta outage). Try again before deciding.
CHECK_RETRY_DELAYS = (timedelta(seconds=15), timedelta(seconds=60), timedelta(minutes=5))

DEFAULT_BUTTON_LABEL = "Já segui"


def gate_payload(delivery_id: int) -> str:
    return f"{PAYLOAD_PREFIX}{delivery_id}"


def parse_gate_payload(payload: object) -> int | None:
    """The delivery a tap belongs to, or None for anything that is not one of our buttons."""
    if not isinstance(payload, str):
        return None
    match = _PAYLOAD.match(payload)
    return int(match.group(1)) if match else None
