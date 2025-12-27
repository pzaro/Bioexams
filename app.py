import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Master Medical Extractor", layout="wide")
st.title("🧬 Master Extractor: Ολική Επανεκκίνηση")
st.markdown("Αυτή η έκδοση περιέχει ΟΛΕΣ τις μεθόδους ανάγνωσης (CSV, Text, Table).")

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΕΞΑΓΩΓΗΣ ---

def clean_value(val_str):
    """Καθαρίζει μια τιμή από σκουπίδια και την κάνει αριθμό."""
    if not val_str: return None
    # Αφαιρούμε $, *, ", κενά
    clean = val_str.replace('$', '').replace('*', '').replace('"', '').replace(' ', '')
    # Αλλαγή κόμματος σε τελεία
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def extract_date(text, filename):
    # Ψάχνουμε ημερομηνία στο κείμενο
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    
    # Ψάχνουμε στο όνομα αρχείου
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def extract_from_csv_structure(text, keyword):
    """
    ΜΕΘΟΔΟΣ 1 (Η πιο ισχυρή για τα αρχεία σου):
    Ψάχνει για: "KEYWORD...", "VALUE"
    """
    # Regex: Βρες κάτι σε εισαγωγικά που έχει τη λέξη κλειδί, μετά κόμμα, μετά εισαγωγικά με την τιμή
    pattern = rf'(?i)"[^"]*{keyword}[^"]*"\s*,\s*"([^"]*)"'
    match = re.search(pattern, text)
    if match:
        return clean_value(match.group(1))
    return None

def extract_from_plain_text(text, keyword):
    """
    ΜΕΘΟΔΟΣ 2 (Εφεδρική):
    Ψάχνει για: KEYWORD (οτιδήποτε) NUMBER
    """
    # Καθαρίζουμε το κείμενο από εισαγωγικά για να γίνει απλό
    clean_text = text.replace('"', ' ').replace(',', '.')
    pattern = rf"(?i){keyword}.{{0,40}}(\d+[.]?\d*)"
    match = re.search(pattern, clean_text)
    if match:
        return float(match.group(1))
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF εδώ", type="pdf", accept_multiple_files=True)
debug_mode = st.checkbox("🕵️ ΕΝΕΡΓΟΠΟΙΗΣΗ DEBUG (Δείξε μου το κείμενο)")

# --- ΛΕΞΙΚΟ ΑΝΑΖΗΤΗΣΗΣ ---
metrics_config = {
    "Αιμοπετάλια (PLT)": "PLT",
    "Αιμοσφαιρίνη (HGB)": "HGB",
    "Λευκά (WBC)": "WBC",
    "Αιματοκρίτης": "HCT",
    "Σάκχαρο": "Σάκχαρο",
    "Χοληστερίνη": "Χοληστερίνη",
    "Τριγλυκερίδια": "Τριγλυκερίδια",
    "Σίδηρος": "Σίδηρος",
    "Φερριτίνη": "Φερριτίνη",
    "B12": "B12",
    "TSH": "TSH",
    "T4": "T4",
    "Κάλιο": "Κάλιο",
    "Νάτριο": "Νάτριο"
}

selected_metrics = st.multiselect("Επιλογή Εξετάσεων:", list(metrics_config.keys()), default=["Αιμοπετάλια (PLT)"])

# --- ΕΚΤΕΛΕΣΗ ---
if st.button("🚀 ΕΚΚΙΝΗΣΗ") and uploaded_files:
    results = []
    bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        try:
            with pdfplumber.open(file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    full_text += (page.extract_text() or "") + "\n"
            
            # --- DEBUGGING VIEW ---
            if debug_mode:
                with st.expander(f"🔍 RAW TEXT: {file.name}"):
                    st.text(full_text[:600]) # Δείξε τους πρώτους 600 χαρακτήρες
            
            row = {'Αρχείο': file.name, 'Ημερομηνία': extract_date(full_text, file.name)}
            
            for metric in selected_metrics:
                keyword = metrics_config[metric]
                
                # Δοκιμή 1: CSV Μορφή (Εισαγωγικά)
                val = extract_from_csv_structure(full_text, keyword)
                
                # Δοκιμή 2: Απλό Κείμενο (αν απέτυχε η 1)
                if val is None:
                    val = extract_from_plain_text(full_text, keyword)
                
                # Φίλτρο Ασφαλείας: Αν βρήκε έτος (π.χ. 2024) αντί για τιμή
                if val and val > 1900 and keyword != "B12": 
                    val = None
                    
                row[metric] = val
                
            results.append(row)
            
        except Exception as e:
            st.error(f"Σφάλμα στο αρχείο {file.name}: {e}")
        
        bar.progress((i + 1) / len(uploaded_files))

    # --- ΠΑΡΟΥΣΙΑΣΗ ---
    if results:
        df = pd.DataFrame(results)
        
        # Ταξινόμηση
        df['DateSort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('DateSort').drop(columns=['DateSort'])
        
        st.success("✅ Η ανάλυση ολοκληρώθηκε!")
        st.dataframe(df)
        
        # Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="master_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
