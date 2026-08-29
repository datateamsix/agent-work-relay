from __future__ import annotations

from datetime import UTC, datetime, timedelta

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm
except ImportError:  # pragma: no cover
    jwt = None  # type: ignore[assignment]
    rsa = None  # type: ignore[assignment]
    RSAAlgorithm = None  # type: ignore[misc, assignment]

ISSUER = "https://auth.example.test"
AUDIENCE = "https://awr.example.test/mcp"


def jwt_available() -> bool:
    return jwt is not None and rsa is not None


def rsa_pair() -> tuple[object, dict[str, object]]:
    assert rsa is not None
    assert RSAAlgorithm is not None
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk["kid"] = "awr-test-key"
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"
    return private_key, {"keys": [public_jwk]}


def signed_token(private_key: object, **claims: object) -> str:
    assert jwt is not None
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "planner-1",
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "scope": "awr:plan awr:read awr:refresh awr:response awr:decide",
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "awr-test-key"})
