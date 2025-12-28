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

# --- 1. RAGE CONFIG & CSS (DESIGN) ---
st.set_page_config(page_title="Medical Lab Commander Ultimate", layout="wide")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
    
    html, body, .stDataFrame {
        font-family: 'Roboto', sans-serif;
    }

    /* ΚΕΝΤΡΑΡΙΣΜΑ ΠΑΝΤΟΥ ΣΤΟΝ ΠΙΝΑΚΑ */
    .stDataFrame td, .stDataFrame th {
        text-align: center !important;
        vertical-align: middle !important;
    }
    
    /* ΧΡΩΜΑΤΙΣΤΗ ΚΕΦΑΛΙΔΑ */
    .stDataFrame th {
        background-color: #ff4b4b !important;
        color: white !important;
    }
    
    h1, h2, h3 { text-align: center; }
    </style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h5 style='text-align: center;'>Full Analytics | 60+ Metrics | PDF Reports</h5>", unsafe_allow_html=True)

# --- 2. AUTHENTICATION ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Authentication Error: {e}")
        return None

# --- 3. CLEANING FUNCTIONS ---
def clean_number(val_str):
    if not val_str: return None
    # Καθαρισμός συμβόλων που μπερδεύουν
    val_str = val_str.replace('"', '').replace("'", "").replace(",", ".") 
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    val_str = val_str.replace('H', '').replace('L', '') # High/Low indicators
    
    # Κρατάμε μόνο αριθμούς και τελεία
    clean = re.sub(r"[^0-9.]", "", val_str)
    
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    # Καθαρίζουμε πρώτα τα εισαγωγικά για να μην κολλάνε οι αριθμοί
    s_clean = s.replace('"', ' ').replace("'", " ")
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s_clean)
    
    for num in numbers:
        num_fixed = num.replace(',', '.')
        cleaned = clean_number(num_fixed)
        if cleaned is not None:
            return cleaned
    return None

# --- 4. PARSER ENGINE (DEEP SEARCH 5 LINES) ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            # Αν βρεθεί η λέξη κλειδί
            if any(key.upper() in line.upper() for key in keywords):
                
                val = None
                
                # 1. Ψάχνουμε στην ίδια γραμμή
                val = find_first_number(line)
                
                # 2. Deep Search: Ψάχνουμε μέχρι και 5 γραμμές από κάτω
                # Αυτό πιάνει περιπτώσεις όπου ο αριθμός είναι πολύ χαμηλά
                if val is None:
                    for offset in range(1, 6): # i+1 έως i+5
                        if i + offset < len(lines):
                            val = find_first_number(lines[i + offset])
                            if val is not None:
                                break
                
                if val is not None:
                    # --- Φίλτρα Ασφαλείας (Logic Check) ---
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue # Έτος
                    if "PLT" in metric_name and val < 10: continue # Πολύ μικρό για αιμοπετάλια
                    if "WBC" in metric_name and val > 100: continue # Λάθος ανάγνωση
                    if "pH" in metric_name and val > 14: continue
                    if "HGB" in metric_name and val > 25: continue
                    
                    results[metric_name] = val
                    break 
    return results

# --- 5. EXPORT FUNCTIONS (PDF & EXCEL) ---
def create_pdf_report(df, chart_image_bytes):
    pdf = FPDF()
    pdf.add_page()
    # Τίτλος
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "Medical Lab Report", 0, 1, 'C')
    pdf.ln(10)
    
    # Κεφαλίδες Πίνακα
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(60, 10, "File", 1)
    pdf.cell(0, 10, "Values (Summary)", 1, 1)
    
    # Γραμμές Πίνακα
    pdf.set_font("Arial", '', 9)
    cols = df.columns.tolist()
    for index, row in df.iterrows():
        date_str = str(row['Date'])
        file_str = str(row['Αρχείο'])[:25]
        
        # Ενώνουμε τις τιμές σε ένα string για να χωρέσουν
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
    
    # Εισαγωγή Εικόνας Γραφήματος
    if chart_image_bytes:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
            tmp_file.write(chart_image_bytes)
            tmp_path = tmp_file.name
        
        try:
            pdf.image(tmp_path, x=10, w=190)
        except:
            pass # Αν αποτύχει η εικόνα, συνεχίζει χωρίς αυτήν
        os.remove(tmp_path)
        
    return pdf.output(dest='S').encode('latin-1', 'ignore')

def to_excel_with_chart(df, chart_fig):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Data', index=False)
        worksheet = writer.sheets['Data']
        workbook = writer.book
        
        # Center Align στο Excel
        center_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
        worksheet.set_column('A:AZ', 20, center_fmt)
        
        # Εισαγωγή εικόνας αν υπάρχει (χρειάζεται kaleido)
        if chart_fig:
            try:
                img_bytes = chart_fig.to_image(format="png")
                image_data = io.BytesIO(img_bytes)
                worksheet.insert_image('E2', 'chart.png', {'image_data': image_data, 'x_scale': 0.5, 'y_scale': 0.5})
            except:
                pass 
    return output.getvalue()

# --- 6. STATISTICS ROBUST FUNCTION ---
def run_statistics(df, col_x, col_y):
    # Καθαρισμός και μετατροπή
    clean_df = df[[col_x, col_y]].apply(pd.to_numeric, errors='coerce').dropna()
    
    # Έλεγχος για ελάχιστα δεδομένα
    if len(clean_df) < 3:
        msg = f"⚠️ Ανεπαρκή δεδομένα ({len(clean_df)} κοινές μετρήσεις). Απαιτούνται τουλάχιστον 3."
        return msg, None, None
    
    x = clean_df[col_x]
    y = clean_df[col_y]
    
    # Έλεγχος για σταθερές τιμές (διαίρεση με το μηδέν)
    if x.std() == 0 or y.std() == 0:
        msg = f"⚠️ Η μία μεταβλητή είναι σταθερή. Αδύνατη η συσχέτιση."
        return msg, None, None

    try:
        corr, p_value = stats.pearsonr(x, y)
        X = sm.add_constant(x)
        model = sm.OLS(y, X).fit()
        
        significance = "Στατιστικά ΣΗΜΑΝΤΙΚΗ" if p_value < 0.05 else "ΜΗ Στατιστικά Σημαντική"
        
        report = f"""
        ### 📊 Στατιστική Ανάλυση: {col_x} vs {col_y}
        - **Δείγματα (N):** {len(clean_df)}
        - **Συσχέτιση Pearson (r):** {corr:.4f}
        - **P-value:** {p_value:.5f} ({significance})
        - **R-squared:** {model.rsquared:.4f}
        """
        return report, clean_df, model
    except Exception as e:
        return f"⚠️ Σφάλμα υπολογισμού: {str(e)}", None, None

# --- 7. MAIN LOGIC & DATABASE ---

# Η ΠΛΗΡΗΣ ΛΙΣΤΑ (60+ ΔΕΙΚΤΕΣ)
ALL_METRICS_DB = {
    # ΓΕΝΙΚΗ ΑΙΜΑΤΟΣ
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "MCV": ["MCV", "Μέσος Όγκος"],
    "MCH": ["MCH", "Μέση Περιεκτ"],
    "MCHC": ["MCHC", "Μέση Πυκν"],
    "RDW": ["RDW", "Εύρος Κατανομής"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "MPV": ["MPV", "Μέσος Όγκος Αιμοπεταλίων"],
    "PCT": ["PCT", "Αιμοπεταλιοκρίτης"],
    "PDW": ["PDW"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Ουδετερόφιλα %": ["NEUT", "Ουδετερόφιλα", "NE "],
    "Λεμφοκύτταρα %": ["LYMPH", "Λεμφοκύτταρα"],
    "Μονοπύρηνα %": ["MONO", "Μονοπύρηνα"],
    "Ηωσινόφιλα %": ["EOS", "Ηωσινόφιλα"],
    "Βασέοφιλα %": ["BASO", "Βασέοφιλα"],
    
    # ΒΙΟΧΗΜΙΚΕΣ
    "Σάκχαρο (GLU)": ["GLU", "Σάκχαρο", "Glucose"],
    "Ουρία": ["Urea", "Ουρία"],
    "Κρεατινίνη": ["Creatinine", "Κρεατινίνη"],
    "Ουρικό Οξύ": ["Uric Acid", "Ουρικό"],
    "Χοληστερίνη": ["Cholesterol", "Χοληστερίνη"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "Τριγλυκερίδια": ["Triglycerides", "Τριγλυκερίδια"],
    "Ολική Χολερυθρίνη": ["Bilirubin Total", "Χολερυθρίνη Ολική"],
    "Άμεση Χολερυθρίνη": ["Direct", "Άμεση Χολερυθρίνη"],
    
    # ΕΝΖΥΜΑ
    "SGOT (AST)": ["SGOT", "AST", "ΑΣΤ"],
    "SGPT (ALT)": ["SGPT", "ALT", "ΑΛΤ"],
    "γ-GT": ["GGT", "γ-GT", "γGT"],
    "ALP": ["ALP", "Αλκαλική"],
    "CPK": ["CPK"],
    "LDH": ["LDH"],
    "Αμυλάση": ["Amylase", "Αμυλάση"],

    # ΗΛΕΚΤΡΟΛΥΤΕΣ
    "Κάλιο (K)": ["Potassium", "Κάλιο"],
    "Νάτριο (Na)": ["Sodium", "Νάτριο"],
    "Ασβέστιο (Ca)": ["Calcium", "Ασβέστιο"],
    "Μαγνήσιο (Mg)": ["Magnesium", "Μαγνήσιο"],
    "Φώσφορος (P)": ["Phosphorus", "Φώσφορος"],

    # ΣΙΔΗΡΟΣ & ΒΙΤΑΜΙΝΕΣ
    "Σίδηρος (Fe)": ["Fe ", "Σίδηρος"],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "B12": ["B12"],
    "Φυλλικό Οξύ": ["Folic", "Φυλλικό"],
    "Βιταμίνη D3": ["Vit D", "D3", "25-OH"],

    # ΘΥΡΕΟΕΙΔΗΣ
    "TSH": ["TSH"],
    "T3": ["T3 "],
    "T4": ["T4 "],
    "FT3": ["FT3"],
    "FT4": ["FT4"],
    "Anti-TPO": ["TPO", "Αντιθυρεοειδικά"],

    # ΦΛΕΓΜΟΝΗ / ΠΗΞΗ
    "CRP": ["CRP"],
    "TKE": ["ESR", "ΤΚΕ"],
    "Ινωδογόνο": ["Fibrinogen", "Ινωδογόνο"],
    "PT": ["PT ", "Προθρομβίνης"],
    "INR": ["INR"],
    
    # ΟΥΡΑ & ΚΑΡΚΙΝΙΚΟΙ
    "pH Ούρων": ["pH"],
    "Ειδικό Βάρος": ["S.G.", "Ειδικό Βάρος"],
    "Λεύκωμα Ούρων": ["Protein", "Λεύκωμα"],
    "PSA": ["PSA"],
    "CEA": ["CEA"],
    "CA 125": ["CA 125"],
    "CA 19-9": ["CA 19-9"]
}

if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# SIDEBAR CONFIG
st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)

all_keys = list(ALL_METRICS_DB.keys())
# Προεπιλογή: Μερικές βασικές, αλλά ο χρήστης μπορεί να διαλέξει "Select All"
container = st.sidebar.container()
select_all = st.sidebar.checkbox("Επιλογή ΟΛΩΝ των εξετάσεων")

if select_all:
    selected_metric_keys = container.multiselect("Εξετάσεις:", all_keys, default=all_keys)
else:
    # Default selection
    selected_metric_keys = container.multiselect("Εξετάσεις:", all_keys, default=["Αιμοπετάλια (PLT)", "Σάκχαρο", "Χοληστερίνη", "Ερυθρά (RBC)", "Λευκά (WBC)"])

active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

if st.sidebar.button("🚀 ΕΝΑΡΞΗ ΕΞΑΓΩΓΗΣ") and uploaded_files:
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
                
                # --- DEEP PARSER CALL ---
                data = parse_google_text_deep(full_text, active_metrics_map)
                
                # Date
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
            st.success("Η εξαγωγή ολοκληρώθηκε!")

# --- 8. DASHBOARD ---
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    # Επιλογή στηλών που έχουν δεδομένα
    cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()
    
    # Format Date
    display_df = final_df.copy()
    display_df['Date'] = display_df['Date'].dt.strftime('%d/%m/%Y')

    # TAB 1: ΠΙΝΑΚΑΣ
    st.subheader("📋 Αποτελέσματα")
    st.dataframe(display_df, use_container_width=True)

    # TAB 2: ΓΡΑΦΗΜΑ
    st.subheader("📈 Ιστορικό Γράφημα")
    if len(cols) > 2:
        plot_df = final_df.melt(id_vars=['Date', 'Αρχείο'], var_name='Metric', value_name='Value').dropna()
        fig = px.line(plot_df, x='Date', y='Value', color='Metric', markers=True, title="Πορεία Εξετάσεων")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        fig = None
        st.info("Επίλεξε εξετάσεις για να δεις γράφημα.")

    # TAB 3: ΣΤΑΤΙΣΤΙΚΑ
    st.divider()
    st.subheader("🧮 Στατιστική Ανάλυση")
    stat_cols = [c for c in cols if c not in ['Date', 'Αρχείο']]
    
    c1, c2 = st.columns(2)
    with c1: x_ax = st.selectbox("Μεταβλητή X", stat_cols, index=0 if len(stat_cols)>0 else None)
    with c2: y_ax = st.selectbox("Μεταβλητή Y", stat_cols, index=1 if len(stat_cols)>1 else 0)
    
    if st.button("Υπολογισμός Στατιστικών"):
        if x_ax and y_ax and x_ax != y_ax:
            # Κλήση Robust Function
            report, c_data, mod = run_statistics(final_df, x_ax, y_ax)
            if c_data is None:
                st.warning(report)
            else:
                st.markdown(report)
                fig_r = px.scatter(c_data, x=x_ax, y=y_ax, trendline="ols", title=f"{x_ax} vs {y_ax}")
                st.plotly_chart(fig_r, use_container_width=True)
        else:
            st.warning("Διάλεξε διαφορετικές μεταβλητές.")

    # EXPORT BUTTONS
    st.divider()
    st.subheader("📥 Λήψη Αναφοράς")
    
    ec1, ec2 = st.columns(2)
    
    with ec1:
        if fig:
            try:
                xl_data = to_excel_with_chart(final_df, fig)
                st.download_button("📊 Excel με Γράφημα", xl_data, "report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except:
                st.warning("Για γραφήματα στο Excel χρειάζεται το 'kaleido'.")
    
    with ec2:
        if fig:
            try:
                img_bytes = fig.to_image(format="png")
                pdf_bytes = create_pdf_report(display_df, img_bytes)
                st.download_button("📄 PDF Report", pdf_bytes, "report.pdf", "application/pdf")
            except:
                st.warning("Για PDF με εικόνα χρειάζεται το 'kaleido'.")
