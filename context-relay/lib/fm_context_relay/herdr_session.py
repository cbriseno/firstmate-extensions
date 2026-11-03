"""Canonical identity for Herdr sessions used by context-relay.

Herdr represents its default session as JSON null, while operators name it
"default". Those two values identify the same session; named sessions remain
exact strings and malformed values are rejected.
"""


def normalize(value):
    if value is None:
        return "default"
    if not isinstance(value, str) or not value or value.strip() != value or any(c.isspace() for c in value):
        raise ValueError("Herdr session is invalid")
    return "default" if value == "default" else value
