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

# --- 1. CONFIG & CSS ---
st.set_page_config(page_title="Medical Lab Commander", layout="wide")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
    
    html, body, .stDataFrame {
        font-family: 'Roboto', sans-serif;
    }

    /* ΚΕΝΤΡΑΡΙΣΜΑ */
    .stDataFrame td, .stDataFrame th {
        text-align: center !important;
        vertical-align: middle !important;
    }
    
    .stDataFrame th {
        background-color: #ff4b4b !important;
        color: white !important;
    }
    
    h1, h2, h3 { text-align: center; }
    </style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h5 style='text-align: center;'>Strict English Acronyms Mode</h5>", unsafe_allow_html=True)

# --- 2. AUTH ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Auth Error: {e}")
        return None

# --- 3. CLEANING ---
def clean_number(val_str):
    if not val_str: return None
    # Καθαρισμός συμβόλων
    val_str = val_str.replace('"', '').replace("'", "")
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    val_str = val_str.replace('H', '').replace('L', '') 
    
    # Αντικατάσταση κόμματος με τελεία
    val_str = val_str.replace(',', '.')

    # Κρατάμε αριθμούς και τελεία
    clean = re.sub(r"[^0-9.]", "", val_str)
    
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    # Καθαρίζουμε πρώτα τα εισαγωγικά και άνω κάτω τελείες
    s_clean = s.replace('"', ' ').replace("'", " ").replace(':', ' ')
    
    # Ψάχνουμε μοτίβα αριθμών
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s_clean)
    
    for num in numbers:
        cleaned = clean_number(num)
        if cleaned is not None:
            return cleaned
    return None

# --- 4. ENGINE (DEEP SEARCH) ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            # ΑΥΣΤΗΡΗ ΑΝΑΖΗΤΗΣΗ (μόνο τα αγγλικά ακρώνυμα)
            # Χρησιμοποιούμε split() για να βρούμε τη λέξη "σκέτη" αν γίνεται, 
            # αλλά το 'in' είναι πιο ασφαλές για κολλημένες λέξεις.
            line_upper = line.upper()
            
            # Αν βρεθεί το ακρώνυμο (π.χ. "RBC")
            if any(key.upper() in line_upper for key in keywords):
                
                val = None
                
                # 1. Ίδια γραμμή
                val = find_first_number(line)
                
                # 2. Deep Search (5 γραμμές κάτω)
                if val is None:
                    for offset in range(1, 6):
                        if i + offset < len(lines):
                            val = find_first_number(lines[i + offset])
                            if val is not None:
                                break
                
                if val is not None:
                    # Logic Filters
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue
                    if "PLT" in metric_name and val < 10: continue
                    if "WBC" in metric_name and val > 100: continue
                    if "HGB" in metric_name and val > 25: continue
                    if "pH" in metric_name and val > 14: continue
                    
                    results[metric_name] = val
                    break 
    return results

# --- 5. EXPORT ---
def create_pdf_report(df, chart_image_bytes):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "Medical Lab Report", 0, 1, 'C')
    pdf.ln(10)
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(60, 10, "File", 1)
    pdf.cell(0, 10, "Values", 1, 1)
    
    pdf.set_font("Arial", '', 9)
    cols = df.columns.tolist()
    for index, row in df.iterrows():
        date_str = str(row['Date'])
        file_str = str(row['Αρχείο'])[:25]
        
        vals = []
        for c in cols:
            if c not in ['Date', 'Αρχείο'] and pd.notna(row[c]):
                vals.append(f"{c[:4]}:{row[c]}")
        vals_str = ", ".join(vals)
        
        pdf.cell(30, 10, date_str, 1)
        pdf.cell(60, 10, file_str, 1)
        pdf.multi_cell(0, 10, vals_str, 1)
        pdf.ln(1)
    pdf.ln(10)
    
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
        worksheet.set_column('A:AZ', 20, center_fmt)
        
        if chart_fig:
            try:
                img_bytes = chart_fig.to_image(format="png")
                image_data = io.BytesIO(img_bytes)
                worksheet.insert_image('E2', 'chart.png', {'image_data': image_data, 'x_scale': 0.5, 'y_scale': 0.5})
            except:
                pass 
    return output.getvalue()

# --- 6. STATISTICS ---
def run_statistics(df, col_x, col_y):
    clean_df = df[[col_x, col_y]].apply(pd.to_numeric, errors='coerce').dropna()
    
    if len(clean_df) < 3:
        msg = f"⚠️ Ανεπαρκή δεδομένα ({len(clean_df)}). Απαιτούνται 3+."
        return msg, None, None
    x = clean_df[col_x]
    y = clean_df[col_y]
    if x.std() == 0 or y.std() == 0:
        msg = f"⚠️ Σταθερή τιμή. Αδύνατη η στατιστική."
        return msg, None, None

    try:
        corr, p_value = stats.pearsonr(x, y)
        X = sm.add_constant(x)
        model = sm.OLS(y, X).fit()
        significance = "Στατιστικά ΣΗΜΑΝΤΙΚΗ" if p_value < 0.05 else "ΜΗ Σημαντική"
        
        report = f"""
        ### 📊 Στατιστική: {col_x} vs {col_y}
        - **N:** {len(clean_df)}
        - **Pearson r:** {corr:.4f}
        - **P-value:** {p_value:.5f} ({significance})
        - **R-squared:** {model.rsquared:.4f}
        """
        return report, clean_df, model
    except Exception as e:
        return f"Error: {str(e)}", None, None

# --- 7. DATABASE (ONLY ENGLISH ACRONYMS) ---
# Εδώ άλλαξαν όλα. ΜΟΝΟ αγγλικά κλειδιά για να μην μπερδεύεται τίποτα.

ALL_METRICS_DB = {
    # CBC (Γενική Αίματος)
    "RBC (Ερυθρά)": ["RBC"], 
    "HGB (Αιμοσφαιρίνη)": ["HGB"],
    "HCT (Αιματοκρίτης)": ["HCT"],
    "PLT (Αιμοπετάλια)": ["PLT"],
    "WBC (Λευκά)": ["WBC"],
    
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW"],
    "MPV": ["MPV"],
    "PCT": ["PCT"],
    "PDW": ["PDW"],
    
    # WBC Diff (Τύπος) - Βάλαμε και τα σύντομα (NE, EO, BA) που είδαμε στο PDF σου
    "NEUT (Ουδετερόφιλα)": ["NEUT", "NE"], 
    "LYMPH (Λεμφοκύτταρα)": ["LYMPH"],
    "MONO (Μονοπύρηνα)": ["MONO"],
    "EOS (Ηωσινόφιλα)": ["EOS", "EO"],
    "BASO (Βασέοφιλα)": ["BASO", "BA"],
    
    # BIOCHEM (Βιοχημικά)
    "GLU (Σάκχαρο)": ["GLU", "GLUCOSE"],
    "UREA (Ουρία)": ["UREA"],
    "CREA (Κρεατινίνη)": ["CREATININE", "CREA", "CR"],
    "UA (Ουρικό Οξύ)": ["URIC ACID", "UA"],
    "CHOL (Χοληστερίνη)": ["CHOLESTEROL", "CHOL"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "TRIG (Τριγλυκερίδια)": ["TRIGLYCERIDES", "TRIG"],
    "CRP": ["CRP"],
    
    # LIVER/ENZYMES
    "AST (SGOT)": ["AST", "SGOT"],
    "ALT (SGPT)": ["ALT", "SGPT"],
    "GGT (γ-GT)": ["GGT"],
    "ALP": ["ALP"],
    "FE (Σίδηρος)": ["FE ", "IRON"], # Fe με κενό
    "FERR (Φερριτίνη)": ["FERRITIN"],
    "B12": ["B12"],
    "FOLIC (Φυλλικό)": ["FOLIC"],
    "VIT D (Βιταμίνη D)": ["VIT D", "D3", "25-OH"],
    
    # THYROID / OTHER
    "TSH": ["TSH"],
    "T3": ["T3"],
    "T4": ["T4"],
    "FT3": ["FT3"],
    "FT4": ["FT4"],
    "PSA": ["PSA"]
}

if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# SIDEBAR
st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)

all_keys = list(ALL_METRICS_DB.keys())

# Default values must match dictionary keys exactly
default_choices = [
    "PLT (Αιμοπετάλια)", 
    "GLU (Σάκχαρο)", 
    "CHOL (Χοληστερίνη)",
    "RBC (Ερυθρά)", 
    "WBC (Λευκά)"
]
# Safety check
safe_defaults = [x for x in default_choices if x in all_keys]

container = st.sidebar.container()
select_all = st.sidebar.checkbox("Επιλογή ΟΛΩΝ")

if select_all:
    selected_metric_keys = container.multiselect("Εξετάσεις (Acronyms Only):", all_keys, default=all_keys)
else:
    selected_metric_keys = container.multiselect("Εξετάσεις (Acronyms Only):", all_keys, default=safe_defaults)

active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

if st.sidebar.button("🚀 ΕΝΑΡΞΗ") and uploaded_files:
    client = get_vision_client()
    if client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            try:
                images = convert_from_bytes(file.read())
                full_text = ""
                for img in images:
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    content = img_byte_arr.getvalue()
                    image = vision.Image(content=content)
                    response = client.text_detection(image=image)
                    if response.text_annotations:
                        full_text += response.text_annotations[0].description + "\n"
                
                # CALL PARSER
                data = parse_google_text_deep(full_text, active_metrics_map)
                
                # DATE
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                if date_match:
                    data['Date'] = pd.to_datetime(date_match.group(1), dayfirst=True)
                else:
                    m = re.search(r'(\d{6})', file.name)
                    if m:
                        d_str = m.group(1)
                        data['Date'] = pd.to_datetime(f"{d_str[4:6]}/{d_str[2:4]}/20{d_str[0:2]}", dayfirst=True)
                    else:
                        data['Date'] = pd.NaT
                
                data['Αρχείο'] = file.name
                all_data.append(data)
                
            except Exception as e:
                st.error(f"Error file {file.name}: {e}")
            bar.progress((i+1)/len(uploaded_files))
            
        if all_data:
            st.session_state.df_master = pd.DataFrame(all_data).sort_values('Date')
            st.success("Ολοκληρώθηκε!")

# DASHBOARD
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()
    display_df = final_df.copy()
    display_df['Date'] = display_df['Date'].dt.strftime('%d/%m/%Y')

    st.subheader("📋 Αποτελέσματα")
    st.dataframe(display_df, use_container_width=True)

    st.subheader("📈 Γράφημα")
    if len(cols) > 2:
        plot_df = final_df.melt(id_vars=['Date', 'Αρχείο'], var_name='Metric', value_name='Value').dropna()
        fig = px.line(plot_df, x='Date', y='Value', color='Metric', markers=True, title="Ιστορικό")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        fig = None
        st.info("Επίλεξε εξετάσεις για γράφημα.")

    st.divider()
    st.subheader("🧮 Στατιστικά")
    stat_cols = [c for c in cols if c not in ['Date', 'Αρχείο']]
    c1, c2 = st.columns(2)
    with c1: x_ax = st.selectbox("X", stat_cols, index=0 if len(stat_cols)>0 else None)
    with c2: y_ax = st.selectbox("Y", stat_cols, index=1 if len(stat_cols)>1 else 0)
    
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
    st.subheader("📥 Λήψη")
    ec1, ec2 = st.columns(2)
    with ec1:
        if fig:
            try:
                xl = to_excel_with_chart(final_df, fig)
                st.download_button("📊 Excel", xl, "report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
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
