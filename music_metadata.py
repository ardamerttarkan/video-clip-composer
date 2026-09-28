"""YouTube müzik alanları ve başlığından düzenlenebilir öneriler çıkarır."""
from __future__ import annotations

import re


def _clean_title(value: str) -> str:
    def drop_label(match: re.Match[str]) -> str:
        content = match.group(1)
        return " " if re.search(r"official|music video|lyric|audio|visualizer|\bhd\b|\b4k\b", content, re.I) else match.group(0)

    value = re.sub(r"[\[(]([^\])]+)[\])]", drop_label, value)
    value = re.sub(r"\s+", " ", value).strip(" -–—|·")
    return value.strip()


def infer_music(info: dict) -> tuple[str, str]:
    """Prefer explicit yt-dlp fields, then a conventional 'Artist - Song' title."""
    title = _clean_title(info.get("title") if isinstance(info.get("title"), str) else "")
    artist = info.get("artist") if isinstance(info.get("artist"), str) else ""
    if not artist and isinstance(info.get("artists"), list):
        artist = " & ".join(x for x in info["artists"] if isinstance(x, str))
    song = info.get("track") if isinstance(info.get("track"), str) else ""
    artist, song = artist.strip(), _clean_title(song)

    parts = re.split(r"\s+[-–—|]\s+", title, maxsplit=1)
    if len(parts) == 2:
        artist = artist or parts[0].strip()
        song = song or parts[1].strip()
    else:
        song = song or title
        uploader = info.get("uploader") if isinstance(info.get("uploader"), str) else ""
        if not artist and re.search(r"(?:VEVO|\s+-\s+Topic)$", uploader, re.I):
            artist = re.sub(r"(?:VEVO|\s+-\s+Topic)$", "", uploader, flags=re.I).strip()

    feature = re.search(r"\s+(?:ft\.?|feat\.?|featuring)\s+(.+)$", song, re.I)
    if feature and artist:
        guest = feature.group(1).strip()
        song = song[:feature.start()].strip()
        if guest.casefold() not in artist.casefold():
            artist = f"{artist} ft. {guest}"
    return artist, song
