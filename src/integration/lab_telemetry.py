"""Measured job work; never a certificate of complete historical coverage."""

import copy
import hashlib
import os
import time


class HttpMeter:
    def __init__(self, session, clock=time.monotonic, evidence_dir=None):
        self.session, self.clock = session, clock
        self.started = clock()
        self.bytes_received = 0
        self.evidence_refs = []
        self.response_receipts = []
        self.evidence_dir = evidence_dir

    def request(self, *args, **kwargs):
        response = self.session.request(*args, **kwargs)
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        if self.evidence_dir is not None:
            target = self.evidence_dir / (digest + ".http-body")
            try:
                with target.open("xb") as out:
                    out.write(body)
                    out.flush()
                    os.fsync(out.fileno())
            except FileExistsError:
                if target.read_bytes() != body:
                    raise ValueError("Original HTTP body receipt differs")
        self.bytes_received += len(body)
        self.response_receipts.append(dict(source_hash=digest, bytes=len(body), source_ref=args[1]))
        if digest not in self.evidence_refs:
            self.evidence_refs.append(digest)
        return response

    def snapshot(self):
        elapsed = max(0, self.clock() - self.started)
        return dict(
            bytes_received=self.bytes_received,
            elapsed_seconds=elapsed,
            bytes_per_second=self.bytes_received / elapsed if elapsed > 0 else None,
            evidence_refs=list(self.evidence_refs),
            response_receipts=copy.deepcopy(self.response_receipts),
            expected_bytes=None,
            byte_scope="decoded_http_response_body",
        )

    def close(self):
        self.session.close()


def attempt_measurement(prior, measured):
    """Prior is immutable run-entry checkpoint, never the last reported snapshot."""
    result = copy.deepcopy(measured)
    result["bytes_received"] += prior.get("bytes_received", 0)
    result["elapsed_seconds"] += prior.get("elapsed_seconds", 0)
    seconds = result["elapsed_seconds"]
    result["bytes_per_second"] = result["bytes_received"] / seconds if seconds > 0 else None
    result["evidence_refs"] = list(
        dict.fromkeys(prior.get("evidence_refs", []) + measured["evidence_refs"])
    )
    result["response_receipts"] = copy.deepcopy(
        prior.get("response_receipts", []) + measured.get("response_receipts", [])
    )
    return result


def acknowledgment(prior, verified_records, measured, *, complete):
    records = dict(prior.get("record_hashes", {}))
    for key, digest in verified_records.items():
        if key in records and records[key] != digest:
            raise ValueError("Acknowledged record identity changed")
        records[key] = digest
    return dict(
        schema_version=1,
        source="bybit",
        coverage="UNKNOWN",
        work_complete=bool(complete),
        records_ingested=len(records),
        records_verified=len(records),
        record_hashes=records,
        expected_records=None,
        verification_scope="stored record values and original provenance; not historical completeness",
        **copy.deepcopy(measured),
    )
