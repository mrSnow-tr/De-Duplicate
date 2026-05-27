import os
import hashlib
import shutil
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# OPTIONAL PROGRESS BAR
# =========================

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


# =========================
# CROSS-PLATFORM DOWNLOADS PATH
# =========================

def get_downloads_path():
    home = Path.home()

    # Android / Termux detection
    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        android_path = Path("/storage/emulated/0/Download")
        if android_path.exists():
            return android_path

        return home / "storage" / "downloads"

    # Windows / Linux / macOS
    return home / "Downloads"


DOWNLOADS_PATH = get_downloads_path()

# =========================
# FOLDERS
# =========================

IMAGE_FOLDER = "Images"
VIDEO_FOLDER = "Videos"
DUPLICATE_FOLDER = "Duplicates"

IMAGES_PATH = DOWNLOADS_PATH / IMAGE_FOLDER
VIDEOS_PATH = DOWNLOADS_PATH / VIDEO_FOLDER
DUPLICATES_PATH = DOWNLOADS_PATH / DUPLICATE_FOLDER

IMAGES_PATH.mkdir(parents=True, exist_ok=True)
VIDEOS_PATH.mkdir(parents=True, exist_ok=True)
DUPLICATES_PATH.mkdir(parents=True, exist_ok=True)


# =========================
# SUPPORTED FILE TYPES
# =========================

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".bmp", ".webp", ".tiff", ".heic", ".dng"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov",
    ".wmv", ".flv", ".webm", ".m4v",".3gp"
}


# =========================
# SAFE EXIT HANDLING
# =========================

exit_event = threading.Event()


def safe_exit_handler(signum=None, frame=None):
    print("\n[INFO] Safe exit triggered. Finishing current operations...")
    exit_event.set()


import signal
signal.signal(signal.SIGINT, safe_exit_handler)


# =========================
# HASH GENERATION (OPTIMIZED)
# =========================

def generate_file_hash(file_path, chunk_size=1024 * 1024):
    sha256 = hashlib.sha256()

    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()

    except Exception as e:
        print(f"[ERROR] Hash failed: {file_path} -> {e}")
        return None


# =========================
# FILE CATEGORY DETECTION
# =========================

def get_file_category(file_path):
    ext = file_path.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


# =========================
# SAFE MOVE (NO OVERWRITE)
# =========================

def safe_move(src, dest_folder):
    dest = dest_folder / src.name
    counter = 1

    while dest.exists():
        dest = dest_folder / f"{src.stem}_{counter}{src.suffix}"
        counter += 1

    shutil.move(str(src), str(dest))


# =========================
# DUPLICATE HANDLER (SAFE)
# =========================

def handle_duplicate(file_path):
    try:
        safe_move(file_path, DUPLICATES_PATH)
        print(f"[DUPLICATE → MOVED] {file_path.name}")
    except Exception as e:
        print(f"[ERROR] Duplicate move failed: {file_path} -> {e}")


# =========================
# FILE PROCESSING TASK
# =========================

def process_file(file_path, seen_hashes):
    if exit_event.is_set():
        return "exit"

    category = get_file_category(file_path)
    if not category:
        return "skipped"

    file_hash = generate_file_hash(file_path)
    if not file_hash:
        return "skipped"

    if file_hash in seen_hashes:
        handle_duplicate(file_path)
        return "duplicate"

    seen_hashes.add(file_hash)

    try:
        if category == "image":
            safe_move(file_path, IMAGES_PATH)
        elif category == "video":
            safe_move(file_path, VIDEOS_PATH)

        print(f"[MOVED] {file_path.name}")
        return "moved"

    except Exception as e:
        print(f"[ERROR] Move failed: {file_path} -> {e}")
        return "error"


# =========================
# MAIN ENGINE
# =========================

def process_files():
    print("\n==============================")
    print("   PRODUCTION DEDUP SCRIPT")
    print("==============================\n")

    if not DOWNLOADS_PATH.exists():
        print(f"[ERROR] Downloads folder not found: {DOWNLOADS_PATH}")
        return

    all_files = [
        f for f in DOWNLOADS_PATH.rglob("*")
        if f.is_file()
        and IMAGES_PATH not in f.parents
        and VIDEOS_PATH not in f.parents
        and DUPLICATES_PATH not in f.parents
    ]

    if not all_files:
        print("[INFO] No files found.")
        return

    seen_hashes = set()

    stats = {
        "moved": 0,
        "duplicate": 0,
        "skipped": 0,
        "error": 0
    }

    max_workers = min(8, os.cpu_count() or 4)

    print(f"[INFO] Processing {len(all_files)} files using {max_workers} threads...\n")

    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = []

    for file in all_files:
        futures.append(executor.submit(process_file, file, seen_hashes))

    iterator = as_completed(futures)

    if tqdm:
        iterator = tqdm(iterator, total=len(futures), desc="Processing")

    try:
        for future in iterator:
            if exit_event.is_set():
                break

            result = future.result()

            if result in stats:
                stats[result] += 1

    except KeyboardInterrupt:
        safe_exit_handler()

    finally:
        executor.shutdown(wait=False)

    # =========================
    # SUMMARY
    # =========================

    print("\n==============================")
    print("          SUMMARY")
    print("==============================")

    print(f"Moved files     : {stats['moved']}")
    print(f"Duplicates      : {stats['duplicate']}")
    print(f"Skipped         : {stats['skipped']}")
    print(f"Errors          : {stats['error']}")

    print("\n[DONE] Process completed safely.")


# =========================
# ENTRY POINT
# =========================

if __name__ == "__main__":
    process_files()