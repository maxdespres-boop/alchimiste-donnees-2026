import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Dashboard Ventes Pro", layout="wide")

# --- CONFIGURATION ---
ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

# --- AUTHENTIFICATION ---
def get_gdrive_service():
    # Récupération des secrets au format TOML
    creds_dict = st.secrets["connections"]["gcs"]
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return build('drive', 'v3', credentials=creds)

@st.cache_data(ttl=3600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    
    # 1. Lister les fichiers dans le dossier
    query = f"'{folder_id}' in parents and name contains '.csv' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])

    if not items:
        return None

    df_list = []
    for item in items:
        # 2. Télécharger le contenu du fichier
        request = service.files().get_media(fileId=item['id'])
        fh = io.BytesIO(request.execute())
        
        # 3. Lire le CSV (on garde vos paramètres : virgule et latin1)
        df_temp = pd.read_csv(fh, sep=',', encoding='latin1')
        df_list.append(df_temp)

    return pd.concat(df_list, ignore_index=True)

# --- CORPS DE L'APPLICATION ---
try:
    df = load_data_from_drive(ID_DOSSIER)

    if df is not None:
        df['DocDate'] = pd.to_datetime(df['DocDate'])
        
        st.title("📊 Rapport de Ventes Alchimiste")
        
        # --- KPI ---
        total_caisses = df['LineQty'].sum()
        total_ventes = df['LineTotal'].sum()
        total_rabais = df['Rabais'].sum()
        denominateur = total_ventes + total_rabais
        pct_rabais = (total_rabais / denominateur * 100) if denominateur != 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Caisses", f"{total_caisses:,.0f}")
        c2.metric("Ventes ($)", f"{total_ventes:,.2f} $")
        c3.metric("Total Rabais ($)", f"{total_rabais:,.2f} $")
        c4.metric("% Rabais", f"{pct_rabais:.2f} %")

        # --- GRAPHIQUE SKU ---
        st.header("📦 Ventes par SKU")
        sku_data = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        st.plotly_chart(px.bar(sku_data, x='ItemName', y='LineQty', text_auto=True), use_container_width=True)

        # --- SKU PAR JOUR ---
        st.header("📅 Ventes par SKU et par Jour")
        sku_day_pivot = df.pivot_table(index=['ItemCode', 'ItemName'], 
                                        columns=df['DocDate'].dt.strftime('%Y-%m-%d'), 
                                        values='LineQty', 
                                        aggfunc='sum', 
                                        fill_value=0)
        st.dataframe(sku_day_pivot, use_container_width=True)

        # --- RESTE DES ANALYSES ---
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Par Bannière")
            st.plotly_chart(px.pie(df, values='LineQty', names='GroupName'), use_container_width=True)
        with col_b:
            st.subheader("Par Région")
            st.dataframe(df.groupby('CityS')['LineQty'].sum().sort_values(ascending=False))

    else:
        st.warning("Aucun fichier CSV trouvé. Vérifiez l'ID du dossier et le partage avec le compte de service.")

except Exception as e:
    st.error(f"Erreur d'accès : {e}")
    st.info("Vérifiez que l'API Google Drive est bien activée dans la console Google Cloud.")
