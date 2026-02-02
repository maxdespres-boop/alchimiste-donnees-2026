import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb"

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx') and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: return None
    df_list = []
    for item in items:
        try:
            request = service.files().get_media(fileId=item['id'])
            content = request.execute()
            if item['name'].lower().endswith('.xlsx'):
                df_temp = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                df_temp = pd.read_csv(io.StringIO(content.decode('latin1')), sep=',', quotechar='"', on_bad_lines='skip')
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

try:
    if df_raw_all is not None:
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw = df_raw_all.dropna(subset=['DocDate']).copy()
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        # --- CALCULS TEMPORELS (Avant conversion) ---
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Nom'] = df_raw['DocDate'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DocDate'].dt.dayofyear

        # --- LOGIQUES DE CONVERSION ---
        def harmoniser_alc(row):
            code = str(row['ItemCode']).strip()
            qty = row['LineQty']
            # CORRECTION : En 2025, le fichier est déjà en format caisse. 
            # On ne divise par 2 que pour les nouveaux fichiers SAP de 2026.
            if row['Année'] >= 2026 and code.endswith('12'):
                return pd.Series([qty * 0.5, code[:-2]])
            return pd.Series([qty, code])

        def harmoniser_loop(row):
            code = str(row['ItemCode']).strip()
            qty = row['LineQty']
            if code.endswith('12'): return pd.Series([qty, code[:-2]])
            return pd.Series([qty, code])

        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_alc, axis=1)
            label_unit = "Eq. 24"
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            
            # --- KPI ---
            df_2026 = df_alc[df_alc['Année'] == 2026]
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            
            st.title("📊 Dashboard Alchimiste")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Vol. 2026", f"{df_2026['CAISSE EQ'].sum():,.0f}")
            k2.metric("Ventes 2026", f"{df_2026['LineTotal'].sum():,.0f} $")
            k3.metric("Vol. 2025 YTD", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
            k4.metric("Ventes 2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

            # --- GRAPHIQUES & EXPORT ---
            st.header("📈 Comparaison YTD")
            df_ytd_global = pd.concat([df_2026, df_2025_ytd])
            yoy_val = df_ytd_global.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
            st.plotly_chart(px.line(yoy_val.reset_index(), x='Mois_Nom', y=yoy_val.columns, markers=True, title="Ventes Dollars YTD"), use_container_width=True)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                yoy_val.to_excel(writer, sheet_name='Finance_YTD_Match')
                df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').to_excel(writer, sheet_name='Volume_Mensuel')
            st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_Final.xlsx")

        elif page == "LOOP":
            st.title("🍹 Dashboard LOOP")
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_loop, axis=1)
            st.dataframe(df_raw.groupby('Mois_Nom')['CAISSE EQ'].sum())

    else: st.warning("ID Dossier manquant.")
except Exception as e: st.error(f"Erreur : {e}")
