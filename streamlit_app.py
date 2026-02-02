import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP", layout="wide")

# --- CONFIGURATION DES DOSSIERS ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb"

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx') and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: return None
    
    df_list = []
    for item in items:
        try:
            request = service.files().get_media(fileId=item['id'])
            content = request.execute()
            if item['name'].lower().endswith('.xlsx'):
                df_temp = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                df_temp = pd.read_csv(io.StringIO(content.decode('latin1')), sep=',', quotechar='"', on_bad_lines='skip')
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ET PRÉPARATION ---
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # 1. Nettoyage et Dates (Crucial pour la suite)
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw = df_raw_all.dropna(subset=['DocDate']).copy()
    
    for col in ['LineQty', 'LineTotal', 'Rabais']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DocDate'].dt.year
    df_raw['Mois_Nom'] = df_raw['DocDate'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DocDate'].dt.dayofyear

    # 2. Logiques de conversion corrigées
    def harmoniser_alc(row):
        code = str(row['ItemCode']).strip()
        qty = row['LineQty']
        # FIX : On ne divise par 2 que pour 2026 (SAP). 2025 est déjà correct.
        if row['Année'] >= 2026 and code.endswith('12'):
            return pd.Series([qty * 0.5, code[:-2]])
        return pd.Series([qty, code])

    def harmoniser_loop(row):
        code = str(row['ItemCode']).strip()
        qty = row['LineQty']
        if code.endswith('12'): return pd.Series([qty, code[:-2]])
        return pd.Series([qty, code])

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_alc, axis=1)
        label_unit = "Eq. 24"
    else:
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_loop, axis=1)
        label_unit = "Caisses (12)"

    # --- SECTION ALCHIMISTE ---
    if page == "Alchimiste":
        df_alc = df_raw[df_raw['Année'] >= 2025].copy()
        
        # Filtrage YTD pour les KPI et l'Excel financier
        df_2026 = df_alc[df_alc['Année'] == 2026]
        max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
        df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
        
        st.title("📊 Rapport Alchimiste (Stabilisé)")

        # 1. KPI avec YOY en Volume et en Dollars
        k1, k2, k3, k4 = st.columns(4)
        vol_26 = df_2026['CAISSE EQ'].sum()
        vol_25 = df_2025_ytd['CAISSE EQ'].sum()
        val_26 = df_2026['LineTotal'].sum()
        val_25 = df_2025_ytd['LineTotal'].sum()

        k1.metric("Vol. 2026 (YTD)", f"{vol_26:,.1f} {label_unit}")
        k2.metric("Ventes 2026 (YTD)", f"{val_26:,.0f} $")
        k3.metric("YOY Volume", f"{vol_25:,.1f}", delta=f"{vol_26 - vol_25:,.1f}")
        k4.metric("YOY Dollars", f"{val_25:,.0f} $", delta=f"{val_26 - val_25:,.0f} $")

        # 2. Graphiques
        st.header("📈 Comparaison Mensuelle")
        # Graphique de Volume (Toute l'année 2025 vs 2026 actuel)
        yoy_vol = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        st.plotly_chart(px.line(yoy_vol.reset_index(), x='Mois_Nom', y=yoy_vol.columns, markers=True, title="Volume par Mois"), use_container_width=True)

        # 3. Focus Semaine et Produits
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🏢 Top Bannières (2026)")
            st.plotly_chart(px.pie(df_2026.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.3), use_container_width=True)
        with c2:
            st.subheader("👥 Top Clients (2026)")
            st.dataframe(df_2026.groupby('CardName')['CAISSE EQ'].sum().sort_values(ascending=False).head(10), use_container_width=True)

        # 4. EXPORT EXCEL (Le balancement est ici)
        # On crée un DataFrame spécifique YTD pour que l'Excel financier matche l'App
        df_fin_match = pd.concat([df_2026, df_2025_ytd])
        excel_val_ytd = df_fin_match.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            excel_val_ytd.to_excel(writer, sheet_name='Finance_YTD_MATCH')
            yoy_vol.to_excel(writer, sheet_name='Volume_Mensuel_Global')
            df_2026.groupby('ItemName').agg({'CAISSE EQ':'sum', 'LineTotal':'sum'}).to_excel(writer, sheet_name='Details_Produits_2026')
        
        st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_Alchimiste_{date.today()}.xlsx")

    # --- SECTION LOOP ---
    elif page == "LOOP":
        st.title("🍹 Rapport LOOP")
        df_loop = df_raw[df_raw['Année'] >= 2025].copy()
        mensuel_loop = df_loop.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        st.plotly_chart(px.bar(mensuel_loop.reset_index(), x='Mois_Nom', y=mensuel_loop.columns, barmode='group'), use_container_width=True)
        st.dataframe(df_loop.groupby('ItemName')['CAISSE EQ'].sum().sort_values(ascending=False), use_container_width=True)

else:
    st.error("Impossible de charger les données. Vérifiez la connexion Drive.")
