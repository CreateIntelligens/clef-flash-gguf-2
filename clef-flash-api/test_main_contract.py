import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import main as api


STRUCTURED = {
    "model": "clef-flash",
    "state": {"invoice": {"status": "paid", "total": 1250}},
    "questions": {
        "status": {
            "type": "choice",
            "instructions": {"question": "Payment status?"},
            "criteria": {"paid": {"description": "Paid"}, "unpaid": None},
        },
        "large": {"type": "noul", "instructions": ["Total above 1000?"]},
        "amount": {
            "type": "score",
            "instructions": {"question": "Amount level?"},
            "criteria": ["Low", {"description": "Medium"}, ["High"]],
        },
    },
}

RESULT = {
    "model": "backend-model",
    "answers": {
        "status": {"type": "choice", "choice": "paid", "probabilities": {"paid": 0.9, "unpaid": 0.1}, "confidence": 0.8},
        "large": {"type": "noul", "noul": 0.9},
        "amount": {
            "type": "score",
            "score": 1.7,
            "legend": {"0": "Low", "1": {"description": "Medium"}, "2": ["High"]},
            "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
            "confidence": 0.7,
        },
    },
    "usage": {"input_tokens": 100, "output_tokens": 0},
}


class MainContractTests(unittest.TestCase):
    def test_health_uses_isolated_health_client(self):
        with TestClient(api.app) as client:
            health_get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))
            infer_get = AsyncMock()
            with patch.object(api.app.state.health_http, "get", health_get), patch.object(api.app.state.http, "get", infer_get):
                resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        health_get.assert_awaited_once()
        infer_get.assert_not_called()

    def test_systemone_success_and_async_telemetry(self):
        with patch.object(api, "record_telemetry", new_callable=AsyncMock) as telemetry:
            with TestClient(api.app) as client:
                post_mock = AsyncMock(return_value=httpx.Response(200, json=RESULT))
                with patch.object(api.app.state.http, "post", post_mock):
                    resp = client.post("/v1/systemone", json=STRUCTURED)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["model"], "clef-flash")
        telemetry.assert_awaited_once()
        self.assertIsNotNone(telemetry.call_args.kwargs["telemetry_client"])
        self.assertEqual(telemetry.call_args.kwargs["status_code"], 200)

    def test_physical_batch_overflow_maps_500_to_400(self):
        err_payload = {
            "error": {
                "message": "input (4570 tokens) is too large to process. increase the physical batch size (current batch size: 4096)"
            }
        }
        with patch.object(api, "record_telemetry", new_callable=AsyncMock) as telemetry:
            with TestClient(api.app) as client:
                post_mock = AsyncMock(return_value=httpx.Response(500, json=err_payload))
                with patch.object(api.app.state.http, "post", post_mock):
                    resp = client.post("/v1/systemone", json=STRUCTURED)
        self.assertEqual(resp.status_code, 400, resp.text)
        telemetry.assert_awaited_once()
        self.assertEqual(telemetry.call_args.kwargs["status_code"], 400)
        self.assertEqual(telemetry.call_args.kwargs["error_type"], "BatchSizeExceeded")

    def test_queue_full_returns_503_with_retry_after(self):
        with patch.object(api, "record_telemetry", new_callable=AsyncMock) as telemetry, patch.object(api, "INFERENCE_QUEUE_TIMEOUT", 0.01):
            with TestClient(api.app) as client:
                api.app.state.inference_semaphore = asyncio.Semaphore(0)
                post_mock = AsyncMock()
                with patch.object(api.app.state.http, "post", post_mock):
                    resp = client.post("/v1/systemone", json=STRUCTURED)
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.headers.get("retry-after"), "2")
        post_mock.assert_not_called()
        telemetry.assert_awaited_once()
        self.assertEqual(telemetry.call_args.kwargs["error_type"], "InferenceQueueFull")

    def test_influx_tag_escaping(self):
        self.assertEqual(api._escape_influx_tag("host=a, b\\c"), "host_a__b_c")


if __name__ == "__main__":
    unittest.main()
