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

@st.cache_data(ttl=600)
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
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

        # --- FILTRES ---
        st.sidebar.header("📅 Filtres & Infos")
        min_date, max_date = df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()
        date_range = st.sidebar.date_input("Période", value=(min_date, max_date))

        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else:
            df = df_raw.copy()

        st.sidebar.write(f"**Diagnostic :** {len(df)} lignes lues.")

        st.title("📊 Rapport de Ventes Alchimiste")

        # --- 1. FOCUS SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_of_last_week = latest_day - pd.Timedelta(days=6)
        df_latest_week = df_raw[df_raw['DocDate'] >= start_of_last_week].copy()
        with st.expander(f"🔔 FOCUS : Dernière semaine reçue", expanded=True):
            if not df_latest_week.empty:
                latest_sku = df_latest_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('LineQty', ascending=False)
                latest_sku.columns = ['Code', 'Produit', 'Caisses', 'Total ($)']
                st.table(latest_sku)

        st.divider()

        # --- 2. KPI ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{df['LineQty'].sum():,.2f}")
        c2.metric("Lignes de Ventes", len(df))
        c3.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c4.metric("Nb Factures", df['DocNum'].nunique())

        # --- 3. VENTES PAR PRODUIT ---
        st.header("📦 Ventes par Produit (SKU)")
        sku_total = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', text_auto='.2f', color_continuous_scale='Viridis')
        fig.update_layout(xaxis_tickangle=-45, bargap=0.3) 
        st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # --- 4. BANNIÈRES (RESTAURÉ) ---
        st.header("🏢 Ventes par Bannière")
        col_pie, col_table_ban = st.columns([1, 1])
        if 'GroupName' in df.columns:
            ban_total = df.groupby('GroupName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            with col_pie:
                st.plotly_chart(px.pie(ban_total, values='LineQty', names='GroupName', hole=0.4, color_discrete_sequence=px.colors.sequential.Viridis), use_container_width=True)
            with col_table_ban:
                st.write("###") 
                st.dataframe(ban_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 5. CLIENTS ---
        st.header("👥 Ventes par Client")
        if 'CardName' in df.columns:
            client_total = df.groupby('CardName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            st.dataframe(client_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 6. CALENDRIER ---
        st.header("📅 Calendrier des Ventes")
        df['Mois'] = df['DocDate'].dt.to_period('M').astype(str)
        c_m, c_d = st.columns(2)
        with c_m:
            st.subheader("Par Mois")
            st.dataframe(df.pivot_table(index='ItemName', columns='Mois', values='LineQty', aggfunc='sum', fill_value=0), use_container_width=True)
        with c_d:
            st.subheader("Détail Quotidien")
            st.dataframe(df.pivot_table(index='ItemName', columns=df['DocDate'].dt.date, values='LineQty', aggfunc='sum', fill_value=0), use_container_width=True)

        # --- 7. EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            format_bold = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
            def write_sheet(dataframe, sheet_name):
                dataframe.to_excel(writer, sheet_name=sheet_name, index=True)
                ws = writer.sheets[sheet_name]
                ws.write(len(dataframe)+1, 0, "TOTAL", format_bold)
                # Somme simplifiée pour l'export
            sku_total.to_excel(writer, sheet_name='Produits')
            if 'GroupName' in df.columns: ban_total.to_excel(writer, sheet_name='Bannieres')
        
        st.sidebar.download_button("📥 Télécharger Excel", output.getvalue(), "Rapport_Alchimiste.xlsx")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
