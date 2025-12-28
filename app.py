import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# Ρύθμιση σελίδας
st.set_page_config(page_title="Lab Results CSV-Miner", layout="wide")
st.title("🩸 Εξαγωγή Εξετάσεων (Μέθοδος CSV-Mining)")
st.info("Αυτός ο κώδικας είναι σχεδιασμένος ειδικά για PDF που έχουν τη μορφή `\"Εξέταση\",\"Τιμή\"`.")

# --- ΣΥΝΑΡΤΗΣΕΙΣ ---

def clean_value(val_str):
    """
    Καθαρίζει την τιμή από σκουπίδια και την κάνει αριθμό.
    Π.χ. το "4,38" γίνεται 4.38, το "$222*" γίνεται 222.0
    """
    if not val_str: return None
    # Κρατάμε μόνο αριθμούς και κόμμα/τελεία
    clean = re.sub(r"[^0-9,.]", "", val_str)
    # Αλλαγή κόμματος σε τελεία για την Python
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def extract_date(text, filename):
    # Προσπάθεια εύρεσης ημερομηνίας στο κείμενο
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    
    # Αν δεν βρεθεί, ψάχνουμε στο όνομα αρχείου (π.χ. 240115)
    match_file = re.search(r'[-_]?(\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        # Υποθέτουμε μορφή YYMMDD
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def parse_line_csv_style(line):
    """
    Η ΚΡΙΣΙΜΗ ΣΥΝΑΡΤΗΣΗ:
    Ψάχνει για κείμενο που είναι φυλακισμένο σε εισαγωγικά.
    """
    # Βρες όλα τα κομμάτια που είναι ανάμεσα σε "..."
    tokens = re.findall(r'"([^"]*)"', line)
    
    # Χρειαζόμαστε τουλάχιστον 2 κομμάτια: ["Εξέταση", "Τιμή", "Όρια..."]
    if len(tokens) >= 2:
        exam_name = tokens[0].strip()
        raw_value = tokens[1].strip()
        
        # Καθαρίζουμε την τιμή
        final_value = clean_value(raw_value)
        
        # Φίλτρο: Το όνομα της εξέτασης πρέπει να έχει νόημα (πάνω από 2 γράμματα)
        # και η τιμή να είναι έγκυρος αριθμός.
        if len(exam_name) > 2 and final_value is not None:
            # Έξτρα φίλτρο: Αν η τιμή μοιάζει με χρονολογία (π.χ. 2024), την αγνοούμε
            # Εκτός αν είναι B12 που έχει μεγάλες τιμές
            if final_value > 1900 and final_value < 2100 and "B12" not in exam_name:
                return None
                
            return exam_name, final_value
            
    return None

# --- UI ΕΦΑΡΜΟΓΗΣ ---

uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF αρχεία σας", type="pdf", accept_multiple_files=True)
debug_mode = st.checkbox("Ενεργοποίηση Debug (Δείξε μου τι βρίσκεις ζωντανά)")

# Λίστα με τις εξετάσεις που μας ενδιαφέρουν (για φιλτράρισμα στο τέλος)
TARGET_EXAMS = [
    "PLT", "Αιμοπετάλια", 
    "HGB", "Αιμοσφαιρίνη", 
    "WBC", "Λευκά",
    "RBC", "Ερυθρά",
    "HCT", "Αιματοκρίτης",
    "Σάκχαρο", "Glucose",
    "Χοληστερίνη", "Cholesterol",
    "Τριγλυκερίδια",
    "Σίδηρος", "Fe",
    "Φερριτίνη",
    "B12",
    "TSH", "Θυρεοειδοτρόπος"
]

if st.button("🚀 ΕΞΑΓΩΓΗ ΔΕΔΟΜΕΝΩΝ") and uploaded_files:
    
    all_results = []
    progress_bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        try:
            with pdfplumber.open(file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    full_text += (page.extract_text() or "") + "\n"
            
            # Βρίσκουμε την ημερομηνία
            date = extract_date(full_text, file.name)
            
            # Σπάμε το κείμενο σε γραμμές
            lines = full_text.split('\n')
            
            row_data = {'Αρχείο': file.name, 'Ημερομηνία': date}
            
            # Ανάλυση γραμμή-γραμμή
            for line in lines:
                # Αν η γραμμή δεν έχει εισαγωγικά, την αγνοούμε (δεν είναι δεδομένο)
                if '"' not in line:
                    continue
                
                parsed = parse_line_csv_style(line)
                if parsed:
                    exam, val = parsed
                    
                    # Ελέγχουμε αν αυτή η εξέταση είναι στη λίστα που μας ενδιαφέρει
                    # (Ψάχνουμε αν κάποια λέξη-στόχος υπάρχει μέσα στο όνομα που βρήκαμε)
                    for target in TARGET_EXAMS:
                        if target.upper() in exam.upper():
                            # Χρησιμοποιούμε τον "καθαρό" στόχο ως όνομα στήλης για ομοιομορφία
                            # Π.χ. αντί για "PLT Αιμοπετάλια" θα γράψουμε "PLT" ή "Αιμοπετάλια"
                            # Εδώ κρατάμε το πλήρες όνομα που βρήκε στο PDF για σιγουριά, ή μπορούμε να το απλοποιήσουμε.
                            # Ας κρατήσουμε το target για ομαδοποίηση.
                            
                            # Αποθήκευση: Αν έχουμε ξαναβρεί αυτό το target σε αυτό το αρχείο, δεν το πειράζουμε
                            if target not in row_data: 
                                row_data[target] = val
                            
                            if debug_mode and i==0:
                                st.write(f"✅ {target}: {val} (από: {exam})")
                            break
            
            all_results.append(row_data)
            
        except Exception as e:
            st.error(f"Σφάλμα στο αρχείο {file.name}: {e}")
            
        progress_bar.progress((i + 1) / len(uploaded_files))
        
    # --- ΕΜΦΑΝΙΣΗ ΑΠΟΤΕΛΕΣΜΑΤΩΝ ---
    if all_results:
        df = pd.DataFrame(all_results)
        
        # Ταξινόμηση βάσει ημερομηνίας
        df['DateSort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('DateSort').drop(columns=['DateSort'])
        
        # Μετακίνηση βασικών στηλών μπροστά
        cols = ['Ημερομηνία', 'Αρχείο'] + [c for c in df.columns if c not in ['Ημερομηνία', 'Αρχείο']]
        df = df[cols]
        
        st.success("Η εξαγωγή ολοκληρώθηκε!")
        st.dataframe(df)
        
        # Κουμπί Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button(
            label="📥 Κατέβασμα σε Excel",
            data=buffer.getvalue(),
            file_name="lab_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Δεν βρέθηκαν αποτελέσματα. Βεβαιώσου ότι τα PDF δεν είναι σκαναρισμένες εικόνες.")
