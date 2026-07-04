import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from biblecue import parse_scripture, BIBLE_BOOKS

def test_direct_reference():
    results = parse_scripture("John 3:16")
    assert ("John", 3, 16) in results

def test_spoken_numbers():
    results = parse_scripture("John three sixteen")
    assert ("John", 3, 16) in results

def test_famous_passage_shepherd_psalm():
    results = parse_scripture("let's read the shepherd psalm")
    assert ("Psalms", 23, 1) in results

def test_famous_passage_love_chapter():
    results = parse_scripture("now we go to the love chapter")
    assert ("1 Corinthians", 13, 1) in results

def test_book_alias_first_corinthians():
    assert BIBLE_BOOKS.get("first corinthians") == "1 Corinthians"

def test_book_alias_revelations():
    assert BIBLE_BOOKS.get("revelations") == "Revelation"

def test_returns_list():
    assert isinstance(parse_scripture("John 3:16"), list)

def test_empty_text():
    assert parse_scripture("") == []

def test_caps_for_three_per_transcript():
    results = parse_scripture("John 1:1, John 2:2, John 3:3, John 4:4")
    assert len(results) == 3

def test_accent_philippians_fillippians():
    results = parse_scripture("fillippians 4:13")
    assert ("Philippians", 4, 13) in results

def test_accent_psalms_salm():
    results = parse_scripture("salm 23:1")
    assert ("Psalms", 23, 1) in results

def test_accent_psalms_salms():
    results = parse_scripture("salms chapter 91 verse 1")
    assert ("Psalms", 91, 1) in results

def test_accent_shepherd_salm():
    results = parse_scripture("the shepherd salm")
    assert ("Psalms", 23, 1) in results

def test_accent_colossian():
    results = parse_scripture("colossian 3:16")
    assert ("Colossians", 3, 16) in results

def test_accent_corinthian():
    results = parse_scripture("corinthian 13:4")
    assert ("1 Corinthians", 13, 4) in results

def test_near_miss_does_not_crash():
    results = parse_scripture("phillipan 4 13")
    assert isinstance(results, list)


def test_fetch_from_db_returns_none_gracefully():
    from biblecue import _fetch_from_db
    # If bible.db doesn't exist in test env, should return (None, None) — not raise
    text, ref = _fetch_from_db("John", 3, 16, "KJV")
    assert text is None or isinstance(text, str)
