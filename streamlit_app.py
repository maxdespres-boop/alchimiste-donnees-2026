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

# --- LOGIQUES DE CONVERSION (NOUVELLE LOGIQUE EQ 12) ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    
    # 2026 : Si c'est déjà un format 12 (12 ou G4P), 1 caisse facturée = 1 caisse Eq. 12
    if code.endswith('12') or code.endswith('G4P'):
        return pd.Series([qty, code])
    
    # 2025 : Si c'est un format 24, 1 caisse facturée = 2 caisses Eq. 12
    return pd.Series([qty * 2, code])

def harmoniser_formats_loop(row):
    # LOOP est déjà en base 12, on ne touche à rien
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
            date_sel = st.sidebar.date_input("Filtrer la vue globale", value=st.session_state["date_picker_key"], key="date_picker_key")

            if isinstance(date_sel, tuple) and len(date_sel) == 2:
                df_detail = df_alc[(df_alc['DateAnalyse'].dt.date >= date_sel[0]) & (df_alc['DateAnalyse'].dt.date <= date_sel[1])].copy()
            else:
                df_detail = df_alc.copy()

            st.title(f"📊 Rapport Alchimiste - Standardisé en {label_unit}")

            # KPI
            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_rabais_2026 = df_2026['Rabais'].sum()
            ventes_brutes_2026 = df_2026['LineTotal'].sum() + total_rabais_2026
            
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            total_eq_2025_ytd = df_2025_ytd['CAISSE EQ'].sum()

            k1, k2, k3, k4 = st.columns(4)
            k1.metric(f"VOL. 2026 ({label_unit})", f"{total_eq_2026:,.1f}")
            k2.metric("VENTES $ 2026", f"{df_2026['LineTotal'].sum():,.2f} $")
            k3.metric("RABAIS 2026", f"{total_rabais_2026:,.2f} $")
            k4.metric(f"VOL. 2025 (YTD - {label_unit})", f"{total_eq_2025_ytd:,.1f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.1f}")

            # ... (Gardez le début du code identique jusqu'aux KPI) ...

        if page == "Alchimiste":
            df_alc = df_raw[df_raw['Année'] >= 2025].copy()
            # ... (Logique de date picker) ...

            st.title(f"📊 Rapport Alchimiste - Audit Volume vs Argent")

            # --- CALCULS KPI YOY (ARGENT + VOLUME) ---
            df_2026 = df_alc[df_alc['Année'] == 2026]
            total_eq_2026 = df_2026['CAISSE EQ'].sum()
            total_ventes_2026 = df_2026['LineTotal'].sum()
            
            max_day_2026 = df_2026['Jour_Annee'].max() if not df_2026.empty else 366
            df_2025_ytd = df_alc[(df_alc['Année'] == 2025) & (df_alc['Jour_Annee'] <= max_day_2026)]
            
            total_eq_2025_ytd = df_2025_ytd['CAISSE EQ'].sum()
            total_ventes_2025_ytd = df_2025_ytd['LineTotal'].sum()

            # --- AFFICHAGE KPI ---
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("📦 Comparaison Volume")
                k1, k2 = st.columns(2)
                k1.metric(f"Vol. 2026 ({label_unit})", f"{total_eq_2026:,.0f}")
                k2.metric(f"Vol. 2025 (YTD)", f"{total_eq_2025_ytd:,.0f}", delta=f"{total_eq_2026 - total_eq_2025_ytd:,.0f}")

            with col2:
                st.subheader("💰 Comparaison Argent")
                k3, k4 = st.columns(2)
                k3.metric("Ventes $ 2026", f"{total_ventes_2026:,.2f} $")
                # Voici le test : Si ce delta est positif de 100k, mais le volume est négatif, le problème est la colonne LineQty
                k4.metric("Ventes $ 2025 (YTD)", f"{total_ventes_2025_ytd:,.2f} $", delta=f"{total_ventes_2026 - total_ventes_2025_ytd:,.2f} $")

            # --- GRAPHIQUES ---
            tab1, tab2 = st.tabs(["📈 Graphique Volume", "💵 Graphique Argent"])
            
            with tab1:
                yoy_vol = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_vol.reset_index(), x='Mois_Nom', y=yoy_vol.columns, markers=True, title="Volume Mensuel (Eq. 12)"), use_container_width=True)
            
            with tab2:
                yoy_val = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
                st.plotly_chart(px.line(yoy_val.reset_index(), x='Mois_Nom', y=yoy_val.columns, markers=True, title="Ventes Mensuelles ($)"), use_container_width=True)

# ... (Reste du code identique) ...

            # Graphique YoY
            st.header("📈 Comparaison Mensuelle (Volume Eq. 12)")
            yoy_pivot = df_alc.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
            st.plotly_chart(px.line(yoy_pivot.reset_index(), x='Mois_Nom', y=yoy_pivot.columns, markers=True), use_container_width=True)

            # Bannières & Clients
            cb, cc = st.columns(2)
            with cb:
                st.header("🏢 Top Bannières")
                if 'GroupName' in df_detail.columns:
                    st.plotly_chart(px.pie(df_detail.groupby('GroupName')['CAISSE EQ'].sum().reset_index(), values='CAISSE EQ', names='GroupName', hole=0.4), use_container_width=True)
            with cc:
                st.header("👥 Top 15 Clients")
                st.dataframe(df_detail.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15).rename(columns={'CardName':'Client','CAISSE EQ':label_unit}), use_container_width=True, hide_index=True)

            # Détail Produits
            st.header("📦 Détail Produits & Prix Moyen")
            sku_data = df_detail.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
            sku_data['$/Caisse (12)'] = sku_data['LineTotal'] / sku_data['CAISSE EQ']
            st.dataframe(sku_data.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}).style.format({'$/Caisse (12)': '{:.2f}'}), use_container_width=True)

        elif page == "LOOP":
            # (Structure identique pour LOOP)
            st.title("🍹 Rapport LOOP")
            df_loop = df_raw[df_raw['Année'] >= 2025].copy()
            if not df_loop.empty:
                sku_loop = df_loop.groupby('ItemName').agg({'LineQty':'sum', 'CAISSE EQ':'sum', 'LineTotal':'sum'}).sort_values('CAISSE EQ', ascending=False)
                st.dataframe(sku_loop.rename(columns={'LineQty':'Qté Phys.', 'CAISSE EQ':label_unit, 'LineTotal':'Total ($)'}), use_container_width=True)

    else:
        st.warning("Vérifiez les dossiers Drive.")

except Exception as e:
    st.error(f"Erreur : {e}")
