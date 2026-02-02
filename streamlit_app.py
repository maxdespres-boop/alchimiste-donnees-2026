import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste Pro", layout="wide")

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

def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025: return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or (not code.endswith('12')): return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- LOGIQUE D'EXPORTATION EXCEL STYLISÉE ---
def to_excel_pro(df_week, pivot_yoy, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # Formats
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#1f4e78', 'font_color': 'white', 'border': 1})
        total_fmt = workbook.add_format({'bold': True, 'bg_color': '#d9e1f2', 'border': 1})
        money_fmt = workbook.add_format({'num_format': '#,##0.00 $', 'border': 1})
        qty_fmt = workbook.add_format({'num_format': '#,##0', 'border': 1})

        sheets = {
            'Dernière Semaine': (df_week, "Ventes de la semaine"),
            'Mensuel YOY': (pivot_yoy, "Volume & Dollars par Mois"),
            'SKU par Mois': (pivot_sku, "Volume par SKU et Mois"),
            'Par Bannière': (pivot_banner, "Volume par Bannière")
        }

        for sheet_name, (df, title) in sheets.items():
            # Ajout des totaux pour les tableaux pivots
            if sheet_name != 'Dernière Semaine':
                df.loc['TOTAL'] = df.sum(numeric_only=True)
                df['TOTAL_ROW'] = df.sum(axis=1, numeric_only=True)
            
            df.to_excel(writer, sheet_name=sheet_name, startrow=1)
            worksheet = writer.sheets[sheet_name]
            
            # Application des styles sur les colonnes (simplifié)
            for i, col in enumerate(df.columns):
                worksheet.write(1, i+1, col, header_fmt)
            
        writer.close()
    return output.getvalue()

# --- CHARGEMENT ---
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # Traitement temporel
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

    # --- KPI & GRAPHIQUES ---
    st.title(f"🚀 Dashboard {page}")
    
    # 1. Comparaison YOY Mensuelle (Le retour!)
    st.subheader("📊 Comparaison Mensuelle YOY")
    col_v, col_d = st.columns(2)
    
    pivot_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_val = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    with col_v:
        st.write("**Volume (Caisses EQ)**")
        st.dataframe(pivot_vol.style.format("{:.0f}").highlight_max(axis=1, color="#b7e4c7"), use_container_width=True)
    with col_d:
        st.write("**Valeur ($)**")
        st.dataframe(pivot_val.style.format("{:,.2f} $"), use_container_width=True)

    # --- PRÉPARATION EXCEL ---
    # Focus Semaine
    max_date = df_raw['DateAnalyse'].max()
    df_week = df_raw[df_raw['DateAnalyse'] > (max_date - timedelta(days=7))].groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'})
    
    # SKU par Mois
    pivot_sku = df_raw[df_raw['Année'] == 2026].pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    
    # Par Bannière
    pivot_banner = df_raw[df_raw['Année'] == 2026].pivot_table(index='GroupName', values='CAISSE EQ', aggfunc='sum').sort_values('CAISSE EQ', ascending=False)

    # --- BOUTON TÉLÉCHARGEMENT ---
    st.sidebar.divider()
    excel_data = to_excel_pro(df_week, pivot_vol, pivot_sku, pivot_banner)
    st.sidebar.download_button(
        label="📥 Télécharger Rapport Excel PRO",
        data=excel_data,
        file_name=f"Rapport_{page}_{date.today()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Affichage du reste du dashboard original...
    st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True, title="Tendance Volume"), use_container_width=True)

else:
    st.warning("Données introuvables sur le Drive.")
