"""
Run once to build resources/bible.db.
Usage: python scripts/build_bible_db.py

Fetches public-domain translations from bible-api.com using 8 parallel
threads. Takes ~3-5 minutes (vs 18+ minutes single-threaded).
"""

import os, time, sqlite3, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

TRANSLATIONS = {
    "KJV":   "kjv",
    "WEB":   "web",
    "ASV":   "asv",
    "BBE":   "bbe",
    "YLT":   "ylt",
    "DARBY": "darby",
}

BOOKS = [
    ("Genesis", 50), ("Exodus", 40), ("Leviticus", 27), ("Numbers", 36),
    ("Deuteronomy", 34), ("Joshua", 24), ("Judges", 21), ("Ruth", 4),
    ("1 Samuel", 31), ("2 Samuel", 24), ("1 Kings", 22), ("2 Kings", 25),
    ("1 Chronicles", 29), ("2 Chronicles", 36), ("Ezra", 10), ("Nehemiah", 13),
    ("Esther", 10), ("Job", 42), ("Psalms", 150), ("Proverbs", 31),
    ("Ecclesiastes", 12), ("Song of Solomon", 8), ("Isaiah", 66),
    ("Jeremiah", 52), ("Lamentations", 5), ("Ezekiel", 48), ("Daniel", 12),
    ("Hosea", 14), ("Joel", 3), ("Amos", 9), ("Obadiah", 1), ("Jonah", 4),
    ("Micah", 7), ("Nahum", 3), ("Habakkuk", 3), ("Zephaniah", 3),
    ("Haggai", 2), ("Zechariah", 14), ("Malachi", 4), ("Matthew", 28),
    ("Mark", 16), ("Luke", 24), ("John", 21), ("Acts", 28), ("Romans", 16),
    ("1 Corinthians", 16), ("2 Corinthians", 13), ("Galatians", 6),
    ("Ephesians", 6), ("Philippians", 4), ("Colossians", 4),
    ("1 Thessalonians", 5), ("2 Thessalonians", 3), ("1 Timothy", 6),
    ("2 Timothy", 4), ("Titus", 3), ("Philemon", 1), ("Hebrews", 13),
    ("James", 5), ("1 Peter", 5), ("2 Peter", 3), ("1 John", 5),
    ("2 John", 1), ("3 John", 1), ("Jude", 1), ("Revelation", 22),
]

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "bible.db")
WORKERS = 8


def create_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verses (
            book        TEXT    NOT NULL,
            chapter     INTEGER NOT NULL,
            verse       INTEGER NOT NULL,
            translation TEXT    NOT NULL,
            text        TEXT    NOT NULL,
            PRIMARY KEY (book, chapter, verse, translation)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bcv ON verses(book, chapter, verse)")
    conn.commit()
    return conn


def fetch_chapter(book, chapter, trans_name, trans_api):
    url = f"https://bible-api.com/{book} {chapter}?translation={trans_api}"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                data = r.json()
                rows = [(v["book_name"], v["chapter"], v["verse"], trans_name, v["text"].strip())
                        for v in data.get("verses", [])]
                return (book, chapter, trans_name, rows)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
        except Exception as e:
            time.sleep(2 * (attempt + 1))
    return (book, chapter, trans_name, [])


def main():
    conn = create_db(DB_PATH)
    db_lock = Lock()

    # Build the full list of (book, chapter, trans_name, trans_api) tasks
    tasks = []
    for trans_name, trans_api in TRANSLATIONS.items():
        for book, num_chapters in BOOKS:
            for ch in range(1, num_chapters + 1):
                tasks.append((book, ch, trans_name, trans_api))

    total = len(tasks)
    done = 0
    start = time.time()

    print(f"Building bible.db — {total} chapter requests across {len(TRANSLATIONS)} translations")
    print(f"Using {WORKERS} parallel workers. This takes ~3-5 minutes.\n")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_chapter, *t): t for t in tasks}
        for future in as_completed(futures):
            book, chapter, trans_name, rows = future.result()
            done += 1

            if rows:
                with db_lock:
                    conn.executemany(
                        "INSERT OR REPLACE INTO verses VALUES (?,?,?,?,?)", rows)
                    conn.commit()

            if done % 100 == 0 or done == total:
                elapsed = time.time() - start
                rate = done / elapsed
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} ({done*100//total}%) — {rate:.1f} req/s — ETA {eta:.0f}s")

    conn.close()
    size_mb = os.path.getsize(DB_PATH) / 1_048_576
    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s — {DB_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
