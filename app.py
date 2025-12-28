# app.py
# Streamlit app: Extract lab values from PDF table-like text and export to Excel
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
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Σίδηρος": ["Σίδηρος", "Iron"],
    "Φερριτίνη": ["Φερριτίνη", "Ferritin"],
    "B12": ["B12", "Βιταμίνη B12", "Β12"],
    "TSH": ["TSH"],
}


DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")


# ----------------------------
# Helpers
# ----------------------------
def extract_text_from_pdf(uploaded_file) -> str:
    """
    Read all pages text from PDF via pdfplumber.
    Works for table-like PDFs where text is selectable (not pure scanned images).
    """
    text_parts = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_parts.append(t)
    return "\n".join(text_parts)


def normalize_text(raw_text: str) -> str:
    """
    Normalize whitespace and common PDF artifacts.
    """
    t = raw_text.replace("\u00A0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return t


def clean_value_to_float_or_text(value: str):
    """
    - Remove symbols like $, * and extra spaces
    - Convert comma decimal to dot decimal
    - Try parse float; else return cleaned text
    """
    if value is None:
        return None

    v = value.strip()
    v = v.replace("$", "").replace("*", "").strip()
    v = v.replace(" ", "")
    v = v.replace(",", ".")

    # keep only numeric-sign-dot
    v_numeric_candidate = re.sub(r"[^0-9\.\-\+]", "", v)

    if re.fullmatch(r"[\-\+]?\d+(\.\d+)?", v_numeric_candidate or ""):
        try:
            return float(v_numeric_candidate)
        except ValueError:
            pass

    return v


def find_date_in_text(raw_text: str):
    """
    Find first date in text: DD/MM/YY or DD/MM/YYYY.
    Return (iso_date YYYY-MM-DD, display_date DD/MM/YYYY).
    """
    m = DATE_PATTERN.search(raw_text)
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
    """
    Parse date from filename:
      - 8 digits (YYYYMMDD)
      - 6 digits (YYMMDD) e.g. 240115 => 15/01/2024
    Return (iso_date, display_date).
    """
    m8 = re.search(r"(\d{8})", filename)
    if m8:
        s = m8.group(1)
        try:
            dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))
            return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

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


def extract_value_from_table(raw_text: str, keywords: list[str]):
    """
    Extract the first numeric value from lines that contain one of the keywords.
    Works for table-like PDFs where text is extracted as lines, e.g.:
      "PLT Αιμοπετάλια 106* 140-440"
      "HGB Αιμοσφαιρίνη 12,8 12-16"
      "WBC Λευκά Αιμοσφαίρια 9,99 4,0-10,0"
    Strategy:
      1) Find a line containing a keyword.
      2) Extract the first numeric token AFTER keyword match (more robust than first number in line).
    """
    lines = raw_text.splitlines()

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        for kw in keywords:
            if kw.lower() in line_stripped.lower():
                # Extract numbers in order; pick the first plausible "result"
                # Many lines begin with code/letters then value; we take the first numeric token.
                m = re.search(r"([-+]?\d+(?:[.,]\d+)?)", line_stripped)
                if m:
                    return m.group(1)

    return None


def build_results_dataframe(files, selected_tests, debug_mode=False):
    rows = []
    debug_payload = None

    for i, f in enumerate(files):
        filename = getattr(f, "name", "uploaded.pdf")

        raw_text = extract_text_from_pdf(f)
        norm_text = normalize_text(raw_text)

        # Debug: show first 500 chars of first file
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
            raw_val = extract_value_from_table(norm_text, keywords)
            row[test_name] = clean_value_to_float_or_text(raw_val) if raw_val is not None else None

        rows.append(row)

        # Reset pointer (Streamlit may reuse file object)
        try:
            f.seek(0)
        except Exception:
            pass

    df = pd.DataFrame(rows)

    # Sort by date if possible
    if "Ημερομηνία (ISO)" in df.columns:
        df["_sort_date"] = pd.to_datetime(df["Ημερομηνία (ISO)"], errors="coerce")
        df = df.sort_values(["_sort_date", "Αρχείο"], na_position="last").drop(columns=["_sort_date"])

    return df, debug_payload


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
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
    "Εξάγει τιμές από PDF που περιέχουν πίνακες (Εξέταση | Αποτέλεσμα | Τιμές αναφοράς) "
    "και αποθηκεύει τα αποτελέσματα σε Excel."
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle(
        "Debug Mode",
        value=False,
        help="Δείχνει τους πρώτους 500 χαρακτήρες του κειμένου του 1ου αρχείου.",
    )
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
    "Αν κάποια τιμή δεν εντοπίζεται, ενεργοποίησε το Debug Mode και πρόσθεσε/διόρθωσε keywords στο TEST_KEYWORDS."
)
