"""Sliding-window rate limit per caller (client IP), used for scans and verifications.

In-process, so each gunicorn worker keeps its own count and a restart resets it.
"""

import collections
import threading
import time


class RateLimiter:
    def __init__(self, limit, window, clock=time.time):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def hit(self, key):
        """Return 0 if allowed, else seconds to wait."""
        now = self._clock()
        with self._lock:
            seen = self._hits[key]
            while seen and seen[0] <= now - self.window:
                seen.popleft()
            if len(seen) >= self.limit:
                if not seen:  # limit is 0
                    return int(self.window)
                return max(1, int(seen[0] + self.window - now))
            seen.append(now)
            return 0
