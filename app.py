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

# --- ΡΥΘΜΙΣΕΙΣ CSS (ΚΕΝΤΡΑΡΙΣΜΑ) ---
st.set_page_config(page_title="Medical Commander Ultimate", layout="wide")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
    
    html, body, .stDataFrame {
        font-family: 'Roboto', sans-serif;
    }

    /* ΚΕΝΤΡΑΡΙΣΜΑ ΣΤΑ ΚΕΛΙΑ ΤΟΥ ΠΙΝΑΚΑ */
    .stDataFrame td {
        text-align: center !important;
        vertical-align: middle !important;
    }
    
    .stDataFrame th {
        text-align: center !important;
        background-color: #ff4b4b !important;
        color: white !important;
    }
    
    /* Κεντράρισμα τίτλων */
    h1, h2, h3 { text-align: center; }
    </style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h5 style='text-align: center;'>Ανάλυση | Γραφήματα | PDF Report</h5>", unsafe_allow_html=True)

# --- 1. AUTH ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Auth Error: {e}")
        return None

# --- 2. CLEANING (Βελτιωμένο για τα Αιμοπετάλια) ---
def clean_number(val_str):
    if not val_str: return None
    # Αφαίρεση ειδικών χαρακτήρων που μπερδεύουν (εισαγωγικά, κόμματα στην αρχή)
    val_str = val_str.replace('"', '').replace("'", "").replace(",", ".") 
    # Προσοχή: Αντικαθιστώ το κόμμα με τελεία ΕΔΩ για να μην μπερδευτεί μετά
    
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    
    # Κρατάμε αριθμούς και τελείες
    clean = re.sub(r"[^0-9.]", "", val_str)
    
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    # Πιο επιθετικό regex: Ψάχνει αριθμούς ακόμα και αν είναι κολλημένοι σε σύμβολα
    # π.χ. ","201 -> βρίσκει 201
    # Διαχειρίζεται και το 4,52 (γίνεται 4.52) και το 201
    
    # Βήμα 1: Καθαρισμός της γραμμής από σκουπίδια CSV
    s_clean = s.replace('"', ' ').replace("'", " ")
    
    # Βήμα 2: Εύρεση
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s_clean)
    
    for num in numbers:
        # Αντικατάσταση κόμματος με τελεία για τη μετατροπή
        num_fixed = num.replace(',', '.')
        cleaned = clean_number(num_fixed)
        if cleaned is not None:
            return cleaned
    return None

# --- 3. PARSER (Deep Search 3 Levels) ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            if any(key.upper() in line.upper() for key in keywords):
                
                val = find_first_number(line)
                
                # Έλεγχος επόμενης γραμμής (i+1)
                if val is None and i + 1 < len(lines):
                    val = find_first_number(lines[i+1])
                
                # Έλεγχος μεθεπόμενης (i+2) - ΓΙΑ ΤΑ ΑΙΜΟΠΕΤΑΛΙΑ ΣΟΥ
                if val is None and i + 2 < len(lines):
                    val = find_first_number(lines[i+2])

                # Έλεγχος 3ης γραμμής (i+3) - Για πολύ σπασμένους πίνακες
                if val is None and i + 3 < len(lines):
                    val = find_first_number(lines[i+3])
                
                if val is not None:
                    # Φίλτρα
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue
                    if "PLT" in metric_name and val < 10: continue
                    if "WBC" in metric_name and val > 100: continue
                    
                    results[metric_name] = val
                    break 
    return results

# --- 4. EXPORT FUNCTIONS (PDF & EXCEL) ---

def create_pdf_report(df, chart_image_bytes):
    pdf = FPDF()
    pdf.add_page()
    
    # Font (Arial supports basic chars, but for Greek we need a font that supports it. 
    # FPDF standard fonts don't support Greek well. 
    # For simplicity in this demo, we will use transcription or standard chars.
    # PRO TIP: Σε παραγωγικό περιβάλλον πρέπει να φορτώσεις .ttf αρχείο με Ελληνικά.)
    # Θα χρησιμοποιήσουμε απλά λατινικούς χαρακτήρες για τους τίτλους για να μην σκάσει, 
    # ή θα αγνοήσουμε τα ελληνικά αν δεν έχουμε το font file.
    # Εδώ θα βάλω ένα workaround:
    
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "Medical Lab Report", 0, 1, 'C')
    pdf.ln(10)
    
    # 1. Table Data
    pdf.set_font("Arial", 'B', 10)
    # Headers
    cols = df.columns.tolist()
    # Simplified headers for PDF width
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(60, 10, "File", 1)
    pdf.cell(0, 10, "Values (Summary)", 1, 1)
    
    pdf.set_font("Arial", '', 9)
    for index, row in df.iterrows():
        date_str = str(row['Date'])
        file_str = str(row['Αρχείο'])[:25] # Cut long names
        # Join values
        vals = []
        for c in cols:
            if c not in ['Date', 'Αρχείο'] and pd.notna(row[c]):
                vals.append(f"{c[:4]}:{row[c]}")
        vals_str = ", ".join(vals)
        
        pdf.cell(30, 10, date_str, 1)
        pdf.cell(60, 10, file_str, 1)
        pdf.multi_cell(0, 10, vals_str, 1)
        pdf.ln(1) # Small gap

    pdf.ln(10)
    
    # 2. Add Chart Image
    if chart_image_bytes:
        # Save bytes to temp file because FPDF wants a file path
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
            tmp_file.write(chart_image_bytes)
            tmp_path = tmp_file.name
        
        pdf.image(tmp_path, x=10, w=190)
        os.remove(tmp_path) # Cleanup
        
    return pdf.output(dest='S').encode('latin-1', 'ignore') # Encode logic for FPDF

def to_excel_with_chart(df, chart_fig):
    output = io.BytesIO()
    workbook = user_xlsxwriter_logic(output, df, chart_fig) # Custom logic below
    return output.getvalue()

def user_xlsxwriter_logic(output, df, chart_fig):
    # Χρήση xlsxwriter για να βάλουμε και την εικόνα
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Data', index=False)
        worksheet = writer.sheets['Data']
        
        # Format for centering in Excel
        workbook = writer.book
        center_format = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
        worksheet.set_column('A:Z', 20, center_format)
        
        # Insert Chart Image if available
        if chart_fig:
            img_bytes = chart_fig.to_image(format="png")
            image_data = io.BytesIO(img_bytes)
            worksheet.insert_image('E2', 'chart.png', {'image_data': image_data, 'x_scale': 0.5, 'y_scale': 0.5})
            
    return output

# --- 5. APP LOGIC ---

# Λεξικό (Συντομευμένο για το παράδειγμα, βάλε το πλήρες από πριν αν θες)
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

# --- ΡΥΘΜΙΣΕΙΣ CSS (ΚΕΝΤΡΑΡΙΣΜΑ) ---
st.set_page_config(page_title="Medical Commander Ultimate", layout="wide")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
    
    html, body, .stDataFrame {
        font-family: 'Roboto', sans-serif;
    }

    /* ΚΕΝΤΡΑΡΙΣΜΑ ΣΤΑ ΚΕΛΙΑ ΤΟΥ ΠΙΝΑΚΑ */
    .stDataFrame td {
        text-align: center !important;
        vertical-align: middle !important;
    }
    
    .stDataFrame th {
        text-align: center !important;
        background-color: #ff4b4b !important;
        color: white !important;
    }
    
    /* Κεντράρισμα τίτλων */
    h1, h2, h3 { text-align: center; }
    </style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h5 style='text-align: center;'>Ανάλυση | Γραφήματα | PDF Report</h5>", unsafe_allow_html=True)

# --- 1. AUTH ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Auth Error: {e}")
        return None

# --- 2. CLEANING (Βελτιωμένο για τα Αιμοπετάλια) ---
def clean_number(val_str):
    if not val_str: return None
    # Αφαίρεση ειδικών χαρακτήρων που μπερδεύουν (εισαγωγικά, κόμματα στην αρχή)
    val_str = val_str.replace('"', '').replace("'", "").replace(",", ".") 
    # Προσοχή: Αντικαθιστώ το κόμμα με τελεία ΕΔΩ για να μην μπερδευτεί μετά
    
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    
    # Κρατάμε αριθμούς και τελείες
    clean = re.sub(r"[^0-9.]", "", val_str)
    
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    # Πιο επιθετικό regex: Ψάχνει αριθμούς ακόμα και αν είναι κολλημένοι σε σύμβολα
    # π.χ. ","201 -> βρίσκει 201
    # Διαχειρίζεται και το 4,52 (γίνεται 4.52) και το 201
    
    # Βήμα 1: Καθαρισμός της γραμμής από σκουπίδια CSV
    s_clean = s.replace('"', ' ').replace("'", " ")
    
    # Βήμα 2: Εύρεση
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s_clean)
    
    for num in numbers:
        # Αντικατάσταση κόμματος με τελεία για τη μετατροπή
        num_fixed = num.replace(',', '.')
        cleaned = clean_number(num_fixed)
        if cleaned is not None:
            return cleaned
    return None

# --- 3. PARSER (Deep Search 3 Levels) ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            if any(key.upper() in line.upper() for key in keywords):
                
                val = find_first_number(line)
                
                # Έλεγχος επόμενης γραμμής (i+1)
                if val is None and i + 1 < len(lines):
                    val = find_first_number(lines[i+1])
                
                # Έλεγχος μεθεπόμενης (i+2) - ΓΙΑ ΤΑ ΑΙΜΟΠΕΤΑΛΙΑ ΣΟΥ
                if val is None and i + 2 < len(lines):
                    val = find_first_number(lines[i+2])

                # Έλεγχος 3ης γραμμής (i+3) - Για πολύ σπασμένους πίνακες
                if val is None and i + 3 < len(lines):
                    val = find_first_number(lines[i+3])
                
                if val is not None:
                    # Φίλτρα
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue
                    if "PLT" in metric_name and val < 10: continue
                    if "WBC" in metric_name and val > 100: continue
                    
                    results[metric_name] = val
                    break 
    return results

# --- 4. EXPORT FUNCTIONS (PDF & EXCEL) ---

def create_pdf_report(df, chart_image_bytes):
    pdf = FPDF()
    pdf.add_page()
    
    # Font (Arial supports basic chars, but for Greek we need a font that supports it. 
    # FPDF standard fonts don't support Greek well. 
    # For simplicity in this demo, we will use transcription or standard chars.
    # PRO TIP: Σε παραγωγικό περιβάλλον πρέπει να φορτώσεις .ttf αρχείο με Ελληνικά.)
    # Θα χρησιμοποιήσουμε απλά λατινικούς χαρακτήρες για τους τίτλους για να μην σκάσει, 
    # ή θα αγνοήσουμε τα ελληνικά αν δεν έχουμε το font file.
    # Εδώ θα βάλω ένα workaround:
    
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "Medical Lab Report", 0, 1, 'C')
    pdf.ln(10)
    
    # 1. Table Data
    pdf.set_font("Arial", 'B', 10)
    # Headers
    cols = df.columns.tolist()
    # Simplified headers for PDF width
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(60, 10, "File", 1)
    pdf.cell(0, 10, "Values (Summary)", 1, 1)
    
    pdf.set_font("Arial", '', 9)
    for index, row in df.iterrows():
        date_str = str(row['Date'])
        file_str = str(row['Αρχείο'])[:25] # Cut long names
        # Join values
        vals = []
        for c in cols:
            if c not in ['Date', 'Αρχείο'] and pd.notna(row[c]):
                vals.append(f"{c[:4]}:{row[c]}")
        vals_str = ", ".join(vals)
        
        pdf.cell(30, 10, date_str, 1)
        pdf.cell(60, 10, file_str, 1)
        pdf.multi_cell(0, 10, vals_str, 1)
        pdf.ln(1) # Small gap

    pdf.ln(10)
    
    # 2. Add Chart Image
    if chart_image_bytes:
        # Save bytes to temp file because FPDF wants a file path
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
            tmp_file.write(chart_image_bytes)
            tmp_path = tmp_file.name
        
        pdf.image(tmp_path, x=10, w=190)
        os.remove(tmp_path) # Cleanup
        
    return pdf.output(dest='S').encode('latin-1', 'ignore') # Encode logic for FPDF

def to_excel_with_chart(df, chart_fig):
    output = io.BytesIO()
    workbook = user_xlsxwriter_logic(output, df, chart_fig) # Custom logic below
    return output.getvalue()

def user_xlsxwriter_logic(output, df, chart_fig):
    # Χρήση xlsxwriter για να βάλουμε και την εικόνα
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Data', index=False)
        worksheet = writer.sheets['Data']
        
        # Format for centering in Excel
        workbook = writer.book
        center_format = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
        worksheet.set_column('A:Z', 20, center_format)
        
        # Insert Chart Image if available
        if chart_fig:
            img_bytes = chart_fig.to_image(format="png")
            image_data = io.BytesIO(img_bytes)
            worksheet.insert_image('E2', 'chart.png', {'image_data': image_data, 'x_scale': 0.5, 'y_scale': 0.5})
            
    return output

# --- 5. APP LOGIC ---

# Λεξικό (Συντομευμένο για το παράδειγμα, βάλε το πλήρες από πριν αν θες)
ALL_METRICS_DB = {
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Σάκχαρο": ["GLU", "Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Cholesterol", "Χοληστερίνη"],
    "Σίδηρος": ["Fe ", "Σίδηρος"],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "B12": ["B12"],
    "TSH": ["TSH"]
}

if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# SIDEBAR
st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Ανέβασε PDF", type="pdf", accept_multiple_files=True)

# Pre-selection
all_keys = list(ALL_METRICS_DB.keys())
selected_metric_keys = st.sidebar.multiselect("Επιλογή Εξετάσεων:", all_keys, default=["Αιμοπετάλια (PLT)", "Σάκχαρο", "Χοληστερίνη"])
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
                
                # DEEP PARSER CALL
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
                st.error(f"Error {file.name}: {e}")
            bar.progress((i+1)/len(uploaded_files))
            
        if all_data:
            st.session_state.df_master = pd.DataFrame(all_data).sort_values('Date')
            st.success("Έτοιμο!")

# MAIN VIEW
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    # Filter columns to selected only
    cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()
    
    # Format date for display
    display_df = final_df.copy()
    display_df['Date'] = display_df['Date'].dt.strftime('%d/%m/%Y')

    # --- 1. TABLE CENTERED ---
    st.subheader("📋 Πίνακας Δεδομένων")
    
    # Χρήση CSS class 'stDataFrame' που ορίσαμε πάνω για κεντράρισμα
    st.dataframe(display_df, use_container_width=True)

    # --- 2. CHART ---
    st.subheader("📈 Ιστορικό Γράφημα")
    if len(cols) > 2: # Date, File + at least 1 metric
        plot_df = final_df.melt(id_vars=['Date', 'Αρχείο'], var_name='Εξέταση', value_name='Τιμή').dropna()
        fig = px.line(plot_df, x='Date', y='Τιμή', color='Εξέταση', markers=True, title="Πορεία Εξετάσεων")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Επίλεξε εξετάσεις για να δεις γράφημα.")
        fig = None

    # --- 3. EXPORTS (PDF & EXCEL WITH CHART) ---
    st.divider()
    st.subheader("📥 Εξαγωγή Αναφοράς")
    
    c1, c2 = st.columns(2)
    
    # EXCEL BUTTON
    with c1:
        if fig:
            # Note: to_image requires kaleido package
            try:
                excel_data = user_xlsxwriter_logic(io.BytesIO(), final_df, fig)
                st.download_button(
                    "📊 Excel με Γράφημα",
                    excel_data.getvalue(),
                    "report_with_chart.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.warning(f"Για εξαγωγή γραφήματος σε Excel χρειάζεται το 'kaleido'. Εξάγω μόνο δεδομένα. ({e})")
                # Fallback simple excel
                simple_out = io.BytesIO()
                final_df.to_excel(simple_out, index=False)
                st.download_button("📊 Απλό Excel", simple_out.getvalue(), "data.xlsx")

    # PDF BUTTON
    with c2:
        if fig:
            try:
                img_bytes = fig.to_image(format="png")
                pdf_bytes = create_pdf_report(display_df, img_bytes)
                st.download_button(
                    "📄 PDF Report (Συνολικό)",
                    pdf_bytes,
                    "lab_report.pdf",
                    "application/pdf"
                )
            except Exception as e:
                st.error(f"Σφάλμα PDF: {e}")

if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# SIDEBAR
st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Ανέβασε PDF", type="pdf", accept_multiple_files=True)

# Pre-selection
all_keys = list(ALL_METRICS_DB.keys())
selected_metric_keys = st.sidebar.multiselect("Επιλογή Εξετάσεων:", all_keys, default=["Αιμοπετάλια (PLT)", "Σάκχαρο", "Χοληστερίνη"])
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
                
                # DEEP PARSER CALL
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
                st.error(f"Error {file.name}: {e}")
            bar.progress((i+1)/len(uploaded_files))
            
        if all_data:
            st.session_state.df_master = pd.DataFrame(all_data).sort_values('Date')
            st.success("Έτοιμο!")

# MAIN VIEW
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    # Filter columns to selected only
    cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
    final_df = df[cols].copy()
    
    # Format date for display
    display_df = final_df.copy()
    display_df['Date'] = display_df['Date'].dt.strftime('%d/%m/%Y')

    # --- 1. TABLE CENTERED ---
    st.subheader("📋 Πίνακας Δεδομένων")
    
    # Χρήση CSS class 'stDataFrame' που ορίσαμε πάνω για κεντράρισμα
    st.dataframe(display_df, use_container_width=True)

    # --- 2. CHART ---
    st.subheader("📈 Ιστορικό Γράφημα")
    if len(cols) > 2: # Date, File + at least 1 metric
        plot_df = final_df.melt(id_vars=['Date', 'Αρχείο'], var_name='Εξέταση', value_name='Τιμή').dropna()
        fig = px.line(plot_df, x='Date', y='Τιμή', color='Εξέταση', markers=True, title="Πορεία Εξετάσεων")
        fig.update_layout(title_x=0.5)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Επίλεξε εξετάσεις για να δεις γράφημα.")
        fig = None

    # --- 3. EXPORTS (PDF & EXCEL WITH CHART) ---
    st.divider()
    st.subheader("📥 Εξαγωγή Αναφοράς")
    
    c1, c2 = st.columns(2)
    
    # EXCEL BUTTON
    with c1:
        if fig:
            # Note: to_image requires kaleido package
            try:
                excel_data = user_xlsxwriter_logic(io.BytesIO(), final_df, fig)
                st.download_button(
                    "📊 Excel με Γράφημα",
                    excel_data.getvalue(),
                    "report_with_chart.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.warning(f"Για εξαγωγή γραφήματος σε Excel χρειάζεται το 'kaleido'. Εξάγω μόνο δεδομένα. ({e})")
                # Fallback simple excel
                simple_out = io.BytesIO()
                final_df.to_excel(simple_out, index=False)
                st.download_button("📊 Απλό Excel", simple_out.getvalue(), "data.xlsx")

    # PDF BUTTON
    with c2:
        if fig:
            try:
                img_bytes = fig.to_image(format="png")
                pdf_bytes = create_pdf_report(display_df, img_bytes)
                st.download_button(
                    "📄 PDF Report (Συνολικό)",
                    pdf_bytes,
                    "lab_report.pdf",
                    "application/pdf"
                )
            except Exception as e:
                st.error(f"Σφάλμα PDF: {e}")
