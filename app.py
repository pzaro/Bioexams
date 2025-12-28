# app.py
# Streamlit app: Extract lab values from PDF by locating keywords and nearest value tokens
# Uses pdfplumber.extract_words() (positions) WITHOUT requiring table headers/anchors.
# Keeps values as written in PDF (commas, *, ++++, <, >, etc.)

import re
from io import BytesIO
from datetime import datetime

import pdfplumber
import pandas as pd
import streamlit as st


# ----------------------------
# Defaults / Aliases
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
    "Σάκχαρο",
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
]

TEST_ALIASES = {
    "Αιμοπετάλια": ["PLT", "Αιμοπετάλια"],
    "Αιμοσφαιρίνη": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά": ["WBC", "Λευκά"],
    "Αιματοκρίτης": ["HCT", "Αιματοκρίτης"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Ουρία": ["Ουρία"],
    "Κρεατινίνη": ["Κρεατινίνη"],
    "Ουρικό Οξύ": ["Ουρικό", "Ουρικό Οξύ"],
    "AST": ["SGOT", "AST"],
    "ALT": ["SGPT", "ALT"],
    "ALP": ["Αλκαλική", "ALP", "Φωσφατάση"],
    "γ-GT": ["γ-GT", "g-GT", "GGT"],
    "CPK": ["CPK", "Κρεατινοφωσφοκινάση"],
    "LDH": ["LDH"],
    "Σίδηρος": ["Σίδηρος"],
    "Φερριτίνη": ["Φερριτίνη"],
    "B12": ["B12", "Β12", "Βιταμίνη"],
    "Φυλλικό Οξύ": ["Φυλλικό", "Φυλλικό Οξύ"],
    "TSH": ["TSH"],
    "Βιταμίνη D": ["Βιταμίνη", "25-OHD", "25-OH"],
}

DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")

# Value token as it appears in lab reports:
# - numeric with optional decimal comma/dot and optional trailing *
# - < 0,01 style
# - plus signs + / ++ / +++ / ++++
# - common qualitative outputs
VALUE_TOKEN_RE = re.compile(
    r"""^(
        <\s*\d+(?:[.,]\d+)?\*? |
        \d+(?:[.,]\d+)?\*? |
        \+{1,4} |
        Ιχνη|Ίχνη|Trace|Σπάνια|ΟΧΙ|ΝΑΙ|Όχι|Ναι|Αρνητικό|Θετικό|αρνητικό|θετικό
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


# ----------------------------
# Date helpers
# ----------------------------
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
    m8 = re.search(r"(\d{8})", filename or "")
    if m8:
        s = m8.group(1)
        try:
            dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))
            return dt.date().isoformat(), dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

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
# PDF extraction helpers
# ----------------------------
def safe_open_pdf(file_obj):
    # Ensure pointer at start before pdfplumber reads
    try:
        file_obj.seek(0)
    except Exception:
        pass
    return pdfplumber.open(file_obj)


def extract_full_text(pdf_file) -> str:
    try:
        with safe_open_pdf(pdf_file) as pdf:
            parts = [(p.extract_text() or "") for p in pdf.pages]
        return "\n".join(parts)
    except Exception:
        return ""


def is_value_token(s: str) -> bool:
    t = (s or "").strip()
    # Normalize weird spaces
    t = t.replace("\u00A0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return bool(VALUE_TOKEN_RE.match(t))


def normalize_word(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()


def keyword_match(word_text: str, alias: str) -> bool:
    return alias.lower() in (word_text or "").lower()


def distance(a, b):
    # Euclidean-ish distance on (x0, top)
    return ((a["x0"] - b["x0"]) ** 2 + (a["top"] - b["top"]) ** 2) ** 0.5


def extract_value_by_proximity(pdf_file, aliases: list[str], y_band=4.0, x_min_gap=8.0):
    """
    For each page:
      - extract words with positions
      - find keyword word(s) that match aliases
      - collect candidate value tokens (raw) near keyword:
          a) same line band: abs(top - kw_top) <= y_band and x0 > kw_x1 + gap
          b) fallback: slightly below: 0 < (top - kw_top) <= 18 and x0 > kw_x1 + gap
      - pick nearest reasonable candidate (prefer same-line, then below)
    Returns raw value token string or None.
    """
    try:
        with safe_open_pdf(pdf_file) as pdf:
            for page in pdf.pages:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=True) or []
                # Normalize text field
                for w in words:
                    w["text"] = normalize_word(w.get("text", ""))

                # Identify keyword occurrences
                kw_words = []
                for w in words:
                    for a in aliases:
                        if keyword_match(w["text"], a):
                            kw_words.append(w)
                            break
                if not kw_words:
                    continue

                # Candidate value words
                value_words = [w for w in words if is_value_token(w["text"])]

                if not value_words:
                    continue

                # Evaluate candidates
                best = None  # (priority, dist, value_text)
                for kw in kw_words:
                    kw_top = kw["top"]
                    kw_x1 = kw["x1"]

                    for vw in value_words:
                        # Must be to the right (typically result column)
                        if vw["x0"] <= kw_x1 + x_min_gap:
                            continue

                        dy = vw["top"] - kw_top
                        # priority 0: same-line band
                        if abs(dy) <= y_band:
                            pr = 0
                        # priority 1: slightly below (broken line)
                        elif 0 < dy <= 18:
                            pr = 1
                        else:
                            continue

                        d = distance(kw, vw)
                        cand = (pr, d, vw["text"])
                        if best is None or cand < best:
                            best = cand

                if best:
                    return best[2]
    except Exception:
        return None

    return None


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    return bio.getvalue()


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Εξαγωγή εξετάσεων από PDF (Proximity)", layout="wide")
st.title("Εξαγωγή εξετάσεων από PDF σε Excel (Proximity)")
st.caption(
    "Εξάγει τιμές εντοπίζοντας τις λέξεις-κλειδιά και την πλησιέστερη τιμή δεξιά/λίγο κάτω. "
    "Δεν απαιτεί headers/anchors και κρατά τις τιμές όπως στο PDF."
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle("Debug Mode", value=False, help="Δείχνει στατιστικά/δείγμα κειμένου του 1ου PDF.")
    y_band = st.slider("y_band (ίδια γραμμή)", 2.0, 8.0, 4.0, 0.5)
    x_gap = st.slider("x_min_gap (δεξιά από keyword)", 0.0, 30.0, 8.0, 1.0)

    selected_tests = st.multiselect(
        "Επιλογή εξετάσεων",
        options=list(TEST_ALIASES.keys()),
        default=DEFAULT_TESTS,
    )

files = st.file_uploader("Ανέβασε PDF αρχεία", type=["pdf"], accept_multiple_files=True)
run = st.button("Έναρξη Εξαγωγής", type="primary", disabled=not files or not selected_tests)

if run:
    rows = []
    debug_info = None

    with st.spinner("Εξάγω δεδομένα..."):
        for i, f in enumerate(files):
            filename = getattr(f, "name", "uploaded.pdf")

            # Date: prefer text, fallback filename
            full_text = extract_full_text(f)
            iso, disp = find_date_in_text(full_text)
            if not iso:
                iso, disp = find_date_in_filename(filename)

            # Debug: for first file show if we actually get words/tokens
            if debug_mode and i == 0:
                try:
                    with safe_open_pdf(f) as pdf:
                        page0 = pdf.pages[0]
                        w = page0.extract_words(keep_blank_chars=False, use_text_flow=True) or []
                        sample_words = [normalize_word(x.get("text", "")) for x in w[:40]]
                        debug_info = {
                            "extract_text_first_600": (page0.extract_text() or "")[:600],
                            "words_count_page1": len(w),
                            "first_words_sample": sample_words,
                        }
                except Exception as e:
                    debug_info = {"error": str(e)}

            row = {"Αρχείο": filename, "Ημερομηνία (ISO)": iso, "Ημερομηνία": disp}

            for test in selected_tests:
                aliases = TEST_ALIASES.get(test, [test])
                val = extract_value_by_proximity(f, aliases, y_band=y_band, x_min_gap=x_gap)
                row[test] = val  # raw, as-is
            rows.append(row)

            try:
                f.seek(0)
            except Exception:
                pass

    if debug_mode:
        st.subheader("Debug (1ο PDF)")
        st.json(debug_info or {})

    df = pd.DataFrame(rows)
    df["_sort_date"] = pd.to_datetime(df["Ημερομηνία (ISO)"], errors="coerce")
    df = df.sort_values(["_sort_date", "Αρχείο"], na_position="last").drop(columns=["_sort_date"])

    st.subheader("Αποτελέσματα")
    st.dataframe(df, use_container_width=True)

    st.download_button(
        "Κατέβασμα Excel (.xlsx)",
        data=to_excel_bytes(df),
        file_name="exams_extracted_proximity.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
