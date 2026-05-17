"""PayPal Orders v2 API helpers (async, using httpx)."""
import httpx

from app.config import settings


async def get_access_token() -> str:
    """Obtain a Bearer token from PayPal using client credentials."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def create_paypal_order(access_token: str, amount_cents: int) -> str:
    """Create a PayPal order and return its order ID."""
    amount_str = f"{amount_cents / 100:.2f}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v2/checkout/orders",
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "amount": {
                            "currency_code": "USD",
                            "value": amount_str,
                        }
                    }
                ],
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def capture_paypal_order(access_token: str, order_id: str) -> dict:
    """Capture a PayPal order and return the full response dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v2/checkout/orders/{order_id}/capture",
            json={},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_paypal_order(access_token: str, order_id: str) -> dict:
    """Fetch order details (for verifying captured amount)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.paypal_base_url}/v2/checkout/orders/{order_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
