"""WhatsApp booking confirmations, sent from the business number when a call that made a booking ends.

A WhatsApp call opens Meta's 24-hour customer service window, so a plain text message (no approved
template) can be sent right after the call.
"""

import os
from datetime import datetime

import aiohttp
from loguru import logger

GRAPH_URL = "https://graph.facebook.com/v23.0"


def spoken_time(requested: str) -> str:
    """'2026-10-30T12:00:00+02:00' -> 'Friday 30 October at 12:00'; anything else is shown as given."""
    try:
        return f"{datetime.fromisoformat(requested):%A %-d %B at %H:%M}"
    except ValueError:
        return requested


def booking_status(booking: dict) -> str:
    if booking.get("status") == "cancelled":
        return "Cancelled"
    return "Rescheduled" if booking.get("rescheduled_at") else "Confirmed"


def booking_message(bookings: list[dict]) -> str:
    """One message covering every booking made, moved or cancelled on the call."""
    business = os.getenv("BUSINESS_NAME", "Demo Co")
    lines = [f"Hi {bookings[0]['name']}, thanks for calling {business}. "
             f"Here's {'an update on your bookings' if len(bookings) > 1 else 'your booking'}:"]
    for b in bookings:
        lines += ["", f"Status: {booking_status(b)}", f"Reference: {b['reference']}", f"Service: {b['service']}",
                  f"When: {spoken_time(b['requested'])}"]
    if all(b.get("status") == "cancelled" for b in bookings):
        lines += ["", "To make a new booking, call us on this number."]
    else:
        lines += ["", "To change or cancel, call us on this number."]
    return "\n".join(lines)


async def send_whatsapp_text(session: aiohttp.ClientSession, to: str, body: str) -> bool:
    token, phone_number_id = os.getenv("WHATSAPP_TOKEN"), os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and phone_number_id):
        logger.warning("WhatsApp credentials not set; booking confirmation not sent")
        return False
    try:
        async with session.post(
            f"{GRAPH_URL}/{phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status == 200:
                logger.info(f"Booking confirmation sent to {to}")
                return True
            logger.error(f"Booking confirmation to {to} failed: {response.status} {await response.text()}")
    except Exception as e:
        logger.error(f"Booking confirmation to {to} failed: {e}")
    return False
