import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re
import scipy.stats as stats
from fpdf import FPDF
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import tempfile
import os

# =========================
# 1) SETUP
# =========================
st.set_page_config(page_title="Medical Lab Commander", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
html, body, .stDataFrame { font-family: 'Roboto', sans-serif; }
.stDataFrame td, .stDataFrame th { text-align: center !important; vertical-align: middle !important; }
.stDataFrame th { background-color: #ff4b4b !important; color: white !important; }
h1, h2, h3 { text-align: center; }
</style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h5 style='text-align: center;'>V17: Strict Only + Print PDF (Table + Chart) + PLT Default</h5>", unsafe_allow_html=True)

# =========================
# 2) AUTH
# =========================
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Auth Error: {e}")
        return None

# =========================
# 3) HELPERS
# =========================
def normalize_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def clean_number(val_str: str):
    if not val_str:
        return None

    s = val_str.strip()
    s = s.replace('"', '').replace("'", "").replace(':', '')
    s = s.replace('*', '').replace('$', '').replace('≤', '').replace('≥', '')
    s = s.replace('<', '').replace('>', '')
    s = s.replace('O', '0').replace('o', '0')
    s = s.replace('–', '-').replace('−', '-')
    s = re.sub(r"[^0-9,.\-]", "", s)

    if "," in s and "." in s:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        if "," in s and "." not in s:
            s = s.replace(".", "")
            s = s.replace(",", ".")

    s = s.strip()
    if s.count("-") > 1:
        s = s.replace("-", "")
    if "-" in s and not s.startswith("-"):
        s = s.replace("-", "")

    try:
        return float(s)
    except:
        return None

def find_all_numbers(s: str):
    if not s:
        return []
    s_clean = s.replace('"', ' ').replace("'", " ").replace(':', ' ')
    candidates = re.findall(
        r"[-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|[-]?\d+(?:[.,]\d+)?",
        s_clean
    )
    out = []
    for c in candidates:
        v = clean_number(c)
        if v is not None:
            out.append(v)
    return out

def keyword_hit(line_upper: str, kw: str) -> bool:
    kw = (kw or "").upper().strip()
    if not kw:
        return False

    if " " in kw:
        return kw in line_upper

    # Πιάνει: R B C / R.B.C / R-B-C / W B C κ.λπ.
    if 2 <= len(kw) <= 5 and re.fullmatch(r"[A-Z0-9]+", kw):
        spaced = r"\W*".join(list(map(re.escape, kw)))
        if re.search(spaced, line_upper):
            return True

    pattern = r"(?:^|[^A-Z0-9Α-Ω])" + re.escape(kw) + r"(?:$|[^A-Z0-9Α-Ω])"
    return re.search(pattern, line_upper) is not None

def pick_best_value(metric_name: str, values: list[float]):
    m = (metric_name or "").upper()
    values = [v for v in values if v is not None]
    if not values:
        return None

    # Κλείδωμα WBC ώστε να μην παίρνει ποσοστά (π.χ. 62.5)
    if "WBC" in m or "ΛΕΥΚ" in m:
        vals = [v for v in values if 0.1 <= v <= 30]
        return vals[0] if vals else None

    if "RBC" in m or "ΕΡΥΘ" in m:
        vals = [v for v in values if 1.0 <= v <= 8.0]
        return vals[0] if vals else None

    if "HGB" in m or "ΑΙΜΟΣΦ" in m:
        vals = [v for v in values if 5.0 <= v <= 25.0]
        return vals[0] if vals else None

    if "HCT" in m or "ΑΙΜΑΤΟΚ" in m:
        vals = [v for v in values if 10.0 <= v <= 70.0]
        return vals[0] if vals else None

    if "PLT" in m or "ΑΙΜΟΠΕΤ" in m or "PLATE" in m:
        vals = [v for v in values if 10 <= v <= 2000]
        ints = [v for v in vals if abs(v - round(v)) < 1e-6]
        return ints[0] if ints else (vals[0] if vals else None)

    return values[0]

def parse_google_text_deep(full_text: str, selected_metrics: dict, debug: bool = False):
    results = {}
    debug_rows = []

    lines = [normalize_line(x) for x in (full_text or "").split("\n")]
    lines = [x for x in lines if x]

    all_possible_keywords = set()
    for k_list in selected_metrics.values():
        for k in k_list:
            if k:
                all_possible_keywords.add(k.upper().strip())

    for metric_name, keywords in selected_metrics.items():
        current_keywords = [k.upper().strip() for k in keywords if k]

        found_at_line = ""
        candidates = []
        picked = None

        for i, line in enumerate(lines):
            line_upper = line.upper()

            if any(keyword_hit(line_upper, k) for k in current_keywords):
                found_at_line = line
                candidates = []
                candidates += find_all_numbers(line)

                # λίγο μεγαλύτερο lookahead για RBC (λόγω πινάκων)
                max_lookahead = 10 if "RBC" in metric_name.upper() else 7

                for offset in range(1, max_lookahead):
                    if i + offset >= len(lines):
                        break

                    nxt = lines[i + offset]
                    nxt_upper = nxt.upper()

                    # STOP αν μπήκαμε σε άλλη εξέταση
                    found_other = False
                    for known_k in all_possible_keywords:
                        if known_k not in current_keywords and keyword_hit(nxt_upper, known_k):
                            found_other = True
                            break
                    if found_other:
                        break

                    candidates += find_all_numbers(nxt)

                picked = pick_best_value(metric_name, candidates)

                # κόψε “έτη” 1990-2030 εκτός B12
                if picked is not None and (1990 < picked < 2030) and ("B12" not in metric_name.upper()):
                    picked = None

                if picked is not None:
                    results[metric_name] = picked
                break

        if debug:
            debug_rows.append({
                "Metric": metric_name,
                "MatchedLine": found_at_line,
                "Candidates": ", ".join([str(x) for x in candidates]),
                "Picked": results.get(metric_name, None)
            })

    dbg_df = pd.DataFrame(debug_rows) if debug else None
    return results, dbg_df

# =========================
# 4) OCR
# =========================
def ocr_pdf_to_text(client, pdf_bytes: bytes, dpi: int = 300) -> str:
    images = convert_from_bytes(pdf_bytes, dpi=dpi, fmt="png", grayscale=True)

    full_text = ""
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        content = buf.getvalue()

        image = vision.Image(content=content)
        response = client.document_text_detection(image=image)

        if response.error.message:
            st.warning(f"OCR warning: {response.error.message}")

        if response.full_text_annotation and response.full_text_annotation.text:
            full_text += response.full_text_annotation.text + "\n"
        elif response.text_annotations:
            full_text += response.text_annotations[0].description + "\n"

    return full_text

def extract_date_from_text_or_filename(full_text: str, filename: str):
    date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text or "")
    if date_match:
        return pd.to_datetime(date_match.group(1), dayfirst=True, errors='coerce')

    m = re.search(r'(\d{6})', filename or "")
    if m:
        d_str = m.group(1)
        return pd.to_datetime(f"{d_str[4:6]}/{d_str[2:4]}/20{d_str[0:2]}", dayfirst=True, errors='coerce')

    return pd.NaT

# =========================
# 5) CHART (matplotlib -> PNG bytes)
# =========================
def make_chart_png(final_df: pd.DataFrame, metric_cols: list[str]) -> bytes | None:
    """
    Δημιουργεί ένα απλό line chart ανά εξέταση (όσες είναι επιλεγμένες),
    με άξονα Χ την ημερομηνία.
    Επιστρέφει PNG bytes για ενσωμάτωση στο PDF.
    """
    if final_df is None or final_df.empty:
        return None
    if not metric_cols:
        return None

    dfc = final_df.copy()
    dfc["Date"] = pd.to_datetime(dfc["Date"], errors="coerce")
    dfc = dfc.dropna(subset=["Date"]).sort_values("Date")

    # κράτα μόνο στήλες που υπάρχουν και έχουν τουλάχιστον 1 τιμή
    usable = []
    for c in metric_cols:
        if c in dfc.columns and pd.to_numeric(dfc[c], errors="coerce").notna().any():
            usable.append(c)
    if not usable:
        return None

    fig, ax = plt.subplots(figsize=(8.27, 4.5), dpi=150)  # ~A4 width, nice height
    for c in usable:
        y = pd.to_numeric(dfc[c], errors="coerce")
        ax.plot(dfc["Date"], y, marker="o", linewidth=1.5, label=c)

    ax.set_title("Ιστορικό Μετρήσεων")
    ax.set_xlabel("Ημερομηνία")
    ax.set_ylabel("Τιμή")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%Y"))
    fig.autofmt_xdate(rotation=45)

    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(loc="best", fontsize=8)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

# =========================
# 6) PDF (Print-ready: Table + Chart page)
# =========================
def create_print_pdf(display_df: pd.DataFrame, chart_png_bytes: bytes | None, title: str = "Medical Lab Report"):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)

    # ---- Page 1: Table ----
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, title, ln=True, align="C")
    pdf.ln(2)

    pdf.set_font("Arial", "B", 9)
    cols = list(display_df.columns)

    # widths: Date μικρό, File μεγάλο, metrics μεσαία
    col_widths = []
    for c in cols:
        if c.lower() == "date":
            col_widths.append(25)
        elif c == "Αρχείο":
            col_widths.append(70)
        else:
            col_widths.append(30)

    # header
    for c, w in zip(cols, col_widths):
        pdf.cell(w, 8, str(c)[:20], border=1, align="C")
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    for _, row in display_df.iterrows():
        for c, w in zip(cols, col_widths):
            val = "" if pd.isna(row[c]) else str(row[c])
            pdf.cell(w, 8, val[:35], border=1, align="C")
        pdf.ln()

    # ---- Page 2: Chart ----
    if chart_png_bytes:
        pdf.add_page()
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 10, "Γράφημα", ln=True, align="C")
        pdf.ln(2)

        # write png to temp file (FPDF expects a path)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(chart_png_bytes)
            tmp_path = tmp.name

        try:
            # fit to A4 width with margins
            pdf.image(tmp_path, x=10, w=190)
        finally:
            try:
                os.remove(tmp_path)
            except:
                pass

    return pdf.output(dest="S").encode("latin-1", "ignore")

# =========================
# 7) STATS + THEORY (Pearson)
# =========================
def stats_method_explanation():
    return """
**Μέθοδος συσχέτισης: Pearson correlation (r)**

Χρησιμοποιείται όταν συγκρίνουμε **δύο συνεχείς αριθμητικές μεταβλητές** και θέλουμε να μετρήσουμε την **γραμμική** τους σχέση.
- r ∈ [-1, +1]
- r > 0: θετική γραμμική συσχέτιση
- r < 0: αρνητική γραμμική συσχέτιση
- r ≈ 0: απουσία γραμμικής συσχέτισης

**Έλεγχος σημαντικότητας (p-value):**
- H0: ρ = 0 (καμία συσχέτιση στον πληθυσμό)
- Αν p < 0.05: ένδειξη στατιστικά σημαντικής συσχέτισης

**Γιατί επιλέχθηκε εδώ:**
Τα εργαστηριακά μεγέθη (PLT, WBC κ.λπ.) είναι συνεχείς τιμές και η βασική ερώτηση είναι αν συν-μεταβάλλονται γραμμικά.

**Περιορισμοί:**
- Ευαισθησία σε outliers
- Με μικρό N, η p-value είναι ασταθής
- Αν η σχέση είναι μη-γραμμική αλλά μονοτονική, ο Pearson μπορεί να υποεκτιμήσει τη σχέση

**Εναλλακτική:**
Spearman rho (rank-based) για outliers/μη-κανονικότητα ή μονοτονική σχέση.
"""

def run_statistics_pearson(df, col_x, col_y):
    clean_df = df[[col_x, col_y]].apply(pd.to_numeric, errors='coerce').dropna()
    if len(clean_df) < 3:
        return f"⚠️ Χρειάζονται 3+ μετρήσεις (βρέθηκαν {len(clean_df)}).", None

    x = clean_df[col_x]
    y = clean_df[col_y]
    if x.std() == 0 or y.std() == 0:
        return "⚠️ Σταθερή τιμή σε μία από τις δύο μεταβλητές (μηδενική διακύμανση).", None

    corr, p_value = stats.pearsonr(x, y)
    return {"N": len(clean_df), "Pearson r": corr, "p-value": p_value}, clean_df

# =========================
# 8) METRICS DB (Strict only)
# =========================
ALL_METRICS_DB = {
    "PLT (Αιμοπετάλια)": ["PLT", "Platelets", "Αιμοπετάλια"],
    "WBC (Λευκά)": ["WBC", "Λευκά"],
    "RBC (Ερυθρά)": ["RBC", "R.B.C", "ERY", "ER", "Ερυθρά", "Ερυθροκύτταρα", "Ερυθροκυτταρ"],
    "HGB (Αιμοσφαιρίνη)": ["HGB", "H.B.G", "Αιμοσφαιρίνη"],
    "HCT (Αιματοκρίτης)": ["HCT", "Αιματοκρίτης"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "MPV": ["MPV"],
    "PCT": ["PCT"],
    "PDW": ["PDW"],
    "Σάκχαρο (GLU)": ["GLU", "GLUCOSE", "Σάκχαρο"],
    "CRP": ["CRP", "Ποσοτική"],
}

# =========================
# 9) SESSION STATE
# =========================
if "df_master" not in st.session_state:
    st.session_state.df_master = None
if "debug_master" not in st.session_state:
    st.session_state.debug_master = None

# =========================
# 10) SIDEBAR
# =========================
st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Ανέβασε PDF", type="pdf", accept_multiple_files=True)

dpi = st.sidebar.slider("Ποιότητα OCR (DPI)", 200, 400, 300, 50)
show_debug = st.sidebar.checkbox("Εμφάνιση Debug", value=False)

all_keys = list(ALL_METRICS_DB.keys())

# Προεπιλογή ΜΟΝΟ PLT
default_selected = ["PLT (Αιμοπετάλια)"]

selected_metric_keys = st.sidebar.multiselect(
    "Εξετάσεις:",
    all_keys,
    default=default_selected
)

active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

if st.sidebar.button("🚀 START") and uploaded_files:
    client = get_vision_client()
    if not client:
        st.stop()

    all_data = []
    debug_tables = []
    bar = st.progress(0.0)

    for i, file in enumerate(uploaded_files):
        try:
            pdf_bytes = file.getvalue()
            full_text = ocr_pdf_to_text(client, pdf_bytes, dpi=dpi)

            data, dbg = parse_google_text_deep(full_text, active_metrics_map, debug=show_debug)

            the_date = extract_date_from_text_or_filename(full_text, file.name)
            data["Date"] = the_date
            data["Αρχείο"] = file.name
            all_data.append(data)

            if show_debug and dbg is not None:
                dbg["Date"] = the_date
                dbg["Αρχείο"] = file.name
                debug_tables.append(dbg)

        except Exception as e:
            st.error(f"Error {file.name}: {e}")

        bar.progress((i + 1) / len(uploaded_files))

    if all_data:
        st.session_state.df_master = pd.DataFrame(all_data).sort_values("Date")
        st.success("Done!")
    else:
        st.warning("Δεν εξήχθησαν δεδομένα.")

    if show_debug and debug_tables:
        st.session_state.debug_master = pd.concat(debug_tables, ignore_index=True)
    else:
        st.session_state.debug_master = None

# =========================
# 11) DASHBOARD
# =========================
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()

    cols = ["Date", "Αρχείο"] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()

    display_df = final_df.copy()
    display_df["Date"] = pd.to_datetime(display_df["Date"], errors="coerce").dt.strftime("%d/%m/%Y")

    st.subheader("📋 Αποτελέσματα")
    st.dataframe(display_df, use_container_width=True)

    # Debug
    if st.session_state.debug_master is not None:
        st.subheader("🧪 Debug (Διάγνωση εξαγωγής)")
        dbg_show = st.session_state.debug_master.copy()
        dbg_show["Date"] = pd.to_datetime(dbg_show["Date"], errors="coerce").dt.strftime("%d/%m/%Y")
        st.dataframe(dbg_show[["Date", "Αρχείο", "Metric", "MatchedLine", "Candidates", "Picked"]], use_container_width=True)

    st.divider()

    # Chart in UI
    st.subheader("📈 Γράφημα")
    metric_cols = [c for c in cols if c not in ["Date", "Αρχείο"]]
    chart_png = make_chart_png(final_df, metric_cols)

    if chart_png:
        st.image(chart_png, caption="Ιστορικό Μετρήσεων", use_container_width=True)
    else:
        st.info("Δεν υπάρχουν αρκετά δεδομένα για γράφημα (διάλεξε 1+ εξετάσεις με τιμές).")

    st.divider()

    # PDF PRINT BUTTON (Table + Chart)
    st.subheader("🖨️ Εκτύπωση")
    pdf_bytes = create_print_pdf(display_df, chart_png, title="Medical Lab Report (Print)")
    st.download_button(
        "📄 Δημιουργία PDF για Εκτύπωση (Πίνακας + Γράφημα)",
        data=pdf_bytes,
        file_name="medical_lab_print.pdf",
        mime="application/pdf"
    )

    st.divider()

    # Stats section
    st.subheader("🧮 Συσχέτιση / Στατιστική")
    stat_cols = [c for c in cols if c not in ["Date", "Αρχείο"]]
    if len(stat_cols) >= 2:
        c1, c2 = st.columns(2)
        with c1:
            x_ax = st.selectbox("X (μεταβλητή)", stat_cols, index=0)
        with c2:
            y_ax = st.selectbox("Y (μεταβλητή)", stat_cols, index=1)

        if st.button("Run Correlation"):
            if x_ax == y_ax:
                st.warning("Διάλεξε δύο διαφορετικές μεταβλητές.")
            else:
                st.markdown(stats_method_explanation())
                res, clean_df = run_statistics_pearson(final_df, x_ax, y_ax)
                if clean_df is None:
                    st.warning(res)
                else:
                    st.write({
                        "N": res["N"],
                        "Pearson r": round(res["Pearson r"], 4),
                        "p-value": round(res["p-value"], 6),
                    })
    else:
        st.info("Για συσχέτιση χρειάζονται τουλάχιστον 2 επιλεγμένες εξετάσεις.")
