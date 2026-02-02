import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Audit Alchimiste - Volume Brut", layout="wide")

# --- CONFIGURATION DES DOSSIERS ---
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

# --- LOGIQUE D'AUDIT SANS CONVERSION ---
def ventiler_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    # On sépare simplement selon le code
    if code.endswith('12') or code.endswith('G4P'):
        return pd.Series([qty, 0]) # C'est un format 12
    else:
        return pd.Series([0, qty]) # On assume que c'est un format 24

# --- CHARGEMENT ---
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE)

try:
    if df_raw_all is not None:
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
        df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
        df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        # On ventile les quantités
        df_raw[['QTY_12', 'QTY_24']] = df_raw.apply(ventiler_formats, axis=1)
        df_raw['Année'] = df_raw['DateAnalyse'].dt.year
        df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

        df_alc = df_raw[df_raw['Année'] >= 2025].copy()
        
        st.title("🔍 Audit des Volumes Bruts (Sans Conversion)")
        
        # --- CALCULS ---
        df_2026 = df_alc[df_alc['Année'] == 2026]
        max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
        df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]

        # --- KPI ---
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Ventes $ 2026", f"{df_2026['LineTotal'].sum():,.0f} $")
            st.metric("Ventes $ 2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", 
                      delta=f"{df_2026['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")
        
        with c2:
            st.metric("Total Caisses 24 (Brut) 2026", f"{df_2026['QTY_24'].sum():,.0f}")
            st.metric("Total Caisses 24 (Brut) 2025", f"{df_2025_ytd['QTY_24'].sum():,.0f}")

        with c3:
            st.metric("Total Caisses 12 (Brut) 2026", f"{df_2026['QTY_12'].sum():,.0f}")
            st.metric("Total Caisses 12 (Brut) 2025", f"{df_2025_ytd['QTY_12'].sum():,.0f}")

        # --- TABLEAU DE DÉTAIL POUR COMPRENDRE ---
        st.divider()
        st.subheader("📦 Vérification par Produit (Top 20)")
        sku_audit = df_alc[df_alc['Année'] == 2026].groupby('ItemName').agg({
            'LineQty': 'sum',
            'LineTotal': 'sum'
        }).sort_values('LineTotal', ascending=False).head(20)
        sku_audit['Prix Moyen Unitaire'] = sku_audit['LineTotal'] / sku_audit['LineQty']
        st.dataframe(sku_audit.style.format({'LineTotal': '{:,.2f} $', 'Prix Moyen Unitaire': '{:.2f} $'}))

    else:
        st.warning("Aucune donnée trouvée.")
except Exception as e:
    st.error(f"Erreur : {e}")
