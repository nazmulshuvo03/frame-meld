from pydantic import field_validator


class _RejectsExplicitNull:
    """Shared by every PATCH schema. Every field on all of them is optional in
    the sense of "may be omitted", not "may be cleared to nothing": each
    backs a NOT NULL column with a "" default, so send "" to empty a text
    field. `model_dump(exclude_unset=True)` excludes fields the client
    *omitted*, not fields it explicitly set to null — so an unguarded
    `{"heading": null}` reaches `setattr(row, name, None)` against a NOT
    NULL column and raises IntegrityError at flush, a 500 instead of a 422.

    A `mode="before"` validator does not run for a field the client
    omitted, only for one it actually sent — checked against the installed
    pydantic (2.13.5): an omitted field keeps its default untouched and
    unvalidated, so this rejects null without disturbing the
    omitted-means-unchanged path that `exclude_unset` relies on.

    Lives here rather than in one router's schema module because the
    subtlety above must not exist in two versions that can drift apart.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_not_a_value(cls, value: object) -> object:
        if value is None:
            raise ValueError('null is not a valid value — omit the field, or send ""')
        return value
