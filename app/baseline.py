import os
import json

from google import genai
from tenacity import (
    retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential
)

from app.models import ScreeningVerdict

BASELINE_PROMPT_TEMPLATE = """You are reviewing a DNA synthesis order for anything suspicious.

Customer: {institution_name}
Declared purpose: {declared_purpose}
Order sequences: {sequences}

Based only on this information, is this order suspicious? Respond with a single word: SUSPICIOUS or CLEAR.
"""

MODEL_NAME = "gemini-2.5-flash"


class BaselineUnavailableError(Exception):
    """
    Raised when the Gemini call fails after all retries are exhausted -
    e.g. sustained high demand / rate limiting / a transient outage.
    Distinguished from a missing-API-key RuntimeError, which is a config
    error and is never worth retrying.
    """


def _get_client() -> genai.Client:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY to run the baseline.")
    return genai.Client(api_key=api_key)


@retry(
    retry=retry_if_not_exception_type(RuntimeError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=65),
    reraise=True,
)
def _call_gemini(client: genai.Client, prompt: str):
    return client.models.generate_content(model=MODEL_NAME, contents=prompt)


def run_baseline(order: dict, customer: dict) -> dict:
    """
    Args:
        order: dict with order_id, customer_id, sequences
        customer: dict with institution_name, declared_purpose (from db.get_customer)
    Returns:
        dict with order_id, verdict ("clear" or "escalate" - baseline has
        no concept of the REVIEW_RECOMMENDED middle tier), raw_response
    Raises:
        RuntimeError: missing API key (config error, never retried)
        BaselineUnavailableError: all retries exhausted (e.g. sustained
            high demand on the model) - the caller should treat this as
            "baseline could not be evaluated for this case", not a crash.
    """
    client = _get_client()
    prompt = BASELINE_PROMPT_TEMPLATE.format(
        institution_name=customer["institution_name"],
        declared_purpose=customer["declared_purpose"],
        sequences=json.dumps(order["sequences"]),
    )

    try:
        response = _call_gemini(client, prompt)
    except RuntimeError:
        raise
    except Exception as e:
        raise BaselineUnavailableError(
            f"Gemini call failed after retries (order {order['order_id']}): {e}"
        ) from e

    raw = response.text.strip().upper()
    verdict = ScreeningVerdict.ESCALATE if "SUSPICIOUS" in raw else ScreeningVerdict.CLEAR

    return {
        "order_id": order["order_id"],
        "verdict": verdict.value,
        "raw_response": raw,
    }