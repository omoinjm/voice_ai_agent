"""Who is calling: verifies the bearer token and loads (or creates) the user row.

Production: Cognito ID tokens, checked against the user pool's JWKS. Platform admins are members of the
`platform_admin` Cognito group. Local development (AUTH_MODE=dev): `Bearer dev:<email>` or `dev:<email>:admin`.
"""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_session
from .models import User

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str | None
    is_admin: bool


@dataclass(frozen=True)
class CurrentUser:
    user: User
    is_admin: bool


@lru_cache
def _jwks_client(region: str, pool_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def verify_token(token: str, settings: Settings) -> Identity:
    if settings.auth_mode == "dev":
        parts = token.split(":")
        if len(parts) not in (2, 3) or parts[0] != "dev" or "@" not in parts[1]:
            raise _unauthorized("Dev tokens look like dev:<email> or dev:<email>:admin")
        email = parts[1].lower()
        return Identity(subject=f"dev:{email}", email=email, name=None, is_admin=parts[2:] == ["admin"])

    issuer = f"https://cognito-idp.{settings.aws_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    try:
        key = _jwks_client(settings.aws_region, settings.cognito_user_pool_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=settings.cognito_app_client_id, issuer=issuer
        )
    except jwt.PyJWTError as e:
        raise _unauthorized(f"Invalid token: {e}") from e
    if claims.get("token_use") != "id":
        raise _unauthorized("Send the Cognito ID token")
    if not claims.get("email_verified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify your email address first")
    return Identity(
        subject=claims["sub"],
        email=claims["email"].lower(),
        name=claims.get("name"),
        is_admin=settings.admin_group in claims.get("cognito:groups", []),
    )


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized("Sign in first")
    identity = verify_token(credentials.credentials, settings)

    user = await session.scalar(select(User).where(User.auth_subject == identity.subject))
    if user is None:
        user = User(auth_subject=identity.subject, email=identity.email, name=identity.name)
        session.add(user)
        await session.commit()
    return CurrentUser(user=user, is_admin=identity.is_admin)


async def require_admin(me: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not me.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform admins only")
    return me
