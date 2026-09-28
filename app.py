"""Yerel nostaljik müzik Reels hazırlayıcısı."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import streamlit as st

from caption_generator import CaptionError, generate_caption
from music_metadata import infer_music
from video_engine import (OUTPUTS, WORK, VideoError, check_space, parse_time,
                          probe_video, render, run_checked, unique_output,
                          validate_youtube_url)

st.set_page_config(page_title="Nostaljik Reels Stüdyosu", page_icon="🎵", layout="centered")
st.markdown("""<style>
.block-container {max-width: 850px; padding-top: 2rem}
h1 {letter-spacing: -.04em}
.stButton button {font-weight: 650}
</style>""", unsafe_allow_html=True)


def executable_version(name: str) -> tuple[int, int] | None:
    path = shutil.which(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout
        match = re.search(r"\b(\d+)\.(\d+)", out)
        return (int(match.group(1)), int(match.group(2))) if match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def environment() -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    yt_issues: list[str] = []
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        fatal.append("FFmpeg/FFprobe eksik: `sudo apt install ffmpeg` çalıştırın.")
    if not Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").is_file():
        fatal.append("Yazı tipi eksik: `sudo apt install fonts-dejavu-core` çalıştırın.")
    deno = executable_version("deno")
    node = executable_version("node")
    if (deno or (0, 0)) < (2, 3) and (node or (0, 0)) < (22, 0):
        yt_issues.append("YouTube için Deno 2.3+ (önerilir) veya Node.js 22+ kurun. "
                         "Deno kurulumu: https://docs.deno.com/runtime/getting_started/installation/")
    if importlib.util.find_spec("yt_dlp_ejs") is None:
        yt_issues.append("YouTube çözücüsü eksik: sanal ortamda `pip install -U 'yt-dlp[default]'` çalıştırın.")
    return fatal, yt_issues


def yt_args() -> list[str]:
    if (executable_version("deno") or (0, 0)) >= (2, 3):
        return []
    if (executable_version("node") or (0, 0)) >= (22, 0):
        return ["--js-runtimes", "node"]
    return []


def display_time(seconds: float) -> str:
    value = int(seconds)
    hours, remainder = divmod(value, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds_left:02d}"
    return f"{minutes:02d}:{seconds_left:02d}"


def clip_controls(key_prefix: str, total_duration: float) -> tuple[float, int] | None:
    start_raw = st.text_input("Video kaçıncı dakikada başlasın?", value="0:00",
                              help="Örnek: 1:10 = 1 dakika 10 saniye. Uzun videolarda 1:02:30 da yazabilirsiniz.",
                              key=f"{key_prefix}_start")
    length_choice = st.radio("Klip uzunluğu", ["20 sn", "25 sn", "30 sn", "Özel"],
                             index=1, horizontal=True, key=f"{key_prefix}_length")
    duration = (st.number_input("Özel uzunluk (saniye)", min_value=1, value=25, step=1,
                                key=f"{key_prefix}_custom_length")
                if length_choice == "Özel" else int(length_choice.split()[0]))
    try:
        start = parse_time(start_raw)
        if start + duration > total_duration + 0.05:
            raise VideoError(f"Kesit {display_time(start + duration)} noktasında bitiyor; "
                             f"video {display_time(total_duration)} uzunluğunda.")
        st.caption(f"Seçilen bölüm: {display_time(start)} → "
                   f"{display_time(start + duration)} · {duration} saniye")
        return start, duration
    except VideoError as exc:
        st.error(str(exc))
        return None


def local_gemini_key() -> str:
    """Use the process environment, then the local .env file."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(__file__).resolve().parent / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "GEMINI_API_KEY":
                value = value.strip()
                return json.loads(value) if value.startswith('"') else value
    except (OSError, ValueError):
        return ""
    return ""


@st.cache_resource
def clean_old_work() -> bool:
    """Only app-managed staging files older than seven days; outputs are untouched."""
    cutoff = time.time() - 7 * 24 * 3600
    WORK.mkdir(exist_ok=True)
    for path in WORK.iterdir():
        if not path.name.startswith(("upload-", "youtube-", "preview-", "render-")):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            pass
    return True


def video_info(url: str) -> dict:
    result = run_checked([sys.executable, "-m", "yt_dlp", "--no-playlist",
                          "--skip-download", "--dump-single-json", *yt_args(), url],
                         "YouTube bilgi alma", timeout=90)
    try:
        info = json.loads(result.stdout)
        artist, song = infer_music(info)
        raw_duration = info.get("duration")
        duration = float(raw_duration) if isinstance(raw_duration, (int, float)) else None
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            duration = None
        return {"title": info.get("title") or "Başlıksız", "duration": duration,
                "thumbnail": info.get("thumbnail"), "artist": artist, "song": song}
    except json.JSONDecodeError as exc:
        raise VideoError("YouTube bilgi alma: geçersiz yanıt alındı.") from exc


def download_section(url: str, start: float, duration: int, total_duration: float) -> tuple[Path, float]:
    """Download a padded range and return (file, local FFmpeg start)."""
    padding = 3.0
    window_start = max(0.0, start - padding)
    window_end = min(total_duration, start + duration + padding)
    expected_length = window_end - window_start
    target_dir = WORK / f"youtube-{uuid.uuid4().hex}"
    check_space(WORK, 500_000_000)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        # HLS bölüm indirmeleri bazı videolarda yalnızca ses üretebiliyor.
        # Önce doğrudan HTTPS H.264/AAC akışlarını seç; gerekirse başka biçimi dene.
        direct_format = (
            "bv*[protocol=https][height<=1080][vcodec^=avc1]+ba[protocol=https][ext=m4a]/"
            "bv*[protocol=https][height<=1080]+ba[protocol=https]/"
            "b[protocol=https][height<=1080]"
        )
        attempts = [
            (direct_format, []),
            # Tek dosyada video+ses; ayrı ses akışına gelen 403'ü aşabilir.
            ("b[protocol=https][height<=1080]", ["--extractor-args", "youtube:player_client=android"]),
            ("bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b", []),
        ]
        failures: list[str] = []
        attempt = 0
        while attempt < len(attempts):
            format_selector, extra_args = attempts[attempt]
            attempt += 1
            attempt_dir = target_dir / f"attempt-{attempt}"
            attempt_dir.mkdir()
            try:
                run_checked([sys.executable, "-m", "yt_dlp", "--no-playlist", *yt_args(), *extra_args,
                             "-f", format_selector, "--merge-output-format", "mp4",
                             "--download-sections", f"*{window_start:.3f}-{window_end:.3f}",
                             "--force-keyframes-at-cuts",
                             "-o", str(attempt_dir / "source.%(ext)s"), url],
                            "YouTube kesit indirme", timeout=1800)
                files = [p for p in attempt_dir.iterdir()
                         if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm"}]
                if len(files) != 1:
                    raise VideoError("tek bir oynatılabilir video dosyası oluşmadı")
                try:
                    actual_length = probe_video(files[0]).duration
                except VideoError as exc:
                    raise VideoError(f"indirilen bölümde geçerli görüntü yok ({exc})") from exc
                if abs(actual_length - expected_length) > 0.75:
                    raise VideoError(f"beklenen yaklaşık {expected_length:.1f} sn, "
                                     f"gelen dosya {actual_length:.1f} sn")
                return files[0], start - window_start
            except VideoError as exc:
                detail = str(exc)
                if "403 Forbidden" in detail or "HTTP Error 403" in detail:
                    failures.append("YouTube medya sunucusu 403 erişim hatası döndürdü")
                    if attempt == 1:
                        # yt-dlp her çağrıda yeni bir medya bağlantısı alır.
                        attempts.insert(1, (direct_format, []))
                        time.sleep(2)
                elif "geçerli görüntü yok" in detail:
                    failures.append("indirilen kesitte görüntü akışı bulunamadı")
                elif "Requested format is not available" in detail:
                    failures.append("bu videoda seçilen akış biçimi bulunamadı")
                elif "429" in detail:
                    failures.append("YouTube çok fazla istek hatası döndürdü (429)")
                else:
                    failures.append("yt-dlp/FFmpeg bölüm indirme hatası")
                shutil.rmtree(attempt_dir, ignore_errors=True)
        raise VideoError("YouTube kesiti doğrulanmış bir video dosyası olarak alınamadı. "
                         f"Denemeler: {'; '.join(failures)}. "
                         "Biraz sonra yeniden deneyin veya yerel video dosyası kullanın.")
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def source_path(kind: str) -> Path | None:
    if kind == "YouTube URL'si":
        url = st.text_input("YouTube video URL'si", placeholder="https://www.youtube.com/watch?v=...")
        if st.button("Video bilgisini getir", disabled=not url):
            try:
                clean = validate_youtube_url(url)
                with st.spinner("Video bilgisi alınıyor..."):
                    st.session_state.yt_info = video_info(clean)
                    st.session_state.yt_url = clean
                    st.session_state.source = None
                    st.session_state.yt_ready = None
                    st.session_state.artist_input = st.session_state.yt_info["artist"]
                    st.session_state.song_input = st.session_state.yt_info["song"]
            except VideoError as exc:
                st.error(str(exc))
        info = st.session_state.get("yt_info")
        if info and st.session_state.get("yt_url") == url.strip():
            st.subheader(info["title"])
            if info["artist"] or info["song"]:
                st.caption(f"Algılanan müzik: {info['artist'] or 'Sanatçı belirsiz'} · {info['song'] or 'Şarkı belirsiz'}")
            if info["duration"]:
                st.write(f"Video süresi: {display_time(info['duration'])}")
            if info["thumbnail"]:
                st.image(info["thumbnail"], width=320)
            st.link_button("Videoyu YouTube'da açıp zamanı bul", url.strip())
            st.caption("Videodaki zamanı YouTube'da bulun, ardından başlangıcı aşağıya yazın.")
            if not info["duration"]:
                st.error("Video süresi bilinmiyor; bölüm indirme için süre bilgisi gerekli.")
                return None
            st.subheader("İndirilecek kesit")
            selection = clip_controls("youtube", info["duration"])
            if st.button("Yalnızca seçilen kesiti indir", type="primary", disabled=selection is None):
                try:
                    assert selection is not None
                    st.session_state.yt_ready = None
                    with st.spinner("Seçilen bölüm indiriliyor; bağlantıya göre biraz sürebilir..."):
                        section_path, local_start = download_section(
                            url.strip(), selection[0], selection[1], info["duration"])
                        st.session_state.yt_ready = {
                            "url": url.strip(), "selection": selection, "path": section_path,
                            "local_start": local_start,
                        }
                except VideoError as exc:
                    st.error(str(exc))
                    st.info("Dosya yükleme veya yerel dosya yolu sekmesine geçerek devam edebilirsiniz.")
            ready = st.session_state.get("yt_ready")
            if ready and ready["url"] == url.strip() and ready["selection"] == selection:
                return ready["path"]
    elif kind == "Dosya yükle":
        uploaded = st.file_uploader("Video dosyası", type=["mp4", "mov", "mkv", "webm", "avi"])
        if uploaded and st.button("Yüklenen dosyayı kullan"):
            dest = None
            try:
                check_space(WORK, uploaded.size + 500_000_000)
                dest = WORK / f"upload-{uuid.uuid4().hex}{Path(uploaded.name).suffix.lower()}"
                with dest.open("wb") as out:
                    shutil.copyfileobj(uploaded, out)
                probe_video(dest)
                st.session_state.source = dest
                st.session_state.source_kind = kind
                st.session_state.artist_input = ""
                st.session_state.song_input = ""
            except VideoError as exc:
                if dest is not None:
                    dest.unlink(missing_ok=True)
                st.error(str(exc))
        if st.session_state.get("source_kind") == kind:
            return st.session_state.get("source")
    else:
        raw = st.text_input("Yerel video dosyasının mutlak yolu", placeholder="/home/kullanici/Videolar/klip.mp4")
        if st.button("Yerel dosyayı kullan", disabled=not raw):
            path = Path(raw).expanduser()
            try:
                if not path.is_absolute() or not path.is_file():
                    raise VideoError("Mutlak yol girin; yol mevcut bir dosyayı göstermeli.")
                probe_video(path)
                st.session_state.source = path
                st.session_state.source_kind = kind
                st.session_state.artist_input = ""
                st.session_state.song_input = ""
            except VideoError as exc:
                st.error(str(exc))
        if st.session_state.get("source_kind") == kind:
            return st.session_state.get("source")
    return None


def main() -> None:
    WORK.mkdir(exist_ok=True)
    clean_old_work()
    OUTPUTS.mkdir(exist_ok=True)
    st.title("Nostaljik Reels Stüdyosu")
    st.caption("Şarkını ve kesitini seç; dikey videoyu yerel olarak hazırla.")
    fatal, yt_issues = environment()
    for issue in fatal:
        st.error(issue)
    for issue in yt_issues:
        st.info(issue)
    if fatal:
        st.stop()

    st.header("1 · Kaynak video")
    choices = ["YouTube URL'si", "Dosya yükle"]
    # Bu ayar yalnızca yerel bağlamda çalıştırılan uygulamada etkin olur.
    current_url = st.context.url or ""
    if st.get_option("server.address") == "127.0.0.1" and (
        not current_url or current_url.startswith(("http://127.0.0.1:", "https://127.0.0.1:"))
    ):
        choices.append("Yerel dosya yolu")
    kind = st.radio("Kaynak türü", choices, horizontal=True)
    path = source_path(kind)
    if not path:
        st.stop()
    try:
        info = probe_video(path)
    except VideoError as exc:
        st.error(str(exc))
        st.stop()
    if kind == "YouTube URL'si":
        st.success(f"Kısa kaynak bölüm hazır · {info.duration:.1f} sn · {info.width}×{info.height}")
        st.caption("Bu oynatıcıda kesin kesimin öncesi ve sonrasından birkaç saniye de bulunur.")
    else:
        st.success(f"Kaynak hazır · {info.duration:.1f} sn · {info.width}×{info.height}")
    st.video(str(path))
    if not info.has_audio:
        st.warning("Bu kaynakta ses akışı bulunamadı.")
    st.header("2 · Kesit ve yazılar")
    if kind == "YouTube URL'si":
        ready = st.session_state.yt_ready
        start, duration = ready["local_start"], ready["selection"][1]
        st.caption(f"YouTube kesiti: {display_time(ready['selection'][0])} → "
                   f"{display_time(ready['selection'][0] + duration)}. "
                   "Değiştirmek için yukarıdan yeni kesit seçip indirin.")
    else:
        selection = clip_controls("local", info.duration)
        if selection is None:
            st.stop()
        start, duration = selection
    artist = st.text_input("Sanatçı", key="artist_input")
    song = st.text_input("Şarkı adı", key="song_input")
    top = st.text_area("Üst metin", value="2010'ların hit şarkılarını hatırlıyoruz", height=100)
    day_col, total_col = st.columns(2)
    with day_col:
        day = st.number_input("Gün numarası", min_value=1, value=1, step=1)
    with total_col:
        total = st.number_input("Toplam gün", min_value=1, value=365, step=1)
    bottom = f"Gün {day}/{total}"
    st.caption(f"Alt sayaç: {bottom}")
    show_song = st.checkbox("Şarkı adı ve sanatçıyı videoda göster", value=False)
    mode_label = st.radio("Klip yerleşimi", ["Oranı koruyarak sığdır", "Alanı doldur ve kırp"], horizontal=True)
    with st.expander("Türkçe video açıklaması oluştur (isteğe bağlı)"):
        st.caption("35–55 kelimelik açıklama hazırlanır. Önce Gemini 2.5 Flash denenir; hesapta erişim yoksa Flash-Lite kullanılır. Yalnızca sanatçı ve şarkı adı gönderilir; metin videonun içine yazılmaz.")
        saved_api_key = local_gemini_key()
        if saved_api_key:
            st.caption("Gemini API anahtarı yerel ayarlardan yüklendi.")
            api_key = saved_api_key
        else:
            api_key = st.text_input("Gemini API anahtarı", type="password")
        caption_subject = (artist.strip(), song.strip())
        if st.session_state.get("caption_subject") != caption_subject:
            st.session_state.caption_subject = caption_subject
            st.session_state.caption_draft = ""
        if st.button("Türkçe açıklama üret", disabled=not (api_key and artist.strip() and song.strip())):
            try:
                with st.spinner("Açıklama hazırlanıyor..."):
                    st.session_state.caption_draft = generate_caption(api_key, artist, song)
            except CaptionError as exc:
                st.error(str(exc))
        st.text_area("Açıklama (düzenleyip kopyalayabilirsiniz)", key="caption_draft", height=110)
    allow_silent = st.checkbox("Ses yoksa sessiz video üret", value=False) if not info.has_audio else False
    mode = "fit" if mode_label.startswith("Oranı") else "fill"
    song_line = f"{artist} · {song}" if show_song and artist.strip() and song.strip() else ""

    try:
        if start + duration > info.duration + 0.05:
            raise VideoError(f"Kesit {start + duration:.1f}. saniyede bitiyor; video {info.duration:.1f} saniye.")
        if not info.has_audio and not allow_silent:
            raise VideoError("Sessiz kaynak için açık sessiz video seçimini işaretleyin.")
    except VideoError as exc:
        st.error(str(exc))
        st.stop()

    signature = (str(path), path.stat().st_mtime_ns, start, duration, artist, song,
                 day, total, top, bottom, song_line, mode, allow_silent)
    st.header("3 · Önizle ve üret")
    if st.button("Kısa önizleme oluştur"):
        preview_path = WORK / f"preview-{uuid.uuid4().hex}.mp4"
        bar = st.progress(0, text="Önizleme hazırlanıyor")
        try:
            elapsed = render(path, preview_path, start, min(duration, 4.0), top, bottom,
                             song_line, mode, True, allow_silent,
                             lambda p: bar.progress(p, text=f"Önizleme %{p * 100:.0f}"))
            st.session_state.preview = (signature, preview_path)
            st.success(f"Önizleme hazır · {elapsed:.1f} sn")
        except VideoError as exc:
            st.error(str(exc))
    preview = st.session_state.get("preview")
    if preview and preview[0] == signature and preview[1].is_file():
        st.video(str(preview[1]))
        st.caption("Önizleme ilk 4 saniyeyi ve düşük çözünürlüğü kullanır.")
    if st.button("Nihai videoyu üret", type="primary", disabled=not (preview and preview[0] == signature)):
        bar = st.progress(0, text="Nihai video hazırlanıyor")
        dest = None
        try:
            dest = unique_output(artist, song, int(day))
            elapsed = render(path, dest, start, duration, top, bottom, song_line,
                             mode, False, allow_silent,
                             lambda p: bar.progress(p, text=f"Kodlama %{p * 100:.0f}"))
            st.session_state.final = (signature, dest)
            st.success(f"Video hazır · {elapsed:.1f} sn · {dest}")
        except VideoError as exc:
            if dest is not None:
                dest.unlink(missing_ok=True)
            st.error(str(exc))
    final = st.session_state.get("final")
    if final and final[0] == signature and final[1].is_file():
        st.video(str(final[1]))
        with final[1].open("rb") as file:
            st.download_button("MP4 indir", data=file, file_name=final[1].name,
                               mime="video/mp4")
        st.caption(f"Kalıcı çıktı: {final[1]}")


if __name__ == "__main__":
    main()
