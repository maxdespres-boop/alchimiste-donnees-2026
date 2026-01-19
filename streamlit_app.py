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
        
        # Lecture robuste (on traite les guillemets et les lignes brisées)
        df_temp = pd.read_csv(
            io.StringIO(content), 
            sep=',', 
            quotechar='"', 
            on_bad_lines='skip', 
            skip_blank_lines=True
        )
        df_list.append(df_temp)
            
    if not df_list: return None
    return pd.concat(df_list, ignore_index=True)

# --- TRAITEMENT DES DONNÉES ---
try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage et Conversion
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

        # --- BARRE LATÉRALE ---
        st.sidebar.header("📅 Filtres & Infos")
        min_date, max_date = df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()
        date_range = st.sidebar.date_input("Sélectionnez les dates", value=(min_date, max_date))

        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else:
            df = df_raw.copy()

        st.sidebar.divider()
        st.sidebar.write(f"**Diagnostic :**")
        st.sidebar.write(f"Lignes détectées : {len(df)}")
        st.sidebar.info("Note: L'app additionne les quantités réelles de chaque ligne.")

        st.title("📊 Rapport de Ventes Alchimiste")

        # --- 1. FOCUS DERNIÈRE SEMAINE ---
        latest_day = df_raw['DocDate'].max()
        start_of_last_week = latest_day - pd.Timedelta(days=6)
        df_latest_week = df_raw[df_raw['DocDate'] >= start_of_last_week].copy()

        with st.expander(f"🔔 FOCUS : Dernière semaine reçue (du {start_of_last_week.strftime('%Y-%m-%d')} au {latest_day.strftime('%Y-%m-%d')})", expanded=True):
            if not df_latest_week.empty:
                latest_sku = df_latest_week.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum', 'LineTotal': 'sum'}).reset_index().sort_values('LineQty', ascending=False)
                latest_sku['ItemName'] = latest_sku['ItemName'].replace(NOMS_COURTS)
                latest_sku.columns = ['Code', 'Produit', 'Caisses', 'Total ($)']
                st.table(latest_sku)
            else:
                st.write("Aucune donnée pour la dernière semaine.")

        st.divider()

        # --- 2. KPI GLOBAUX ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{df['LineQty'].sum():,.2f}")
        c2.metric("Nb Lignes de Ventes", len(df))
        c3.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c4.metric("Nb Factures (DocNum)", df['DocNum'].nunique())

        # --- 3. VENTES PAR PRODUIT (SKU) ---
        st.header("📦 Ventes par Produit (SKU)")
        sku_total = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        sku_total['ItemName'] = sku_total['ItemName'].replace(NOMS_COURTS)

        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', text_auto='.2f', color_continuous_scale='Viridis')
        fig.update_layout(xaxis_tickangle=-45, bargap=0.3) 
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 4. VENTES PAR CLIENT ---
        st.header("👥 Ventes par Client")
        if 'CardName' in df.columns:
            client_total = df.groupby('CardName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            client_total.columns = ['Nom du Client', 'Total Caisses']
            st.dataframe(client_total, use_container_width=True, hide_index=True)

        st.divider()

        # --- 5. GRILLES TEMPORELLES ---
        st.header("📅 Calendrier des Ventes")
        col_m, col_d = st.columns(2)
        
        with col_m:
            st.subheader("Ventes par Mois")
            df['Mois'] = df['DocDate'].dt.to_period('M').astype(str)
            pivot_month = df.pivot_table(index='ItemName', columns='Mois', values='LineQty', aggfunc='sum', fill_value=0)
            st.dataframe(pivot_month, use_container_width=True)
            
        with col_d:
            st.subheader("Détail Quotidien")
            pivot_day = df.pivot_table(index='ItemName', columns=df['DocDate'].dt.date, values='LineQty', aggfunc='sum', fill_value=0)
            st.dataframe(pivot_day, use_container_width=True)

        # --- 6. EXPORT EXCEL AVEC TOTAUX EN GRAS ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            format_bold = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
            
            def write_sheet_with_total(dataframe, sheet_name, has_index=False):
                dataframe.to_excel(writer, sheet_name=sheet_name, index=has_index)
                worksheet = writer.sheets[sheet_name]
                num_rows = len(dataframe)
                num_cols = len(dataframe.columns) + (1 if has_index else 0)
                
                worksheet.write(num_rows + 1, 0, "TOTAL GÉNÉRAL", format_bold)
                for col_num in range(1, num_cols):
                    col_data = dataframe.iloc[:, col_num - (1 if has_index else 0)]
                    if pd.api.types.is_numeric_dtype(col_data):
                        worksheet.write(num_rows + 1, col_num, col_data.sum(), format_bold)

            write_sheet_with_total(sku_total, 'Produits')
            if 'CardName' in df.columns:
                write_sheet_with_total(client_total, 'Clients')
            write_sheet_with_total(pivot_month, 'Mensuel', has_index=True)
            write_sheet_with_total(pivot_day, 'Quotidien', has_index=True)
        
        st.sidebar.download_button(label="📥 Télécharger Rapport Excel", data=output.getvalue(), 
                                   file_name="Rapport_Ventes_Alchimiste.xlsx", 
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
