import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Hybrid Medical Extractor", layout="wide")
st.title("🧬 Υβριδική Εξαγωγή (Tables + Text)")
st.markdown("Αυτή η έκδοση προσπαθεί να διαβάσει το PDF **σαν Πίνακα** (γραμμές/στήλες). Αν αποτύχει, ψάχνει γραμμή-γραμμή.")

# --- ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ---
def clean_number(value_str):
    """Καθαρίζει μια τιμή από σκουπίδια ($, *, ", κενά) και την κάνει αριθμό"""
    if not isinstance(value_str, str): return None
    # Κρατάμε μόνο αριθμούς, κόμματα και τελείες
    clean = re.sub(r"[^0-9,.]", "", value_str)
    # Αλλαγή κόμματος σε τελεία
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

# --- ΚΥΡΙΑ ΛΟΓΙΚΗ ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF", type="pdf", accept_multiple_files=True)
debug_mode = st.checkbox("🕵️ ΕΝΕΡΓΟΠΟΙΗΣΗ DEBUG (Δείξε μου τι διαβάζεις)")

# Λεξικό: Τι ψάχνουμε (Keywords)
metrics_map = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Τριγλυκερίδια": ["Τριγλυκερίδια"],
    "Σίδηρος": ["Σίδηρος", "Fe "],
    "B12": ["B12"],
    "TSH": ["TSH"]
}

selected_metrics = st.multiselect("Επιλογή Εξετάσεων:", list(metrics_map.keys()), default=["Αιμοπετάλια (PLT)"])

if st.button("🚀 ΕΚΚΙΝΗΣΗ") and uploaded_files:
    results = []
    
    for i, file in enumerate(uploaded_files):
        file_data = {'Αρχείο': file.name, 'Ημερομηνία': 'Άγνωστη'}
        full_text_for_date = ""
        
        # Λίστα για να αποθηκεύσουμε ΟΛΕΣ τις λέξεις που βρήκαμε (για το Debug)
        found_data_debug = []

        try:
            with pdfplumber.open(file) as pdf:
                # 1. ΠΡΟΣΠΑΘΕΙΑ ΜΕ ΠΙΝΑΚΕΣ (TABLES) - Πιο αξιόπιστη
                for page in pdf.pages:
                    full_text_for_date += (page.extract_text() or "") + " "
                    
                    # Εξαγωγή πινάκων
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            # Καθαρίζουμε τα κενά (None) από τη γραμμή
                            clean_row = [str(cell).strip() if cell else "" for cell in row]
                            
                            # Ελέγχουμε αν αυτή η γραμμή περιέχει κάποια εξέταση
                            for metric_name in selected_metrics:
                                keywords = metrics_map[metric_name]
                                # Αν βρούμε λέξη κλειδί στη γραμμή
                                if any(k.upper() in str(r).upper() for r in clean_row for k in keywords):
                                    # Ψάχνουμε τον ΠΡΩΤΟ αριθμό που υπάρχει ΣΤΑ ΕΠΟΜΕΝΑ ΚΕΛΙΑ
                                    for cell_value in clean_row:
                                        val = clean_number(cell_value)
                                        # Φίλτρο: Να είναι αριθμός και να μην είναι ημερομηνία (π.χ. > 2020)
                                        # Επίσης για PLT συνήθως είναι > 10
                                        if val is not None and val < 2020:
                                            # Αν δεν έχουμε ήδη βρει τιμή, την αποθηκεύουμε
                                            if metric_name not in file_data:
                                                file_data[metric_name] = val
                                                found_data_debug.append(f"Table Found: {metric_name} -> {val}")
                                            break

                # 2. ΠΡΟΣΠΑΘΕΙΑ ΜΕ ΚΕΙΜΕΝΟ (TEXT LINES) - Αν αποτύχουν οι πίνακες
                # Σπάμε το κείμενο σε γραμμές
                lines = full_text_for_date.split('\n')
                for line in lines:
                    for metric_name in selected_metrics:
                        if metric_name not in file_data: # Μόνο αν δεν το βρήκαμε στον πίνακα
                            keywords = metrics_map[metric_name]
                            if any(k.upper() in line.upper() for k in keywords):
                                # Βρέθηκε η λέξη στη γραμμή. Ψάχνουμε αριθμούς.
                                # Καθαρίζουμε την γραμμή από εισαγωγικά κλπ
                                clean_line = line.replace('"', ' ').replace('$', ' ')
                                numbers = re.findall(r"(\d+[,.]?\d*)", clean_line)
                                for num in numbers:
                                    val = clean_number(num)
                                    if val and val < 2020:
                                        file_data[metric_name] = val
                                        found_data_debug.append(f"Text Found: {metric_name} -> {val}")
                                        break
            
            # Βρίσκουμε ημερομηνία στο τέλος
            file_data['Ημερομηνία'] = extract_date(full_text_for_date, file.name)
            results.append(file_data)
            
            # --- DEBUG AREA ---
            if debug_mode:
                st.warning(f"🔍 DEBUG για αρχείο: {file.name}")
                st.write("Τι βρέθηκε:", found_data_debug)
                if not found_data_debug:
                    st.error("Δεν βρέθηκε τίποτα. Δείγμα κειμένου:")
                    st.text(full_text_for_date[:500]) # Δείξε μας τι βλέπει

        except Exception as e:
            st.error(f"Σφάλμα στο {file.name}: {e}")

    # ΕΜΦΑΝΙΣΗ
    if results:
        df = pd.DataFrame(results)
        # Ταξινόμηση
        try:
            df['Ημερομηνία'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
            df = df.sort_values('Ημερομηνία')
            df['Ημερομηνία'] = df['Ημερομηνία'].dt.strftime('%d/%m/%Y')
        except: pass

        st.success("✅ Ολοκληρώθηκε!")
        st.dataframe(df)
        
        # Excel Download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
