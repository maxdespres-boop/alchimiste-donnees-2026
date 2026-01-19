import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Ventes Alchimiste", layout="wide")

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
        df_temp = pd.read_csv(io.StringIO(content), sep=',', quotechar='"', on_bad_lines='skip', skip_blank_lines=True)
        df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

# --- FONCTION DE CALCUL CAISSE ÉQUIVALENTE ---
def calculer_caisse_eq(row):
    item_name = str(row['ItemName']).upper()
    qty = row['LineQty']
    # Si c'est une caisse de 12, elle vaut 0.5 d'une caisse de 24
    if "12" in item_name or "1/12" in str(row.get('Format', '')):
        return qty * 0.5
    # Par défaut (pour les 4 packs ou formats 24), on garde 1:1
    return qty

try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        for col in ['LineQty', 'LineTotal']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

        # Ajout de la colonne CAISSE EQ
        df_raw['CAISSE EQ'] = df_raw.apply(calculer_caisse_eq, axis=1)

        # Filtres Sidebar
        st.sidebar.header("📅 Filtres")
        date_range = st.sidebar.date_input("Période", value=(df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()))
        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else: df = df_raw.copy()

        st.title("📊 Rapport de Ventes Alchimiste")

        # --- 1. FOCUS DERNIÈRE SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_week = latest_day - pd.Timedelta(days=6)
        df_week = df_raw[df_raw['DocDate'] >= start_week].copy()
        with st.expander(f"🔔 FOCUS : Dernière semaine reçue", expanded=True):
            week_sku = df_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'CAISSE EQ': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('CAISSE EQ', ascending=False)
            st.table(week_sku.rename(columns={'LineQty': 'Caisses (Physiques)', 'LineTotal': 'Total ($)'}))

        # --- 2. KPI ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total CAISSES EQ", f"{df['CAISSE EQ'].sum():,.2f}", help="Toutes les ventes converties en équivalent caisses de 24.")
        c2.metric("Caisses Physiques", f"{df['LineQty'].sum():,.2f}")
        c3.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c4.metric("Nb Factures", df['DocNum'].nunique())

        # --- 3. VENTES PAR PRODUIT ---
        st.header("📦 Performance par Produit")
        sku_total = df.groupby('ItemName').agg({'LineQty': 'sum', 'CAISSE EQ': 'sum'}).reset_index().sort_values('CAISSE EQ', ascending=False)
        fig = px.bar(sku_total, x='ItemName', y='CAISSE EQ', color='CAISSE EQ', text_auto='.2f', color_continuous_scale='Viridis', labels={'CAISSE EQ': 'Caisses Eq (24)'})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_total, use_container_width=True, hide_index=True)

        # --- 4. BANNIÈRES ET CLIENTS ---
        st.header("🏢 Analyse par Bannière")
        if 'GroupName' in df.columns:
            ban_total = df.groupby('GroupName').agg({'LineQty': 'sum', 'CAISSE EQ': 'sum'}).reset_index().sort_values('CAISSE EQ', ascending=False)
            st.plotly_chart(px.pie(ban_total, values='CAISSE EQ', names='GroupName', hole=0.4, color_discrete_sequence=px.colors.sequential.Viridis), use_container_width=True)
            st.dataframe(ban_total, use_container_width=True)

        # --- 5. EXPORT EXCEL (AVEC NOUVEL ONGLET FOCUS) ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            format_bold = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
            
            # Onglet 1: Focus dernière semaine (Nouveauté demandée)
            week_sku.to_excel(writer, sheet_name='Derniere_Semaine', index=False)
            
            # Onglets standards
            sku_total.to_excel(writer, sheet_name='Produits', index=False)
            if 'GroupName' in df.columns: ban_total.to_excel(writer, sheet_name='Bannieres', index=False)
            
            # Pivot mensuel en Caisses EQ
            df.pivot_table(index='ItemName', columns=df['DocDate'].dt.to_period('M').astype(str), values='CAISSE EQ', aggfunc='sum', fill_value=0).to_excel(writer, sheet_name='Mensuel_EQ')

        st.sidebar.divider()
        st.sidebar.download_button("📥 Télécharger Rapport Excel Complet", output.getvalue(), "Rapport_Ventes_Alchimiste.xlsx")

except Exception as e:
    st.error(f"Erreur : {e}")
