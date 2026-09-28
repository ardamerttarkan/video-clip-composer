import unittest

from music_metadata import infer_music


class MusicMetadataTests(unittest.TestCase):
    def test_prefers_explicit_music_fields(self):
        self.assertEqual(infer_music({
            "title": "Some channel title",
            "artist": "Kesha",
            "track": "Tik Tok",
        }), ("Kesha", "Tik Tok"))

    def test_reads_conventional_video_title(self):
        self.assertEqual(infer_music({
            "title": "Pitbull - Rain Over Me ft. Marc Anthony (Official Video)",
        }), ("Pitbull ft. Marc Anthony", "Rain Over Me"))

    def test_keeps_song_parentheses_and_uses_topic_artist(self):
        self.assertEqual(infer_music({
            "title": "Tonight (I'm Lovin' You) [Official Audio]",
            "uploader": "Enrique Iglesias - Topic",
        }), ("Enrique Iglesias", "Tonight (I'm Lovin' You)"))


if __name__ == "__main__":
    unittest.main()
