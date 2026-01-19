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
        content = request.execute()
        
        # --- NOUVELLE STRATÉGIE DE LECTURE ---
        # On lit le fichier d'abord comme du texte pur pour nettoyer les problèmes
        data = io.StringIO(content.decode('latin1'))
        
        try:
            df_temp = pd.read_csv(
                data,
                sep=',',
                quotechar='"',           # Gère les virgules dans les adresses
                doublequote=True,        # Gère les guillemets doubles
                on_bad_lines='warn',     # Nous avertira s'il y a encore un souci
                engine='python',         # Moteur plus flexible pour les fichiers mal formés
                skip_blank_lines=True
            )
            df_list.append(df_temp)
        except Exception as e:
            st.error(f"Erreur sur le fichier {item['name']}: {e}")
            
    if not df_list: return None
    return pd.concat(df_list, ignore_index=True)

# --- LOGIQUE D'AFFICHAGE ---
try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        # Nettoyage des colonnes numériques (parfois lues comme du texte à cause des virgules)
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns and df_raw[col].dtype == 'object':
                df_raw[col] = pd.to_numeric(df_raw[col].str.replace(',', '.'), errors='coerce')

        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'])
        
        # Filtre sidebar
        st.sidebar.header("📅 Période")
        min_date, max_date = df_raw['DocDate'].min().date(), df_raw['DocDate'].max().date()
        date_range = st.sidebar.date_input("Dates", value=(min_date, max_date))

        if isinstance(date_range, tuple) and len(date_range) == 2:
            df = df_raw[(df_raw['DocDate'].dt.date >= date_range[0]) & (df_raw['DocDate'].dt.date <= date_range[1])].copy()
        else:
            df = df_raw.copy()

        # AFFICHAGE DU DÉCOMPTE POUR VÉRIFICATION
        st.sidebar.write(f"**Lignes totales :** {len(df)}")

        # 1. KPI
        st.title("📊 Rapport de Ventes Alchimiste")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Caisses", f"{df['LineQty'].sum():,.2f}")
        c2.metric("Ventes ($)", f"{df['LineTotal'].sum():,.2f} $")
        c3.metric("Nb Factures", df['DocNum'].nunique())

        # 2. GRAPHIQUE SKUS (STYLE VERT/BLEU)
        st.header("📦 Ventes par Produit")
        sku_total = df.groupby('ItemName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        fig = px.bar(sku_total, x='ItemName', y='LineQty', color='LineQty', 
                     color_continuous_scale='Viridis', text_auto='.2f')
        fig.update_layout(bargap=0.3)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_total, use_container_width=True)

        # 3. CLIENTS
        st.header("👥 Top Clients")
        client_total = df.groupby('CardName')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        st.dataframe(client_total, use_container_width=True)

        # 4. GRILLE MENSUELLE ET QUOTIDIENNE
        st.header("📅 Calendrier des Ventes")
        df['Mois'] = df['DocDate'].dt.to_period('M').astype(str)
        st.subheader("Par Mois")
        st.dataframe(df.pivot_table(index='ItemName', columns='Mois', values='LineQty', aggfunc='sum', fill_value=0))
        
        st.subheader("Détail Quotidien")
        st.dataframe(df.pivot_table(index='ItemName', columns=df['DocDate'].dt.date, values='LineQty', aggfunc='sum', fill_value=0))

except Exception as e:
    st.error(f"Erreur globale : {e}")
