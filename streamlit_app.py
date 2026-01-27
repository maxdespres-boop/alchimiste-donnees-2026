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
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    if code.endswith('12'):
        return pd.Series([qty * 0.5, code[:-2]]) # Alchimiste : 12 -> 0.5 (base 24)
    return pd.Series([qty, code])

def harmoniser_formats_loop(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    # LOOP : On ne touche pas à la quantité, tout est déjà en format 12.
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
        df_raw = df_raw_all.dropna(subset=['DocDate']).copy()
        
        for col in ['LineQty', 'LineTotal', 'Rabais']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        # Application de la conversion selon la page
        if page == "Alchimiste":
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
            label_unit = "Eq. 24"
        else:
            df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_loop, axis=1)
            label_unit = "Caisses (12)"

        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Num'] = df_raw['DocDate'].dt.month
        df_raw['Mois_Nom'] = df_raw['DocDate'].dt.strftime('%m - %B')
        df_raw['Jour_Annee'] = df_raw['DocDate'].dt.dayofyear

        if page == "Alchimiste":
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            st.sidebar.divider()
            st.sidebar.header("⚙️ Contrôles Alchimiste")
            start_ytd_2026 = date(2026, 1, 1)
            def reset_ytd(): st.session_state["date_picker_key"] = (start_ytd_2026, date.today())
            if "date_picker_key" not in st.session_state: reset_ytd()
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
                st.table(week_summary.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Ventes ($)'}))

            # 2. KPI
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

            # 3. Graphique YoY
            st.header("📈 Comparaison Mensuelle (2025-2026)")
            yoy_pivot = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
            c1, c2 = st.columns([3, 2])
            with c1: st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Nom', y=yoy_pivot.columns, markers=True), use_container_width=True)
            with c2: st.dataframe(yoy_pivot.style.format("{:.1f}"), use_container_width=True)

            # 4. Bannières & Clients
            cb, cc = st.columns(2)
            with cb:
                st.header("🏢 Top Bannières")
                if 'GroupName' in df_detail.columns:
                    st.plotly_chart(px.pie(df_detail.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
            with cc:
                st.header("👥 Top 15 Clients")
                st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15).rename(columns={'CardName':'Client','CAISSE EQ':label_unit}), use_container_width=True, hide_index=True)

            # 5. Détail Produits
            st.header("📦 Détail par Produit sur la période")
            sku_data = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            st.dataframe(sku_data.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

            # Export Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                week_summary.to_excel(writer, sheet_name='Focus_Semaine', index=False)
                yoy_pivot.to_excel(writer, sheet_name='Performance_YTD')
                sku_data.to_excel(writer, sheet_name='Detail_Periode')
            st.sidebar.download_button(label="📥 Télécharger Rapport Alchimiste", data=output.getvalue(), file_name=f"Rapport_Alchimiste_{date.today()}.xlsx")

        elif page == "LOOP":
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            st.title("🍹 Rapport de Ventes : LOOP (Format 12)")
            
            if not df_loop.empty:
                st.subheader("📦 Ventes par SKU (Cumulatif)")
                sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

                st.subheader("📅 Détail Mensuel (Caisses 12)")
                mensuel_loop = df_loop.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.dataframe(mensuel_loop, use_container_width=True)

                st.plotly_chart(px.bar(df_loop.groupby('Mois_Nom')['CAISSE EQ'].sum().reset_index(), x='Mois_Nom', y='CAISSE EQ', title="Volume mensuel LOOP"), use_container_width=True)

    else:
        st.warning(f"Veuillez configurer l'ID du dossier {page} dans le code.")

except Exception as e:
    st.error(f"Erreur : {e}")
    
# À ajouter pour déboguer
if df_raw_all is not None:
    st.write(f"Total brut dans les fichiers : {df_raw_all['LineTotal'].sum():,.2f} $")
    st.write(f"Lignes avec dates invalides : {df_raw_all['DocDate'].isna().sum()}")

# --- BLOC DE DÉBOGAGE TEMPORAIRE ---
st.subheader("🔍 Analyse de la différence")
c1, c2, c3 = st.columns(3)
with c1:
    st.write("Total 2025 (Toute l'année) :")
    st.write(df_alc[df_alc['Année'] == 2025]['LineTotal'].sum())
with c2:
    st.write("Total 2026 (Toute l'année) :")
    st.write(df_alc[df_alc['Année'] == 2026]['LineTotal'].sum())
with c3:
    st.write("Montants négatifs (Retours) :")
    st.write(df_alc[df_alc['LineTotal'] < 0]['LineTotal'].sum())
