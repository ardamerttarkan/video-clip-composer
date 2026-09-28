"""FFmpeg tabanlı, tek video için güvenli Reels üretimi."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
WORK = ROOT / "work"
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
CANVAS = (1080, 1920)


class VideoError(Exception):
    pass


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    has_audio: bool


def run_checked(args: list[str], stage: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"{stage}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1600:]
        raise VideoError(f"{stage}: {detail or 'komut başarısız oldu'}")
    return result


def probe_video(path: Path) -> Probe:
    if not path.is_file():
        raise VideoError("Kaynak dosya bulunamadı.")
    result = run_checked([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path),
    ], "Video inceleme")
    try:
        data = json.loads(result.stdout)
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("geçersiz süre")
        return Probe(duration, int(video["width"]), int(video["height"]),
                     any(s["codec_type"] == "audio" for s in data["streams"]))
    except (KeyError, ValueError, StopIteration, TypeError) as exc:
        raise VideoError("Video inceleme: geçerli video akışı veya süre bulunamadı.") from exc


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or any(not p.isdigit() for p in parts):
        raise VideoError("Başlangıç mm:ss veya hh:mm:ss biçiminde olmalı.")
    nums = [int(p) for p in parts]
    if any(n >= 60 for n in nums[1:]):
        raise VideoError("Dakika ve saniye alanları 0–59 arasında olmalı.")
    return float(sum(n * 60 ** i for i, n in enumerate(reversed(nums))))


def check_space(directory: Path, required_bytes: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < required_bytes:
        need = required_bytes / (1024 ** 3)
        have = free / (1024 ** 3)
        raise VideoError(f"Disk alanı yetersiz: yaklaşık {need:.2f} GB gerekli, {have:.2f} GB boş.")


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
    if parsed.scheme != "https" or host not in allowed or not parsed.path.strip("/"):
        raise VideoError("Geçerli bir HTTPS YouTube video bağlantısı girin.")
    return url.strip()


def slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:48] or "reel"


def unique_output(artist: str, song: str, day: int) -> Path:
    OUTPUTS.mkdir(exist_ok=True)
    stem = f"gun-{day:03d}_{slug(artist)}_{slug(song)}"
    for n in range(1, 10000):
        path = OUTPUTS / f"{stem}{'_' + str(n) if n > 1 else ''}.mp4"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return path
    raise VideoError("Çıktı dosyası için benzersiz ad bulunamadı.")


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # Çok uzun tek kelimeyi de güvenli genişlikte böl.
            for char in word:
                if draw.textbbox((0, 0), current + char, font=font)[2] > max_width and current:
                    lines.append(current)
                    current = ""
                current += char
        lines.append(current)
    return lines


def _draw_box(image: Image.Image, text: str, bounds: tuple[int, int, int, int],
              preferred_size: int, minimum_size: int, spacing: int = 12) -> None:
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = bounds
    if not text.strip():
        return
    for size in range(preferred_size, minimum_size - 1, -2):
        font = ImageFont.truetype(str(FONT), size)
        lines = _wrapped_lines(draw, text, font, x2 - x1)
        heights = [draw.textbbox((0, 0), line or " ", font=font)[3] -
                   draw.textbbox((0, 0), line or " ", font=font)[1] for line in lines]
        line_step = max(heights) + spacing
        total_height = len(lines) * line_step - spacing
        if total_height <= y2 - y1:
            top = y1 + (y2 - y1 - total_height) / 2
            for line in lines:
                draw.text((CANVAS[0] / 2, top), line, fill="white", font=font,
                          anchor="ma", stroke_width=1, stroke_fill="white")
                top += line_step
            return
    raise VideoError("Metin yerleşimi: metin ayrılan alana sığmıyor; kısaltın.")


def make_overlay(path: Path, top: str, bottom: str, song_line: str) -> None:
    if not FONT.is_file():
        raise VideoError("Metin yerleşimi: DejaVu Sans fontu yok. fonts-dejavu-core kurun.")
    image = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    _draw_box(image, top, (72, 105, 1008, 535), 64, 32)
    _draw_box(image, song_line, (72, 1340, 1008, 1455), 34, 26)
    _draw_box(image, bottom, (72, 1500, 1008, 1795), 44, 28)
    image.save(path)


def render(source: Path, dest: Path, start: float, duration: float, top: str,
           bottom: str, song_line: str, mode: str, preview: bool,
           allow_silent: bool = False, progress: Callable[[float], None] | None = None) -> float:
    """Render to an unoccupied destination. Returns elapsed seconds."""
    WORK.mkdir(parents=True, exist_ok=True)
    info = probe_video(source)
    if start < 0 or duration <= 0 or start + duration > info.duration + 0.05:
        raise VideoError("Kesit seçimi: başlangıç veya bitiş kaynak video süresini aşıyor.")
    if not info.has_audio and not allow_silent:
        raise VideoError("Kaynakta ses yok. Sessiz video üretimi seçeneğini açın.")
    if mode not in {"fit", "fill"}:
        raise VideoError("Geçersiz yerleşim modu.")
    # Çıktı + geçici dosya için ihtiyatlı pay.
    check_space(dest.parent, max(500_000_000, int(duration * (1_500_000 if not preview else 300_000))))
    dest.parent.mkdir(parents=True, exist_ok=True)
    scale_factor = 0.5 if preview else 1
    w, h = int(1080 * scale_factor), int(1920 * scale_factor)
    box_w, box_h, box_y = w, int(720 * scale_factor), int(600 * scale_factor)
    with tempfile.TemporaryDirectory(prefix="render-", dir=WORK) as temp:
        tempdir = Path(temp)
        overlay = tempdir / "text.png"
        make_overlay(overlay, top, bottom, song_line)
        filters = (f"scale={box_w}:{box_h}:force_original_aspect_ratio={'increase' if mode == 'fill' else 'decrease'},"
                   f"{'crop' if mode == 'fill' else 'pad'}={box_w}:{box_h}"
                   f"{'' if mode == 'fill' else ':(ow-iw)/2:(oh-ih)/2:black'},setsar=1")
        graph = (f"[0:v]{filters}[clip];color=c=black:s={w}x{h}:r=30[bg];"
                 f"[bg][clip]overlay=0:{box_y}:shortest=1[base];"
                 f"[1:v]scale={w}:{h}[text];[base][text]overlay=0:0:shortest=1,format=yuv420p[v]")
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                "-progress", "pipe:1", "-ss", f"{start:.3f}", "-i", str(source),
                "-loop", "1", "-i", str(overlay),
                "-t", f"{duration:.3f}", "-filter_complex", graph,
                "-map", "[v]"]
        if info.has_audio:
            args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
        else:
            args += ["-an"]
        args += ["-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast",
                 "-crf", "27" if preview else "20", "-pix_fmt", "yuv420p", "-r", "30",
                 "-movflags", "+faststart", "-shortest", "-f", "mp4", str(dest)]
        started = time.monotonic()
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, bufsize=1)
            assert process.stdout is not None
            for line in process.stdout:
                if line.startswith("out_time_ms=") and progress:
                    try:
                        progress(min(0.99, max(0.0, int(line.split("=", 1)[1]) / 1_000_000 / duration)))
                    except ValueError:
                        pass
            stderr = process.communicate()[1]
            if process.returncode:
                raise VideoError(f"Video kodlama: {stderr.strip()[-1600:] or 'FFmpeg başarısız oldu'}")
            result = probe_video(dest)
            if abs(result.duration - duration) > 0.25 or not dest.stat().st_size:
                raise VideoError(f"Çıktı denetimi: beklenen {duration:.2f} sn, bulunan {result.duration:.2f} sn.")
            if progress:
                progress(1.0)
            return time.monotonic() - started
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise VideoError(f"Video kodlama: FFmpeg başlatılamadı veya çıktı yazılamadı: {exc}") from exc
        except VideoError:
            dest.unlink(missing_ok=True)
            raise
