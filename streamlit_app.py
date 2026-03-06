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
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx' or name contains '.CSV') and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: 
        return None
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
        except Exception:
            continue
    return pd.concat(df_list, ignore_index=True) if df_list else None

# --- LISTE EXPLICITE DES CODES 4-PACK À DOUBLER ---
# Ces SKUs sont vendus en 4-packs et doivent être multipliés par 2
# pour obtenir l'équivalent caisse de 12 (3 x 4-packs = 1 caisse de 12)
CODES_4PACK = {
    'MABLON4P',
    'MAIPA4',
    'MAECOSS4',
    'MAROUSS4',
    'MABLONSG4P',
    'MABLANSG4P',
    'MABLONSGSA4P',
    'MAIPADG4P',
    'MAROUSG4P',
}

# --- LOGIQUE DE CONVERSION ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        # En 2025, on double uniquement les 4-packs connus explicitement
        if code in CODES_4PACK:
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])
    else:
        # En 2026, on double les 4-packs (SG4P ou tout code ne finissant pas par '12')
        if code.endswith('SG4P') or code in CODES_4PACK or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- CORRECTION SKU SANS ALCOOL (BLONDE vs BLANCHE) ---
def corriger_sku_sans_alcool(row):
    """Distingue BLONDE SANS ALCOOL (MABLONSA12) et BLANCHE SANS ALCOOL (MABLANSA12)
    qui partagent le même ItemName dans les fichiers source."""
    code = str(row['ItemCode']).strip().upper()
    name = str(row['ItemName']).strip()
    if 'SANS ALCOOL' not in name.upper():
        return name
    if code == 'MABLONSA12':
        # Remplace toute variante de "SANS ALCOOL B" ou "SANS ALCOOL" par BLONDE SANS ALCOOL
        import re
        return re.sub(r'SANS ALCOOL\s*B?', 'BLONDE SANS ALCOOL', name, flags=re.IGNORECASE).strip()
    elif code == 'MABLANSA12':
        import re
        return re.sub(r'SANS ALCOOL\s*B?', 'BLANCHE SANS ALCOOL', name, flags=re.IGNORECASE).strip()
    return name

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
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

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

    # --- CORRECTION SKU SANS ALCOOL (BLONDE vs BLANCHE) ---
    # MABLONSA12 = BLONDE SANS ALCOOL / MABLANSA12 = BLANCHE SANS ALCOOL
    # Les deux partagent le même ItemName dans les fichiers source, on les distingue via ItemCode
    if 'ItemCode' in df_raw.columns and 'ItemName' in df_raw.columns:
        df_raw['ItemName'] = df_raw.apply(corriger_sku_sans_alcool, axis=1)

    # --- FILTRES SIDEBAR ---
    st.sidebar.divider()
    
    # Déterminer la date de début selon la marque
    start_ytd = date(2026, 1, 1) if page == "Alchimiste" else date(2025, 11, 1)
    
    if "date_range" not in st.session_state or st.session_state.get("last_page") != page:
        st.session_state["date_range"] = (start_ytd, date.today())
        st.session_state["last_page"] = page

    if st.sidebar.button("🔄 Reset YTD"):
        st.session_state["date_range"] = (start_ytd, date.today())

    date_sel = st.sidebar.date_input("Analyse détaillée (Graphs)", value=st.session_state["date_range"], key="date_range")

    # --- FILTRE AVEC .copy() POUR ÉVITER SettingWithCopyWarning ---
    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        mask = (
            (df_raw['DateAnalyse'].dt.date >= date_sel[0]) &
            (df_raw['DateAnalyse'].dt.date <= date_sel[1])
        )
        df_filtered = df_raw[mask].copy()  # ← correctif critique

    if df_filtered.empty:
        st.warning(f"⚠️ Aucune donnée trouvée pour la période sélectionnée ({date_sel[0]} → {date_sel[1]}). Vérifiez que des données existent pour cette plage.")

    # --- KPI COMPARATIFS YTD ---
    df_2026_full = df_raw[df_raw['Année'] == 2026]
    max_day_2026 = df_2026_full['Jour_Annee'].max() if not df_2026_full.empty else 366
    df_2025_ytd = df_raw[(df_raw['Année'] == 2025) & (df_raw['Jour_Annee'] <= max_day_2026)]

    st.title(f"📊 Dashboard {page}")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📦 Volume (Eq. 12)")
        v1, v2 = st.columns(2)
        v1.metric("2026 YTD", f"{df_2026_full['CAISSE EQ'].sum():,.0f}")
        v2.metric("2025 YTY", f"{df_2025_ytd['CAISSE EQ'].sum():,.0f}", delta=f"{df_2026_full['CAISSE EQ'].sum() - df_2025_ytd['CAISSE EQ'].sum():,.0f}")
    with c2:
        st.subheader("💰 Ventes ($)")
        s1, s2 = st.columns(2)
        s1.metric("2026 YTD", f"{df_2026_full['LineTotal'].sum():,.0f} $")
        s2.metric("2025 YTD", f"{df_2025_ytd['LineTotal'].sum():,.0f} $", delta=f"{df_2026_full['LineTotal'].sum() - df_2025_ytd['LineTotal'].sum():,.0f} $")

    # --- VENTES DERNIÈRE SEMAINE ---
    st.divider()
    st.header("📊 Ventes de la dernière semaine")
    
    df_2026 = df_raw[df_raw['Année'] == 2026].copy()
    
    if not df_2026.empty:
        derniere_semaine = df_2026['Semaine'].max()
        df_derniere_semaine = df_2026[df_2026['Semaine'] == derniere_semaine]
        
        ventes_semaine = df_derniere_semaine.groupby('ItemName').agg({
            'CAISSE EQ': 'sum',
            'LineTotal': 'sum'
        }).reset_index()
        
        ventes_semaine = ventes_semaine.rename(columns={
            'ItemName': 'SKU',
            'CAISSE EQ': 'Caisses',
            'LineTotal': 'Ventes ($)'
        })
        
        ventes_semaine = ventes_semaine.sort_values('Ventes ($)', ascending=False)
        
        total_caisses = ventes_semaine['Caisses'].sum()
        total_dollars = ventes_semaine['Ventes ($)'].sum()
        
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.metric("Semaine", f"#{int(derniere_semaine)} - 2026")
        with col2:
            st.metric("Total Caisses", f"{total_caisses:,.0f}")
        with col3:
            st.metric("Total Ventes", f"{total_dollars:,.2f} $")
        
        st.dataframe(
            ventes_semaine.style.format({
                'Caisses': '{:.2f}',
                'Ventes ($)': '{:,.2f} $'
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.warning("Aucune donnée disponible pour 2026.")

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

    # --- LOGIQUE EXPORT EXCEL (AVEC WoW) ---
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
