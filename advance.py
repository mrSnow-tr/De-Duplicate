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
except ImportError:
    tqdm = None


def progress(iterable, desc, total=None):
    if tqdm:
        return tqdm(iterable, desc=desc, total=total)
    return iterable


# =========================
# CONFIG
# =========================

MIN_FILE_SIZE = 5 * 1024

FILE_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"},
    "Videos": {".mp4", ".avi", ".mov", ".webm"},
    "Documents": {".pdf", ".doc", ".docx", ".txt"},
    "Recording": {".wav", ".aac", ".flac"},
    "Movies": {".mkv", ".3gp", ".m4v"},
    "Music": {".mp3", ".m4a"},
    "Programs": {".exe", ".msi", ".apk", ".deb", ".rpm", ".pkg"},
    "Compressed": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"},
    "Torrents": {".torrent"},
    "Subtitles": {".srt", ".ass", ".ssa", ".sub", ".vtt"}
}

WHITELIST_FOLDERS = {
    "Downloads",
    "Desktop",
    "Telegram",
    "WhatsApp",
    "Bluetooth",
    "ADM"
}

EXCLUDE_FOLDERS = set(FILE_CATEGORIES.keys()) | {"Duplicates"}

DB_NAME = "advance.db"


# =========================
# DATABASE
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
    conn.execute("DELETE FROM moves")
    conn.commit()


def log_move(conn, src, dst):
    conn.execute("INSERT INTO moves (src, dst) VALUES (?, ?)", (str(src), str(dst)))


# =========================
# ENV
# =========================

def is_android():
    return "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ


# =========================
# ROOTS
# =========================

def get_scan_roots():
    roots = []

    if is_android():
        return [Path("/storage/emulated/0")]

    system = platform.system()

    if system == "Windows":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            d = Path(f"{letter}:\\")
            if d.exists():
                roots.append(d)

    roots.append(Path.home() / "Downloads")

    if system != "Windows":
        roots.extend(Path("/mnt").glob("*"))

    return roots


# =========================
# DESTINATION
# =========================

def get_destination_root():
    return Path("/storage/emulated/0") if is_android() else Path.home()


def build_folders():
    root = get_destination_root()

    folders = {}
    for c in FILE_CATEGORIES:
        p = root / c
        p.mkdir(parents=True, exist_ok=True)
        folders[c] = p

    dup = root / "Duplicates"
    dup.mkdir(parents=True, exist_ok=True)

    for c in FILE_CATEGORIES:
        (dup / c).mkdir(parents=True, exist_ok=True)

    return folders, dup


# =========================
# FILTERS
# =========================

def is_hidden(path):
    return path.name.startswith(".")


def is_system_folder(path):
    return "android/data" in str(path).lower() or "android/obb" in str(path).lower()


def category_of(path):
    ext = path.suffix.lower()
    for c, exts in FILE_CATEGORIES.items():
        if ext in exts:
            return c
    return None


def is_in_output_folder(path):
    return any(x in path.parts for x in EXCLUDE_FOLDERS)


# =========================
# HASH
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
# SCORE
# =========================

def file_score(path):
    try:
        s = path.stat()
        score = s.st_size + s.st_mtime

        low = str(path).lower()
        if "downloads" in low:
            score -= 10**12
        if "telegram" in low or "whatsapp" in low:
            score -= 10**11

        return score
    except:
        return 0


# =========================
# SAFE MOVE
# =========================

def safe_move(conn, src, dst_folder):
    if src.parent == dst_folder:
        return

    dst = dst_folder / src.name
    i = 1

    while dst.exists():
        dst = dst_folder / f"{src.stem}_{i}{src.suffix}"
        i += 1

    try:
        shutil.move(str(src), str(dst))
        log_move(conn, src, dst)
    except Exception as e:
        print(f"[MOVE ERROR] {src}: {e}")


# =========================
# SCAN
# =========================

def scan_files():
    for root in get_scan_roots():
        if not root.exists():
            continue

        for base, _, files in os.walk(root):
            bp = Path(base)

            if is_android() and "android/data" in base.lower():
                continue

            if not any(f in bp.parts for f in WHITELIST_FOLDERS):
                continue

            if is_in_output_folder(bp):
                continue

            for f in files:
                yield bp / f


# =========================
# FULL SCAN
# =========================

def run_full_scan():

    conn = init_db()
    reset_db(conn)

    print("\n==============================")
    print("   Advance DUPLICATE ENGINE")
    print("==============================\n")

    folders, duplicates_root = build_folders()

    files = []

    for f in scan_files():
        try:
            if is_hidden(f) or is_system_folder(f):
                continue
            if f.stat().st_size < MIN_FILE_SIZE:
                continue
            if not category_of(f):
                continue
            files.append(f)
        except:
            continue

    print(f"[INFO] Files: {len(files)}")

    max_workers = min(32, (os.cpu_count() or 4) * 2)

    hash_map = {}

    def process(f):
        qh = quick_hash(f)
        if not qh:
            return None
        return full_hash(f), f

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(process, f) for f in files]

        for fut in progress(as_completed(futures), "Hashing", len(futures)):
            res = fut.result()
            if not res:
                continue
            h, f = res
            hash_map.setdefault(h, []).append(f)

    # =========================
    # MOVE PHASE (FIXED)
    # =========================

    moved = 0
    duplicates = 0

    items = list(hash_map.items())

    for h, group in progress(items, "Moving", len(items)):

        if len(group) == 1:
            f = group[0]
            cat = category_of(f)
            if cat:
                safe_move(conn, f, folders[cat])
                moved += 1
            continue

        original = max(group, key=file_score)
        cat = category_of(original)

        if cat:
            safe_move(conn, original, folders[cat])
            moved += 1

        for f in group:
            if f == original:
                continue
            cat = category_of(f)
            if cat:
                safe_move(conn, f, duplicates_root / cat)
                duplicates += 1

    conn.commit()
    conn.close()

    print("\n====================")
    print("SUMMARY")
    print("====================")
    print(f"Moved: {moved}")
    print(f"Duplicates: {duplicates}")


# =========================
# UNDO
# =========================

def undo_last_session():
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute("SELECT src, dst FROM moves").fetchall()

    print(f"[UNDO] {len(rows)} files")

    for src, dst in progress(rows, "Undoing", len(rows)):
        try:
            s = Path(src)
            d = Path(dst)
            d.parent.mkdir(parents=True, exist_ok=True)

            if d.exists():
                shutil.move(str(d), str(s))
        except Exception as e:
            print(f"[UNDO ERROR] {dst}: {e}")

    conn.execute("DELETE FROM moves")
    conn.commit()
    conn.close()

    print("[UNDO COMPLETE]")


# =========================
# MENU
# =========================

def advance_menu():
    while True:
        print("\n1. Full scan     -> Full Scan to organize and filter")
        print("2. Undo previous -> Undo everything from previous scan")
        print("3. Exit or e     -> Exit or Quite the Duplicate finder")

        c = input("> ").strip().lower()

        if c in ("1", "full", "f" ):
            run_full_scan()
        elif c in ("2", "undo", "u"):
            undo_last_session()
        elif c in ("3", "exit", "e"):
            break
        else:
            print("Invalid")


def run_advance_mode():
    advance_menu()