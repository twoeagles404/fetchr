"""
Fetchr Post-Download Organizer
──────────────────────────────────────────────────────────────────────────────
Two optional post-processing steps (both enabled per-setting):

  auto_extract:
    Extracts ZIP, TAR, GZ, BZ2, XZ archives into a subfolder named after
    the archive. RAR extraction requires the `rarfile` package + unrar binary.

  organise_by_type:
    Moves completed files into category subfolders inside the save directory:
      Videos/   Images/   Audio/   Documents/   Archives/   Other/

Both steps run after the download is confirmed complete.
──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

# Optional RAR support
try:
    import rarfile
    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False

# ── File type map ─────────────────────────────────────────────────────────────

CATEGORIES: dict[str, list[str]] = {
    "Videos":    ["mp4", "mkv", "webm", "avi", "mov", "flv", "wmv", "m4v", "ts", "mpg", "mpeg", "3gp"],
    "Images":    ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tiff", "tif", "heic", "avif"],
    "Audio":     ["mp3", "m4a", "flac", "wav", "ogg", "opus", "aac", "wma", "alac"],
    "Documents": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "mobi", "md"],
    "Archives":  ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "tgz"],
}

EXTRACTABLE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".tgz"}
if RAR_AVAILABLE:
    EXTRACTABLE_EXTS.add(".rar")


def get_category(filename: str) -> str:
    """Return the category folder name for a given filename."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for category, exts in CATEGORIES.items():
        if ext in exts:
            return category
    return "Other"


def is_extractable(file_path: Path) -> bool:
    """Return True if this file can be extracted."""
    name = file_path.name.lower()
    if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return True
    return file_path.suffix.lower() in EXTRACTABLE_EXTS


# ── Main entry point ──────────────────────────────────────────────────────────

async def process_download(
    file_path: Path,
    auto_extract: bool = False,
    organise: bool = False,
) -> Optional[Path]:
    """
    Run post-download processing on a completed file.
    Returns the final file path (may have moved if organise=True).
    """
    if not file_path.exists():
        return None

    loop = asyncio.get_event_loop()
    current_path = file_path

    if auto_extract and is_extractable(current_path):
        await loop.run_in_executor(None, _extract, current_path)

    if organise:
        new_path = await loop.run_in_executor(None, _organise, current_path)
        if new_path:
            current_path = new_path

    return current_path


# ── Extract ───────────────────────────────────────────────────────────────────

def _extract(file_path: Path) -> None:
    """Extract an archive to a subfolder named after the archive (without extension)."""
    name = file_path.name.lower()

    # Determine output directory (strip all archive extensions from name)
    stem = file_path.stem
    if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")):
        stem = Path(stem).stem   # strip double extension e.g. archive.tar.gz → archive
    out_dir = file_path.parent / stem

    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(file_path, "r") as zf:
                out_dir.mkdir(exist_ok=True)
                zf.extractall(out_dir)
            print(f"📦  Extracted: {file_path.name} → {out_dir.name}/")

        elif (name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"))
              or file_path.suffix in (".gz", ".bz2", ".xz")):
            with tarfile.open(file_path) as tf:
                out_dir.mkdir(exist_ok=True)
                tf.extractall(out_dir)
            print(f"📦  Extracted: {file_path.name} → {out_dir.name}/")

        elif name.endswith(".rar") and RAR_AVAILABLE:
            with rarfile.RarFile(file_path) as rf:
                out_dir.mkdir(exist_ok=True)
                rf.extractall(out_dir)
            print(f"📦  Extracted: {file_path.name} → {out_dir.name}/")

        else:
            print(f"⚠️   Cannot extract: {file_path.name} (unsupported or missing library)")

    except Exception as e:
        print(f"⚠️   Extraction failed [{file_path.name}]: {e}")


# ── Organise ──────────────────────────────────────────────────────────────────

def _organise(file_path: Path) -> Optional[Path]:
    """
    Move file into a category subfolder inside its current directory.
    Returns the new path, or None if the file wasn't moved.
    """
    if not file_path.exists():
        return None

    category = get_category(file_path.name)
    cat_dir  = file_path.parent / category
    cat_dir.mkdir(exist_ok=True)

    dest = cat_dir / file_path.name

    # Avoid overwrite — append _1, _2, ... until name is free
    if dest.exists():
        stem   = file_path.stem
        suffix = file_path.suffix
        i = 1
        while dest.exists():
            dest = cat_dir / f"{stem}_{i}{suffix}"
            i += 1

    try:
        file_path.rename(dest)
        print(f"📁  Organised: {file_path.name} → {category}/{dest.name}")
        return dest
    except Exception as e:
        print(f"⚠️   Organise failed [{file_path.name}]: {e}")
        return None
