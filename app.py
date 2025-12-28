import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re

# --- ΡΥΘΜΙΣΕΙΣ ΣΕΛΙΔΑΣ ---
st.set_page_config(page_title="Google Vision Extractor", layout="wide")
st.title("🦅 Google Vision OCR Extractor")
st.info("Χρήση της Τεχνητής Νοημοσύνης της Google για ανάγνωση των PDF.")

# --- 1. ΑΥΘΕΝΤΙΚΟΠΟΙΗΣΗ ΜΕ GOOGLE ---
def get_vision_client():
    try:
        # ΔΙΟΡΘΩΣΗ: Διαβάζουμε τα secrets απευθείας ως dictionary (TOML format)
        # Δεν χρειάζεται json.loads πλέον, γιατί το Streamlit το έχει ήδη μετατρέψει.
        key_dict = st.secrets["gcp_service_account"]
        
        # Δημιουργία των credentials από το λεξικό
        creds = service_account.Credentials.from_service_account_info(key_dict)
        client = vision.ImageAnnotatorClient(credentials=creds)
        return client
    except Exception as e:
        st.error(f"Πρόβλημα με το κλειδί Google Cloud: {e}")
        return None

# --- 2. ΣΥΝΑΡΤΗΣΕΙΣ ΚΑΘΑΡΙΣΜΟΥ & ΕΥΡΕΣΗΣ ---

def clean_number(val_str):
    """Μετατρέπει κείμενο σε αριθμό, διορθώνοντας λάθη του OCR."""
    if not val_str: return None
    
    # Αντικατάσταση κοινών λαθών OCR (π.χ. το γράμμα O αντί για 0, το l αντί για 1)
    val_str = val_str.replace('O', '0').replace('o', '0')
    val_str = val_str.replace('l', '1').replace('I', '1')
    
    # Κρατάμε μόνο ψηφία και κόμμα/τελεία
    clean = re.sub(r"[^0-9,.]", "", val_str)
    # Αλλαγή κόμματος σε τελεία
    clean = clean.replace(',', '.')
    
    try:
        return float(clean)
    except:
        return None

def parse_google_text(full_text, metrics_map):
    results = {}
    lines = full_text.split('\n')
    
    for line in lines:
        # Καθαρίζουμε τη γραμμή από περιττά κενά
        clean_line = " ".join(line.split())
        
        for metric, keywords in metrics_map.items():
            # Έλεγχος αν υπάρχει η λέξη κλειδί στη γραμμή
            if any(key.upper() in clean_line.upper() for key in keywords):
                
                # Ψάχνουμε όλους τους αριθμούς στη γραμμή
                numbers = re.findall(r"(\d+[,.]\d+|\d+)", clean_line)
                
                # Προσπαθούμε να βρούμε τον σωστό αριθμό
                for num in numbers:
                    val = clean_number(num)
                    
                    if val is not None:
                        # --- ΦΙΛΤΡΑ ΛΟΓΙΚΗΣ (Για να μην πάρουμε σκουπίδια) ---
                        
                        # 1. Αγνοούμε έτη (π.χ. 2024, 2023) εκτός αν είναι B12
                        if val > 1900 and val < 2100 and "B12" not in metric: 
                            continue
                        
                        # 2. Για Αιμοπετάλια (PLT), τιμές κάτω από 10 είναι συνήθως λάθος
                        if "PLT" in metric and val < 10: 
                            continue
                        
                        # 3. Για Αιμοσφαιρίνη (HGB), τιμές πάνω από 20 είναι συνήθως λάθος
                        if "HGB" in metric and val > 20:
                            continue

                        results[metric] = val
                        break # Βρήκαμε τιμή, πάμε στην επόμενη εξέταση
    return results

# --- 3. UI ΕΦΑΡΜΟΓΗΣ ---

uploaded_files = st.file_uploader("📂 Ανεβάστε PDF", type="pdf", accept_multiple_files=True)

# Λεξικό με τις εξετάσεις που ψάχνουμε και τα "κλειδιά" τους
metrics_config = {
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Σάκχαρο": ["Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Χοληστερίνη", "Cholesterol"],
    "Τριγλυκερίδια": ["Τριγλυκερίδια"],
    "Σίδηρος": ["Σίδηρος", "Fe "], # Το κενό στο "Fe " βοηθά να μην μπερδευτεί με Ferritin
    "B12": ["B12"],
    "TSH": ["TSH"]
}

if st.button("🚀 ΑΠΟΣΤΟΛΗ ΣΤΗ GOOGLE") and uploaded_files:
    # 1. Παίρνουμε τον "πελάτη" της Google
    client = get_vision_client()
    
    if client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            file_results = {'Αρχείο': file.name}
            full_text_scan = ""
            
            try:
                # 2. Μετατροπή PDF σε Εικόνες (μια εικόνα ανά σελίδα)
                # Το poppler πρέπει να είναι εγκατεστημένο (packages.txt)
                images = convert_from_bytes(file.read())
                
                for img in images:
                    # Μετατροπή εικόνας σε bytes για τη Google
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    content = img_byte_arr.getvalue()
                    
                    # 3. Κλήση στο Google Vision API
                    image = vision.Image(content=content)
                    response = client.text_detection(image=image)
                    
                    if response.text_annotations:
                        # Το [0] περιέχει όλο το κείμενο της σελίδας
                        full_text_scan += response.text_annotations[0].description + "\n"
                
                # 4. Ανάλυση του κειμένου που επέστρεψε η Google
                data = parse_google_text(full_text_scan, metrics_config)
                file_results.update(data)
                
                # 5. Προσπάθεια εύρεσης Ημερομηνίας
                # Ψάχνουμε στο κείμενο για DD/MM/YYYY
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text_scan)
                if date_match:
                    file_results['Ημερομηνία'] = date_match.group(1)
                else:
                    # Αν δεν βρεθεί, ψάχνουμε στο όνομα αρχείου (π.χ. ...-240115.pdf)
                    match_file = re.search(r'[-_]?(\d{6})', file.name)
                    if match_file:
                        d = match_file.group(1)
                        file_results['Ημερομηνία'] = f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
                    else:
                        file_results['Ημερομηνία'] = "Άγνωστη"
                
                all_data.append(file_results)
                
            except Exception as e:
                st.error(f"Σφάλμα στο αρχείο {file.name}: {e}")
            
            bar.progress((i + 1) / len(uploaded_files))

        # --- ΕΜΦΑΝΙΣΗ ΑΠΟΤΕΛΕΣΜΑΤΩΝ ---
        if all_data:
            df = pd.DataFrame(all_data)
            
            # Ταξινόμηση με βάση την ημερομηνία
            try:
                df['SortDate'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
                df = df.sort_values('SortDate').drop(columns=['SortDate'])
            except: 
                pass
            
            # Φέρνουμε την Ημερομηνία πρώτη
            cols = ['Ημερομηνία', 'Αρχείο'] + [c for c in df.columns if c not in ['Ημερομηνία', 'Αρχείο']]
            df = df[cols]
            
            st.success("✅ Η Google ολοκλήρωσε την ανάγνωση!")
            st.dataframe(df)
            
            # Κουμπί Download Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.download_button(
                "📥 Κατέβασμα Excel", 
                data=output.getvalue(), 
                file_name="google_vision_results.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    else:
        st.warning("Δεν βρέθηκε έγκυρο κλειδί Google API στα Secrets.")
