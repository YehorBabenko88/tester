"""Catalog lifecycle with one PostgreSQL session owner per category.

Browsers only query committed snapshots and enqueue coalesced requests. In-memory
publication by the acquisition service remains private until its transaction commits.
"""

import asyncio
import json
import logging
import threading
from copy import deepcopy

import psycopg

from .market_catalog import CATEGORIES, CatalogService, _category

LOG = logging.getLogger("tester.catalog")


class PersistentCatalogService(CatalogService):
    def __init__(self, source, store, **kwargs):
        super().__init__(source, **kwargs)
        self.store = store
        self._runtime_refresh = {category: threading.Lock() for category in CATEGORIES}

    def _load(self, category):
        state = self.store.read(category)
        with self._lock:
            if state["snapshot"] is None:
                self._snapshots.pop(category, None)
            else:
                self._snapshots[category] = state["snapshot"]
            self._attempts[category] = state["attempt"]
        return state

    def query(self, category, **kwargs):
        _category(category)
        state = self.store.read(category)
        # Query an isolated committed view: acquisition's temporary in-memory
        # publication must never leak before the database commit.
        view = CatalogService(None, clock=self.clock)
        if state["snapshot"] is not None:
            view._snapshots[category] = state["snapshot"]
        view._attempts[category] = state["attempt"]
        data = view.query(category, **kwargs)
        data["catalog"].update(
            {
                "next_refresh_at": state["next_refresh_at"],  # Unix seconds, like last_success.
                "next_refresh_at_ms": int(state["next_refresh_at"] * 1000),
                "refresh_interval_seconds": 300,
                "refresh_pending": state["pending"],
            }
        )
        return data

    def refresh(self, category):
        """Only an advisory-lock owner can issue network requests or publish."""
        _category(category)
        if not self._runtime_refresh[category].acquire(blocking=False):
            return False
        try:
            return self._refresh_owned(category)
        finally:
            self._runtime_refresh[category].release()

    def _refresh_owned(self, category):
        if self._cancel.is_set():
            return False
        if not self.store.claim(category):
            return False
        state = self._load(category)
        if self.clock() < state["eligible_at"]:
            return False
        self.store.take_pending(category)
        ok = super().refresh(category)
        with self._lock:
            candidate = self._snapshots.get(category)
            previous = state["snapshot"]
            changed = candidate is not None and (
                previous is None or candidate["revision"] != previous["revision"]
            )
            # Exhausted catalog acquisition may publish a new valid catalog while
            # its ticker stage fails. Persist that composite but keep error backoff.
            snapshot = deepcopy(candidate) if ok or changed else None
            attempt = deepcopy(self._attempts[category])
        if snapshot is not None and len(json.dumps(snapshot).encode()) > 32 * 1024 * 1024:
            snapshot, ok = None, False
            attempt.update(last_error="snapshot_budget", completeness="PARTIAL")
        failures = 0 if ok else min(3, state["failures"] + 1)
        finished = self.clock()
        delay = min(1800, 300 * 2**failures)
        # Manual requests share the same persisted backoff/source budget.
        with self._lock:
            if self._cancel.is_set():
                return False
            self.store.publish(
                category,
                snapshot,
                attempt,
                finished + delay + self.jitter(),
                finished + (30 if ok else delay),
                failures,
            )
        return ok

    def request_refresh(self, category):
        _category(category)
        if self._wake is None:
            raise RuntimeError("Catalog scheduler is not running")
        self.store.request(category)
        self._event_loop.call_soon_threadsafe(self._wake.set)

    async def _poll(self):
        while not self._cancel.is_set():
            self._wake.clear()
            for category in CATEGORIES:
                if self._cancel.is_set():
                    return
                try:
                    owned = await asyncio.to_thread(self.store.claim, category)
                    if not owned:
                        continue
                    state = await asyncio.to_thread(self.store.read, category)
                    now = self.clock()
                    if now >= state["next_refresh_at"] or (
                        state["pending"] and now >= state["eligible_at"]
                    ):
                        await asyncio.to_thread(self.refresh, category)
                except (psycopg.Error, RuntimeError):
                    # Never publish a local-only snapshot after DB/session failure.
                    await asyncio.to_thread(self.store.release, category)
                    LOG.warning("catalog_store_unavailable", extra={"category": category})
            try:
                await asyncio.wait_for(self._wake.wait(), 1)
            except TimeoutError:
                pass

    async def start(self):
        # Read-only schema readiness check: explicit operator migration is mandatory.
        await asyncio.to_thread(self.store.check_ready)
        for category in CATEGORIES:
            await asyncio.to_thread(self._load, category)
        await super().start()

    async def stop(self):
        try:
            await super().stop()
        finally:

            def drain():
                for lock in self._runtime_refresh.values():
                    with lock:
                        pass

            await asyncio.to_thread(drain)
            await asyncio.to_thread(self.store.close)
