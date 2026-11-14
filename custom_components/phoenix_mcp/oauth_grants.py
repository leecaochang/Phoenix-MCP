"""Approved OAuth sign-ins: each one binds an app to one access key.

A sign-in holds only SHA-256 hashes of its tokens. It is valid while its key is valid, has
OAuth sign-in switched on, and carries the generation the sign-in was issued under; rotation
and switching OAuth off advance that generation in the key's own save, so validity never
waits for a row here to be deleted. Deleting dead rows is cleanup.

Every refresh token a sign-in issues carries the same family tag, stored only as a hash, so
a redeemed token stays recognisable as reuse after it fell off the bounded retired list.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.util.dt import utcnow

from .access_key_store import _parse_dt, hmac_compare
from .const import (
    OAUTH_ACCESS_TOKEN_PREFIX, OAUTH_ACCESS_TOKEN_TTL_SECONDS, OAUTH_MAX_GRANTS,
    OAUTH_MAX_GRANTS_PER_ACCESS_KEY, OAUTH_REFRESH_GRACE_SECONDS, OAUTH_REFRESH_SIBLINGS_MAX, OAUTH_REFRESH_USES_MAX,
    OAUTH_REFRESH_FAMILY_HEX_LENGTH, OAUTH_REFRESH_TOKEN_PREFIX, OAUTH_RETIRED_TOKENS_MAX,
    OAUTH_SECRET_HEX_LENGTH,
)
from .durable_storage import DurableStorageError, DurableStore

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)


def mint_secret(prefix: str) -> tuple[str, str]:
    """Return a new raw secret with ``prefix`` and its SHA-256 hex hash."""
    raw = prefix + secrets.token_hex(OAUTH_SECRET_HEX_LENGTH // 2)
    return raw, secret_hash(raw)


def secret_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_family() -> str:
    return secrets.token_hex(OAUTH_REFRESH_FAMILY_HEX_LENGTH // 2)


def _family_of(raw_refresh: str) -> str:
    """The family tag a refresh token carries right after its prefix."""
    start = len(OAUTH_REFRESH_TOKEN_PREFIX)
    return raw_refresh[start:start + OAUTH_REFRESH_FAMILY_HEX_LENGTH]


def _mint_refresh(family: str) -> tuple[str, str]:
    """A new refresh token of ``family`` and its hash; as long as any other secret."""
    raw = (OAUTH_REFRESH_TOKEN_PREFIX + family
           + secrets.token_hex((OAUTH_SECRET_HEX_LENGTH - OAUTH_REFRESH_FAMILY_HEX_LENGTH) // 2))
    return raw, secret_hash(raw)


@dataclass
class _HashUntil:
    """A token hash and the moment it stops being accepted.

    ``uses`` counts how often a redeemed refresh token was used, its rotation included.
    """

    token_hash: str
    until: datetime
    uses: int = 1

    def to_dict(self) -> dict:
        return {"hash": self.token_hash, "until": self.until.isoformat(),
                **({"uses": self.uses} if self.uses > 1 else {})}

    @classmethod
    def from_dict(cls, raw: Any) -> _HashUntil | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("hash"), str):
            return None
        until = _parse_dt(raw.get("until")) if isinstance(raw.get("until"), str) else None
        uses = raw.get("uses")
        if not isinstance(uses, int) or isinstance(uses, bool) or uses < 1:
            # A record saved before use counts marks a token that gave its one sibling as spent.
            uses = 2 if raw.get("spent") is True else 1
        return cls(raw["hash"], until, uses) if until is not None else None


@dataclass
class OAuthGrant:
    """One approved sign-in: an app bound to one access key."""

    id: str
    access_key_id: str
    generation: int
    client_id: str
    client_name: str
    redirect_host: str
    created_at: datetime
    created_by: str
    last_used_at: datetime | None = None
    # Accepted access tokens, newest last. A replaced one keeps a short grace.
    access_tokens: list[_HashUntil] = field(default_factory=list)
    # Refresh tokens any one of which may be redeemed next (normally one; a retried
    # refresh inside the grace adds a sibling).
    refresh_hashes: list[str] = field(default_factory=list)
    # Redeemed refresh tokens: a few more uses each until their grace ends, then proof of reuse.
    retired_refresh: list[_HashUntil] = field(default_factory=list)
    # Hash of the family tag in every refresh token issued since; empty for a sign-in
    # stored before tags, until its next refresh.
    family_hash: str = ""

    def to_admin_dict(self) -> dict:
        """What the panel shows: never a hash."""
        return {
            "id": self.id,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "redirect_host": self.redirect_host,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }

    def audit_label(self) -> dict[str, str]:
        """The app an audit row names: the name it gave itself, and the host it signed in through."""
        return {"name": self.client_name, "host": self.redirect_host}

    def to_storage_dict(self) -> dict:
        return {
            **self.to_admin_dict(),
            "access_key_id": self.access_key_id,
            "generation": self.generation,
            "access_tokens": [t.to_dict() for t in self.access_tokens],
            "refresh_hashes": list(self.refresh_hashes),
            "retired_refresh": [t.to_dict() for t in self.retired_refresh],
            "family_hash": self.family_hash,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> OAuthGrant:
        created_at = _parse_dt(raw["created_at"])
        if created_at is None:
            raise ValueError("unparseable created_at")
        generation = raw["generation"]
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise ValueError("generation must be an int")
        return cls(
            id=str(raw["id"]),
            access_key_id=str(raw["access_key_id"]),
            generation=generation,
            client_id=str(raw["client_id"]),
            client_name=str(raw.get("client_name") or ""),
            redirect_host=str(raw.get("redirect_host") or ""),
            created_at=created_at,
            created_by=str(raw.get("created_by") or ""),
            last_used_at=_parse_dt(raw.get("last_used_at")) if isinstance(raw.get("last_used_at"), str) else None,
            access_tokens=[t for t in map(_HashUntil.from_dict, raw.get("access_tokens") or []) if t],
            refresh_hashes=[h for h in raw.get("refresh_hashes") or [] if isinstance(h, str)],
            retired_refresh=[t for t in map(_HashUntil.from_dict, raw.get("retired_refresh") or []) if t],
            family_hash=raw["family_hash"] if isinstance(raw.get("family_hash"), str) else "",
        )

    def is_live(self, access_key: AccessKeyRecord | None) -> bool:
        """Whether this sign-in may still be used to present ``access_key``."""
        return (
            access_key is not None
            and access_key.id == self.access_key_id
            and access_key.is_valid()
            and access_key.oauth_enabled
            and access_key.oauth_generation == self.generation
        )


@dataclass(frozen=True)
class IssuedTokens:
    """Raw tokens returned once to the app, never stored."""

    grant_id: str
    access_token: str
    refresh_token: str
    expires_in: int


RefreshOutcome = Literal["issued", "unknown", "reused"]


class GrantLimitError(Exception):
    """The access key, or Phoenix as a whole, holds the most sign-ins it may."""


class OAuthGrantStore:
    """The sign-ins Phoenix approved. A None store keeps them in memory only."""

    def __init__(self, store: DurableStore | None = None) -> None:
        self._store = store
        self._grants: dict[str, OAuthGrant] = {}
        self.lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self._store is None:
            return
        try:
            raw = await self._store.async_load()
        except Exception:  # an unreadable file grants nothing and must not block setup
            _LOGGER.exception("Could not load OAuth sign-ins; none is accepted until it is repaired")
            return
        grants: dict[str, OAuthGrant] = {}
        for item in (raw or {}).get("grants", []) if isinstance(raw, dict) else []:
            try:
                grant = OAuthGrant.from_dict(item)
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt OAuth sign-in record: %s", type(exc).__name__)
                continue
            grants[grant.id] = grant
        self._grants = grants

    async def _async_commit(self, grants: dict[str, OAuthGrant]) -> None:
        """Write ``grants`` and make them current only once the write succeeded."""
        if self._store is not None:
            await self._store.async_save({"grants": [g.to_storage_dict() for g in grants.values()]})
        self._grants = grants

    def _copy(self) -> dict[str, OAuthGrant]:
        return copy.deepcopy(self._grants)

    def get(self, grant_id: str | None) -> OAuthGrant | None:
        return self._grants.get(grant_id) if grant_id else None

    def is_live(self, grant_id: str, access_key: AccessKeyRecord | None) -> bool:
        grant = self._grants.get(grant_id)
        return grant is not None and grant.is_live(access_key)

    def list_for_key(self, access_key_id: str) -> list[OAuthGrant]:
        return sorted(
            (g for g in self._grants.values() if g.access_key_id == access_key_id),
            key=lambda g: g.created_at,
        )

    def count_for_key(self, access_key_id: str, generation: int) -> int:
        return sum(1 for g in self._grants.values()
                   if g.access_key_id == access_key_id and g.generation == generation)

    def find_access_token(self, token_hash: str, now: datetime | None = None) -> tuple[OAuthGrant, datetime] | None:
        """The sign-in whose unexpired access token has this hash, and when that token ends.

        Compared in constant time.
        """
        now = now or utcnow()
        found: tuple[OAuthGrant, datetime] | None = None
        for grant in self._grants.values():
            for token in grant.access_tokens:
                if hmac_compare(token.token_hash, token_hash) and now < token.until:
                    found = (grant, token.until)
        return found

    def find_by_access_hash(self, token_hash: str, now: datetime | None = None) -> OAuthGrant | None:
        """The sign-in whose unexpired access token has this hash, compared in constant time."""
        found = self.find_access_token(token_hash, now)
        return found[0] if found is not None else None

    def _find_by_refresh_hash(self, token_hash: str, family_hash: str = "") -> tuple[OAuthGrant | None, str]:
        """The sign-in that knows this refresh token and how: current, retired, family, or none.

        "family" is a token that carries a sign-in's family tag but is no longer listed: an
        old one, so its use is reuse.
        """
        found: tuple[OAuthGrant | None, str] = (None, "none")
        for grant in self._grants.values():
            for current in grant.refresh_hashes:
                if hmac_compare(current, token_hash):
                    found = (grant, "current")
            for retired in grant.retired_refresh:
                if hmac_compare(retired.token_hash, token_hash) and found[1] == "none":
                    found = (grant, "retired")
            if (family_hash and grant.family_hash and hmac_compare(grant.family_hash, family_hash)
                    and found[1] == "none"):
                found = (grant, "family")
        return found

    def grant_for_refresh(self, token_hash: str) -> OAuthGrant | None:
        """The sign-in that issued this refresh token, current or retired."""
        return self._find_by_refresh_hash(token_hash)[0]

    def touch(self, grant_id: str, when: datetime) -> None:
        """Record use in memory; the next write carries it to storage."""
        grant = self._grants.get(grant_id)
        if grant is not None:
            grant.last_used_at = when

    async def async_create(
        self, *, access_key: AccessKeyRecord, client_id: str, client_name: str,
        redirect_host: str, created_by: str,
    ) -> IssuedTokens:
        """Store a new sign-in for ``access_key`` and return its first tokens.

        Raises GrantLimitError at a cap and DurableStorageError when it could not be saved,
        in which case nothing was issued.
        """
        async with self.lock:
            if self.count_for_key(access_key.id, access_key.oauth_generation) >= OAUTH_MAX_GRANTS_PER_ACCESS_KEY:
                raise GrantLimitError("per_key")
            if len(self._grants) >= OAUTH_MAX_GRANTS:
                raise GrantLimitError("total")
            now = utcnow()
            family = _new_family()
            access_raw, access_hash = mint_secret(OAUTH_ACCESS_TOKEN_PREFIX)
            refresh_raw, refresh_hash = _mint_refresh(family)
            grant = OAuthGrant(
                id=str(uuid.uuid4()),
                access_key_id=access_key.id,
                generation=access_key.oauth_generation,
                client_id=client_id,
                client_name=client_name,
                redirect_host=redirect_host,
                created_at=now,
                created_by=created_by,
                last_used_at=now,
                access_tokens=[_HashUntil(access_hash, now + timedelta(seconds=OAUTH_ACCESS_TOKEN_TTL_SECONDS))],
                refresh_hashes=[refresh_hash],
                family_hash=secret_hash(family),
            )
            grants = self._copy()
            grants[grant.id] = grant
            await self._async_commit(grants)
            return IssuedTokens(grant.id, access_raw, refresh_raw, OAUTH_ACCESS_TOKEN_TTL_SECONDS)

    async def async_refresh(
        self, raw_token: str, client_id: str | None, access_key_of: Any,
    ) -> tuple[RefreshOutcome, IssuedTokens | None, OAuthGrant | None]:
        """Redeem a refresh token for new tokens.

        ``access_key_of(grant)`` returns the bound key or None; a dead sign-in is unknown,
        returned with the sign-in so the refusal can be recorded. A current token rotates; a
        redeemed one may add a sibling inside its grace, up to OAUTH_REFRESH_USES_MAX uses in
        all, while fewer than OAUTH_REFRESH_SIBLINGS_MAX tokens are current. Any other use of a
        redeemed token, or of an older token of the sign-in's family, ends the sign-in
        ("reused"). Raises
        DurableStorageError when the result could not be saved, leaving the old tokens in force.
        """
        token_hash = secret_hash(raw_token)
        async with self.lock:
            grant, how = self._find_by_refresh_hash(token_hash, secret_hash(_family_of(raw_token)))
            if grant is None:
                return "unknown", None, None
            if client_id is not None and client_id != grant.client_id:
                return "unknown", None, grant
            if not grant.is_live(access_key_of(grant)):
                return "unknown", None, grant
            now = utcnow()
            grants = self._copy()
            working = grants[grant.id]
            retired = None
            if how == "retired":
                retired = next(t for t in working.retired_refresh if hmac_compare(t.token_hash, token_hash))
                if (now >= retired.until or retired.uses >= OAUTH_REFRESH_USES_MAX
                        or len(working.refresh_hashes) >= OAUTH_REFRESH_SIBLINGS_MAX):
                    how = "family"
            if how == "family":
                del grants[grant.id]
                await self._async_commit(grants)
                return "reused", None, grant
            if working.family_hash:
                family = _family_of(raw_token)
            else:  # a sign-in stored before family tags gets one now
                family = _new_family()
                working.family_hash = secret_hash(family)
            grace_until = now + timedelta(seconds=OAUTH_REFRESH_GRACE_SECONDS)
            access_raw, access_hash = mint_secret(OAUTH_ACCESS_TOKEN_PREFIX)
            refresh_raw, refresh_hash = _mint_refresh(family)
            access_tokens = [t for t in working.access_tokens if now < t.until]
            if retired is None:
                working.retired_refresh.extend(_HashUntil(h, grace_until) for h in working.refresh_hashes)
                working.refresh_hashes = [refresh_hash]
                # Older access tokens keep only the grace, so a request already in flight finishes.
                access_tokens = [
                    _HashUntil(t.token_hash, min(t.until, grace_until)) for t in access_tokens
                ][-OAUTH_REFRESH_SIBLINGS_MAX:]
            else:
                # A sibling neither shortens nor drops a token already issued: the app whose
                # refresh it repeats may be the one that holds them.
                retired.uses += 1
                working.refresh_hashes.append(refresh_hash)
            # A redeemed hash cut from this list stays known through the family tag.
            working.retired_refresh = working.retired_refresh[-OAUTH_RETIRED_TOKENS_MAX:]
            working.access_tokens = [
                *access_tokens, _HashUntil(access_hash, now + timedelta(seconds=OAUTH_ACCESS_TOKEN_TTL_SECONDS))]
            working.last_used_at = now
            await self._async_commit(grants)
            return "issued", IssuedTokens(grant.id, access_raw, refresh_raw, OAUTH_ACCESS_TOKEN_TTL_SECONDS), working

    async def async_remove(self, grant_id: str) -> OAuthGrant | None:
        """Delete one sign-in; return it, or None if there was none."""
        async with self.lock:
            if grant_id not in self._grants:
                return None
            grants = self._copy()
            removed = grants.pop(grant_id)
            await self._async_commit(grants)
            return removed

    async def async_prune(self, access_key_of: Any) -> int:
        """Delete every sign-in that can no longer be used; return how many went.

        Best effort: a failed write leaves dead rows that still grant nothing.
        """
        async with self.lock:
            dead = [g.id for g in self._grants.values() if not g.is_live(access_key_of(g))]
            if not dead:
                return 0
            grants = self._copy()
            for grant_id in dead:
                grants.pop(grant_id, None)
            try:
                await self._async_commit(grants)
            except DurableStorageError:
                _LOGGER.warning("Could not remove %d ended OAuth sign-ins; they stay unusable", len(dead))
                return 0
            return len(dead)

    async def async_clear(self) -> None:
        """Delete every sign-in and the file that held them (integration removal)."""
        async with self.lock:
            if self._store is not None:
                await self._store.async_remove()
            self._grants = {}
