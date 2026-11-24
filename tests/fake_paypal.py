"""In-memory PayPal, spoken to over httpx so real request code is exercised."""
import json

import httpx


class FakePayPal:
    def __init__(self):
        self.orders = {}
        self.captures = {}
        self.calls = []           # (method, path, headers, body)
        self.capture_count = 0    # charges actually made
        self.capture_status = "COMPLETED"
        self.fail = {}            # path suffix -> status code or exception
        self.signature_ok = True
        self.after_capture_fail = False   # charge, then lose the response

    def client(self):
        return httpx.Client(
            base_url="https://api-m.sandbox.paypal.com", transport=httpx.MockTransport(self.handle)
        )

    def approve(self, order_id):
        self.orders[order_id]["status"] = "APPROVED"

    def handle(self, request):
        path, body = request.url.path, request.content.decode()
        self.calls.append((request.method, path, request.headers, body))
        for suffix, what in self.fail.items():
            if path.endswith(suffix):
                if isinstance(what, Exception):
                    raise what
                return httpx.Response(what, json={"name": "ERR"})
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok"})
        if path == "/v1/notifications/verify-webhook-signature":
            ok = "SUCCESS" if self.signature_ok else "FAILURE"
            return httpx.Response(200, json={"verification_status": ok})
        if path == "/v2/checkout/orders":
            data = json.loads(body)
            oid = f"ORDER{len(self.orders) + 1}"
            self.orders[oid] = {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                                "purchase_units": data["purchase_units"]}
            url = f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"
            return httpx.Response(200, json={"id": oid, "links": [{"rel": "payer-action", "href": url}]})
        if path.startswith("/v2/payments/captures/"):
            cap = self.captures.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=cap) if cap else httpx.Response(404, json={})
        oid = path.split("/")[4]
        order = self.orders.get(oid)
        if order is None:
            return httpx.Response(404, json={})
        if path.endswith("/capture"):
            return self.capture(order)
        return httpx.Response(200, json=order)

    def capture(self, order):
        if order["status"] != "APPROVED" or self.capture_status == "REFUSED":
            return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"})
        unit = order["purchase_units"][0]
        cap = {"id": f"CAP{len(self.captures) + 1}", "status": self.capture_status,
               "amount": unit["amount"], "custom_id": unit["custom_id"]}
        self.captures[cap["id"]] = cap
        unit.setdefault("payments", {}).setdefault("captures", []).append(cap)
        order["status"] = "COMPLETED"
        self.capture_count += 1
        if self.after_capture_fail:
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(201, json={"id": order["id"], "status": "COMPLETED"})
