# app.py
# Streamlit app: Extract lab values from PDF tables using word positions (pdfplumber.extract_words)
# and export to Excel.
#
# Requirements: streamlit, pdfplumber, pandas, openpyxl, re

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
    "B12": ["B12", "Βιταμίνη", "Β12"],
    "TSH": ["TSH"],
}

DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")

# Numeric token (supports comma decimal and optional trailing symbols like * )
NUM_PATTERN = re.compile(r"^[-+]?\d+(?:[.,]\d+)?\*?$")


# ----------------------------
# Helpers: Dates
# ----------------------------
def find_date_in_text(raw_text: str):
    m = DATE_PATTERN.search(raw_text or "")
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

    # 6 digits YYMMDD
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


# ----------------------------
# Helpers: Cleaning
# ----------------------------
def clean_value_to_float_or_none(value: str):
    """
    Convert string like '106*', '12,8', '9.99' -> float.
    Return None if not parseable.
    """
    if value is None:
        return None
    v = value.strip().replace("*", "").replace(" ", "")
    v = v.replace(",", ".")
    v = re.sub(r"[^0-9\.\-\+]", "", v)
    if re.fullmatch(r"[\-\+]?\d+(\.\d+)?", v or ""):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()


# ----------------------------
# PDF Extraction Core
# ----------------------------
def extract_debug_text(pdf_file, max_chars=800):
    """
    Debug: show beginning of extracted text for page 1 (if available).
    Useful to confirm extract_text returns something.
    """
    try:
        with pdfplumber.open(pdf_file) as pdf:
            if not pdf.pages:
                return ""
            t = pdf.pages[0].extract_text() or ""
            return t[:max_chars]
    except Exception as e:
        return f"[DEBUG ERROR] {e}"


def words_by_line(words, y_tol=3.0):
    """
    Group extracted words into "lines" based on their 'top' coordinate.
    Returns list of lines, each is a list of word dicts (sorted by x0).
    """
    if not words:
        return []

    # Sort by vertical position then horizontal
    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    current = []
    current_top = None

    for w in words_sorted:
        if current_top is None:
            current_top = w["top"]
            current = [w]
            continue

        if abs(w["top"] - current_top) <= y_tol:
            current.append(w)
        else:
            # finalize line
            lines.append(sorted(current, key=lambda ww: ww["x0"]))
            current_top = w["top"]
            current = [w]

    if current:
        lines.append(sorted(current, key=lambda ww: ww["x0"]))

    return lines


def line_text(line_words):
    return normalize_spaces(" ".join(w["text"] for w in line_words))


def is_numeric_token(token: str) -> bool:
    t = (token or "").strip()
    return bool(NUM_PATTERN.match(t))


def extract_value_from_pdf_tables(pdf_file, keywords: list[str], y_tol=3.0):
    """
    Robust extraction for table PDFs:
      - Use page.extract_words() to preserve positions.
      - Find a line that contains one of the keywords.
      - From that same line, pick the first numeric token to the RIGHT of the keyword (by x position).
    Returns first found numeric string token (e.g., '106*', '12,8', '9,99') or None.
    """
    kw_lower = [k.lower() for k in keywords]

    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                # Extract words with positions
                words = page.extract_words(
                    keep_blank_chars=False,
                    use_text_flow=True,  # helps some PDFs preserve reading order
                )
                lines = words_by_line(words, y_tol=y_tol)

                for lw in lines:
                    txt = line_text(lw).lower()
                    if not txt:
                        continue

                    # Find which keyword matches this line
                    matched_kw = None
                    for k in kw_lower:
                        if k in txt:
                            matched_kw = k
                            break
                    if not matched_kw:
                        continue

                    # Identify keyword span: approximate by finding first word that contains keyword substring
                    # then use its x1 as left boundary for result tokens.
                    boundary_x = None
                    for w in lw:
                        if matched_kw in w["text"].lower():
                            boundary_x = w["x1"]
                            break

                    # If keyword spans multiple words (e.g., "Λευκά Αιμοσφαίρια"), boundary might be early;
                    # improve: set boundary to the max x1 of all words participating in the keyword phrase.
                    # Simple heuristic: extend boundary for adjacent words until line still contains keyword tokens.
                    if boundary_x is None:
                        boundary_x = lw[0]["x1"]

                    # Candidate numeric tokens on the right side
                    candidates = []
                    for w in lw:
                        if w["x0"] > boundary_x + 5:  # small gap
                            if is_numeric_token(w["text"]):
                                candidates.append((w["x0"], w["text"]))

                    # If found numeric candidates, take the leftmost one (result column)
                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        return candidates[0][1]

                    # Fallback: sometimes value is not a standalone word token (e.g., "106*" glued),
                    # or split weirdly. Try regex over whole line and take first numeric.
                    m = re.search(r"([-+]?\d+(?:[.,]\d+)?\*?)", line_text(lw))
                    if m:
                        return m.group(1)

    except Exception:
        # If pdfplumber fails, return None (handled upstream)
        return None

    return None


def extract_date_from_pdf(pdf_file):
    """
    Try extract date from full text across all pages.
    """
    try:
        with pdfplumber.open(pdf_file) as pdf:
            full_text = []
            for page in pdf.pages:
                full_text.append(page.extract_text() or "")
            all_text = "\n".join(full_text)
    except Exception:
        return None, None

    iso_date, disp = find_date_in_text(all_text)
    return iso_date, disp


# ----------------------------
# Build Results
# ----------------------------
def build_results_dataframe(files, selected_tests, debug_mode=False):
    rows = []
    debug_payload = None

    for i, f in enumerate(files):
        filename = getattr(f, "name", "uploaded.pdf")

        # Debug: show extract_text snippet from first file
        if debug_mode and i == 0:
            debug_payload = extract_debug_text(f, max_chars=800)

        # Date: prefer PDF text; fallback to filename
        iso_date, disp_date = extract_date_from_pdf(f)
        if not iso_date:
            iso_date, disp_date = find_date_in_filename(filename)

        row = {
            "Αρχείο": filename,
            "Ημερομηνία (ISO)": iso_date,
            "Ημερομηνία": disp_date,
        }

        for test_name in selected_tests:
            keywords = TEST_KEYWORDS.get(test_name, [test_name])

            raw_val = extract_value_from_pdf_tables(f, keywords, y_tol=3.0)
            row[test_name] = clean_value_to_float_or_none(raw_val)

        rows.append(row)

        # Reset pointer for Streamlit re-reads
        try:
            f.seek(0)
        except Exception:
            pass

    df = pd.DataFrame(rows)
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
    "Για PDF που είναι πίνακες (Εξέταση | Αποτέλεσμα | Τιμές αναφοράς). "
    "Η εξαγωγή γίνεται με θέσεις λέξεων (x/y) ώστε να πιάνει αξιόπιστα το αποτέλεσμα."
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle(
        "Debug Mode",
        value=False,
        help="Δείχνει τους πρώτους ~800 χαρακτήρες του extract_text() της 1ης σελίδας του 1ου αρχείου.",
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

        if debug_mode:
            st.markdown("### Debug Output (extract_text από 1η σελίδα / 1ο αρχείο)")
            st.code(debug_payload or "")

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
    "Αν κάποια τιμή δεν εντοπίζεται, συνήθως οφείλεται σε διαφορετική ονομασία/συντομογραφία. "
    "Πρόσθεσε keyword στο TEST_KEYWORDS. Αν πάλι δεν πιάνει, θα χρειαστεί μικρή ρύθμιση του y_tol ή "
    "του κανόνα επιλογής της τιμής (αποτέλεσμα vs reference)."
)
