"""Tests for bot/fileconvert.py.

The pure-logic tests (parsing, categories, cross-family rejection) need no
third-party libraries. The real-conversion tests are guarded with
pytest.importorskip so they exercise the actual engine when the optional deps
are installed (as they are in CI via requirements.txt) and skip otherwise.
"""

import os
import wave

import pytest

from bot import fileconvert as fc


# ── pure logic (no optional deps needed) ────────────────────────────────────


def test_parse_target_format_variants():
    assert fc.parse_target_format("mp3") == "mp3"
    assert fc.parse_target_format(".PNG") == "png"
    assert fc.parse_target_format("to pdf") == "pdf"
    assert fc.parse_target_format("convert to jpg please") == "jpg"
    assert fc.parse_target_format("/convertfile webp") == "webp"
    assert fc.parse_target_format("") is None
    assert fc.parse_target_format(None) is None


def test_category_of():
    assert fc.category_of("png") == "image"
    assert fc.category_of("JPG") == "image"
    assert fc.category_of("mp4") == "media"
    assert fc.category_of("mp3") == "media"
    assert fc.category_of("pdf") == "doc"
    assert fc.category_of("docx") == "doc"
    assert fc.category_of("exe") is None


def test_normalize_ext():
    assert fc.normalize_ext("clip.MP4") == "mp4"
    assert fc.normalize_ext(".Pdf") == "pdf"
    assert fc.normalize_ext("mp3") == "mp3"
    assert fc.normalize_ext(None) == ""


def test_convert_rejects_cross_family(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"not really a png")
    with pytest.raises(fc.ConversionError):
        fc.convert(str(src), "png", "mp3")


def test_convert_rejects_unsupported_target(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    with pytest.raises(fc.ConversionError):
        fc.convert(str(src), "png", "exe")


def test_convert_rejects_unreadable_source(tmp_path):
    src = tmp_path / "a.xyz"
    src.write_bytes(b"x")
    with pytest.raises(fc.ConversionError):
        fc.convert(str(src), "xyz", "png")


def test_convert_rejects_same_format(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    with pytest.raises(fc.ConversionError):
        fc.convert(str(src), "png", "png")


# ── real conversions (skip if the optional lib is missing) ──────────────────


def test_image_png_to_jpg(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(str(src))
    out = fc.convert(str(src), "png", "jpg")
    assert out.endswith(".jpg") and os.path.exists(out)
    with open(out, "rb") as f:
        assert f.read(3) == b"\xff\xd8\xff"  # JPEG magic bytes


def test_txt_to_pdf(tmp_path):
    pytest.importorskip("fpdf")
    src = tmp_path / "n.txt"
    src.write_text("Hello PDF\nSecond line", encoding="utf-8")
    out = fc.convert(str(src), "txt", "pdf")
    with open(out, "rb") as f:
        assert f.read(4) == b"%PDF"


def test_txt_to_docx_and_back(tmp_path):
    pytest.importorskip("docx")
    src = tmp_path / "n.txt"
    src.write_text("Line A\nLine B", encoding="utf-8")
    docx_out = fc.convert(str(src), "txt", "docx")
    assert os.path.exists(docx_out)
    txt_out = fc.convert(docx_out, "docx", "txt")
    assert "Line A" in open(txt_out, encoding="utf-8").read()


def test_pdf_to_txt_roundtrip(tmp_path):
    pytest.importorskip("fpdf")
    pytest.importorskip("pypdf")
    src = tmp_path / "s.txt"
    src.write_text("Extract me from a pdf", encoding="utf-8")
    pdf = fc.convert(str(src), "txt", "pdf")
    txt = fc.convert(pdf, "pdf", "txt")
    assert "Extract" in open(txt, encoding="utf-8").read()


def test_media_wav_to_mp3(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    wav = tmp_path / "t.wav"
    with wave.open(str(wav), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)  # 0.1s of silence
    out = fc.convert(str(wav), "wav", "mp3")
    assert os.path.exists(out) and os.path.getsize(out) > 0
