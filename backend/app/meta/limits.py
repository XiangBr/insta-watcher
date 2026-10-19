"""Meta platform limits, in one place. Changing any of these needs a conversation first."""

from datetime import timedelta

# Private replies per hour per professional account (posts and reels).
PRIVATE_REPLY_HOURLY_LIMIT = 750
RATE_WINDOW = timedelta(hours=1)

# A private reply must go out within 7 days of the comment.
PRIVATE_REPLY_MAX_COMMENT_AGE = timedelta(days=7)

# Messaging window opened by a user message (phase 3).
MESSAGING_WINDOW = timedelta(hours=24)

# Button template.
BUTTON_TITLE_MAX_CHARS = 20
MAX_BUTTONS = 3

# Text message size accepted by the Send API.
MESSAGE_TEXT_MAX_CHARS = 1000

# Retry after a confirmed, retryable rejection (rate limits). One initial try plus these.
RETRY_BACKOFF = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=45))
MAX_ATTEMPTS = 1 + len(RETRY_BACKOFF)

# Long-lived Instagram tokens: refresh needs a token at least 24 hours old.
TOKEN_MIN_AGE_FOR_REFRESH = timedelta(hours=24)
TOKEN_LIFETIME_ASSUMED = timedelta(days=60)

# Quick replies: the tappable options under a message. Text only, they vanish after a tap.
QUICK_REPLY_MAX = 13
QUICK_REPLY_TITLE_MAX_CHARS = 20
