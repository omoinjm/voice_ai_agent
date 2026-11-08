"""Businesses (tenants): sign-up creates one, owned by the signed-in user, with a draft application."""

import re
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, current_user
from ..db import get_session
from ..models import BUSINESS_CATEGORIES, Application, AuditLog, Membership, Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(pattern=f"^({'|'.join(BUSINESS_CATEGORIES)})$")


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    category: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


def slugify(name: str) -> str:
    """A slug that fits the tenants.slug constraint: lowercase, starts with a letter, 3-32 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24].strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"b-{slug}".strip("-")
    return slug if len(slug) >= 3 else f"{slug}-biz"


async def get_tenant_for(tenant_id: uuid.UUID, me: CurrentUser, session: AsyncSession) -> Tenant:
    """The tenant, if the caller is an admin or a member of it (404 otherwise, so ids don't leak)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is not None and not me.is_admin:
        member = await session.get(Membership, (me.user.id, tenant_id))
        if member is None:
            tenant = None
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such business")
    return tenant


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    me: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    base = slugify(body.name)
    slug = base
    while await session.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(2)}"

    tenant = Tenant(slug=slug, name=body.name.strip(), category=body.category)
    tenant.memberships.append(Membership(user_id=me.user.id, role="owner"))
    tenant.application = Application(data={"trading_name": body.name.strip(), "category": body.category})
    session.add(tenant)
    await session.flush()
    session.add(AuditLog(actor_id=me.user.id, tenant_id=tenant.id, action="tenant.created", detail={"slug": slug}))
    await session.commit()
    return tenant


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    me: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[Tenant]:
    query = select(Tenant).order_by(Tenant.created_at.desc())
    if not me.is_admin:
        query = query.join(Membership).where(Membership.user_id == me.user.id)
    return list(await session.scalars(query))


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: uuid.UUID, me: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Tenant:
    return await get_tenant_for(tenant_id, me, session)
