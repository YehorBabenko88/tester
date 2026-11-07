"""HTTP-independent route dispatcher for E's loopback server. Does not bind a port."""
import secrets
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .contracts import bounded_json
from .plane import Conflict, QuotaExceeded


class ControlAPI:
    def __init__(self, plane, auth, *, allowed_origin, csrf_secret):
        if not isinstance(csrf_secret, str) or len(csrf_secret) < 32: raise ValueError("strong CSRF secret required")
        self.plane, self.auth = plane, auth
        self.allowed_origin, self.csrf_secret = allowed_origin, csrf_secret

    def handle(self, method, path, *, body=None, bearer=None, origin=None, csrf=None,
               local_human_channel=False):
        """E supplies flags from trusted transport/session, never JSON or headers.

        E must bound raw bytes BEFORE JSON decoding, reject duplicate JSON keys,
        enforce Host/peer loopback and apply per-token ingress rate limits.
        """
        identity = None
        try:
            try: identity = self.auth.authenticate(bearer)
            except PermissionError: return 401, {"error":"unauthenticated"}
            if origin is not None and origin != self.allowed_origin: raise PermissionError("origin denied")
            if origin is not None and method != "GET" and (not isinstance(csrf, str) or not secrets.compare_digest(csrf, self.csrf_secret)): raise PermissionError("CSRF denied")
            if body is not None: bounded_json(body, 32768)
            url = urlsplit(path)
            if url.scheme or url.netloc or url.fragment: raise ValueError("relative API path required")
            parts = url.path.strip("/").split("/")
            query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True) if url.query else {}
            if set(query)-{"after", "limit"} or any(len(v) != 1 for v in query.values()): raise ValueError("invalid query")
            pagination = {k:int(v[0]) for k,v in query.items()}
            if parts[:2] != ["api", "control"]: return 404, {"error":"not_found"}
            route = parts[2:]
            if method == "GET" and route == ["capabilities"] and not query: return 200, self.plane.capabilities(identity)
            if method == "POST" and route == ["plan"] and not query: return 200, self.plane.plan(identity, body)
            if method == "POST" and route == ["jobs"] and not query:
                if not isinstance(body, dict) or set(body) != {"plan_id"}: raise ValueError("plan_id required")
                self._uuid(body["plan_id"])
                return 202, self.plane.submit(identity, body["plan_id"])
            if method == "GET" and route == ["audit"]: return 200, self.plane.audit(identity, **pagination)
            if len(route) in (2, 3) and route[0] == "jobs":
                job_id = self._uuid(route[1])
                if method == "GET" and len(route) == 2 and not query: return 200, self.plane.get(identity, job_id)
                if method == "POST" and route[2:] == ["cancel"] and not query:
                    if body not in (None, {}): raise ValueError("empty body required")
                    return 200, self.plane.cancel(identity, job_id)
                if method == "GET" and route[2:] == ["events"]: return 200, self.plane.events(identity, job_id, **pagination)
                if method == "GET" and route[2:] == ["artifacts"] and not query: return 200, self.plane.artifacts(identity, job_id)
            if method == "POST" and len(route) == 2 and route[0] == "approvals" and not query:
                if not local_human_channel or not identity.human or not isinstance(csrf, str) or not secrets.compare_digest(csrf, self.csrf_secret): raise PermissionError("human channel required")
                if body not in (None, {}): raise ValueError("empty body required")
                return 200, self.plane.approve(identity, self._uuid(route[1]))
            return 404, {"error":"not_found"}
        except (PermissionError, ValueError, TypeError, KeyError, Conflict, QuotaExceeded) as exc:
            if isinstance(exc, PermissionError): status, code = 403, "forbidden"
            elif isinstance(exc, Conflict): status, code = 409, "conflict"
            elif isinstance(exc, QuotaExceeded): status, code = 429, "quota_exceeded"
            elif isinstance(exc, KeyError): status, code = 404, "not_found"
            else: status, code = 400, "invalid_request"
            # Never persist submitted bodies, credential strings or exception text.
            if identity is not None:
                with self.plane._db() as db:
                    self.plane._audit(db, identity.actor, sorted(identity.scopes)[0], "denied", "api", code=code)
            return status, {"error":code}

    @staticmethod
    def _uuid(value):
        try:
            result = str(UUID(value))
            if value != result: raise ValueError("canonical UUID required")
            return result
        except (AttributeError, TypeError) as exc: raise ValueError("invalid UUID") from exc
