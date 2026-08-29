from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from .clock import Clock, UtcClock
from .contracts import ArtifactUploadTicket
from .errors import ArtifactTicketError
from .ports import ArtifactMetadataStore


def hash_upload_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_upload_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_upload_token(token)


class TicketService:
    def __init__(
        self,
        metadata: ArtifactMetadataStore,
        *,
        clock: Clock | None = None,
        ttl_seconds: float = 900.0,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.metadata = metadata
        self.clock = clock or UtcClock()
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes

    def issue(self, artifact_id: str, owner: str) -> tuple[ArtifactUploadTicket, str]:
        token, token_hash = issue_upload_token()
        expires_at = (self.clock.now() + timedelta(seconds=self.ttl_seconds)).isoformat()
        ticket = ArtifactUploadTicket(
            ticket_id=f"tkt-{uuid4()}",
            artifact_id=artifact_id,
            owner=owner,
            token_hash=token_hash,
            expires_at=expires_at,
            spent_at=None,
            max_bytes=self.max_bytes,
        )
        return self.metadata.put_upload_ticket(ticket), token

    def require_open(
        self, artifact_id: str, *, actor: str, token: str, now: datetime | None = None
    ) -> ArtifactUploadTicket:
        ticket = self.metadata.get_upload_ticket(artifact_id)
        if ticket is None:
            raise ArtifactTicketError("Upload ticket is missing.")
        if ticket.owner != actor:
            raise ArtifactTicketError("Upload ticket belongs to another actor.")
        if ticket.token_hash != hash_upload_token(token):
            raise ArtifactTicketError("Upload ticket token is invalid.")
        moment = now or self.clock.now()
        if ticket.spent_at is not None:
            raise ArtifactTicketError("Upload ticket has already been spent.")
        expires = datetime.fromisoformat(ticket.expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=moment.tzinfo)
        if expires <= moment:
            raise ArtifactTicketError("Upload ticket has expired.")
        return ticket

    def spend(self, artifact_id: str, *, now: datetime | None = None) -> ArtifactUploadTicket:
        return self.metadata.spend_upload_ticket(artifact_id, now=now or self.clock.now())
