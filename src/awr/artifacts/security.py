from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import uuid4

from ..responses.cache import security_receipt_cache_key
from ..responses.contracts import POLICY_VERSION
from .clock import Clock, UtcClock
from .contracts import (
    REASON_EXTENSION_MISMATCH,
    REASON_MALWARE,
    REASON_POLYGLOT,
    REASON_SCANNER_TIMEOUT,
    REASON_SCANNER_UNAVAILABLE,
    REASON_SIZE,
    REASON_TAMPERING,
    REASON_TYPE_DISALLOWED,
    REASON_TYPE_MISMATCH,
    Artifact,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
    is_rejection,
    status_from_security_receipt,
)
from .detect import DetectionResult, detect_family
from .errors import ArtifactAccessError, ArtifactConflictError
from .policy import control_authority_diagnostics, type_conflict_reason
from .ports import ArtifactBodyStore, ArtifactMetadataStore
from .scan import (
    ScanOutcome,
    ScanResult,
    SecurityScanner,
    assert_never_scan_outcome,
)
from .validate import validate_payload

_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class _Decision:
    status: ArtifactStatus
    verdict: ArtifactSecurityVerdict
    reason_codes: tuple[str, ...]
    detection: DetectionResult | None
    scan: ScanResult
    diagnostics: dict[str, object]


class ArtifactSecurityService:
    """Only path that may move an artifact from QUARANTINED to SCANNING or CLEAN."""

    def __init__(
        self,
        metadata: ArtifactMetadataStore,
        bodies: ArtifactBodyStore,
        scanner: SecurityScanner,
        *,
        clock: Clock | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        self.metadata = metadata
        self.bodies = bodies
        self.scanner = scanner
        self.clock = clock or UtcClock()
        self.max_bytes = max_bytes
        self.lease_ttl_seconds = lease_ttl_seconds

    def inspect(self, artifact_id: str) -> ArtifactSecurityReceipt:
        current = self.metadata.get(artifact_id)
        if current is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        existing = self._existing_terminal_receipt(current)
        if existing is not None:
            return self._ensure_clean_body(existing)

        lease_id = str(uuid4())
        claim = self.metadata.claim_scan_lease(
            artifact_id,
            now=self.clock.now(),
            lease_ttl_seconds=self.lease_ttl_seconds,
            lease_id=lease_id,
        )
        if claim is None:
            refreshed = self.metadata.get(artifact_id)
            if refreshed is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            terminal = self._existing_terminal_receipt(refreshed)
            if terminal is not None:
                return terminal
            raise ArtifactConflictError(
                f"Artifact {artifact_id} is already being scanned by another worker."
            )

        if (
            claim.already_scanned
            and claim.existing_receipt is not None
            and self._receipt_reusable(claim.existing_receipt)
        ):
            return self._finish_from_receipt(
                claim.artifact_id, claim.lease_id, claim.existing_receipt
            )

        started_at = self.clock.now().isoformat()
        artifact = self.metadata.get(artifact_id)
        try:
            decision = self._evaluate(artifact)
        except ArtifactAccessError:
            decision = self._rejected(
                ArtifactStatus.REJECTED_TAMPERING,
                ArtifactSecurityVerdict.INCONCLUSIVE,
                (REASON_TAMPERING,),
                _unavailable_scan("missing_quarantine"),
                None,
                b"",
                detail="missing_quarantine",
            )
        except Exception:  # noqa: BLE001
            decision = self._rejected(
                ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
                ArtifactSecurityVerdict.UNAVAILABLE,
                (REASON_SCANNER_UNAVAILABLE,),
                _unavailable_scan("scanner_exception"),
                None,
                b"",
                detail="scanner_exception",
            )
        completed_at = self.clock.now().isoformat()
        diagnostics = decision_status_diagnostics(decision.status, decision.diagnostics)
        diagnostics["receipt_cache_key"] = security_receipt_cache_key(
            sha256=claim.generation_sha256,
            scanner_id=decision.scan.engine,
            scanner_version=decision.scan.engine_version,
            signature_version=decision.scan.signature_version,
        )
        receipt = ArtifactSecurityReceipt(
            receipt_id=f"scr-{uuid4()}",
            artifact_id=artifact_id,
            scanner_id=decision.scan.engine,
            scanner_version=decision.scan.engine_version,
            signature_version=decision.scan.signature_version,
            verdict=decision.verdict,
            reason_codes=decision.reason_codes,
            scanned_sha256=claim.generation_sha256,
            started_at=started_at,
            completed_at=completed_at,
            diagnostics=diagnostics,
        )
        stored = self.metadata.put_security_receipt(receipt)
        if stored.receipt_id != receipt.receipt_id:
            return self._finish_from_receipt(artifact_id, claim.lease_id, stored)
        status = self._promote_clean_or_reject(
            artifact_id, claim.generation_sha256, status_from_security_receipt(stored)
        )
        detected = stored.diagnostics.get("detected_media_type")
        media_type = detected if isinstance(detected, str) else None
        self.metadata.complete_scan(
            artifact_id,
            lease_id=claim.lease_id,
            status=status,
            detected_media_type=media_type,
            now=self.clock.now(),
        )
        return stored

    def _existing_terminal_receipt(self, artifact: Artifact) -> ArtifactSecurityReceipt | None:
        if artifact.status is ArtifactStatus.CLEAN or is_rejection(artifact.status):
            if artifact.sha256:
                receipt = self.metadata.get_security_receipt_for_digest(
                    artifact.artifact_id, artifact.sha256
                )
                if receipt is not None and self._receipt_reusable(receipt):
                    return receipt
            receipts = self.metadata.list_security_receipts(artifact.artifact_id)
            reusable = [item for item in receipts if self._receipt_reusable(item)]
            if reusable:
                return reusable[-1]
        return None

    @staticmethod
    def _receipt_reusable(receipt: ArtifactSecurityReceipt) -> bool:
        expected = security_receipt_cache_key(
            sha256=receipt.scanned_sha256,
            scanner_id=receipt.scanner_id,
            scanner_version=receipt.scanner_version,
            signature_version=receipt.signature_version,
        )
        stored = receipt.diagnostics.get("receipt_cache_key")
        if stored is None:
            return True
        return stored == expected if isinstance(stored, str) else False

    def _ensure_clean_body(self, receipt: ArtifactSecurityReceipt) -> ArtifactSecurityReceipt:
        status = status_from_security_receipt(receipt)
        if status is ArtifactStatus.CLEAN and not self.bodies.has_clean(
            receipt.artifact_id, receipt.scanned_sha256
        ):
            self.bodies.promote_clean(receipt.artifact_id, receipt.scanned_sha256)
        return receipt

    def _promote_clean_or_reject(
        self, artifact_id: str, sha256: str, status: ArtifactStatus
    ) -> ArtifactStatus:
        if status is not ArtifactStatus.CLEAN:
            return status
        try:
            if not self.bodies.has_clean(artifact_id, sha256):
                self.bodies.promote_clean(artifact_id, sha256)
        except ArtifactAccessError:
            return ArtifactStatus.REJECTED_TAMPERING
        return status

    def _finish_from_receipt(
        self,
        artifact_id: str,
        lease_id: str,
        receipt: ArtifactSecurityReceipt,
    ) -> ArtifactSecurityReceipt:
        current = self.metadata.get(artifact_id)
        if current is not None and (
            current.status is ArtifactStatus.CLEAN or is_rejection(current.status)
        ):
            return self._ensure_clean_body(receipt)
        status = self._promote_clean_or_reject(
            artifact_id, receipt.scanned_sha256, status_from_security_receipt(receipt)
        )
        detected = receipt.diagnostics.get("detected_media_type")
        media_type = detected if isinstance(detected, str) else None
        self.metadata.complete_scan(
            artifact_id,
            lease_id=lease_id,
            status=status,
            detected_media_type=media_type,
            now=self.clock.now(),
        )
        return receipt

    def _evaluate(self, artifact: Artifact | None) -> _Decision:
        if artifact is None or not artifact.sha256:
            scan = _unavailable_scan("missing_generation")
            return _Decision(
                status=ArtifactStatus.REJECTED_TAMPERING,
                verdict=ArtifactSecurityVerdict.INCONCLUSIVE,
                reason_codes=(REASON_TAMPERING,),
                detection=None,
                scan=scan,
                diagnostics=_diagnostics(scan, None, extra={"detail": "missing_generation"}),
            )
        payload = self._read_quarantine(artifact.artifact_id, artifact.sha256)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != artifact.sha256 or (
            artifact.byte_length is not None and len(payload) != artifact.byte_length
        ):
            scan = _clean_placeholder(self.scanner)
            return self._rejected(
                ArtifactStatus.REJECTED_TAMPERING,
                ArtifactSecurityVerdict.INCONCLUSIVE,
                (REASON_TAMPERING,),
                scan,
                None,
                payload,
            )
        if len(payload) > self.max_bytes:
            scan = _clean_placeholder(self.scanner)
            return self._rejected(
                ArtifactStatus.REJECTED_SIZE,
                ArtifactSecurityVerdict.INCONCLUSIVE,
                (REASON_SIZE,),
                scan,
                None,
                payload,
            )
        detection = detect_family(
            payload,
            declared_media_type=artifact.declared_media_type,
            original_filename=artifact.original_filename,
        )
        conflict = type_conflict_reason(
            declared_media_type=artifact.declared_media_type,
            original_filename=artifact.original_filename,
            detection=detection,
        )
        if conflict is not None:
            reason = _type_reason(conflict, detection)
            scan = _clean_placeholder(self.scanner)
            return self._rejected(
                ArtifactStatus.REJECTED_TYPE,
                ArtifactSecurityVerdict.INCONCLUSIVE,
                (reason,),
                scan,
                detection,
                payload,
            )
        scan = self.scanner.scan(payload)
        malware = _scan_rejection(scan)
        if malware is not None:
            status, verdict, reasons = malware
            return self._rejected(status, verdict, reasons, scan, detection, payload)
        validation = validate_payload(detection.family, payload)
        if not validation.ok:
            return self._rejected(
                validation.status,
                _verdict_for_status(validation.status),
                (validation.reason_code,) if validation.reason_code else (),
                scan,
                detection,
                payload,
                detail=validation.detail,
            )
        return _Decision(
            status=ArtifactStatus.CLEAN,
            verdict=ArtifactSecurityVerdict.CLEAN,
            reason_codes=(),
            detection=detection,
            scan=scan,
            diagnostics=_diagnostics(scan, detection, payload=payload),
        )

    def _read_quarantine(self, artifact_id: str, sha256: str) -> bytes:
        chunks: list[bytes] = []
        with self.bodies.open_quarantine(artifact_id, sha256) as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def _rejected(
        self,
        status: ArtifactStatus,
        verdict: ArtifactSecurityVerdict,
        reasons: tuple[str, ...],
        scan: ScanResult,
        detection: DetectionResult | None,
        payload: bytes,
        *,
        detail: str | None = None,
    ) -> _Decision:
        extra: dict[str, object] = {}
        if detail:
            extra["detail"] = detail
        return _Decision(
            status=status,
            verdict=verdict,
            reason_codes=reasons,
            detection=detection,
            scan=scan,
            diagnostics=_diagnostics(scan, detection, payload=payload, extra=extra),
        )


def _type_reason(conflict: str, detection: DetectionResult) -> str:
    if conflict == "polyglot" or detection.polyglot:
        return REASON_POLYGLOT
    if conflict == "extension":
        return REASON_EXTENSION_MISMATCH
    if conflict == "disallowed":
        return REASON_TYPE_DISALLOWED
    return REASON_TYPE_MISMATCH


def _scan_rejection(
    scan: ScanResult,
) -> tuple[ArtifactStatus, ArtifactSecurityVerdict, tuple[str, ...]] | None:
    match scan.outcome:
        case ScanOutcome.CLEAN:
            return None
        case ScanOutcome.INFECTED:
            return (
                ArtifactStatus.REJECTED_MALWARE,
                ArtifactSecurityVerdict.MALICIOUS,
                (REASON_MALWARE,),
            )
        case ScanOutcome.TIMEOUT:
            return (
                ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
                ArtifactSecurityVerdict.UNAVAILABLE,
                (REASON_SCANNER_TIMEOUT, REASON_SCANNER_UNAVAILABLE),
            )
        case ScanOutcome.UNAVAILABLE | ScanOutcome.MALFORMED:
            return (
                ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
                ArtifactSecurityVerdict.UNAVAILABLE,
                (REASON_SCANNER_UNAVAILABLE,),
            )
        case _:
            return assert_never_scan_outcome(scan.outcome)


def _verdict_for_status(status: ArtifactStatus) -> ArtifactSecurityVerdict:
    if status is ArtifactStatus.REJECTED_MALWARE:
        return ArtifactSecurityVerdict.MALICIOUS
    if status is ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE:
        return ArtifactSecurityVerdict.UNAVAILABLE
    return ArtifactSecurityVerdict.INCONCLUSIVE


def _diagnostics(
    scan: ScanResult,
    detection: DetectionResult | None,
    *,
    payload: bytes | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload_bytes = payload or b""
    diagnostics: dict[str, object] = {
        "engine": scan.engine,
        "engine_version": scan.engine_version,
        "signature_version": scan.signature_version,
        "scan_outcome": scan.outcome.value,
        "policy_version": POLICY_VERSION,
        **control_authority_diagnostics(payload_bytes),
    }
    if scan.signature:
        diagnostics["signature"] = scan.signature
    if detection is not None:
        diagnostics["detected_media_type"] = detection.media_type
        diagnostics["detected_family"] = detection.family.value
        diagnostics["polyglot"] = detection.polyglot
    if extra:
        diagnostics.update(extra)
    return diagnostics


def _unavailable_scan(detail: str) -> ScanResult:
    return ScanResult(
        outcome=ScanOutcome.UNAVAILABLE,
        engine="none",
        engine_version="0",
        signature_version="0",
        detail=detail,
    )


def _clean_placeholder(scanner: SecurityScanner) -> ScanResult:
    del scanner
    return ScanResult(
        outcome=ScanOutcome.CLEAN,
        engine="policy",
        engine_version="0",
        signature_version="0",
        detail="not_scanned",
    )


def decision_status_diagnostics(
    status: ArtifactStatus, diagnostics: dict[str, object]
) -> dict[str, object]:
    merged = dict(diagnostics)
    merged["artifact_status"] = status.value
    return merged
