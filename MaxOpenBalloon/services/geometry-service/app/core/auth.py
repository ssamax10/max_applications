from dataclasses import dataclass
import os

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.core.settings import settings


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    subject: str | None = None


def _allow_tenant_header_fallback() -> bool:
    value = os.getenv('ALLOW_TENANT_HEADER_FALLBACK', 'false').strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def _decode_bearer_token(token: str) -> dict:
    if not settings.oidc_jwks_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='OIDC_JWKS_URL must be configured when auth is enabled',
        )

    jwks_client = jwt.PyJWKClient(settings.oidc_jwks_url)
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
    except (jwt.PyJWKClientError, jwt.PyJWTError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Unable to validate bearer token against JWKS',
        ) from exc

    decode_kwargs: dict[str, object] = {
        'algorithms': ['RS256', 'RS384', 'RS512'],
        'options': {'require': ['exp', 'iat', 'sub']},
    }

    if settings.oidc_audience:
        decode_kwargs['audience'] = settings.oidc_audience
    else:
        decode_kwargs['options'] = {'require': ['exp', 'iat', 'sub'], 'verify_aud': False}

    if settings.oidc_issuer:
        decode_kwargs['issuer'] = settings.oidc_issuer

    try:
        return jwt.decode(token, signing_key, **decode_kwargs)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid bearer token') from exc


def _extract_tenant_from_payload(payload: dict, x_tenant_id: str | None) -> str:
    tenant_id = payload.get(settings.tenant_claim)
    if not isinstance(tenant_id, str) or not tenant_id:
        if _allow_tenant_header_fallback() and x_tenant_id:
            return x_tenant_id
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Missing tenant claim in token')
    return tenant_id


def get_tenant_context(
    authorization: str | None = Header(default=None, alias='Authorization'),
    x_tenant_id: str | None = Header(default=None, alias='X-Tenant-ID'),
) -> TenantContext:
    if settings.auth_required:
        if not authorization or not authorization.lower().startswith('bearer '):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')

        token = authorization.split(' ', 1)[1].strip()
        payload = _decode_bearer_token(token)

        token_tenant = _extract_tenant_from_payload(payload, x_tenant_id)
        if x_tenant_id and x_tenant_id != token_tenant:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Tenant header mismatch')

        subject = payload.get('sub')
        return TenantContext(tenant_id=token_tenant, subject=subject if isinstance(subject, str) else None)

    if not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing tenant header')

    return TenantContext(tenant_id=x_tenant_id, subject=None)


def tenant_context_dependency(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    return context
