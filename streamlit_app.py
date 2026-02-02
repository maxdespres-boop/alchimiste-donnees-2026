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

# --- LOGIQUE DE CONVERSION ALCHIMISTE ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        return pd.Series([qty, code]) # Déjà doublé par le courtier
    else:
        # En 2026, on double les formats 24 (SG4P ou sans suffixe 12) pour égaler 2025
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- FONCTION EXCEL ESTHÉTIQUE ---
def generate_styled_excel(df_week, pivot_yoy_vol, pivot_yoy_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        # Formats
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'border': 1, 'align': 'center'})
        money_fmt = workbook.add_format({'num_format': '#,##0.00 $', 'border': 1})
        qty_fmt = workbook.add_format({'num_format': '#,##0', 'border': 1})
        total_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1})

        def write_sheet(df, name, is_money=False):
            # Ajout des totaux
            df_temp = df.copy()
            df_temp.loc['TOTAL'] = df_temp.sum(numeric_only=True)
            if len(df_temp.columns) > 1:
                df_temp['TOTAL_LIGNE'] = df_temp.sum(axis=1, numeric_only=True)
            
            df_temp.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            f = money_fmt if is_money else qty_fmt
            # Ajustement largeur et formatage
            for i, col in enumerate(df_temp.columns):
                ws.set_column(i+1, i+1, 15, f)
            ws.set_column(0, 0, 30) # Première colonne plus large

        write_sheet(df_week, 'Dernière Semaine')
        write_sheet(pivot_yoy_vol, 'Volume Mensuel YOY')
        write_sheet(pivot_yoy_val, 'Dollars Mensuels YOY', is_money=True)
        write_sheet(pivot_sku, 'Volume par SKU par Mois')
        write_sheet(pivot_banner, 'Volume par Bannière')

    return output.getvalue()

# --- CHARGEMENT ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
    
    for col in ['LineQty', 'LineTotal']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- KPI COMPARATIFS ---
    df_2026 = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard {page}")
    k_vol, k_val = st.columns(2)
    with k_vol:
        st.subheader("📦 Volume (Eq. 12)")
        v1, v2 = st.columns(2)
        v1.metric("2026", f"{df_2026['CAISSE EQ'].sum():,.0f}")
        v2.metric("2025 YTD", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    
    with k_val:
        st.subheader("💰 Ventes ($)")
        s1, s2 = st.columns(2)
        s1.metric("2026", f"{df_2026['LineTotal'].sum():,.0f} $")
        s2.metric("2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- TABLEAUX ET GRAPHES ---
    st.divider()
    pivot_yoy_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_yoy_val = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    t1, t2 = st.tabs(["📉 Tendance Volume", "💵 Tendance Argent"])
    with t1:
        st.plotly_chart(px.line(pivot_yoy_vol.reset_index(), x='Mois_Nom', y=pivot_yoy_vol.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_yoy_vol.style.format("{:.0f}"), use_container_width=True)
    with t2:
        st.plotly_chart(px.line(pivot_yoy_val.reset_index(), x='Mois_Nom', y=pivot_yoy_val.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_yoy_val.style.format("{:,.2f} $"), use_container_width=True)

    # --- PRÉPARATION EXCEL ---
    max_date = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_date - timedelta(days=7))].groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'})
    pivot_sku = df_2026.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_banner = df_2026.pivot_table(index='GroupName', values='CAISSE EQ', aggfunc='sum').sort_values('CAISSE EQ', ascending=False)

    st.sidebar.divider()
    excel_file = generate_styled_excel(df_week, pivot_yoy_vol, pivot_yoy_val, pivot_sku, pivot_banner)
    st.sidebar.download_button("📥 Télécharger Rapport Excel PRO", data=excel_file, file_name=f"Rapport_{page}_{date.today()}.xlsx")

else:
    st.warning("Veuillez connecter le Drive.")
