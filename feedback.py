"""Small anonymous feedback endpoint support; no player or gameplay state."""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import date
from ipaddress import ip_address, ip_network
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from database import save_feedback_submission


MAX_BODY_BYTES = 24 * 1024
RATE_WINDOW_SECONDS = 600
MAX_PER_PEER = 20
MAX_GLOBAL = 100
PUBLIC_ORIGIN = "https://animoredle.com"


class FeedbackSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    kind: Literal["bug", "feedback"]
    message: str = Field(min_length=10, max_length=5000)
    expected_behavior: str | None = Field(default=None, max_length=1500)
    steps: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=60)
    challenge_date: str | None = Field(default=None, max_length=10)
    device_context: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=254)
    website: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_category(self):
        categories = ({"Higher Score", "More Popular", "More Episodes", "More Recent"}
                      if self.kind == "bug" else
                      {"Suggestion", "Content or data", "Accessibility", "Other"})
        if self.category and self.category not in categories:
            raise ValueError("Choose one of the available report categories.")
        return self

    @field_validator("message", "expected_behavior", "steps", "category",
                     "device_context", "email", "website")
    @classmethod
    def reject_control_characters(cls, value):
        if value and any(ord(char) < 32 and char not in "\n\t\r" for char in value):
            raise ValueError("Please remove control characters.")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if not value:
            return None
        if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value):
            raise ValueError("Enter a valid contact email or leave it blank.")
        return value

    @field_validator("challenge_date")
    @classmethod
    def validate_date(cls, value):
        if not value:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use YYYY-MM-DD for the challenge date.")
        date.fromisoformat(value)
        return value


class FeedbackRateLimiter:
    """Bounded, process-local protection for the current single-worker deployment."""

    def __init__(self, trusted_proxy_cidrs=None):
        configured = (os.getenv("ANIME_DAILY_FEEDBACK_TRUSTED_PROXIES", "")
                      if trusted_proxy_cidrs is None else trusted_proxy_cidrs)
        try:
            self._trusted_proxies = tuple(ip_network(item.strip(), strict=True)
                                          for item in configured.split(",") if item.strip())
            if any(network.prefixlen == 0 for network in self._trusted_proxies):
                raise ValueError
        except ValueError:
            raise ValueError("Feedback proxy trust requires explicit IPs/CIDRs, never wildcards or default routes.") from None
        self._salt = secrets.token_bytes(32)
        self._peers = {}
        self._global = deque()
        self._lock = threading.Lock()

    def request_peer(self, request):
        """ACA appends the observed client IP on the right; earlier values are untrusted."""
        peer = request.client.host if request.client else "unknown"
        try:
            address = ip_address(peer)
        except ValueError:
            return peer
        if not any(address in network for network in self._trusted_proxies):
            return str(address)
        forwarded = request.headers.getlist("x-forwarded-for")
        if len(forwarded) != 1 or len(forwarded[0]) > 1024:
            return str(address)
        candidate = forwarded[0].rsplit(",", 1)[-1].strip()
        try:
            if "%" in candidate:
                raise ValueError
            client = ip_address(candidate)
            return str(getattr(client, "ipv4_mapped", None) or client)
        except ValueError:
            return str(address)

    def allow(self, peer, now=None):
        now = time.monotonic() if now is None else now
        key = hmac.new(self._salt, peer.encode(), hashlib.sha256).digest()
        cutoff = now - RATE_WINDOW_SECONDS
        with self._lock:
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            self._peers = {
                key: times for key, times in self._peers.items()
                if times and times[-1] > cutoff
            }
            times = self._peers.setdefault(key, deque())
            while times and times[0] <= cutoff:
                times.popleft()
            if (len(self._global) >= MAX_GLOBAL or len(times) >= MAX_PER_PEER
                    or len(self._peers) > 2048):
                return False
            times.append(now)
            self._global.append(now)
            return True


async def receive_feedback(request: Request):
    origin = request.headers.get("origin", "")
    if (origin not in {PUBLIC_ORIGIN, str(request.base_url).rstrip("/")}
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "Submit feedback from the AniMoredle feedback page.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Feedback must be submitted as JSON.")
    try:
        if int(request.headers.get("content-length", "0")) > MAX_BODY_BYTES:
            raise HTTPException(413, "The report is too large. Please shorten it.")
    except ValueError:
        raise HTTPException(400, "Invalid request length.") from None
    limiter = request.app.state.feedback_limiter
    if not limiter.allow(limiter.request_peer(request)):
        raise HTTPException(429, "Too many reports. Please try again in ten minutes.",
                            headers={"Retry-After": str(RATE_WINDOW_SECONDS)})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "The report is too large. Please shorten it.")
    try:
        submission = FeedbackSubmission.model_validate_json(bytes(body))
    except ValidationError as error:
        errors = [
            {"field": str(item["loc"][0]) if item["loc"] else "report",
             "message": item["msg"]}
            for item in error.errors(include_input=False)
        ]
        raise HTTPException(422, detail=errors) from None
    if submission.website:
        return {"accepted": True, "id": str(submission.id)}
    data = submission.model_dump(mode="json", exclude={"website"})
    content_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
        await run_in_threadpool(
            save_feedback_submission, data, content_hash, request.app.state.database_path,
        )
    except ValueError:
        raise HTTPException(409, "That report ID was already used. Reload to start a new report.") from None
    except Exception:
        raise HTTPException(503, "Your report could not be saved. Please try again.") from None
    return {"accepted": True, "id": str(submission.id)}
