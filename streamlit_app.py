import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master Intégral", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb" 

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
    # On récupère aussi 'modifiedTime' pour identifier le fichier le plus récent
    results = service.files().list(q=query, fields="files(id, name, modifiedTime)").execute().get('files', [])
    if not results: return None, None
    
    df_list = []
    # Trier pour identifier le plus récent
    results_sorted = sorted(results, key=lambda x: x['modifiedTime'], reverse=True)
    latest_file_id = results_sorted[0]['id']
    latest_file_name = results_sorted[0]['name']

    for item in results_sorted:
        try:
            request = service.files().get_media(fileId=item['id'])
            content = request.execute()
            if item['name'].lower().endswith('.xlsx'):
                df_temp = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                raw_str = content.decode('latin1')
                try:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=',', quotechar='"', on_bad_lines='skip')
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=';', quotechar='"', on_bad_lines='skip')
            
            df_temp.columns = df_temp.columns.str.strip()
            df_temp['_source_file'] = item['id'] # Pour isoler le dernier fichier plus tard
            for col in ['LineQty', 'LineTotal']:
                if col in df_temp.columns:
                    df_temp[col] = pd.to_numeric(df_temp[col].astype(str).str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
            df_list.append(df_temp)
        except Exception: continue
    
    full_df = pd.concat(df_list, ignore_index=True) if df_list else None
    return full_df, latest_file_id

# --- LOGIQUE DE CONVERSION ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025: return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or (not code.endswith('12')): return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all, latest_id = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # --- PRÉ-TRAITEMENT ---
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw_all['Année'] = df_raw_all['DateAnalyse'].dt.year
    df_raw_all['Semaine'] = df_raw_all['DateAnalyse'].dt.isocalendar().week
    df_raw_all['Mois_Nom'] = df_raw_all['DateAnalyse'].dt.strftime('%m - %B')
    df_raw_all['Jour_Annee'] = df_raw_all['DateAnalyse'].dt.dayofyear

    # Subdivision Métro / Super C
    if 'GroupName' in df_raw_all.columns:
        df_raw_all['Bannière'] = df_raw_all['GroupName'].astype(str)
        mask_m = df_raw_all['GroupName'].str.contains("METRO|MÉTRO", case=False, na=False)
        mask_s = df_raw_all['CardName'].str.contains("SUPER C", case=False, na=False)
        df_raw_all.loc[mask_m, 'Bannière'] = "METRO"
        df_raw_all.loc[mask_m & mask_s, 'Bannière'] = "SUPER C"

    if page == "Alchimiste":
        df_raw_all[['CAISSE EQ', 'SKU_BASE']] = df_raw_all.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw_all['CAISSE EQ'] = df_raw_all['LineQty']

    # --- SECTION RÉTRACTABLE : DERNIER ARRIVAGE ---
    with st.expander("🔔 ANALYSE DU DERNIER FICHIER DÉPOSÉ", expanded=True):
        df_latest = df_raw_all[df_raw_all['_source_file'] == latest_id].copy()
        target_week = df_latest['Semaine'].max()
        
        st.write(f"Données basées sur la **Semaine {target_week}** du dernier fichier importé.")
        
        # Comparaison SKU Year over Year pour cette semaine spécifique
        df_hist = df_raw_all[(df_raw_all['Semaine'] == target_week)]
        sku_comp = df_hist.pivot_table(
            index='ItemName', 
            columns='Année', 
            values=['CAISSE EQ', 'LineTotal'], 
            aggfunc='sum'
        ).fillna(0)
        
        # On s'assure que 2025 et 2026 existent pour le calcul
        cols_present = sku_comp.columns
        if ('CAISSE EQ', 2026) in cols_present and ('CAISSE EQ', 2025) in cols_present:
            sku_comp[('Variation', 'Vol')] = sku_comp[('CAISSE EQ', 2026)] - sku_comp[('CAISSE EQ', 2025)]
            sku_comp[('Variation', '$$')] = sku_comp[('LineTotal', 2026)] - sku_comp[('LineTotal', 2025)]

        st.dataframe(sku_comp.style.format("{:,.0f}").format("{:,.2f} $", subset=[col for col in sku_comp.columns if 'LineTotal' in str(col) or '$$' in str(col)]), use_container_width=True)

    # --- LE RESTE DU DASHBOARD (YTD) ---
    st.title(f"📊 Dashboard Global {page}")
    df_2026 = df_raw_all[df_raw_all['Année'] == 2026]
    df_2025_ytd = df_raw_all[(df_raw_all['Année'] == 2025) & (df_raw_all['Jour_Annee'] <= df_2026['Jour_Annee'].max() if not df_2026.empty else 366)]

    c1, c2 = st.columns(2)
    c1.metric("Volume 2026 YTD", f"{df_2026['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    c2.metric("Ventes 2026 YTD", f"{df_2026['LineTotal'].sum():,.0f} $", delta=f"{df_2026['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- TOP BANNIÈRES (Avec Super C séparé) ---
    st.divider()
    st.header("🏢 Répartition par Bannière")
    # On utilise ici le filtre de date de la sidebar pour cette section
    st.sidebar.divider()
    date_sel = st.sidebar.date_input("Période Graphiques", value=(date(2026,1,1), date.today()))
    
    df_filt = df_raw_all[(df_raw_all['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw_all['DateAnalyse'].dt.date <= date_sel[1])]
    
    banner_chart = df_filt.groupby('Bannière')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
    st.plotly_chart(px.pie(banner_chart.head(10), values='CAISSE EQ', names='Bannière', hole=0.4), use_container_width=True)

    # ... (Reste de ton code pour les graphiques mensuels et export)
else:
    st.error("Aucune donnée trouvée.")
