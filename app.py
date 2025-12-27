import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Lab Results Scanner", layout="wide")
st.title("🔬 Εξαγωγή Εξετάσεων (Έκδοση Σαρωτής)")
st.markdown("Αυτή η έκδοση καθαρίζει 'κρυφούς' χαρακτήρες (όπως \", $, *) που εμποδίζουν την ανάγνωση.")

# --- Η ΣΥΝΑΡΤΗΣΗ ΚΑΘΑΡΙΣΜΟΥ ΚΑΙ ΕΥΡΕΣΗΣ ---
def aggressive_extract(text, keywords):
    # 1. Καθαρισμός "θορύβου" από το PDF (με βάση τα δείγματα που είδαμε)
    # Αντικαθιστούμε εισαγωγικά, δολάρια, αστερίσκους και κάθετες γραμμές με κενά
    clean_text = text.replace('"', ' ').replace('$', ' ').replace('*', ' ').replace('|', ' ')
    
    # 2. Αντικατάσταση πολλαπλών κενών με ένα
    clean_text = re.sub(r'\s+', ' ', clean_text)

    for key in keywords:
        # Ψάχνουμε τη λέξη κλειδί (αδιαφορώντας για κεφαλαία/μικρά)
        # και παίρνουμε τα επόμενα 50 ψηφία κειμένου
        match = re.search(f"(?i){key}.{{0,50}}", clean_text)
        
        if match:
            found_chunk = match.group(0)
            
            # 3. Ψάχνουμε για ΑΡΙΘΜΟ μέσα σε αυτό το κομμάτι
            # Ο αριθμός μπορεί να είναι ακέραιος (150) ή δεκαδικός (12,5 ή 12.5)
            # Αγνοούμε αριθμούς που μοιάζουν με ημερομηνίες ή κωδικούς (π.χ. μεγάλα νούμερα)
            numbers = re.findall(r"(\d+[,.]?\d*)", found_chunk)
            
            for num_str in numbers:
                # Μετατροπή σε float
                val_clean = num_str.replace(',', '.')
                try:
                    value = float(val_clean)
                    
                    # Φίλτρα Λογικής (για να μην πιάσει π.χ. το έτος 2024 αντί για τα αιμοπετάλια)
                    # Αν ψάχνουμε αιμοπετάλια και βρούμε αριθμό < 10, μάλλον είναι λάθος, πάμε στον επόμενο
                    if "PLT" in key and value < 10: 
                        continue 
                        
                    return value
                except ValueError:
                    continue
    return None

def extract_date(text, filename):
    # Καθαρισμός για να βρούμε την ημερομηνία πιο εύκολα
    clean_text = text.replace('"', ' ').replace('Ημ/νία:', ' ').replace('Date:', ' ')
    
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', clean_text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2: year = "20" + year
        return f"{day}/{month}/{year}"
    
    # Αν δεν βρεθεί στο κείμενο, ψάχνουμε στο όνομα αρχείου
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF αρχεία σας", type="pdf", accept_multiple_files=True)

# --- ΕΠΙΛΟΓΕΣ ---
metrics_config = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη", "Hemoglobin"],
    "Λευκά Αιμοσφαίρια (WBC)": ["WBC", "Λευκά", "White Blood"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose", "GLU"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Τριγλυκερίδια": ["Τριγλυκερίδια", "Triglycerides"],
    "Σίδηρος": ["Σίδηρος", "Fe "],
    "Φερριτίνη": ["Φερριτίνη", "Ferritin"],
    "B12": ["B12", "Vit B12"],
    "TSH": ["TSH", "Θυρεοειδοτρόπος"]
}

selected_metrics = st.multiselect("Επιλέξτε Εξετάσεις:", list(metrics_config.keys()), default=["Αιμοπετάλια (PLT)"])

# --- ΕΚΤΕΛΕΣΗ ---
if st.button("🚀 ΕΞΑΓΩΓΗ") and uploaded_files:
    results = []
    progress = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        try:
            with pdfplumber.open(file) as pdf:
                text = ""
                for page in pdf.pages:
                    text += (page.extract_text() or "") + " "
            
            row = {'Αρχείο': file.name, 'Ημερομηνία': extract_date(text, file.name)}
            
            for metric in selected_metrics:
                val = aggressive_extract(text, metrics_config[metric])
                row[metric] = val
            
            results.append(row)
            
        except Exception as e:
            st.error(f"Error in {file.name}: {e}")
        
        progress.progress((i + 1) / len(uploaded_files))

    if results:
        df = pd.DataFrame(results)
        # Ταξινόμηση
        df['Date_Obj'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('Date_Obj').drop(columns=['Date_Obj'])
        
        st.write("### Αποτελέσματα:")
        st.dataframe(df)
        
        # Download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
