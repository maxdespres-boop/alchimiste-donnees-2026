import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
from datetime import date

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master Intégral", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb"

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3',
                 credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and (name contains '.csv' or name contains '.xlsx') and trashed = false"
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
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=',', on_bad_lines='skip')
                except:
                    df_temp = pd.read_csv(io.StringIO(raw_str), sep=';', on_bad_lines='skip')

            for col in ['LineQty', 'LineTotal']:
                if col in df_temp.columns:
                    df_temp[col] = pd.to_numeric(df_temp[col], errors='coerce').fillna(0)

            df_list.append(df_temp)

        except Exception:
            continue

    return pd.concat(df_list, ignore_index=True) if df_list else None


# --- CONVERSION ALCHIMISTE ---
def harmoniser_formats_alc(row):
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']

    if row['Année'] == 2025:
        return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])


# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])

df_raw_all = load_data_from_drive(
    ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP
)

if df_raw_all is not None:

    # --- PRÉ-TRAITEMENT ---
    df_raw_all['DocDate'] = pd.to_datetime(df_raw_all['DocDate'], errors='coerce')
    df_raw_all['DateLivraison'] = pd.to_datetime(df_raw_all['DateLivraison'], errors='coerce')
    df_raw_all['DateAnalyse'] = df_raw_all['DateLivraison'].fillna(df_raw_all['DocDate'])

    df_raw = df_raw_all[df_raw_all['DateAnalyse'].dt.year >= 2025].copy()

    df_raw['Année'] = df_raw['DateAnalyse'].dt.year
    df_raw['Jour_Annee'] = df_raw['DateAnalyse'].dt.dayofyear
    df_raw['Semaine'] = df_raw['DateAnalyse'].dt.isocalendar().week
    df_raw['Mois_Nom'] = df_raw['DateAnalyse'].dt.strftime('%m - %B')

    if page == "Alchimiste":
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats_alc, axis=1)
    else:
        df_raw['CAISSE EQ'] = df_raw['LineQty']

    # --- LOGIQUE ANNÉE DYNAMIQUE ---
    latest_year = df_raw['Année'].max()
    df_latest_year = df_raw[df_raw['Année'] == latest_year]

    previous_year = latest_year - 1
    df_previous_year = df_raw[df_raw['Année'] == previous_year]

    # --- KPI YTD ---
    max_day_latest = df_latest_year['Jour_Annee'].max()
    df_previous_ytd = df_previous_year[df_previous_year['Jour_Annee'] <= max_day_latest]

    st.title(f"📊 Dashboard {page}")

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("📦 Volume (Eq. 12)")
        v1, v2 = st.columns(2)
        v1.metric(f"{latest_year} YTD",
                  f"{df_latest_year['CAISSE EQ'].sum():,.0f}")
        v2.metric(f"{previous_year} YTD",
                  f"{df_previous_ytd['CAISSE EQ'].sum():,.0f}",
                  delta=f"{df_latest_year['CAISSE EQ'].sum() - df_previous_ytd['CAISSE EQ'].sum():,.0f}")

    with c2:
        st.subheader("💰 Ventes ($)")
        s1, s2 = st.columns(2)
        s1.metric(f"{latest_year} YTD",
                  f"{df_latest_year['LineTotal'].sum():,.0f} $")
        s2.metric(f"{previous_year} YTD",
                  f"{df_previous_ytd['LineTotal'].sum():,.0f} $",
                  delta=f"{df_latest_year['LineTotal'].sum() - df_previous_ytd['LineTotal'].sum():,.0f} $")

    # ==========================================================
    # 🔥 NOUVELLE SECTION – DERNIÈRE SEMAINE PAR SKU
    # ==========================================================

    st.divider()
    st.subheader("📦 Ventes par SKU – Dernière semaine disponible")

    current_week = df_latest_year['Semaine'].max()
    df_last_week = df_latest_year[df_latest_year['Semaine'] == current_week]

    sku_last_week = (
        df_last_week
        .groupby('ItemName')
        .agg({
            'CAISSE EQ': 'sum',
            'LineTotal': 'sum'
        })
        .reset_index()
        .rename(columns={
            'ItemName': 'SKU',
            'CAISSE EQ': 'Caisses',
            'LineTotal': 'Ventes ($)'
        })
        .sort_values('Caisses', ascending=False)
    )

    total_row = pd.DataFrame({
        'SKU': ['TOTAL'],
        'Caisses': [sku_last_week['Caisses'].sum()],
        'Ventes ($)': [sku_last_week['Ventes ($)'].sum()]
    })

    sku_last_week = pd.concat([sku_last_week, total_row], ignore_index=True)

    st.dataframe(
        sku_last_week.style.format({
            'Caisses': '{:,.0f}',
            'Ventes ($)': '{:,.2f} $'
        }),
        use_container_width=True,
        hide_index=True
    )

    # ==========================================================
    # VUE MENSUELLE
    # ==========================================================

    st.divider()

    pivot_vol = df_raw.pivot_table(
        index='Mois_Nom',
        columns='Année',
        values='CAISSE EQ',
        aggfunc='sum'
    ).fillna(0)

    pivot_val = df_raw.pivot_table(
        index='Mois_Nom',
        columns='Année',
        values='LineTotal',
        aggfunc='sum'
    ).fillna(0)

    tab_vol, tab_val = st.tabs(["📉 Volume Mensuel", "💵 Argent Mensuel"])

    with tab_vol:
        st.plotly_chart(
            px.line(pivot_vol.reset_index(),
                    x='Mois_Nom',
                    y=pivot_vol.columns,
                    markers=True),
            use_container_width=True
        )
        st.dataframe(pivot_vol.style.format("{:.0f}"), use_container_width=True)

    with tab_val:
        st.plotly_chart(
            px.line(pivot_val.reset_index(),
                    x='Mois_Nom',
                    y=pivot_val.columns,
                    markers=True),
            use_container_width=True
        )
        st.dataframe(pivot_val.style.format("{:,.2f} $"), use_container_width=True)

else:
    st.error("Données introuvables. Vérifiez vos dossiers Drive.")
