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
    # On récupère modifiedTime pour identifier le dernier fichier
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
    items = service.files().list(q=query, fields="files(id, name, modifiedTime)").execute().get('files', [])
    
    if not items: return None, None
    
    # Trier par date de modification pour trouver le dernier ID
    items_sorted = sorted(items, key=lambda x: x['modifiedTime'], reverse=True)
    latest_id = items_sorted[0]['id']
    
    df_list = []
    for item in items_sorted:
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
            
            # Marquer la source pour l'analyse du dernier arrivage
            df_temp['_file_id'] = item['id']
            
            for col in ['LineQty', 'LineTotal', 'Rabais']:
                if col in df_temp.columns and df_temp[col].dtype == 'object':
                    df_temp[col] = df_temp[col].str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
            df_list.append(df_temp)
        except Exception: continue
        
    full_df = pd.concat(df_list, ignore_index=True) if df_list else None
    return full_df, latest_id

# --- LOGIQUE DE CONVERSION ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- EXCEL PRO ---
def generate_styled_excel(df_week_comp, pivot_vol, pivot_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        fmt_money = workbook.add_format({'num_format': '#,##0.00 $'})
        fmt_qty = workbook.add_format({'num_format': '#,##0'})
        fmt_perc = workbook.add_format({'num_format': '0.0%'})
        
        def save_sheet(df, name, is_money=False, add_row_total=True):
            df_t = df.copy()
            if add_row_total:
                df_t.loc['TOTAL GLOBAL'] = df_t.sum(numeric_only=True)
            df_t.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            for i, col in enumerate(df_t.columns):
                f = fmt_perc if "Variation %" in str(col) else (fmt_money if is_money else fmt_qty)
                ws.set_column(i+1, i+1, 20, f)
            ws.set_column(0, 0, 40)

        save_sheet(df_week_comp, 'Comparaison Semaine', add_row_total=True)
        save_sheet(pivot_vol, 'Vol Mensuel YOY', add_row_total=False)
        save_sheet(pivot_val, 'Dollars Mensuels YOY', is_money=True, add_row_total=False)
        save_sheet(pivot_sku, 'Détail SKU 2026', add_row_total=True)
        save_sheet(pivot_banner, 'Bannières 2026', add_row_total=True)
    return output.getvalue()

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])

# Appel de la fonction de chargement (retourne le DF et l'ID du dernier fichier)
df_raw_all, latest_file_id = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # --- PRÉ-TRAITEMENT DES DATES ---
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])
    
    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()
    for col in ['LineQty', 'LineTotal']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
    
    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear
    df_raw['Semaine'] = df_raw['DateAnalyse'].dt.isocalendar().week

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- SECTION RÉTRACTABLE : ANALYSE DU DERNIER ARRIVAGE ---
    with st.expander("🔔 ANALYSE DU DERNIER ARRIVAGE (Détail SKU & Totaux)", expanded=True):
        # On isole les données qui proviennent uniquement du fichier le plus récent
        df_latest_file = df_raw[df_raw['_file_id'] == latest_file_id].copy()
        
        if not df_latest_file.empty:
            target_week = df_latest_file['Semaine'].max()
            st.subheader(f"Focus : Semaine {target_week} (Données du dernier fichier)")
            
            # On récupère toute l'historique pour CETTE semaine spécifique (2025 vs 2026)
            df_hist_week = df_raw[df_raw['Semaine'] == target_week].copy()
            
            sku_comp = df_hist_week.pivot_table(
                index='ItemName', 
                columns='Année', 
                values=['CAISSE EQ', 'LineTotal'], 
                aggfunc='sum'
            ).fillna(0)

            # Vérification de l'existence des deux années pour éviter les erreurs
            years_available = sku_comp.columns.get_level_values(1).unique()
            if 2025 in years_available and 2026 in years_available:
                sku_comp[('Variation', 'Vol')] = sku_comp[('CAISSE EQ', 2026)] - sku_comp[('CAISSE EQ', 2025)]
                sku_comp[('Variation', '$$')] = sku_comp[('LineTotal', 2026)] - sku_comp[('LineTotal', 2025)]
                
                # Ajout de la ligne de Total Global
                sku_comp.loc['--- TOTAL GLOBAL ---'] = sku_comp.sum()

                # Formatage du tableau
                fmt = {}
                for col in sku_comp.columns:
                    if 'LineTotal' in col[0] or '$$' in col[1]:
                        fmt[col] = "{:,.2f} $"
                    else:
                        fmt[col] = "{:,.0f}"

                st.dataframe(
                    sku_comp.style.format(fmt).apply(
                        lambda x: ['font-weight: bold; background-color: #f0f2f6' if x.name == '--- TOTAL GLOBAL ---' else '' for _ in x], 
                        axis=1
                    ), use_container_width=True
                )
            else:
                st.info("Données comparatives (2025 vs 2026) non disponibles pour cette semaine précise.")
        else:
            st.warning("Impossible d'isoler le dernier fichier importé.")

    # --- FILTRES SIDEBAR ---
    st.sidebar.divider()
    start_ytd = date(2026, 1, 1)
    if "date_range" not in st.session_state: st.session_state["date_range"] = (start_ytd, date.today())
    if st.sidebar.button("🔄 Reset YTD"): st.session_state["date_range"] = (start_ytd, date.today())
    date_sel = st.sidebar.date_input("Analyse détaillée (Graphs)", value=st.session_state["date_range"], key="date_range")

    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        df_filtered = df_raw[(df_raw['DateAnalyse'].dt.date >= date_sel[0]) & (df_raw['DateAnalyse'].dt.date <= date_sel[1])]

    # --- KPI COMPARATIFS YTD ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard Global {page}")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📦 Volume Global (YTD)")
        v1, v2 = st.columns(2)
        v1.metric("2026 YTD", f"{df_2026_full['CAISSE EQ'].sum():,.0f}")
        v2.metric("2025 YTD", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026_full['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    with c2:
        st.subheader("💰 Ventes Globales (YTD)")
        s1, s2 = st.columns(2)
        s1.metric("2026 YTD", f"{df_2026_full['LineTotal'].sum():,.0f} $")
        s2.metric("2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026_full['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- VUE MENSUELLE ---
    st.divider()
    pivot_vol = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_val = df_raw.pivot_table(index='Mois_Nom', columns='Année', values='LineTotal', aggfunc='sum').fillna(0)
    
    tab_vol, tab_val = st.tabs(["📉 Volume Mensuel", "💵 Argent Mensuel"])
    with tab_vol:
        st.plotly_chart(px.line(pivot_vol.reset_index(), x='Mois_Nom', y=pivot_vol.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_vol.style.format("{:.0f}"), use_container_width=True)
    with tab_val:
        st.plotly_chart(px.line(pivot_val.reset_index(), x='Mois_Nom', y=pivot_val.columns, markers=True), use_container_width=True)
        st.dataframe(pivot_val.style.format("{:,.2f} $"), use_container_width=True)

    # --- LOGIQUE EXPORT EXCEL ---
    current_week = df_2026_full['Semaine'].max() if not df_2026_full.empty else 1
    df_w_2026 = df_2026_full[df_2026_full['Semaine'] == current_week]
    df_w_2025 = df_raw[(df_raw['Année'] == 2025) & (df_raw['Semaine'] == current_week)]
    
    w26 = df_w_2026.groupby('ItemName')['CAISSE EQ'].sum()
    w25 = df_w_2025.groupby('ItemName')['CAISSE EQ'].sum()
    
    df_week_comp = pd.DataFrame({f'Sem {current_week} (2025)': w25, f'Sem {current_week} (2026)': w26}).fillna(0)
    df_week_comp['Var. Absolue'] = df_week_comp.iloc[:, 1] - df_week_comp.iloc[:, 0]
    df_week_comp['Variation %'] = (df_week_comp['Var. Absolue'] / df_week_comp.iloc[:, 0].replace(0, 1))

    pivot_sku_xls = df_2026_full.pivot_table(index='ItemName', columns='Mois_Nom', values='CAISSE EQ', aggfunc='sum').fillna(0)
    pivot_banner_xls = df_2026_full.groupby('GroupName')['CAISSE EQ'].sum().to_frame()

    excel_file = generate_styled_excel(df_week_comp, pivot_vol, pivot_val, pivot_sku_xls, pivot_banner_xls)
    st.sidebar.download_button(f"📥 Télécharger Rapport {page} (Excel)", data=excel_file, file_name=f"Rapport_{page}_{date.today()}.xlsx")

    # --- TOP BANNIÈRES ET CLIENTS ---
    st.divider()
    col_left, col_right = st.columns(2)
    with col_left:
        st.header("🏢 Top Bannières")
        if 'GroupName' in df_filtered.columns:
            df_filtered['GroupName_Adjusted'] = df_filtered.apply(
                lambda row: 'SUPER C' if 'SUPER C' in str(row['CardName']).upper() else row['GroupName'], 
                axis=1
            )
            banner_data = df_filtered.groupby('GroupName_Adjusted')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
            st.plotly_chart(px.pie(banner_data.head(10), values='CAISSE EQ', names='GroupName_Adjusted', hole=0.4), use_container_width=True)
    with col_right:
        st.header("👥 Top 15 Clients")
        client_data = df_filtered.groupby('CardName')['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False).head(15)
        st.dataframe(client_data.rename(columns={'CardName':'Client','CAISSE EQ':'Caisses'}), use_container_width=True, hide_index=True)

    # --- COMPARISON SKU YOY (BAS DE PAGE) ---
    st.divider()
    st.header("📦 Comparaison par SKU (YTD)")
    sku_2026_ytd = df_2026_full.groupby('ItemName')['CAISSE EQ'].sum()
    sku_2025_ytd_val = df_2025_ytd.groupby('ItemName')['CAISSE EQ'].sum()
    sku_yoy = pd.DataFrame({'2025 (YTD)': sku_2025_ytd_val, '2026 (YTD)': sku_2026_ytd}).fillna(0)
    sku_yoy['Variation'] = sku_yoy['2026 (YTD)'] - sku_yoy['2025 (YTD)']
    st.dataframe(sku_yoy.sort_values('2026 (YTD)', ascending=False).style.format("{:.0f}").bar(subset=['Variation'], align='mid', color=['#ff9999', '#99ff99']), use_container_width=True)

else:
    st.error("Données introuvables. Vérifiez vos dossiers Drive.")
