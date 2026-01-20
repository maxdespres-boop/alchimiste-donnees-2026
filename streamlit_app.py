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
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb" # À remplacer par le vrai ID

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    if folder_id == "ID_DOSSIER_LOOP": return None # Sécurité si non configuré
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

def harmoniser_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]])
    return pd.Series([qty, code])

# --- NAVIGATION ---
st.sidebar.title("🍺 Navigation")
page = st.sidebar.radio("Sélectionner une marque :", ["Alchimiste", "LOOP"])

# --- CHARGEMENT ---
current_id = ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
df_raw_all = load_data_from_drive(current_id)

try:
    if df_raw_all is not None:
        # Nettoyage commun
        df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
        df_raw_base = df_raw_all.dropna(subset=['DocDate']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw_base[col] = pd.to_numeric(df_raw_base[col], errors='coerce').fillna(0)
        
        df_raw_base[['CAISSE EQ', 'SKU_BASE']] = df_raw_base.apply(harmoniser_formats, axis=1)
        df_raw_base['Année'] = df_raw_base['DocDate'].dt.year
        df_raw_base['Mois_Nom'] = df_raw_base['DocDate'].dt.strftime('%m - %B')
        df_raw_base['Jour_Annee'] = df_raw_base['DocDate'].dt.dayofyear

        # --- LOGIQUE PAGE ALCHIMISTE (VOTRE CODE ORIGINAL) ---
        if page == "Alchimiste":
            # Filtrer 2024 (uniquement pour Alchimiste)
            df_alc = df_raw_base[df_raw_base['Année'] >= 2025].copy()
            
            # --- GESTION DU FILTRE DE DATE (SIDEBAR) ---
            st.sidebar.divider()
            st.sidebar.header("⚙️ Contrôles Alchimiste")
            start_ytd_2026 = date(2026, 1, 1)
            end_today = date.today()

            def reset_ytd():
                st.session_state["date_picker_key"] = (start_ytd_2026, end_today)

            if "date_picker_key" not in st.session_state:
                reset_ytd()

            st.sidebar.button("🔄 Reset YTD (Jan 2026)", on_click=reset_ytd)
            date_sel = st.sidebar.date_input("Filtrer la vue globale", value=st.session_state["date_picker_key"], key="date_picker_key")

            if isinstance(date_sel, tuple) and len(date_sel) == 2:
                df_detail = df_alc[(df_alc['DocDate'].dt.date >= date_sel[0]) & (df_alc['DocDate'].dt.date <= date_sel[1])].copy()
            else:
                df_detail = df_alc.copy()

            st.title("📊 Rapport de Ventes Alchimiste")

            # 1. FOCUS SEMAINE
            latest_day = df_alc['DocDate'].max()
            df_week = df_alc[df_alc['DocDate'] >= (latest_day - pd.Timedelta(days=6))].copy()
            week_summary = df_week.groupby(['SKU_BASE', 'ItemName']).agg({'LineQty': 'sum', 'CAISSE EQ': 'sum', 'LineTotal': 'sum'}).reset_index()
            with st.expander(f"🔔 FOCUS : Derniers 7 jours reçus", expanded=True):
                st.table(week_summary.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':'Eq. 24', 'LineTotal':'Ventes ($)'}))

            # 2. KPI YTD
            st.subheader("🎯 Performance YTD Automatique")
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
            k4.metric("CAISSES EQ 2025 (YTD)", f"{total_eq_2025_ytd:,.1f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.1f} vs an dernier")

            st.divider()

            # 3. YoY MENSUEL
            st.header("📈 Comparaison Mensuelle (2025-2026)")
            yoy_pivot = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
            c1, c2 = st.columns([3, 2])
            with c1: st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Nom', y=yoy_pivot.columns, markers=True), use_container_width=True)
            with c2: st.dataframe(yoy_pivot.style.format("{:.1f}"), use_container_width=True)

            st.divider()

            # 4. BANNIÈRES & CLIENTS
            cb, cc = st.columns(2)
            with cb:
                st.header("🏢 Top Bannières")
                if 'GroupName' in df_detail.columns:
                    st.plotly_chart(px.pie(df_detail.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
            with cc:
                st.header("👥 Top 15 Clients")
                st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15).rename(columns={'CardName':'Client','CAISSE EQ':'Eq 24'}), use_container_width=True, hide_index=True)

            # 5. DÉTAIL PRODUITS
            st.header("📦 Détail par Produit sur la période")
            sku_data = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            st.dataframe(sku_data.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':'Eq. 24', 'LineTotal':'Total ($)'}), use_container_width=True)

            # EXPORT
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                week_summary.to_excel(writer, sheet_name='Focus_Semaine', index=False)
                yoy_pivot.to_excel(writer, sheet_name='Performance_YTD')
                sku_data.to_excel(writer, sheet_name='Detail_Periode')
            st.sidebar.download_button("📥 Télécharger Rapport Alchimiste", data=output.getvalue(), file_name=f"Rapport_Alchimiste_{date.today()}.xlsx")

        # --- LOGIQUE PAGE LOOP ---
        elif page == "LOOP":
            st.title("🍹 Rapport de Ventes : LOOP")
            if df_raw_base.empty:
                st.info("En attente de fichiers dans le dossier LOOP...")
            else:
                st.subheader("📦 Ventes par SKU")
                sku_loop = df_raw_base.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':'Eq. 24', 'LineTotal':'Total ($)'}), use_container_width=True)

                st.subheader("📅 Ventes Mensuelles (Eq. 24)")
                mensuel_loop = df_raw_base.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.dataframe(mensuel_loop, use_container_width=True)

    else:
        st.warning(f"Aucun fichier trouvé pour {page}. Vérifiez les IDs de dossiers et les partages Drive.")

except Exception as e:
    st.error(f"Erreur de traitement : {e}")
