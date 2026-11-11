"""Per-session rate limiting for CareCompass.

Limits:
  - At most 10 messages per 60-second sliding window (per session).
  - At most 100 messages per session (lifetime cap).

Usage::

    limiter = RateLimiter()
    allowed, wait_msg = limiter.check("session-uuid")
    if not allowed:
        show_polite_message(wait_msg)
        return

    limiter.record("session-uuid")
    # ... process request ...

Thread safety: not required for single-threaded Streamlit sessions.
The state is stored in the RateLimiter instance, which callers should
keep in ``st.session_state`` or equivalent per-session storage.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

# Limits — do not increase without a security review.
_PER_MINUTE_LIMIT: int = 10
_SESSION_LIMIT: int = 100
_WINDOW_SECONDS: float = 60.0

_MSG_PER_MINUTE = (
    "You've sent quite a few messages in the last minute. "
    "Please wait a moment before sending another — this helps us keep the "
    "service available for everyone."
)
_MSG_SESSION_CAP = (
    "You've reached the maximum number of messages for this session. "
    "Please start a new session to continue."
)


@dataclass
class RateLimiter:
    """Track per-session message timestamps and enforce rate limits.

    Attributes:
        timestamps: Sliding window of timestamps (seconds since epoch) for
                    the current session.
        total_count: Total messages sent in this session (lifetime).
    """

    timestamps: deque[float] = field(default_factory=deque)
    total_count: int = 0

    def check(self) -> tuple[bool, str]:
        """Return (allowed, message).

        Call this *before* processing a request.
        If ``allowed`` is False, ``message`` is a polite explanation to show
        the user. Does NOT record the attempt.

        Returns:
            A tuple of (bool, str).  When bool is True the request may proceed;
            when False the str contains the message to display.
        """
        now = time.monotonic()

        # Lifetime cap — checked first.
        if self.total_count >= _SESSION_LIMIT:
            return False, _MSG_SESSION_CAP

        # Sliding-window cap — prune stale timestamps.
        cutoff = now - _WINDOW_SECONDS
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

        if len(self.timestamps) >= _PER_MINUTE_LIMIT:
            oldest = self.timestamps[0]
            wait_secs = int(_WINDOW_SECONDS - (now - oldest)) + 1
            msg = (
                _MSG_PER_MINUTE
                + f" (approximately {wait_secs} second{'s' if wait_secs != 1 else ''}.)"
            )
            return False, msg

        return True, ""

    def record(self) -> None:
        """Record that one message was processed.

        Call this *after* a successful check() and before processing.
        """
        self.timestamps.append(time.monotonic())
        self.total_count += 1
