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
                df_temp = pd.read_csv(io.StringIO(content.decode('latin1')), sep=',', quotechar='"', on_bad_lines='skip')
            df_list.append(df_temp)
        except Exception: continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LOGIQUES DE CONVERSION ---
def conv_alchimiste(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]]) # Vers format 24
    return pd.Series([qty, code])

def conv_loop(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    # Si c'est un format 24 (pas de "12" à la fin), on double pour avoir du "Equivalent 12"
    if not code.endswith('12'):
        return pd.Series([qty * 2, code])
    return pd.Series([qty, code[:-2]]) # Si finit par 12, on garde tel quel (format cible)

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ---
current_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_id)

try:
    if df_raw_all is not None:
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_base = df_raw_all.dropna(subset=['DocDate']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw_base[col] = pd.to_numeric(df_raw_base[col], errors='coerce').fillna(0)
        
        # Application de la conversion selon la marque
        if page == "Alchimiste":
            df_raw_base[['UNIT_EQ', 'SKU_BASE']] = df_raw_base.apply(conv_alchimiste, axis=1)
            label_unit = "Eq. 24"
        else:
            df_raw_base[['UNIT_EQ', 'SKU_BASE']] = df_raw_base.apply(conv_loop, axis=1)
            label_unit = "Eq. 12"

        df_raw_base['Année'] = df_raw_base['DocDate'].dt.year
        df_raw_base['Mois_Nom'] = df_raw_base['DocDate'].dt.strftime('%m - %B')
        df_raw_base['Jour_Annee'] = df_raw_base['DocDate'].dt.dayofyear

        # --- PAGE ALCHIMISTE ---
        if page == "Alchimiste":
            df_alc = df_raw_base[df_raw_base['Année'] >= 2025].copy()
            st.sidebar.divider()
            st.sidebar.header("⚙️ Contrôles Alchimiste")
            start_ytd_2026 = date(2026, 1, 1)
            def reset_ytd(): st.session_state["date_picker_key"] = (start_ytd_2026, date.today())
            if "date_picker_key" not in st.session_state: reset_ytd()
            st.sidebar.button("🔄 Reset YTD (Jan 2026)", on_click=reset_ytd)
            date_sel = st.sidebar.date_input("Filtrer", value=st.session_state["date_picker_key"], key="date_picker_key")
            df_detail = df_alc[(df_alc['DocDate'].dt.date >= date_sel[0]) & (df_alc['DocDate'].dt.date <= date_sel[1])] if isinstance(date_sel, tuple) else df_alc

            st.title("📊 Rapport Alchimiste (Format 24)")
            
            latest_day = df_alc['DocDate'].max()
            df_week = df_alc[df_alc['DocDate'] >= (latest_day - pd.Timedelta(days=6))]
            week_summary = df_week.groupby(['SKU_BASE', 'ItemName']).agg({'LineQty': 'sum', 'UNIT_EQ': 'sum', 'LineTotal': 'sum'}).reset_index()
            with st.expander("🔔 FOCUS : Derniers 7 jours", expanded=True):
                st.table(week_summary.rename(columns={'LineQty':'Qté Phys.', 'UNIT_EQ':label_unit, 'LineTotal':'Ventes ($)'}))

            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['UNIT_EQ'].sum()
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            total_eq_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]['UNIT_EQ'].sum()

            k1, k2, k3 = st.columns(3)
            k1.metric("CAISSES EQ 2026", f"{total_eq_2026:,.1f}")
            k2.metric("EQ 2025 (YTD)", f"{total_eq_2025_ytd:,.1f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.1f}")
            k3.metric("% RABAIS 2026", f"{(df_2026['Rabais'].sum()/(df_2026['LineTotal'].sum()+df_2026['Rabais'].sum())*100):.2f}%")

            st.header("📈 Comparaison Mensuelle")
            yoy_p = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='UNIT_EQ', aggfunc='sum').fillna(0)
            st.plotly_chart(px.line(yoy_p.reset_index(), x='Mois_Nom', y=yoy_p.columns, markers=True), use_container_width=True)

            st.header("👥 Top 15 Clients")
            st.dataframe(df_detail.groupby('CardName')['UNIT_EQ'].sum().reset_index().sort_values('UNIT_EQ', ascending=False).head(15), use_container_width=True, hide_index=True)

        # --- PAGE LOOP ---
        elif page == "LOOP":
            # Pour LOOP, on inclut 2025 (Novembre/Décembre) et 2026
            df_loop = df_raw_base[df_raw_base['Année'] >= 2025].copy()
            
            st.title("🍹 Rapport de Ventes : LOOP (Format 12)")
            
            st.subheader("📦 Ventes par SKU (Cumulatif 2025-2026)")
            sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'UNIT_EQ':'sum', 'LineTotal':'sum'}).sort_values('UNIT_EQ', ascending=False)
            st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'UNIT_EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

            st.subheader("📅 Détail Mensuel (Caisses Eq. 12)")
            mensuel_loop = df_loop.pivot_table(index='ItemName', columns='Mois_Nom', values='UNIT_EQ', aggfunc='sum').fillna(0)
            st.dataframe(mensuel_loop, use_container_width=True)

            st.plotly_chart(px.bar(df_loop.groupby('Mois_Nom')['UNIT_EQ'].sum().reset_index(), x='Mois_Nom', y='UNIT_EQ', title="Volume mensuel global LOOP (Eq. 12)"), use_container_width=True)

    else:
        st.warning(f"Dossier {page} vide ou non configuré.")

except Exception as e:
    st.error(f"Erreur : {e}")
