import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Alchimiste Pro", layout="wide")

# --- CONFIGURATION ---
ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"
NOMS_COURTS = {'La Blonde sans alcool': 'BLO Sans Alcool', 'La Blanche sans alcool': 'BLA Sans Alcool'}

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and name contains '.csv' and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: return None
    df_list = []
    for item in items:
        content = service.files().get_media(fileId=item['id']).execute().decode('latin1')
        # Lecture robuste avec moteur Python
        df_temp = pd.read_csv(io.StringIO(content), sep=',', quotechar='"', on_bad_lines='skip', skip_blank_lines=True)
        df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage et Conversion
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

        # Sidebar Filtres
        st.sidebar.header("📅 Filtres")
        date_range = st.sidebar.date_input("Période", value=(df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()))
        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else: df = df_raw.copy()

        st.title("📊 Rapport de Ventes Alchimiste")

        # --- FOCUS SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_week = latest_day - pd.Timedelta(days=6)
        df_week = df_raw[df_raw['DocDate'] >= start_week].copy()
        with st.expander(f"🔔 FOCUS : Dernière semaine ({start_week.strftime('%Y-%m-%d')} au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            week_sku = df_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('LineQty', ascending=False)
            st.table(week_sku.rename(columns={'LineQty': 'Caisses', 'LineTotal': 'Total ($)'}))

        # --- KPI OPTIMISÉS ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Volume Caisses", f"{df['LineQty'].sum():,.2f}")
        c2.metric("Lignes de Ventes", len(df), help="C'est le nombre de transactions (votre calcul de 23).")
        c3.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c4.metric("Nb Factures", df['DocNum'].nunique())

        # --- GRAPHIQUE ---
        st.header("📦 Ventes par Produit")
        sku_total = df.groupby('ItemName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', color_continuous_scale='Viridis', text_auto='.2f')
        fig.update_layout(bargap=0.3, xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

        # --- EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            format_bold = writer.book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
            # On génère les onglets... (Logique identique à la précédente)
            sku_total.to_excel(writer, sheet_name='Produits', index=False)
            # Ajout du total en gras
            ws = writer.sheets['Produits']
            ws.write(len(sku_total)+1, 0, "TOTAL GÉNÉRAL", format_bold)
            ws.write(len(sku_total)+1, 1, sku_total['LineQty'].sum(), format_bold)

        st.sidebar.download_button("📥 Télécharger Excel", output.getvalue(), "Rapport_Alchimiste.xlsx")

except Exception as e:
    st.error(f"Erreur : {e}")
