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
            
            # Nettoyage des colonnes numériques
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
        # Logique Alchimiste spécifique
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- EXCEL PRO (STYLE ALIGNÉ) ---
def generate_styled_excel(df_week, pivot_vol, pivot_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        # Formats
        fmt_money = workbook.add_format({'num_format': '#,##0.00 $', 'align': 'center'})
        fmt_qty = workbook.add_format({'num_format': '#,##0', 'align': 'center'})
        fmt_header = workbook.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'border': 1})

        def save_sheet(df, name, is_money=False):
            # Ajout d'une ligne de total identique à l'app
            df_export = df.copy()
            if not df_export.empty:
                df_export.loc['TOTAL GLOBAL'] = df_export.sum(numeric_only=True)
            
            df_export.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            f = fmt_money if is_money else fmt_qty
            
            # Ajustement largeur colonnes
            ws.set_column(0, 0, 35) # Index
            for i, col in enumerate(df_export.columns):
                ws.set_column(i+1, i+1, 18, f)

        # On exporte exactement les pivots calculés pour l'interface
        save_sheet(df_week, 'Dernière Semaine')
        save_sheet(pivot_vol, 'Vol Mensuel YOY')
        save_sheet(pivot_val, 'Dollars Mensuels YOY', is_money=True)
        save_sheet(pivot_sku, 'SKU Détail 2026')
        save_sheet(pivot_banner, 'Bannières 2026')
        
    return output.getvalue()

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # Nettoyage Dates
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    
    # Correction : Conversion numérique avant tout calcul
    for col in ['LineQty', 'LineTotal']:
        df_raw_all[col] = pd.to_numeric(df_raw_all[col], errors='coerce').fillna(0)

    # Création des colonnes de temps sur l'ensemble du dataset
    df_raw_all['Année'] = df_raw_all['DateAnalyse'].dt.year
    df_raw_all['Mois_Nom'] = df_raw_all['DateAnalyse'].dt.strftime('%m - %B')
    df_raw_all['Jour_Annee'] = df_raw_all['DateAnalyse'].dt.dayofyear
    
    # Filtrage global (Exclusion avant 2025)
    df_raw = df_raw_all[df_raw_all['Année'] >= 2025].copy()

    # Application de la logique métier (Caisse EQ)
    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- KPI COMPARATIFS YTD ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard {page}")
    
    # Affichage des métriques
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

    # --- PIVOTS POUR L'APP ET L'EXCEL (Même source) ---
    pivot_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_val = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    st.divider()
    tab_vol, tab_val = st.tabs(["📉 Volume Mensuel", "💵 Argent Mensuel"])
    with tab_vol:
        st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_vol.style.format("{:.0f}"), use_container_width=True)
    with tab_val:
        st.plotly_chart(px.line(pivot_val.reset_index(), x='Mois_Nom', y=pivot_val.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_val.style.format("{:,.2f} $"), use_container_width=True)

    # --- EXPORT EXCEL ---
    # Préparation des données spécifiques à l'export
    max_d = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_d - timedelta(days=7))].groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'})
    
    # Pour l'onglet SKU, on prend le détail mensuel de 2026
    pivot_sku_xls = df_2026_full.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    
    # Pour les bannières
    pivot_banner_xls = df_2026_full.groupby('GroupName')['CAISSE EQ'].sum().to_frame()

    # Génération du fichier avec les pivots exacts affichés ci-dessus
    excel_file = generate_styled_excel(df_week, pivot_vol, pivot_val, pivot_sku_xls, pivot_banner_xls)
    
    st.sidebar.divider()
    st.sidebar.download_button(
        label=f"📥 Télécharger Rapport {page} (Excel)",
        data=excel_file,
        file_name=f"Rapport_{page}_{date.today()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ... (Le reste de ton code pour les bannières et clients reste identique)
else:
    st.error("Données introuvables. Vérifiez vos dossiers Drive.")
