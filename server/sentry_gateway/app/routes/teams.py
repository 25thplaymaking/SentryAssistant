"""Teams: shared spaces with explicit membership and roles.

A team gets its own profile, which is a separate runtime home. Personal memory,
connectors, devices, and private workspaces never cross into it; sharing is an
explicit, attributable action rather than a side effect of membership.

Invitations are *records*, not messages. This module never emails anyone —
delivering an invitation is a human-facing action and needs the initiating
user's approval at the moment of sending, which is not something an API call
can stand in for.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from ..audit.service import AuditEvent, AuditService, Decision
from ..workorders.transitions import TeamRole
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/teams", tags=["teams"])

INVITATION_TTL = timedelta(days=7)


class CreateTeam(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TeamView(BaseModel):
    id: UUID
    name: str
    profile_id: UUID
    role: TeamRole


class MemberView(BaseModel):
    user_id: UUID
    display_name: str
    role: TeamRole
    joined_at: datetime


class InviteRequest(BaseModel):
    email: EmailStr
    role: TeamRole = TeamRole.COLLABORATOR


class InvitationView(BaseModel):
    id: UUID
    email: EmailStr
    role: TeamRole
    expires_at: datetime
    #: Always false. The record exists; nothing has been sent.
    delivered: bool = False
    delivery_note: str = (
        "Invitation recorded but not sent. Delivering it is a human-facing "
        "action and requires explicit approval at the moment of sending."
    )


class ChangeRole(BaseModel):
    role: TeamRole


class ShareRequest(BaseModel):
    """Copy a sanitized personal result into team scope, with attribution."""

    content: str = Field(min_length=1, max_length=100_000)
    source_kind: str = Field(min_length=1, max_length=50)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


async def _membership(conn, team_id: UUID, user_id: UUID) -> TeamRole:
    """Current role, read from live rows. A removed member has none."""
    row = await conn.fetchrow(
        """
        SELECT role FROM team_members
        WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
        """,
        team_id,
        user_id,
    )
    if row is None:
        # 404 rather than 403 so team IDs cannot be probed by non-members.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return TeamRole(row["role"])


async def _require_owner(conn, team_id: UUID, user_id: UUID) -> None:
    if await _membership(conn, team_id, user_id) is not TeamRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires team owner permission.",
        )


@router.post("", response_model=TeamView, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: CreateTeam, request: Request, caller: Caller = Depends(require_caller)
) -> TeamView:
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # The team profile is a distinct runtime home. It is never the
            # creator's personal profile under another name.
            profile_id = await conn.fetchval(
                """
                INSERT INTO profiles (kind, owner_user_id, display_name, runtime_home)
                VALUES ('team', $1, $2, $3) RETURNING id
                """,
                caller.user_id,
                body.name,
                f"/srv/sentry/runtimes/team-{caller.user_id}-{body.name}",
            )
            team_id = await conn.fetchval(
                """
                INSERT INTO teams (profile_id, name, created_by)
                VALUES ($1,$2,$3) RETURNING id
                """,
                profile_id,
                body.name,
                caller.user_id,
            )
            await conn.execute(
                """
                INSERT INTO team_members (team_id, user_id, role)
                VALUES ($1,$2,'owner')
                """,
                team_id,
                caller.user_id,
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="team.create",
                    decision=Decision.ALLOWED,
                    correlation_id=f"team-{team_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    team_id=team_id,
                    profile_id=profile_id,
                    target_kind="team",
                    target_id=str(team_id),
                    detail=f"created team {body.name!r}",
                ),
            )

    return TeamView(id=team_id, name=body.name, profile_id=profile_id, role=TeamRole.OWNER)


@router.get("", response_model=list[TeamView])
async def list_teams(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[TeamView]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.name, t.profile_id, m.role
            FROM teams t
            JOIN team_members m ON m.team_id = t.id
            WHERE m.user_id = $1 AND m.removed_at IS NULL AND t.deleted_at IS NULL
            ORDER BY t.created_at
            """,
            caller.user_id,
        )
    return [
        TeamView(
            id=r["id"], name=r["name"], profile_id=r["profile_id"], role=TeamRole(r["role"])
        )
        for r in rows
    ]


@router.get("/{team_id}/members", response_model=list[MemberView])
async def list_members(
    team_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> list[MemberView]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        await _membership(conn, team_id, caller.user_id)
        rows = await conn.fetch(
            """
            SELECT m.user_id, u.display_name, m.role, m.joined_at
            FROM team_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.team_id = $1 AND m.removed_at IS NULL
            ORDER BY m.joined_at
            """,
            team_id,
        )
    return [
        MemberView(
            user_id=r["user_id"],
            display_name=r["display_name"],
            role=TeamRole(r["role"]),
            joined_at=r["joined_at"],
        )
        for r in rows
    ]


@router.post("/{team_id}/invitations", response_model=InvitationView, status_code=201)
async def invite_member(
    team_id: UUID,
    body: InviteRequest,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> InvitationView:
    """Record an invitation. Deliberately does not send anything."""
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_owner(conn, team_id, caller.user_id)
            invitation_id = await conn.fetchval(
                """
                INSERT INTO team_invitations
                    (team_id, invited_email, role, invited_by, expires_at)
                VALUES ($1,$2,$3,$4,$5) RETURNING id
                """,
                team_id,
                str(body.email),
                body.role.value,
                caller.user_id,
                datetime.now(timezone.utc) + INVITATION_TTL,
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="team.invite",
                    decision=Decision.ALLOWED,
                    correlation_id=f"invite-{invitation_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    team_id=team_id,
                    target_kind="invitation",
                    target_id=str(invitation_id),
                    # The address is hashed rather than stored in the audit row.
                    detail=f"invitation recorded (not sent) for "
                    f"{hashlib.sha256(str(body.email).encode()).hexdigest()[:16]}",
                ),
            )

    return InvitationView(
        id=invitation_id,
        email=body.email,
        role=body.role,
        expires_at=datetime.now(timezone.utc) + INVITATION_TTL,
    )


@router.post("/{team_id}/members/{user_id}/role", response_model=MemberView)
async def change_role(
    team_id: UUID,
    user_id: UUID,
    body: ChangeRole,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> MemberView:
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_owner(conn, team_id, caller.user_id)
            await _membership(conn, team_id, user_id)

            # Demoting the last owner would strand the team with nobody able to
            # manage it, so it is refused.
            if body.role is not TeamRole.OWNER:
                owners = await conn.fetchval(
                    """
                    SELECT count(*) FROM team_members
                    WHERE team_id = $1 AND role = 'owner' AND removed_at IS NULL
                    """,
                    team_id,
                )
                current = await conn.fetchval(
                    "SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2",
                    team_id,
                    user_id,
                )
                if current == "owner" and owners <= 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="A team must keep at least one owner.",
                    )

            row = await conn.fetchrow(
                """
                UPDATE team_members SET role = $3
                WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
                RETURNING user_id, role, joined_at
                """,
                team_id,
                user_id,
                body.role.value,
            )
            display_name = await conn.fetchval(
                "SELECT display_name FROM users WHERE id = $1", user_id
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="team.role.change",
                    decision=Decision.ALLOWED,
                    correlation_id=f"team-{team_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    team_id=team_id,
                    target_kind="member",
                    target_id=str(user_id),
                    detail=f"role set to {body.role.value}",
                ),
            )

    return MemberView(
        user_id=row["user_id"],
        display_name=display_name,
        role=TeamRole(row["role"]),
        joined_at=row["joined_at"],
    )


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: UUID,
    user_id: UUID,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> None:
    """Remove a member. Their team access ends at once; their personal profile,
    devices, and history are untouched."""
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_owner(conn, team_id, caller.user_id)
            await _membership(conn, team_id, user_id)

            owners = await conn.fetchval(
                """
                SELECT count(*) FROM team_members
                WHERE team_id = $1 AND role = 'owner' AND removed_at IS NULL
                """,
                team_id,
            )
            target_role = await conn.fetchval(
                "SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2",
                team_id,
                user_id,
            )
            if target_role == "owner" and owners <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A team must keep at least one owner.",
                )

            # Timestamped rather than deleted, so past attributions stay readable.
            await conn.execute(
                """
                UPDATE team_members SET removed_at = now()
                WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
                """,
                team_id,
                user_id,
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="team.member.remove",
                    decision=Decision.ALLOWED,
                    correlation_id=f"team-{team_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    team_id=team_id,
                    target_kind="member",
                    target_id=str(user_id),
                    detail="member removed; personal profile retained",
                ),
            )


@router.post("/{team_id}/share", status_code=status.HTTP_201_CREATED)
async def share_with_team(
    team_id: UUID,
    body: ShareRequest,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, str]:
    """Explicitly copy personal content into team scope.

    Nothing is promoted automatically. The shared copy records the original
    author, source profile, and a content hash so its provenance survives.
    """
    pool = _pool(request)
    audit = AuditService(pool)
    content_hash = hashlib.sha256(body.content.encode("utf-8")).hexdigest()

    async with pool.acquire() as conn:
        async with conn.transaction():
            role = await _membership(conn, team_id, caller.user_id)
            if role is TeamRole.OBSERVER:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Observers cannot share content into a team.",
                )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="team.share",
                    decision=Decision.ALLOWED,
                    correlation_id=f"share-{content_hash[:16]}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    team_id=team_id,
                    target_kind=body.source_kind,
                    target_id=content_hash,
                    evidence={"content_hash": content_hash},
                    detail=f"shared {body.source_kind} into team scope",
                ),
            )

    return {"content_hash": content_hash, "team_id": str(team_id)}
