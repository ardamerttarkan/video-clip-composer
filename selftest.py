"""Çevrimdışı uçtan uca FFmpeg doğrulaması; test_artifacts/ içine yazar."""
from pathlib import Path
import json
import subprocess

from video_engine import probe_video, render, parse_time, VideoError

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "test_artifacts"
ARTIFACTS.mkdir(exist_ok=True)
source = ARTIFACTS / "synthetic_source.mp4"
output = ARTIFACTS / "synthetic_reel.mp4"
frame = ARTIFACTS / "synthetic_frame.png"

subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "8", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-pix_fmt", "yuv420p", str(source)], check=True)

assert parse_time("01:02") == 62
assert parse_time("01:01:02") == 3662
try:
    parse_time("01:99")
except VideoError:
    pass
else:
    raise AssertionError("Geçersiz zaman kabul edildi")

elapsed = render(source, output, 2, 3, "2010'ların Hit Şarkılarını Hatırlatıyoruz",
                 "Gün 12/365", "Pitbull · Test Şarkısı",
                 "fit", False)
probe = probe_video(output)
assert (probe.width, probe.height) == (1080, 1920)
assert probe.has_audio
assert abs(probe.duration - 3) <= 0.25, probe.duration
streams = json.loads(subprocess.run([
    "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt",
    "-of", "json", str(output)], capture_output=True, text=True, check=True).stdout)["streams"]
assert any(s["codec_type"] == "video" and s["codec_name"] == "h264" and
           s["pix_fmt"] == "yuv420p" for s in streams)
assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in streams)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "1", "-i", str(output), "-frames:v", "1", str(frame)], check=True)
print(f"OK: {output}, {probe.duration:.2f} sn, ses={probe.has_audio}, işlem={elapsed:.1f} sn")
print(f"Kare: {frame}")
