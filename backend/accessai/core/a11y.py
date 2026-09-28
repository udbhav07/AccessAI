"""What needs alt text and what counts as a labelled form field.

Shared so the scraper (deciding what to fix) and the verifier (measuring coverage) agree.
"""

FORM_FIELDS = ("input", "select", "textarea")

# Hidden fields have nothing to name; buttons are named by their own value.
NON_LABELLABLE_TYPES = frozenset({
    "hidden", "submit", "button", "reset", "image",
})


def input_type(inp):
    return (inp.get("type") or "text").lower()


def needs_label(field):
    return field.name != "input" or input_type(field) not in NON_LABELLABLE_TYPES


def form_fields(soup):
    return [f for f in soup.find_all(FORM_FIELDS) if needs_label(f)]


def explicit_label(soup, field):
    if not field.get("id"):
        return None
    return soup.find("label", attrs={"for": field["id"]})


def has_aria_name(field):
    return bool(field.get("aria-label") or field.get("aria-labelledby"))


def is_labelled(soup, field):
    if field.find_parent("label") is not None or has_aria_name(field):
        return True
    match = explicit_label(soup, field)
    return match is not None and bool(match.get_text(strip=True))


def labelling_strategy(soup, field):
    """How to name this field: ``'for'``, ``'aria'`` (no id to point at), or None."""
    if not needs_label(field):
        return None
    if field.find_parent("label") is not None or has_aria_name(field):
        return None
    return "for" if field.get("id") else "aria"


def is_decorative(img):
    """``alt=""`` or a presentation role marks an image screen readers should skip."""
    return (img.get("alt") is not None and not img["alt"].strip()) \
        or (img.get("role") or "").lower() in ("presentation", "none") \
        or (img.get("aria-hidden") or "").lower() == "true"


def has_alt(img):
    return bool((img.get("alt") or "").strip())


def needs_alt(img):
    return not has_alt(img) and not is_decorative(img)
