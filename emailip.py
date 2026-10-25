
import os
import requests

API_URL = "https://emailreputation.abstractapi.com/v1"


def verify_with_api(email):
    api_key = os.getenv("ABSTRACT_API_KEY")

    if not api_key:
        return None

    try:
        response = requests.get(
            API_URL,
            params={
                "api_key": api_key,
                "email": email,
            },
            timeout=(4, 15),
        )
        response.raise_for_status()
        data = response.json()

    except (requests.RequestException, ValueError):
        return None

    delivery = data.get("email_deliverability") or {}
    quality = data.get("email_quality") or {}

    status = str(delivery.get("status", "")).lower()
    format_valid = delivery.get("is_format_valid")
    mx_valid = delivery.get("is_mx_valid")
    smtp_valid = delivery.get("is_smtp_valid")
    catch_all = quality.get("is_catchall")

    notes = ["Checked using Abstract Email Reputation"]

    if (
        format_valid is False
        or mx_valid is False
        or status == "undeliverable"
    ):
        return {
            "verdict": "likely to bounce",
            "reason": "The provider reports invalid format, mail routing, or undeliverable status.",
            "notes": notes,
        }

    if catch_all is True:
        return {
            "verdict": "unknown",
            "reason": "Catch-all domain; individual mailbox is unconfirmed.",
            "notes": notes,
        }

    if (
        status == "deliverable"
        and format_valid is True
        and mx_valid is True
        and smtp_valid is True
    ):
        return {
            "verdict": "likely to be delivered",
            "reason": "The provider reports deliverable status and valid SMTP and MX checks.",
            "notes": notes,
        }

    return {
        "verdict": "unknown",
        "reason": "The provider could not conclusively confirm deliverability.",
        "notes": notes,
    }
