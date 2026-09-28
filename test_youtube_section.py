import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from video_engine import Probe, VideoError


class YouTubeSectionTests(unittest.TestCase):
    def test_403_retries_and_uses_single_file_fallback(self):
        formats = []

        def fake_download(args, *_args, **_kwargs):
            selector = args[args.index("-f") + 1]
            formats.append(selector)
            if len(formats) < 3:
                raise VideoError("HTTP error 403 Forbidden")
            target = Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4"))
            target.write_bytes(b"sample")

        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(app, "WORK", Path(directory)),
                  patch.object(app, "run_checked", side_effect=fake_download),
                  patch.object(app, "probe_video", return_value=Probe(36, 640, 360, True)),
                  patch.object(app.time, "sleep")):
                path, local_start = app.download_section("https://www.youtube.com/watch?v=test", 44, 30, 246)
                self.assertEqual(local_start, 3)
                self.assertEqual(len(formats), 3)
                self.assertEqual(formats[0], formats[1])
                self.assertEqual(formats[2], "b[protocol=https][height<=1080]")
                self.assertTrue(path.is_file())
                self.assertFalse((path.parent.parent / "attempt-1").exists())
                self.assertFalse((path.parent.parent / "attempt-2").exists())


if __name__ == "__main__":
    unittest.main()
