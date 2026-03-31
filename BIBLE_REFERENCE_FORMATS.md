# WCI BibleShow — Supported Bible Reference Formats

This document lists every spoken and written format the detection system
recognises. It is generated from a direct reading of the parser in
`WCIBibleshow.py` (functions `parse_scripture`, `normalise_spoken`,
`extract_spoken_numbers`, `_words_to_numbers`, `_detect_navigation`, and
the `FAMOUS_PASSAGES` / `BIBLE_BOOKS` tables).

---

## 1. Standard Written Formats

These are matched by the `python-scriptures` library (Pass 3) and the
strict regex (Pass 4) after normalisation.

| Format | Example |
|--------|---------|
| `Book Chapter:Verse` | `John 3:16` |
| `Book Chapter.Verse` | `John 3.16` (dot converted to colon) |
| `Book Ch:V-V2` (range — start verse used) | `Matthew 5:3-12` |
| `Book Ch:V through V2` (range — start verse used) | `Romans 8:28 through 39` |
| `Book Ch:V to V2` (range — start verse used) | `Psalms 23:1 to 6` |
| Numbered book with digit prefix | `1 Corinthians 13:4` |
| Numbered book with Roman numeral prefix | `I Corinthians 13:4`, `II Timothy 3:16` |

---

## 2. Spoken "Verse" Keyword Formats

The word "verse" (and optional connector words) is converted to colon
notation before matching.

| Spoken phrase | Normalised to | Example |
|---------------|---------------|---------|
| `Book N verse V` | `Book N:V` | `John 3 verse 16` |
| `Book N and verse V` | `Book N:V` | `Romans 8 and verse 28` |
| `Book N from verse V` | `Book N:V` | `Psalms 23 from verse 1` |

---

## 3. Spoken "Chapter" Keyword Formats

The word "chapter" is stripped (converted to a bare number) before matching.

| Spoken phrase | Normalised to | Example |
|---------------|---------------|---------|
| `Book chapter N verse V` | `Book N:V` | `Matthew chapter 5 verse 3` |
| `Book chapter N and verse V` | `Book N:V` | `Luke chapter 4 and verse 18` |
| `Book chapter N from verse V` | `Book N:V` | `Isaiah chapter 53 from verse 5` |
| `Book chapter N` (no verse — only detected if verse context exists) | `Book N` | `John chapter 3` |

---

## 4. Word-Number Formats

All number words (ones, teens, tens, compounds, hundreds) are converted to
digits before matching. This applies to both chapter and verse positions.

### Single-word numbers (1–19)
`one two three four five six seven eight nine ten`
`eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen`

### Tens (20, 30 … 90)
`twenty thirty forty fifty sixty seventy eighty ninety`

### Compound tens+ones (21–99)
`twenty one` `twenty two` … `ninety nine`

### Hundreds
`one hundred` `one hundred and twenty five` etc.

### Ordinals (map to their cardinal equivalent)
`first second third fourth fifth sixth seventh eighth ninth tenth`

### Examples with word numbers

| Spoken phrase | Example |
|---------------|---------|
| `Book word-num word-num` | `John three sixteen` |
| `Book chapter word-num verse word-num` | `Matthew chapter five verse three` |
| `Book word-num verse word-num` | `Romans eight verse twenty eight` |
| `Book chapter word-num` | `Psalms chapter twenty three` |
| Mixed digits and words | `John chapter 3 verse sixteen` |

---

## 5. Comma and Semicolon Tolerance

Commas and semicolons that speech recognition inserts mid-phrase are
stripped before any detection pass runs. This means all of the formats
above also work with commas between chapter and verse.

| Spoken (with comma) | Detected as |
|---------------------|-------------|
| `Matthew 7, verse 11` | `Matthew 7:11` |
| `Joshua chapter 4, verse 5` | `Joshua 4:5` |
| `John chapter 3, verse 16` | `John 3:16` |
| `Romans 8, 28` | `Romans 8:28` |
| `John chapter 3; verse 16` | `John 3:16` |

---

## 6. Numbered / Prefixed Book Formats

Books with a numeric prefix can be spoken in several ways.

| Written / spoken | Canonical name |
|------------------|----------------|
| `1 Corinthians` / `First Corinthians` / `1st Corinthians` | 1 Corinthians |
| `2 Corinthians` / `Second Corinthians` / `2nd Corinthians` | 2 Corinthians |
| `1 Samuel` / `First Samuel` | 1 Samuel |
| `2 Samuel` / `Second Samuel` | 2 Samuel |
| `1 Kings` / `First Kings` | 1 Kings |
| `2 Kings` / `Second Kings` | 2 Kings |
| `1 Chronicles` / `First Chronicles` | 1 Chronicles |
| `2 Chronicles` / `Second Chronicles` | 2 Chronicles |
| `1 Thessalonians` / `First Thessalonians` | 1 Thessalonians |
| `2 Thessalonians` / `Second Thessalonians` | 2 Thessalonians |
| `1 Timothy` / `First Timothy` | 1 Timothy |
| `2 Timothy` / `Second Timothy` | 2 Timothy |
| `1 Peter` / `First Peter` / `St Peter` / `Saint Peter` | 1 Peter |
| `2 Peter` / `Second Peter` | 2 Peter |
| `1 John` / `First John` / `St John` / `Saint John` | 1 John |
| `2 John` / `Second John` | 2 John |
| `3 John` / `Third John` | 3 John |
| `I Corinthians` (Roman numeral) | 1 Corinthians |
| `II Timothy` (Roman numeral) | 2 Timothy |
| _(same Roman-numeral pattern for all numbered books)_ | |

When a numbered book is spoken without its prefix (e.g. just "Corinthians",
"Timothy", "Peter", "Kings", "Samuel", "Chronicles", "Thessalonians") the
system defaults to the **first** book in the series.

---

## 7. Common Abbreviations Recognised

| Spoken/written | Canonical |
|----------------|-----------|
| `Psalm` / `Psalms` | Psalms |
| `Revelations` | Revelation |
| `Rev` / `Reve` | Revelation |
| `Isa` | Isaiah |
| `Zech` / `Zach` | Zechariah |
| `Mal` | Malachi |
| `Hab` | Habakkuk |
| `Zeph` | Zephaniah |
| `Hag` | Haggai |
| `Nah` | Nahum |
| `Jas` / `Jss` / `Jame` | James |
| `Song of Solomon` / `Song of Songs` / `Songs` | Song of Solomon |

---

## 8. Phonetic Mishearing Variants

Speech recognition sometimes produces phonetically similar wrong words.
The system maps these to the correct book.

| Mishearing | Corrected to |
|------------|--------------|
| `Leak` / `Louk` / `Loke` | Luke |
| `Nehemia` / `Hemia` / `Nemia` | Nehemiah |
| `Extra` / `Ezrah` | Ezra |
| `Now` (e.g. "Now chapter 5") | Numbers |
| `Phase` | 1 Peter |
| `Petter` / `Petah` | 1 Peter |
| `Obediah` / `Obidiah` / `Obediance` | Obadiah |
| `Habacuck` / `Habakuk` / `Habakook` | Habakkuk |
| `Malachy` | Malachi |
| `Zepania` / `Zephannia` | Zephaniah |
| `Phillipians` / `Philipians` | Philippians |
| `Collossians` / `Collosians` | Colossians |

Additionally, a fuzzy-matching pass (`difflib`, cutoff 0.72) tries to
autocorrect any book name that appears immediately before a chapter/verse
indicator and is not already in the known list.

---

## 9. Verse Range Handling

Verse ranges are **truncated to the start verse** before matching. Only
the opening verse is sent to ProPresenter.

| Spoken range | Detected as |
|--------------|-------------|
| `Matthew 5:3-12` | Matthew 5:3 |
| `Matthew 5:3 to 12` | Matthew 5:3 |
| `Matthew 5:3 through 12` | Matthew 5:3 |
| `verse 12 to 15` | verse 12 |
| `verse 12 through 15` | verse 12 |
| `verse 12 and 15` | verse 12 |

---

## 10. Famous / Indirect Passages

These phrases trigger a reference lookup without any chapter or verse
number being spoken.

| Phrase (case-insensitive) | Maps to |
|---------------------------|---------|
| `shepherd psalm` / `23rd psalm` / `psalm 23` / `lord is my shepherd` | Psalms 23:1 |
| `love chapter` | 1 Corinthians 13:1 |
| `faith chapter` / `hall of faith` | Hebrews 11:1 |
| `armor of god` / `armour of god` | Ephesians 6:11 |
| `lord's prayer` | Matthew 6:9 |
| `beatitudes` | Matthew 5:3 |
| `sermon on the mount` | Matthew 5:1 |
| `ten commandments` | Exodus 20:1 |
| `great commission` | Matthew 28:19 |
| `golden rule` | Matthew 7:12 |
| `in the beginning` | Genesis 1:1 |
| `for god so loved` / `john 316` / `john three sixteen` | John 3:16 |
| `fruits of the spirit` / `fruit of the spirit` | Galatians 5:22 |
| `i can do all things` / `all things through christ` | Philippians 4:13 |
| `renew your mind` / `be transformed` | Romans 12:2 |
| `no weapon formed` | Isaiah 54:17 |
| `greater is he` | 1 John 4:4 |
| `the truth shall set` / `truth will set you free` | John 8:32 |
| `come to me all` / `weary and burdened` | Matthew 11:28 |
| `plans to prosper` / `know the plans` | Jeremiah 29:11 |
| `all things work together` / `good to those who love` | Romans 8:28 |
| `fear not` / `do not fear` | Isaiah 41:10 |
| `be strong and courageous` | Joshua 1:9 |
| `cast all your anxiety` | 1 Peter 5:7 |
| `lean not on your own` / `trust in the lord` | Proverbs 3:5 |
| `new creation` / `old has gone` | 2 Corinthians 5:17 |
| `whatsoever things are true` / `think on these things` | Philippians 4:8 |
| `god of all grace` / `after you have suffered a while` / `suffered a while` | 1 Peter 5:10 |

---

## 11. Navigation Commands (Relative — No Book Needed)

When a verse is already on screen, the preacher can navigate with relative
commands. No book name is required.

| Spoken command | Action |
|----------------|--------|
| `next verse` | Advance one verse |
| `previous verse` / `prev verse` / `back verse` | Go back one verse |
| `next` (alone, no colon-reference present) | Advance one verse |
| `previous` / `prev` / `back` (alone) | Go back one verse |
| `next chapter` | First verse of next chapter |
| `previous chapter` / `prev chapter` | First verse of previous chapter |
| `verse N` (no book in phrase) | Jump to verse N in current chapter |
| `verse five` (word number, no book) | Same as above |
| `read verse N` | Jump to verse N |
| `go to verse N` | Jump to verse N |
| `chapter N verse M` (no book) | Jump to chapter N verse M, same book |
| `chapter N` (no book, no verse) | Jump to chapter N verse 1, same book |

---

## 12. Verse-1 Default Suppression

To avoid false positives, a reference that resolves to **verse 1** is only
shown if the spoken text contains at least one of:

- A colon format (`3:16`)
- The word `verse` followed by a number or number word
- `chapter N … verse N` pattern
- Two sequential numbers after a book name (e.g. `John three sixteen`)

This prevents phrases like "in the book of John chapter 3" from triggering
John 3:1 unintentionally.

---

## 13. Detection Limits

- At most **3 references** are returned from a single transcript segment.
- A configurable **cooldown** (default 12 seconds) prevents the same or a
  new reference from being re-sent to ProPresenter too quickly.
- Interim (mid-sentence) results trigger detection; final results also
  trigger detection. If the same text was already detected on an interim
  pass it is skipped on the final pass to avoid double-firing.

---

## Quick Reference Card

```
Standard:          John 3:16
With dot:          John 3.16
Spoken verse kw:   John 3 verse 16
Spoken chapter kw: John chapter 3 verse 16
Word numbers:      John three sixteen
Mixed:             John chapter three verse sixteen
With comma:        John chapter 3, verse 16
Range (→ start):   John 3:16-18  /  John 3 verse 16 to 18
Numbered book:     1 Corinthians 13:4
Spoken numbered:   First Corinthians 13 verse 4
Roman numeral:     I Corinthians 13:4
Famous passage:    the love chapter  /  for god so loved
Navigation:        next verse  /  verse 5  /  chapter 3 verse 2
```
