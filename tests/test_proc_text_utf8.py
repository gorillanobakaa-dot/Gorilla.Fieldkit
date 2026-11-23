"""fkproc text mode decodes UTF-8 with replacement: byte 0x81 (undefined in cp1252) must not crash."""
import sys

from fieldkit import proc as fkproc


def test_text_mode_survives_byte_0x81():
    code = "import sys; sys.stdout.buffer.write(b'ok \\x81 \\xc3\\xa9 end')"
    r = fkproc.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.startswith("ok ") and r.stdout.endswith(" end")
    assert "\u00e9" in r.stdout


def test_caller_encoding_wins():
    kw = fkproc._text({"text": True, "encoding": "latin-1"})
    assert kw["encoding"] == "latin-1" and "errors" not in kw
    assert fkproc._text({"capture_output": True}) == {"capture_output": True}
    assert fkproc._text({"text": True, "errors": "strict"})["errors"] == "strict"
