import dataclasses
import unittest

from uif.provider_catalogue import normalize_model_catalogue as observe
from uif.provider_pool import ProviderRoute


class CatalogueTests(unittest.TestCase):
    def test_known_wire_shapes(self):
        model = "vendor/test:free"
        for shape, payload in (
            ("openai-models", {"data": [{"id": model, "api_key": "ignored"}]}),
            ("openai-model", {"id": model}),
            ("gemini-model", {"name": "models/" + model}),
            ("gemini-models", {"models": [{"name": "models/" + model}]}),
            ("cloudflare-workers-ai", {"success": True, "result": [{"name": model}]}),
            ("codex-app-server", {"data": [{"model": model, "id": "client alias"}]}),
            ("ollama-tags", {"models": [{"name": model}]}),
            ("windmill-model-audit", {"provider": "nous", "status": "catalog_verified_not_inference",
                                     "inference_calls": 0, "model_ids": [model]}),
        ):
            with self.subTest(shape=shape):
                observation = observe(shape, payload)
                self.assertEqual(dataclasses.asdict(observation), {
                    "models": (model,), "complete": shape == "ollama-tags"})
                route = ProviderRoute("id", "provider", "client", model,
                                      "", "", "", "", "", "", "", frozenset())
                self.assertTrue(observation.lists(route))
                self.assertFalse(observation.lists(dataclasses.replace(route, model="other")))

    def test_partial_or_empty_is_not_complete(self):
        result = observe("gemini-models", {"models": [], "nextPageToken": "ignored"})
        self.assertEqual(result.models, ())
        self.assertFalse(result.complete)

    def test_invalid_input_never_becomes_a_positive_observation(self):
        for shape, payload in (
            ("some-url", {"data": [{"id": "some-model"}]}),
            ("openai-models", {"data": [{"id": "test/../paid"}]}),
            ("openai-models", {"data": [{"id": "https://provider.example/model"}]}),
            ("openai-models", {"data": [{"id": "\n"}]}),
            ("openai-models", {"data": [{"id": "a" * 193}]}),
            ("openai-models", {"data": [{"id": "a"}] * 4097}),
            ("gemini-models", {"models": [{"name": "model"}]}),
            ("cloudflare-workers-ai", {"success": False, "result": []}),
            ("windmill-model-audit", {"provider": "deepseek", "status": "balance_metadata_verified",
                                     "inference_calls": 0, "model_ids": ["anything"]}),
        ):
            with self.subTest(shape=shape):
                with self.assertRaises(ValueError):
                    observe(shape, payload)
