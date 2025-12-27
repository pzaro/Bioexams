import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# Ρύθμιση Σελίδας
st.set_page_config(page_title="Medical Data Extractor", layout="wide")

st.title("🩺 Εξαγωγή Ιατρικών Δεδομένων από PDF")
st.markdown("Ανεβάστε τα αρχεία PDF και επιλέξτε ποιες εξετάσεις θέλετε να εξάγετε σε Excel.")

# --- 1. Upload Αρχείων ---
uploaded_files = st.file_uploader("Επιλογή αρχείων PDF", type="pdf", accept_multiple_files=True)

# --- 2. Λίστα Επιλογών ---
metrics_map = {
    "Αιμοπετάλια (PLT)": r"PLT\s.*?(\d{2,3})",
    "Αιμοσφαιρίνη (HGB)": r"HGB\s.*?(\d{1,2}[.,]\d{1})",
    "Λευκά Αιμοσφαίρια (WBC)": r"WBC\s.*?(\d{1,2}[.,]\d{1,2})",
    "Αιματοκρίτης (HCT)": r"HCT\s.*?(\d{2}[.,]\d{1})",
    "Σάκχαρο (Glucose)": r"(?:Σάκχαρο|Glucose)\s.*?(\d{2,3})",
    "Χοληστερίνη (Chol)": r"(?:Χοληστερίνη|Cholesterol)\s.*?(\d{2,3})",
    "Τριγλυκερίδια": r"Τριγλυκερίδια\s.*?(\d{2,3})",
    "HDL": r"HDL\s.*?(\d{2,3})",
    "LDL": r"LDL\s.*?(\d{2,3})",
    "Σίδηρος (Fe)": r"Σίδηρος\s.*?(\d{2,3})",
    "Φερριτίνη": r"Φερριτίνη\s.*?(\d{1,3})",
    "B12": r"B12\s.*?(\d{2,4})",
    "TSH (Θυρεοειδής)": r"TSH\s.*?(\d{1,2}[.,]\d{2,3})",
    "FT4": r"FT4\s.*?(\d{1}[.,]\d{1,2})",
    "Κάλιο (K)": r"Κάλιο\s.*?(\d{1}[.,]\d{1})",
    "Νάτριο (Na)": r"Νάτριο\s.*?(\d{3})"
}

selected_metrics = st.multiselect("Επιλέξτε Εξετάσεις προς εξαγωγή:", list(metrics_map.keys()), default=["Αιμοπετάλια (PLT)"])

# --- 3. Λογική Επεξεργασίας ---
def extract_date(text, filename):
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    match_file = re.search(r'[-_](\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

if st.button("🚀 Έναρξη Επεξεργασίας") and uploaded_files:
    results = []
    progress_bar = st.progress(0)
    
    for i, uploaded_file in enumerate(uploaded_files):
        try:
            with pdfplumber.open(uploaded_file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    full_text += (page.extract_text() or "") + " "
            
            clean_text = full_text.replace('\n', ' ')
            row = {'Όνομα Αρχείου': uploaded_file.name, 'Ημερομηνία': extract_date(clean_text, uploaded_file.name)}
            
            for metric in selected_metrics:
                pattern = metrics_map[metric]
                match = re.search(pattern, clean_text)
                if match:
                    val = match.group(1).replace(',', '.')
                    try:
                        row[metric] = float(val)
                    except:
                        row[metric] = val
                else:
                    row[metric] = None
            
            results.append(row)
        except Exception as e:
            st.error(f"Σφάλμα στο αρχείο {uploaded_file.name}: {e}")
        
        progress_bar.progress((i + 1) / len(uploaded_files))

    if results:
        df = pd.DataFrame(results)
        
        # Προσπάθεια ταξινόμησης
        try:
            df['Ημερομηνία'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
            df = df.sort_values('Ημερομηνία')
            df['Ημερομηνία'] = df['Ημερομηνία'].dt.strftime('%d/%m/%Y') # Επιστροφή σε μορφή κειμένου για εμφάνιση
        except:
            pass

        st.success("Η επεξεργασία ολοκληρώθηκε!")
        st.dataframe(df)

        # Download Button
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 Κατεβάστε το Excel",
            data=buffer.getvalue(),
            file_name="medical_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    if not uploaded_files:
        st.info("Παρακαλώ ανεβάστε αρχεία για να ξεκινήσετε.")