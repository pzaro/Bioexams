# app.py
# Streamlit app: Text-first extraction from PDF (pdfplumber.extract_text) + Regex
# - Always extracts dates (from text or filename)
# - Extracts selected lab values using flexible regex patterns
# - Keeps values as written in PDF (commas, *, ++++, <, >), NO float conversion

import re
from io import BytesIO
from datetime import datetime

import pdfplumber
import pandas as pd
import streamlit as st


# ----------------------------
# Keywords / Defaults
# ----------------------------
DEFAULT_TESTS = [
    "Αιμοπετάλια",
    "Αιμοσφαιρίνη",
    "Λευκά",
    "Αιματοκρίτης",
    "MCV",
    "MCH",
    "MCHC",
    "RDW",
    "Ουρία",
    "Κρεατινίνη",
    "Ουρικό Οξύ",
    "AST",
    "ALT",
    "ALP",
    "γ-GT",
    "CPK",
    "LDH",
    "Σίδηρος",
    "Φερριτίνη",
    "B12",
    "Φυλλικό Οξύ",
    "TSH",
    "Βιταμίνη D",
    "Σάκχαρο",
]

# You can add more tests/aliases here anytime
TEST_ALIASES = {
    "Αιμοπετάλια": ["PLT", "Αιμοπετάλια"],
    "Αιμοσφαιρίνη": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά": ["WBC", "Λευκά"],
    "Αιματοκρίτης": ["HCT", "Αιματοκρίτης"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "Ουρία": ["Ουρία"],
    "Κρεατινίνη": ["Κρεατινίνη"],
    "Ουρικό Οξύ": ["Ουρικό", "Ουρικό Οξύ"],
    "AST": ["SGOT", "AST"],
    "ALT": ["SGPT", "ALT"],
    "ALP": ["Αλκαλική Φωσφατάση", "ALP"],
    "γ-GT": ["γ-GT", "g-GT", "GGT", "γ -GT", "γ- GT"],
    "CPK": ["CPK", "Κρεατινοφωσφοκινάση"],
    "LDH": ["LDH"],
    "Σίδηρος": ["Σίδηρος"],
    "Φερριτίνη": ["Φερριτίνη"],
    "B12": ["B12", "Βιταμίνη B12", "Β12"],
    "Φυλλικό Οξύ": ["Φυλλικό", "Φυλλικό Οξύ"],
    "TSH": ["TSH"],
    "Βιταμίνη D": ["Βιταμίνη D", "25-OHD", "25-OH", "25-OH D", "25-OHD3"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
}

DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")


# ----------------------------
# Helpers
# ----------------------------
def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    # Keep newlines (useful), but normalize horizontal whitespace
    s = re.sub(r"[ \t]+", " ", s)
    return s


def extract_full_text(pdf_file) -> str:
    parts = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return normalize_text("\n".join(parts))


def find_date_in_text(text: str):
    m = DATE_PATTERN.search(text or "")
    if not m:
        return None, None
    dd, mm, yy = m.group(1), m.group(2), m.group(3)
    if len(yy) == 2:
        y = int(yy)
        yyyy = 2000 + y if y <= 79 else 1900 + y
    else:
        yyyy = int(yy)
    try:
        dt = datetime(yyyy, int(mm), int(dd))
        return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
    except ValueError:
        return None, None


def find_date_in_filename(filename: str):
    # 8 digits YYYYMMDD
    m8 = re.search(r"(\d{8})", filename or "")
    if m8:
        s = m8.group(1)
        try:
            dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))
            return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    # 6 digits YYMMDD (e.g. 240115 => 15/01/2024)
    m6 = re.search(r"(\d{6})", filename or "")
    if m6:
        s = m6.group(1)
        yy = int(s[0:2])
        mm = int(s[2:4])
        dd = int(s[4:6])
        yyyy = 2000 + yy if yy <= 79 else 1900 + yy
        try:
            dt = datetime(yyyy, mm, dd)
            return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    return None, None


def extract_value_text_first(full_text: str, aliases: list[str]):
    """
    Text-first regex extraction that works even when columns are broken.
    Strategy:
      1) Find a line that contains alias; if found, capture the first "value token" after it.
      2) If lines are broken, search a small window of text after alias occurrence.

    We keep the value "as written": e.g. 106*, 9,99, ++++, <0,01, Trace, etc.
    """

    # Define a permissive "value token":
    # - numbers with optional comma/point decimals + optional * (e.g., 106*, 9,99)
    # - or symbols/strings used in urinalysis: +, ++, +++, ++++, Ιχνη, Trace, Σπάνια, ΟΧΙ, ΝΑΙ
    value_token = r"(?P<val><\s*\d+[.,]?\d*\*?|\d+[.,]?\d*\*?|\+{1,4}|Ιχνη|Ίχνη|Trace|Σπάνια|ΟΧΙ|ΝΑΙ|Όχι|Ναι|αρνητικό|θετικό)"

    lines = (full_text or "").splitlines()

    # 1) Line-based pass
    for line in lines:
        low = line.lower()
        for a in aliases:
            if a.lower() in low:
                # Capture value token anywhere after the alias in the same line
                # Example: "PLT Αιμοπετάλια 106* 140-440"
                #          "WBC Λευκά Αιμοσφαίρια 9,99 4,0-10,0"
                pat = re.compile(re.escape(a) + r".{0,80}?" + value_token, flags=re.IGNORECASE)
                m = pat.search(line)
                if m:
                    return m.group("val").strip()

    # 2) Window-based pass (handles broken lines/columns)
    text = full_text or ""
    for a in aliases:
        for m0 in re.finditer(re.escape(a), text, flags=re.IGNORECASE):
            window = text[m0.end() : m0.end() + 120]  # look ahead
            m = re.search(value_token, window, flags=re.IGNORECASE)
            if m:
                return m.group("val").strip()

    return None


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    return bio.getvalue()


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Εξαγωγή εξετάσεων από PDF (Text-first)", layout="wide")

st.title("Εξαγωγή εξετάσεων από PDF σε Excel (Text-first)")
st.caption(
    "Επιστρέφει στην παλιά λογική: διαβάζει το κείμενο του PDF και εξάγει τιμές με regex. "
    "Οι τιμές κρατιούνται όπως αναγράφονται στο PDF (π.χ. 106*, 9,99, ++++)."
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle("Debug Mode", value=False, help="Δείχνει το κείμενο (πρώτοι 1200 χαρακτήρες) του 1ου PDF.")
    selected_tests = st.multiselect(
        "Επιλογή εξετάσεων",
        options=list(TEST_ALIASES.keys()),
        default=DEFAULT_TESTS,
    )

files = st.file_uploader("Ανέβασε PDF αρχεία", type=["pdf"], accept_multiple_files=True)

run = st.button("Έναρξη Εξαγωγής", type="primary", disabled=not files or not selected_tests)

if run:
    rows = []
    debug_text = None

    with st.spinner("Εξάγω δεδομένα..."):
        for i, f in enumerate(files):
            filename = getattr(f, "name", "uploaded.pdf")
            full_text = extract_full_text(f)

            # Debug for first file
            if debug_mode and i == 0:
                debug_text = full_text[:1200]

            iso_date, disp_date = find_date_in_text(full_text)
            if not iso_date:
                iso_date, disp_date = find_date_in_filename(filename)

            row = {
                "Αρχείο": filename,
                "Ημερομηνία (ISO)": iso_date,
                "Ημερομηνία": disp_date,
            }

            for test in selected_tests:
                aliases = TEST_ALIASES.get(test, [test])
                val = extract_value_text_first(full_text, aliases)
                row[test] = val

            rows.append(row)

            try:
                f.seek(0)
            except Exception:
                pass

    if debug_mode:
        st.subheader("Debug: κείμενο 1ου PDF (πρώτοι 1200 χαρακτήρες)")
        st.code(debug_text or "")

    df = pd.DataFrame(rows)

    # Sort by date if possible
    df["_sort_date"] = pd.to_datetime(df["Ημερομηνία (ISO)"], errors="coerce")
    df = df.sort_values(["_sort_date", "Αρχείο"], na_position="last").drop(columns=["_sort_date"])

    st.subheader("Αποτελέσματα")
    st.dataframe(df, use_container_width=True)

    xlsx = to_excel_bytes(df)
    st.download_button(
        "Κατέβασμα Excel (.xlsx)",
        data=xlsx,
        file_name="exams_extracted_text_first.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
