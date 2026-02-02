import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste - Audit Formats", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
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
                raw_str = content.decode('latin1')
                try:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=',', quotechar='"', on_bad_lines='skip')
                    if len(df_temp.columns) < 5: raise Exception()
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=';', quotechar='"', on_bad_lines='skip')
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE VENTILATION DES FORMATS ---
def ventiler_formats(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    
    # 1. Le Sans Gluten (SG4P)
    if code.endswith('SG4P'):
        return pd.Series([0, qty, 0])
    
    # 2. Les caisses de 12 (finissent par 12)
    elif code.endswith('12'):
        return pd.Series([qty, 0, 0])
    
    # 3. Tout le reste (Format 24 traditionnel)
    else:
        return pd.Series([0, 0, qty])

# --- CHARGEMENT ET PRÉPARATION ---
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE)

if df_raw_all is not None:
    # Dates
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
    
    # Numérique
    for col in ['LineQty', 'LineTotal', 'Rabais']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    # Ventilation
    df_raw[['VOL_12', 'VOL_SG4P', 'VOL_24']] = df_raw.apply(ventiler_formats, axis=1)
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

    st.title("📊 Rapport Audit Alchimiste : 2025 vs 2026")

    # --- KPI COMPARATIFS YTD ---
    df_2026 = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.subheader("💰 Performance Financière (Argent)")
    m1, m2 = st.columns(2)
    m1.metric("Ventes $ 2026", f"{df_2026['LineTotal'].sum():,.2f} $")
    m2.metric("Ventes $ 2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.2f} $", 
              delta=f"{df_2026['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.2f} $")

    st.divider()
    st.subheader("📦 Audit des Volumes (Quantités brutes)")
    v1, v2, v3 = st.columns(3)
    
    with v1:
        st.info("Format 12 (Codes *12)")
        st.metric("2026", f"{df_2026['VOL_12'].sum():,.0f}")
        st.metric("2025", f"{df_2025_ytd['VOL_12'].sum():,.0f}")

    with v2:
        st.warning("Sans Gluten (Codes *SG4P)")
        st.metric("2026", f"{df_2026['VOL_SG4P'].sum():,.0f}")
        st.metric("2025", f"{df_2025_ytd['VOL_SG4P'].sum():,.0f}")

    with v3:
        st.success("Format 24 (Traditionnel)")
        st.metric("2026", f"{df_2026['VOL_24'].sum():,.0f}")
        st.metric("2025", f"{df_2025_ytd['VOL_24'].sum():,.0f}")

    # --- DÉTAIL PRODUITS POUR VÉRIFIER LE SG ---
    st.divider()
    st.subheader("🔍 Focus Produit : Est-ce que tes 1500 caisses de SG sont ici ?")
    
    # On filtre sur les produits qui contiennent "GLUTEN" ou dont le code finit par SG4P
    df_sg_check = df_2026[df_2026['ItemCode'].str.contains('SG4P', na=False) | df_2026['ItemName'].str.contains('GLUTEN', na=False, case=False)]
    
    if not df_sg_check.empty:
        summary_sg = df_sg_check.groupby(['ItemCode', 'ItemName']).agg({'LineQty':'sum', 'LineTotal':'sum'}).reset_index()
        st.dataframe(summary_sg.sort_values('LineQty', ascending=False), use_container_width=True)
    else:
        st.error("ALERTE : Aucune ligne de Sans Gluten détectée en 2026 avec ces filtres !")

else:
    st.warning("En attente des données Drive...")
