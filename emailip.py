from email_validator import (
    validate_email,
    EmailNotValidError,
    EmailUndeliverableError,
)


def verify_with_api(email):
    try:
        result = validate_email(
            email,
            check_deliverability=True,
        )

        return {
            "verdict": "unknown",
            "reason": (
                "Email syntax is valid and domain mail routing "
                "is available. Individual mailbox is unconfirmed."
            ),
            "notes": ["Checked using email-validator DNS checks"],
        }

    except EmailUndeliverableError as exc:
        return {
            "verdict": "likely to bounce",
            "reason": str(exc),
            "notes": ["Domain mail routing check failed"],
        }

    except EmailNotValidError as exc:
        return {
            "verdict": "likely to bounce",
            "reason": str(exc),
            "notes": ["Email syntax or domain validation failed"],
        }

    except Exception:
        return {
            "verdict": "unknown",
            "reason": "DNS validation could not be completed.",
            "notes": [],
        }
