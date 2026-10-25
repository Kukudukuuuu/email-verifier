
# emailip.py
import os
import requests

API_URL = "https://emailverifier.reoon.com/api/v1/verify"


def verify_with_api(email):
    api_key = os.getenv("REOON_API_KEY")

    if not api_key:
        return None

    try:
        response = requests.get(
            API_URL,
            params={
                "email": email,
                "key": api_key,
                "mode": "power",
            },
            timeout=(5, 120),
        )
        response.raise_for_status()
        data = response.json()

    except (requests.RequestException, ValueError):
        # Keep the app working if Reoon is unavailable.
        return None

    status = str(data.get("status", "")).lower()
    notes = ["Verified using Reoon Power mode"]

    if status == "error":
        return None

    if data.get("is_valid_syntax") is False:
        verdict = "likely to bounce"
        reason = "Reoon reports invalid email syntax."

    elif data.get("is_catch_all") is True or status == "catch_all":
        verdict = "unknown"
        reason = "The domain is catch-all; this mailbox is unconfirmed."

    elif (
        status == "safe"
        and data.get("is_deliverable") is True
        and data.get("is_safe_to_send") is True
    ):
        verdict = "likely to be delivered"
        reason = "Reoon reports the address as safe and deliverable."

    elif status in ("invalid", "disabled"):
        verdict = "likely to bounce"
        reason = f"Reoon classified the address as {status}."

    elif status == "disposable":
        verdict = "unknown"
        reason = "Reoon identifies a disposable address."

    elif status in ("spamtrap", "inbox_full"):
        verdict = "likely to bounce"
        reason = f"Reoon flagged the address as {status}."

    else:
        verdict = "unknown"
        reason = "Reoon could not conclusively verify this address."

    return {
        "verdict": verdict,
        "reason": reason,
        "notes": notes,
    }
