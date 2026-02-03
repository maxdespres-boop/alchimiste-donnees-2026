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
                # Utilisation de latin1 pour bien lire les accents du CSV (comme MÉTRO)
                df_temp = pd.read_csv(io.BytesIO(content), sep=',', encoding='latin1', on_bad_lines='skip')
            
            # Nettoyage des noms de colonnes (enlève les espaces invisibles)
            df_temp.columns = df_temp.columns.str.strip()
            df_list.append(df_temp)
        except Exception as e:
            st.error(f"Erreur sur le fichier {item['name']}: {e}")
            continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION UNITÉS ---
def harmoniser_vers_12(row, marque):
    code = str(row.get('ItemCode', '')).strip().upper()
    qty = pd.to_numeric(row.get('LineQty', 0), errors='coerce') or 0
    annee = row.get('Année', 2026)
    if marque == "Alchimiste":
        if annee == 2025: return pd.Series([qty * 2, code])
        else:
            # Si le code finit par SG4P (Pack de 4), 1 unité = 4 canettes. 
            # Pour faire une caisse de 12, il faut 3 unités. Donc qty / 3.
            if code.endswith('SG4P'): return pd.Series([qty / 3, code])
            return pd.Series([qty / 12, code])
    else: return pd.Series([qty, code])

# --- MAIN APP ---
st.sidebar.title("🍺 Contrôles Dashboard")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # Nettoyage des données numériques
    for col in ['LineQty', 'LineTotal']:
        if col in df_raw_all.columns:
            df_raw_all[col] = pd.to_numeric(df_raw_all[col].astype(str).str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

    # Dates
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    df_raw_all['Année'] = df_raw_all['DateAnalyse'].dt.year
    df_raw_all['Mois_Nom'] = df_raw_all['DateAnalyse'].dt.strftime('%m - %B')
    df_raw_all['Jour_Annee'] = df_raw_all['DateAnalyse'].dt.dayofyear

    # Conversion Caisses de 12
    df_raw_all[['CAISSE_12', 'SKU_BASE']] = df_raw_all.apply(harmoniser_vers_12, axis=1, args=(page,))

    # --- SÉLECTEUR DE DATE ---
    st.sidebar.divider()
    ytd_start = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (ytd_start, date.today())
    date_sel = st.sidebar.date_input("Période d'analyse", value=st.session_state["date_range"], key="date_range")

    # Filtrage
    df_filtered = df_raw_all.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw_all[(df_raw_all['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw_all['DateAnalyse'].dt.date <= date_sel[1])]

    # --- LOGIQUE DE SUBDIVISION DES BANNIÈRES (BASÉE SUR TON CSV) ---
    if 'GroupName' in df_filtered.columns and 'CardName' in df_filtered.columns:
        # On crée la colonne propre
        df_filtered['Banniere_Clean'] = df_filtered['GroupName'].astype(str)
        
        # On cherche "METRO" ou "MÉTRO" dans GroupName
        is_metro = df_filtered['GroupName'].str.contains("METRO|MÉTRO", case=False, na=False)
        # On cherche "SUPER C" dans CardName
        is_superc = df_filtered['CardName'].str.contains("SUPER C", case=False, na=False)
        
        df_filtered.loc[is_metro, 'Banniere_Clean'] = "METRO"
        df_filtered.loc[is_metro & is_superc, 'Banniere_Clean'] = "SUPER C"
    
    # --- CALCULS YTD ---
    df_2026 = df_raw_all[df_raw_all['Année'] == 2026]
    max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
    df_2025_ytd = df_raw_all[(df_raw_all['Année'] == 2025) & (df_raw_all['Jour_Annee'] <= max_day_2026)]

    # --- AFFICHAGE ---
    st.title(f"📊 Dashboard {page}")
    
    c1, c2, c3, c4 = st.columns(4)
    v26, v25 = df_2026['CAISSE_12'].sum(), df_2025_ytd['CAISSE_12'].sum()
    $26, $25 = df_2026['LineTotal'].sum(), df_2025_ytd['LineTotal'].sum()
    
    c1.metric("Volume 2026 (12)", f"{v26:,.0f}")
    c2.metric("Ventes 2026 ($)", f"{$26:,.0f} $")
    c3.metric("YOY Vol. (vs 2025)", f"{v25:,.0f}", delta=f"{v26-v25:,.0f}")
    c4.metric("YOY Ventes (vs 2025)", f"{$25:,.0f} $", delta=f"{$26-$25:,.0f} $")

    st.divider()
    t1, t2 = st.tabs(["📉 Performance", "🏢 Bannières"])
    
    with t1:
        df_viz = pd.concat([df_2026, df_2025_ytd])
        p1 = df_viz.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE_12', aggfunc='sum').fillna(0)
        st.plotly_chart(px.line(p1.reset_index(), x='Mois_Nom', y=p1.columns, markers=True, title="Volume YTD"), use_container_width=True)

    with t2:
        st.header("🏢 Analyse par Enseigne")
        banner_data = df_filtered.groupby('Banniere_Clean')['CAISSE_12'].sum().reset_index().sort_values('CAISSE_12', ascending=False)
        st.plotly_chart(px.pie(banner_data.head(10), values='CAISSE_12', names='Banniere_Clean', hole=0.4), use_container_width=True)
        st.dataframe(banner_data, hide_index=True)

    # SKU Section
    st.header("📦 Performance par SKU")
    sku_data = df_filtered.groupby('ItemName').agg({'CAISSE_12':'sum', 'LineTotal':'sum'}).sort_values('CAISSE_12', ascending=False)
    st.dataframe(sku_data.style.format({'CAISSE_12': '{:,.1f}', 'LineTotal': '{:,.2f} $'}), use_container_width=True)

    # EXPORT
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_filtered.pivot_table(index='Banniere_Clean', columns='Année', values='LineTotal', aggfunc='sum').to_excel(writer, sheet_name='Finance')
        sku_data.to_excel(writer, sheet_name='SKU')
    st.sidebar.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name=f"Rapport_{page}.xlsx")

else:
    st.error("Données Drive introuvables.")
