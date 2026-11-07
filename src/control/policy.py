"""Trusted gateway identities. Request bodies never grant authority."""
from dataclasses import dataclass
import hashlib
import secrets
import threading
import time

from .commands import ACTIONS
from .contracts import identifier


@dataclass(frozen=True)
class Identity:
    actor: str
    actions: frozenset[str]
    scopes: frozenset[str]
    expires_at: int
    human: bool = False

    def check(self, now, *, action=None, scope=None, actor=None):
        if now >= self.expires_at or (action is not None and action not in self.actions) or (scope is not None and scope not in self.scopes) or (actor is not None and actor != self.actor):
            raise PermissionError("identity expired or capability denied")


class TokenAuth:
    """Provisioned by E from an external secret store; never exposed as an agent tool."""
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._tokens = {}
        self._lock = threading.Lock()

    def register(self, token, identity):
        if not isinstance(token, str) or len(token) < 32 or len(token) > 512: raise ValueError("strong token required")
        if not isinstance(identity, Identity) or not identifier(identity.actor) or not identity.actions <= ACTIONS or not identity.scopes or not all(identifier(s) for s in identity.scopes): raise ValueError("invalid identity")
        with self._lock: self._tokens[hashlib.sha256(token.encode()).digest()] = identity

    def revoke(self, token):
        with self._lock: self._tokens.pop(hashlib.sha256(token.encode()).digest(), None)

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 512: raise PermissionError("invalid credential")
        supplied = hashlib.sha256(token.encode()).digest()
        with self._lock:
            identity = next((v for k, v in self._tokens.items() if secrets.compare_digest(k, supplied)), None)
        if identity is None: raise PermissionError("invalid credential")
        identity.check(self.clock())
        return identity
