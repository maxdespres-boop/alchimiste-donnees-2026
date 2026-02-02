import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master", layout="wide")

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

# --- LOGIQUE DE CONVERSION ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- EXCEL PRO (STYLE ÉPURÉ SANS BORDURES) ---
def generate_styled_excel(df_week, pivot_vol, pivot_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        # Formats sans bordures
        fmt_header = workbook.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'align': 'center'})
        fmt_money = workbook.add_format({'num_format': '#,##0.00 $'})
        fmt_qty = workbook.add_format({'num_format': '#,##0'})
        fmt_total = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2'})
        
        def save_sheet(df, name, is_money=False, add_row_total=True):
            df_t = df.copy()
            # Total par colonne (toujours présent)
            df_t.loc['TOTAL GLOBAL'] = df_t.sum(numeric_only=True)
            # Total par rangée (désactivé pour les deux premiers onglets)
            if add_row_total and len(df_t.columns) > 1: 
                df_t['TOTAL'] = df_t.sum(axis=1, numeric_only=True)
            
            df_t.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            f = fmt_money if is_money else fmt_qty
            
            # Ajustement largeur et application du format (sans bordures)
            for i, col in enumerate(df_t.columns): 
                ws.set_column(i+1, i+1, 18, f)
            ws.set_column(0, 0, 35) # Colonne noms

        # Application des règles spécifiques par onglet
        save_sheet(df_week, 'Dernière Semaine', add_row_total=False) # Onglet 1
        save_sheet(pivot_vol, 'Vol Mensuel YOY', add_row_total=False) # Onglet 2
        save_sheet(pivot_val, 'Dollars Mensuels YOY', is_money=True, add_row_total=False)
        save_sheet(pivot_sku, 'SKU par Mois', add_row_total=True)
        save_sheet(pivot_banner, 'Bannières', add_row_total=True)
        
    return output.getvalue()

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # Nettoyage et préparation des dates
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    
    # FILTRE STRICT : On retire 2024 ici
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    df_raw = df_raw.dropna(subset=['DateAnalyse'])
    
    for col in ['LineQty', 'LineTotal', 'Rabais']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- FILTRES SIDEBAR ---
    st.sidebar.divider()
    start_ytd = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (start_ytd, date.today())
    if st.sidebar.button("🔄 Reset YTD"): st.session_state["date_range"] = (start_ytd, date.today())
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    # Filtrage des données selon le sélecteur
    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw[(df_raw['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw['DateAnalyse'].dt.date <= date_sel[1])]

    # --- KPI COMPARATIFS YTD (2025 vs 2026 uniquement) ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard {page} (2025-2026)")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📦 Volume (Eq. 12)")
        v1, v2 = st.columns(2)
        v1.metric("2026 YTD", f"{df_2026_full['CAISSE EQ'].sum():,.0f}")
        v2.metric("2025 YTD", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026_full['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    with c2:
        st.subheader("💰 Ventes ($)")
        s1, s2 = st.columns(2)
        s1.metric("2026 YTD", f"{df_2026_full['LineTotal'].sum():,.0f} $")
        s2.metric("2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026_full['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- VUE MENSUELLE ---
    st.divider()
    pivot_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_val = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    tab_vol, tab_val = st.tabs(["📉 Volume Mensuel", "💵 Argent Mensuel"])
    with tab_vol:
        st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_vol.style.format("{:.0f}"), use_container_width=True)
    with tab_val:
        st.plotly_chart(px.line(pivot_val.reset_index(), x='Mois_Nom', y=pivot_val.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_val.style.format("{:,.2f} $"), use_container_width=True)

    # --- TOP BANNIÈRES ET CLIENTS ---
    st.divider()
    col_left, col_right = st.columns(2)
    with col_left:
        st.header("🏢 Top Bannières")
        if 'GroupName' in df_filtered.columns:
            banner_data = df_filtered.groupby('GroupName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
            st.plotly_chart(px.pie(banner_data.head(10), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
    with col_right:
        st.header("👥 Top 15 Clients")
        client_data = df_filtered.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15)
        st.dataframe(client_data.rename(columns={'CardName':'Client','CAISSE EQ':'Caisses'}), use_container_width=True, hide_index=True)

    # --- EXPORT EXCEL ---
    max_d = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_d - timedelta(days=7))].groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'})
    pivot_sku = df_2026_full.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_banner = df_2026_full.pivot_table(index='GroupName' if 'GroupName' in df_raw.columns else 'ItemName', values='CAISSE EQ', aggfunc='sum').sort_values('CAISSE EQ', ascending=False)

    excel_file = generate_styled_excel(df_week, pivot_vol, pivot_val, pivot_sku, pivot_banner)
    st.sidebar.download_button(f"📥 Télécharger Rapport {page} (Excel)", data=excel_file, file_name=f"Rapport_{page}_{date.today()}.xlsx")

else:
    st.warning("Données introuvables. Vérifiez vos dossiers Drive.")
