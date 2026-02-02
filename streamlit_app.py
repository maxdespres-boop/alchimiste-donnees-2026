import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP", layout="wide")

# --- CONFIGURATION DES DOSSIERS ---
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

# --- LOGIQUES DE CONVERSION CORRIGÉES ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    # Correction double comptage : On n'applique le *0.5 QUE pour 2026 (SAP)
    # 2025 (Courtier) est déjà au bon format.
    if row['Année'] == 2026 and code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]])
    return pd.Series([qty, code])

def harmoniser_formats_loop(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty, code[:-2]])
    return pd.Series([qty, code])

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ---
current_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_id)

try:
    if df_raw_all is not None:
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw = df_raw_all.dropna(subset=['DocDate']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Nom'] = df_raw['DocDate'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DocDate'].dt.dayofyear

        # Application de la conversion (Année est maintenant dispo pour harmoniser_formats_alc)
        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
            label_unit = "Eq. 24"
        else:
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_loop, axis=1)
            label_unit = "Caisses (12)"

        if page == "Alchimiste":
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            
            # --- KPI & GRAPHIQUES FINANCIERS RESTAURÉS ---
            df_2026 = df_alc[df_alc['Année'] == 2026]
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            
            # Totaux financiers
            total_val_2026 = df_2026['LineTotal'].sum()
            total_val_2025_ytd = df_2025_ytd['LineTotal'].sum()

            st.title("📊 Rapport de Ventes Alchimiste")

            # KPI Visuels
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("CAISSES EQ 2026", f"{df_2026['CAISSE EQ'].sum():,.1f}")
            k2.metric("VENTES $ 2026", f"{total_val_2026:,.2f} $")
            k3.metric("CAISSES EQ 2025 (YTD)", f"{df_2025_ytd['CAISSE EQ'].sum():,.1f}")
            k4.metric("VENTES $ 2025 (YTD)", f"{total_val_2025_ytd:,.2f} $", delta=f"{total_val_2026 - total_val_2025_ytd:,.2f} $")

            # Graphiques YoY (Volume et Dollars)
            st.header("📈 Analyse Comparative")
            tab1, tab2 = st.tabs(["Volume (Eq. 24)", "Ventes ($)"])
            
            with tab1:
                yoy_vol = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_vol.reset_index(), x='Mois_Nom', y=yoy_vol.columns, markers=True), use_container_width=True)
            
            with tab2:
                # On utilise df_alc filtré YTD pour que Janvier 2025 balance avec l'app dans l'Excel
                df_ytd_only = pd.concat([df_2026, df_2025_ytd])
                yoy_val = df_ytd_only.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_val.reset_index(), x='Mois_Nom', y=yoy_val.columns, markers=True, title="Ventes YTD"), use_container_width=True)

            # Export Excel (Balancement App/Excel garanti par df_ytd_only)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                yoy_vol.to_excel(writer, sheet_name='Volume_YOY')
                yoy_val.to_excel(writer, sheet_name='Ventes_YTD_Dollars')
                df_alc.groupby('ItemName').agg({'CAISSE EQ':'sum', 'LineTotal':'sum'}).to_excel(writer, sheet_name='Detail_Produits')
            
            st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_{page}.xlsx")

        elif page == "LOOP":
            # Section LOOP simplifiée pour rester fidèle à ton original
            st.title("🍹 Rapport LOOP")
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            st.dataframe(df_loop.groupby('ItemName')['CAISSE EQ'].sum())

    else:
        st.warning("Données Drive introuvables.")

except Exception as e:
    st.error(f"Erreur : {e}")
