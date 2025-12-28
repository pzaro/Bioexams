import streamlit as st
from google.cloud import vision
from google.oauth2 import service_account
from pdf2image import convert_from_bytes
import pandas as pd
import io
import re

# --- ΡΥΘΜΙΣΕΙΣ ---
st.set_page_config(page_title="Medical Lab Extractor Pro", layout="wide")
st.title("🩸 Εξαγωγή Εξετάσεων (Πλήρης Έλεγχος)")
st.info("Επίλεξε από τη λίστα ποιούς δείκτες θέλεις να ψάξει η Google στα PDF σου.")

# --- 1. ΑΥΘΕΝΤΙΚΟΠΟΙΗΣΗ (GOOGLE VISION) ---
def get_vision_client():
    try:
        key_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(key_dict)
        client = vision.ImageAnnotatorClient(credentials=creds)
        return client
    except Exception as e:
        st.error(f"Πρόβλημα με το κλειδί Google Cloud: {e}")
        return None

# --- 2. ΚΑΘΑΡΙΣΜΟΣ ΤΙΜΩΝ ---
def clean_number(val_str):
    if not val_str: return None
    # Διορθώσεις OCR λαθών
    val_str = val_str.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    val_str = val_str.replace('S', '5').replace('B', '8') # Συχνά λάθη
    
    clean = re.sub(r"[^0-9,.]", "", val_str)
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except:
        return None

# --- 3. LOGIC ΕΥΡΕΣΗΣ ---
def parse_google_text(full_text, selected_metrics_map):
    results = {}
    lines = full_text.split('\n')
    
    for line in lines:
        clean_line = " ".join(line.split()) # Καθαρισμός κενών
        
        # Ψάχνουμε ΜΟΝΟ για τους δείκτες που επέλεξε ο χρήστης
        for metric_name, keywords in selected_metrics_map.items():
            
            # Αν βρούμε έστω μία λέξη-κλειδί στη γραμμή (π.χ. "RBC" ή "Ερυθρά")
            if any(key.upper() in clean_line.upper() for key in keywords):
                
                # Ψάχνουμε αριθμούς
                numbers = re.findall(r"(\d+[,.]\d+|\d+)", clean_line)
                
                for num in numbers:
                    val = clean_number(num)
                    if val is not None:
                        # --- Φίλτρα Ασφαλείας ---
                        # Έτη
                        if val > 1900 and val < 2100 and "B12" not in metric_name: continue
                        # Κωδικοί εξετάσεων (συχνά 6-8 ψηφία)
                        if val > 10000: continue
                        
                        # Ειδικά φίλτρα για να μην μπερδεύει τα νούμερα
                        if "Αιματοκρίτης" in metric_name and val < 10: continue # Ο HCT είναι συνήθως > 20
                        if "Αιμοπετάλια" in metric_name and val < 10: continue # Τα PLT είναι συνήθως > 100
                        
                        results[metric_name] = val
                        break
    return results

# --- 4. Η ΜΕΓΑΛΗ ΛΙΣΤΑ ΔΕΙΚΤΩΝ ---
# Εδώ ορίζουμε ΤΑ ΠΑΝΤΑ. Μπορείς να προσθέσεις κι άλλα αν λείπουν.
ALL_METRICS = {
    # --- Γενική Αίματος ---
    "Ερυθρά (RBC)": ["RBC", "Ερυθρά", "Red Blood"],
    "Αιμοσφαιρίνη (HGB)": ["HGB", "Αιμοσφαιρίνη", "Hemoglobin"],
    "Αιματοκρίτης (HCT)": ["HCT", "Αιματοκρίτης", "Hematocrit"],
    "Μέσος Όγκος Ερ. (MCV)": ["MCV", "Μέσος Όγκος"],
    "Μέση Περιεκτ. Αιμ. (MCH)": ["MCH", "Μέση Περιεκτ"],
    "Μέση Πυκν. Αιμ. (MCHC)": ["MCHC", "Μέση Πυκν"],
    "Αιμοπετάλια (PLT)": ["PLT", "Αιμοπετάλια", "Platelets"],
    "Λευκά (WBC)": ["WBC", "Λευκά", "White Blood"],
    "Ουδετερόφιλα (NEUT)": ["NEUT", "Ουδετερόφιλα", "Polymorph"],
    "Λεμφοκύτταρα (LYMPH)": ["LYMPH", "Λεμφοκύτταρα"],
    "Μονοπύρηνα (MONO)": ["MONO", "Μονοπύρηνα"],
    "Ηωσινόφιλα (EOS)": ["EOS", "Ηωσινόφιλα"],
    "Βασέοφιλα (BASO)": ["BASO", "Βασέοφιλα"],
    
    # --- Βιοχημικές ---
    "Σάκχαρο (GLU)": ["GLU", "Σάκχαρο", "Glucose"],
    "Ουρία": ["Urea", "Ουρία"],
    "Κρεατινίνη": ["Creatinine", "Κρεατινίνη"],
    "Ουρικό Οξύ": ["Uric Acid", "Ουρικό Οξύ"],
    "Χοληστερίνη Ολική": ["Cholesterol", "Χοληστερίνη"],
    "HDL (Καλή)": ["HDL"],
    "LDL (Κακή)": ["LDL"],
    "Τριγλυκερίδια": ["Triglycerides", "Τριγλυκερίδια"],
    "SGOT (AST)": ["SGOT", "AST", "ΑΣΤ"],
    "SGPT (ALT)": ["SGPT", "ALT", "ΑΛΤ"],
    "γ-GT": ["GGT", "γ-GT", "γGT"],
    "Αλκαλική Φωσφατάση (ALP)": ["ALP", "Αλκαλική"],
    "Σίδηρος (Fe)": ["Iron", "Σίδηρος", "Fe "],
    "Φερριτίνη": ["Ferritin", "Φερριτίνη"],
    "Ασβέστιο (Ca)": ["Calcium", "Ασβέστιο"],
    "Μαγνήσιο (Mg)": ["Magnesium", "Μαγνήσιο"],
    "Κάλιο (K)": ["Potassium", "Κάλιο"],
    "Νάτριο (Na)": ["Sodium", "Νάτριο"],
    
    # --- Ορμόνες & Βιταμίνες ---
    "TSH (Θυρεοειδής)": ["TSH", "Θυρεοειδοτρόπος"],
    "FT4": ["FT4", "Ελεύθερη Θυροξίνη"],
    "FT3": ["FT3", "Τριιωδοθυρονίνη"],
    "T3": ["T3 "],
    "T4": ["T4 "],
    "Βιταμίνη B12": ["B12", "Cobalamin"],
    "Φυλλικό Οξύ": ["Folic", "Φυλλικό"],
    "Βιταμίνη D": ["Vit D", "D3", "25-OH"]
}

# --- 5. UI ΕΦΑΡΜΟΓΗΣ ---
uploaded_files = st.file_uploader("📂 Ανεβάστε PDF", type="pdf", accept_multiple_files=True)

# MULTISELECT: Εδώ επιλέγεις τι θες!
st.write("### ⚙️ Επιλογή Δεικτών")
selected_keys = st.multiselect(
    "Ποιές εξετάσεις θέλεις να εξάγεις;", 
    list(ALL_METRICS.keys()), 
    default=["Ερυθρά (RBC)", "Αιμοσφαιρίνη (HGB)", "Αιμοπετάλια (PLT)", "Λευκά (WBC)", "Σάκχαρο (GLU)", "Χοληστερίνη Ολική"] # Προεπιλογές
)

# Δημιουργία υπο-λίστας μόνο με τα επιλεγμένα
active_metrics = {k: ALL_METRICS[k] for k in selected_keys}

if st.button("🚀 ΕΝΑΡΞΗ EXCEL") and uploaded_files:
    client = get_vision_client()
    
    if not active_metrics:
        st.warning("⚠️ Δεν επέλεξες καμία εξέταση! Διάλεξε κάτι από τη λίστα.")
    elif client:
        all_data = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            file_results = {'Αρχείο': file.name}
            full_text_scan = ""
            
            try:
                # PDF -> Images
                images = convert_from_bytes(file.read())
                
                for img in images:
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    content = img_byte_arr.getvalue()
                    
                    # Google Vision Call
                    image = vision.Image(content=content)
                    response = client.text_detection(image=image)
                    
                    if response.text_annotations:
                        full_text_scan += response.text_annotations[0].description + "\n"
                
                # Ανάλυση με βάση τις επιλογές σου
                data = parse_google_text(full_text_scan, active_metrics)
                file_results.update(data)
                
                # Ημερομηνία
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', full_text_scan)
                if date_match:
                    file_results['Ημερομηνία'] = date_match.group(1)
                else:
                    m_file = re.search(r'[-_]?(\d{6})', file.name)
                    if m_file:
                        d = m_file.group(1)
                        file_results['Ημερομηνία'] = f"{d[4:6]}/{d[2:4]}/20{d[0:2]}"
                    else:
                        file_results['Ημερομηνία'] = "Άγνωστη"
                
                all_data.append(file_results)
                
            except Exception as e:
                st.error(f"Σφάλμα στο {file.name}: {e}")
            
            bar.progress((i + 1) / len(uploaded_files))

        if all_data:
            df = pd.DataFrame(all_data)
            
            # Ταξινόμηση
            try:
                df['Sort'] = pd.to_datetime(df['Ημερομηνία'], dayfirst=True, errors='coerce')
                df = df.sort_values('Sort').drop(columns=['Sort'])
            except: pass
            
            # Τακτοποίηση στηλών: Ημερομηνία -> Αρχείο -> Επιλεγμένοι Δείκτες
            desired_order = ['Ημερομηνία', 'Αρχείο'] + selected_keys
            # Φιλτράρουμε μόνο όσες στήλες υπάρχουν όντως στο df (μήπως κάποιες δεν βρέθηκαν καθόλου)
            final_cols = [c for c in desired_order if c in df.columns]
            df = df[final_cols]
            
            st.success(f"Βρέθηκαν δεδομένα σε {len(all_data)} αρχεία!")
            st.dataframe(df)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.download_button(
                "📥 Κατέβασμα Excel", 
                data=output.getvalue(), 
                file_name="blood_tests_results.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
