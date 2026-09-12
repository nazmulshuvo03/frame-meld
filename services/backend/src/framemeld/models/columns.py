import enum

from sqlalchemy import Enum


def enum_column(enum_cls: type[enum.StrEnum], constraint_name: str, length: int = 20) -> Enum:
    """A VARCHAR column with a named CHECK constraint over the enum's *values*.

    All four keyword arguments are load-bearing and each fails silently if
    omitted:

    - `native_enum=False` avoids a Postgres native enum, which would need an
      `ALTER TYPE` migration every time a member is added.
    - `create_constraint=True` is required. SQLAlchemy 2.0 defaults it to
      False, so `native_enum=False` on its own produces a bare, completely
      unconstrained VARCHAR — no validation at any layer.
    - `name` names the CHECK so a later migration can drop and recreate it.
    - `values_callable` stores `'story'` rather than the member name
      `'STORY'`, keeping the database consistent with the API and with the
      values listed in the spec.

    `length` must be at least as long as the longest *value*, not the longest
    member name. Constraint names are schema-global, so prefix them with the
    table: `shot_camera`, not `camera`.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        create_constraint=True,
        name=constraint_name,
        values_callable=lambda e: [member.value for member in e],
    )
