import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io

st.set_page_config(page_title="Alchimiste - Analyse Comparative YoY", layout="wide")

ID_DOSSIER = "1kclIHYXAdBV-Jzi_0ymmycqCUryil5oA"

def get_gdrive_service():
    creds_dict = st.secrets["connections"]["gcs"]
    return build('drive', 'v3', credentials=service_account.Credentials.from_service_account_info(creds_dict))

@st.cache_data(ttl=600)
def load_data_from_drive(folder_id):
    service = get_gdrive_service()
    query = f"'{folder_id}' in parents and name contains '.csv' and trashed = false"
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    if not items: return None
    df_list = []
    for item in items:
        content = service.files().get_media(fileId=item['id']).execute().decode('latin1')
        df_temp = pd.read_csv(io.StringIO(content), sep=',', quotechar='"', on_bad_lines='skip', skip_blank_lines=True)
        df_list.append(df_temp)
    return pd.concat(df_list, ignore_index=True)

# --- LOGIQUE DE FUSION ET CONVERSION ---
def harmoniser_formats(row):
    code = str(row['ItemCode']).strip()
    qty = row['LineQty']
    
    # 1. Calcul de l'équivalent 24 (La règle du "12")
    if code.endswith('12'):
        caisse_eq = qty * 0.5
        sku_base = code[:-2] # On retire le '12' pour fusionner avec l'ancien code
    else:
        caisse_eq = qty
        sku_base = code
        
    return pd.Series([caisse_eq, sku_base])

try:
    df_raw = load_data_from_drive(ID_DOSSIER)
    if df_raw is not None:
        df_raw['DocDate'] = pd.to_datetime(df_raw['DocDate'], errors='coerce')
        df_raw = df_raw.dropna(subset=['DocDate'])
        df_raw['LineQty'] = pd.to_numeric(df_raw['LineQty'], errors='coerce').fillna(0)
        
        # Application de la transformation
        df_raw[['CAISSE EQ', 'SKU_BASE']] = df_raw.apply(harmoniser_formats, axis=1)
        
        # Dimensions temporelles
        df_raw['Année'] = df_raw['DocDate'].dt.year
        df_raw['Mois_Num'] = df_raw['DocDate'].dt.month
        
        st.title("📊 Analyse Comparative Alchimiste")
        st.info("💡 Note : Tous les volumes sont convertis en **Équivalent 24 canettes** (les codes finissant par '12' sont comptés pour 0.5).")

        # --- 1. GRAPHIQUE YoY ---
        st.header("📈 Évolution Mensuelle (YoY)")
        yoy_pivot = df_raw.pivot_table(index='Mois_Num', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        
        if len(yoy_pivot.columns) >= 2:
            fig_yoy = px.line(yoy_pivot.reset_index(), x='Mois_Num', y=yoy_pivot.columns, 
                             markers=True, labels={'value': 'Caisses EQ 24', 'Mois_Num': 'Mois'})
            st.plotly_chart(fig_yoy, use_container_width=True)

        # --- 2. TABLEAU DE PERFORMANCE SKU FUSIONNÉ ---
        st.header("📦 Performance par Produit (Formats 12 & 24 combinés)")
        # On regroupe par SKU_BASE pour que MABLON et MABLON12 soient sur la même ligne
        sku_compare = df_raw.pivot_table(index='SKU_BASE', columns='Année', values='CAISSE EQ', aggfunc='sum').fillna(0)
        
        # Ajout de la colonne croissance si on a 2 années
        if len(sku_compare.columns) >= 2:
            cols = sorted(sku_compare.columns)
            sku_compare['Croissance Absolue'] = sku_compare[cols[-1]] - sku_compare[cols[-2]]
            st.dataframe(sku_compare.sort_values(cols[-1], ascending=False), use_container_width=True)
        else:
            st.dataframe(sku_compare, use_container_width=True)

        # --- 3. EXPORT EXCEL ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            sku_compare.to_excel(writer, sheet_name='Performance_Produits')
            yoy_pivot.to_excel(writer, sheet_name='Ventes_Mensuelles')
            
        st.sidebar.divider()
        st.sidebar.download_button("📥 Télécharger Rapport Comparatif", output.getvalue(), "Analyse_YoY_Alchimiste.xlsx")

except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
