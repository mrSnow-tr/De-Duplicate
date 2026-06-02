import os
import hashlib
import shutil
import threading
import sqlite3
import signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# OPTIONAL TQDM
# =========================
try:
    from tqdm import tqdm
    USE_TQDM = True
except ImportError:
    tqdm = None
    USE_TQDM = False


# =========================
# CONFIG
# =========================
MIN_FILE_SIZE = 5 * 1024
exit_event = threading.Event()

DB_PATH = Path("moderate.db")
DB_LOCK = threading.Lock()

# SINGLE GLOBAL CONNECTION (IMPORTANT FIX)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
cur = conn.cursor()


# =========================
# SAFE EXIT
# =========================
def safe_exit_handler(signum=None, frame=None):
    print("\n[INFO] Safe exit triggered...")
    exit_event.set()

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
# =========================
FILE_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"},
    "Videos": {".mp4", ".avi", ".mov", ".webm"},
    "Documents": {".pdf", ".doc", ".docx", ".txt"},
    "Music": {".mp3", ".m4a", ".aac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Programs": {".exe", ".msi", ".apk", ".deb", ".rpm"},
}

SUPPORTED_EXTENSIONS = {ext for exts in FILE_CATEGORIES.values() for ext in exts}


# =========================
# STORAGE
# =========================
CATEGORY_PATHS = {}
DUPLICATES_PATH = DOWNLOADS_PATH / "Duplicates"


def setup_storage():
    global CATEGORY_PATHS, DUPLICATES_PATH

    for cat in FILE_CATEGORIES:
        path = DOWNLOADS_PATH / cat
        path.mkdir(parents=True, exist_ok=True)
        CATEGORY_PATHS[cat] = path

    DUPLICATES_PATH.mkdir(parents=True, exist_ok=True)


setup_storage()


# =========================
# SQLITE INIT
# =========================
def init_db():
    cur.execute("DROP TABLE IF EXISTS seen")
    cur.execute("DROP TABLE IF EXISTS records")

    cur.execute("""
        CREATE TABLE seen (
            hash TEXT PRIMARY KEY
        )
    """)

    cur.execute("""
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT,
            src TEXT,
            dest TEXT,
            category TEXT,
            action TEXT
        )
    """)

    conn.commit()


init_db()


# =========================
# HASHING
# =========================
def quick_hash(file_path):
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read(4096)).hexdigest()
    except:
        return None


def full_hash(file_path):
    sha = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha.update(chunk)
        return sha.hexdigest()
    except:
        return None


# =========================
# CATEGORY
# =========================
def get_category(path):
    ext = path.suffix.lower()
    for cat, exts in FILE_CATEGORIES.items():
        if ext in exts:
            return cat
    return None


# =========================
# SAFE MOVE
# =========================
def safe_move(src, dest_folder):
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = dest_folder / src.name

    counter = 1
    while dest.exists():
        dest = dest_folder / f"{src.stem}_{counter}{src.suffix}"
        counter += 1

    shutil.move(str(src), str(dest))
    return dest


# =========================
# PROCESS FILE (FIXED)
# =========================
def process_file(file_path):
    if exit_event.is_set():
        return None

    try:
        if file_path.stat().st_size < MIN_FILE_SIZE:
            return None
    except:
        return None

    category = get_category(file_path)
    if not category:
        return None

    fh = full_hash(file_path)
    if not fh:
        return None

    # default values (FIX CRASH)
    action = None
    dest = None

    with DB_LOCK:
        try:
            cur.execute("INSERT INTO seen(hash) VALUES (?)", (fh,))
            is_new = True
        except sqlite3.IntegrityError:
            is_new = False

        if is_new:
            dest = safe_move(file_path, CATEGORY_PATHS[category])
            action = "moved"
        else:
            dest = safe_move(file_path, DUPLICATES_PATH)
            action = "duplicate"

        cur.execute("""
            INSERT INTO records(hash, src, dest, category, action)
            VALUES (?, ?, ?, ?, ?)
        """, (fh, str(file_path), str(dest), category, action))

        conn.commit()

    return action


# =========================
# SCAN
# =========================
def run_moderate_scan():
    print("\n==============================")
    print("    Moderate ENGINE")
    print("================================")

    files = [f for f in DOWNLOADS_PATH.iterdir() if f.is_file()]

    if not files:
        print("[INFO] No files found.")
        return

    print(f"[INFO] Processing {len(files)} files...\n")

    stats = {"moved": 0, "duplicate": 0}

    max_workers = min(32, (os.cpu_count() or 4) * 2)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(process_file, f) for f in files]

        iterator = as_completed(futures)
        if USE_TQDM:
            iterator = tqdm(iterator, total=len(futures), desc="Processing")

        for fut in iterator:
            res = fut.result()
            if res:
                stats[res] += 1

    print("\n==== SUMMARY ====")
    for k, v in stats.items():
        print(f"{k}: {v}")


# =========================
# UNDO (FIXED)
# =========================
def undo_last_session():
    cur.execute("SELECT src, dest FROM records ORDER BY id DESC")
    records = cur.fetchall()

    if not records:
        print("[INFO] Nothing to undo.")
        return

    print(f"[INFO] Undoing {len(records)} operations...\n")

    iterator = records
    if USE_TQDM:
        iterator = tqdm(records, desc="Undoing")

    for src, dest in iterator:
        try:
            src_path = Path(src)
            dest_path = Path(dest)

            if dest_path.exists():
                src_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest_path), str(src_path))
        except:
            pass

    cur.execute("DELETE FROM seen")
    cur.execute("DELETE FROM records")
    conn.commit()

    print("[DONE] Undo completed.")


# =========================
# MENU
# =========================
def moderate_menu():
    while True:
        print("\n1. Moderate scan -> Scan 'Download' folder to organize")
        print("2. Undo previous -> Undo everything from previous scan")
        print("3. Exit or e     -> Exit or Quite the Duplicate finder")

        choice = input("\nEnter your selection: ").strip().lower()

        if choice in ("m", "1", "moderate"):
            run_moderate_scan()

        elif choice in ("undo", "2", "u"):
            undo_last_session()

        elif choice in ("exit", "e", "3"):
            break

        else:
            print("Invalid option")


# =========================
# ENTRY
# =========================
def run_moderate_mode():
    moderate_menu()