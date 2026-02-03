import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

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
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION UNIQUE (BASE 12 CAISSES) ---
def harmoniser_vers_12(row, marque):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    annee = row['Année']

    if marque == "Alchimiste":
        # Données 2025 (Courtier) : Déjà en caisses de 24. On multiplie par 2 pour avoir l'équivalent 12.
        if annee == 2025:
            return pd.Series([qty * 2, code])
        # Données 2026 (SAP) : Unités individuelles. On divise par 12 pour les caisses.
        else:
            if code.endswith('SG4P'): return pd.Series([qty * 2, code]) # Packs de 4 (3 packs = 12 can)
            return pd.Series([qty / 12, code])
    else:
        # LOOP : Déjà en format 12, pas de conversion nécessaire.
        return pd.Series([qty, code])

# --- MAIN APP ---
st.sidebar.title("🍺 Contrôles Dashboard")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # Préparation DateAnalyse
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear
    
    for col in ['LineQty', 'LineTotal']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

    # Conversion des quantités
    df_raw[['CAISSE_12', 'SKU_BASE']] = df_raw.apply(harmoniser_vers_12, axis=1, args=(page,))

    # --- SÉLECTEUR DE DATE & RESET ---
    st.sidebar.divider()
    ytd_start = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (ytd_start, date.today())
    if st.sidebar.button("🔄 Reset Dates (YTD 2026)"):
        st.session_state["date_range"] = (ytd_start, date.today())
        st.rerun()
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    # --- CALCULS YTD POUR KPIs ET EXCEL ---
    df_2026 = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]
    
    # Données filtrées par le sélecteur pour les tableaux détaillés
    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw[(df_raw['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw['DateAnalyse'].dt.date <= date_sel[1])]

    # --- AFFICHAGE KPIs ---
    st.title(f"📊 Dashboard {page} (Base Caisses 12)")
    
    c1, c2, c3, c4 = st.columns(4)
    v26, v25 = df_2026['CAISSE_12'].sum(), df_2025_ytd['CAISSE_12'].sum()
    $26, $25 = df_2026['LineTotal'].sum(), df_2025_ytd['LineTotal'].sum()

    c1.metric("Volume 2026 (12)", f"{v26:,.0f}")
    c2.metric("Ventes 2026 ($)", f"{$26:,.0f} $")
    c3.metric("YOY Volume (vs 2025 YTD)", f"{v25:,.0f}", delta=f"{v26-v25:,.0f}")
    c4.metric("YOY Ventes (vs 2025 YTD)", f"{$25:,.0f} $", delta=f"{$26-$25:,.0f} $")

    # --- GRAPHIQUES ---
    st.divider()
    df_ytd_global = pd.concat([df_2026, df_2025_ytd])
    pivot_vol = df_ytd_global.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE_12', aggfunc='sum').fillna(0)
    pivot_val = df_ytd_global.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)

    t1, t2 = st.tabs(["📉 Volume Mensuel (YTD)", "💵 Dollars Mensuels (YTD)"])
    with t1: st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True), use_container_width=True)
    with t2: st.plotly_chart(px.line(pivot_val.reset_index(), x='Mois_Nom', y=pivot_val.columns, markers=True), use_container_width=True)

    # --- VENTES PAR SKU ---
    st.header("📦 Performance par SKU (Période sélectionnée)")
    sku_data = df_filtered.groupby('ItemName').agg({'CAISSE_12':'sum', 'LineTotal':'sum'}).sort_values('CAISSE_12', ascending=False)
    st.dataframe(sku_data.style.format({'CAISSE_12': '{:,.1f}', 'LineTotal': '{:,.2f} $'}), use_container_width=True)

    # --- EXPORT EXCEL SYNCHRONISÉ ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pivot_val.to_excel(writer, sheet_name='Finance_YTD_Match') # Match exact avec l'App
        pivot_vol.to_excel(writer, sheet_name='Volume_YTD_Match')
        sku_data.to_excel(writer, sheet_name='Ventes_Par_SKU')
    
    st.sidebar.divider()
    st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_{page}_YTD.xlsx")

else:
    st.error("Impossible d'accéder aux données sur Google Drive.")
