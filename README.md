# De-Duplicate

A cross-platform Python script for filtering duplicate images and videos using SHA256 hashing.

Works on:

- Termux (Android)
- Windows
- Linux
- macOS

---

# Features

- Detects duplicates using SHA256 hash
- Works even if filenames are changed
- Filters duplicate files automatically
- Sorts unique files into folders
- Recursive folder scanning
- Cross-platform support
- Lightweight and fast

---


# Current Supported File Types

- Images
- Videos
- Documents
- Music
- Programs
- Compressed
- Code
- Duplicate


# Installation Guide 

## 1. Clone Repository
```bash
git clone https://github.com/torekultushar/De-Duplicate.git
cd De-Duplicate
```

## 2. Install Python

Download Python:

https://www.python.org/downloads/

Check version:
```bash
python --version
```

## 3. Install Requirements
```bash
pip install -r requirements.txt
```

# Windows
```bash
python main.py
```

# Linux / macOS
```bash
python3 main.py
```
# Termux
```bash
termux-setup-storage
python main.py
```

# How Duplicate Detection Works 

The script does NOT compare filenames.
Instead, it generates a SHA256 hash from the actual file content. It creates a unique digital fingerprint for every file.

# Usage
Script will filter-out  duplicate files from Downloads folder and short them inside Downloads folder according to their type. 

# Requirements
```bash
Python 3.8+
```
