import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Ventes Alchimiste", layout="wide")

# --- CONFIGURATION ---
ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

NOMS_COURTS = {
    'La Blonde sans alcool': 'BLO Sans Alcool',
    'La Blanche sans alcool': 'BLA Sans Alcool'
}

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return build('drive', 'v3', credentials=creds)

@st.cache_data(ttl=3600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and name contains '.csv' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])
    if not items: return None
    df_list = []
    for item in items:
        request = service.files().get_media(fileId=item['id'])
        fh = io.BytesIO(request.execute())
        df_temp = pd.read_csv(fh, sep=',', encoding='latin1')
        df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

# --- CHARGEMENT ---
try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'])
        
        # --- FILTRE DE DATE ---
        st.sidebar.header("📅 Période d'analyse")
        min_date = df_raw['DocDate'].min().date()
        max_date = df_raw['DocDate'].max().date()
        date_range = st.sidebar.date_input("Sélectionnez les dates", value=(min_date, max_date), min_value=min_date, max_value=max_date)

        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
            mask = (df_raw['DocDate'].dt.date >= start_date) & (df_raw['DocDate'].dt.date <= end_date)
            df = df_raw.loc[mask].copy()
        else:
            df = df_raw.copy()

        # --- 1. TITRE ET FOCUS SEMAINE ---
        st.title("📊 Rapport de Ventes Alchimiste")
        latest_day = df_raw['DocDate'].max()
        start_of_last_week = latest_day - pd.Timedelta(days=6)
        df_latest_week = df_raw[df_raw['DocDate'] >= start_of_last_week].copy()

        with st.expander(f"🔔 FOCUS : Dernière semaine reçue (du {start_of_last_week.strftime('%Y-%m-%d')} au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            latest_sku = df_latest_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('LineQty', ascending=False)
            latest_sku['ItemName'] = latest_sku['ItemName'].replace(NOMS_COURTS)
            latest_sku.columns = ['Code', 'Produit', 'Caisses', 'Total ($)']
            st.table(latest_sku)

        st.divider()

        # --- 2. KPI GLOBAUX ---
        total_caisses = df['LineQty'].sum()
        total_ventes = df['LineTotal'].sum()
        total_rabais = df['Rabais'].sum()
        pct_rabais = (total_rabais / (total_ventes + total_rabais) * 100) if (total_ventes + total_rabais) != 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{total_caisses:,.0f}")
        c2.metric("Ventes ($)", f"{total_ventes:,.2f} $")
        c3.metric("Total Rabais ($)", f"{total_rabais:,.2f} $")
        c4.metric("% Rabais", f"{pct_rabais:.2f} %")

        # --- 3. VENTES PAR PRODUIT ---
        st.header("📦 Ventes par Produit (SKU)")
        sku_total = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        sku_total['ItemName'] = sku_total['ItemName'].replace(NOMS_COURTS)

        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', 
                     text_auto=True, color_continuous_scale='Viridis')
        fig.update_layout(xaxis_tickangle=-45, bargap=0.3) 
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 4. VENTES PAR CLIENT ---
        st.header("👥 Ventes par Client")
        if 'CardCode' in df.columns and 'CardName' in df.columns:
            client_total = df.groupby(['CardCode', 'CardName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            client_total.columns = ['Code Client', 'Nom du Client', 'Total Caisses']
            st.dataframe(client_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 5. ANALYSE BANNIÈRES (Graphique à gauche, Tableau à droite) ---
        st.header("🏢 Ventes par Bannière")
        col_pie, col_table_ban = st.columns([1, 1])
        ban_total = df.groupby('GroupName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        
        with col_pie:
            st.plotly_chart(px.pie(ban_total, values='LineQty', names='GroupName', hole=0.4, 
                                  color_discrete_sequence=px.colors.sequential.Viridis), use_container_width=True)
        
        with col_table_ban:
            st.write("###") # Petit espace pour aligner
            st.dataframe(ban_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 6. GRILLE QUOTIDIENNE (Plein écran en bas) ---
        st.header("📅 Détail Quotidien")
        pivot_day = df.pivot_table(index='ItemName', columns=df['DocDate'].dt.strftime('%Y-%m-%d'), 
                                   values='LineQty', aggfunc='sum', fill_value=0)
        st.dataframe(pivot_day, use_container_width=True)

        # --- EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            sku_total.to_excel(writer, sheet_name='Produits', index=False)
            if 'CardCode' in df.columns: client_total.to_excel(writer, sheet_name='Clients', index=False)
            ban_total.to_excel(writer, sheet_name='Bannieres', index=False)
            pivot_day.to_excel(writer, sheet_name='Quotidien')
        
        st.sidebar.divider()
        st.sidebar.download_button(label="📥 Télécharger Rapport Excel", data=output.getvalue(), 
                                   file_name="Rapport_Ventes_Alchimiste.xlsx", 
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
