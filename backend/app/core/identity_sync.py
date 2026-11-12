"""Synchronize SCIM-managed groups and memberships."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.oidc_group_sync import sync_oidc_group_memberships
from app.models import SCIMGroup, SCIMGroupMembership, User


def scim_group_names(db: Session, user: User) -> list[str]:
    return list(
        db.scalars(
            select(SCIMGroup.display_name)
            .join(SCIMGroupMembership, SCIMGroupMembership.group_id == SCIMGroup.id)
            .where(SCIMGroupMembership.user_id == user.id)
            .order_by(SCIMGroup.display_name)
        ).all()
    )


def sync_group_memberships(db: Session, user: User, group_names: list[str]) -> None:
    """Apply mapped roles under the SCIM source tag."""
    sync_oidc_group_memberships(db, user, group_names, identity_source="scim")
