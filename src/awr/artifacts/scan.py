from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Never, Protocol


class ScanOutcome(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ScanResult:
    outcome: ScanOutcome
    engine: str
    engine_version: str
    signature_version: str
    signature: str | None = None
    detail: str | None = None


class SecurityScanner(Protocol):
    def scan(self, payload: bytes) -> ScanResult: ...


def assert_never_scan_outcome(value: Never) -> Never:
    raise ValueError(f"Unhandled scan outcome: {value!r}")


EICAR_BYTES = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

_CLAMDSCAN = "clamdscan"
_CLAMSCAN = "clamscan"


class CleanScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        return ScanResult(
            outcome=ScanOutcome.CLEAN,
            engine="fake-clean",
            engine_version="1",
            signature_version="1",
        )


class InfectedScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        return ScanResult(
            outcome=ScanOutcome.INFECTED,
            engine="fake-malware",
            engine_version="1",
            signature_version="1",
            signature="EICAR",
        )


class EicarScanner:
    """Marks the standard EICAR ASCII fixture infected; every other payload is clean."""

    def scan(self, payload: bytes) -> ScanResult:
        if EICAR_BYTES in payload:
            return ScanResult(
                outcome=ScanOutcome.INFECTED,
                engine="fake-eicar",
                engine_version="1",
                signature_version="1",
                signature="EICAR-Test-File",
            )
        return ScanResult(
            outcome=ScanOutcome.CLEAN,
            engine="fake-eicar",
            engine_version="1",
            signature_version="1",
        )


class TimeoutScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        return ScanResult(
            outcome=ScanOutcome.TIMEOUT,
            engine="fake-timeout",
            engine_version="0",
            signature_version="0",
            detail="scanner_timeout",
        )


class UnavailableScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        return ScanResult(
            outcome=ScanOutcome.UNAVAILABLE,
            engine="fake-unavailable",
            engine_version="0",
            signature_version="0",
            detail="scanner_unavailable",
        )


class MalformedScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        return ScanResult(
            outcome=ScanOutcome.MALFORMED,
            engine="fake-malformed",
            engine_version="0",
            signature_version="0",
            detail="scanner_malformed_response",
        )


class ClamAvScanner:
    """ClamAV adapter that invokes argv lists only; never `shell=True`."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._engine_version = "unknown"
        self._signature_version = "unknown"
        self._versions_loaded = False

    def scan(self, payload: bytes) -> ScanResult:
        self._load_versions()
        commands = self._candidate_commands()
        if not commands:
            return ScanResult(
                outcome=ScanOutcome.UNAVAILABLE,
                engine="clamav",
                engine_version=self._engine_version,
                signature_version=self._signature_version,
                detail="clamav_binary_missing",
            )
        handle, path = tempfile.mkstemp(prefix="awr-scan-")
        try:
            with os.fdopen(handle, "wb") as body:
                body.write(payload)
                body.flush()
                os.fsync(body.fileno())
            last_error = "clamav_unavailable"
            for command in commands:
                try:
                    completed = subprocess.run(
                        [*command, path],
                        capture_output=True,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                except FileNotFoundError:
                    last_error = "clamav_binary_missing"
                    continue
                except subprocess.TimeoutExpired:
                    return ScanResult(
                        outcome=ScanOutcome.TIMEOUT,
                        engine="clamav",
                        engine_version=self._engine_version,
                        signature_version=self._signature_version,
                        detail="scanner_timeout",
                    )
                if completed.returncode == 0:
                    return ScanResult(
                        outcome=ScanOutcome.CLEAN,
                        engine="clamav",
                        engine_version=self._engine_version,
                        signature_version=self._signature_version,
                    )
                if completed.returncode == 1:
                    signature = _signature_from_output(completed.stdout, completed.stderr)
                    return ScanResult(
                        outcome=ScanOutcome.INFECTED,
                        engine="clamav",
                        engine_version=self._engine_version,
                        signature_version=self._signature_version,
                        signature=signature,
                    )
                last_error = "clamav_engine_error"
            return ScanResult(
                outcome=ScanOutcome.UNAVAILABLE,
                engine="clamav",
                engine_version=self._engine_version,
                signature_version=self._signature_version,
                detail=last_error,
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def _candidate_commands(self) -> list[list[str]]:
        commands: list[list[str]] = []
        if shutil.which(_CLAMDSCAN):
            commands.append([_CLAMDSCAN, "--no-summary", "--stdout"])
        if shutil.which(_CLAMSCAN):
            commands.append([_CLAMSCAN, "--no-summary", "--stdout"])
        return commands

    def _load_versions(self) -> None:
        if self._versions_loaded:
            return
        self._versions_loaded = True
        binary = shutil.which(_CLAMSCAN) or shutil.which(_CLAMDSCAN)
        if binary is None:
            return
        try:
            completed = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                timeout=min(5.0, self.timeout_seconds),
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return
        raw = (completed.stdout or completed.stderr).decode("utf-8", errors="replace").strip()
        engine, signature = _parse_clam_version(raw)
        self._engine_version = engine
        self._signature_version = signature


def _parse_clam_version(raw: str) -> tuple[str, str]:
    first = raw.splitlines()[0] if raw else ""
    parts = [part.strip() for part in first.split("/") if part.strip()]
    if not parts:
        return "unknown", "unknown"
    if len(parts) == 1:
        return parts[0], "unknown"
    return parts[0], "/".join(parts[1:])


def _signature_from_output(stdout: bytes, stderr: bytes) -> str | None:
    text = (stdout or stderr).decode("utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if "FOUND" in stripped:
            return stripped.split(":", 1)[-1].replace("FOUND", "").strip() or "FOUND"
    return None


def build_scanner(kind: str, timeout_seconds: float) -> SecurityScanner:
    if kind == "fake_clean":
        return CleanScanner()
    if kind == "fake_eicar":
        return EicarScanner()
    return ClamAvScanner(timeout_seconds=timeout_seconds)
