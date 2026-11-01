"""Find the picture tools: the owner's fieldkit.local.json, then PATH. Nothing is guessed or installed."""
import shutil
import subprocess
import sys

from ..core import settings

#: name -> (what it is for, version argv tail, source, {platform: install command})
SPECS = {
    "magick": ("measure (SSIM, PSNR), resize, fallback encoder", ["-version"], "https://imagemagick.org",
               {"windows": "scoop install imagemagick", "linux": "micromamba install -y -p ~/.local -c conda-forge imagemagick",
                "darwin": "brew install imagemagick"}),
    "tesseract": ("read the text in a picture", ["--version"], "https://github.com/tesseract-ocr/tesseract",
                  {"windows": "scoop install tesseract", "linux": "micromamba install -y -p ~/.local -c conda-forge tesseract",
                   "darwin": "brew install tesseract"}),
    "cwebp": ("WebP encoder (libwebp)", ["-version"], "https://developers.google.com/speed/webp/download",
              {"windows": "scoop install libwebp", "linux": "sudo apt install webp", "darwin": "brew install webp"}),
    "avifenc": ("AVIF encoder (libavif)", ["--version"], "https://github.com/AOMediaCodec/libavif",
                {"windows": "scoop install libavif", "linux": "sudo apt install libavif-bin",
                 "darwin": "brew install libavif"}),
    "cjpeg": ("JPEG encoder (mozjpeg)", ["-version"], "https://github.com/mozilla/mozjpeg",
              {"windows": "scoop install mozjpeg", "linux": "sudo apt install mozjpeg (or build mozjpeg)",
               "darwin": "brew install mozjpeg"}),
    "oxipng": ("PNG optimiser", ["--version"], "https://github.com/shssoichiro/oxipng",
               {"windows": "scoop install oxipng", "linux": "cargo install oxipng", "darwin": "brew install oxipng"}),
}


def platform_key(platform=None):
    p = platform or sys.platform
    return "windows" if p.startswith("win") else "darwin" if p == "darwin" else "linux"


def find(name, local=None, which=shutil.which):
    """-> the path of tool NAME, or None. The configured path wins, but only if it names a program that is there."""
    local = settings.local_settings() if local is None else local
    configured = ((local.get("image") or {}).get("tools") or {}).get(name)
    if configured:
        return which(configured)            # a configured path that is not a program is 'absent', not a PATH guess
    return which(name)


def version(path, name, run=subprocess.run):
    try:
        r = run([path, *SPECS[name][1]], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0][:120] if out else ""


def report(local=None, which=shutil.which, run=subprocess.run, platform=None):
    pk = platform_key(platform)
    rows = []
    for name, (what, _, source, install) in SPECS.items():
        path = find(name, local, which)
        rows.append({"tool": name, "for": what, "present": bool(path), "path": path or "",
                     "version": version(path, name, run) if path else "", "source": source,
                     "install": "" if path else install.get(pk, "")})
    return rows
