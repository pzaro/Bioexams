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

# --- ΡΥΘΜΙΣΕΙΣ ΣΕΛΙΔΑΣ & CSS ---
st.set_page_config(page_title="Medical Commander Design", layout="wide")

# Custom CSS για ομορφιά και κεντράρισμα
st.markdown("""
    <style>
    /* Εισαγωγή γραμματοσειράς */
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;700&display=swap');

    html, body, [class*="css"]  {
        font-family: 'Roboto', sans-serif;
    }
    
    /* Κεντράρισμα τίτλων */
    h1, h2, h3 {
        text-align: center;
        color: #0e1117;
    }
    
    /* Στυλ για τα tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
        justify-content: center;
    }

    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #f0f2f6;
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }

    .stTabs [aria-selected="true"] {
        background-color: #ffffff;
        border-bottom: 2px solid #ff4b4b;
    }
    
    /* Ευθυγράμμιση πίνακα στη μέση */
    .stDataFrame {
        margin: 0 auto;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🩸 Medical Lab Commander")
st.markdown("<h4 style='text-align: center; color: gray;'>Advanced Analytics & Beautiful Data</h4>", unsafe_allow_html=True)
st.divider()

# --- 1. AUTHENTICATION ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Authentication Error: {e}")
        return None

# --- 2. DATA CLEANING ---
def clean_number(val_str):
    if not val_str: return None
    val_str = val_str.replace('"', '').replace("'", "")
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '').replace('<', '').replace('>', '')
    val_str = val_str.replace('H', '').replace('L', '') 
    
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

# --- 3. PARSER ENGINE ---
def parse_google_text_deep(full_text, selected_metrics):
    results = {}
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    for metric_name, keywords in selected_metrics.items():
        for i, line in enumerate(lines):
            if any(key.upper() in line.upper() for key in keywords):
                val = find_first_number(line)
                if val is None and i + 1 < len(lines):
                    val = find_first_number(lines[i+1])
                if val is None and i + 2 < len(lines):
                    val = find_first_number(lines[i+2])
                
                if val is not None:
                    if val > 1990 and val < 2030 and "B12" not in metric_name: continue
                    if "PLT" in metric_name and val < 10: continue
                    if "WBC" in metric_name and val > 100: continue
                    if "HGB" in metric_name and val > 25: continue
                    if "pH" in metric_name and val > 14: continue
                    
                    results[metric_name] = val
                    break 
    return results

# --- 4. STYLE FUNCTION (Η ΜΑΓΕΙΑ ΤΟΥ DESIGN) ---
def style_dataframe(df):
    """
    Αυτή η συνάρτηση παίρνει τα δεδομένα και τα κάνει 'κούκλα'.
    """
    # Μορφοποίηση ημερομηνίας
    df_styled = df.copy()
    if 'Date' in df_styled.columns:
        df_styled['Date'] = df_styled['Date'].dt.strftime('%d/%m/%Y')
    
    # Εφαρμογή Pandas Styler
    styler = df_styled.style.set_properties(**{
        'text-align': 'center',      # Κεντράρισμα κειμένου
        'font-size': '16px',         # Μεγαλύτερη γραμματοσειρά
        'border': '1px solid #f0f2f6'
    })
    
    # Κεντράρισμα Κεφαλίδων (Headers)
    styler.set_table_styles([
        {'selector': 'th', 'props': [
            ('text-align', 'center'), 
            ('background-color', '#ff4b4b'), 
            ('color', 'white'),
            ('font-weight', 'bold'),
            ('padding', '10px')
        ]},
        {'selector': 'td', 'props': [
            ('padding', '10px')      # "Αέρας" στα κελιά
        ]}
    ])
    
    # Formatting αριθμών (2 δεκαδικά)
    styler.format(precision=2)
    
    return styler

# --- 5. SESSION STATE ---
if 'df_master' not in st.session_state:
    st.session_state.df_master = None

# --- 6. STATISTICS ---
def run_statistics(df, col_x, col_y):
    clean_df = df[[col_x, col_y]].apply(pd.to_numeric, errors='coerce').dropna()
    
    if len(clean_df) < 3:
        msg = f"⚠️ Ανεπαρκή δεδομένα ({len(clean_df)} εγγραφές)."
        return msg, None, None
    
    x = clean_df[col_x]
    y = clean_df[col_y]
    
    if x.std() == 0 or y.std() == 0:
        msg = f"⚠️ Η μία μεταβλητή είναι σταθερή."
        return msg, None, None

    try:
        corr, p_value = stats.pearsonr(x, y)
        X = sm.add_constant(x)
        model = sm.OLS(y, X).fit()
        significance = "Στατιστικά ΣΗΜΑΝΤΙΚΗ" if p_value < 0.05 else "ΜΗ Στατιστικά Σημαντική"
        
        report = f"""
        ### 📊 Ανάλυση: {col_x} vs {col_y}
        - **Δείγματα:** {len(clean_df)}
        - **Συσχέτιση (r):** {corr:.4f}
        - **P-value:** {p_value:.5f} ({significance})
        - **R-squared:** {model.rsquared:.4f}
        """
        return report, clean_df, model
    except Exception as e:
        return f"⚠️ Σφάλμα: {str(e)}", None, None

# --- 7. CONFIG & SIDEBAR ---
ALL_METRICS_DB = {
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "MCV (Μέσος Όγκος)": ["MCV", "Μέσος Όγκος"],
    "MCH": ["MCH", "Μέση Περιεκτ"],
    "MCHC": ["MCHC", "Μέση Πυκν"],
    "RDW": ["RDW", "Εύρος Κατανομής"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "MPV": ["MPV", "Μέσος Όγκος Αιμοπεταλίων"],
    "PCT (Αιμοπεταλιοκρίτης)": ["PCT", "Αιμοπεταλιοκρίτης"],
    "PDW": ["PDW"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Ουδετερόφιλα %": ["NEUT", "Ουδετερόφιλα", "NE "],
    "Λεμφοκύτταρα %": ["LYMPH", "Λεμφοκύτταρα"],
    "Μονοπύρηνα %": ["MONO", "Μονοπύρηνα"],
    "Ηωσινόφιλα %": ["EOS", "Ηωσινόφιλα"],
    "Βασέοφιλα %": ["BASO", "Βασέοφιλα"],
    "Σάκχαρο (GLU)": ["GLU", "Σάκχαρο", "Glucose"],
    "Ουρία": ["Urea", "Ουρία"],
    "Κρεατινίνη": ["Creatinine", "Κρεατινίνη"],
    "Ουρικό Οξύ": ["Uric Acid", "Ουρικό"],
    "Χοληστερίνη Ολική": ["Cholesterol", "Χοληστερίνη"],
    "HDL (Καλή)": ["HDL"],
    "LDL (Κακή)": ["LDL"],
    "Τριγλυκερίδια": ["Triglycerides", "Τριγλυκερίδια"],
    "Ολική Χολερυθρίνη": ["Bilirubin Total", "Χολερυθρίνη Ολική"],
    "Άμεση Χολερυθρίνη": ["Direct", "Άμεση Χολερυθρίνη"],
    "SGOT (AST)": ["SGOT", "AST", "ΑΣΤ"],
    "SGPT (ALT)": ["SGPT", "ALT", "ΑΛΤ"],
    "γ-GT": ["GGT", "γ-GT", "γGT"],
    "Αλκαλική Φωσφατάση (ALP)": ["ALP", "Αλκαλική"],
    "CPK": ["CPK", "Κρεατινοφωσφοκινάση"],
    "LDH": ["LDH", "Γαλακτική"],
    "Αμυλάση": ["Amylase", "Αμυλάση"],
    "Κάλιο (K)": ["Potassium", "Κάλιο"],
    "Νάτριο (Na)": ["Sodium", "Νάτριο"],
    "Ασβέστιο (Ca)": ["Calcium", "Ασβέστιο"],
    "Μαγνήσιο (Mg)": ["Magnesium", "Μαγνήσιο"],
    "Φώσφορος (P)": ["Phosphorus", "Φώσφορος"],
    "Σίδηρος (Fe)": ["Fe ", "Σίδηρος"],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "Βιταμίνη B12": ["B12", "Cobalamin"],
    "Φυλλικό Οξύ": ["Folic", "Φυλλικό"],
    "Βιταμίνη D3": ["Vit D", "D3", "25-OH"],
    "TSH": ["TSH", "Θυρεοειδοτρόπος"],
    "T3": ["T3 "],
    "T4": ["T4 "],
    "FT3": ["FT3"],
    "FT4": ["FT4"],
    "Anti-TPO": ["TPO", "Αντιθυρεοειδικά"],
    "CRP": ["CRP", "C-Αντιδρώσα"],
    "TKE (Καθίζηση)": ["ESR", "ΤΚΕ", "Ταχύτητα Καθιζήσεως"],
    "Ινωδογόνο": ["Fibrinogen", "Ινωδογόνο"],
    "PT (Χρόνος Προθρομβίνης)": ["PT ", "Προθρομβίνης"],
    "INR": ["INR"],
    "pH Ούρων": ["pH"],
    "Ειδικό Βάρος": ["S.G.", "Ειδικό Βάρος"],
    "Λευκώματα Ούρων": ["Protein", "Λεύκωμα"],
    "PSA": ["PSA"],
    "CEA": ["CEA"],
    "CA 125": ["CA 125"],
    "CA 19-9": ["CA 19-9"]
}

st.sidebar.header("⚙️ Ρυθμίσεις")
uploaded_files = st.sidebar.file_uploader("Ανέβασε PDF", type="pdf", accept_multiple_files=True)

st.sidebar.subheader("Επιλογή Εξετάσεων")
all_keys = list(ALL_METRICS_DB.keys())
default_group = [
    "Ερυθρά (RBC)", "Αιμοσφαιρίνη (HGB)", "Αιμοπετάλια (PLT)", "Λευκά (WBC)",
    "Σάκχαρο (GLU)", "Χοληστερίνη Ολική", "Τριγλυκερίδια", "Σίδηρος (Fe)", "Φερριτίνη",
    "B12", "TSH", "SGOT (AST)", "SGPT (ALT)"
]

container = st.sidebar.container()
all_selected = st.sidebar.checkbox("Επιλογή ΟΛΩΝ (60+ δείκτες)")

if all_selected:
    selected_metric_keys = container.multiselect("Λίστα:", all_keys, default=all_keys)
else:
    selected_metric_keys = container.multiselect("Λίστα:", all_keys, default=default_group)

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
                
                data = parse_google_text_deep(full_text, active_metrics_map)
                
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
            st.success("✅ Δεδομένα έτοιμα!")

# --- 8. DASHBOARD UI ---
if st.session_state.df_master is not None:
    df = st.session_state.df_master.copy()
    
    # Φίλτρο Χρόνου
    time_period = st.radio("", ["Όλα", "3 Μήνες", "6 Μήνες", "1 Έτος"], horizontal=True)
    if time_period != "Όλα" and not df['Date'].isna().all():
        max_d = df['Date'].max()
        if time_period == "3 Μήνες": cutoff = max_d - pd.DateOffset(months=3)
        elif time_period == "6 Μήνες": cutoff = max_d - pd.DateOffset(months=6)
        elif time_period == "1 Έτος": cutoff = max_d - pd.DateOffset(years=1)
        df = df[df['Date'] >= cutoff]

    tab1, tab2, tab3 = st.tabs(["📋 Πίνακας", "📈 Γραφήματα", "🧮 Στατιστικά"])
    
    with tab1:
        # Επιλογή στηλών που έχουν όντως δεδομένα
        cols = ['Date', 'Αρχείο'] + [c for c in selected_metric_keys if c in df.columns]
        
        # --- THE MAGIC LINE FOR STYLING ---
        # 1. Εφαρμόζουμε το στυλ (κεντράρισμα κλπ)
        styled_df = style_dataframe(df[cols])
        
        # 2. Το δείχνουμε στο Streamlit με full width
        st.dataframe(styled_df, use_container_width=True, height=500)
        
        # Excel Export (Clean Dataframe, not styled)
        s_df = df.copy()
        s_df['Date'] = s_df['Date'].dt.strftime('%d/%m/%Y')
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='openpyxl') as writer:
            s_df[cols].to_excel(writer, index=False)
        st.download_button("📥 Λήψη Excel", out.getvalue(), "results.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with tab2:
        if not selected_metric_keys:
            st.info("Επίλεξε εξετάσεις.")
        else:
            plot_df = df.melt(id_vars=['Date'], value_vars=[c for c in selected_metric_keys if c in df.columns], var_name='Εξέταση', value_name='Τιμή')
            fig = px.line(plot_df, x='Date', y='Τιμή', color='Εξέταση', markers=True, title="Ιστορική Εξέλιξη")
            fig.update_layout(title_x=0.5) # Center chart title
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        stat_cols = [c for c in df.columns if c not in ['Date', 'Αρχείο']]
        c1, c2 = st.columns(2)
        x_ax = c1.selectbox("X", stat_cols, index=0 if len(stat_cols)>0 else None)
        y_ax = c2.selectbox("Y", stat_cols, index=1 if len(stat_cols)>1 else 0)
        
        if st.button("Υπολογισμός"):
            if x_ax and y_ax and x_ax != y_ax:
                rep, c_data, mod = run_statistics(df, x_ax, y_ax)
                if c_data is None:
                    st.warning(rep)
                else:
                    st.markdown(rep)
                    fig_r = px.scatter(c_data, x=x_ax, y=y_ax, trendline="ols", title=f"{x_ax} vs {y_ax}")
                    fig_r.update_layout(title_x=0.5)
                    st.plotly_chart(fig_r, use_container_width=True)
            else:
                st.warning("Διάλεξε διαφορετικές μεταβλητές.")
