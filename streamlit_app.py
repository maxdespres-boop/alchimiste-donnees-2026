import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Ventes Alchimiste", layout="wide")

ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

NOMS_COURTS = {
    'La Blonde sans alcool': 'BLO Sans Alcool',
    'La Blanche sans alcool': 'BLA Sans Alcool'
}

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return build('drive', 'v3', credentials=creds)

@st.cache_data(ttl=600) # Rafraîchissement plus fréquent
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and name contains '.csv' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])
    if not items: return None
    
    df_list = []
    for item in items:
        request = service.files().get_media(fileId=item['id'])
        content = request.execute().decode('latin1')
        
        # Nettoyage manuel des retours à la ligne à l'intérieur des guillemets
        # avant de transformer en DataFrame
        f = io.StringIO(content)
        df_temp = pd.read_csv(f, sep=',', skip_blank_lines=True, on_bad_lines='skip')
        df_list.append(df_temp)
            
    if not df_list: return None
    return pd.concat(df_list, ignore_index=True)

# --- LOGIQUE D'AFFICHAGE ---
try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage des colonnes
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate']) # On enlève les lignes cassées qui n'ont pas de date
        
        # Conversion numérique forcée
        for col in ['LineQty', 'LineTotal']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

        # --- BARRE LATÉRALE (DIAGNOSTIC) ---
        st.sidebar.header("🛠 Diagnostic")
        st.sidebar.write(f"Fichiers lus : {len(df_raw['RefPartenaire'].unique()) if 'RefPartenaire' in df_raw.columns else '?'}")
        st.sidebar.write(f"Lignes totales : {len(df_raw)}")
        
        min_date, max_date = df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()
        date_range = st.sidebar.date_input("Filtrer par dates", value=(min_date, max_date))

        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else:
            df = df_raw.copy()

        st.title("📊 Rapport de Ventes Alchimiste")

        # --- 1. RESTAURATION : FOCUS DERNIÈRE SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_of_last_week = latest_day - pd.Timedelta(days=6)
        df_latest_week = df_raw[df_raw['DocDate'] >= start_of_last_week].copy()

        with st.expander(f"🔔 FOCUS : Dernière semaine reçue (du {start_of_last_week.strftime('%Y-%m-%d')} au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            if not df_latest_week.empty:
                latest_sku = df_latest_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('LineQty', ascending=False)
                latest_sku.columns = ['Code', 'Produit', 'Caisses', 'Total ($)']
                st.table(latest_sku)
            else:
                st.write("Aucune donnée pour la dernière semaine.")

        st.divider()

        # --- 2. KPI ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{df['LineQty'].sum():,.2f}")
        c2.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c3.metric("Nb Factures", df['DocNum'].nunique())
        c4.metric("Nb Clients", df['CardCode'].nunique() if 'CardCode' in df.columns else 'N/A')

        # --- 3. GRAPHIQUE ET TABLEAU SKU ---
        st.header("📦 Ventes par Produit")
        sku_total = df.groupby('ItemName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', color_continuous_scale='Viridis', text_auto='.2f')
        fig.update_layout(bargap=0.3, xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 4. CALENDRIER ---
        st.header("📅 Calendrier des Ventes")
        col_month, col_day = st.columns(2)
        
        with col_month:
            st.subheader("Par Mois")
            df['Mois'] = df['DocDate'].dt.to_period('M').astype(str)
            pivot_m = df.pivot_table(index='ItemName', columns='Mois', values='LineQty', aggfunc='sum', fill_value=0)
            st.dataframe(pivot_m, use_container_width=True)
            
        with col_day:
            st.subheader("Détail Quotidien")
            pivot_d = df.pivot_table(index='ItemName', columns=df['DocDate'].dt.date, values='LineQty', aggfunc='sum', fill_value=0)
            st.dataframe(pivot_d, use_container_width=True)

except Exception as e:
    st.error(f"Une erreur est survenue lors du chargement : {e}")
