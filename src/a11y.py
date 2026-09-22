"""Shared definitions of what counts as an accessible name.

The scraper decides which inputs to fix and the verifier decides which inputs
are covered. Those were two separate pieces of code answering the same
question differently -- the verifier accepted a wrapping ``<label>`` and the
scraper did not, so a correctly wrapped input got a second label inserted in
front of it and the verifier called the result an improvement. One definition,
used by both.
"""

# An input a label would make no sense on. A hidden field has nothing to name,
# and a button's own value or text is already its accessible name -- naming
# them would add noise to a screen reader, not remove it.
NON_LABELLABLE_TYPES = frozenset({
    "hidden", "submit", "button", "reset", "image",
})


def input_type(inp):
    """The effective type. An absent or unknown type renders as text."""
    return (inp.get("type") or "text").lower()


def needs_label(inp):
    return input_type(inp) not in NON_LABELLABLE_TYPES


def explicit_label(soup, inp):
    """The ``<label for=...>`` pointing at this input, if there is one."""
    if not inp.get("id"):
        return None
    return soup.find("label", attrs={"for": inp["id"]})


def is_labelled(soup, inp):
    """Does this input already have an accessible name from any source?"""
    if inp.find_parent("label") is not None:
        return True
    if inp.get("aria-label") or inp.get("aria-labelledby"):
        return True
    match = explicit_label(soup, inp)
    return match is not None and bool(match.get_text(strip=True))


def labelling_strategy(soup, inp):
    """How to give this input a name: ``'for'``, ``'aria'``, or None to skip.

    ``'for'``  -- it has an id, so a ``<label for>`` can point at it. The
                  caller still has to decide between writing a new one and
                  rewriting the one that is already there.
    ``'aria'`` -- no id, so no label can reach it. ``aria-label`` needs none,
                  and setting an attribute inserts no node, so it cannot shift
                  the layout the way an inserted ``<label>`` does. Without
                  this, every input without an id was skipped outright while
                  still counting against coverage -- which is most inputs on
                  most real forms.
    ``None``   -- nothing to do, or nothing we should touch.
    """
    if not needs_label(inp):
        return None
    if inp.find_parent("label") is not None:
        return None          # already named by its wrapper; a second is worse
    if inp.get("id"):
        return "for"
    if inp.get("aria-label") or inp.get("aria-labelledby"):
        return None
    return "aria"
