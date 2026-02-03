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

# --- LOGIQUE DE CONVERSION ---
def harmoniser_vers_12(row, marque):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    annee = row['Année']
    if marque == "Alchimiste":
        if annee == 2025: return pd.Series([qty * 2, code])
        else:
            if code.endswith('SG4P'): return pd.Series([qty * 2, code])
            return pd.Series([qty / 12, code])
    else: return pd.Series([qty, code])

# --- LOGIQUE DE SUBDIVISION DES BANNIÈRES (CORRIGÉE) ---
def subdiviser_banniere(row):
    # On convertit tout en string et on enlève TOUS les espaces doubles ou bizarres
    groupe = str(row.get('GroupName', '')).upper()
    client = str(row.get('CardName', '')).upper()
    
    # Au lieu de chercher la phrase exacte, on cherche simplement si le mot METRO est présent
    # Cela règle le problème du "MÉTRO FRANCHISÉ -CO" avec son espace erroné.
    if "METRO" in groupe or "MÉTRO" in groupe:
        # On applique la même souplesse pour Super C
        if "SUPER C" in client or "SUPERC" in client:
            return "SUPER C"
        else:
            return "METRO"

# --- MAIN APP ---
st.sidebar.title("🍺 Contrôles Dashboard")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear
    
    for col in ['LineQty', 'LineTotal']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)

    df_raw[['CAISSE_12', 'SKU_BASE']] = df_raw.apply(harmoniser_vers_12, axis=1, args=(page,))
    
    # Application de la nouvelle subdivision
    df_raw['Banniere_Clean'] = df_raw.apply(subdiviser_banniere, axis=1)

    # --- SÉLECTEUR DE DATE & RESET ---
    st.sidebar.divider()
    ytd_start = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (ytd_start, date.today())
    if st.sidebar.button("🔄 Reset Dates (YTD 2026)"):
        st.session_state["date_range"] = (ytd_start, date.today())
        st.rerun()
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    # --- CALCULS YTD ---
    df_2026 = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]
    
    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw[(df_raw['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw['DateAnalyse'].dt.date <= date_sel[1])]

    # --- AFFICHAGE KPIs ---
    st.title(f"📊 Dashboard {page}")
    c1, c2, c3, c4 = st.columns(4)
    v26, v25 = df_2026['CAISSE_12'].sum(), df_2025_ytd['CAISSE_12'].sum()
    $26, $25 = df_2026['LineTotal'].sum(), df_2025_ytd['LineTotal'].sum()

    c1.metric("Volume 2026 (12)", f"{v26:,.0f}")
    c2.metric("Ventes 2026 ($)", f"{$26:,.0f} $")
    c3.metric("YOY Volume", f"{v25:,.0f}", delta=f"{v26-v25:,.0f}")
    c4.metric("YOY Ventes", f"{$25:,.0f} $", delta=f"{$26-$25:,.0f} $")

    # --- GRAPHIQUES ---
    st.divider()
    t1, t2 = st.tabs(["📉 Performance Temporelle", "🏢 Analyse Bannières"])
    
    with t1:
        df_ytd_global = pd.concat([df_2026, df_2025_ytd])
        p1 = df_ytd_global.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE_12', aggfunc='sum').fillna(0)
        st.plotly_chart(px.line(p1.reset_index(), x='Mois_Nom', y=p1.columns, markers=True, title="Volume Mensuel YTD (Base 12)"), use_container_width=True)

    with t2:
        st.header("🏢 Répartition par Bannière")
        banner_data = df_filtered.groupby('Banniere_Clean')['CAISSE_12'].sum().reset_index().sort_values('CAISSE_12', ascending=False)
        st.plotly_chart(px.pie(banner_data.head(15), values='CAISSE_12', names='Banniere_Clean', hole=0.4), use_container_width=True)
        st.dataframe(banner_data.rename(columns={'Banniere_Clean':'Bannière', 'CAISSE_12':'Caisses (12)'}), hide_index=True, use_container_width=True)

    # --- VENTES PAR SKU ---
    st.header("📦 Performance par SKU (Période sélectionnée)")
    sku_data = df_filtered.groupby('ItemName').agg({'CAISSE_12':'sum', 'LineTotal':'sum'}).sort_values('CAISSE_12', ascending=False)
    st.dataframe(sku_data.style.format({'CAISSE_12': '{:,.1f}', 'LineTotal': '{:,.2f} $'}), use_container_width=True)

    # --- EXPORT EXCEL ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_filtered.pivot_table(index='Banniere_Clean', columns='Année', values='LineTotal', aggfunc='sum').to_excel(writer, sheet_name='Finance_Bannieres')
        sku_data.to_excel(writer, sheet_name='Ventes_Par_SKU')
    st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_{page}_Clean.xlsx")

else:
    st.error("Données Drive introuvables.")
