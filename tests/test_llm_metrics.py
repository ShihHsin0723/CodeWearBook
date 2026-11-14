import os
import unittest
from unittest.mock import Mock, patch

import llm
from config import LlmSource
from llm_metrics import QueryMetrics, set_call_usage
from scripts.eval.summarize_efficiency import cost_for_call, summarize_rows


class LlmMetricsTests(unittest.TestCase):
    def test_latest_opus_is_selected_once_from_available_models(self):
        response = Mock()
        response.json.return_value = {"data": [
            {"id": "claude-sonnet-5-5"},
            {"id": "claude-opus-5-5"},
            {"id": "claude-opus-5"},
        ]}
        llm._resolve_anthropic_model.cache_clear()
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "latest-opus"}), \
             patch.object(llm, "_get_api_key", return_value="test-key"), \
             patch.object(llm.requests, "get", return_value=response) as get:
            self.assertEqual(llm._resolve_anthropic_model(), "claude-opus-5-5")
            self.assertEqual(llm._resolve_anthropic_model(), "claude-opus-5-5")
            get.assert_called_once()
        llm._resolve_anthropic_model.cache_clear()

    def test_only_configured_provider_is_used(self):
        with patch.object(llm, "_select_working_api_provider", return_value=LlmSource.ANTROPIC_API), \
             patch.object(llm, "_provider_call", side_effect=RuntimeError("unavailable")) as call:
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                llm.call_llm("test")
            call.assert_called_once_with(LlmSource.ANTROPIC_API, "test", llm.DEFAULT_LLM_TIMEOUT_SECONDS)

    def test_call_metrics_capture_tokens_and_failure(self):
        def successful_call(_prompt, timeout):
            set_call_usage("claude-opus-5-5", 17, 4)
            return "OK"

        with patch.object(llm, "call_claude_llm", side_effect=successful_call):
            metrics = QueryMetrics()
            with metrics:
                self.assertEqual(llm._provider_call(LlmSource.ANTROPIC_API, "test", 5), "OK")
            result = metrics.as_dict()
            self.assertEqual(result["llm_input_tokens"], 17)
            self.assertEqual(result["llm_output_tokens"], 4)
            self.assertEqual(result["llm_call_count"], 1)
            self.assertTrue(result["llm_usage_complete"])
            self.assertGreaterEqual(result["query_elapsed_seconds"], result["llm_elapsed_seconds"])

        with patch.object(llm, "call_claude_llm", side_effect=RuntimeError("failed")):
            metrics = QueryMetrics()
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with metrics:
                    llm._provider_call(LlmSource.ANTROPIC_API, "test", 5)
            self.assertEqual(metrics.as_dict()["llm_calls"][0]["error"], "RuntimeError")
            self.assertFalse(metrics.as_dict()["llm_usage_complete"])

    def test_anthropic_payload_and_usage_parsing(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {
            "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 15, "output_tokens": 4,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
        }
        with patch.object(llm, "_get_api_key", return_value="test-key"), \
             patch.object(llm, "_resolve_anthropic_model", return_value="claude-opus-5-5"), \
             patch.object(llm.requests, "post", return_value=response) as post:
            metrics = QueryMetrics()
            with metrics:
                reply = llm._provider_call(LlmSource.ANTROPIC_API, "Reply OK", 5)
        self.assertEqual(reply, "OK")
        self.assertNotIn("temperature", post.call_args.kwargs["json"])
        self.assertEqual(metrics.as_dict()["llm_input_tokens"], 15)
        self.assertEqual(metrics.as_dict()["llm_calls"][0]["model"], "claude-opus-5-5")

    def test_summary_does_not_price_unknown_usage(self):
        prices = {"claude-opus-5-5": {"input": 4, "output": 20,
                    "cache_write_5m": 5, "cache_write_1h": 8, "cache_read": 0.2}}
        call = {"model": "claude-opus-5-5", "provider": "Anthropic",
                "input_tokens": 100, "output_tokens": 10,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "elapsed_seconds": 0.1, "attempts": 1}
        self.assertAlmostEqual(cost_for_call(call, prices), 0.0006)
        rows = [{"query_elapsed_seconds": 1.0, "llm_elapsed_seconds": 0.1,
                 "llm_calls": [call], "llm_usage_complete": True,
                 "sql_match": True, "dataframe_match": True}]
        summary = summarize_rows(rows, prices)
        self.assertAlmostEqual(summary["estimated_llm_cost_usd"], 0.0006)
        self.assertEqual(summary["query_latency_p95_seconds"], 1.0)
        self.assertEqual(summary["query_latency_mean_seconds"], 1.0)
        self.assertEqual(summary["input_tokens_mean_per_case"], 100)
        self.assertEqual(summary["output_tokens_mean_per_case"], 10)
        self.assertEqual(summary["total_tokens_mean_per_case"], 110)
        self.assertIsNone(summarize_rows(rows, {})["estimated_llm_cost_usd"])


if __name__ == "__main__":
    unittest.main()
