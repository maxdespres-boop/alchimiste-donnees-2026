import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master Final", layout="wide")

# --- CONFIGURATION DRIVE ---
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
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION HYBRIDE (BASE 12) ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        return pd.Series([qty, code]) # Déjà en format 12 via courtier
    else:
        # Format SAP 2026 : Si finit par SG4P ou n'est pas codé 12 -> x2 pour Eq. 12
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- EXPORT EXCEL ÉPURÉ ---
def generate_styled_excel(df_week, pivot_vol, pivot_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        fmt_money = workbook.add_format({'num_format': '#,##0.00 $'})
        fmt_qty = workbook.add_format({'num_format': '#,##0'})
        
        def save_sheet(df, name, is_money=False, add_row_total=True):
            df_t = df.copy()
            df_t.loc['TOTAL GLOBAL'] = df_t.sum(numeric_only=True)
            if add_row_total and len(df_t.columns) > 1: 
                df_t['TOTAL'] = df_t.sum(axis=1, numeric_only=True)
            df_t.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            ws.hide_gridlines(2) # Retire les bordures
            f = fmt_money if is_money else fmt_qty
            for i, col in enumerate(df_t.columns): ws.set_column(i+1, i+1, 18, f)
            ws.set_column(0, 0, 35)

        save_sheet(df_week, 'Dernière Semaine', add_row_total=False)
        save_sheet(pivot_vol, 'Vol Mensuel YOY (YTD)', add_row_total=False)
        save_sheet(pivot_val, 'Dollars Mensuels YOY (YTD)', is_money=True, add_row_total=False)
        save_sheet(pivot_sku, 'SKU par Mois', add_row_total=True)
        save_sheet(pivot_banner, 'Bannières', add_row_total=True)
    return output.getvalue()

# --- INTERFACE ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateAnalyse'] = pd.to_datetime(df_raw_all.get('DateLivraison', df_raw_all['DocDate']), errors='coerce')
    
    # Exclusion stricte de 2024
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    
    for col in ['LineQty', 'LineTotal', 'Rabais']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
        label_unit = "Eq. 12"
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']
        label_unit = "Caisses (12)"

    # --- FILTRES SIDEBAR ---
    st.sidebar.divider()
    start_ytd = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (start_ytd, date.today())
    if st.sidebar.button("🔄 Reset YTD"): st.session_state["date_range"] = (start_ytd, date.today())
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw[(df_raw['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw['DateAnalyse'].dt.date <= date_sel[1])]

    # --- CALCULS YTD STRICTS ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]
    df_ytd_combined = pd.concat([df_2026_full, df_2025_ytd])

    st.title(f"📊 Dashboard {page}")
    
    # KPI
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Volume 2026 YTD", f"{df_2026_full['CAISSE EQ'].sum():,.0f} {label_unit}", 
                  delta=f"{df_2026_full['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f} vs 2025")
    with c2:
        st.metric("Ventes 2026 YTD", f"{df_2026_full['LineTotal'].sum():,.0f} $", 
                  delta=f"{df_2026_full['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $ vs 2025")

    # GRAPHES YOY
    st.divider()
    pivot_vol_ytd = df_ytd_combined.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_val_ytd = df_ytd_combined.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    tab1, tab2 = st.tabs(["📉 Volume Mensuel (YTD)", "💵 Dollars Mensuels (YTD)"])
    with tab1:
        st.plotly_chart(px.line(pivot_vol_ytd.reset_index(), x='Mois_Nom', y=pivot_vol_ytd.columns, markers=True), use_container_width=True)
    with tab2:
        st.plotly_chart(px.line(pivot_val_ytd.reset_index(), x='Mois_Nom', y=pivot_val_ytd.columns, markers=True), use_container_width=True)

    # BANNIÈRES & CLIENTS
    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("🏢 Top Bannières")
        if 'GroupName' in df_filtered.columns:
            st.plotly_chart(px.pie(df_filtered.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
    with col_b:
        st.subheader("👥 Top 15 Clients")
        st.dataframe(df_filtered.groupby('CardName')['CAISSE EQ'].sum().sort_values(ascending=False).head(15), use_container_width=True)

    # SKU COMPARISON YOY
    st.divider()
    st.header("📦 Performance par SKU (YTD)")
    sku_yoy = pd.DataFrame({
        '2025 (YTD)': df_2025_ytd.groupby('ItemName')['CAISSE EQ'].sum(),
        '2026 (YTD)': df_2026_full.groupby('ItemName')['CAISSE EQ'].sum()
    }).fillna(0)
    sku_yoy['Variation'] = sku_yoy['2026 (YTD)'] - sku_yoy['2025 (YTD)']
    st.dataframe(sku_yoy.sort_values('2026 (YTD)', ascending=False).style.format("{:.0f}").bar(subset=['Variation'], align='mid', color=['#ff9999', '#99ff99']), use_container_width=True)

    # EXPORT EXCEL
    max_d = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_d - timedelta(days=7))].groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'})
    pivot_sku_xls = df_2026_full.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_banner_xls = df_2026_full.groupby('GroupName')['CAISSE EQ'].sum().to_frame()

    excel_file = generate_styled_excel(df_week, pivot_vol_ytd, pivot_val_ytd, pivot_sku_xls, pivot_banner_xls)
    st.sidebar.download_button(f"📥 Télécharger Rapport {page}", data=excel_file, file_name=f"Rapport_{page}_{date.today()}.xlsx")

else:
    st.warning("Données introuvables. Vérifiez les IDs de dossiers Drive.")
