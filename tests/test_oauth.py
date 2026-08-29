from __future__ import annotations

import time
import unittest

from oauth_helpers import AUDIENCE, ISSUER, jwt_available, rsa_pair, signed_token

from awr.auth.tokens import AuthError, TokenVerifier, require_scope
from awr.settings import Settings


def _settings(jwks: dict[str, object]) -> Settings:
    return Settings(
        env="test",
        auth_mode="oauth",
        public_base_url="https://awr.example.test",
        oauth_issuer=ISSUER,
        oauth_audience=AUDIENCE,
        extra_jwks=jwks,
    )


@unittest.skipUnless(jwt_available(), "JWT extra is required")
class JwtTokenVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, self.jwks = rsa_pair()
        self.verifier = TokenVerifier(_settings(self.jwks))

    def test_valid_token(self) -> None:
        principal = self.verifier.verify(signed_token(self.private_key))
        self.assertEqual(principal.subject, "planner-1")
        self.assertIn("awr:plan", principal.scopes)

    def test_expired_token(self) -> None:
        token = signed_token(self.private_key, exp=int(time.time()) - 30)
        with self.assertRaises(AuthError) as caught:
            self.verifier.verify(token)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("expired", caught.exception.description.lower())

    def test_wrong_issuer(self) -> None:
        token = signed_token(self.private_key, iss="https://evil.example.test")
        with self.assertRaises(AuthError) as caught:
            self.verifier.verify(token)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("issuer", caught.exception.description.lower())

    def test_wrong_audience(self) -> None:
        token = signed_token(self.private_key, aud="https://other.example.test")
        with self.assertRaises(AuthError) as caught:
            self.verifier.verify(token)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("audience", caught.exception.description.lower())

    def test_insufficient_scope(self) -> None:
        principal = self.verifier.verify(signed_token(self.private_key, scope="awr:read"))
        with self.assertRaises(AuthError) as caught:
            require_scope(principal, "awr:plan")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.error, "insufficient_scope")


class StaticTokenTests(unittest.TestCase):
    def test_static_token_is_rejected_in_production(self) -> None:
        with self.assertRaisesRegex(Exception, "Static token"):
            Settings(
                env="production",
                auth_mode="static",
                public_base_url="https://awr.example.test",
                static_token="secret",
                storage="firestore",
            ).validate()

    def test_static_token_grants_all_scopes(self) -> None:
        settings = Settings(env="test", auth_mode="static", static_token="local-dev-token")
        principal = TokenVerifier(settings).verify("local-dev-token")
        self.assertEqual(
            principal.scopes,
            frozenset({"awr:plan", "awr:read", "awr:refresh", "awr:response", "awr:decide"}),
        )


if __name__ == "__main__":
    unittest.main()
