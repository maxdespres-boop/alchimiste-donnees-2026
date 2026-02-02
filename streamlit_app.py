import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP", layout="wide")

# --- CONFIGURATION DES DOSSIERS ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb" 

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    if folder_id == "VOTRE_ID_DOSSIER_LOOP_ICI": return None
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
                # Tentative de lecture CSV avec détection de séparateur (virgule ou point-virgule)
                raw_data = content.decode('latin1')
                try:
                    df_temp = pd.read_csv(io.StringIO(raw_data), sep=',', quotechar='"', on_bad_lines='skip')
                    if len(df_temp.columns) <= 1: # Si mal lu, on essaie le point-virgule
                        df_temp = pd.read_csv(io.StringIO(raw_data), sep=';', quotechar='"', on_bad_lines='skip')
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_data), sep=';', quotechar='"', on_bad_lines='skip')
            
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUES DE CONVERSION ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if qty == 0: return pd.Series([0, code])
    
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]])
    return pd.Series([qty, code])

def harmoniser_formats_loop(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty, code[:-2]])
    return pd.Series([qty, code])

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ---
current_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_id)

try:
    if df_raw_all is not None:
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
        df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
        
        df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
            label_unit = "Eq. 24"
        else:
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_loop, axis=1)
            label_unit = "Caisses (12)"

        df_raw['Année'] = df_raw['DateAnalyse'].dt.year
        df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

        if page == "Alchimiste":
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            st.sidebar.divider()
            st.sidebar.header("⚙️ Contrôles Alchimiste")
            start_ytd_2026 = date(2026, 1, 1)
            def reset_ytd(): st.session_state["date_picker_key"] = (start_ytd_2026, date.today())
            if "date_picker_key" not in st.session_state: reset_ytd()
            st.sidebar.button("🔄 Reset YTD (Jan 2026)", on_click=reset_ytd)
            date_sel = st.sidebar.date_input("Filtrer (Date Livraison)", value=st.session_state["date_picker_key"], key="date_picker_key")

            if isinstance(date_sel, tuple) and len(date_sel) == 2:
                df_detail = df_alc[(df_alc['DateAnalyse'].dt.date >= date_sel[0]) & (df_alc['DateAnalyse'].dt.date <= date_sel[1])].copy()
            else:
                df_detail = df_alc.copy()

            st.title("📊 Rapport Alchimiste")

            # KPI
            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_rabais_2026 = df_2026['Rabais'].sum()
            ventes_brutes_2026 = df_2026['LineTotal'].sum() + total_rabais_2026
            pct_rabais = (total_rabais_2026 / ventes_brutes_2026 * 100) if ventes_brutes_2026 != 0 else 0
            
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            total_eq_2025_ytd = df_2025_ytd['CAISSE EQ'].sum()

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("CAISSES EQ 2026", f"{total_eq_2026:,.1f}")
            k2.metric("RABAIS 2026", f"{total_rabais_2026:,.2f} $")
            k3.metric("% DE RABAIS", f"{pct_rabais:.2f} %")
            k4.metric("CAISSES EQ 2025 (YTD)", f"{total_eq_2025_ytd:,.1f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.1f}")

            # Graphique
            st.header("📈 Comparaison Mensuelle")
            yoy_pivot = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
            st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Nom', y=yoy_pivot.columns, markers=True), use_container_width=True)

            # --- SECTION DÉBOGAGE : PRIX MOYEN ---
            st.header("📦 Vérification : Détail par Produit & Prix")
            sku_check = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            sku_check['Prix_Moyen_Caisse'] = sku_check['LineTotal'] / sku_check['CAISSE EQ']
            st.dataframe(sku_check.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)', 'Prix_Moyen_Caisse':'$/Caisse'}).style.format({'$/Caisse': '{:.2f}'}), use_container_width=True)

        elif page == "LOOP":
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            st.title("🍹 Rapport LOOP")
            if not df_loop.empty:
                st.subheader("📦 Ventes par SKU")
                sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop, use_container_width=True)

    else:
        st.warning("Dossier non configuré.")

except Exception as e:
    st.error(f"Erreur : {e}")
