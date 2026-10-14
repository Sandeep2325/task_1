import json

from cryptography.hazmat.primitives.asymmetric import rsa

from workspace import auth

from .base import PROJECT, IntegrationTestCase

BOM = f"/api/projects/{PROJECT}/bom"
SIDEBAR = f"/api/projects/{PROJECT}/items/DRV-03/sidebar"
PRICE_KEYS = {"unit_price", "line_total", "totals", "currency", "price_valid_until", "lead_time_days", "longest_lead_time"}


def all_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from all_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from all_keys(v)


class BomEntitlementTests(IntegrationTestCase):
    async def test_sales_engineer_gets_prices_totals_and_longest_lead_time(self):
        body = await self.get_json(BOM, role="sales_engineer", country="DE")
        self.assertEqual(len(body["lines"]), 5)
        self.assertTrue(all(line["price_status"] == "ok" for line in body["lines"]))
        expected = sum(line["line_total"] for line in body["lines"])
        self.assertEqual(body["pricing"]["totals"], {"EUR": round(expected, 2)})
        self.assertTrue(body["pricing"]["complete"])
        self.assertEqual(body["pricing"]["longest_lead_time"]["item_tags"], ["CAB-02"])

    async def test_channel_partner_response_contains_no_price_data_at_all(self):
        body = await self.get_json(BOM, role="channel_partner", country="DE")
        self.assertEqual(body["pricing"], {"status": "not_permitted"})
        self.assertFalse(PRICE_KEYS & set(all_keys(body)), json.dumps(body))
        self.assertEqual([line["qty"] for line in body["lines"]], [1, 1, 1, 1, 1])
        self.assertEqual(self.calls["pricing"], 0)  # the ERP is never even asked

    async def test_partner_cannot_read_prices_a_sales_engineer_already_cached(self):
        await self.get_json(SIDEBAR, role="sales_engineer")
        body = await self.get_json(SIDEBAR, role="channel_partner")
        self.assertEqual(body["sections"]["pricing"], {
            "status": "not_permitted", "data": None, "fetched_at": None, "pending": False, "error": None,
        })

    async def test_administrator_sees_prices(self):
        body = await self.get_json(SIDEBAR, role="administrator")
        self.assertEqual(body["sections"]["pricing"]["status"], "ok")

    async def test_prices_follow_the_callers_country(self):
        de = await self.get_json(SIDEBAR, country="DE")
        india = await self.get_json(SIDEBAR, country="IN")
        self.assertEqual(de["sections"]["pricing"]["data"]["currency"], "EUR")
        self.assertEqual(india["sections"]["pricing"]["data"]["currency"], "INR")
        self.assertNotEqual(de["sections"]["pricing"]["data"]["unit_price"], india["sections"]["pricing"]["data"]["unit_price"])

    async def test_country_without_price_list_is_unavailable_not_an_error(self):
        body = await self.get_json(SIDEBAR, country="BR")
        self.assertEqual(body["sections"]["pricing"]["status"], "unavailable")
        self.assertEqual(body["sections"]["catalogue"]["status"], "ok")


class TokenTests(IntegrationTestCase):
    async def assert_401(self, token):
        response = await self.async_client.get(SIDEBAR, headers={"Authorization": f"Bearer {token}"} if token else {})
        self.assertEqual(response.status_code, 401, response.content)

    async def test_missing_token(self):
        await self.assert_401(None)

    async def test_expired_token(self):
        await self.assert_401(auth.mint_dev_token(sub="u", role="sales_engineer", country="DE", ttl_s=-120))

    async def test_wrong_audience(self):
        await self.assert_401(auth.mint_dev_token(sub="u", role="sales_engineer", country="DE", aud="another-api"))

    async def test_wrong_issuer(self):
        await self.assert_401(auth.mint_dev_token(sub="u", role="sales_engineer", country="DE", iss="https://evil.example"))

    async def test_signed_by_another_key(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        await self.assert_401(auth.mint_dev_token(sub="u", role="sales_engineer", country="DE", key=other))

    async def test_unknown_role(self):
        await self.assert_401(auth.mint_dev_token(sub="u", role="superuser", country="DE"))

    async def test_role_cannot_be_escalated_by_editing_the_token(self):
        token = auth.mint_dev_token(sub="u", role="channel_partner", country="DE")
        header, payload, sig = token.split(".")
        import base64

        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["role"] = "sales_engineer"
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        await self.assert_401(f"{header}.{forged}.{sig}")
