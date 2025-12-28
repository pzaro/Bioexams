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

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Medical Commander", layout="wide")
st.title("🩸 Medical Lab Commander")
st.markdown("1. Επιλογή Εξετάσεων -> 2. Εξαγωγή -> 3. Στατιστική Ανάλυση & Ιστορικό")

# --- 1. AUTHENTICATION ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Authentication Error: {e}")
        return None

# --- 2. DATA CLEANING (Ενισχυμένο) ---
def clean_number(val_str):
    if not val_str: return None
    
    # 1. Αφαίρεση θορύβου OCR και συμβόλων CSV
    val_str = val_str.replace('"', '').replace("'", "") # Αφαίρεση εισαγωγικών
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    
    # 2. Regex για εξαγωγή καθαρού αριθμού
    # Βρίσκει αριθμούς όπως: 12,6 | 4.52 | 201
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.') # Αλλαγή υποδιαστολής
    
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    # Ψάχνει τον πρώτο έγκυρο αριθμό σε μια γραμμή
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s)
    for num in numbers:
        cleaned = clean_number(num)
        if cleaned is not None:
            return cleaned
    return None

# --- 3. THE ENGINE (Deep Look-Ahead) ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    
    # Σπάμε σε γραμμές και αφαιρούμε τα πολλά κενά
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    # Για κάθε εξέταση που επέλεξε ο χρήστης
    for metric_name, keywords in selected_metrics.items():
        
        for i, line in enumerate(lines):
            # Αν βρούμε τη λέξη κλειδί
            if any(key.upper() in line.upper() for key in keywords):
                
                val = None
                
                # Προσπάθεια 1: ΙΔΙΑ ΓΡΑΜΜΗ
                val = find_first_number(line)
                
                # Προσπάθεια 2: ΕΠΟΜΕΝΗ ΓΡΑΜΜΗ (i+1)
                if val is None and i + 1 < len(lines):
                    val = find_first_number(lines[i+1])
                
                # Προσπάθεια 3: ΜΕΘΕΠΟΜΕΝΗ ΓΡΑΜΜΗ (i+2) - Για δύσκολες περιπτώσεις
                if val is None and i + 2 < len(lines):
                    val = find_first_number(lines[i+2])
                
                # Αν βρέθηκε τιμή, κάνουμε ελέγχους εγκυρότητας
                if val is not None:
                    # Αγνοούμε έτη (εκτός αν είναι B12 που έχει μεγάλες τιμές)
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue
                    
                    # Ειδικά φίλτρα για να μην παίρνουμε σκουπίδια
                    if "PLT" in metric_name and val < 10: continue # Τα PLT δεν είναι ποτέ μονοψήφια
                    if "WBC" in metric_name and val > 100: continue
                    if "HGB" in metric_name and val > 25: continue
                    
                    results[metric_name] = val
                    break # Βρήκαμε τιμή, πάμε στην επόμενη εξέταση
    return results

# --- 4. DATA STORAGE ---
if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# --- 5. STATS ENGINE ---
def run_statistics(df, col_x, col_y):
    clean_df = df[[col_x, col_y]].dropna()
    if len(clean_df) < 3:
        return "⚠️ Χρειάζονται τουλάχιστον 3 κοινές μετρήσεις για στατιστική."
    
    x = clean_df[col_x]
    y = clean_df[col_y]
    
    corr, p_value = stats.pearsonr(x, y)
    X = sm.add_constant(x)
    model = sm.OLS(y, X).fit()
    
    significance = "Στατιστικά ΣΗΜΑΝΤΙΚΗ" if p_value < 0.05 else "ΜΗ Στατιστικά Σημαντική"
    
    report = f"""
    ### 📊 Ανάλυση: {col_x} vs {col_y}
    - **Συσχέτιση (r):** {corr:.4f}
    - **P-value:** {p_value:.5f} ({significance})
    - **R-squared:** {model.rsquared:.4f} (Ερμηνευτικότητα μοντέλου: {model.rsquared*100:.1f}%)
    """
    return report, clean_df, model

# --- 6. SIDEBAR & CONFIG ---

# ΛΕΞΙΚΟ ΟΛΩΝ ΤΩΝ ΕΞΕΤΑΣΕΩΝ
ALL_METRICS_DB = {
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"], # Το διορθώσαμε!
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Ουδετερόφιλα %": ["NEUT", "Ουδετερόφιλα", "NE "], # Προσοχή στο NE
    "Λεμφοκύτταρα %": ["LYMPH", "Λεμφοκύτταρα"],
    "Σάκχαρο": ["GLU", "Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Cholesterol", "Χοληστερίνη"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "Τριγλυκερίδια": ["Triglycerides", "Τριγλυκερίδια"],
    "Σίδηρος": ["Fe ", "Σίδηρος"], # Κενό στο Fe για να μην μπερδεύει Ferritin
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "B12": ["B12"],
    "TSH": ["TSH"],
    "T3": ["T3 "],
    "T4": ["T4 "],
    "CRP": ["CRP"],
    "Ουρία": ["Urea", "Ουρία"],
    "Κρεατινίνη": ["Creatinine", "Κρεατινίνη"],
    "SGOT": ["SGOT", "AST"],
    "SGPT": ["SGPT", "ALT"],
    "γ-GT": ["GGT", "γ-GT"]
}

st.sidebar.header("⚙️ Βήμα 1: Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Ανέβασε PDF", type="pdf", accept_multiple_files=True)

# 1. PRE-SELECTION (Αυτό που ζήτησες)
st.sidebar.subheader("Επιλογή Εξετάσεων (Πριν την εξαγωγή)")
# Προεπιλέγουμε τα βασικά
default_selection = ["Ερυθρά (RBC)", "Αιμοσφαιρίνη (HGB)", "Αιμοπετάλια (PLT)", "Λευκά (WBC)", "Σάκχαρο", "Χοληστερίνη"]
selected_metric_keys = st.sidebar.multiselect(
    "Ποιες εξετάσεις να ψάξω;", 
    list(ALL_METRICS_DB.keys()), 
    default=default_selection
)

# Φτιάχνουμε το μικρό λεξικό για την αναζήτηση
active_metrics_map = {k: ALL_METRICS_DB[k] for k in selected_metric_keys}

# 2. RUN BUTTON
if st.sidebar.button("🚀 ΕΝΑΡΞΗ ΕΞΑΓΩΓΗΣ") and uploaded_files:
    client = get_vision_client()
    if client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            try:
                # Μετατροπή PDF -> Εικόνα
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
                
                # --- ΕΔΩ ΤΡΕΧΕΙ Η ΝΕΑ DEEP LOGIC ---
                data = parse_google_text_deep(full_text, active_metrics_map)
                
                # Ημερομηνία
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                if date_match:
                    data['Date'] = pd.to_datetime(date_match.group(1), dayfirst=True)
                else:
                    # Αν δεν βρεθεί μέσα, ψάχνουμε στο όνομα αρχείου YYMMDD
                    m = re.search(r'(\d{6})', file.name)
                    if m:
                        d_str = m.group(1)
                        # Υποθέτουμε μορφή YYMMDD
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
            st.success("✅ Ετοιμο!")

# --- 7. MAIN DASHBOARD ---
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    # --- ΦΙΛΤΡΑ DASHBOARD ---
    st.divider()
    st.header("📊 Ανάλυση Αποτελεσμάτων")
    
    col_filter_1, col_filter_2 = st.columns(2)
    
    with col_filter_1:
        time_period = st.radio("Χρονικό Διάστημα:", ["Όλα", "3 Μήνες", "6 Μήνες", "1 Έτος"], horizontal=True)
    
    # Εφαρμογή φίλτρου χρόνου
    if time_period != "Όλα" and not df['Date'].isna().all():
        max_date = df['Date'].max()
        if time_period == "3 Μήνες": cutoff = max_date - pd.DateOffset(months=3)
        elif time_period == "6 Μήνες": cutoff = max_date - pd.DateOffset(months=6)
        elif time_period == "1 Έτος": cutoff = max_date - pd.DateOffset(years=1)
        df = df[df['Date'] >= cutoff]

    # --- TABS ---
    tab1, tab2, tab3 = st.tabs(["📋 Δεδομένα", "📈 Γραφήματα", "🧮 Στατιστικά"])
    
    with tab1:
        # Format ημερομηνίας για εμφάνιση
        show_df = df.copy()
        show_df['Date'] = show_df['Date'].dt.strftime('%d/%m/%Y')
        
        # Ταξινόμηση στηλών
        cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
        st.dataframe(show_df[cols], use_container_width=True)
        
        # Excel
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='openpyxl') as writer:
            show_df[cols].to_excel(writer, index=False)
        st.download_button("📥 Excel Download", out.getvalue(), "lab_results.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with tab2:
        st.subheader("Ιστορικό Γράφημα")
        if not selected_metric_keys:
            st.info("Δεν έχουν επιλεγεί εξετάσεις.")
        else:
            # Μετατροπή σε Long Format για το Plotly
            plot_df = df.melt(id_vars=['Date'], value_vars=[c for c in selected_metric_keys if c in df.columns], var_name='Εξέταση', value_name='Τιμή')
            fig = px.line(plot_df, x='Date', y='Τιμή', color='Εξέταση', markers=True, title="Διαχρονική Εξέλιξη")
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.subheader("Στατιστική Επαλήθευση (Regression/Correlation)")
        stat_cols = [c for c in df.columns if c not in ['Date', 'Αρχείο']]
        
        c1, c2 = st.columns(2)
        x_axis = c1.selectbox("Μεταβλητή X", stat_cols, index=0 if len(stat_cols)>0 else None)
        y_axis = c2.selectbox("Μεταβλητή Y", stat_cols, index=1 if len(stat_cols)>1 else 0)
        
        if st.button("Υπολογισμός Στατιστικών"):
            if x_axis and y_axis and x_axis != y_axis:
                report, clean_data, model = run_statistics(df, x_axis, y_axis)
                st.markdown(report)
                
                fig_reg = px.scatter(clean_data, x=x_axis, y=y_axis, trendline="ols", title=f"Παλινδρόμηση: {x_axis} vs {y_axis}")
                st.plotly_chart(fig_reg, use_container_width=True)
            else:
                st.warning("Επίλεξε δύο διαφορετικές μεταβλητές.")
