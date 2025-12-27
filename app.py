import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- ΡΥΘΜΙΣΕΙΣ ΣΕΛΙΔΑΣ ---
st.set_page_config(page_title="Smart Medical Extractor", layout="wide")
st.title("🩺 Έξυπνη Εξαγωγή Εξετάσεων (Robust Mode)")
st.markdown("""
Αυτή η έκδοση είναι σχεδιασμένη να διαβάζει δύσκολες μορφοποιήσεις (αστερίσκους, αλλαγές γραμμών, κόμματα).
""")

# --- Η "ΕΞΥΠΝΗ" ΣΥΝΑΡΤΗΣΗ ΕΞΑΓΩΓΗΣ ---
def smart_extract(text, patterns):
    """
    Ψάχνει στο κείμενο με βάση πολλαπλά κλειδιά.
    Μόλις βρει το κλειδί, ψάχνει τον κοντινότερο αριθμό δεξιά του.
    """
    # Αντικαθιστούμε αλλαγές γραμμής με κενά για να γίνει το κείμενο μια ευθεία γραμμή
    clean_text = text.replace('\n', ' ').replace('\r', ' ')
    
    for pattern in patterns:
        # Ψάχνουμε τη λέξη κλειδί (π.χ. "PLT") και παίρνουμε τα επόμενα 30 ψηφία
        # (?i) = ignore case (δεν μας νοιάζουν κεφαλαία/μικρά)
        match = re.search(f"(?i){pattern}.{{0,40}}", clean_text)
        
        if match:
            # Βρήκαμε την περιοχή γύρω από τη λέξη κλειδί. Τώρα ψάχνουμε τον αριθμό μέσα εκεί.
            chunk = match.group(0)
            
            # Regex για αριθμό: Μπορεί να έχει κόμμα ή τελεία (π.χ. 12,5 ή 12.5 ή 140)
            # Αγνοούμε τον αστερίσκο (*)
            number_match = re.search(r"(\d+([.,]\d+)?)", chunk)
            
            if number_match:
                value_str = number_match.group(1)
                # Διόρθωση: Αντικατάσταση κόμματος με τελεία για να το καταλάβει η Python
                value_str = value_str.replace(',', '.')
                try:
                    return float(value_str)
                except ValueError:
                    continue
    return None

def extract_date(text, filename):
    # Ψάχνουμε ημερομηνία στο κείμενο (μορφής 15/01/24 ή 15/01/2024)
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    
    # Αν δεν βρεθεί, ψάχνουμε στο όνομα αρχείου (π.χ. NAME-240115.pdf)
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

# --- UPLOAD ΑΡΧΕΙΩΝ ---
uploaded_files = st.file_uploader("📂 Σύρετε τα αρχεία PDF εδώ (Απεριόριστα)", type="pdf", accept_multiple_files=True)

# --- ΛΙΣΤΑ ΕΞΕΤΑΣΕΩΝ (ΜΕ ΠΟΛΛΑΠΛΑ ΚΛΕΙΔΙΑ ΓΙΑ ΣΙΓΟΥΡΙΑ) ---
# Εδώ ορίζουμε τι ψάχνουμε. Κάθε εξέταση έχει μια λίστα από πιθανά ονόματα (keywords).
metrics_config = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη", "Hemoglobin"],
    "Λευκά Αιμοσφαίρια (WBC)": ["WBC", "Λευκά", "White Blood"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose", "GLU"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol", "CHOL"],
    "Τριγλυκερίδια": ["Τριγλυκερίδια", "Triglycerides", "TRIG"],
    "Σίδηρος (Fe)": ["Σίδηρος", "Iron", "Fe "], # Κενό μετά το Fe για να μην πιάσει το Ferritin
    "Φερριτίνη": ["Φερριτίνη", "Ferritin"],
    "B12": ["B12", "Vit B12"],
    "TSH": ["TSH", "Θυρεοειδοτρόπος"],
    "T3": ["T3", "Τριιωδοθυρονίνη"],
    "T4": ["T4", "Θυροξίνη"],
    "Κάλιο": ["Κάλιο", "Potassium", " K "],
    "Νάτριο": ["Νάτριο", "Sodium", " Na "]
}

selected_metrics = st.multiselect(
    "Επιλέξτε Εξετάσεις:", 
    list(metrics_config.keys()), 
    default=["Αιμοπετάλια (PLT)", "Αιμοσφαιρίνη (HGB)", "Λευκά Αιμοσφαίρια (WBC)"]
)

# --- ΕΚΤΕΛΕΣΗ ---
if st.button("🚀 ΕΞΑΓΩΓΗ ΤΙΜΩΝ") and uploaded_files:
    results = []
    progress_bar = st.progress(0)
    
    st.info(f"Επεξεργασία {len(uploaded_files)} αρχείων...")

    for i, uploaded_file in enumerate(uploaded_files):
        try:
            # Διάβασμα PDF
            with pdfplumber.open(uploaded_file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    full_text += (page.extract_text() or "") + " "
            
            # Δημιουργία γραμμής αποτελεσμάτων
            row = {
                'Όνομα Αρχείου': uploaded_file.name, 
                'Ημερομηνία': extract_date(full_text, uploaded_file.name)
            }
            
            # Εξαγωγή κάθε επιλεγμένης εξέτασης
            for metric in selected_metrics:
                patterns = metrics_config[metric]
                val = smart_extract(full_text, patterns)
                row[metric] = val
            
            results.append(row)

        except Exception as e:
            st.error(f"Σφάλμα στο αρχείο {uploaded_file.name}: {e}")
        
        # Ενημέρωση μπάρας
        progress_bar.progress((i + 1) / len(uploaded_files))

    # --- ΕΜΦΑΝΙΣΗ ΑΠΟΤΕΛΕΣΜΑΤΩΝ ---
    if results:
        df = pd.DataFrame(results)
        
        # Μορφοποίηση ημερομηνίας
        df['Ημερομηνία'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('Ημερομηνία')
        df['Ημερομηνία'] = df['Ημερομηνία'].dt.strftime('%d/%m/%Y')

        st.success("✅ Ολοκληρώθηκε!")
        st.dataframe(df)

        # Download Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 Κατεβάστε το Excel",
            data=buffer.getvalue(),
            file_name="lab_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Δεν βρέθηκαν αποτελέσματα. Δοκιμάστε να ανοίξετε τα PDF και να δείτε αν το κείμενο επιλέγεται με το ποντίκι.")

elif not uploaded_files:
    st.write("👆 Ανεβάστε τα PDF σας για να ξεκινήσετε.")