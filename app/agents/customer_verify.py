"""
Customer Verification Agent.

Responsibility: assess the customer/institution behind an order for
identity-risk signals, independent of what sequences were ordered:
  - denylist match (sanctions-style list or a hardcoded denylist_flag)
  - "shell" pattern heuristic: very new account + vague declared purpose
    + free-mail email domain (gmail/outlook/etc rather than an
    institutional domain)

This agent knows nothing about sequences or fragmentation - it only ever
looks at the Customer record. Keeping it single-purpose (same principle as
the other two agents) means a judge can look at any one Signal and know
exactly which piece of evidence produced it.

Scope cut (documented): the "vague declared purpose" check is a small
hardcoded keyword list, not an LLM-based semantic judgment. This was a
deliberate trade-off for hackathon time - flagged as an area where the
orchestrator/LLM-reasoning layer could add real value later (e.g. letting
Gemini assess purpose plausibility against the institution profile).
"""

FREE_EMAIL_DOMAINS = {"gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "protonmail.com"}
VAGUE_PURPOSE_PHRASES = {"general research", "confidential", "confidential - client nda"}
NEW_ACCOUNT_THRESHOLD_DAYS = 14

from app.db import get_customer, is_customer_denylisted
from app.models import Signal, RiskTier, SignalSource


def verify_customer(conn, customer_id: str) -> list[Signal]:
    customer = get_customer(conn, customer_id)
    if customer is None:
        return [Signal(
            source=SignalSource.CUSTOMER_VERIFY,
            description=f"No customer record found for customer_id={customer_id}",
            risk_tier=RiskTier.MEDIUM,
            confidence=1.0,
            evidence={"customer_id": customer_id}
        )]

    signals: list[Signal] = []

    if is_customer_denylisted(conn, customer_id):
        signals.append(Signal(
            source=SignalSource.CUSTOMER_VERIFY,
            description=f"Customer '{customer['institution_name']}' matches a denylist entry",
            risk_tier=RiskTier.HIGH,
            confidence=1.0,
            evidence={"customer_id": customer_id, "institution_name": customer["institution_name"]}
        ))

    is_new_account = customer["account_age_days"] <= NEW_ACCOUNT_THRESHOLD_DAYS
    is_free_email = customer["email_domain"].lower() in FREE_EMAIL_DOMAINS
    is_vague_purpose = customer["declared_purpose"].strip().lower() in VAGUE_PURPOSE_PHRASES

    shell_signal_count = sum([is_new_account, is_free_email, is_vague_purpose])

    if shell_signal_count >= 2:
        signals.append(Signal(
            source=SignalSource.CUSTOMER_VERIFY,
            description=(
                f"Customer '{customer['institution_name']}' matches multiple shell-account "
                f"indicators (new account: {is_new_account}, free email domain: {is_free_email}, "
                f"vague declared purpose: {is_vague_purpose})"
            ),
            risk_tier=RiskTier.MEDIUM,
            confidence=round(shell_signal_count / 3, 2),
            evidence={
                "customer_id": customer_id,
                "account_age_days": customer["account_age_days"],
                "email_domain": customer["email_domain"],
                "declared_purpose": customer["declared_purpose"],
            }
        ))

    return signals