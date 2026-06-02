import os
import hashlib
import shutil
import platform
import threading
import time
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


# =========================
# CONFIG
# =========================

MAX_WORKERS = min(32, (os.cpu_count() or 4) * 2)
MIN_FILE_SIZE = 5 * 1024  # 5kb minimum limit
QUEUE_LIMIT = MAX_WORKERS * 40


# =========================
# SQLITE (UNDO SYSTEM)
# =========================

DB_FILE = "basic.db"
db_conn = None
db_lock = threading.Lock()


def init_db():
    global db_conn
    db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    db_conn.execute("PRAGMA journal_mode=WAL")

    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_path TEXT,
            moved_path TEXT,
            action TEXT,
            timestamp REAL
        )
    """)
    db_conn.commit()


def clear_db():
    db_conn.execute("DELETE FROM moves")
    db_conn.commit()


def log_move(o, m, a):
    with db_lock:
        db_conn.execute(
            "INSERT INTO moves VALUES (NULL, ?, ?, ?, ?)",
            (str(o), str(m), a, time.time())
        )
        db_conn.commit()


# =========================
# FILE TYPES
# =========================

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

SUPPORTED = {e for s in FILE_CATEGORIES.values() for e in s}


# =========================
# SYSTEM
# =========================

def is_android():
    return "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ


def scan_roots():
    if is_android():
        return [Path("/storage/emulated/0")]

    system = platform.system()

    if system == "Windows":
        return [Path(f"{d}:\\") for d in "DEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{d}:\\").exists()] + [Path.home() / "Downloads"]

    return [Path.home() / "Downloads"]


def best_drive():
    if is_android():
        return Path("/storage/emulated/0")
    return Path.home()


def make_folders():
    root = best_drive()
    dup = root / "Duplicates"
    corrupt = root / "Corrupted"
    dup.mkdir(exist_ok=True)
    corrupt.mkdir(exist_ok=True)
    return dup, corrupt


# =========================
# FILTERS (FAST PRECHECK FIRST)
# =========================

def valid_file(p):
    return (
        p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in SUPPORTED
    )


# =========================
# HASHING
# =========================

def hash_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except:
        return None


# =========================
# CORRUPTION CHECK
# =========================

def corrupted(p):
    try:
        if p.stat().st_size == 0:
            return True
        with open(p, "rb") as f:
            f.read(512)
        return False
    except:
        return True


# =========================
# SCORE SYSTEM
# =========================

def score(p: Path):
    s = 0
    sp = str(p).lower()

    if "camera" in sp or "dcim" in sp:
        s += 100
    if "downloads" in sp:
        s += 50
    if "cache" in sp or "temp" in sp:
        s -= 70

    try:
        s += max(0, 30 - len(p.parts))
    except:
        pass

    return s


# =========================
# SAFE MOVE + LOG
# =========================

def move(src, folder, action):
    dest = folder / src.name
    i = 1

    while dest.exists():
        dest = folder / f"{src.stem}_{i}{src.suffix}"
        i += 1

    try:
        log_move(src, dest, action)
        shutil.move(str(src), str(dest))
    except:
        pass


# =========================
# STREAM FILE GENERATOR (ULTRA FAST)
# =========================

def stream_files():
    for root in scan_roots():
        if not root.exists():
            continue

        for cur, _, names in os.walk(root):
            for n in names:
                p = Path(cur) / n

                try:
                    if p.stat().st_size < MIN_FILE_SIZE:
                        continue
                except:
                    continue

                if valid_file(p):
                    yield p


# =========================
# CORE PROCESSOR (FAST PATH + CACHE)
# =========================

hash_cache = {}
db = {}
db_lock_mem = threading.Lock()


def process_file(path, dup_folder, corrupt_folder):

    if corrupted(path):
        move(path, corrupt_folder, "corrupt")
        return "corrupt"

    h = hash_cache.get(path)

    if not h:
        h = hash_file(path)
        hash_cache[path] = h

    if not h:
        return "skip"

    s = score(path)

    with db_lock_mem:

        if h not in db:
            db[h] = path
            return "unique"

        existing = db[h]

        if s > score(existing):
            move(existing, dup_folder, "duplicate")
            db[h] = path
            return "replaced"

        else:
            move(path, dup_folder, "duplicate")
            return "duplicate"


# =========================
# ULTRA ENGINE SCANNER
# =========================

def run_full_scan():

    clear_db()

    print("\n==============================")
    print("   BASIC ENGINE")
    print("==============================\n")

    dup_folder, corrupt_folder = make_folders()

    stats = {"unique": 0, "duplicate": 0, "replaced": 0, "corrupt": 0, "skip": 0}

    futures = set()

    if tqdm:
        pbar = tqdm(desc="Scanning", unit="file")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:

        for file in stream_files():

            futures.add(ex.submit(process_file, file, dup_folder, corrupt_folder))

            # backpressure control (VERY IMPORTANT)
            if len(futures) >= QUEUE_LIMIT:

                done, futures = wait(futures, return_when=FIRST_COMPLETED)

                for d in done:
                    r = d.result()
                    if r in stats:
                        stats[r] += 1
                        if tqdm:
                            pbar.update(1)

        # flush remaining
        for f in futures:
            r = f.result()
            if r in stats:
                stats[r] += 1
                if tqdm:
                    pbar.update(1)

    if tqdm:
        pbar.close()

    print("\n==============================")
    print("        SUMMARY")
    print(stats)
    print("\n[DONE]")


# =========================
# MENU
# =========================

def undo_last_session():
    print("\n[UNDO] Restoring...")

    rows = db_conn.execute(
        "SELECT original_path, moved_path FROM moves ORDER BY id DESC"
    ).fetchall()

    count = 0

    for o, m in rows:
        op, mp = Path(o), Path(m)

        try:
            if mp.exists():
                op.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(mp), str(op))
                count += 1
        except:
            pass

    db_conn.execute("DELETE FROM moves")
    db_conn.commit()

    print(f"[DONE] Restored {count} files\n")


def basic_menu():
    while True:
        print("\n1. Full scan     -> Scan everything and filter duplicates")
        print("2. Undo previous -> Undo everything from previous scan")
        print("3. Exit or e     -> Exit or Quite the Duplicate finder")

        choice = input("\nEnter your selection: ").strip().lower()

        if choice in ("full", "1", "f"):
            run_full_scan()

        elif choice in ("undo", "2", "u"):
            undo_last_session()

        elif choice in ("exit", "e", "3"):
            break

        else:
            print("Invalid option")


# =========================
# MAIN ENTRY FOR main.py
# =========================

def run_basic_mode():
	init_db(),
	basic_menu()