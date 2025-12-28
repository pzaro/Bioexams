# app.py
# Streamlit app: Extract lab values from "CSV-like quoted" PDF text and export to Excel
# Libraries: streamlit, pdfplumber, pandas, re, openpyxl

import re
from io import BytesIO
from datetime import datetime

import pdfplumber
import pandas as pd
import streamlit as st


# ----------------------------
# Configuration / Keywords
# ----------------------------
DEFAULT_TESTS = [
    "Αιμοπετάλια",
    "Αιμοσφαιρίνη",
    "Λευκά",
    "Σάκχαρο",
    "Χοληστερίνη",
    "Φερριτίνη",
    "B12",
    "TSH",
]

TEST_KEYWORDS = {
    "Αιμοπετάλια": ["PLT", "Αιμοπετάλια"],
    "Αιμοσφαιρίνη": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά": ["WBC", "Λευκά"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Χοληστερίνη"],
    "Σίδηρος": ["Σίδηρος"],
    "Φερριτίνη": ["Φερριτίνη"],
    "B12": ["B12", "Βιταμίνη B12", "Β12"],
    "TSH": ["TSH"],
}

# Regex pattern template (critical requirement)
# r'"[^"]*KEYWORD[^"]*"\s*,\s*"([^"]*)"'
REGEX_TEMPLATE = r'"[^"]*{kw}[^"]*"\s*,\s*"([^"]*)"'

DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")


# ----------------------------
# Helpers
# ----------------------------
def extract_text_from_pdf(uploaded_file) -> str:
    """
    Read all pages text from PDF via pdfplumber.
    """
    text_parts = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            # extract_text may return None on some PDFs; handle gracefully
            t = page.extract_text() or ""
            text_parts.append(t)
    return "\n".join(text_parts)


def normalize_text_for_csv_like_parsing(raw_text: str) -> str:
    """
    Normalize whitespace while preserving quotes/commas structure.
    PDFs often split rows with newlines, multiple spaces, etc.
    """
    # Replace weird non-breaking spaces, unify newlines
    t = raw_text.replace("\u00A0", " ")
    # Collapse excessive whitespace (but keep quotes/commas intact)
    t = re.sub(r"[ \t]+", " ", t)
    # Keep newlines as separators, but also allow regex to match across them
    return t


def clean_value_to_float_or_text(value: str):
    """
    - Remove symbols like $, * and extra spaces
    - Convert comma decimal to dot decimal
    - Try to parse as float (supports ints too)
    - If not numeric, return cleaned text
    """
    if value is None:
        return None

    v = value.strip()

    # Remove common decorations: currency, asterisks, etc.
    # Keep digits, comma, dot, minus, plus; remove other symbols/spaces
    # But do it cautiously: first remove obvious markers, then trim again.
    v = v.replace("$", "").replace("*", "").strip()

    # If value contains thousands separators or embedded spaces, remove spaces
    v = v.replace(" ", "")

    # Convert decimal comma to decimal point (e.g., "13,2" -> "13.2")
    v = v.replace(",", ".")

    # Some values might be like "157**" or "7,1%" in other PDFs; strip trailing non-numeric
    v_numeric_candidate = re.sub(r"[^0-9\.\-\+]", "", v)

    # Try float parse if it looks like a number
    if re.fullmatch(r"[\-\+]?\d+(\.\d+)?", v_numeric_candidate or ""):
        try:
            return float(v_numeric_candidate)
        except ValueError:
            pass

    # Return as text fallback
    return v


def find_date_in_text(raw_text: str):
    """
    Find the first date in DD/MM/YY or DD/MM/YYYY format in the PDF text.
    Return as ISO string YYYY-MM-DD, and also a display DD/MM/YYYY.
    """
    m = DATE_PATTERN.search(raw_text)
    if not m:
        return None, None

    dd, mm, yy = m.group(1), m.group(2), m.group(3)
    if len(yy) == 2:
        # Assumption: 00-79 => 2000-2079, 80-99 => 1980-1999 (common heuristic)
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
    """
    If no date in text, parse from filename like NAME-240115.pdf => 15/01/2024.
    Accepts patterns with 6 digits (YYMMDD) or 8 digits (YYYYMMDD).
    """
    # Look for 8 digits first
    m8 = re.search(r"(\d{8})", filename)
    if m8:
        s = m8.group(1)
        try:
            dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))
            return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    # Look for 6 digits (YYMMDD)
    m6 = re.search(r"(\d{6})", filename)
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


def extract_value_for_keywords(raw_text: str, keywords: list[str]):
    """
    Apply the critical CSV-like regex pattern per keyword.
    Returns first match found (value string), else None.
    """
    # Make regex resilient across line breaks: allow whitespace (\s) to include \n
    # Use DOTALL? Not required because pattern uses [^"]*, which doesn't cross quotes;
    # but \s* around comma can cross newlines.
    for kw in keywords:
        safe_kw = re.escape(kw)
        pattern = re.compile(REGEX_TEMPLATE.format(kw=safe_kw), flags=re.IGNORECASE)
        m = pattern.search(raw_text)
        if m:
            return m.group(1)
    return None


def build_results_dataframe(files, selected_tests, debug_mode=False):
    """
    For each PDF:
      - extract all text
      - normalize
      - find date (text first, fallback filename)
      - extract selected test values
    """
    rows = []
    debug_payload = None

    for i, f in enumerate(files):
        filename = getattr(f, "name", "uploaded.pdf")
        raw_text = extract_text_from_pdf(f)
        norm_text = normalize_text_for_csv_like_parsing(raw_text)

        # Debug: show first 500 chars of FIRST file text (as requested)
        if debug_mode and i == 0:
            debug_payload = norm_text[:500]

        iso_date, display_date = find_date_in_text(norm_text)
        if not iso_date:
            iso_date, display_date = find_date_in_filename(filename)

        row = {
            "Αρχείο": filename,
            "Ημερομηνία (ISO)": iso_date,
            "Ημερομηνία": display_date,
        }

        for test_name in selected_tests:
            keywords = TEST_KEYWORDS.get(test_name, [test_name])
            raw_val = extract_value_for_keywords(norm_text, keywords)
            cleaned = clean_value_to_float_or_text(raw_val) if raw_val is not None else None
            row[test_name] = cleaned

        rows.append(row)

        # Reset file pointer for safety if Streamlit reuses objects
        try:
            f.seek(0)
        except Exception:
            pass

    df = pd.DataFrame(rows)

    # Sort by date if available
    if "Ημερομηνία (ISO)" in df.columns:
        df["_sort_date"] = pd.to_datetime(df["Ημερομηνία (ISO)"], errors="coerce")
        df = df.sort_values(["_sort_date", "Αρχείο"], na_position="last").drop(columns=["_sort_date"])

    return df, debug_payload


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """
    Write dataframe to an in-memory .xlsx using openpyxl engine.
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    return output.getvalue()


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Εξαγωγή Εξετάσεων από PDF σε Excel", layout="wide")

st.title("Εξαγωγή Μικροβιολογικών Εξετάσεων από PDF σε Excel")
st.caption(
    "Ανιχνεύει τιμές από PDF όπου το περιεχόμενο είναι αποθηκευμένο σαν CSV με εισαγωγικά "
    '(π.χ. "PLT Αιμοπετάλια","222","140-440").'
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle("Debug Mode", value=False, help="Δείχνει τους πρώτους 500 χαρακτήρες του κειμένου του 1ου αρχείου.")
    selected_tests = st.multiselect(
        "Επιλογή εξετάσεων",
        options=list(TEST_KEYWORDS.keys()),
        default=DEFAULT_TESTS,
    )

st.subheader("1) Ανέβασμα PDF")
files = st.file_uploader(
    "Επίλεξε ένα ή περισσότερα PDF αρχεία",
    type=["pdf"],
    accept_multiple_files=True,
)

st.subheader("2) Εξαγωγή")

run = st.button("Έναρξη Εξαγωγής", type="primary", disabled=not files or not selected_tests)

if run:
    if not files:
        st.error("Δεν ανέβασες αρχεία PDF.")
    elif not selected_tests:
        st.error("Δεν επέλεξες εξετάσεις.")
    else:
        with st.spinner("Εξάγω δεδομένα από τα PDF..."):
            df, debug_payload = build_results_dataframe(files, selected_tests, debug_mode=debug_mode)

        if debug_mode and debug_payload is not None:
            st.markdown("### Debug Output (πρώτοι 500 χαρακτήρες)")
            st.code(debug_payload)

        st.markdown("### Αποτελέσματα")
        st.dataframe(df, use_container_width=True)

        xlsx_bytes = dataframe_to_excel_bytes(df)
        st.download_button(
            label="Κατέβασμα Excel (.xlsx)",
            data=xlsx_bytes,
            file_name="exams_extracted.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.markdown("---")
st.caption(
    "Σημείωση: Αν κάποια τιμή δεν εντοπίζεται, συνήθως σημαίνει ότι στο συγκεκριμένο PDF "
    "η εξέταση έχει διαφορετική ονομασία/συντομογραφία. Πρόσθεσε νέο keyword στο TEST_KEYWORDS."
)
