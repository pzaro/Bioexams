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
import numpy as np

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Lab Analytics Pro", layout="wide")
st.title("🧬 Medical Lab Analytics & Statistics")
st.markdown("Εξαγωγή δεδομένων -> Γραφήματα -> Στατιστική Επαλήθευση (P-value/Regression)")

# --- 1. AUTH & SETUP ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Error Auth: {e}")
        return None

# --- 2. CLEANING UTILS ---
def clean_number(val_str):
    if not val_str: return None
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '') 
    # Αφαιρούμε και το < > γιατί στατιστικά δεν μπορούμε να τα επεξεργαστούμε εύκολα
    
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def find_first_number(s):
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s)
    for num in numbers:
        cleaned = clean_number(num)
        if cleaned is not None:
            return cleaned
    return None

# --- 3. SMART PARSER (LOOK-AHEAD) ---
def parse_google_text_smart(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            if any(key.upper() in line.upper() for key in keywords):
                val = find_first_number(line)
                if val is None and i + 1 < len(lines):
                    val = find_first_number(lines[i+1])
                
                if val is not None:
                    # Φίλτρα Λογικής
                    if val > 1900 and val < 2100 and "B12" not in metric_name: continue
                    if "Αιμοπετάλια" in metric_name and val < 10: continue
                    if "WBC" in metric_name and val > 100: continue # Λάθος ανάγνωση
                    
                    results[metric_name] = val
                    break
    return results

# --- 4. DATA LOADER (SESSION STATE) ---
# Αποθηκεύουμε τα δεδομένα για να μην ξανα-καλούμε τη Google όταν αλλάζεις φίλτρα
if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# --- 5. STATS FUNCTIONS ---
def run_statistics(df, col_x, col_y):
    # Καθαρισμός NaN
    clean_df = df[[col_x, col_y]].dropna()
    
    if len(clean_df) < 3:
        return "⚠️ Χρειάζονται τουλάχιστον 3 κοινές μετρήσεις για στατιστική ανάλυση."
    
    x = clean_df[col_x]
    y = clean_df[col_y]
    
    # 1. Pearson Correlation
    corr, p_value = stats.pearsonr(x, y)
    
    # 2. Linear Regression (OLS)
    X = sm.add_constant(x) # Προσθήκη σταθεράς
    model = sm.OLS(y, X).fit()
    
    # Ερμηνεία
    significance = "Στατιστικά ΣΗΜΑΝΤΙΚΗ" if p_value < 0.05 else "ΜΗ Στατιστικά Σημαντική"
    
    report = f"""
    ### 📊 Στατιστική Αναφορά: {col_x} vs {col_y}
    
    **1. Συσχέτιση (Correlation):**
    * **Συντελεστής Pearson (r):** {corr:.4f} 
        *(Το 1 σημαίνει τέλεια θετική σχέση, το -1 τέλεια αρνητική, το 0 καμία σχέση)*
    * **P-value:** {p_value:.5f}
    * **Συμπέρασμα:** Η σχέση είναι **{significance}** (όριο p < 0.05).
    
    **2. Γραμμική Παλινδρόμηση (Regression):**
    * **R-squared:** {model.rsquared:.4f}
        *(Εξηγεί το {model.rsquared*100:.1f}% της μεταβλητότητας)*
    * **Εξίσωση:** {col_y} = {model.params.iloc[0]:.2f} + ({model.params.iloc[1]:.2f} * {col_x})
    
    **💡 Ερμηνεία με απλά λόγια:**
    """
    
    if p_value < 0.05:
        if corr > 0:
            report += f"Υπάρχει σοβαρή ένδειξη ότι όταν αυξάνεται το **{col_x}**, τείνει να αυξάνεται και το **{col_y}**."
        else:
            report += f"Υπάρχει σοβαρή ένδειξη ότι όταν αυξάνεται το **{col_x}**, το **{col_y}** τείνει να μειώνεται."
    else:
        report += f"Δεν βρέθηκε στατιστικά αποδεδειγμένη σχέση μεταξύ τους με τα υπάρχοντα δεδομένα ({len(clean_df)} δείγματα). Η όποια σχέση φαίνεται τυχαία."
        
    return report, clean_df, model

# --- 6. ΚΥΡΙΩΣ ΕΦΑΡΜΟΓΗ ---

uploaded_files = st.sidebar.file_uploader("1. Ανέβασε PDF", type="pdf", accept_multiple_files=True)

# Πλήρης Λίστα
ALL_METRICS = {
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Ουδετερόφιλα %": ["NEUT", "Ουδετερόφιλα"],
    "Λεμφοκύτταρα %": ["LYMPH", "Λεμφοκύτταρα"],
    "Σάκχαρο": ["GLU", "Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Cholesterol", "Χοληστερίνη"],
    "HDL": ["HDL"],
    "LDL": ["LDL"],
    "Τριγλυκερίδια": ["Triglycerides", "Τριγλυκερίδια"],
    "Σίδηρος": ["Fe ", "Σίδηρος"],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "B12": ["B12"],
    "TSH": ["TSH"],
    "CRP": ["CRP"],
    "Ουρία": ["Urea", "Ουρία"],
    "Κρεατινίνη": ["Creatinine", "Κρεατινίνη"],
    "SGOT": ["SGOT", "AST"],
    "SGPT": ["SGPT", "ALT"],
    "γ-GT": ["GGT", "γ-GT"]
}

# Κουμπί Επεξεργασίας (τρέχει μόνο μια φορά)
if st.sidebar.button("🚀 Επεξεργασία Αρχείων") and uploaded_files:
    client = get_vision_client()
    if client:
        all_data = []
        progress_bar = st.progress(0)
        
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
                
                data = parse_google_text_smart(full_text, ALL_METRICS)
                
                # Ημερομηνία
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                if date_match:
                    data['Date'] = pd.to_datetime(date_match.group(1), dayfirst=True)
                else:
                    m = re.search(r'(\d{6})', file.name)
                    if m:
                        d_str = m.group(1)
                        data['Date'] = pd.to_datetime(f"{d_str[4:6]}/{d_str[2:4]}/20{d_str[0:2]}", dayfirst=True)
                    else:
                        data['Date'] = pd.NaT # Not a Time
                
                data['Αρχείο'] = file.name
                all_data.append(data)
                
            except Exception as e:
                st.error(f"Error {file.name}: {e}")
            progress_bar.progress((i+1)/len(uploaded_files))
            
        if all_data:
            st.session_state.df_master = pd.DataFrame(all_data).sort_values('Date')
            st.success("Η επεξεργασία ολοκληρώθηκε! Τώρα μπορείς να παίξεις με τα φίλτρα.")

# --- ANALYTICS DASHBOARD ---
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    st.divider()
    
    # --- SIDEBAR FILTERS ---
    st.sidebar.header("2. Φίλτρα & Επιλογές")
    
    # 1. Date Filter
    time_filter = st.sidebar.radio("Χρονικό Διάστημα:", ["Όλα", "Τελευταίο 3μηνο", "Τελευταίο 6μηνο", "Τελευταίο Έτος"])
    
    if time_filter != "Όλα" and not df['Date'].isna().all():
        last_date = df['Date'].max()
        if time_filter == "Τελευταίο 3μηνο":
            cutoff = last_date - pd.DateOffset(months=3)
        elif time_filter == "Τελευταίο 6μηνο":
            cutoff = last_date - pd.DateOffset(months=6)
        elif time_filter == "Τελευταίο Έτος":
            cutoff = last_date - pd.DateOffset(years=1)
        
        df = df[df['Date'] >= cutoff]
    
    # 2. Metric Filter
    available_cols = [c for c in df.columns if c not in ['Date', 'Αρχείο']]
    selected_metrics = st.sidebar.multiselect("Επιλογή Εξετάσεων:", available_cols, default=available_cols[:3])
    
    # --- MAIN VIEW ---
    
    # Tab layout
    tab1, tab2, tab3 = st.tabs(["📋 Πίνακας Δεδομένων", "📈 Ιστορικά Γραφήματα", "🧮 Στατιστική Επαλήθευση"])
    
    with tab1:
        st.subheader(f"Δεδομένα ({time_filter})")
        # Format Date for display
        display_df = df.copy()
        display_df['Date'] = display_df['Date'].dt.strftime('%d/%m/%Y')
        cols_to_show = ['Date', 'Αρχείο'] + selected_metrics
        st.dataframe(display_df[cols_to_show], use_container_width=True)
        
        # Excel Download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            display_df[cols_to_show].to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", output.getvalue(), "analytics_results.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with tab2:
        st.subheader("Ιστορική Εξέλιξη")
        if not selected_metrics:
            st.warning("Επίλεξε εξετάσεις από το μενού αριστερά.")
        else:
            # Create Line Chart with Plotly
            # Χρειάζεται να κάνουμε melt το dataframe για να το καταλάβει το plotly
            plot_df = df.melt(id_vars=['Date'], value_vars=selected_metrics, var_name='Εξέταση', value_name='Τιμή')
            
            fig = px.line(plot_df, x='Date', y='Τιμή', color='Εξέταση', markers=True, 
                          title=f"Πορεία Εξετάσεων - {time_filter}",
                          hover_data={'Date': '|%d/%m/%Y'})
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.subheader("🤖 Στατιστική Ανάλυση & Συσχέτιση")
        st.markdown("Επίλεξε δύο μεγέθη για να δούμε αν επηρεάζει το ένα το άλλο (π.χ. *Σίδηρος vs Αιμοσφαιρίνη*).")
        
        col1, col2 = st.columns(2)
        with col1:
            stat_x = st.selectbox("Μεταβλητή Χ (Ανεξάρτητη)", available_cols, index=0)
        with col2:
            stat_y = st.selectbox("Μεταβλητή Y (Εξαρτημένη)", available_cols, index=1 if len(available_cols)>1 else 0)
            
        if st.button("Τρέξε Στατιστικά"):
            if stat_x == stat_y:
                st.error("Επίλεξε δύο διαφορετικές μεταβλητές.")
            else:
                report, clean_data, model = run_statistics(df, stat_x, stat_y)
                st.markdown(report)
                
                # Scatter Plot με γραμμή παλινδρόμησης
                if isinstance(model, sm.regression.linear_model.RegressionResultsWrapper):
                    fig_reg = px.scatter(clean_data, x=stat_x, y=stat_y, trendline="ols",
                                         title=f"Γραμμική Παλινδρόμηση: {stat_x} vs {stat_y}",
                                         labels={stat_x: stat_x, stat_y: stat_y})
                    st.plotly_chart(fig_reg, use_container_width=True)
