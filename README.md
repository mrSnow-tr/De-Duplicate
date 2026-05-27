# De-Duplicate

A cross-platform Python script for removing duplicate images and videos using SHA256 hashing.

Works on:

- Termux (Android)
- Windows
- Linux
- macOS

---

# Features

- Detects duplicates using SHA256 hash
- Works even if filenames are changed
- Deletes duplicate files automatically
- Sorts unique files into folders
- Recursive folder scanning
- Cross-platform support
- Lightweight and fast
- Easy to extend later

---


# Current Supported File Types

## Images

- jpg
- jpeg
- png
- gif
- bmp
- webp
- tiff
- heic

## Videos

- mp4
- mkv
- avi
- mov
- wmv
- flv
- webm
- m4v


# Installation Guide 

## 1. Clone Repository

'git clone https://github.com/torekultushar/De-Duplicate.git'

'cd De-Duplicate'

## 2. Install Python

Download Python:

https://www.python.org/downloads/

Check version:
'python --version'

## 3. Install Requirements
'pip install -r requirements.txt'

# Windows
'python main.py'

# Linux / macOS
'python3 main.py'

# Termux
'python main.py'

# How Duplicate Detection Works 

The script does NOT compare filenames.

Instead, it generates a SHA256 hash from the actual file content.
It creates a unique digital fingerprint for every file.

# Usage

Put all files inside the Raw folder.
Run the script.

# Requirements
Python 3.8+
