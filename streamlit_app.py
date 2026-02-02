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
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: return None
    
    df_list = []
    for item in items:
        try:
            request = service.files().get_media(fileId=item['id'])
            content = request.execute()
            filename = item['name'].lower()
            
            if filename.endswith('.xlsx'):
                df_temp = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                raw_str = content.decode('latin1')
                try:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=',', quotechar='"', on_bad_lines='skip')
                    if len(df_temp.columns) < 5: raise Exception()
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=';', quotechar='"', on_bad_lines='skip')
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUE DE CONVERSION FINALE (LA CLÉ !) ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    annee = row['Année']
    
    # En 2025, le courtier a déjà tout mis en Eq. 12 (sauf peut-être certains codes)
    # On fait confiance à la donnée 2025 car l'argent et les caisses balançaient pour le courtier.
    if annee == 2025:
        return pd.Series([qty, code])
    
    # En 2026, on sort du SAP brut, donc on doit convertir pour égaler la logique 2025
    else:
        # Si c'est un format 24 (Sans Gluten SG4P ou codes sans suffixe "12")
        # On multiplie par 2 pour obtenir des caisses Eq. 12
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        # Si c'est déjà un code finit par 12, c'est déjà une unité de 12.
        return pd.Series([qty, code])

def harmoniser_formats_loop(row):
    # LOOP est resté sur sa logique originale
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    return pd.Series([qty, code])

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ---
current_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_id)

try:
    if df_raw_all is not None:
        # Dates et Nettoyage
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
        df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
        df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        df_raw['Année'] = df_raw['DateAnalyse'].dt.year
        df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear

        # Application de la conversion
        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
            label_unit = "Eq. 12"
        else:
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_loop, axis=1)
            label_unit = "Caisses (12)"

        if page == "Alchimiste":
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            
            # Sidebar Filtres
            st.sidebar.divider()
            start_ytd_2026 = date(2026, 1, 1)
            if "date_picker_key" not in st.session_state: st.session_state["date_picker_key"] = (start_ytd_2026, date.today())
            date_sel = st.sidebar.date_input("Filtrer la période", value=st.session_state["date_picker_key"], key="date_picker_key")

            if isinstance(date_sel, tuple) and len(date_sel) == 2:
                df_detail = df_alc[(df_alc['DateAnalyse'].dt.date >= date_sel[0]) & (df_alc['DateAnalyse'].dt.date <= date_sel[1])].copy()
            else:
                df_detail = df_alc.copy()

            st.title(f"📊 Rapport Performance Alchimiste ({label_unit})")

            # --- SECTION KPI DOUBLES (Argent + Volume) ---
            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_ventes_2026 = df_2026['LineTotal'].sum()
            
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            
            total_eq_2025_ytd = df_2025_ytd['CAISSE EQ'].sum()
            total_ventes_2025_ytd = df_2025_ytd['LineTotal'].sum()

            c_vol, c_val = st.columns(2)
            with c_vol:
                st.subheader(f"📦 Volume ({label_unit})")
                k1, k2 = st.columns(2)
                k1.metric("2026", f"{total_eq_2026:,.0f}")
                k2.metric("2025 (YTD)", f"{total_eq_2025_ytd:,.0f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.0f}")
            
            with c_val:
                st.subheader("💰 Ventes ($)")
                k3, k4 = st.columns(2)
                k3.metric("2026", f"{total_ventes_2026:,.0f} $")
                k4.metric("2025 (YTD)", f"{total_ventes_2025_ytd:,.0f} $", delta=f"{total_ventes_2026 - total_ventes_2025_ytd:,.0f} $")

            # --- GRAPHIQUES ---
            st.divider()
            t1, t2 = st.tabs(["📈 Tendance Volume", "💵 Tendance Argent"])
            with t1:
                yoy_vol = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_vol.reset_index(), x='Mois_Nom', y=yoy_vol.columns, markers=True), use_container_width=True)
            with t2:
                yoy_val = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_val.reset_index(), x='Mois_Nom', y=yoy_val.columns, markers=True), use_container_width=True)

            # --- BANNIÈRES ET CLIENTS ---
            st.divider()
            cb, cc = st.columns(2)
            with cb:
                st.header("🏢 Top Bannières")
                if 'GroupName' in df_detail.columns:
                    st.plotly_chart(px.pie(df_detail.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
            with cc:
                st.header("👥 Top 15 Clients")
                st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15).rename(columns={'CardName':'Client','CAISSE EQ':label_unit}), use_container_width=True, hide_index=True)

            # --- DÉTAIL PRODUITS ---
            st.header("📦 Détail par Produit")
            sku_data = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            st.dataframe(sku_data.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

        elif page == "LOOP":
            st.title("🍹 Rapport LOOP")
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            if not df_loop.empty:
                sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

    else:
        st.warning("Veuillez vérifier la connexion au Google Drive.")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
