"""
File-type classification tests — the gate the Library and /upload routes use
to decide what's a map vs a video vs unsupported.  Audio is intentionally
absent (the table delegates sound to the music-output client).
"""

from media import get_file_type


def test_image_extensions():
    assert get_file_type("map.png") == "image"
    assert get_file_type("MAP.JPG") == "image"      # case-insensitive
    assert get_file_type("token.webp") == "image"


def test_video_extensions():
    assert get_file_type("clip.mp4") == "video"
    assert get_file_type("loop.MKV") == "video"


def test_unsupported_and_edge_cases():
    assert get_file_type("song.mp3") is None        # audio not handled here
    assert get_file_type("noext") is None
    assert get_file_type("archive.tar.gz") is None  # 'gz' is not a media type
    assert get_file_type(".hidden") is None
