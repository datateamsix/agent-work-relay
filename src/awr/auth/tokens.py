from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from ..settings import AWR_SCOPES, Settings

try:
    import jwt
    from jwt import PyJWKClient
except ImportError:  # pragma: no cover - optional hosted extra
    jwt = None  # type: ignore[assignment]
    PyJWKClient = None  # type: ignore[misc, assignment]


class AuthError(Exception):
    def __init__(
        self, status_code: int, error: str, description: str, scope: str | None = None
    ) -> None:
        super().__init__(description)
        self.status_code = status_code
        self.error = error
        self.description = description
        self.scope = scope


class Principal:
    def __init__(
        self, subject: str, client_id: str, scopes: frozenset[str], claims: dict[str, Any]
    ) -> None:
        self.subject = subject
        self.client_id = client_id
        self.scopes = scopes
        self.claims = claims


def _scopes_from_claims(claims: dict[str, Any]) -> frozenset[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return frozenset(part for part in scope.split() if part)
    scp = claims.get("scp")
    if isinstance(scp, list):
        return frozenset(str(item) for item in scp)
    return frozenset()


def _audiences(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwk_client: Any | None = None

    def verify(self, token: str) -> Principal:
        if self.settings.auth_mode == "static":
            return self._verify_static(token)
        return self._verify_jwt(token)

    def _verify_static(self, token: str) -> Principal:
        expected = self.settings.static_token
        if not expected or token != expected:
            raise AuthError(401, "invalid_token", "The access token is invalid.")
        return Principal(
            subject="static-operator",
            client_id="awr-static",
            scopes=frozenset(AWR_SCOPES),
            claims={
                "iss": "awr-static",
                "aud": self.settings.oauth_audience or self.settings.resource_url,
            },
        )

    def _verify_jwt(self, token: str) -> Principal:
        if jwt is None:
            raise AuthError(401, "invalid_token", "JWT verification is unavailable.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(401, "invalid_token", "The access token is malformed.") from exc
        key = self._signing_key(token, header)
        issuer = self.settings.oauth_issuer
        audience = self.settings.oauth_audience
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(401, "invalid_token", "The access token has expired.") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError(401, "invalid_token", "The access token issuer is invalid.") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError(401, "invalid_token", "The access token audience is invalid.") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(401, "invalid_token", "The access token is invalid.") from exc
        if issuer and claims.get("iss") != issuer:
            raise AuthError(401, "invalid_token", "The access token issuer is invalid.")
        if audience and audience not in _audiences(claims.get("aud")):
            raise AuthError(401, "invalid_token", "The access token audience is invalid.")
        if int(claims["exp"]) <= int(time.time()):
            raise AuthError(401, "invalid_token", "The access token has expired.")
        scopes = _scopes_from_claims(claims)
        subject = str(claims.get("sub") or claims.get("client_id") or "unknown")
        client_id = str(claims.get("azp") or claims.get("client_id") or subject)
        return Principal(subject=subject, client_id=client_id, scopes=scopes, claims=claims)

    def _signing_key(self, token: str, header: dict[str, Any]) -> Any:
        if self.settings.extra_jwks:
            kid = header.get("kid")
            keys = self.settings.extra_jwks.get("keys", [])
            if isinstance(keys, list):
                for item in keys:
                    if isinstance(item, dict) and item.get("kid") == kid:
                        assert jwt is not None
                        return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
            raise AuthError(401, "invalid_token", "The access token signing key is unknown.")
        if PyJWKClient is None:
            raise AuthError(401, "invalid_token", "JWKS verification is unavailable.")
        jwks_url = self.settings.oauth_jwks_url or _jwks_url_from_issuer(self.settings.oauth_issuer)
        if not jwks_url:
            raise AuthError(401, "invalid_token", "JWKS URL is not configured.")
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
        try:
            return self._jwk_client.get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise AuthError(
                401, "invalid_token", "The access token signing key is unknown."
            ) from exc


def _jwks_url_from_issuer(issuer: str | None) -> str | None:
    if not issuer:
        return None
    parsed = urlparse(issuer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{issuer.rstrip('/')}/.well-known/jwks.json"


def require_scope(principal: Principal, required: str | None) -> None:
    if not principal.scopes.intersection(AWR_SCOPES):
        raise AuthError(
            403,
            "insufficient_scope",
            "The access token is missing required AWR scopes.",
            scope=" ".join(AWR_SCOPES),
        )
    if required and required not in principal.scopes:
        raise AuthError(
            403,
            "insufficient_scope",
            f"The access token is missing required scope: {required}.",
            scope=required,
        )
