import os
import sys
import hashlib
import shutil
import threading
import string
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


# =========================
# CONFIG
# =========================

MIN_FILE_SIZE = 5 * 1024  # 5 KB

exit_event = threading.Event()
hash_lock = threading.Lock()


# =========================
# SAFE EXIT
# =========================

def safe_exit_handler(signum=None, frame=None):
    print("\n[INFO] Safe exit triggered. Finishing current operations...")
    exit_event.set()


import signal
signal.signal(signal.SIGINT, safe_exit_handler)


# =========================
# DOWNLOADS PATH
# =========================

def get_downloads_path():
    home = Path.home()

    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        candidates = [
            Path("/storage/emulated/0/Download"),
            Path("/storage/emulated/0/download"),
            home / "storage" / "downloads",
            home / "storage" / "Download",
        ]

        for path in candidates:
            if path.is_dir():
                return path

    candidates = [
        home / "Downloads",
        home / "Download",
        home / "downloads",
        home / "download",
    ]

    for path in candidates:
        if path.is_dir():
            return path

    fallback = home / "Downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DOWNLOADS_PATH = get_downloads_path()


# =========================
# FILE CATEGORIES
# (unchanged, shortened here for clarity in explanation)
# =========================

FILE_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"},
    "Videos": {".mp4", ".avi", ".mov", ".webm"},
    "Documents": {".pdf", ".doc", ".docx", ".txt"},
    "Recording": { ".wav", ".aac", ".flac"},
    "Movies": { ".mkv", ".3gp", ".m4v"},
    "Music": { ".mp3", ".m4a" },
    "Programs": { ".exe", ".msi", ".apk", ".deb",".rpm", ".pkg" }, 
    "Compressed": { ".zip ", ".rar", ".7z", ".tar", ".gz", ".tgz" },
    "Torrents": { ".torrent"},
    "Subtitles": { ".srt", ".ass", ".ssa", ".sub", ".vtt"}
}

SUPPORTED_EXTENSIONS = {
    ext for exts in FILE_CATEGORIES.values() for ext in exts
}


# =========================
# STORAGE SETUP
# =========================

CATEGORY_PATHS = {}
DUPLICATES_PATH = None


def setup_storage():
    global CATEGORY_PATHS, DUPLICATES_PATH

    for category in FILE_CATEGORIES:
        path = DOWNLOADS_PATH / category
        path.mkdir(parents=True, exist_ok=True)
        CATEGORY_PATHS[category] = path

    DUPLICATES_PATH = DOWNLOADS_PATH / "Duplicates"
    DUPLICATES_PATH.mkdir(parents=True, exist_ok=True)


setup_storage()


# =========================
# HASH
# =========================

def generate_file_hash(file_path, chunk_size=1024 * 1024):
    sha256 = hashlib.sha256()

    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()
    except:
        return None


# =========================
# CATEGORY DETECTION
# =========================

def get_file_category(file_path):
    ext = file_path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return None

    for cat, exts in FILE_CATEGORIES.items():
        if ext in exts:
            return cat

    return None


# =========================
# SAFE MOVE
# =========================

def safe_move(src, dest_folder):
    dest = dest_folder / src.name
    counter = 1

    while dest.exists():
        dest = dest_folder / f"{src.stem}_{counter}{src.suffix}"
        counter += 1

    shutil.move(str(src), str(dest))


# =========================
# DUPLICATES
# =========================

def handle_duplicate(file_path):
    try:
        safe_move(file_path, DUPLICATES_PATH)
        print(f"[DUPLICATE] {file_path.name}")
    except:
        pass


# =========================
# FILE PROCESSOR
# =========================

def process_file(file_path, seen_hashes):
    if exit_event.is_set():
        return "exit"

    try:
        if file_path.stat().st_size < MIN_FILE_SIZE:
            return "skipped"
    except:
        return "skipped"

    category = get_file_category(file_path)
    if not category:
        return "skipped"

    file_hash = generate_file_hash(file_path)
    if not file_hash:
        return "skipped"

    with hash_lock:
        if file_hash in seen_hashes:
            handle_duplicate(file_path)
            return "duplicate"
        seen_hashes.add(file_hash)

    try:
        safe_move(file_path, CATEGORY_PATHS[category])
        print(f"[MOVED] {file_path.name}")
        return "moved"
    except:
        return "error"


# =========================
# ENGINE
# =========================

def process_files():
    print("\n==============================")
    print("   QUICK MODE ORGANIZER")
    print("==============================\n")

    all_files = []

    for f in DOWNLOADS_PATH.glob("*"):
        if f.is_file():
            all_files.append(f)

    if not all_files:
        print("[INFO] No files found in Downloads.")
        return

    seen_hashes = set()

    stats = {"moved": 0, "duplicate": 0, "skipped": 0, "error": 0}

    max_workers = min(32, (os.cpu_count() or 4) * 2)

    print(f"[INFO] Processing {len(all_files)} files...\n")

    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = [executor.submit(process_file, f, seen_hashes) for f in all_files]

    iterator = as_completed(futures)

    if tqdm:
        iterator = tqdm(iterator, total=len(futures), desc="Processing")

    for future in iterator:
        if exit_event.is_set():
            break

        result = future.result()

        if result in stats:
            stats[result] += 1

    executor.shutdown(wait=True)

    print("\n==============================")
    print("SUMMARY")
    print("==============================")

    for k, v in stats.items():
        print(f"{k.capitalize():<12}: {v}")

    print("\n[DONE]")


# =========================
# ENTRY POINT (FOR main.py)
# =========================

def run_moderate_mode():
    process_files()