import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re
import json

st.set_page_config(page_title="Google Vision Extractor", layout="wide")
st.title("🦅 Google Vision OCR Extractor")
st.info("Χρήση της Τεχνητής Νοημοσύνης της Google για ανάγνωση των PDF.")

# --- 1. ΑΥΘΕΝΤΙΚΟΠΟΙΗΣΗ ΜΕ GOOGLE ---
# Παίρνουμε το κλειδί από τα Streamlit Secrets
def get_vision_client():
    try:
        # Διαβάζουμε το JSON key από τα secrets (.streamlit/secrets.toml)
        key_dict = json.loads(st.secrets["gcp_service_account"]["json_key"])
        creds = service_account.Credentials.from_service_account_info(key_dict)
        client = vision.ImageAnnotatorClient(credentials=creds)
        return client
    except Exception as e:
        st.error(f"Πρόβλημα με το κλειδί Google Cloud: {e}")
        return None

# --- 2. ΣΥΝΑΡΤΗΣΕΙΣ ΚΑΘΑΡΙΣΜΟΥ ---
def clean_number(val_str):
    """Μετατρέπει κείμενο σε αριθμό (π.χ. '4,38' -> 4.38)"""
    if not val_str: return None
    # Διορθώσεις συχνών λαθών OCR
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1')
    
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

def parse_google_text(full_text, metrics_map):
    results = {}
    lines = full_text.split('\n')
    
    for line in lines:
        for metric, keywords in metrics_map.items():
            # Έλεγχος αν υπάρχει η λέξη κλειδί στη γραμμή
            if any(key.upper() in line.upper() for key in keywords):
                # Ψάχνουμε αριθμούς στη γραμμή
                numbers = re.findall(r"(\d+[,.]\d+|\d+)", line)
                
                # Συνήθως η σωστή τιμή είναι κοντά στην εξέταση.
                # Θα πάρουμε τον πρώτο έγκυρο αριθμό.
                for num in numbers:
                    val = clean_number(num)
                    if val is not None:
                        # Φίλτρα λογικής (για να μην πάρουμε ημερομηνίες ή κωδικούς)
                        if val > 1900 and metric != "B12": continue
                        if metric == "Αιμοπετάλια (PLT)" and val < 10: continue
                        
                        results[metric] = val
                        break
    return results

# --- 3. UI & ΕΚΤΕΛΕΣΗ ---
uploaded_files = st.file_uploader("📂 Ανεβάστε PDF", type="pdf", accept_multiple_files=True)

metrics_config = {
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

if st.button("🚀 ΑΠΟΣΤΟΛΗ ΣΤΗ GOOGLE") and uploaded_files:
    client = get_vision_client()
    
    if client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            file_results = {'Αρχείο': file.name}
            full_text = ""
            
            try:
                # Μετατροπή PDF σε Εικόνες (μια ανά σελίδα)
                images = convert_from_bytes(file.read())
                
                for img in images:
                    # Μετατροπή εικόνας σε bytes για τη Google
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    content = img_byte_arr.getvalue()
                    
                    # Κλήση στο Google Vision API
                    image = vision.Image(content=content)
                    response = client.text_detection(image=image)
                    
                    if response.text_annotations:
                        # Το [0] είναι όλο το κείμενο μαζί
                        full_text += response.text_annotations[0].description + "\n"
                
                # Ανάλυση του κειμένου που επέστρεψε η Google
                data = parse_google_text(full_text, metrics_config)
                file_results.update(data)
                
                # Ημερομηνία
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                file_results['Ημερομηνία'] = date_match.group(1) if date_match else "Άγνωστη"
                
                all_data.append(file_results)
                
            except Exception as e:
                st.error(f"Σφάλμα στο αρχείο {file.name}: {e}")
            
            bar.progress((i + 1) / len(uploaded_files))

        if all_data:
            df = pd.DataFrame(all_data)
            # Ταξινόμηση
            try:
                df['Sort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
                df = df.sort_values('Sort').drop(columns=['Sort'])
            except: pass
            
            st.success("✅ Η Google διάβασε τα αρχεία!")
            st.dataframe(df)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Κατέβασμα Excel", data=output.getvalue(), file_name="google_vision_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.warning("Δεν βρέθηκε κλειδί Google API στα Secrets.")
