import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master Intégral", layout="wide")

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
                try:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=',', quotechar='"', on_bad_lines='skip')
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=';', quotechar='"', on_bad_lines='skip')
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION (CORRIGÉE) ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    # 2025 est déjà en format caisse (Courtier). On ne touche à rien.
    if row['Année'] == 2025:
        return pd.Series([qty, code])
    # 2026 SAP : Si c'est un format 12 (ex: ...12), SAP compte en unités. On divise pour avoir des caisses de 12.
    # Si c'est un pack (SG4P), on multiplie par 2 pour l'équivalent caisse de 12.
    else:
        if code.endswith('SG4P'):
            return pd.Series([qty * 2, code])
        elif code.endswith('12'):
            return pd.Series([qty / 12 if qty >= 12 else qty, code]) # Protection si déjà converti
        return pd.Series([qty, code])

# --- EXCEL STYLE ---
def generate_styled_excel(df_week, pivot_vol, pivot_val_ytd, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Onglet Finance (Celui qui doit matcher l'App)
        pivot_val_ytd.to_excel(writer, sheet_name='Finance_YTD_Match')
        # Onglet Volume
        pivot_vol.to_excel(writer, sheet_name='Volume_Mensuel_YOY')
        # Détails
        df_week.to_excel(writer, sheet_name='Derniere_Semaine')
        pivot_sku.to_excel(writer, sheet_name='Details_SKU_2026')
        pivot_banner.to_excel(writer, sheet_name='Bannieres_2026')
        
    return output.getvalue()

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    
    for col in ['LineQty', 'LineTotal']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

    # Application de la logique Alchimiste
    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- KPI COMPARATIFS YTD (C'est ici qu'on assure le matching) ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard {page}")
    
    # Affichage des KPI
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Vol. 2026 YTD", f"{df_2026_full['CAISSE EQ'].sum():,.0f}")
    k2.metric("Ventes 2026 YTD", f"{df_2026_full['LineTotal'].sum():,.0f} $")
    k3.metric("Vol. 2025 YTD", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026_full['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    k4.metric("Ventes 2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026_full['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- GRAPHIQUES ---
    st.divider()
    pivot_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    
    # Pivot financier spécifique YTD pour l'export et l'affichage
    df_ytd_only = pd.concat([df_2026_full, df_2025_ytd])
    pivot_val_ytd = df_ytd_only.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    t1, t2 = st.tabs(["📉 Volume Mensuel (Global)", "💵 Argent Mensuel (YTD Match)"])
    with t1:
        st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True), use_container_width=True)
    with t2:
        st.plotly_chart(px.line(pivot_val_ytd.reset_index(), x='Mois_Nom', y=pivot_val_ytd.columns, markers=True), use_container_width=True)

    # --- TOP BANNIÈRES ---
    st.header("🏢 Top Bannières (Période 2026)")
    if 'GroupName' in df_2026_full.columns:
        banner_data = df_2026_full.groupby('GroupName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
        st.plotly_chart(px.pie(banner_data.head(10), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)

    # --- EXPORT EXCEL ---
    max_d = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_d - timedelta(days=7))].groupby('ItemName').agg({'CAISSE EQ':'sum', 'LineTotal':'sum'})
    pivot_sku_xls = df_2026_full.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_banner_xls = df_2026_full.groupby('GroupName')['CAISSE EQ'].sum().to_frame()

    excel_file = generate_styled_excel(df_week, pivot_vol, pivot_val_ytd, pivot_sku_xls, pivot_banner_xls)
    st.sidebar.download_button(f"📥 Télécharger Rapport {page}", data=excel_file, file_name=f"Rapport_{page}_{date.today()}.xlsx")

else:
    st.error("Données introuvables. Vérifiez vos dossiers Drive.")
