import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Google Vision Diagnostic", layout="wide")
st.title("🔧 Εργαλείο Διάγνωσης Google Vision")
st.warning("Αυτή η έκδοση θα σου δείξει ΑΚΡΙΒΩΣ τι κείμενο επιστρέφει η Google, για να δούμε γιατί δεν πιάνει τις τιμές.")

# --- AUTH ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        client = vision.ImageAnnotatorClient(credentials=creds)
        return client
    except Exception as e:
        st.error(f"Error Auth: {e}")
        return None

# --- ΚΑΘΑΡΙΣΜΟΣ ---
def clean_number(val_str):
    if not val_str: return None
    # Αντικαταστάσεις για συχνά λάθη OCR
    val_str = val_str.replace('O', '0').replace('o', '0')
    val_str = val_str.replace('l', '1').replace('I', '1')
    
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

# --- UI ---
uploaded_file = st.file_uploader("Ανέβασε ΕΝΑ PDF για δοκιμή", type="pdf")

if st.button("🔍 ΣΚΑΝΑΡΙΣΜΑ & ΕΛΕΓΧΟΣ") and uploaded_file:
    client = get_vision_client()
    
    if client:
        try:
            st.info("Μετατροπή PDF σε εικόνα και αποστολή στη Google...")
            
            # 1. Convert PDF to Image
            images = convert_from_bytes(uploaded_file.read())
            full_text = ""
            
            # 2. Google Vision
            for img in images:
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                content = img_byte_arr.getvalue()
                
                image = vision.Image(content=content)
                response = client.text_detection(image=image)
                
                if response.text_annotations:
                    full_text += response.text_annotations[0].description + "\n"
            
            # --- ΤΟ ΠΙΟ ΣΗΜΑΝΤΙΚΟ ΜΕΡΟΣ: ΕΜΦΑΝΙΣΗ RAW TEXT ---
            st.divider()
            st.subheader("🔍 Τι βλέπει η Google (Raw Text)")
            st.text_area("Αντέγραψε αυτό το κείμενο και δείξε μου τι λέει αν δεν βγάζει νόημα:", full_text, height=400)
            
            # --- ΔΟΚΙΜΑΣΤΙΚΗ ΑΝΑΛΥΣΗ ---
            st.divider()
            st.subheader("🧪 Δοκιμή Εξαγωγής")
            
            # Δοκιμάζουμε να βρούμε Αιμοπετάλια με πολλούς τρόπους
            keywords = ["PLT", "Αιμοπετάλια", "Aιμοπετάλια", "Platelets"] # Πρόσεξε το λατινικό A
            found_val = None
            found_line = ""
            
            lines = full_text.split('\n')
            for line in lines:
                # Καθαρισμός γραμμής
                clean_line = " ".join(line.split())
                
                if any(k.upper() in clean_line.upper() for k in keywords):
                    found_line = clean_line
                    # Ψάχνουμε αριθμούς
                    numbers = re.findall(r"(\d+[,.]\d+|\d+)", clean_line)
                    for num in numbers:
                        val = clean_number(num)
                        if val is not None and val > 10: # Φίλτρο για PLT
                            found_val = val
                            break
            
            if found_val:
                st.success(f"✅ ΒΡΕΘΗΚΑΝ ΑΙΜΟΠΕΤΑΛΙΑ: {found_val}")
                st.write(f"Η γραμμή που το βρήκε ήταν: `{found_line}`")
            else:
                st.error("❌ ΔΕΝ βρέθηκαν Αιμοπετάλια.")
                if found_line:
                    st.warning(f"Βρήκα τη λέξη 'Αιμοπετάλια' σε αυτή τη γραμμή, αλλά δεν μπόρεσα να διαβάσω τον αριθμό: \n`{found_line}`")
                else:
                    st.warning("Δεν βρήκα καν τη λέξη 'Αιμοπετάλια' ή 'PLT' στο κείμενο.")

        except Exception as e:
            st.error(f"Σφάλμα: {e}")
