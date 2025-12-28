import json
import re
from io import BytesIO
from datetime import datetime

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account


# -----------------------------
# Date helpers
# -----------------------------
DATE_PATTERN = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")


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

    # 6 digits YYMMDD (e.g. 240115 -> 15/01/2024)
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


# -----------------------------
# Google Vision client
# -----------------------------
def get_vision_client():
    """
    Expects Streamlit secret:
      GCP_SERVICE_ACCOUNT_JSON = """{...}"""
    """
    if "GCP_SERVICE_ACCOUNT_JSON" not in st.secrets:
        return None, (
            "Λείπει το Secret `GCP_SERVICE_ACCOUNT_JSON`.\n"
            "Βάλε στο Streamlit Cloud → Settings → Secrets το JSON του Service Account."
        )

    raw = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    try:
        info = json.loads(raw)
    except Exception:
        # sometimes user may paste as dict-like; try to be helpful
        return None, "Το `GCP_SERVICE_ACCOUNT_JSON` δεν είναι έγκυρο JSON."

    creds = service_account.Credentials.from_service_account_info(info)
    client = vision.ImageAnnotatorClient(credentials=creds)
    return client, None


# -----------------------------
# PDF -> Images (PyMuPDF)
# -----------------------------
def pdf_to_png_bytes_list(pdf_bytes: bytes, dpi: int = 220):
    """
    Render each page to PNG bytes using PyMuPDF.
    Works on Streamlit Cloud (no poppler needed).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    zoom = dpi / 72.0  # 72 is default
    mat = fitz.Matrix(zoom, zoom)

    for i in range(doc.page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        images.append((i + 1, pix.tobytes("png")))
    doc.close()
    return images


# -----------------------------
# OCR + parsing
# -----------------------------
def ocr_image_bytes(client: vision.ImageAnnotatorClient, png_bytes: bytes) -> str:
    image = vision.Image(content=png_bytes)
    response = client.text_detection(image=image)
    if response.error and response.error.message:
        raise RuntimeError(response.error.message)
    if not response.text_annotations:
        return ""
    return response.text_annotations[0].description or ""


VALUE_TOKEN = r"(<\s*\d+(?:[.,]\d+)?\*?|\d+(?:[.,]\d+)?\*?|\+{1,4}|Ιχνη|Ίχνη|Trace|Σπάνια|ΟΧΙ|ΝΑΙ|Όχι|Ναι|Αρνητικό|Θετικό|αρνητικό|θετικό)"
ROW_RE = re.compile(
    rf"""
    ^\s*
    (?P<exam>.+?)                 # everything up to the value
    \s+
    (?P<result>{VALUE_TOKEN})     # the value token (raw)
    (?:\s+(?P<ref>.+))?           # rest of line as reference (raw)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_ocr_text(text: str) -> str:
    t = (text or "").replace("\u00A0", " ")
    # keep line breaks; just normalize spaces
    t = re.sub(r"[ \t]+", " ", t)
    return t


def extract_rows_from_ocr_text(text: str):
    """
    Convert OCR full text into table-like rows.
    Heuristics:
      - parse by lines
      - keep lines that contain a value token
      - return exam/result/ref exactly as OCR produced
    """
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = ROW_RE.match(line)
        if not m:
            continue

        exam = (m.group("exam") or "").strip()
        result = (m.group("result") or "").strip()
        ref = (m.group("ref") or "").strip()

        # filter obvious non-test lines
        low = exam.lower()
        if low.startswith("σχό") or low.startswith("ερμηνε") or low.startswith("παρατη"):
            continue

        # exam should not be just a unit or a range
        if len(exam) < 2:
            continue

        rows.append(
            {
                "Εξέταση": exam,
                "Αποτέλεσμα": result,
                "Τ. Αναφοράς": ref,
                "Raw line": line,
            }
        )
    return rows


def make_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot to wide: 1 column per exam, raw result values.
    Deduplicate by keeping first occurrence per (file,date,exam).
    """
    base = df_long.copy()
    base = base[base["Εξέταση"].notna() & (base["Εξέταση"].str.strip() != "")]
    base = base.sort_values(["Αρχείο", "Ημερομηνία (ISO)", "Σελίδα"]).drop_duplicates(
        subset=["Αρχείο", "Ημερομηνία (ISO)", "Εξέταση"], keep="first"
    )

    wide = base.pivot_table(
        index=["Αρχείο", "Ημερομηνία (ISO)", "Ημερομηνία"],
        columns="Εξέταση",
        values="Αποτέλεσμα",
        aggfunc="first",
    ).reset_index()

    wide.columns = [str(c) for c in wide.columns]
    return wide


def to_excel_bytes(df_long: pd.DataFrame, df_wide: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df_long.to_excel(writer, index=False, sheet_name="Long_All_Rows")
        df_wide.to_excel(writer, index=False, sheet_name="Wide_Results")
    return bio.getvalue()


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="OCR PDF Εξετάσεων (Google Vision)", layout="wide")
st.title("OCR PDF Εξετάσεων → Excel (Google Vision)")
st.caption(
    "Για PDF που είναι εικόνες (χωρίς selectable text). Κρατά τις τιμές όπως αναγράφονται στο PDF."
)

client, err = get_vision_client()
if err:
    st.error(err)
    st.stop()

with st.sidebar:
    st.header("Ρυθμίσεις")
    dpi = st.slider("Ποιότητα render (DPI)", 150, 320, 220, 10)
    make_wide_view = st.toggle("Δημιουργία Wide πίνακα", value=True)
    debug_mode = st.toggle("Debug Mode", value=False)

files = st.file_uploader("Ανέβασε PDF αρχεία", type=["pdf"], accept_multiple_files=True)
run = st.button("Έναρξη OCR & Εξαγωγής", type="primary", disabled=not files)

if run:
    all_rows = []
    debug_payload = {}

    progress = st.progress(0)
    total_pages = 0

    # First pass: count pages
    file_pages = []
    for f in files:
        pdf_bytes = f.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pc = doc.page_count
        doc.close()
        file_pages.append((f.name, pdf_bytes, pc))
        total_pages += pc

    done = 0

    with st.spinner("Τρέχει OCR..."):
        for (fname, pdf_bytes, pc) in file_pages:
            # Render pages to images
            images = pdf_to_png_bytes_list(pdf_bytes, dpi=dpi)

            # OCR all pages and merge text (for date detection)
            ocr_text_pages = []
            for (pageno, png_bytes) in images:
                text = ocr_image_bytes(client, png_bytes)
                text = normalize_ocr_text(text)
                ocr_text_pages.append((pageno, text))

                done += 1
                progress.progress(min(1.0, done / max(1, total_pages)))

            full_ocr_text = "\n".join([t for _, t in ocr_text_pages])

            # Date: prefer OCR text; fallback filename
            iso, disp = find_date_in_text(full_ocr_text)
            if not iso:
                iso, disp = find_date_in_filename(fname)

            # Parse rows per page
            for pageno, page_text in ocr_text_pages:
                rows = extract_rows_from_ocr_text(page_text)
                for r in rows:
                    r.update(
                        {
                            "Αρχείο": fname,
                            "Ημερομηνία (ISO)": iso,
                            "Ημερομηνία": disp,
                            "Σελίδα": pageno,
                        }
                    )
                all_rows.extend(rows)

            if debug_mode and fname not in debug_payload:
                debug_payload[fname] = {
                    "ocr_text_first_1200": full_ocr_text[:1200],
                    "pages": pc,
                    "rows_extracted": len(all_rows),
                }

    progress.empty()

    if debug_mode:
        st.subheader("Debug")
        st.json(debug_payload)

    if not all_rows:
        st.error(
            "Δεν εντοπίστηκαν γραμμές εξετάσεων από το OCR κείμενο. "
            "Αυτό σημαίνει ότι χρειάζεται μικρή προσαρμογή του parser στο format του συγκεκριμένου εργαστηρίου."
        )
        st.stop()

    df_long = pd.DataFrame(all_rows)

    # Order columns
    cols = [
        "Αρχείο",
        "Ημερομηνία (ISO)",
        "Ημερομηνία",
        "Σελίδα",
        "Εξέταση",
        "Αποτέλεσμα",
        "Τ. Αναφοράς",
        "Raw line",
    ]
    df_long = df_long[[c for c in cols if c in df_long.columns]]

    # Sort
    df_long["_sort_date"] = pd.to_datetime(df_long["Ημερομηνία (ISO)"], errors="coerce")
    df_long = df_long.sort_values(["_sort_date", "Αρχείο", "Σελίδα"], na_position="last").drop(columns=["_sort_date"])

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
        file_name="lab_ocr_results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
