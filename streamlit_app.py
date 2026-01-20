import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Multi-Marques", layout="wide")

# --- CONFIGURATION DES DOSSIERS (À METTRE À JOUR) ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD" # Remplacez si l'ID change
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

def harmoniser_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]])
    return pd.Series([qty, code])

# --- NAVIGATION ---
st.sidebar.title("🍺 Sélection de Marque")
page = st.sidebar.radio("Aller vers :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT DES DONNÉES SELON LA PAGE ---
current_folder_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_folder_id)

try:
    if df_raw_all is not None:
        # Nettoyage commun
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw = df_raw_all.dropna(subset=['DocDate']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
            else:
                df_raw[col] = 0
        
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats, axis=1)
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Nom'] = df_raw['DocDate'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DocDate'].dt.dayofyear

        # --- LOGIQUE PAGE ALCHIMISTE ---
        if page == "Alchimiste":
            # (On garde votre logique exacte 2025-2026 ici)
            df_raw = df_raw[df_raw['Année'] >= 2025]
            
            st.sidebar.header("⚙️ Contrôles Alchimiste")
            start_ytd = date(2026, 1, 1)
            def reset_ytd(): st.session_state["date_alc"] = (start_ytd, date.today())
            if "date_alc" not in st.session_state: reset_ytd()
            st.sidebar.button("🔄 Reset YTD 2026", on_click=reset_ytd)
            date_sel = st.sidebar.date_input("Filtrer", value=st.session_state["date_alc"], key="date_alc")
            
            # Filtrage
            df_detail = df_raw[(df_raw['DocDate'].dt.date >= date_sel[0]) & (df_raw['DocDate'].dt.date <= date_sel[1])] if isinstance(date_sel, tuple) else df_raw

            st.title("📊 Rapport Alchimiste (2025-2026)")
            
            # KPI et Tableaux Alchimiste (Identique à votre code actuel)
            df_2026 = df_raw[df_raw['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_eq_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= (df_2026['Jour_Annee'].max() if not df_2026.empty else 366))]['CAISSE EQ'].sum()
            
            k1, k2, k3 = st.columns(3)
            k1.metric("EQ 2026", f"{total_eq_2026:,.1f}")
            k2.metric("EQ 2025 YTD", f"{total_eq_2025_ytd:,.1f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.1f}")
            k3.metric("% Rabais", f"{(df_2026['Rabais'].sum() / (df_2026['LineTotal'].sum() + df_2026['Rabais'].sum()) * 100):.2f}%" if (df_2026['LineTotal'].sum() + df_2026['Rabais'].sum()) != 0 else "0%")

            st.header("📈 Comparaison Mensuelle")
            yoy_piv = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
            st.plotly_chart(px.line(yoy_piv.reset_index(), x='Mois_Nom', y=yoy_piv.columns, markers=True), use_container_width=True)

            st.header("👥 Top 15 Clients")
            st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15), use_container_width=True, hide_index=True)

        # --- LOGIQUE PAGE LOOP ---
        elif page == "LOOP":
            st.title("🍹 Rapport de Ventes : LOOP")
            
            # Pas de filtre complexe ici, on affiche tout ce qui est dans le dossier LOOP
            st.subheader("📦 Ventes par SKU (Total)")
            sku_loop = df_raw.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':'Eq. 24', 'LineTotal':'Total ($)'}), use_container_width=True)

            st.subheader("📅 Ventes par Mois (Eq. 24)")
            mensuel_loop = df_raw.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
            st.dataframe(mensuel_loop, use_container_width=True)
            
            # Petit graphique de tendance pour LOOP
            tendance_loop = df_raw.groupby('Mois_Nom')['CAISSE EQ'].sum().reset_index()
            st.plotly_chart(px.bar(tendance_loop, x='Mois_Nom', y='CAISSE EQ', title="Évolution mensuelle LOOP"), use_container_width=True)

    else:
        st.warning(f"Aucun fichier trouvé dans le dossier {page}. Vérifiez le dossier Drive.")

except Exception as e:
    st.error(f"Erreur : {e}")
