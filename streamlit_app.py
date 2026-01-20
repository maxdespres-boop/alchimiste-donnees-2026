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
        df_temp = pd.read_csv(io.StringIO(content), sep=',', quotechar='"', on_bad_lines='skip', skip_blank_lines=True)
        df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

# --- LOGIQUE DE CONVERSION ET FUSION ---
def harmoniser_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]]) # Caisse EQ 24, SKU sans le 12
    return pd.Series([qty, code]) # Caisse EQ 24, SKU tel quel

try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        df_raw['LineQty'] = pd.to_numeric(df_raw['LineQty'], errors='coerce').fillna(0)
        df_raw['LineTotal'] = pd.to_numeric(df_raw['LineTotal'], errors='coerce').fillna(0)
        
        # Application conversion
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats, axis=1)
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Num'] = df_raw['DocDate'].dt.month

        st.title("📊 Dashboard Ventes Alchimiste")
        st.info("💡 Tous les volumes sont uniformisés en **Equivalent 24 canettes**.")

        # --- 1. FOCUS DERNIÈRE SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_week = latest_day - pd.Timedelta(days=6)
        df_latest = df_raw[df_raw['DocDate'] >= start_week].copy()
        with st.expander(f"🔔 FOCUS : Dernière semaine reçue (jusqu'au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            focus_table = df_latest.groupby(['SKU_BASE', 'ItemName']).agg({'LineQty': 'sum', 'CAISSE EQ': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('CAISSE EQ', ascending=False)
            st.table(focus_table.rename(columns={'LineQty': 'Qté Physique', 'CAISSE EQ': 'Caisses EQ 24', 'LineTotal': 'Total ($)'}))

        st.divider()

        # --- 2. ANALYSE COMPARATIVE (YoY) ---
        st.header("📈 Comparaison Annuelle (YoY)")
        yoy_pivot = df_raw.pivot_table(index='Mois_Num', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        
        col_graph, col_stats = st.columns([2, 1])
        with col_graph:
            if len(yoy_pivot.columns) >= 2:
                st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Num', y=yoy_pivot.columns, markers=True, title="Évolution du volume (Caisses EQ 24)"), use_container_width=True)
            else:
                st.write("Importez les données 2024 pour voir la comparaison.")
        
        with col_stats:
            st.metric("Total Caisses EQ (Période)", f"{df_raw['CAISSE EQ'].sum():,.1f}")
            st.metric("Ventes Totales ($)", f"{df_raw['LineTotal'].sum():,.2f} $")
            st.metric("Nombre de Lignes", len(df_raw))

        st.divider()

        # --- 3. BANNIÈRES ET CLIENTS ---
        col_pie, col_cli = st.columns(2)
        with col_pie:
            st.header("🏢 Bannières")
            if 'GroupName' in df_raw.columns:
                ban_data = df_raw.groupby('GroupName')['CAISSE EQ'].sum().reset_index()
                st.plotly_chart(px.pie(ban_data, values='CAISSE EQ', names='GroupName', hole=0.4, color_discrete_sequence=px.colors.sequential.Viridis), use_container_width=True)
        with col_cli:
            st.header("👥 Top 10 Clients")
            client_data = df_raw.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(10)
            st.dataframe(client_data, use_container_width=True, hide_index=True)

        st.divider()

        # --- 4. CALENDRIER DES VENTES ---
        st.header("📅 Calendrier des Ventes")
        c_m, c_d = st.columns(2)
        with c_m:
            st.subheader("Par Mois")
            st.dataframe(df_raw.pivot_table(index='ItemName', columns=df_raw['DocDate'].dt.to_period('M').astype(str), values='CAISSE EQ', aggfunc='sum', fill_value=0), use_container_width=True)
        with c_d:
            st.subheader("Détail Quotidien (Derniers 30 jours)")
            recent_days = df_raw[df_raw['DocDate'] > (latest_day - pd.Timedelta(days=30))]
            st.dataframe(recent_days.pivot_table(index='ItemName', columns=recent_days['DocDate'].dt.date, values='CAISSE EQ', aggfunc='sum', fill_value=0), use_container_width=True)

        # --- 5. EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            focus_table.to_excel(writer, sheet_name='Focus_Semaine', index=False)
            yoy_pivot.to_excel(writer, sheet_name='YoY_Mensuel')
            df_raw.groupby(['SKU_BASE', 'Année'])['CAISSE EQ'].sum().unstack().fillna(0).to_excel(writer, sheet_name='Performance_Produits')
        
        st.sidebar.download_button("📥 Télécharger Rapport Complet", output.getvalue(), "Rapport_Alchimiste_Final.xlsx")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
