"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""

from framemeld.models.user import Session, User  # noqa: F401

__all__ = ["Session", "User"]
