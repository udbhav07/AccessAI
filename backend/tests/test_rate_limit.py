"""Tests for the sliding-window limiter, using a fake clock."""

from accessai.api.rate_limit import RateLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_allows_up_to_the_limit_then_says_how_long_to_wait():
    clock = Clock()
    limiter = RateLimiter(limit=2, window=60, clock=clock)

    assert limiter.hit("a") == 0
    clock.now += 10
    assert limiter.hit("a") == 0
    assert limiter.hit("a") == 50, "the oldest hit expires 50s from now"


def test_the_window_slides():
    clock = Clock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)

    assert limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    clock.now += 60
    assert limiter.hit("a") == 0


def test_refused_attempts_do_not_extend_the_wait():
    clock = Clock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    limiter.hit("a")
    for _ in range(5):
        limiter.hit("a")
    clock.now += 60
    assert limiter.hit("a") == 0


def test_callers_are_counted_separately():
    limiter = RateLimiter(limit=1, window=60, clock=Clock())
    assert limiter.hit("a") == 0
    assert limiter.hit("b") == 0


def test_a_limit_of_zero_pauses_scanning_instead_of_crashing():
    limiter = RateLimiter(limit=0, window=60, clock=Clock())
    assert limiter.hit("a") == 60
