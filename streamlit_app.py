import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Alchimiste Pro", layout="wide")

# --- CONFIGURATION ---
ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

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
            
            # --- LECTURE SELON LE FORMAT ---
            if item['name'].lower().endswith('.xlsx'):
                # Lecture Excel
                df_temp = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                # Lecture CSV
                df_temp = pd.read_csv(io.StringIO(content.decode('latin1')), sep=',', quotechar='"', on_bad_lines='skip')
            
            df_list.append(df_temp)
        except Exception as e:
            st.warning(f"Impossible de lire {item['name']}: {e}")
            
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION 12/24 ---
def harmoniser_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]])
    return pd.Series([qty, code])

try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage des colonnes (Essentiel pour Excel)
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        
        # S'assurer que les chiffres sont bien lus comme des nombres
        for col in ['LineQty', 'LineTotal']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        # Application de la logique de conversion
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats, axis=1)
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Num'] = df_raw['DocDate'].dt.month

        st.title("📊 Dashboard Ventes Alchimiste")
        st.success(f"Données chargées : {len(df_raw)} lignes (CSV et Excel combinés)")

        # --- 1. FOCUS DERNIÈRE SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_week = latest_day - pd.Timedelta(days=6)
        df_latest = df_raw[df_raw['DocDate'] >= start_week].copy()
        
        with st.expander(f"🔔 FOCUS : Dernière semaine reçue (jusqu'au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            focus_table = df_latest.groupby(['SKU_BASE', 'ItemName']).agg({'LineQty': 'sum', 'CAISSE EQ': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('CAISSE EQ', ascending=False)
            st.table(focus_table.rename(columns={'LineQty': 'Qté Phys.', 'CAISSE EQ': 'Eq. 24', 'LineTotal': 'Ventes ($)'}))

        st.divider()

        # --- 2. ANALYSE COMPARATIVE (YoY) ---
        st.header("📈 Comparaison Annuelle (YoY)")
        yoy_pivot = df_raw.pivot_table(index='Mois_Num', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        
        col_graph, col_stats = st.columns([2, 1])
        with col_graph:
            if len(yoy_pivot.columns) >= 2:
                st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Num', y=yoy_pivot.columns, markers=True), use_container_width=True)
            else:
                st.info("Ajoutez vos fichiers 2024 dans le Drive pour voir la comparaison.")
        
        with col_stats:
            st.metric("Total Caisses EQ", f"{df_raw['CAISSE EQ'].sum():,.1f}")
            st.metric("Ventes Totales ($)", f"{df_raw['LineTotal'].sum():,.2f} $")

        # --- 3. BANNIÈRES & CALENDRIERS ---
        st.header("🏢 Analyse par Bannière")
        if 'GroupName' in df_raw.columns:
            ban_data = df_raw.groupby('GroupName')['CAISSE EQ'].sum().reset_index()
            st.plotly_chart(px.pie(ban_data, values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)

        st.header("📅 Ventes Mensuelles par Produit")
        st.dataframe(df_raw.pivot_table(index='ItemName', columns=df_raw['DocDate'].dt.to_period('M').astype(str), values='CAISSE EQ', aggfunc='sum', fill_value=0), use_container_width=True)

        # --- 4. EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_raw.to_excel(writer, sheet_name='Donnees_Brutes', index=False)
            yoy_pivot.to_excel(writer, sheet_name='YoY_Mensuel')
        
        st.sidebar.download_button("📥 Télécharger Rapport Excel", output.getvalue(), "Rapport_Alchimiste.xlsx")

except Exception as e:
    st.error(f"Erreur lors du traitement : {e}")
