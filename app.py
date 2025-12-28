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
st.markdown("<h5 style='text-align: center;'>V14: Document OCR + Auto Extract (Microbiology-friendly)</h5>", unsafe_allow_html=True)

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
# 3) NUMBER CLEANING (Robust Greek/Intl)
# =========================
def clean_number(val_str: str):
    if not val_str:
        return None

    s = val_str.strip()
    s = s.replace('"', '').replace("'", "").replace(':', '')
    s = s.replace('*', '').replace('$', '').replace('≤', '').replace('≥', '')
    s = s.replace('<', '').replace('>', '')
    s = s.replace('O', '0').replace('o', '0')  # OCR
    s = s.replace('–', '-').replace('−', '-')  # minus variants

    # Κράτα μόνο digits, separators, sign
    s = re.sub(r"[^0-9,.\-]", "", s)

    # Αν υπάρχουν ΚΑΙ κόμμα ΚΑΙ τελεία: αποφάσισε ποιο είναι decimal
    # Π.χ. 1.234,56 -> decimal=,  /  1,234.56 -> decimal=.
    if "," in s and "." in s:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            # Greek style: '.' thousands, ',' decimal
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            # US style: ',' thousands, '.' decimal
            s = s.replace(",", "")
    else:
        # Αν μόνο κόμμα: το θεωρούμε decimal
        if "," in s and "." not in s:
            s = s.replace(".", "")
            s = s.replace(",", ".")

    # Τελικός καθαρισμός (κρατάμε 1 minus στην αρχή)
    s = s.strip()
    s = re.sub(r"^(?!-)", "", s)  # no-op safety
    # Σβήσε extra '-' αν υπάρχουν στη μέση
    if s.count("-") > 1:
        s = s.replace("-", "")
    # Αν '-' δεν είναι στην αρχή, βγάλε το
    if "-" in s and not s.startswith("-"):
        s = s.replace("-", "")

    try:
        return float(s)
    except:
        return None

def find_first_number(s: str):
    if not s:
        return None
    s_clean = s.replace('"', ' ').replace("'", " ").replace(':', ' ')
    # Πιάνει 1.234,56 / 1234,56 / 1234.56 / 1234
    candidates = re.findall(r"[-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|[-]?\d+(?:[.,]\d+)?", s_clean)
    for c in candidates:
        v = clean_number(c)
        if v is not None:
            return v
    return None

# =========================
# 4) OCR ENGINE
# =========================
def ocr_pdf_to_text(client, pdf_bytes: bytes, dpi: int = 300):
    """
    PDF -> images -> Vision document_text_detection -> full text
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
            # Δεν σταματάμε, αλλά το εμφανίζουμε
            st.warning(f"OCR warning: {response.error.message}")

        if response.full_text_annotation and response.full_text_annotation.text:
            full_text += response.full_text_annotation.text + "\n"
        elif response.text_annotations:
            # fallback
            full_text += response.text_annotations[0].description + "\n"

    return full_text

def normalize_line(s: str):
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def build_all_keywords(selected_metrics: dict):
    all_k = set()
    for k_list in selected_metrics.values():
        for k in k_list:
            all_k.add(k.upper().strip())
    return all_k

def keyword_hit(line_upper: str, kw: str):
    """
    Word-boundary-ish hit:
    - Αν το kw είναι αλφαριθμητικό, θέλουμε να μη βαράει μέσα σε λέξεις.
    """
    kw = kw.upper().strip()
    if not kw:
        return False

    # Αν έχει κενό (π.χ. "FE "), κάνε απλό contains
    if " " in kw:
        return kw in line_upper

    # Για μικρά tokens τύπου NE/EO/BA, θέλουμε σύνορα
    # \b σε unicode μπορεί να είναι περίεργο, αλλά εδώ βοηθάει αρκετά.
    pattern = r"(?:^|[^A-Z0-9Α-Ω])" + re.escape(kw) + r"(?:$|[^A-Z0-9Α-Ω])"
    return re.search(pattern, line_upper) is not None

def parse_google_text_deep(full_text: str, selected_metrics: dict):
    results = {}
    lines = [normalize_line(x) for x in full_text.split("\n")]
    lines = [x for x in lines if x]

    all_possible_keywords = build_all_keywords(selected_metrics)

    for metric_name, keywords in selected_metrics.items():
        metric_found = False
        current_keywords = [k.upper().strip() for k in keywords]

        for i, line in enumerate(lines):
            line_upper = line.upper()

            if any(keyword_hit(line_upper, k) for k in current_keywords):
                # 1) ίδια γραμμή
                val = find_first_number(line)

                # 2) deep search με stop logic (έως 6 γραμμές)
                if val is None:
                    for offset in range(1, 7):
                        if i + offset >= len(lines):
                            break
                        nxt = lines[i + offset]
                        nxt_upper = nxt.upper()

                        # STOP: αν δω άλλον keyword (όχι από το current metric)
                        found_other = False
                        for known_k in all_possible_keywords:
                            if known_k and (known_k not in current_keywords) and keyword_hit(nxt_upper, known_k):
                                found_other = True
                                break
                        if found_other:
                            break

                        val = find_first_number(nxt)
                        if val is not None:
                            break

                if val is not None:
                    # Light sanity filters (όπως είχες)
                    if (1990 < val < 2030) and ("B12" not in metric_name.upper()):
                        continue
                    if "PLT" in metric_name.upper() and val < 10:
                        continue
                    if "WBC" in metric_name.upper() and val > 100:
                        continue
                    if "HGB" in metric_name.upper() and val > 25:
                        continue

                    results[metric_name] = val
                    metric_found = True
                    break

        if metric_found:
            continue

    return results

# =========================
# 5) AUTO EXTRACT (all results lines)
# =========================
UNIT_RX = r"(?:mg/dL|g/dL|mmol/L|μmol/L|umol/L|IU/L|U/L|mIU/L|ng/mL|pg/mL|%|fL|pg|10\^3/μL|10\^3/uL|10\^6/μL|10\^6/uL|/μL|/uL|cells/μL|cfu/mL|CFU/mL)?"

def auto_extract_results(full_text: str):
    """
    Πιάνει γραμμές της μορφής:
    "CRP  0,54 mg/dL"
    "Λευκά  7,20 10^3/μL"
    "E. COLI 10^5 CFU/mL" (κρατάει το 10^5 ως αριθμό 10 και 5 όχι - οπότε εδώ θέλει ειδική αντιμετώπιση)
    Για μικροβιολογικά με εκθέτες, κρατάμε και raw_value.
    """
    rows = []
    lines = [normalize_line(x) for x in full_text.split("\n")]
    lines = [x for x in lines if x]

    for line in lines:
        # Skip πολύ μικρές γραμμές
        if len(line) < 4:
            continue

        # Πρώτα πιάσε πιθανό label + value
        # label: αρχή γραμμής μέχρι πριν τον αριθμό
        m = re.search(r"^(.*?)([-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|[-]?\d+(?:[.,]\d+)?)(?:\s*(" + UNIT_RX + r"))?\s*$", line, flags=re.IGNORECASE)
        if not m:
            continue

        label = m.group(1).strip(" .:-")
        raw_num = m.group(2)
        unit = (m.group(3) or "").strip()

        # Απόρριψε labels που είναι “σκουπίδια”
        if not label or len(label) < 2:
            continue
        # Απόρριψε γραμμές τύπου "Ημερομηνία 12/12/2025"
        if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
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

    # Μικρό de-dup (κρατάμε την πρώτη εμφάνιση ανά Test+Value+Unit)
    if rows:
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["Test", "Value", "Unit"])
        return df
    return pd.DataFrame(columns=["Test", "Value", "Unit", "RawLine"])

# =========================
# 6) EXPORT
# =========================
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

# =========================
# 7) STATISTICS
# =========================
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

# =========================
# 8) DATABASE (your existing)
# =========================
ALL_METRICS_DB = {
    "RBC (Ερυθρά)": ["RBC", "Ερυθρά"],
    "HGB (Αιμοσφαιρίνη)": ["HGB", "Αιμοσφαιρίνη"],
    "HCT (Αιματοκρίτης)": ["HCT", "Αιματοκρίτης"],
    "PLT (Αιμοπετάλια)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "WBC (Λευκά)": ["WBC", "Λευκά"],

    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "MPV": ["MPV"],
    "PCT": ["PCT"],
    "PDW": ["PDW"],

    "NEUT (Ουδετερόφιλα)": ["NEUT", "Ουδετερόφιλα"],
    "LYMPH (Λεμφοκύτταρα)": ["LYMPH", "Λεμφοκύτταρα"],
    "MONO (Μονοπύρηνα)": ["MONO", "Μονοπύρηνα"],
    "EOS (Ηωσινόφιλα)": ["EOS", "Ηωσινόφιλα"],
    "BASO (Βασέοφιλα)": ["BASO", "Βασέοφιλα"],

    "Σάκχαρο (GLU)": ["GLU", "GLUCOSE", "Σάκχαρο"],
    "Ουρία": ["UREA", "Ουρία"],
    "Κρεατινίνη": ["CREATININE", "CREA", "CR", "Κρεατινίνη"],
    "Χοληστερίνη": ["CHOLESTEROL", "CHOL", "Χοληστερίνη"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "Τριγλυκερίδια": ["TRIGLYCERIDES", "TRIG", "Τριγλυκερίδια"],
    "CRP": ["CRP", "Ποσοτική"],

    "AST (SGOT)": ["AST", "SGOT"],
    "ALT (SGPT)": ["ALT", "SGPT"],
    "GGT": ["GGT", "γ-GT"],
    "Σίδηρος (Fe)": ["FE", "IRON", "Σίδηρος"],
    "Φερριτίνη": ["FERRITIN", "Φερριτίνη"],
    "B12": ["B12"],
    "Φυλλικό Οξύ": ["FOLIC", "Φυλλικό"],
    "Βιταμίνη D3": ["VIT D", "D3", "25-OH"],
    "TSH": ["TSH"],
    "PSA": ["PSA"]
}

if 'df_master' not in st.session_state:
    st.session_state.df_master = None
if 'auto_master' not in st.session_state:
    st.session_state.auto_master = None

# =========================
# SIDEBAR
# =========================
st.sidebar.header("⚙️ Settings")
uploaded_files = st.sidebar.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)

mode = st.sidebar.radio("Extraction mode", ["Strict metrics (DB)", "Auto extract (all results)"], index=0)
dpi = st.sidebar.slider("OCR quality (DPI)", 200, 400, 300, 50)

all_keys = list(ALL_METRICS_DB.keys())
default_choices = ["PLT (Αιμοπετάλια)", "Σάκχαρο (GLU)", "Χοληστερίνη", "RBC (Ερυθρά)", "WBC (Λευκά)"]
safe_defaults = [x for x in default_choices if x in all_keys]

container = st.sidebar.container()
select_all = st.sidebar.checkbox("Select All (Strict)")

if select_all:
    selected_metric_keys = container.multiselect("Metrics:", all_keys, default=all_keys)
else:
    selected_metric_keys = container.multiselect("Metrics:", all_keys, default=safe_defaults)

active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

if st.sidebar.button("🚀 START") and uploaded_files:
    client = get_vision_client()
    if client:
        all_data = []
        auto_data = []
        bar = st.progress(0)

        for i, file in enumerate(uploaded_files):
            try:
                pdf_bytes = file.getvalue()  # IMPORTANT: μην κάνεις file.read() (χάνεται/αδειάζει σε loops)

                full_text = ocr_pdf_to_text(client, pdf_bytes, dpi=dpi)

                # date
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                if date_match:
                    the_date = pd.to_datetime(date_match.group(1), dayfirst=True, errors='coerce')
                else:
                    # fallback από όνομα αρχείου τύπου YYMMDD
                    m = re.search(r'(\d{6})', file.name)
                    if m:
                        d_str = m.group(1)
                        the_date = pd.to_datetime(f"{d_str[4:6]}/{d_str[2:4]}/20{d_str[0:2]}", dayfirst=True, errors='coerce')
                    else:
                        the_date = pd.NaT

                if mode == "Strict metrics (DB)":
                    data = parse_google_text_deep(full_text, active_metrics_map)
                    data['Date'] = the_date
                    data['Αρχείο'] = file.name
                    all_data.append(data)

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
                st.session_state.df_master = pd.DataFrame(all_data).sort_values('Date')
                st.session_state.auto_master = None
                st.success("Done (Strict)!")
        else:
            if auto_data:
                st.session_state.auto_master = pd.concat(auto_data, ignore_index=True).sort_values('Date')
                st.session_state.df_master = None
                st.success("Done (Auto)!")


# =========================
# DASHBOARD (STRICT)
# =========================
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()

    cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()

    display_df = final_df.copy()
    display_df['Date'] = pd.to_datetime(display_df['Date'], errors='coerce').dt.strftime('%d/%m/%Y')

    st.subheader("📋 Results")
    st.dataframe(display_df, use_container_width=True)

    st.subheader("📈 Chart")
    if len(cols) > 2:
        plot_df = final_df.melt(id_vars=['Date', 'Αρχείο'], var_name='Metric', value_name='Value').dropna()
        fig = px.line(plot_df, x='Date', y='Value', color='Metric', markers=True, title="History")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        fig = None
        st.info("Select metrics.")

    st.divider()
    st.subheader("🧮 Stats")
    stat_cols = [c for c in cols if c not in ['Date', 'Αρχείο']]
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
    st.subheader("📥 Export")
    ec1, ec2 = st.columns(2)
    with ec1:
        if fig:
            try:
                xl = to_excel_with_chart(final_df, fig)
                st.download_button("📊 Excel", xl, "report.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except:
                st.warning("Needs kaleido")
    with ec2:
        if fig:
            try:
                img = fig.to_image(format="png")
                pdf = create_pdf_report(display_df, img)
                st.download_button("📄 PDF", pdf, "report.pdf", "application/pdf")
            except:
                st.warning("Needs kaleido")


# =========================
# DASHBOARD (AUTO)
# =========================
if st.session_state.auto_master is not None:
    dfA = st.session_state.auto_master.copy()
    dfA["DateStr"] = pd.to_datetime(dfA["Date"], errors="coerce").dt.strftime("%d/%m/%Y")

    st.subheader("🧫 Auto Extract Results (Microbiology-friendly)")
    st.dataframe(dfA[["DateStr", "Αρχείο", "Test", "Value", "Unit", "RawLine"]], use_container_width=True)

    st.subheader("📈 Auto Chart")
    # Επιλογή τεστ
    tests = sorted(dfA["Test"].dropna().unique().tolist())
    chosen_tests = st.multiselect("Select tests to chart", tests, default=tests[:3] if len(tests) >= 3 else tests)

    if chosen_tests:
        chart_df = dfA[dfA["Test"].isin(chosen_tests)].copy()
        chart_df["Date"] = pd.to_datetime(chart_df["Date"], errors="coerce")
        chart_df = chart_df.dropna(subset=["Date", "Value"])
        figA = px.line(chart_df, x="Date", y="Value", color="Test", markers=True, title="Auto History")
        figA.update_layout(title_x=0.5)
        st.plotly_chart(figA, use_container_width=True)
    else:
        figA = None

    st.divider()
    st.subheader("📥 Export (Auto)")
    ec1, ec2 = st.columns(2)
    with ec1:
        if figA:
            try:
                xl = to_excel_with_chart(dfA, figA)
                st.download_button("📊 Excel (Auto)", xl, "auto_report.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except:
                st.warning("Needs kaleido")
    with ec2:
        if figA:
            try:
                img = figA.to_image(format="png")
                # για PDF, βάζουμε μια “pivot” μορφή μόνο για εμφάνιση (αλλιώς είναι long)
                # κρατάμε το long στον Excel.
                pivot = dfA.pivot_table(index=["DateStr", "Αρχείο"], columns="Test", values="Value", aggfunc="first").reset_index()
                pivot.columns = [str(c) for c in pivot.columns]
                pivot = pivot.rename(columns={"DateStr": "Date"})
                pdf = create_pdf_report(pivot, img)
                st.download_button("📄 PDF (Auto)", pdf, "auto_report.pdf", "application/pdf")
            except:
                st.warning("Needs kaleido")
