"""Colour parsing and WCAG contrast maths: luminance, contrast ratio, the AA threshold
for a text size, and the nearest colour that passes.
"""

from PIL import ImageColor

AA_NORMAL = 4.5
AA_LARGE = 3.0
LARGE_TEXT_PX = 24.0            # WCAG "large text": >=24px, or >=18.66px when bold
LARGE_TEXT_BOLD_PX = 18.66


def parse(value):
    """A CSS colour as (r, g, b), or None if it can't be read. Alpha is ignored."""
    try:
        return ImageColor.getrgb(str(value).strip())[:3]
    except (ValueError, AttributeError):
        return None


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def _linearise(channel):
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_linearise(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(rgb_a, rgb_b):
    lighter, darker = sorted((luminance(rgb_a), luminance(rgb_b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def check_contrast(text_colour, background_colour):
    """Contrast ratio (1-21) between two CSS colours, or None if either is unreadable."""
    fg, bg = parse(text_colour), parse(background_colour)
    if fg is None or bg is None:
        return None
    return ratio(fg, bg)


def threshold_for(font_size, font_weight):
    large = font_size >= LARGE_TEXT_PX or (font_size >= LARGE_TEXT_BOLD_PX and font_weight >= 700)
    return AA_LARGE if large else AA_NORMAL


def nearest_passing(fg, bg, threshold):
    """Step `fg` toward black or white until it reaches `threshold` against `bg`.

    Always succeeds: the better of black or white is at least 4.58:1 on any background.
    """
    target = (0, 0, 0) if ratio((0, 0, 0), bg) >= ratio((255, 255, 255), bg) else (255, 255, 255)
    for step in range(1, 21):
        t = step / 20
        candidate = tuple(round(f + (to - f) * t) for f, to in zip(fg, target))
        if ratio(candidate, bg) >= threshold:
            return to_hex(candidate)
    return to_hex(target)
