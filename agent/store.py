"""Saves calls to Postgres while they happen (schema: db/10-schema.sql).

Every write is best-effort: a database problem is logged and never interrupts a live call.
Without DATABASE_URL the store does nothing, so the bot still runs on its own.
"""

import json
import os
from datetime import UTC, datetime

import asyncpg
from loguru import logger

# A booking belongs to a number if it was booked for that number or made on a call from it ($2 = phone).
_BOOKINGS = "bookings b LEFT JOIN calls c ON c.id = b.call_id"
_OWNED_BY = "(b.phone = $2 OR c.phone = $2)"


def _is_upcoming(requested: str, now: datetime) -> bool:
    """Requested times are ISO 8601 when the model follows the tool schema; keep anything unparseable."""
    try:
        when = datetime.fromisoformat(requested)
    except ValueError:
        return True
    return when.tzinfo is None or when >= now


class CallStore:
    def __init__(self):
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        url = os.getenv("DATABASE_URL")
        if not url:
            logger.warning("DATABASE_URL not set; calls won't be saved")
            return
        try:
            self._pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
        except Exception as e:
            logger.error(f"Call store unavailable, calls won't be saved: {e}")

    async def _execute(self, query: str, *args):
        if not self._pool:
            return
        try:
            await self._pool.execute(query, *args)
        except Exception as e:
            logger.error(f"Call store write failed: {e}")

    async def start_call(self, call_id: str, channel: str, phone: str | None, started_at: datetime):
        await self._execute(
            "INSERT INTO calls (id, channel, phone, started_at) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            call_id, channel, phone, started_at,
        )

    async def earlier_calls_today(self, phone: str, call_id: str) -> int:
        """Calls from this number earlier today (South African time), not counting `call_id`."""
        if not self._pool:
            return 0
        try:
            return await self._pool.fetchval(
                "SELECT count(*) FROM calls WHERE phone = $1 AND id <> $2"
                " AND started_at >= date_trunc('day', now() AT TIME ZONE 'Africa/Johannesburg')"
                " AT TIME ZONE 'Africa/Johannesburg'",
                phone, call_id,
            )
        except Exception as e:
            logger.error(f"Call store read failed: {e}")
            return 0

    async def _fetch(self, query: str, *args) -> list[dict]:
        if not self._pool:
            return []
        try:
            return [dict(r) for r in await self._pool.fetch(query, *args)]
        except Exception as e:
            logger.error(f"Call store read failed: {e}")
            return []

    async def unconfirmed_bookings(self, call_id: str, phone: str) -> list[dict]:
        """Bookings made, rescheduled or cancelled on this call that the caller hasn't had a WhatsApp message about."""
        return await self._fetch(
            f"SELECT b.reference, b.name, b.service, b.requested, b.status, b.rescheduled_at FROM {_BOOKINGS}"
            " WHERE b.confirmation_sent_at IS NULL AND (b.call_id = $1 OR " + _OWNED_BY + ") ORDER BY b.created_at",
            call_id, phone,
        )

    async def active_bookings(self, phone: str) -> list[dict]:
        """This number's bookings that aren't cancelled and aren't in the past."""
        rows = await self._fetch(
            f"SELECT b.reference, b.name, b.service, b.requested FROM {_BOOKINGS}"
            " WHERE b.status = 'requested' AND " + _OWNED_BY.replace("$2", "$1") + " ORDER BY b.created_at",
            phone,
        )
        now = datetime.now(UTC)
        return [r for r in rows if _is_upcoming(r["requested"], now)]

    async def change_booking(self, phone: str, reference: str, *, requested: str | None = None,
                             cancel: bool = False) -> dict | None:
        """Reschedule or cancel one of this number's active bookings. Returns the updated booking, or None if
        there's no active booking with that reference for this number. Raises if the database is unavailable."""
        if not self._pool:
            raise RuntimeError("call store unavailable")
        row = await self._pool.fetchrow(
            "UPDATE bookings b SET"
            " status = CASE WHEN $3 THEN 'cancelled' ELSE b.status END,"
            " requested = coalesce($4, b.requested),"
            " rescheduled_at = CASE WHEN $4 IS NULL THEN b.rescheduled_at ELSE now() END,"
            " confirmation_sent_at = NULL"
            " WHERE b.reference = $1 AND b.status = 'requested'"
            " AND (b.phone = $2 OR EXISTS (SELECT 1 FROM calls c WHERE c.id = b.call_id AND c.phone = $2))"
            " RETURNING b.reference, b.name, b.service, b.requested, b.status",
            reference, phone, cancel, requested,
        )
        return dict(row) if row else None

    async def mark_confirmed(self, references: list[str]):
        await self._execute(
            "UPDATE bookings SET confirmation_sent_at = now() WHERE reference = ANY($1::text[])", references
        )

    async def end_call(self, call_id: str, ended_at: datetime):
        await self._execute("UPDATE calls SET ended_at = $2 WHERE id = $1", call_id, ended_at)

    async def add_turn(self, call_id: str, role: str, content: str, interrupted: bool = False):
        await self._execute(
            "INSERT INTO call_turns (call_id, role, content, interrupted) VALUES ($1, $2, $3, $4)",
            call_id, role, content, interrupted,
        )

    async def add_tool_call(self, call_id: str, tool: str, tool_input: dict, result):
        await self._execute(
            "INSERT INTO tool_calls (call_id, tool, input, result) VALUES ($1, $2, $3::jsonb, $4::jsonb)",
            call_id, tool, json.dumps(tool_input), json.dumps(result),
        )

    async def add_booking(self, call_id: str, reference: str, booking: dict):
        await self._execute(
            "INSERT INTO bookings (reference, call_id, name, service, requested, phone) VALUES ($1, $2, $3, $4, $5, $6)"
            " ON CONFLICT (reference) DO NOTHING",
            reference, call_id, booking.get("name"), booking.get("service"), booking.get("datetime"),
            booking.get("phone"),
        )

    async def close(self):
        if self._pool:
            await self._pool.close()
