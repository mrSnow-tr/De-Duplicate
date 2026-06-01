import os
import hashlib
import shutil
import platform
import threading
import time
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
# SPEED CONFIG
# =========================

MAX_WORKERS = min(32, (os.cpu_count() or 4) * 2)
MIN_FILE_SIZE = 5 * 1024


# =========================
# FILE CATEGORIES
# =========================
# (unchanged - your full list kept)
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

SUPPORTED_EXTENSIONS = {ext for exts in FILE_CATEGORIES.values() for ext in exts}


# =========================
# SYSTEM DETECTION
# =========================

def is_android():
    return "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ


# =========================
# SCAN ROOTS
# =========================

def get_scan_roots():
    if is_android():
        return [Path("/storage/emulated/0")]

    system = platform.system()
    roots = []

    if system == "Windows":
        for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{d}:\\")
            if drive.exists():
                roots.append(drive)
        roots.append(Path.home() / "Downloads")

    elif system == "Darwin":
        volumes = Path("/Volumes")
        if volumes.exists():
            roots += [p for p in volumes.iterdir() if p.is_dir()]
        roots.append(Path.home() / "Downloads")

    else:
        roots.append(Path.home() / "Downloads")
        roots += [p for p in Path("/mnt").glob("*")]

    return roots


# =========================
# DESTINATION DRIVE
# =========================

def get_best_drive():
    if is_android():
        return Path("/storage/emulated/0")

    if platform.system() == "Windows":
        best = Path("C:\\")
        max_free = 0

        for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{d}:\\"
            if os.path.exists(drive):
                free = shutil.disk_usage(drive).free
                if free > max_free:
                    max_free = free
                    best = Path(drive)

        return best

    return Path.home()


# =========================
# FOLDERS
# =========================

def build_folders():
    root = get_best_drive()

    dup = root / "Duplicates"
    corrupt = root / "Corrupted"

    dup.mkdir(exist_ok=True)
    corrupt.mkdir(exist_ok=True)

    print(f"\n[INFO] Duplicates: {dup}")
    print(f"[INFO] Corrupted : {corrupt}\n")

    return dup, corrupt


# =========================
# FILTERS
# =========================

def is_hidden(path):
    return path.name.startswith(".")


def is_valid_file(path):
    return (
        path.is_file()
        and not is_hidden(path)
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


# =========================
# HASHING
# =========================

def file_hash(path):
    sha = hashlib.sha256()

    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha.update(chunk)
        return sha.hexdigest()

    except:
        return None


# =========================
# CORRUPTION CHECK
# =========================

def is_corrupted(path):
    try:
        if path.stat().st_size == 0:
            return True

        with open(path, "rb") as f:
            f.read(1024)
        return False

    except:
        return True


# =========================
# SMART PATH SCORE (IMPROVED)
# =========================

def path_score(path: Path) -> int:
    p = str(path).lower()
    score = 0

    # ORIGINAL SOURCE INDICATORS
    if any(x in p for x in ["dcim", "camera", "images", "pictures"]):
        score += 120

    if "downloads" in p:
        score += 60

    if any(x in p for x in ["messenger", "whatsapp", "telegram", "cache", "temp"]):
        score -= 80

    # depth rule (shallower = more original)
    depth = len(path.parts)
    score += max(0, 50 - depth)

    # file age (OLDER = likely original)
    try:
        age = time.time() - path.stat().st_mtime
        score += min(50, age / 86400)  # days bonus
    except:
        pass

    return score


# =========================
# SAFE MOVE
# =========================

def safe_move(src, folder):
    dest = folder / src.name
    i = 1

    while dest.exists():
        dest = folder / f"{src.stem}_{i}{src.suffix}"
        i += 1

    shutil.move(str(src), str(dest))


# =========================
# CLEAN EMPTY FOLDERS
# =========================

def clean_empty_dirs(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_dir():
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except:
                pass


# =========================
# COLLECT FILES
# =========================

def collect_files():
    files = []

    for root in get_scan_roots():
        if not root.exists():
            continue

        for cur, _, names in os.walk(root):
            if is_android() and "Android" in cur:
                continue

            for n in names:
                p = Path(cur) / n

                try:
                    if p.stat().st_size < MIN_FILE_SIZE:
                        continue
                except:
                    continue

                if is_valid_file(p):
                    files.append(p)

    return files


# =========================
# CORE LOGIC (GROUP + BEST KEEP)
# =========================

def process_file(path, lock, dup_folder, corrupt_folder, db):
    if is_corrupted(path):
        safe_move(path, corrupt_folder)
        return "corrupt"

    digest = file_hash(path)
    if not digest:
        return "skip"

    score = path_score(path)

    with lock:

        if digest not in db:
            db[digest] = path
            return "unique"

        existing = db[digest]

        # 4+ duplicates handled naturally via grouping in dict
        if score > path_score(existing):
            safe_move(existing, dup_folder)
            db[digest] = path
            return "replaced"
        else:
            safe_move(path, dup_folder)
            return "duplicate"


# =========================
# MAIN
# =========================

def run_basic_mode():

    print("\n==============================")
    print("   DUPLICATE FINDER STARTED")
    print("==============================\n")

    files = collect_files()

    print(f"[INFO] Files found: {len(files)}")
    print(f"[INFO] Workers: {MAX_WORKERS}\n")

    dup_folder, corrupt_folder = build_folders()

    lock = threading.Lock()
    db = {}

    stats = {
        "unique": 0,
        "duplicate": 0,
        "replaced": 0,
        "corrupt": 0,
        "skip": 0
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [
            ex.submit(process_file, f, lock, dup_folder, corrupt_folder, db)
            for f in files
        ]

        iterator = as_completed(futures)

        if tqdm:
            iterator = tqdm(iterator, total=len(futures), desc="Scanning")

        for f in iterator:
            r = f.result()
            if r in stats:
                stats[r] += 1

    # CLEANUP
    for root in get_scan_roots():
        clean_empty_dirs(root)

    print("\n==============================")
    print("        SUMMARY")
    print("==============================")
    print(stats)
    print(f"\n📁 Duplicates: {dup_folder}")
    print(f"⚠️ Corrupted : {corrupt_folder}")
    print("\n[DONE]")