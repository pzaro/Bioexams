# ============================================================
# Medical Lab Commander — V15
# Document OCR (Vision) + Strict Metrics (robust picking)
# + Auto Extract (microbiology-friendly)
# Fix: WBC/percent-column mistakes (e.g., 62.5 instead of 10.25)
# ============================================================

import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re
import plotly.express as px
import scipy.stats as stats
import statsmodels.api as sm
from fpdf import FPDF
import tempfile
import os

# -------------------------
# 1) APP SETUP
# -------------------------
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
st.markdown("<h5 style='text-align: center;'>V15: Document OCR + Robust Metric Picking + Auto Extract</h5>", unsafe_allow_html=True)

# -------------------------
# 2) AUTH (GCP Vision)
# -------------------------
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Auth Error: {e}")
        return None

# -------------------------
# 3) TEXT / LINE HELPERS
# -------------------------
def normalize_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

# -------------------------
# 4) NUMBER CLEANING (Robust Greek/Intl)
# -------------------------
def clean_number(val_str: str):
    """
    Robust conversion supporting:
    - Greek decimals: 1.234,56 -> 1234.56
    - US decimals:    1,234.56 -> 1234.56
    - Simple:         10,25 -> 10.25
    - OCR junk: O->0, remove < > * etc.
    """
    if not val_str:
        return None

    s = val_str.strip()

    # Remove obvious junk
    s = s.replace('"', '').replace("'", "").replace(':', '')
    s = s.replace('*', '').replace('$', '')
    s = s.replace('≤', '').replace('≥', '')
    s = s.replace('<', '').replace('>', '')
    s = s.replace('O', '0').replace('o', '0')  # OCR
    s = s.replace('–', '-').replace('−', '-')  # minus variants

    # Keep only digits, separators, sign
    s = re.sub(r"[^0-9,.\-]", "", s)

    # Decide decimal separator if both exist
    if "," in s and "." in s:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            # Greek: '.' thousands, ',' decimal
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            # US: ',' thousands, '.' decimal
            s = s.replace(",", "")
    else:
        # Only comma => treat as decimal
        if "," in s and "." not in s:
            s = s.replace(".", "")
            s = s.replace(",", ".")

    s = s.strip()

    # Clean minus usage
    if s.count("-") > 1:
        s = s.replace("-", "")
    if "-" in s and not s.startswith("-"):
        s = s.replace("-", "")

    try:
        return float(s)
    except:
        return None

def find_first_number(s: str):
    vals = find_all_numbers(s)
    return vals[0] if vals else None

def find_all_numbers(s: str):
    """
    Extract *all* numeric candidates from a line, then clean/convert.
    """
    if not s:
        return []
    s_clean = s.replace('"', ' ').replace("'", " ").replace(':', ' ')

    # Captures:
    # - 1.234,56
    # - 1,234.56
    # - 1234,56
    # - 1234.56
    # - 1234
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

# -------------------------
# 5) OCR: PDF -> IMAGES -> Vision (document_text_detection)
# -------------------------
def ocr_pdf_to_text(client, pdf_bytes: bytes, dpi: int = 300) -> str:
    """
    PDF -> images -> Vision document_text_detection -> full text.
    dpi=300 is typically best for lab PDFs.
    """
    images = convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        fmt="png",
        grayscale=True
    )

    full_text = ""
    for img in images:
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG', optimize=True)
        content = img_byte_arr.getvalue()

        image = vision.Image(content=content)
        response = client.document_text_detection(image=image)

        if response.error.message:
            st.warning(f"OCR warning: {response.error.message}")

        if response.full_text_annotation and response.full_text_annotation.text:
            full_text += response.full_text_annotation.text + "\n"
        elif response.text_annotations:
            # fallback
            full_text += response.text_annotations[0].description + "\n"

    return full_text

# -------------------------
# 6) STRICT METRICS PARSER (Robust selection)
# -------------------------
def build_all_keywords(selected_metrics: dict):
    all_k = set()
    for k_list in selected_metrics.values():
        for k in k_list:
            all_k.add((k or "").upper().strip())
    return {k for k in all_k if k}

def keyword_hit(line_upper: str, kw: str) -> bool:
    """
    Safer matching: avoid hitting tiny tokens inside words.
    """
    kw = (kw or "").upper().strip()
    if not kw:
        return False

    # If keyword has space, do contains
    if " " in kw:
        return kw in line_upper

    # Word-boundary-ish on A-Z0-9 and Greek Α-Ω
    pattern = r"(?:^|[^A-Z0-9Α-Ω])" + re.escape(kw) + r"(?:$|[^A-Z0-9Α-Ω])"
    return re.search(pattern, line_upper) is not None

def pick_best_value(metric_name: str, values: list[float]):
    """
    Key fix: if OCR gives e.g. 62.5 (percent) near WBC, reject it and pick realistic WBC.
    """
    m = (metric_name or "").upper()
    values = [v for v in values if v is not None]

    if not values:
        return None

    # Heuristics per metric
    if "WBC" in m or "ΛΕΥΚ" in m:
        # Typical WBC: 3–20, rarely >30. This blocks 62.5% mistakes.
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
        # Platelets commonly 100–450, but allow wide range.
        vals = [v for v in values if 10 <= v <= 2000]
        # Prefer integers
        ints = [v for v in vals if abs(v - round(v)) < 1e-6]
        return ints[0] if ints else (vals[0] if vals else None)

    if m in ("MCV", "MCH", "MCHC", "RDW", "MPV", "PCT", "PDW"):
        # Allow reasonable positive values (avoid years)
        vals = [v for v in values if 0.001 <= v <= 1000]
        return vals[0] if vals else None

    # Default: return first candidate
    return values[0]

def parse_google_text_deep(full_text: str, selected_metrics: dict, debug: bool = False):
    """
    For each metric label, gather candidates from:
    - same line
    - subsequent lines until STOP LOGIC triggers or window ends
    Then pick_best_value(metric_name, candidates).
    """
    results = {}
    debug_rows = []

    lines = [normalize_line(x) for x in (full_text or "").split("\n")]
    lines = [x for x in lines if x]

    all_possible_keywords = build_all_keywords(selected_metrics)

    for metric_name, keywords in selected_metrics.items():
        current_keywords = [(k or "").upper().strip() for k in keywords]
        current_keywords = [k for k in current_keywords if k]

        found_value = None
        found_candidates = None
        found_at_line = None

        for i, line in enumerate(lines):
            line_upper = line.upper()

            if any(keyword_hit(line_upper, k) for k in current_keywords):
                candidates = []

                # same line candidates
                candidates += find_all_numbers(line)

                # deep look (up to 6 lines)
                for offset in range(1, 7):
                    if i + offset >= len(lines):
                        break
                    nxt = lines[i + offset]
                    nxt_upper = nxt.upper()

                    # STOP LOGIC: if next line contains another known keyword (not in current metric), stop.
                    found_other = False
                    for known_k in all_possible_keywords:
                        if known_k and (known_k not in current_keywords) and keyword_hit(nxt_upper, known_k):
                            found_other = True
                            break
                    if found_other:
                        break

                    candidates += find_all_numbers(nxt)

                val = pick_best_value(metric_name, candidates)

                # Additional last-line defenses (optional)
                if val is not None:
                    # block years (unless B12 is involved)
                    if (1990 < val < 2030) and ("B12" not in metric_name.upper()):
                        val = None

                found_value = val
                found_candidates = candidates
                found_at_line = line

                if found_value is not None:
                    results[metric_name] = found_value
                break

        if debug:
            debug_rows.append({
                "Metric": metric_name,
                "MatchedLine": found_at_line or "",
                "Candidates": ", ".join([str(x) for x in (found_candidates or [])]),
                "Picked": results.get(metric_name, None)
            })

    debug_df = pd.DataFrame(debug_rows) if debug else None
    return results, debug_df

# -------------------------
# 7) AUTO EXTRACT (microbiology-friendly)
# -------------------------
UNIT_RX = r"(?:mg/dL|g/dL|mmol/L|μmol/L|umol/L|IU/L|U/L|mIU/L|ng/mL|pg/mL|%|fL|pg|10\^3/μL|10\^3/uL|10\^6/μL|10\^6/uL|/μL|/uL|cells/μL|cfu/mL|CFU/mL)?"

def auto_extract_results(full_text: str):
    """
    Extract lines resembling:  LABEL  NUMBER  UNIT
    Keeps RawLine so you can preserve exact microbiology wording even when not numeric.
    """
    rows = []
    lines = [normalize_line(x) for x in (full_text or "").split("\n")]
    lines = [x for x in lines if x]

    for line in lines:
        if len(line) < 4:
            continue

        # Skip date-like lines
        if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
            continue

        m = re.search(
            r"^(.*?)([-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|[-]?\d+(?:[.,]\d+)?)(?:\s*(" + UNIT_RX + r"))?\s*$",
            line,
            flags=re.IGNORECASE
        )
        if not m:
            continue

        label = (m.group(1) or "").strip(" .:-")
        raw_num = m.group(2)
        unit = (m.group(3) or "").strip()

        if not label or len(label) < 2:
            continue

        val = clean_number(raw_num)
        if val is None:
            continue

        rows.append({
            "Test": label,
            "Value": val,
            "Unit": unit,
            "RawLine": line
        })

    if rows:
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["Test", "Value", "Unit", "RawLine"])
        return df

    return pd.DataFrame(columns=["Test", "Value", "Unit", "RawLine"])

# -------------------------
# 8) EXPORT (PDF/Excel)
# -------------------------
def create_pdf_report(df, chart_image_bytes):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "Medical Lab Report", 0, 1, 'C')
    pdf.ln(8)

    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 8, "Date", 1)
    pdf.cell(60, 8, "File", 1)
    pdf.cell(0, 8, "Values", 1, 1)

    pdf.set_font("Arial", '', 9)
    cols = df.columns.tolist()

    for _, row in df.iterrows():
        date_str = str(row.get('Date', ''))
        file_str = str(row.get('Αρχείο', ''))[:25]

        vals = []
        for c in cols:
            if c not in ['Date', 'Αρχείο'] and pd.notna(row.get(c, None)):
                vals.append(f"{c[:12]}:{row[c]}")
        vals_str = ", ".join(vals)

        pdf.cell(30, 8, date_str, 1)
        pdf.cell(60, 8, file_str, 1)
        pdf.multi_cell(0, 8, vals_str, 1)
        pdf.ln(1)

    pdf.ln(8)
    if chart_image_bytes:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
            tmp_file.write(chart_image_bytes)
            tmp_path = tmp_file.name
        try:
            pdf.image(tmp_path, x=10, w=190)
        except:
            pass
        os.remove(tmp_path)

    return pdf.output(dest='S').encode('latin-1', 'ignore')

def to_excel_with_chart(df, chart_fig):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Data', index=False)
        worksheet = writer.sheets['Data']
        workbook = writer.book
        center_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
        worksheet.set_column('A:AZ', 22, center_fmt)

        if chart_fig:
            try:
                img_bytes = chart_fig.to_image(format="png")
                image_data = io.BytesIO(img_bytes)
                worksheet.insert_image('H2', 'chart.png', {'image_data': image_data, 'x_scale': 0.6, 'y_scale': 0.6})
            except:
                pass
    return output.getvalue()

# -------------------------
# 9) STATISTICS
# -------------------------
def run_statistics(df, col_x, col_y):
    clean_df = df[[col_x, col_y]].apply(pd.to_numeric, errors='coerce').dropna()
    if len(clean_df) < 3:
        return f"⚠️ Need 3+ records (found {len(clean_df)}).", None, None

    x = clean_df[col_x]
    y = clean_df[col_y]

    if x.std() == 0 or y.std() == 0:
        return f"⚠️ Constant value.", None, None

    try:
        corr, p_value = stats.pearsonr(x, y)
        X = sm.add_constant(x)
        model = sm.OLS(y, X).fit()
        significance = "Significant" if p_value < 0.05 else "Not Significant"

        report = f"""
### 📊 Stats: {col_x} vs {col_y}
- **N:** {len(clean_df)}
- **Pearson r:** {corr:.4f}
- **P-value:** {p_value:.5f} ({significance})
- **R²:** {model.rsquared:.4f}
"""
        return report, clean_df, model
    except Exception as e:
        return f"Error: {str(e)}", None, None

# -------------------------
# 10) METRICS DB
# -------------------------
ALL_METRICS_DB = {
    # CBC basic
    "RBC (Ερυθρά)": ["RBC", "Ερυθρά"],
    "HGB (Αιμοσφαιρίνη)": ["HGB", "Αιμοσφαιρίνη"],
    "HCT (Αιματοκρίτης)": ["HCT", "Αιματοκρίτης"],
    "PLT (Αιμοπετάλια)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "WBC (Λευκά)": ["WBC", "Λευκά"],

    # Indices
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "MPV": ["MPV"],
    "PCT": ["PCT"],
    "PDW": ["PDW"],

    # Differential
    "NEUT (Ουδετερόφιλα)": ["NEUT", "Ουδετερόφιλα"],
    "LYMPH (Λεμφοκύτταρα)": ["LYMPH", "Λεμφοκύτταρα"],
    "MONO (Μονοπύρηνα)": ["MONO", "Μονοπύρηνα"],
    "EOS (Ηωσινόφιλα)": ["EOS", "Ηωσινόφιλα"],
    "BASO (Βασέοφιλα)": ["BASO", "Βασέοφιλα"],

    # Biochemistry
    "Σάκχαρο (GLU)": ["GLU", "GLUCOSE", "Σάκχαρο"],
    "Ουρία": ["UREA", "Ουρία"],
    "Κρεατινίνη": ["CREATININE", "CREA", "CR", "Κρεατινίνη"],
    "Χοληστερίνη": ["CHOLESTEROL", "CHOL", "Χοληστερίνη"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "Τριγλυκερίδια": ["TRIGLYCERIDES", "TRIG", "Τριγλυκερίδια"],
    "CRP": ["CRP", "Ποσοτική"],

    # Others
    "AST (SGOT)": ["AST", "SGOT"],
    "ALT (SGPT)": ["ALT", "SGPT"],
    "GGT": ["GGT", "γ-GT"],
    "Σίδηρος (Fe)": ["FE", "IRON", "Σίδηρος"],
    "Φερριτίνη": ["FERRITIN", "Φερριτίνη"],
    "B12": ["B12"],
    "Φυλλικό Οξύ": ["FOLIC", "Φυλλικό"],
    "Βιταμίνη D3": ["VIT D", "D3", "25-OH"],
    "TSH": ["TSH"],
    "PSA": ["PSA"],
}

# -------------------------
# 11) SESSION STATE
# -------------------------
if 'df_master' not in st.session_state:
    st.session_state.df_master = None
if 'auto_master' not in st.session_state:
    st.session_state.auto_master = None
if 'debug_master' not in st.session_state:
    st.session_state.debug_master = None

# -------------------------
# 12) SIDEBAR UI
# -------------------------
st.sidebar.header("⚙️ Settings")
uploaded_files = st.sidebar.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)

mode = st.sidebar.radio(
    "Extraction mode",
    ["Strict metrics (DB)", "Auto extract (all results)"],
    index=0
)

dpi = st.sidebar.slider("OCR quality (DPI)", 200, 400, 300, 50)
show_debug = st.sidebar.checkbox("Show debug table (Strict)", value=False)

all_keys = list(ALL_METRICS_DB.keys())
default_choices = ["PLT (Αιμοπετάλια)", "WBC (Λευκά)", "RBC (Ερυθρά)", "HGB (Αιμοσφαιρίνη)", "Σάκχαρο (GLU)"]
safe_defaults = [x for x in default_choices if x in all_keys]

container = st.sidebar.container()
select_all = st.sidebar.checkbox("Select All (Strict)")

if select_all:
    selected_metric_keys = container.multiselect("Metrics:", all_keys, default=all_keys)
else:
    selected_metric_keys = container.multiselect("Metrics:", all_keys, default=safe_defaults)

active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

# -------------------------
# 13) RUN EXTRACTION
# -------------------------
def extract_date_from_text_or_filename(full_text: str, filename: str):
    date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text or "")
    if date_match:
        return pd.to_datetime(date_match.group(1), dayfirst=True, errors='coerce')

    # fallback from filename YYMMDD (6 digits)
    m = re.search(r'(\d{6})', filename or "")
    if m:
        d_str = m.group(1)  # YYMMDD
        # Build dd/mm/20yy
        return pd.to_datetime(f"{d_str[4:6]}/{d_str[2:4]}/20{d_str[0:2]}", dayfirst=True, errors='coerce')

    return pd.NaT

if st.sidebar.button("🚀 START") and uploaded_files:
    client = get_vision_client()
    if not client:
        st.stop()

    all_data = []
    auto_data = []
    debug_tables = []

    bar = st.progress(0.0)

    for i, file in enumerate(uploaded_files):
        try:
            pdf_bytes = file.getvalue()
            full_text = ocr_pdf_to_text(client, pdf_bytes, dpi=dpi)

            the_date = extract_date_from_text_or_filename(full_text, file.name)

            if mode == "Strict metrics (DB)":
                data, dbg = parse_google_text_deep(full_text, active_metrics_map, debug=show_debug)
                data["Date"] = the_date
                data["Αρχείο"] = file.name
                all_data.append(data)

                if show_debug and dbg is not None:
                    dbg["Date"] = the_date
                    dbg["Αρχείο"] = file.name
                    debug_tables.append(dbg)

            else:
                df_auto = auto_extract_results(full_text)
                df_auto["Date"] = the_date
                df_auto["Αρχείο"] = file.name
                auto_data.append(df_auto)

        except Exception as e:
            st.error(f"Error {file.name}: {e}")

        bar.progress((i + 1) / len(uploaded_files))

    if mode == "Strict metrics (DB)":
        if all_data:
            st.session_state.df_master = pd.DataFrame(all_data).sort_values("Date")
            st.session_state.auto_master = None
            st.success("Done (Strict)!")
        else:
            st.warning("No data extracted in Strict mode.")
        if show_debug and debug_tables:
            st.session_state.debug_master = pd.concat(debug_tables, ignore_index=True)
        else:
            st.session_state.debug_master = None

    else:
        if auto_data:
            st.session_state.auto_master = pd.concat(auto_data, ignore_index=True).sort_values("Date")
            st.session_state.df_master = None
            st.session_state.debug_master = None
            st.success("Done (Auto)!")
        else:
            st.warning("No data extracted in Auto mode.")

# -------------------------
# 14) DASHBOARD — STRICT
# -------------------------
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()

    cols = ["Date", "Αρχείο"] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()

    display_df = final_df.copy()
    display_df["Date"] = pd.to_datetime(display_df["Date"], errors="coerce").dt.strftime("%d/%m/%Y")

    st.subheader("📋 Results (Strict)")
    st.dataframe(display_df, use_container_width=True)

    # Debug view (very useful to validate the fix)
    if st.session_state.debug_master is not None:
        st.subheader("🧪 Debug (Strict) — Matched line, candidates, picked value")
        dbg_show = st.session_state.debug_master.copy()
        dbg_show["Date"] = pd.to_datetime(dbg_show["Date"], errors="coerce").dt.strftime("%d/%m/%Y")
        st.dataframe(dbg_show[["Date", "Αρχείο", "Metric", "MatchedLine", "Candidates", "Picked"]], use_container_width=True)

    st.subheader("📈 Chart (Strict)")
    fig = None
    if len(cols) > 2:
        plot_df = final_df.melt(id_vars=["Date", "Αρχείο"], var_name="Metric", value_name="Value").dropna()
        fig = px.line(plot_df, x="Date", y="Value", color="Metric", markers=True, title="History")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Select metrics.")

    st.divider()
    st.subheader("🧮 Stats (Strict)")
    stat_cols = [c for c in cols if c not in ["Date", "Αρχείο"]]
    c1, c2 = st.columns(2)
    with c1:
        x_ax = st.selectbox("X", stat_cols, index=0 if len(stat_cols) > 0 else None)
    with c2:
        y_ax = st.selectbox("Y", stat_cols, index=1 if len(stat_cols) > 1 else 0)

    if st.button("Run Stats"):
        if x_ax and y_ax and x_ax != y_ax:
            rep, c_dat, mod = run_statistics(final_df, x_ax, y_ax)
            if c_dat is None:
                st.warning(rep)
            else:
                st.markdown(rep)
                fig_r = px.scatter(c_dat, x=x_ax, y=y_ax, trendline="ols", title=f"{x_ax} vs {y_ax}")
                st.plotly_chart(fig_r, use_container_width=True)

    st.divider()
    st.subheader("📥 Export (Strict)")
    ec1, ec2 = st.columns(2)
    with ec1:
        if fig is not None:
            try:
                xl = to_excel_with_chart(final_df, fig)
                st.download_button(
                    "📊 Excel (Strict)",
                    xl,
                    "report_strict.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except:
                st.warning("Excel chart export needs 'kaleido' installed for plotly image rendering.")
    with ec2:
        if fig is not None:
            try:
                img = fig.to_image(format="png")
                pdf = create_pdf_report(display_df, img)
                st.download_button("📄 PDF (Strict)", pdf, "report_strict.pdf", "application/pdf")
            except:
                st.warning("PDF export needs 'kaleido' installed for plotly image rendering.")

# -------------------------
# 15) DASHBOARD — AUTO
# -------------------------
if st.session_state.auto_master is not None:
    dfA = st.session_state.auto_master.copy()
    dfA["DateStr"] = pd.to_datetime(dfA["Date"], errors="coerce").dt.strftime("%d/%m/%Y")

    st.subheader("🧫 Auto Extract Results (Microbiology-friendly)")
    st.dataframe(dfA[["DateStr", "Αρχείο", "Test", "Value", "Unit", "RawLine"]], use_container_width=True)

    st.subheader("📈 Auto Chart")
    tests = sorted(dfA["Test"].dropna().unique().tolist())
    chosen_tests = st.multiselect("Select tests to chart", tests, default=tests[:3] if len(tests) >= 3 else tests)

    figA = None
    if chosen_tests:
        chart_df = dfA[dfA["Test"].isin(chosen_tests)].copy()
        chart_df["Date"] = pd.to_datetime(chart_df["Date"], errors="coerce")
        chart_df = chart_df.dropna(subset=["Date", "Value"])
        figA = px.line(chart_df, x="Date", y="Value", color="Test", markers=True, title="Auto History")
        figA.update_layout(title_x=0.5)
        st.plotly_chart(figA, use_container_width=True)
    else:
        st.info("Select tests to chart.")

    st.divider()
    st.subheader("📥 Export (Auto)")
    ec1, ec2 = st.columns(2)
    with ec1:
        if figA is not None:
            try:
                xl = to_excel_with_chart(dfA, figA)
                st.download_button(
                    "📊 Excel (Auto)",
                    xl,
                    "report_auto.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except:
                st.warning("Excel chart export needs 'kaleido' installed for plotly image rendering.")
    with ec2:
        if figA is not None:
            try:
                img = figA.to_image(format="png")
                # Pivot for PDF readability
                pivot = dfA.pivot_table(
                    index=["DateStr", "Αρχείο"],
                    columns="Test",
                    values="Value",
                    aggfunc="first"
                ).reset_index()
                pivot.columns = [str(c) for c in pivot.columns]
                pivot = pivot.rename(columns={"DateStr": "Date"})
                pdf = create_pdf_report(pivot, img)
                st.download_button("📄 PDF (Auto)", pdf, "report_auto.pdf", "application/pdf")
            except:
                st.warning("PDF export needs 'kaleido' installed for plotly image rendering.")
