"""Report fields and handoff dispositions shared by the workspace and desktop clients."""
import re

DISPOSITIONS = {"owner-review": ("needs-owner-review",),
                "evidence-review": ("evidence-reviewed", "needs-evidence")}


class ReviewContextRequired(RuntimeError):
    """The caller holds the lease but must read the current context to record a disposition."""


def _field(obj, name, limit=160):
    value = obj.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    value.encode("utf-8")  # Reject unpaired surrogates before any graph mutation.
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"invalid {name}")
    return value.strip()


def _case_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,160}", value):
        raise ValueError("invalid case id")
    return value
