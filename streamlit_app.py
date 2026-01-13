import streamlit as st
import pandas as pd
import plotly.express as px
from st_files_connection import FilesConnection

# Configuration de la page
st.set_page_config(page_title="Dashboard Ventes Alchimiste", layout="wide")

ID_DOSSIER = "1A2b3C4d5E6f7G8h9I0j_kLMnO_pQrStU"

conn = st.connection('gcs', type=FilesConnection)

# @st.cache_data(ttl=3600)
def load_data(folder_id):
    clean_id = folder_id.strip().split('/')[-1]
    try:
        files = conn.fs.ls(f"gdrive://{clean_id}/")
        st.success(f"Connexion réussie ! Fichiers trouvés : {len(files)}")
        # ... (reste du code pour lire les CSV) ...
    except Exception as e:
        st.error(f"Détail technique de l'erreur : {str(e)}")
        # Cela nous dira si c'est 'Permission Denied', 'Invalid Credentials' ou 'API not enabled'
        return None

# Lancement du chargement
df = load_data(ID_DOSSIER)

if df is not None:
    # --- PRÉPARATION DES DONNÉES ---
    df['DocDate'] = pd.to_datetime(df['DocDate'])
    
    st.title("📊 Analyse de ventes hebdomadaire")
    st.markdown("---")

    # --- SECTION 1 : INDICATEURS FINANCIERS ---
    total_caisses = df['LineQty'].sum()
    total_ventes = df['LineTotal'].sum()
    total_rabais = df['Rabais'].sum()
    # Formule : Rabais / (Ventes + Rabais)
    pct_rabais = (total_rabais / (total_ventes + total_rabais)) * 100 if (total_ventes + total_rabais) != 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Caisses", f"{total_caisses:,.0f}")
    col2.metric("Ventes Totales", f"{total_ventes:,.2f} $")
    col3.metric("Total Rabais", f"{total_rabais:,.2f} $")
    col4.metric("% Rabais", f"{pct_rabais:.2f} %")

    # --- SECTION 2 : VENTES PAR SKU (EN CAISSES) ---
    st.header("📦 Ventes par SKU")
    # Groupement par ItemCode pour la précision, affichage avec ItemName
    sku_data = df.groupby(['ItemCode', 'ItemName'])['LineQty'].sum().reset_index()
    sku_data = sku_data.sort_values('LineQty', ascending=False)
    
    fig_sku = px.bar(sku_data, x='ItemName', y='LineQty', 
                     title="Total des caisses par produit",
                     labels={'ItemName': 'Produit', 'LineQty': 'Caisses'},
                     text_auto='.2s')
    st.plotly_chart(fig_sku, use_container_width=True)
    st.dataframe(sku_data, use_container_width=True)

    # --- SECTION 3 : VENTES PAR SKU PAR JOUR ---
    st.header("📅 Détail Quotidien par SKU")
    sku_day = df.groupby(['DocDate', 'ItemCode', 'ItemName'])['LineQty'].sum().reset_index()
    # Pivot pour mettre les dates en colonnes
    sku_day_pivot = sku_day.pivot_table(index=['ItemCode', 'ItemName'], 
                                        columns=df['DocDate'].dt.strftime('%Y-%m-%d'), 
                                        values='LineQty', 
                                        aggfunc='sum', 
                                        fill_value=0)
    st.dataframe(sku_day_pivot, use_container_width=True)

    # --- SECTION 4 : BANNIÈRES ET RÉGIONS ---
    st.header("🏢 Analyse Segments")
    c_left, c_right = st.columns(2)
    
    with c_left:
        st.subheader("Par Bannière")
        banniere_df = df.groupby('GroupName')['LineQty'].sum().reset_index()
        fig_ban = px.pie(banniere_df, values='LineQty', names='GroupName', hole=0.3)
        st.plotly_chart(fig_ban, use_container_width=True)

    with c_right:
        st.subheader("Par Région (CityS)")
        region_df = df.groupby('CityS')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
        st.dataframe(region_df, use_container_width=True)

    # --- SECTION 5 : REPRÉSENTANTS ---
    st.header("👥 Performance des Représentants")
    rep_df = df.groupby('RefPartenaire')['LineQty'].sum().reset_index().sort_values('LineQty', ascending=False)
    fig_rep = px.bar(rep_df, x='RefPartenaire', y='LineQty', color='LineQty')
    st.plotly_chart(fig_rep, use_container_width=True)

else:
    st.warning("⚠️ En attente de données... Vérifiez que vos fichiers CSV sont bien dans le dossier Drive et que le dossier est partagé avec l'adresse du compte de service.")
