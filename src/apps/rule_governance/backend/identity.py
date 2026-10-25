"""Persist trusted caller IDs separately from display names during the App migration.

Only entry points may supply these IDs from an authenticated CurrentUser. Legacy
internal calls remain explicitly unattributed; a username is never an ID fallback.
These records are provenance, not proof of a current App grant.
"""


def actor_user_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("authenticated actor user ID must be a non-empty string")
    return value.strip()
