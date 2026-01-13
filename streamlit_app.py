import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Ventes Alchimiste", layout="wide")

# --- CONFIGURATION ---
ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

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
        
        # --- FILTRE DE DATE DANS LA SIDEBAR ---
        st.sidebar.header("📅 Filtres")
        min_date = df_raw['DocDate'].min().date()
        max_date = df_raw['DocDate'].max().date()
        
        date_range = st.sidebar.date_input(
            "Sélectionnez la période",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date
        )

        # Filtrage du dataframe selon la sélection
        if len(date_range) == 2:
            start_date, end_date = date_range
            mask = (df_raw['DocDate'].dt.date >= start_date) & (df_raw['DocDate'].dt.date <= end_date)
            df = df_raw.loc[mask]
        else:
            df = df_raw

        # --- 4. FOCUS FICHIER RÉCENT (Basé sur le brut pour toujours voir le dernier reçu) ---
        latest_date = df_raw['DocDate'].max()
        df_latest = df_raw[df_raw['DocDate'] == latest_date]

        st.title("📊 Rapport de Ventes Hebdomadaire")
        
        with st.expander(f"🔔 Ventes du dernier jour reçu ({latest_date.strftime('%Y-%m-%d')})", expanded=True):
            latest_sku = df_latest.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            st.table(latest_sku)

        st.divider()

        # --- KPI GLOBAUX (Sur période filtrée) ---
        total_caisses = df['LineQty'].sum()
        total_ventes = df['LineTotal'].sum()
        total_rabais = df['Rabais'].sum()
        pct_rabais = (total_rabais / (total_ventes + total_rabais) * 100) if (total_ventes + total_rabais) != 0 else 0

        st.subheader(f"📈 Résumé pour la période : {date_range[0]} au {date_range[1] if len(date_range)>1 else ''}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{total_caisses:,.0f}")
        c2.metric("Ventes Totales", f"{total_ventes:,.2f} $")
        c3.metric("Total Rabais", f"{total_rabais:,.2f} $")
        c4.metric("% Rabais", f"{pct_rabais:.2f} %")

        # --- 2. SKU (Graph + Tableau) ---
        st.header("📦 Ventes par SKU")
        sku_total = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            st.plotly_chart(px.bar(sku_total, x='ItemName', y='LineQty', text_auto=True, color='LineQty'), use_container_width=True)
        with col_table:
            st.dataframe(sku_total, use_container_width=True, hide_index=True)

        # --- 3. BANNIÈRES (Graph + Tableau) ---
        st.header("🏢 Ventes par Bannière")
        ban_total = df.groupby('GroupName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        col_pie, col_ban_table = st.columns([1, 1])
        with col_pie:
            st.plotly_chart(px.pie(ban_total, values='LineQty', names='GroupName', hole=0.3), use_container_width=True)
        with col_ban_table:
            st.dataframe(ban_total, use_container_width=True, hide_index=True)

        # --- RÉGIONS & REPRÉSENTANTS ---
        st.header("📍 Géographie et Équipe")
        c_reg, c_rep = st.columns(2)
        with c_reg:
            st.subheader("Par Ville / Région")
            reg_df = df.groupby('CityS')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            st.dataframe(reg_df, use_container_width=True, hide_index=True)
        with c_rep:
            st.subheader("Par Représentant")
            rep_df = df.groupby('RefPartenaire')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
            st.dataframe(rep_df, use_container_width=True, hide_index=True)

        # --- VENTES PAR JOUR PAR SKU ---
        st.header("📅 Détail Quotidien")
        pivot_day = df.pivot_table(index=['ItemCode', 'ItemName'], columns=df['DocDate'].dt.strftime('%Y-%m-%d'), values='LineQty', aggfunc='sum', fill_value=0)
        st.dataframe(pivot_day, use_container_width=True)

        # --- 1. EXPORT EXCEL ---
        st.sidebar.divider()
        st.sidebar.subheader("💾 Exportation")
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            sku_total.to_excel(writer, sheet_name='Ventes_par_SKU', index=False)
            ban_total.to_excel(writer, sheet_name='Par_Banniere', index=False)
            pivot_day.to_excel(writer, sheet_name='Detail_Quotidien')
            summary_data = pd.DataFrame({
                "Indicateur": ["Date Début", "Date Fin", "Total Caisses", "Ventes $", "Rabais $", "% Rabais"],
                "Valeur": [str(date_range[0]), str(date_range[1] if len(date_range)>1 else ''), total_caisses, total_ventes, total_rabais, f"{pct_rabais:.2f}%"]
            })
            summary_data.to_excel(writer, sheet_name='Resume_Financier', index=False)

        st.sidebar.download_button(
            label="📥 Télécharger Rapport Excel",
            data=output.getvalue(),
            file_name=f"Rapport_Ventes_{date_range[0]}_au_{date_range[1] if len(date_range)>1 else ''}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

except Exception as e:
    st.error(f"Erreur : {e}")
