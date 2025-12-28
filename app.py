import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Lab Extractor Smart", layout="wide")
st.title("🩸 Εξαγωγή Εξετάσεων (Smart Look-Ahead)")
st.success("Αυτός ο κώδικας διορθώνει το πρόβλημα όπου η τιμή (π.χ. 106*) εμφανίζεται στην από κάτω γραμμή.")

# --- 1. AUTHENTICATION ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        return vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        st.error(f"Error with Secrets: {e}")
        return None

# --- 2. DATA CLEANING ---
def clean_number(val_str):
    if not val_str: return None
    # Καθαρισμός από λάθη OCR και σύμβολα
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('*', '').replace('$', '') # Αφαιρούμε το * από το 106*
    
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

# --- 3. Η SMART ΛΟΓΙΚΗ ---
def parse_google_text_smart(full_text, selected_metrics):
    results = {}
    
    # 1. Σπάμε το κείμενο σε γραμμές
    lines = full_text.split('\n')
    lines = [line.strip() for line in lines if line.strip()] # Καθαρίζουμε κενές γραμμές

    # 2. Σκανάρουμε για κάθε εξέταση
    for metric_name, keywords in selected_metrics.items():
        
        for i, line in enumerate(lines):
            # Αν η γραμμή περιέχει τη λέξη κλειδί (π.χ. "PLT")
            if any(key.upper() in line.upper() for key in keywords):
                
                # Προσπάθεια 1: Ψάχνουμε αριθμό στην ΙΔΙΑ γραμμή
                val = find_first_number(line)
                
                # Προσπάθεια 2 (ΤΟ ΚΛΕΙΔΙ ΤΗΣ ΛΥΣΗΣ): 
                # Αν δεν βρούμε, κοιτάμε την ΑΠΟ ΚΑΤΩ γραμμή (i+1)
                if val is None and i + 1 < len(lines):
                    next_line = lines[i+1]
                    val = find_first_number(next_line)
                
                # Αν βρέθηκε τιμή
                if val is not None:
                    # Φίλτρα για να μην πάρουμε λάθος νούμερα
                    if val > 1900 and val < 2100 and "B12" not in metric_name: continue # Έτος
                    if "Αιμοπετάλια" in metric_name and val < 10: continue # Πολύ μικρό για PLT
                    
                    results[metric_name] = val
                    break # Σταματάμε μόλις βρούμε το πρώτο (το 106), ώστε να μην φτάσουμε κάτω στο 120.000
                    
    return results

def find_first_number(s):
    # Βρίσκει τον πρώτο έγκυρο αριθμό σε ένα κείμενο
    numbers = re.findall(r"(\d+[,.]\d+|\d+)", s)
    for num in numbers:
        cleaned = clean_number(num)
        if cleaned is not None:
            return cleaned
    return None

# --- 4. UI ---
uploaded_files = st.file_uploader("📂 Ανεβάστε PDF", type="pdf", accept_multiple_files=True)

# Οι εξετάσεις που ψάχνουμε
ALL_METRICS = {
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια"], # Εδώ είναι το πρόβλημα
    "Λευκά (WBC)": ["WBC", "Λευκά"],
    "Σάκχαρο": ["GLU", "Σάκχαρο", "Glucose"],
    "Χοληστερίνη": ["Cholesterol", "Χοληστερίνη"],
    "Σίδηρος": ["Fe ", "Σίδηρος"],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "B12": ["B12"],
    "TSH": ["TSH"]
}

if st.button("🚀 ΕΝΑΡΞΗ") and uploaded_files:
    client = get_vision_client()
    if client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            try:
                # Μετατροπή σε εικόνα
                images = convert_from_bytes(file.read())
                full_text = ""
                for img in images:
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    content = img_byte_arr.getvalue()
                    
                    image = vision.Image(content=content)
                    response = client.text_detection(image=image)
                    if response.text_annotations:
                        full_text += response.text_annotations[0].description + "\n"
                
                # Ανάλυση με τη νέα Smart μέθοδο
                data = parse_google_text_smart(full_text, ALL_METRICS)
                
                # Ημερομηνία
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text)
                if date_match:
                    data['Ημερομηνία'] = date_match.group(1)
                else:
                    m = re.search(r'(\d{6})', file.name)
                    data['Ημερομηνία'] = f"{m.group(1)[4:6]}/{m.group(1)[2:4]}/20{m.group(1)[0:2]}" if m else "Άγνωστη"
                
                data['Αρχείο'] = file.name
                all_data.append(data)
                
            except Exception as e:
                st.error(f"Σφάλμα στο {file.name}: {e}")
            bar.progress((i+1)/len(uploaded_files))

        if all_data:
            df = pd.DataFrame(all_data)
            # Ταξινόμηση
            try:
                df['Sort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
                df = df.sort_values('Sort').drop(columns=['Sort'])
            except: pass
            
            st.dataframe(df)
            
            # Excel Download
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Κατέβασμα Excel", output.getvalue(), "results_smart.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
