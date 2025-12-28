# app.py
# Streamlit: Extract ALL lab rows from table-like PDFs and export to Excel
# Preserves values exactly as written in the PDF (commas, *, ++++, <, >, etc.)

import re
from io import BytesIO
from datetime import datetime

import pdfplumber
import pandas as pd
import streamlit as st


# ----------------------------
# Date parsing
# ----------------------------
DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")


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

    # 6 digits YYMMDD (e.g., 240115 => 15/01/2024)
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
# PDF word/line utilities
# ----------------------------
def normalize_spaces(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def words_by_line(words, y_tol=3.0):
    """
    Group words into lines by y ('top') proximity. Returns list[list[worddict]].
    """
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    cur = []
    cur_top = None

    for w in words_sorted:
        if cur_top is None:
            cur_top = w["top"]
            cur = [w]
            continue

        if abs(w["top"] - cur_top) <= y_tol:
            cur.append(w)
        else:
            lines.append(sorted(cur, key=lambda ww: ww["x0"]))
            cur_top = w["top"]
            cur = [w]

    if cur:
        lines.append(sorted(cur, key=lambda ww: ww["x0"]))
    return lines


def line_text(line_words) -> str:
    return normalize_spaces(" ".join(w["text"] for w in line_words))


def has_any_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in (s or ""))


def looks_like_section_title(s: str) -> bool:
    """
    Heuristic: section titles are usually text-only (no digits) and short-ish.
    Examples: "ΕΡΥΘΡΑ", "ΑΙΜΟΠΕΤΑΛΙΑ", "ΒΙΟΧΗΜΙΚΕΣ ΕΞΕΤΑΣΕΙΣ"
    """
    t = normalize_spaces(s)
    if not t:
        return False
    if has_any_digit(t):
        return False
    # If it's mostly letters/spaces and at least 3 chars
    if len(t) < 3:
        return False
    # Avoid catching headers like "ΓΕΝΙΚΗ ΕΞΕΤΑΣΗ ΑΙΜΑΤΟΣ" (still fine as a section)
    return True


def find_column_anchors(words):
    """
    Find x positions for table columns based on headers:
      - 'Εξέταση' (left column, not strictly needed)
      - 'Αποτέλεσμα' (result column start)
      - 'Τ. Αναφοράς' (reference column start)
    Returns dict with keys: x_result, x_ref
    """
    # Build a simple searchable list of (text_lower, x0)
    items = [(w["text"].lower(), w["x0"], w["top"]) for w in words]
    x_result = None
    x_ref = None

    # Find 'αποτέλεσμα'
    for txt, x0, _ in items:
        if "αποτέλεσμα" in txt:
            x_result = x0
            break

    # Find 'αναφοράς' or 'τ.' near it
    # Prefer word containing 'αναφοράς'
    for txt, x0, _ in items:
        if "αναφορά" in txt:
            x_ref = x0
            break

    # If not found, sometimes it is 'T. Αναφοράς' split into 'T.' and 'Αναφοράς'
    if x_ref is None:
        for txt, x0, _ in items:
            if txt in ("τ.", "t.", "t"):
                x_ref = x0
                break

    return {"x_result": x_result, "x_ref": x_ref}


def split_line_by_columns(line_words, x_result, x_ref):
    """
    Split a line into three strings by x thresholds:
      - name: words with x0 < x_result
      - result: x_result <= x0 < x_ref
      - ref: x0 >= x_ref
    If anchors are missing, returns None.
    """
    if x_result is None or x_ref is None:
        return None

    name_parts = []
    result_parts = []
    ref_parts = []

    for w in line_words:
        x0 = w["x0"]
        txt = w["text"]
        if x0 < x_result:
            name_parts.append(txt)
        elif x0 < x_ref:
            result_parts.append(txt)
        else:
            ref_parts.append(txt)

    name = normalize_spaces(" ".join(name_parts))
    result = normalize_spaces(" ".join(result_parts))
    ref = normalize_spaces(" ".join(ref_parts))

    # Basic filter: we want actual rows (a name + something numeric somewhere)
    if not name:
        return None
    if not (has_any_digit(result) or has_any_digit(ref)):
        return None

    return name, result, ref


# ----------------------------
# Main extraction
# ----------------------------
def extract_all_rows_from_pdf(file_obj, y_tol=3.0):
    """
    Extract ALL rows across pages using column anchors.
    Returns list of dicts with:
      page, section, exam, result_raw, ref_raw
    """
    rows = []

    with pdfplumber.open(file_obj) as pdf:
        current_section = None

        for page_index, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True) or []
            anchors = find_column_anchors(words)
            x_result = anchors["x_result"]
            x_ref = anchors["x_ref"]

            lines = words_by_line(words, y_tol=y_tol)

            # Update section name by scanning line titles
            for lw in lines:
                txt = line_text(lw)
                if looks_like_section_title(txt):
                    # Keep as section if it's not a column header line
                    low = txt.lower()
                    if "εξέταση" in low and "αποτέλεσμα" in low:
                        continue
                    current_section = txt

                # Skip header-ish lines
                low = txt.lower()
                if "εξέταση" in low and "αποτέλεσμα" in low:
                    continue

                # Attempt split by columns
                splitted = split_line_by_columns(lw, x_result, x_ref)
                if not splitted:
                    continue
                exam, result_raw, ref_raw = splitted

                # Filter out obvious non-test lines
                # e.g. "Σχόλια" or similar
                if exam.lower().startswith("σχό"):
                    continue

                rows.append(
                    {
                        "Σελίδα": page_index,
                        "Ενότητα": current_section,
                        "Εξέταση": exam,
                        "Αποτέλεσμα": result_raw,   # RAW as in PDF
                        "Τ. Αναφοράς": ref_raw,     # RAW as in PDF
                    }
                )

    return rows


def extract_date_for_file(file_obj, filename: str):
    """
    Prefer date from PDF text, else from filename.
    """
    try:
        with pdfplumber.open(file_obj) as pdf:
            full_text = []
            for p in pdf.pages:
                full_text.append(p.extract_text() or "")
            all_text = "\n".join(full_text)
        iso, disp = find_date_in_text(all_text)
        if iso:
            return iso, disp
    except Exception:
        pass
    return find_date_in_filename(filename)


def make_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot long results to wide:
      index: File + Date
      columns: Exam
      values: Result (raw)
    If duplicates exist for same exam/date/file, keeps the first non-empty.
    """
    base = df_long.copy()
    base["Result_for_pivot"] = base["Αποτέλεσμα"]

    # Remove empty exam names
    base = base[base["Εξέταση"].notna() & (base["Εξέταση"].str.strip() != "")]

    # Deduplicate: keep first occurrence per file/date/exam
    base = base.sort_values(["Αρχείο", "Ημερομηνία (ISO)", "Σελίδα"])
    base = base.drop_duplicates(subset=["Αρχείο", "Ημερομηνία (ISO)", "Εξέταση"], keep="first")

    wide = base.pivot_table(
        index=["Αρχείο", "Ημερομηνία (ISO)", "Ημερομηνία"],
        columns="Εξέταση",
        values="Result_for_pivot",
        aggfunc="first",
    ).reset_index()

    # Flatten columns
    wide.columns = [str(c) for c in wide.columns]
    return wide


def to_excel_bytes(df_long: pd.DataFrame, df_wide: pd.DataFrame) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_long.to_excel(writer, index=False, sheet_name="Long_All_Rows")
        df_wide.to_excel(writer, index=False, sheet_name="Wide_Results")
    return out.getvalue()


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Εξαγωγή ΟΛΩΝ των εξετάσεων από PDF", layout="wide")
st.title("Εξαγωγή ΟΛΩΝ των μικροβιολογικών τιμών από PDF σε Excel")
st.caption(
    "Εξάγει όλες τις γραμμές των πινάκων από τα PDF και κρατά τις τιμές όπως αναγράφονται "
    "(κόμματα, αστεράκια, σύμβολα, ++++, κ.λπ.)."
)

with st.sidebar:
    st.header("Ρυθμίσεις")
    debug_mode = st.toggle("Debug Mode", value=False)
    y_tol = st.slider(
        "y_tol (ευαισθησία ομαδοποίησης γραμμών)",
        min_value=2.0,
        max_value=6.0,
        value=3.0,
        step=0.5,
        help="Αν δεν 'κολλάει' σωστά τις λέξεις σε γραμμές, αύξησέ το.",
    )
    make_wide_view = st.toggle(
        "Δημιουργία Wide πίνακα (στήλες ανά εξέταση)",
        value=True,
        help="Εκτός από Long (όλες οι γραμμές), δημιουργεί και Wide (μία στήλη ανά εξέταση).",
    )

files = st.file_uploader("Ανέβασε ένα ή περισσότερα PDF", type=["pdf"], accept_multiple_files=True)

run = st.button("Έναρξη Εξαγωγής", type="primary", disabled=not files)

if run:
    all_rows = []
    debug_text = None

    with st.spinner("Εξάγω γραμμές από τα PDF..."):
        for i, f in enumerate(files):
            filename = getattr(f, "name", "uploaded.pdf")

            # Debug: show text snippet from first file
            if debug_mode and i == 0:
                try:
                    with pdfplumber.open(f) as pdf:
                        debug_text = (pdf.pages[0].extract_text() or "")[:1200]
                except Exception as e:
                    debug_text = f"[DEBUG ERROR] {e}"

            iso, disp = extract_date_for_file(f, filename)

            # Extract all rows
            rows = extract_all_rows_from_pdf(f, y_tol=y_tol)
            for r in rows:
                r.update(
                    {
                        "Αρχείο": filename,
                        "Ημερομηνία (ISO)": iso,
                        "Ημερομηνία": disp,
                    }
                )
            all_rows.extend(rows)

            try:
                f.seek(0)
            except Exception:
                pass

    if debug_mode:
        st.subheader("Debug (extract_text 1ης σελίδας / 1ου αρχείου)")
        st.code(debug_text or "")

    if not all_rows:
        st.error(
            "Δεν εντοπίστηκαν γραμμές πίνακα. Αν τα PDF είναι σκαναρισμένα (εικόνα χωρίς selectable text), "
            "θα χρειαστεί OCR. Αν δεν είναι σκαναρισμένα, δοκίμασε να αυξήσεις το y_tol."
        )
    else:
        df_long = pd.DataFrame(all_rows)

        # Order columns nicely
        cols = ["Αρχείο", "Ημερομηνία (ISO)", "Ημερομηνία", "Σελίδα", "Ενότητα", "Εξέταση", "Αποτέλεσμα", "Τ. Αναφοράς"]
        df_long = df_long[cols]

        st.subheader("Αποτελέσματα (Long: όλες οι γραμμές)")
        st.dataframe(df_long, use_container_width=True)

        df_wide = pd.DataFrame()
        if make_wide_view:
            df_wide = make_wide(df_long)
            st.subheader("Αποτελέσματα (Wide: μία στήλη ανά εξέταση)")
            st.dataframe(df_wide, use_container_width=True)

        xlsx = to_excel_bytes(df_long, df_wide if make_wide_view else pd.DataFrame())
        st.download_button(
            "Κατέβασμα Excel (.xlsx)",
            data=xlsx,
            file_name="lab_results_all_rows.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.markdown("---")
st.caption(
    "Σημείωση: Αν κάποιο PDF είναι σκαναρισμένο (εικόνα), το pdfplumber δεν θα βρει κείμενο. "
    "Τότε απαιτείται OCR (π.χ. Tesseract) — πες μου και θα σου δώσω έκδοση με OCR."
)
