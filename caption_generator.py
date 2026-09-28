"""İsteğe bağlı Gemini metin üretimi. Videonun kendisi bu modüle bağlı değildir."""
from __future__ import annotations

import json
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"


class CaptionError(Exception):
    pass


class ModelUnavailable(CaptionError):
    pass


def generate_caption(api_key: str, artist: str, song: str) -> str:
    if not api_key.strip():
        raise CaptionError("Gemini API anahtarını girin.")
    if not artist.strip() or not song.strip():
        raise CaptionError("Açıklama için sanatçı ve şarkı adını girin.")
    prompt = (
        "Aşağıdaki nostaljik pop şarkısı için Türkçe bir Instagram Reels açıklaması yaz. "
        "Açıklama 35–55 kelime ve 2 veya 3 tam cümle olsun. Şarkının ve sanatçının adını "
        "an; şarkıyı yeniden dinlemenin hissettirdiklerini anlat ve izleyiciyi kendi anısını "
        "hatırlamaya davet et. Sıcak ve doğal bir dil kullan. Gerçek olmayan bilgiler, "
        "çıkış tarihleri, ödüller veya şarkı sözleri uydurma. Yalnızca açıklamayı yaz; "
        "ek açıklama ve tırnak işareti ekleme.\n"
        f"Sanatçı: {artist.strip()}\nŞarkı: {song.strip()}"
    )
    caption, model = _request_with_fallback(api_key, prompt, PRIMARY_MODEL)
    if len(caption.split()) >= 30:
        return caption
    expanded_prompt = (
        "Aşağıdaki taslağı Türkçe, 35–55 kelimelik ve 2 veya 3 cümlelik eksiksiz bir "
        "Instagram Reels açıklamasına genişlet. Şarkı ve sanatçı adlarını koru. Nostaljik "
        "bir duygu ve şarkıyı hatırlamaya yönelik bir davet ekle. Gerçek olmayan bilgiler "
        "veya şarkı sözleri uydurma. Yalnızca açıklamayı yaz.\n"
        f"Sanatçı: {artist.strip()}\nŞarkı: {song.strip()}\nTaslak: {caption}"
    )
    caption, _ = _request_with_fallback(api_key, expanded_prompt, model)
    if len(caption.split()) < 30:
        raise CaptionError("Gemini açıklamayı yine çok kısa üretti. Yeniden deneyin.")
    return caption


def _request_with_fallback(api_key: str, prompt: str, model: str) -> tuple[str, str]:
    try:
        return _request_caption(api_key, prompt, model), model
    except ModelUnavailable:
        if model != PRIMARY_MODEL:
            raise
        return _request_caption(api_key, prompt, FALLBACK_MODEL), FALLBACK_MODEL


def _request_caption(api_key: str, prompt: str, model: str) -> str:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    thinking_config = ({"thinkingBudget": 0} if model == PRIMARY_MODEL else {"thinkingLevel": "minimal"})
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 512,
            "temperature": 0.8,
            "thinkingConfig": thinking_config,
        },
    }).encode("utf-8")
    request = Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key.strip(),
    })
    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
            break
        except HTTPError as exc:
            if exc.code in (500, 502, 503, 504):
                if attempt == 0:
                    time.sleep(1 + random.uniform(0, 0.5))
                    continue
                raise CaptionError("Gemini geçici olarak yanıt vermiyor. Biraz sonra yeniden deneyin.") from exc
            if exc.code in (401, 403):
                raise CaptionError("Gemini API anahtarı geçersiz veya bu model için yetkiniz yok.") from exc
            if exc.code == 429:
                raise CaptionError("Gemini istek sınırına ulaşıldı. Daha sonra yeniden deneyin.") from exc
            if exc.code == 404:
                raise ModelUnavailable("Gemini modeli bu hesapta kullanılamıyor.") from exc
            raise CaptionError(f"Gemini isteği başarısız oldu (HTTP {exc.code}).") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CaptionError("Gemini'ye bağlanılamadı. İnternet bağlantısını kontrol edin.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CaptionError("Gemini'den geçersiz yanıt alındı.") from exc
    try:
        candidate = result["candidates"][0]
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise CaptionError("Gemini yanıtı yarıda kesti. Yeniden deneyin.")
        if candidate.get("finishReason") not in (None, "STOP"):
            raise CaptionError("Gemini açıklamayı tamamlayamadı. Yeniden deneyin.")
        caption = "".join(part.get("text", "") for part in candidate["content"]["parts"]
                          if not part.get("thought")).strip()
        if not caption:
            raise ValueError("boş yanıt")
        return caption
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CaptionError("Gemini metin döndürmedi. İsteği yeniden deneyin.") from exc
