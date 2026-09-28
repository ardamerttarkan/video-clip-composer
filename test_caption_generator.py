import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from caption_generator import CaptionError, generate_caption


class FakeResponse(io.BytesIO):
    pass


LONG_CAPTION = (
    "Bu şarkıyı yeniden duyunca o eski akşamların sıcaklığı bir anda geri geliyor. "
    "Usher ve DJ Got Us Fallin' in Love, tanıdık ritmiyle yıllar önceki güzel anları "
    "hatırlatıyor. Sesi biraz açın ve bu şarkının sizi götürdüğü anı yorumlarda bizimle paylaşın."
)


class CaptionTests(unittest.TestCase):
    def test_request_sends_only_song_metadata_and_returns_text(self):
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": LONG_CAPTION}]}}]}
        with patch("caption_generator.urlopen", return_value=FakeResponse(json.dumps(response).encode())) as call:
            result = generate_caption("test-key", "Usher", "DJ Got Us Fallin' in Love")
        self.assertEqual(result, LONG_CAPTION)
        request = call.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        payload = json.loads(request.data)
        prompt = payload["contents"][0]["parts"][0]["text"]
        self.assertIn("Usher", prompt)
        self.assertIn("DJ Got Us Fallin' in Love", prompt)
        self.assertIn("Türkçe bir Instagram Reels açıklaması", prompt)
        self.assertIn("Sanatçı: Usher", prompt)
        self.assertIn("Şarkı: DJ Got Us Fallin' in Love", prompt)
        self.assertNotIn("test-key", prompt)
        self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingBudget"], 0)
        self.assertIn("gemini-2.5-flash", request.full_url)

    def test_rate_limit_has_clear_error_without_key(self):
        error = HTTPError("https://example.test", 429, "rate limit", {}, None)
        with patch("caption_generator.urlopen", side_effect=error):
            with self.assertRaisesRegex(CaptionError, "istek sınırına"):
                generate_caption("private-key", "Usher", "Test")

    def test_service_unavailable_retries_once(self):
        error = HTTPError("https://example.test", 503, "unavailable", {}, None)
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": LONG_CAPTION}]}}]}
        with patch("caption_generator.urlopen", side_effect=[error, FakeResponse(json.dumps(response).encode())]) as call, \
             patch("caption_generator.time.sleep"):
            self.assertEqual(generate_caption("test-key", "Usher", "Test"), LONG_CAPTION)
        self.assertEqual(call.call_count, 2)

    def test_truncated_answer_is_not_shown(self):
        response = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "A nostalgic"}]}}]}
        with patch("caption_generator.urlopen", return_value=FakeResponse(json.dumps(response).encode())):
            with self.assertRaisesRegex(CaptionError, "yarıda kesti"):
                generate_caption("test-key", "Usher", "Test")

    def test_short_answer_is_expanded_once(self):
        short = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "A nostalgic favorite."}]}}]}
        long = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": LONG_CAPTION}]}}]}
        with patch("caption_generator.urlopen", side_effect=[
            FakeResponse(json.dumps(short).encode()), FakeResponse(json.dumps(long).encode())]) as call:
            self.assertEqual(generate_caption("test-key", "Usher", "Test"), LONG_CAPTION)
        self.assertEqual(call.call_count, 2)
        expanded_prompt = json.loads(call.call_args_list[1].args[0].data)["contents"][0]["parts"][0]["text"]
        self.assertIn("Türkçe, 35–55 kelimelik", expanded_prompt)

    def test_unavailable_25_model_uses_flash_lite(self):
        error = HTTPError("https://example.test", 404, "not found", {}, None)
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": LONG_CAPTION}]}}]}
        with patch("caption_generator.urlopen", side_effect=[error, FakeResponse(json.dumps(response).encode())]) as call:
            self.assertEqual(generate_caption("test-key", "Usher", "Test"), LONG_CAPTION)
        self.assertIn("gemini-2.5-flash", call.call_args_list[0].args[0].full_url)
        fallback_request = call.call_args_list[1].args[0]
        self.assertIn("gemini-3.5-flash-lite", fallback_request.full_url)
        fallback_config = json.loads(fallback_request.data)["generationConfig"]["thinkingConfig"]
        self.assertEqual(fallback_config, {"thinkingLevel": "minimal"})


if __name__ == "__main__":
    unittest.main()
