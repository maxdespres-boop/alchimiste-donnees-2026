import streamlit as st
from st_files_connection import FilesConnection
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Dashboard Ventes Hebdo", layout="wide")

# Connexion sécurisée au Google Drive
conn = st.connection('gcs', type=FilesConnection)

# Remplacez par le chemin de votre dossier Drive
# Format: "gdrive://[FOLDER_ID]/*.CSV"
folder_id = "VOTRE_ID_DE_DOSSIER_ICI"

@st.cache_data(ttl=3600) # Rafraîchit les données toutes les heures
def load_data():
    files = conn.fs.ls(f"{folder_id}")
    files = [f for f in files if f.upper().endswith(".CSV")]    
    df_list = []
    for file in files:
        with conn.fs.open(file, 'rb') as f:
            # On utilise le séparateur ',' tel que vu dans votre fichier F001005.CSV
            df_temp = pd.read_csv(f, sep=',', encoding='latin1')
            df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

try:
    df = load_data()
    df['DocDate'] = pd.to_datetime(df['DocDate'])

    st.title("📊 Compilation des Ventes Hebdomadaires")

    # --- CALCULS ---
    total_caisses = df['LineQty'].sum()
    total_ventes = df['LineTotal'].sum()
    total_rabais = df['Rabais'].sum()
    # Votre formule : Rabais / (Ventes + Rabais)
    ratio_rabais = (total_rabais / (total_ventes + total_rabais)) * 100 if (total_ventes + total_rabais) != 0 else 0

    # Affichage des indicateurs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Caisses", f"{total_caisses:,.0f}")
    c2.metric("Ventes ($)", f"{total_ventes:,.2f} $")
    c3.metric("Total Rabais ($)", f"{total_rabais:,.2f} $")
    c4.metric("% Rabais", f"{ratio_rabais:.2f} %")

    # --- 1. TABLEAU VENTES PAR SKU (ITEMCODE) ---
    st.subheader("📦 Ventes par SKU (Caisses)")
    sku_compil = df.groupby(['ItemCode', 'ItemName']).agg({'LineQty': 'sum'}).reset_index()
    sku_compil = sku_compil.sort_values('LineQty', ascending=False)
    
    fig_sku = px.bar(sku_compil, x='ItemName', y='LineQty', text_auto=True, title="Volume par produit")
    st.plotly_chart(fig_sku, use_container_width=True)

    # --- 2. VENTES PAR SKU PAR JOUR ---
    st.subheader("📅 Détail Quotidien par SKU")
    sku_jour = df.groupby(['DocDate', 'ItemCode', 'ItemName'])['LineQty'].sum().reset_index()
    pivot_jour = sku_jour.pivot_table(index=['ItemCode', 'ItemName'], columns='DocDate', values='LineQty', fill_value=0)
    st.dataframe(pivot_jour)

    # --- 3. ANALYSE BANNIÈRES / RÉGIONS / REP ---
    colA, colB, colC = st.columns(3)
    
    with colA:
        st.write("**Par Bannière**")
        ban_df = df.groupby('GroupName')['LineQty'].sum().reset_index()
        st.plotly_chart(px.pie(ban_df, values='LineQty', names='GroupName'), use_container_width=True)
        
    with colB:
        st.write("**Par Région (CityS)**")
        reg_df = df.groupby('CityS')['LineQty'].sum().sort_values(ascending=False)
        st.table(reg_df)

    with colC:
        st.write("**Par Représentant**")
        rep_df = df.groupby('RefPartenaire')['LineQty'].sum().sort_values(ascending=False)
        st.bar_chart(rep_df)

except Exception as e:
    st.error(f"En attente de données ou erreur de connexion : {e}")
