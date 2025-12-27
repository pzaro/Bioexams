import streamlit as st
import pandas as pd
import re
import calendar

# Ρυθμίσεις Σελίδας
st.set_page_config(page_title="DDD Analysis Tool", layout="wide")

st.title("💊 Υπολογισμός DDD & DID (DDD/1000/ημέρα)")
st.markdown("""
Αυτό το εργαλείο υπολογίζει την κατανάλωση φαρμάκων βάσει των αρχείων πωλήσεων και των δεδομένων DDD.
""")

# --- 1. ΔΕΔΟΜΕΝΑ ΠΛΗΘΥΣΜΟΥ (ΕΛΛΑΔΑ) ---
# Σταθερές τιμές από την ΕΛΣΤΑΤ (όπως στο αρχείο σου)
POPULATION = {
    2016: 10768193,
    2017: 10741165,
    2018: 10724599,
    2019: 10722287,
    2020: 10718565,
    2021: 10482487,
    2022: 10461627,
    2023: 10413982,
    2024: 10400720
    # Για το 2025 χρησιμοποιούμε του 2024 ή προσθέτεις νέα τιμή
}

# --- 2. ΛΕΙΤΟΥΡΓΙΕΣ (FUNCTIONS) ---

def parse_date_columns(df):
    """Εντοπίζει στήλες με ημερομηνίες και Units και μετατρέπει το DataFrame σε Long Format."""
    # Βρίσκουμε στήλες που περιέχουν "Units" και κάποιο μήνα/έτος
    date_cols = []
    
    # Μοτίβο για "Jan 2018 Units" ή "Jan 2018\nUnits"
    pattern = r"([A-Za-z]{3})\s(\d{4}).*[Uu]nits"
    
    melt_vars = []
    for col in df.columns:
        if re.search(pattern, str(col), re.IGNORECASE):
            melt_vars.append(col)
            
    if not melt_vars:
        st.error("Δεν βρέθηκαν στήλες πωλήσεων (μορφή 'Month Year Units'). Ελέγξτε το αρχείο.")
        return None

    # Μετατροπή από Wide σε Long (Unpivot)
    # Κρατάμε τις στήλες αναγνώρισης (όλες εκτός από τις στήλες ημερομηνιών)
    id_vars = [c for c in df.columns if c not in melt_vars]
    
    df_melted = df.melt(id_vars=id_vars, value_vars=melt_vars, var_name='Date_Str', value_name='Units')
    
    # Εξαγωγή Μήνα και Έτους
    def extract_date(s):
        match = re.search(pattern, str(s), re.IGNORECASE)
        if match:
            month_str, year_str = match.groups()
            # Μετατροπή μήνα από όνομα σε αριθμό
            try:
                month_num = list(calendar.month_abbr).index(month_str.title())
            except ValueError:
                # Δοκιμή για πλήρη ονόματα αν χρειαστεί
                return None, None
            return int(year_str), month_num
        return None, None

    df_melted[['Year', 'Month']] = df_melted['Date_Str'].apply(
        lambda x: pd.Series(extract_date(x))
    )
    
    # Καθαρισμός γραμμών χωρίς Units ή Ημερομηνία
    df_melted.dropna(subset=['Year', 'Month', 'Units'], inplace=True)
    df_melted['Units'] = pd.to_numeric(df_melted['Units'], errors='coerce').fillna(0)
    
    # Υπολογισμός ημερών στον μήνα (για τον τύπο του DID)
    df_melted['Days_in_Month'] = df_melted.apply(
        lambda row: calendar.monthrange(int(row['Year']), int(row['Month']))[1], axis=1
    )
    
    # Προσθήκη Πληθυσμού
    df_melted['Population'] = df_melted['Year'].map(POPULATION)
    # Αν λείπει έτος, χρήση του τελευταίου διαθέσιμου (fallback)
    last_pop = list(POPULATION.values())[-1]
    df_melted['Population'] = df_melted['Population'].fillna(last_pop)

    return df_melted

def calculate_ddd(df_sales, df_ref):
    """Συνδυάζει πωλήσεις με δεδομένα αναφοράς και υπολογίζει DDD & DID."""
    
    # Έλεγχος αν υπάρχει κοινή στήλη 'Product'
    if 'Product' not in df_sales.columns or 'Product' not in df_ref.columns:
        st.error("Και τα δύο αρχεία πρέπει να έχουν στήλη 'Product' για την αντιστοίχιση.")
        return None

    # Merge (Left Join)
    merged = pd.merge(df_sales, df_ref, on='Product', how='left')
    
    # Έλεγχος για προϊόντα που δεν βρέθηκαν
    missing = merged[merged['DDD (WHO)'].isna()]['Product'].unique()
    if len(missing) > 0:
        st.warning(f"⚠️ Προσοχή: {len(missing)} προϊόντα δεν βρέθηκαν στο Αρχείο Αναφοράς και δεν θα υπολογιστούν (π.χ. {missing[:3]}).")
    
    # Φιλτράρισμα μόνο όσων έχουν πλήρη στοιχεία
    df_calc = merged.dropna(subset=['MG', 'Pack', 'DDD (WHO)']).copy()
    
    # Βεβαίωση ότι είναι αριθμοί
    for col in ['MG', 'Pack', 'DDD (WHO)', 'Units']:
        df_calc[col] = pd.to_numeric(df_calc[col], errors='coerce')
    
    # --- ΥΠΟΛΟΓΙΣΜΟΙ ---
    # Total MG = Units * Pack Size * MG per unit
    df_calc['Total_MG_Sold'] = df_calc['Units'] * df_calc['Pack'] * df_calc['MG']
    
    # Total DDDs = Total MG / Assigned DDD
    df_calc['Total_DDDs'] = df_calc['Total_MG_Sold'] / df_calc['DDD (WHO)']
    
    # DID = (Total DDDs * 1000) / (Population * Days)
    df_calc['DID'] = (df_calc['Total_DDDs'] * 1000) / (df_calc['Population'] * df_calc['Days_in_Month'])
    
    return df_calc

# --- 3. UI ΕΦΑΡΜΟΓΗΣ ---

col1, col2 = st.columns(2)

with col1:
    st.header("1. Αρχείο Πωλήσεων")
    uploaded_sales = st.file_uploader("Ανεβάστε το Excel/CSV της IQVIA", type=['xlsx', 'csv', 'xlsm'])

with col2:
    st.header("2. Αρχείο Αναφοράς")
    st.info("Πρέπει να περιέχει στήλες: Product, MG, Pack (μέγεθος συσκευασίας), DDD (WHO), Molecule String")
    uploaded_ref = st.file_uploader("Ανεβάστε το Reference Excel", type=['xlsx', 'csv', 'xlsm'])

if uploaded_sales and uploaded_ref:
    st.divider()
    with st.spinner('Επεξεργασία δεδομένων...'):
        # Φόρτωση Πωλήσεων
        if uploaded_sales.name.endswith('csv'):
            df_sales_raw = pd.read_csv(uploaded_sales)
        else:
            df_sales_raw = pd.read_excel(uploaded_sales)
            
        # Φόρτωση Αναφοράς
        if uploaded_ref.name.endswith('csv'):
            df_ref_raw = pd.read_csv(uploaded_ref)
        else:
            # Δοκιμάζουμε να διαβάσουμε το φύλλο 'DATA' αν υπάρχει (όπως στο αρχείο σου)
            try:
                df_ref_raw = pd.read_excel(uploaded_ref, sheet_name='DATA')
            except:
                df_ref_raw = pd.read_excel(uploaded_ref)
        
        # Επιλογή στηλών στο Reference αν χρειάζεται (Mapping)
        # Χρειαζόμαστε: Product, MG, Pack (size), DDD (WHO), Molecule String
        req_cols = ['Product', 'MG', 'Pack', 'DDD (WHO)', 'Molecule String']
        
        # Αν το αρχείο αναφοράς έχει διαφορετικά ονόματα, εδώ θα μπορούσαμε να τα αλλάξουμε. 
        # Προς το παρόν υποθέτουμε ότι ακολουθείς τη δομή της πτυχιακής.
        # Ειδικά για το Pack, στο αρχείο σου η στήλη 'ΑΡ ΔΟΣΕΩΝ' ή 'Pack' ήταν το πλήθος.
        # Θα προσπαθήσουμε να βρούμε τις σωστές στήλες.
        
        available_cols = df_ref_raw.columns.tolist()
        # Απλή λογική για να βρούμε τη στήλη Pack Size (συνήθως αριθμός χαπιών)
        pack_col = 'Pack' if 'Pack' in available_cols else 'ΑΡ ΔΟΣΕΩΝ'
        
        # Καθαρισμός Reference DataFrame
        try:
            df_ref_clean = df_ref_raw[['Product', 'MG', pack_col, 'DDD (WHO)', 'Molecule String']].copy()
            df_ref_clean.rename(columns={pack_col: 'Pack'}, inplace=True)
            # Αφαίρεση διπλότυπων (κρατάμε την πρώτη εγγραφή ανά προϊόν)
            df_ref_clean.drop_duplicates(subset=['Product'], inplace=True)
        except KeyError as e:
            st.error(f"Λείπουν στήλες από το αρχείο αναφοράς: {e}. Βεβαιώσου ότι υπάρχουν οι στήλες: Product, MG, Pack (ή ΑΡ ΔΟΣΕΩΝ), DDD (WHO), Molecule String.")
            st.stop()

        # Επεξεργασία
        df_sales_long = parse_date_columns(df_sales_raw)
        
        if df_sales_long is not None:
            results = calculate_ddd(df_sales_long, df_ref_clean)
            
            if results is not None:
                st.success("Ο υπολογισμός ολοκληρώθηκε!")
                
                # --- ΕΜΦΑΝΙΣΗ ΑΠΟΤΕΛΕΣΜΑΤΩΝ ---
                
                # 1. Συγκεντρωτικά ανά Έτος
                st.subheader("Σύνολο DID ανά Έτος")
                pivot_year = results.groupby('Year')['DID'].sum().reset_index()
                st.dataframe(pivot_year)
                
                # 2. Ανάλυση ανά Δραστική (Substance) ανά Έτος
                st.subheader("DID ανά Δραστική και Έτος")
                pivot_subst = results.groupby(['Molecule String', 'Year'])['DID'].sum().unstack(fill_value=0)
                st.dataframe(pivot_subst)
                
                # 3. Ανάλυση ανά Εμπορική Ονομασία (Brand)
                st.subheader("DID ανά Εμπορική Ονομασία (Top 20)")
                pivot_brand = results.groupby(['Product', 'Year'])['DID'].sum().unstack(fill_value=0)
                pivot_brand['Total'] = pivot_brand.sum(axis=1)
                st.dataframe(pivot_brand.sort_values('Total', ascending=False).head(20))
                
                # --- DOWNLOADS ---
                st.subheader("Λήψη Δεδομένων")
                
                # Πλήρες αρχείο
                csv_full = results.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Κατέβασμα αναλυτικών δεδομένων (CSV)", csv_full, "ddd_analysis_full.csv", "text/csv")
                
                # Συγκεντρωτικό Δραστικών
                csv_subst = pivot_subst.to_csv().encode('utf-8')
                st.download_button("📥 Κατέβασμα συγκεντρωτικού Δραστικών (CSV)", csv_subst, "ddd_by_substance.csv", "text/csv")

                # --- PLOTS ---
                st.subheader("Διάγραμμα Εξέλιξης DID")
                chart_data = results.groupby(['Year', 'Molecule String'])['DID'].sum().reset_index()
                st.line_chart(chart_data, x='Year', y='DID', color='Molecule String')
