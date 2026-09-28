"""The accessibility engine: fetch a page, fix it and verify the fix. Kept free of Flask;
see `accessai.api`. Defines the attributes that track elements from the original to the fixed page.
"""

# Stamped on every element before fixing so the verifier can match old to new.
# nth-child selectors won't do: an inserted <label> shifts its siblings.
ID_ATTR = "data-aai-id"

# Marks nodes the fixer inserted.
NEW_ATTR = "data-aai-new"
