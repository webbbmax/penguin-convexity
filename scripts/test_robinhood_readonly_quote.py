#!/usr/bin/env python3

import unittest

from robinhood_readonly_quote import V2_ROUTER, V3_QUOTER, V4_POOL_MANAGER, V4_QUOTER, quote_pool


TOKEN = "0x1111111111111111111111111111111111111111"
OUTPUT = "0x2222222222222222222222222222222222222222"
PAIR = "0x3333333333333333333333333333333333333333"
POOL_ID = "0x" + "44" * 32


def word(value):
    return f"{int(value) % (1 << 256):064x}"


def address_result(value):
    return "0x" + value.lower().removeprefix("0x").rjust(64, "0")


class FakeClient:
    def __init__(self, version):
        self.version = version
        self.calls = []

    def request(self, source, url, **kwargs):
        payload = kwargs["payload"]
        self.calls.append(payload)
        method = payload["method"]
        if method == "eth_getLogs":
            log = {
                "topics": [
                    "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438",
                    POOL_ID,
                    address_result(TOKEN),
                    address_result(OUTPUT),
                ],
                "data": "0x" + word(3000) + word(60) + word(0) + word(1) + word(0),
            }
            return "success", {"result": [log]}, 200, []
        call = payload["params"][0]
        address = call["to"].lower()
        data = call["data"].lower()
        if address == V4_QUOTER:
            return "success", {"result": "0x" + word(987)}, 200, []
        if data == "0x0dfe1681":
            return "success", {"result": address_result(TOKEN)}, 200, []
        if data == "0xd21220a7":
            return "success", {"result": address_result(OUTPUT)}, 200, []
        if data == "0xddca3f43":
            if self.version == "v3":
                return "success", {"result": "0x" + word(500)}, 200, []
            return "success", {"error": {"code": 3, "message": "execution reverted"}}, 200, []
        if address == V3_QUOTER:
            return "success", {"result": "0x" + word(876) + word(0) * 3}, 200, []
        if address == V2_ROUTER:
            return "success", {"result": "0x" + word(32) + word(2) + word(100) + word(765)}, 200, []
        raise AssertionError((method, address, data[:10]))


class RobinhoodReadOnlyQuoteTests(unittest.TestCase):
    def test_v4_pool_id_is_reconstructed_from_initialize_event_and_quoted(self):
        client = FakeClient("v4")
        result = quote_pool(client, "https://rpc.example", POOL_ID, TOKEN, 100)
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["provider"], "Uniswap V4 Robinhood")
        self.assertEqual(result["outputToken"], OUTPUT)
        self.assertEqual(result["outputAmount"], 987)
        self.assertEqual(client.calls[0]["params"][0]["address"], V4_POOL_MANAGER)
        v4_call = client.calls[-1]["params"][0]["data"]
        self.assertTrue(v4_call.startswith("0xaa9d21cb"))

    def test_v3_pair_uses_official_quoter(self):
        result = quote_pool(FakeClient("v3"), "https://rpc.example", PAIR, TOKEN, 100)
        self.assertEqual((result["state"], result["outputAmount"]), ("success", 876))
        self.assertEqual(result["route"]["protocol"], "uniswap_v3")

    def test_v2_pair_falls_back_to_official_router(self):
        result = quote_pool(FakeClient("v2"), "https://rpc.example", PAIR, TOKEN, 100)
        self.assertEqual((result["state"], result["outputAmount"]), ("success", 765))
        self.assertEqual(result["route"]["protocol"], "uniswap_v2")


if __name__ == "__main__":
    unittest.main()
