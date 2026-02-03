import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb" 

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
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
                raw_str = content.decode('latin1')
                df_temp = pd.read_csv(io.StringIO(raw_str), sep=None, engine='python', on_bad_lines='skip')
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE SUBDIVISION (SUPER SIMPLE) ---
def clean_banniere(row):
    # On convertit tout en texte pour éviter les erreurs
    g = str(row.get('GroupName', '')).upper()
    c = str(row.get('CardName', '')).upper()
    
    # Test large : Si le mot METRO (avec ou sans accent) est présent
    if "METRO" in g or "MÉTRO" in g:
        if "SUPER C" in c or "SUPERC" in c:
            return "SUPER C"
        return "METRO"
    return g

# --- MAIN APP ---
st.sidebar.title("🍺 Contrôles Dashboard")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    st.sidebar.success("✅ Données chargées avec succès")
    
    # Nettoyage et conversion des colonnes numériques
    for col in ['LineQty', 'LineTotal']:
        if col in df_raw_all.columns:
            df_raw_all[col] = pd.to_numeric(df_raw_all[col].astype(str).str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

    # Préparation des dates
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    
    # Création des colonnes de base
    df_raw_all['Année'] = df_raw_all['DateAnalyse'].dt.year
    df_raw_all['Jour_Annee'] = df_raw_all['DateAnalyse'].dt.dayofyear
    df_raw_all['Mois_Nom'] = df_raw_all['DateAnalyse'].dt.strftime('%m - %B')

    # APPLICATION DE LA SUBDIVISION IMMÉDIATEMENT
    df_raw_all['Banniere_Clean'] = df_raw_all.apply(clean_banniere, axis=1)

    # --- SÉLECTEUR DE DATE ---
    ytd_start = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (ytd_start, date.today())
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    # Filtrage par date
    df_filtered = df_raw_all.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw_all[(df_raw_all['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw_all['DateAnalyse'].dt.date <= date_sel[1])]

    # --- AFFICHAGE ---
    st.title(f"📊 Dashboard {page}")
    
    tab1, tab2 = st.tabs(["📉 Performance", "🏢 Bannières"])
    
    with tab1:
        st.subheader("Volume par Mois (Base 12)")
        # (Logique de calcul Volume 12 ici simplifiée pour le test)
        st.info("Sélectionnez l'onglet Bannières pour voir la subdivision.")

    with tab2:
        st.header("🏢 Répartition par Enseigne")
        if 'Banniere_Clean' in df_filtered.columns:
            # On groupe par la nouvelle colonne
            data_ban = df_filtered.groupby('Banniere_Clean')['LineTotal'].sum().reset_index().sort_values('LineTotal', ascending=False)
            
            c1, c2 = st.columns([2, 1])
            with c1:
                fig = px.pie(data_ban.head(10), values='LineTotal', names='Banniere_Clean', hole=0.4, title="Ventes par Bannière ($)")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                st.write("Détails des ventes :")
                st.dataframe(data_ban, hide_index=True)
        else:
            st.error("La colonne 'Banniere_Clean' n'a pas pu être créée.")

else:
    st.error("❌ Erreur de connexion au Drive. Vérifiez vos secrets Streamlit.")
