import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Custom CSV-PDF Extractor", layout="wide")
st.title("🔓 Ειδική Εξαγωγή για τα Αρχεία σου")
st.markdown("Ο κώδικας αυτός είναι ρυθμισμένος να διαβάζει τη μορφή `\"Εξέταση\",\"Τιμή\"` που έχουν τα PDF σου.")

def clean_and_convert(value_str):
    """
    Παίρνει το "4,38" ή "$29*" και το κάνει αριθμό.
    """
    if not value_str: return None
    # Καθαρίζουμε ΟΛΑ τα σύμβολα εκτός από αριθμούς και κόμμα
    clean = re.sub(r"[^0-9,]", "", value_str)
    # Αλλάζουμε το κόμμα σε τελεία
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def extract_date(text, filename):
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def parse_special_format(text, keyword):
    """
    Ψάχνει ακριβώς τη δομή των δικών σου αρχείων.
    Regex εξήγηση:
    1. "              -> Ξεκίνα με εισαγωγικά
    2. [^"]*KEYWORD   -> Βρες τη λέξη κλειδί μέσα στα εισαγωγικά
    3. [^"]*"         -> Κλείσε τα πρώτα εισαγωγικά
    4. \s*,\s* -> Βρες το κόμμα (ίσως με κενά)
    5. "([^"]*)"      -> ΠΙΑΣΕ το περιεχόμενο των επόμενων εισαγωγικών (Η ΤΙΜΗ)
    """
    pattern = rf'"[^"]*{keyword}[^"]*"\s*,\s*"([^"]*)"'
    
    # Ψάχνουμε αδιαφορώντας για κεφαλαία/μικρά (?i)
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        raw_value = match.group(1) # Αυτό είναι π.χ. το "4,38" ή "$29*"
        return clean_and_convert(raw_value)
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF", type="pdf", accept_multiple_files=True)

# --- ΛΕΞΙΚΟ (Τι γράφει μέσα στα εισαγωγικά το PDF) ---
metrics_map = {
    "Αιμοπετάλια": "PLT",        # Ψάχνει για "PLT...","..."
    "Αιμοσφαιρίνη": "HGB",
    "Λευκά": "WBC",
    "Αιματοκρίτης": "HCT",
    "Σάκχαρο": "Σάκχαρο",
    "Χοληστερίνη": "Χοληστερίνη",
    "Τριγλυκερίδια": "Τριγλυκερίδια",
    "Σίδηρος": "Σίδηρος",
    "B12": "B12",
    "TSH": "TSH",
    "Κάλιο": "Κάλιο",
    "Νάτριο": "Νάτριο"
}

selected_metrics = st.multiselect("Επιλογή Εξετάσεων:", list(metrics_map.keys()), default=["Αιμοπετάλια"])

if st.button("🚀 ΤΡΕΞΕ ΤΟ") and uploaded_files:
    results = []
    bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        try:
            with pdfplumber.open(file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    # Προσθέτουμε extract_text()
                    full_text += (page.extract_text() or "") + "\n"
            
            row = {'Αρχείο': file.name, 'Ημερομηνία': extract_date(full_text, file.name)}
            
            for label in selected_metrics:
                keyword = metrics_map[label]
                val = parse_special_format(full_text, keyword)
                row[label] = val
            
            results.append(row)
            
        except Exception as e:
            st.error(f"Error {file.name}: {e}")
            
        bar.progress((i + 1) / len(uploaded_files))

    if results:
        df = pd.DataFrame(results)
        
        # Ταξινόμηση
        df['SortDate'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('SortDate').drop(columns=['SortDate'])
        
        st.success("✅ ΕΠΙΤΕΛΟΥΣ! Τα δεδομένα διαβάστηκαν.")
        st.dataframe(df)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="final_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
