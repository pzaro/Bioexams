import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Ultimate Extractor", layout="wide")
st.title("🛠️ Εργαλείο Εξαγωγής & Debugging")
st.markdown("Αν δεν βλέπετε αποτελέσματα, κοιτάξτε το 'Raw Text' παρακάτω για να δείτε αν το αρχείο διαβάζεται σωστά.")

def extract_date(text, filename):
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def get_value_from_tokens(text, keywords):
    """
    Μέθοδος για αρχεία που έχουν μορφή CSV μέσα στο PDF
    π.χ. "PLT Αιμοπετάλια","400","..."
    """
    # 1. Αντικαθιστούμε τα διαχωριστικά "," με ένα ειδικό σύμβολο (π.χ. |)
    # για να ξέρουμε πού αλλάζει το κελί
    cleaner_text = text.replace('","', '|')
    cleaner_text = cleaner_text.replace('", "', '|') # Με κενό
    
    # 2. Σπάμε το κείμενο σε κομμάτια (tokens)
    tokens = cleaner_text.split('|')
    
    for i, token in enumerate(tokens):
        # Καθαρίζουμε το token από σκουπίδια
        clean_token = token.replace('"', '').replace('\n', '').strip()
        
        # Ελέγχουμε αν αυτό το token περιέχει τη λέξη κλειδί (π.χ. PLT)
        for key in keywords:
            if key.upper() in clean_token.upper():
                # ΑΝ ΒΡΕΘΗΚΕ: Κοιτάμε το ΑΜΕΣΩΣ επόμενο token (που λογικά είναι η τιμή)
                if i + 1 < len(tokens):
                    next_token = tokens[i+1]
                    # Καθαρίζουμε την τιμή (βγάζουμε $, *, κενά)
                    value_str = next_token.replace('$', '').replace('*', '').replace('"', '').strip()
                    value_str = value_str.replace(',', '.') # 12,5 -> 12.5
                    
                    # Προσπάθεια μετατροπής σε αριθμό
                    try:
                        # Ψάχνουμε για αριθμό μέσα στο string (π.χ. αν λέει "Low 45")
                        num_match = re.search(r"(\d+[.]?\d*)", value_str)
                        if num_match:
                            return float(num_match.group(1))
                    except:
                        continue
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF εδώ", type="pdf", accept_multiple_files=True)

metrics_config = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Σίδηρος": ["Σίδηρος", "Fe "],
    "B12": ["B12"],
    "TSH": ["TSH"]
}

selected_metrics = st.multiselect("Επιλογή Εξετάσεων:", list(metrics_config.keys()), default=list(metrics_config.keys())[:3])

if st.button("🚀 ΤΡΕΞΕ ΤΟΝ ΚΩΔΙΚΑ") and uploaded_files:
    results = []
    
    for uploaded_file in uploaded_files:
        with pdfplumber.open(uploaded_file) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += page.extract_text() or ""
        
        # --- DEBUG VIEW ΓΙΑ ΤΟΝ ΧΡΗΣΤΗ ---
        with st.expander(f"🔍 Debug: Τι βλέπω στο αρχείο {uploaded_file.name}"):
            st.text(full_text[:500]) # Δείξε τους πρώτους 500 χαρακτήρες
            if len(full_text) < 50:
                st.error("⚠️ ΤΟ ΚΕΙΜΕΝΟ ΕΙΝΑΙ ΚΕΝΟ! Το PDF είναι πιθανώς σκαναρισμένη εικόνα.")

        row = {'Αρχείο': uploaded_file.name, 'Ημερομηνία': extract_date(full_text, uploaded_file.name)}
        
        for metric in selected_metrics:
            val = get_value_from_tokens(full_text, metrics_config[metric])
            row[metric] = val
            
        results.append(row)

    if results:
        df = pd.DataFrame(results)
        
        # Format Date
        df['Ημερομηνία'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce').dt.strftime('%d/%m/%Y')
        
        st.write("### 📊 Αποτελέσματα")
        st.dataframe(df)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="results_debug.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
