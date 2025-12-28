import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="PDF CSV-Miner", layout="wide")
st.title("⛏️ Εξαγωγή Δεδομένων (Μέθοδος CSV-Mining)")
st.markdown("""
Αυτός ο κώδικας αγνοεί την εμφάνιση του PDF και ψάχνει για κρυμμένα δεδομένα μορφής:
`"Εξέταση","Αποτέλεσμα","Τιμές Αναφοράς"`
""")

# --- ΣΥΝΑΡΤΗΣΕΙΣ ---

def clean_number(val_str):
    """Μετατρέπει το string (π.χ. '4,38' ή '$29*') σε αριθμό."""
    if not val_str: return None
    # Κρατάμε μόνο αριθμούς και κόμμα/τελεία
    clean = re.sub(r"[^0-9,.]", "", val_str)
    # Αλλαγή κόμματος σε τελεία
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def extract_date(text, filename):
    # Πρώτα ψάχνουμε ημερομηνία στο κείμενο (DD/MM/YY ή YYYY)
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if match: return match.group(1)
    
    # Αν αποτύχει, ψάχνουμε στο όνομα αρχείου (π.χ. 240115)
    match_file = re.search(r'[-_]?(\d{6})', filename)
    if match_file:
        d = match_file.group(1)
        # Υποθέτουμε μορφή YYMMDD
        return f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
    return "Άγνωστη"

def parse_hidden_csv_line(line):
    """
    Η ΜΑΓΙΚΗ ΣΥΝΑΡΤΗΣΗ:
    Ψάχνει γραμμές που έχουν τουλάχιστον 2 ζευγάρια εισαγωγικών.
    """
    # Το regex αυτό βρίσκει ΟΛΑ τα κομμάτια που είναι μέσα σε εισαγωγικά "..."
    # π.χ. στη γραμμή: "RBC","4,38","3-5"
    # θα βρει: ['RBC', '4,38', '3-5']
    matches = re.findall(r'"([^"]*)"', line)
    
    # Θέλουμε τουλάχιστον 2 στοιχεία: Όνομα Εξέτασης και Τιμή
    if len(matches) >= 2:
        name = matches[0].strip()
        value_raw = matches[1].strip()
        
        # Καθαρίζουμε την τιμή
        value = clean_number(value_raw)
        
        # Φίλτρο: Το όνομα πρέπει να έχει γράμματα (για να μην πάρει επικεφαλίδες)
        if len(name) > 2 and value is not None:
            # Φίλτρο: Να μην είναι χρονιά (π.χ. 2024)
            if value > 1900 and value < 2100 and "B12" not in name:
                return None
            
            return name, value
            
    return None

# --- UPLOAD ---
uploaded_files = st.file_uploader("📂 Ανεβάστε τα PDF", type="pdf", accept_multiple_files=True)
debug_mode = st.checkbox("Ενεργοποίηση Debug (Δες τι γραμμές εντοπίζονται)")

if st.button("🚀 ΕΞΑΓΩΓΗ ΤΩΡΑ") and uploaded_files:
    all_data = []
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(uploaded_files):
        with pdfplumber.open(file) as pdf:
            full_text = ""
            for page in pdf.pages:
                # Παίρνουμε το raw text
                full_text += (page.extract_text() or "") + "\n"
        
        # Σπάμε σε γραμμές
        lines = full_text.split('\n')
        date = extract_date(full_text, file.name)
        
        file_results = {}
        
        # Debugging view
        if debug_mode and i==0:
            st.write(f"--- RAW TEXT SAMPLE ({file.name}) ---")
            st.code(full_text[:500])
            st.write("--- FOUND LINES ---")

        for line in lines:
            # Αγνοούμε γραμμές που δεν έχουν εισαγωγικά
            if '"' not in line:
                continue
                
            result = parse_hidden_csv_line(line)
            if result:
                name, val = result
                # Αποθηκεύουμε το αποτέλεσμα
                # Χρησιμοποιούμε το όνομα της εξέτασης ως κλειδί
                file_results[name] = val
                
                if debug_mode and i==0:
                    st.text(f"✅ Bρέθηκε: {name} -> {val}")

        # Προσθέτουμε τα μετα-δεδομένα
        file_results['Αρχείο'] = file.name
        file_results['Ημερομηνία'] = date
        
        all_data.append(file_results)
        progress_bar.progress((i + 1) / len(uploaded_files))

    # --- ΑΠΟΤΕΛΕΣΜΑΤΑ ---
    if all_data:
        # Φτιάχνουμε το DataFrame
        df = pd.DataFrame(all_data)
        
        # Φέρνουμε την Ημερομηνία και το Αρχείο πρώτα
        cols = ['Ημερομηνία', 'Αρχείο'] + [c for c in df.columns if c not in ['Ημερομηνία', 'Αρχείο']]
        df = df[cols]
        
        # Ταξινόμηση
        df['DateSort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
        df = df.sort_values('DateSort').drop(columns=['DateSort'])
        
        st.success(f"Ολοκληρώθηκε! Βρέθηκαν {len(df)} εγγραφές.")
        st.dataframe(df)
        
        # Excel Download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            "📥 Κατέβασμα Excel",
            data=output.getvalue(),
            file_name="lab_results_mined.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Δεν βρέθηκαν δεδομένα. Βεβαιώσου ότι τα αρχεία δεν είναι σκαναρισμένες εικόνες.")
