import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="CSV PDF Splitter", layout="wide")
st.title("✂️ Μέθοδος 'Κόψιμο CSV' (Ειδική για τα αρχεία σου)")
st.markdown("Αυτή η μέθοδος αγνοεί το κείμενο και ψάχνει αποκλειστικά για τη δομή `\"Εξέταση\",\"Τιμή\"`.")

# --- ΣΥΝΑΡΤΗΣΕΙΣ ---

def clean_number(val_str):
    """Μετατρέπει το string σε αριθμό, καθαρίζοντας τα σκουπίδια."""
    if not val_str: return None
    # Κρατάμε μόνο ψηφία και κόμμα/τελεία. Πετάμε $, *, ", γράμματα
    clean = re.sub(r"[^0-9,.]", "", val_str)
    # Αλλαγή υποδιαστολής
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def extract_date(text, filename):
    # Πρώτα ψάχνουμε στο κείμενο
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    
    # Μετά στο όνομα αρχείου
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def parse_csv_line(line, target_keywords):
    """
    Η καρδιά του κώδικα:
    Σπάει τη γραμμή στο διαχωριστικό ","
    """
    # Ελέγχουμε αν η γραμμή έχει τη μορφή "Κάτι","Κάτι άλλο"
    if '","' in line:
        parts = line.split('","')
        
        # Το αριστερό κομμάτι είναι το όνομα της εξέτασης
        # Το μεσαίο κομμάτι είναι η τιμή
        if len(parts) >= 2:
            raw_key = parts[0].replace('"', '').strip() # Καθαρίζουμε το πρώτο "
            raw_val = parts[1].replace('"', '').strip() # Καθαρίζουμε το δεύτερο "
            
            # Ελέγχουμε αν το raw_key περιέχει αυτό που ψάχνουμε
            for key in target_keywords:
                # Χρησιμοποιούμε upper() για να μην κολλήσουμε στα κεφαλαία/μικρά
                if key.upper() in raw_key.upper():
                    return clean_number(raw_val)
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF", type="pdf", accept_multiple_files=True)
debug = st.checkbox("Ενεργοποίηση Debug (Δες τις γραμμές που διαβάζει)")

# --- ΛΕΞΙΚΟ ---
metrics_config = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Αιματοκρίτης": ["HCT", "Αιματοκρίτης"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Τριγλυκερίδια": ["Τριγλυκερίδια"],
    "Σίδηρος": ["Σίδηρος", "Fe "],
    "B12": ["B12"],
    "TSH": ["TSH"],
    "Κάλιο": ["Κάλιο"],
    "Νάτριο": ["Νάτριο"]
}

selected_metrics = st.multiselect("Επιλογή Εξετάσεων:", list(metrics_config.keys()), default=["Αιμοπετάλια (PLT)"])

# --- ΕΚΤΕΛΕΣΗ ---
if st.button("🚀 ΤΡΕΞΕ ΤΟ") and uploaded_files:
    results = []
    bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        with pdfplumber.open(file) as pdf:
            # 1. Παίρνουμε όλο το κείμενο
            full_text = ""
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
        
        # 2. Σπάμε το κείμενο σε ΓΡΑΜΜΕΣ
        lines = full_text.split('\n')
        
        row = {'Αρχείο': file.name, 'Ημερομηνία': extract_date(full_text, file.name)}
        
        # Debugging: Δείξε μου τις πρώτες 10 γραμμές να δω αν μοιάζουν με CSV
        if debug and i == 0:
            st.write(f"--- ΔΕΙΓΜΑ ΓΡΑΜΜΩΝ ΑΠΟ {file.name} ---")
            for l in lines[:10]:
                st.code(l)
            st.write("--- ΤΕΛΟΣ ΔΕΙΓΜΑΤΟΣ ---")

        # 3. Σκανάρουμε κάθε γραμμή
        for metric_name in selected_metrics:
            keywords = metrics_config[metric_name]
            found_val = None
            
            for line in lines:
                val = parse_csv_line(line, keywords)
                if val is not None:
                    # Εξτρα φίλτρο: Αν βρήκε έτος (π.χ. 2024), αγνόησέ το
                    if val > 1900 and metric_name != "B12":
                        continue
                    found_val = val
                    break # Βρήκαμε την τιμή, πάμε στην επόμενη εξέταση
            
            row[metric_name] = found_val
            
        results.append(row)
        bar.progress((i + 1) / len(uploaded_files))

    if results:
        df = pd.DataFrame(results)
        
        # Ταξινόμηση
        df['DateSort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('DateSort').drop(columns=['DateSort'])
        
        st.success("✅ Τέλος!")
        st.dataframe(df)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="extracted_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
