import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Surgical Extractor", layout="wide")
st.title("🔬 Χειρουργική Εξαγωγή (CSV Pattern)")
st.markdown("Ειδικά σχεδιασμένο για αρχεία που έχουν μορφή: `\"Εξέταση\",\"Αποτέλεσμα\"`")

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

def find_value_in_csv_format(text, keyword):
    """
    Ψάχνει για το μοτίβο: "KEYWORD...", "VALUE"
    Αγνοεί τα πάντα εκτός από αυτό το ζευγάρι.
    """
    # 1. Φτιάχνουμε ένα Regex που ψάχνει:
    #    " (οτιδήποτε περιέχει τη λέξη κλειδί) "  <-- Ομάδα 1
    #    ακολουθούμενο από κόμμα ,
    #    " (Η ΤΙΜΗ ΠΟΥ ΘΕΛΟΥΜΕ) "                 <-- Ομάδα 2
    
    # (?i) = αδιαφορία για κεφαλαία/μικρά
    # [^"]* = οποιοσδήποτε χαρακτήρας εκτός από εισαγωγικά
    pattern = rf'(?i)"[^"]*{keyword}[^"]*"\s*,\s*"([^"]*)"'
    
    match = re.search(pattern, text)
    if match:
        raw_value = match.group(1) # Παίρνουμε το περιεχόμενο του δεύτερου "..."
        
        # Καθαρισμός της τιμής από σκουπίδια ($, *, κενά)
        clean_val = raw_value.replace('$', '').replace('*', '').replace(' ', '')
        clean_val = clean_val.replace(',', '.') # Αλλαγή υποδιαστολής
        
        # Προσπάθεια μετατροπής σε αριθμό
        try:
            return float(clean_val)
        except ValueError:
            return None
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF (Μορφής CSV)", type="pdf", accept_multiple_files=True)

# --- ΛΕΞΙΚΟ (Τι ψάχνουμε μέσα στα πρώτα εισαγωγικά) ---
metrics_config = {
    "Αιμοπετάλια (PLT)": "PLT", # Ψάχνει για "PLT..."
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
if st.button("🚀 ΕΞΑΓΩΓΗ") and uploaded_files:
    results = []
    bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        with pdfplumber.open(file) as pdf:
            full_text = ""
            for page in pdf.pages:
                # Εδώ παίρνουμε το raw text όπως είναι
                full_text += page.extract_text() or ""
        
        row = {'Αρχείο': file.name, 'Ημερομηνία': extract_date(full_text, file.name)}
        
        # Αντικατάσταση αλλαγών γραμμής με τίποτα, για να κολλήσουν τα "KEY","VAL" αν σπάσουν
        # Αλλά προσεκτικά: Τα CSV συνήθως έχουν \n στο τέλος της γραμμής.
        # Το regex δουλεύει καλύτερα στο raw text.
        
        for metric_name in selected_metrics:
            keyword = metrics_config[metric_name]
            val = find_value_in_csv_format(full_text, keyword)
            row[metric_name] = val
            
        results.append(row)
        bar.progress((i + 1) / len(uploaded_files))

    if results:
        df = pd.DataFrame(results)
        
        # Ταξινόμηση Ημερομηνίας
        df['DateSort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('DateSort').drop(columns=['DateSort'])
        
        st.success("✅ Ολοκληρώθηκε")
        st.dataframe(df)
        
        # Download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="final_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
