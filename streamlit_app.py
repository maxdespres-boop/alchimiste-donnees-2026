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

# --- LOGIQUES DE CONVERSION (CORRIGÉE EQ 12) ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    
    # CAS 1 : Les 4-packs (G4P) de 2026
    # 1 unité de 6x4 = 24 canettes = 2 Caisses de 12
    if code.endswith('G4P'):
        return pd.Series([qty * 2, code])
    
    # CAS 2 : Les caisses de 12 (finissent par 12)
    # 1 caisse de 12 = 1 Caisse de 12
    if code.endswith('12'):
        return pd.Series([qty, code])
    
    # CAS 3 : Les formats 24 (souvent 2025)
    # 1 caisse de 24 = 2 Caisses de 12
    return pd.Series([qty * 2, code])

def harmoniser_formats_loop(row):
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
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
        df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
        
        df_raw = df_raw_all.dropna(subset=['DateAnalyse']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
            label_unit = "Caisses (Eq. 12)"
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
            date_sel = st.sidebar.date_input("Filtrer la période", value=st.session_state["date_picker_key"], key="date_picker_key")

            if isinstance(date_sel, tuple) and len(date_sel) == 2:
                df_detail = df_alc[(df_alc['DateAnalyse'].dt.date >= date_sel[0]) & (df_alc['DateAnalyse'].dt.date <= date_sel[1])].copy()
            else:
                df_detail = df_alc.copy()

            st.title(f"📊 Dashboard Performance Alchimiste")

            # --- SECTION KPI DOUBLES ---
            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_ventes_2026 = df_2026['LineTotal'].sum()
            
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            
            total_eq_2025_ytd = df_2025_ytd['CAISSE EQ'].sum()
            total_ventes_2025_ytd = df_2025_ytd['LineTotal'].sum()

            c_vol, c_val = st.columns(2)
            with c_vol:
                st.subheader("📦 Volume (Eq. 12)")
                k1, k2 = st.columns(2)
                k1.metric("2026", f"{total_eq_2026:,.0f}")
                k2.metric("2025 YTD", f"{total_eq_2025_ytd:,.0f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.0f}")
            
            with c_val:
                st.subheader("💰 Ventes ($)")
                k3, k4 = st.columns(2)
                k3.metric("2026", f"{total_ventes_2026:,.0f} $")
                k4.metric("2025 YTD", f"{total_ventes_2025_ytd:,.0f} $", delta=f"{total_ventes_2026 - total_ventes_2025_ytd:,.0f} $")

            # --- GRAPHIQUES ---
            st.divider()
            tab1, tab2 = st.tabs(["📈 Tendance Volume", "💵 Tendance Ventes ($)"])
            
            with tab1:
                yoy_vol = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_vol.reset_index(), x='Mois_Nom', y=yoy_vol.columns, markers=True), use_container_width=True)
            
            with tab2:
                yoy_val = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_val.reset_index(), x='Mois_Nom', y=yoy_val.columns, markers=True), use_container_width=True)

            # Bannières & Clients
            st.divider()
            cb, cc = st.columns(2)
            with cb:
                st.header("🏢 Top Bannières")
                if 'GroupName' in df_detail.columns:
                    st.plotly_chart(px.pie(df_detail.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
            with cc:
                st.header("👥 Top Clients")
                st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15).rename(columns={'CardName':'Client','CAISSE EQ':label_unit}), use_container_width=True, hide_index=True)

            # Audit Final
            st.header("📦 Audit par Produit")
            sku_data = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            sku_data['$/Caisse (12)'] = sku_data['LineTotal'] / sku_data['CAISSE EQ']
            st.dataframe(sku_data.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}).style.format({'$/Caisse (12)': '{:.2f}'}), use_container_width=True)

        elif page == "LOOP":
            st.title("🍹 Rapport Performance LOOP")
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            if not df_loop.empty:
                sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

    else:
        st.warning("Vérifiez les dossiers Drive.")

except Exception as e:
    st.error(f"Erreur : {e}")
