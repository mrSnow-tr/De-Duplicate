import os
import shutil
import hashlib
import platform
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# OPTIONAL tqdm
# =========================
try:
    from tqdm import tqdm
    USE_TQDM = True
except ImportError:
    USE_TQDM = False


# =========================
# CONFIG
# =========================

MIN_FILE_SIZE = 5 * 1024

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

WHITELIST_FOLDERS = {
    "Downloads",
    "Desktop",
    "Telegram",
    "WhatsApp",
    "Bluetooth",
    "ADM" 
}

EXCLUDE_FOLDERS = {
    "Images",
    "Videos",
    "Documents",
    "Audio",
    "Duplicates"
}

DB_NAME = "de_duplicate.db"


# =========================
# DATABASE (UNDO SYSTEM)
# =========================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src TEXT,
            dst TEXT
        )
    """)

    conn.commit()
    return conn


def reset_db(conn):
    c = conn.cursor()
    c.execute("DELETE FROM moves")
    conn.commit()


def log_move(conn, src, dst):
    c = conn.cursor()
    c.execute("INSERT INTO moves (src, dst) VALUES (?, ?)", (str(src), str(dst)))
    conn.commit()


def undo_last_session():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    rows = c.execute("SELECT src, dst FROM moves").fetchall()

    print(f"[UNDO] Restoring {len(rows)} files...")

    for src, dst in rows:
        try:
            src_p = Path(src)
            dst_p = Path(dst)

            if dst_p.exists():
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst_p), str(src_p))

        except Exception as e:
            print(f"[UNDO ERROR] {dst} -> {e}")

    reset_db(conn)
    conn.close()
    print("[UNDO COMPLETE]")


# =========================
# ENV DETECTION
# =========================

def is_android():
    return "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ


# =========================
# SCAN ROOTS
# =========================

def get_scan_roots():
    roots = []

    if is_android():
        return [Path("/storage/emulated/0")]

    system = platform.system()

    if system == "Windows":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)

        roots.append(Path.home() / "Downloads")

    elif system == "Darwin":
        roots.append(Path.home() / "Downloads")

    else:
        roots.append(Path.home() / "Downloads")
        roots.extend(Path("/mnt").glob("*"))

    return roots


# =========================
# DESTINATION
# =========================

def get_destination_root():
    if is_android():
        return Path("/storage/emulated/0")
    return Path.home()


def build_folders():
    root = get_destination_root()

    folders = {}
    for c in FILE_CATEGORIES:
        p = root / c
        p.mkdir(exist_ok=True)
        folders[c] = p

    dup = root / "Duplicates"
    for c in FILE_CATEGORIES:
        (dup / c).mkdir(parents=True, exist_ok=True)

    return folders, dup


# =========================
# FILE FILTERS
# =========================

def is_hidden(path):
    return path.name.startswith(".")


def is_system_folder(path):
    return any(x in str(path).lower() for x in ["android/data", "android/obb"])


def category_of(path):
    ext = path.suffix.lower()
    for c, exts in FILE_CATEGORIES.items():
        if ext in exts:
            return c
    return None


def is_in_output_folder(path):
    return any(x in path.parts for x in EXCLUDE_FOLDERS)


# =========================
# HASHING
# =========================

def quick_hash(path):
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            sha.update(f.read(65536))

            size = path.stat().st_size
            if size > 65536:
                f.seek(max(0, size - 65536))
                sha.update(f.read(65536))

        return sha.hexdigest()
    except:
        return None


def full_hash(path):
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha.update(chunk)
        return sha.hexdigest()
    except:
        return None


# =========================
# SCORING ORIGINAL
# =========================

def file_score(path):
    score = 0
    try:
        stat = path.stat()

        score += stat.st_size
        score += stat.st_mtime

        low = str(path).lower()

        if "downloads" in low:
            score -= 10**12
        if "telegram" in low or "whatsapp" in low:
            score -= 10**11

    except:
        pass

    return score


# =========================
# MOVE SAFE
# =========================

def safe_move(conn, src, dst_folder):

    if src.parent == dst_folder:
        return

    dst = dst_folder / src.name
    i = 1

    while dst.exists():
        dst = dst_folder / f"{src.stem}_{i}{src.suffix}"
        i += 1

    shutil.move(str(src), str(dst))
    log_move(conn, src, dst)


# =========================
# FILE SCAN
# =========================

def scan_files():
    for root in get_scan_roots():
        if not root.exists():
            continue

        for base, dirs, files in os.walk(root):

            if is_android() and "android/data" in base.lower():
                continue

            base_path = Path(base)

            # WHITELIST (only scan these sources)
            if not any(folder in base_path.parts for folder in WHITELIST_FOLDERS):
                continue

            # skip output folders
            if is_in_output_folder(base_path):
                continue

            for f in files:
                yield base_path / f


# =========================
# MAIN SCAN
# =========================

def run_full_scan():

    conn = init_db()
    reset_db(conn)

    print("\n[FULL SCAN STARTED]\n")

    folders, duplicates_root = build_folders()

    all_files = []

    for f in scan_files():
        try:
            if is_hidden(f) or is_system_folder(f):
                continue

            if f.stat().st_size < MIN_FILE_SIZE:
                continue

            if not category_of(f):
                continue

            all_files.append(f)

        except:
            continue

    max_workers = min(32, (os.cpu_count() or 4) * 2)

    print(f"[INFO] Processing {len(all_files)} files...\n")

    hash_groups = {}

    def process_file(f):
        qh = quick_hash(f)
        if not qh:
            return None
        fh = full_hash(f)
        return (fh, f)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_file, f) for f in all_files]

        iterator = tqdm(as_completed(futures), total=len(futures)) if USE_TQDM else as_completed(futures)

        for fut in iterator:
            result = fut.result()
            if not result:
                continue

            h, f = result
            hash_groups.setdefault(h, []).append(f)

    moved = 0
    duplicates = 0

    for h, files in hash_groups.items():

        if len(files) == 1:
            f = files[0]
            cat = category_of(f)
            if cat:
                safe_move(conn, f, folders[cat])
                moved += 1
            continue

        original = max(files, key=file_score)
        cat = category_of(original)

        if cat:
            safe_move(conn, original, folders[cat])
            moved += 1

        for f in files:
            if f == original:
                continue
            cat = category_of(f)
            if cat:
                safe_move(conn, f, duplicates_root / cat)
                duplicates += 1

    print("\n====================")
    print("SUMMARY")
    print("====================")
    print(f"Moved: {moved}")
    print(f"Duplicates: {duplicates}")

    conn.close()
    
# =========================
# ADVANCE MENU
# =========================

def advance_menu():
    while True:
        print("\n1. Full scan     -> Scan everything and filter duplicates")
        print("2. Undo previous -> Undo everything from previous scan")
        print("3. Exit or e     -> Return to main menu")

        choice = input("\nEnter your selection: ").strip().lower()

        if choice in ("full", "1"):
            run_full_scan()

        elif choice in ("undo", "2"):
            undo_last_session()

        elif choice in ("exit", "e", "3"):
            break

        else:
            print("Invalid option")


# =========================
# MAIN ENTRY FOR main.py
# =========================

def run_advance_mode():
    advance_menu()