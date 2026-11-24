"""PayPal REST calls (Orders v2, captures, webhook verification).

Nothing here touches the database. Every function raises httpx.HTTPError on failure.
"""
import json
from urllib.parse import quote, urlparse

import httpx

from app.config import settings


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.paypal_base_url, timeout=15.0)


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise httpx.DecodingError("PayPal sent an unreadable reply", request=resp.request) from exc
    if not isinstance(data, dict):
        raise httpx.DecodingError("PayPal sent an unexpected reply", request=resp.request)
    return data


def _call(method: str, path: str, *, request_id: str | None = None, **kwargs) -> dict:
    with _client() as client:
        auth = client.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
        )
        auth.raise_for_status()
        headers = {
            "Authorization": f"Bearer {_json(auth).get('access_token', '')}",
            "Content-Type": "application/json",
        }
        if request_id:
            # Makes a retried call return the first result instead of repeating it.
            headers["PayPal-Request-Id"] = request_id
        resp = client.request(method, path, headers=headers, **kwargs)
        resp.raise_for_status()
        return _json(resp)


def dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def create_order(
    *, total_cents: int, custom_id: str, description: str, return_url: str, cancel_url: str
) -> tuple[str, str]:
    """Create an order. Returns (order id, URL to send the buyer to)."""
    order = _call(
        "POST", "/v2/checkout/orders",
        request_id=f"create-{custom_id}",
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "custom_id": custom_id,
                "description": description[:127],
                "amount": {"currency_code": "USD", "value": dollars(total_cents)},
            }],
            "payment_source": {"paypal": {"experience_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            }}},
        },
    )
    links = {link["rel"]: link["href"] for link in order.get("links", [])}
    url = links.get("payer-action") or links.get("approve") or ""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not order.get("id") or parsed.scheme != "https" or not (host == "paypal.com" or host.endswith(".paypal.com")):
        raise httpx.DecodingError("PayPal did not return an approval link")
    return order["id"], url


def get_order(order_id: str) -> dict:
    return _call("GET", f"/v2/checkout/orders/{quote(order_id, safe='')}")


def capture_order(order_id: str, custom_id: str) -> None:
    """Charge an approved order. Read the result back with get_order."""
    _call(
        "POST", f"/v2/checkout/orders/{quote(order_id, safe='')}/capture",
        request_id=f"capture-{custom_id}", json={},
    )


def get_capture(capture_id: str) -> dict:
    return _call("GET", f"/v2/payments/captures/{quote(capture_id, safe='')}")


_SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def verify_webhook(headers, body: str) -> bool:
    """Ask PayPal whether this webhook is genuine. `body` must be valid JSON."""
    fields = {name: headers.get(header) for name, header in _SIGNATURE_HEADERS.items()}
    if not settings.PAYPAL_WEBHOOK_ID or not all(fields.values()):
        return False
    fields["webhook_id"] = settings.PAYPAL_WEBHOOK_ID
    # The event must go back byte for byte; re-serializing it breaks verification.
    payload = json.dumps(fields)[:-1] + ', "webhook_event": ' + body + "}"
    result = _call(
        "POST", "/v1/notifications/verify-webhook-signature", content=payload.encode("utf-8")
    )
    return result.get("verification_status") == "SUCCESS"
