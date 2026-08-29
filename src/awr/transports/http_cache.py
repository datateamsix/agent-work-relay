from __future__ import annotations

import gzip
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..responses.cache import etag_for_digest
from ..responses.canonical import canonical_json, fingerprint_bytes

_GZIP_MIN_BYTES = 512


def no_store_json(payload: dict[str, Any] | list[Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def cached_json(request: Request, payload: dict[str, Any] | list[Any]) -> Response:
    encoded = canonical_json(payload)
    digest = fingerprint_bytes(encoded)
    etag = etag_for_digest(digest)
    if _if_none_match(request, etag):
        response = Response(status_code=304)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, no-cache"
        response.headers["Vary"] = "Accept-Encoding, Authorization"
        return response
    body = encoded
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "Vary": "Accept-Encoding, Authorization",
        "Content-Type": "application/json",
    }
    accept = request.headers.get("accept-encoding", "")
    if "gzip" in accept.lower() and len(body) >= _GZIP_MIN_BYTES:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return Response(content=body, headers=headers)


def _if_none_match(request: Request, etag: str) -> bool:
    inbound = request.headers.get("if-none-match")
    if not inbound:
        return False
    candidates = {part.strip() for part in inbound.split(",")}
    return etag in candidates or etag.strip('"') in {part.strip('"') for part in candidates}
