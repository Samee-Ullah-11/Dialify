"""Extract (name, phone) contacts from PDFs, images, Excel and CSV files.

PDF strategy:
1. Table extraction first (works for PDFs that are contact tables).
2. Line-by-line text parsing: find phone numbers with a regex, take the rest
   of the line (or the previous line) as the name.
3. Pages with no extractable text are rendered to an image and run through
   Tesseract OCR (if installed) — handles scanned PDFs.
"""
import csv
import os
import re
import shutil

import pdfplumber

# Matches international and local formats: +92 300 1234567, (042) 111-222-333,
# 0300-1234567, etc. Requires at least 7 digits total.
PHONE_RE = re.compile(
    r"(?<![\d/])"                 # not preceded by a digit or slash (avoid dates/fractions)
    r"(\+?\d[\d\s\-().]{5,}\d)"   # digits with separators
)

MIN_DIGITS = 7
MAX_DIGITS = 15

# Date-shaped strings that the phone regex would otherwise match,
# e.g. 2025-05-18, 18/05/2025, 2025.05.18
DATE_RE = re.compile(
    r"^\s*(?:(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2})\s*$"
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

# A number right after one of these labels is an ID (transaction id, invoice,
# reference, OTP...), not a phone number.
ID_LABEL_RE = re.compile(
    r"\b(tid|trx|trn|txn|transactions?|ref(erence)?s?|invoice|orders?|receipt|"
    r"slip|voucher|cnic|iban|bill|tracking|serial|batch|code|otp|pin|id)"
    r"\s*(no|num|number)?\s*[.:#\-]*\s*$",
    re.I,
)


def tesseract_available() -> bool:
    if shutil.which("tesseract"):
        return True
    default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(default):
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = default
        return True
    return False


def normalize_phone(raw: str) -> str | None:
    """Keep a leading + and digits only; reject dates and out-of-range digit counts."""
    raw = raw.strip()
    if DATE_RE.match(raw):
        return None
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not (MIN_DIGITS <= len(digits) <= MAX_DIGITS):
        return None
    if len(set(digits)) == 1:  # 0000000-style OCR noise
        return None
    return ("+" if plus else "") + digits


def apply_country_code(phone: str, default_cc: str | None) -> str:
    """03001234567 + cc '92' -> +923001234567. Leaves +numbers alone."""
    if not default_cc or phone.startswith("+"):
        return phone
    cc = re.sub(r"\D", "", default_cc)
    if not cc:
        return phone
    if phone.startswith("00"):
        return "+" + phone[2:]
    if phone.startswith("0"):
        return f"+{cc}{phone[1:]}"
    return phone


def clean_name(text: str) -> str:
    # Strip leftover separators, serial numbers, emails, and labels around the name.
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", text)
    text = re.sub(r"\b(ph|phone|tel|mobile|cell|contact|email|e-?mail|no|num|number)\b[.:#]?", "", text, flags=re.I)
    text = re.sub(r"^[\s\d.:\-–—|,;)]+", "", text)   # leading serial like "12. " or "3)"
    text = re.sub(r"^(to|from|sender|receiver|recipient)\b[.:\-]?\s+", "", text, flags=re.I)
    text = re.sub(r"[\s.:\-–—|,;(]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _contacts_from_line(line: str, prev_line: str) -> list[dict]:
    found = []
    matches = list(PHONE_RE.finditer(line))
    if not matches:
        return found
    # Name = the line with all phone matches removed
    name_part = line
    for m in matches:
        name_part = name_part.replace(m.group(1), " ")
    name = clean_name(name_part)
    if not name or not re.search(r"[A-Za-z\u0600-\u06FF]", name):
        # No name on this line — use the previous line if it looks like a name
        prev = clean_name(prev_line)
        if prev and not PHONE_RE.search(prev_line) and re.search(r"[A-Za-z\u0600-\u06FF]", prev):
            name = prev
        else:
            name = ""
    for m in matches:
        if ID_LABEL_RE.search(line[: m.start()]):
            continue  # labelled as an ID, not a phone number
        phone = normalize_phone(m.group(1))
        if phone:
            found.append({"name": name, "phone": phone})
    return found


def _contacts_from_text(text: str) -> list[dict]:
    found = []
    prev = ""
    for ln in [ln.strip() for ln in text.splitlines()]:
        if ln:
            found += _contacts_from_line(ln, prev)
        prev = ln
    return found


def _contacts_from_row(cells: list[str]) -> list[dict]:
    phones, names = [], []
    for c in cells:
        c = (c or "").strip()
        if not c:
            continue
        m = PHONE_RE.search(c)
        p = None
        if m and not ID_LABEL_RE.search(c[: m.start()]):
            p = normalize_phone(m.group(1))
        if p is None:
            # A spreadsheet cell may hold a bare number with no separators
            p = normalize_phone(c) if re.fullmatch(r"\+?\d{7,15}(\.0)?", c.replace(" ", "")) else None
        if p:
            phones.append(p)
        elif re.search(r"[A-Za-z\u0600-\u06FF]", c):
            names.append(clean_name(c))
    name = max(names, key=len) if names else ""
    return [{"name": name, "phone": p} for p in phones]


# Characters Tesseract commonly misreads inside numbers
_OCR_DIGIT_FIXES = str.maketrans({
    "O": "0", "o": "0", "D": "0",
    "l": "1", "I": "1", "|": "1", "i": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "B": "8",
    "g": "9",
})


_CONFUSABLE = set("OoDlI|iZzSsBg")


def _fix_ocr_digits(text: str) -> str:
    """Repair 0/O, 1/l, 5/S, 8/B confusions — but only inside tokens that are
    clearly meant to be numbers, never inside real words. A token qualifies
    when every non-digit letter in it is a known digit-lookalike."""
    def fix_token(tok: str) -> str:
        alnum = [c for c in tok if c.isalnum()]
        digits = sum(c.isdigit() for c in alnum)
        confusable = sum(c in _CONFUSABLE for c in alnum if not c.isdigit())
        others = len(alnum) - digits - confusable
        if digits >= 4 and others == 0 and digits + confusable >= MIN_DIGITS:
            return tok.translate(_OCR_DIGIT_FIXES)
        return tok

    return "\n".join(
        " ".join(fix_token(t) for t in line.split())
        for line in text.splitlines()
    )


def _preprocess_for_ocr(img):
    """Clean a photo up so Tesseract misreads less: fix rotation, grayscale,
    upscale small images, boost contrast, sharpen."""
    from PIL import Image, ImageFilter, ImageOps

    img = ImageOps.exif_transpose(img)  # phone photos are often rotated via EXIF
    if img.mode != "L":
        img = img.convert("L")
    if img.width < 1500:  # small photos -> characters too few pixels tall
        scale = 1500 / img.width
        img = img.resize((1500, max(1, round(img.height * scale))), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.SHARPEN)


def _phone_shaped(word: str) -> bool:
    """True when a token is mostly digits in phone-number quantity."""
    nonspace = [c for c in word if not c.isspace()]
    digits = sum(c.isdigit() for c in nonspace)
    return (
        MIN_DIGITS <= digits <= MAX_DIGITS
        and digits >= 0.7 * max(1, len(nonspace))
    )


def _ocr_image(img, min_conf: float = 40.0, config: str = "") -> str:
    """OCR an image, keeping only words Tesseract is reasonably confident about.
    Low-confidence words are where the garbage 'phone numbers' come from.
    Phone-shaped tokens get a lower bar (correct numbers sometimes score low);
    true junk tends to score near zero."""
    import pytesseract

    data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list[str]] = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf and not (conf >= 20.0 and _phone_shaped(word)):
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    return _fix_ocr_digits(text)


class Dedup:
    def __init__(self):
        self.contacts: list[dict] = []
        self.seen: set[str] = set()

    def add(self, items):
        for c in items:
            key = c["phone"]
            if key in self.seen:
                if c["name"]:
                    for existing in self.contacts:
                        if existing["phone"] == key and not existing["name"]:
                            existing["name"] = c["name"]
                continue
            self.seen.add(key)
            self.contacts.append(c)


def extract_from_pdf(path: str) -> list[dict]:
    dd = Dedup()
    ocr = tesseract_available()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    dd.add(_contacts_from_row([c or "" for c in row]))
            text = page.extract_text() or ""
            if text.strip():
                dd.add(_contacts_from_text(text))
            elif ocr:
                # Scanned page: render at 2x and OCR it
                img = page.to_image(resolution=200).original
                dd.add(_contacts_from_text(_ocr_image(img)))
    return dd.contacts


def extract_from_image(path: str) -> list[dict]:
    if not tesseract_available():
        raise RuntimeError("OCR engine (Tesseract) is not installed, so images can't be read yet.")
    from PIL import Image

    dd = Dedup()
    with Image.open(path) as img:
        pre = _preprocess_for_ocr(img)
        # Try progressively more permissive OCR modes and stop at the first hit:
        # default page mode, the unprocessed original (preprocessing can hurt
        # e.g. light-on-dark images), then sparse-text and single-block modes,
        # which find short snippets like a lone number on a sign or card.
        attempts = [
            (pre, 40.0, ""),
            (img, 30.0, ""),
            (pre, 25.0, "--psm 11"),
            (pre, 25.0, "--psm 6"),
        ]
        contacts = []
        for target, conf, cfg in attempts:
            contacts = _contacts_from_text(_ocr_image(target, min_conf=conf, config=cfg))
            if contacts:
                break
    dd.add(contacts)
    return dd.contacts


def extract_from_csv(path: str) -> list[dict]:
    dd = Dedup()
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        for row in csv.reader(f, dialect):
            dd.add(_contacts_from_row(row))
    return dd.contacts


def extract_from_xlsx(path: str) -> list[dict]:
    import openpyxl

    dd = Dedup()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = []
            for v in row:
                if v is None:
                    cells.append("")
                elif isinstance(v, float) and v.is_integer():
                    cells.append(str(int(v)))  # Excel stores numbers as floats
                else:
                    cells.append(str(v))
            dd.add(_contacts_from_row(cells))
    wb.close()
    return dd.contacts


def extract_any(path: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_from_pdf(path)
    if ext in IMAGE_EXTS:
        return extract_from_image(path)
    if ext == ".csv":
        return extract_from_csv(path)
    if ext in (".xlsx", ".xlsm"):
        return extract_from_xlsx(path)
    raise RuntimeError(f"Unsupported file type: {ext}")


# Kept for backwards compatibility
extract_contacts = extract_from_pdf


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(extract_any(sys.argv[1]), indent=2, ensure_ascii=False))
