import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
import re
from datetime import date, timedelta

st.set_page_config(page_title="Dashboard Alchimiste & LOOP - Master Intégral", layout="wide")

# --- CONFIGURATION DRIVE ---
ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"
ID_DOSSIER_LOOP = "1LOTLoVm4-FJr96FQTOZzICrn-ZJmB4Pb" 

# --- NOUVEAUX DOSSIERS DRIVE : ventes locales (CAD/CSP) et courtier (Keg Access) ---
ID_DOSSIER_VENTES_CAD = "1qebuLamqI6uSwuuNTHCmlFzdhjuLdiC_"
ID_DOSSIER_VENTES_CSP = "1l6EWS68rlIAZt6xdr6IFG15ymlHquB-z"
ID_DOSSIER_KEG_ACCESS = "1RSKQfcfovdGfbwNPiT6TCqOBgYvsyvFm"

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


# ============================================================================
# --- NOUVEAU : CHARGEMENT DES VENTES LOCALES (CAD/CSP) ET COURTIER (KEG ACCESS)
# ============================================================================

def parse_format_caisse_eq(format_str):
    """
    Décode le format d'emballage indiqué dans le Mémo (ex: '473x12', '473x4x6', '20L')
    et retourne (multiplicateur_caisse_eq, litres_par_unite).

    - '473x12'   -> 12 unités/caisse -> 1.0 caisse éq. (base 12), pas de fût
    - '473x24'   -> 24 unités/caisse -> 2.0 caisses éq.
    - '473x4x6'  -> 4 x 6 = 24 unités -> 2.0 caisses éq. (même logique que les 4-Pack existants)
    - '20L'      -> fût : 0 caisse éq., 20 litres/unité (tracké séparément, cf. SKU LITRES)
    """
    s = str(format_str).strip().upper()

    m_fut = re.match(r'^(\d+(?:[.,]\d+)?)\s*L$', s)
    if m_fut:
        litres = float(m_fut.group(1).replace(',', '.'))
        return 0.0, litres

    nombres = [int(n) for n in re.findall(r'\d+', s)]
    if len(nombres) >= 2:
        unites = 1
        for n in nombres[1:]:
            unites *= n
        return unites / 12, None

    return 1.0, None  # format non reconnu : défaut prudent = 1 caisse éq.


# Table de correspondance des noms de produits Keg Access -> nom complet
# (aligné sur les mots-clés de GAMME_RULES pour que get_gamme() classe correctement)
NOMS_BASE_KEG_ACCESS = {
    'BLONDE': 'BLONDE', 'BLANCHE': 'BLANCHE', 'IPA': 'IPA',
    'ECOSSAISE': 'ECOSSAISE', 'DRYSTOUT': 'DRY STOUT',
    'SURE': 'SURE FRAMBOISE', 'ROUSSE': 'ROUSSE',
    'CALIFORNIA': 'CALIFORNIA', 'PILON': 'PILON', 'FLEUR': 'FLEUR',
    'FORET': 'FORÊT', 'PARASOL': 'PARASOL', 'YUKON': 'YUKON',
    'ARIZONA': 'ARIZONA', 'BIGSURF': 'BIG SURF',
}

def decode_keg_access_sku(product_code):
    """
    Décode un code Keg Access (ex: 'ALC-BlondeSG-CS', 'VIL-California-CS', 'ALC-BlondeSASG-CS')
    en (ItemName lisible compatible avec get_gamme(), ItemCode reconstruit).
    Le suffixe '-CS' = caisse de 24 unités -> 2 caisses éq. de 12 (géré à l'ingestion).
    """
    code = str(product_code).strip()
    code_clean = re.sub(r'-CS$', '', code, flags=re.IGNORECASE)
    prefix, _, base = code_clean.partition('-')
    prefix = prefix.upper()

    gamme_prefix = 'VILAINS' if prefix == 'VIL' else 'ALCHIMISTE AUTHENTIQUE'

    # Décodage des suffixes Sans Gluten (SG) / Sans Alcool (SA), peu importe l'ordre
    sg = sa = False
    reste = base
    changed = True
    while changed:
        changed = False
        if reste.upper().endswith('SG'):
            sg = True
            reste = reste[:-2]
            changed = True
        elif reste.upper().endswith('SA'):
            sa = True
            reste = reste[:-2]
            changed = True

    nom_produit = NOMS_BASE_KEG_ACCESS.get(reste.upper(), reste.upper())
    suffixe = (' SANS GLUTEN' if sg else '') + (' SANS ALCOOL' if sa else '')
    item_name = f"{gamme_prefix} - {nom_produit}{suffixe}"
    item_code = f"KEG-{code_clean.upper()}"
    return item_name, item_code


def _read_excel_any(content, filename):
    """Lit un contenu Excel binaire (.xls ancien format ou .xlsx) sans en-tête."""
    if filename.lower().endswith('.xls'):
        return pd.read_excel(io.BytesIO(content), header=None, engine='xlrd')
    return pd.read_excel(io.BytesIO(content), header=None, engine='openpyxl')


def _date_from_filename_or_meta(item):
    """Essaie d'extraire une date du nom de fichier (AAAA-MM-JJ), sinon retombe sur modifiedTime."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', item.get('name', ''))
    if m:
        return pd.to_datetime(m.group(1))
    return pd.to_datetime(item.get('modifiedTime'), errors='coerce')


@st.cache_data(ttl=600)
def load_ventes_reseau_from_drive(folder_id):
    """
    Charge et normalise les rapports 'VENTES CAD' / 'VENTES CSP' (ventes locales).
    Le réseau (CAD ou CSP) et le SKU sont encodés dans la colonne Mémo/description,
    au format 'RESEAU // GAMME // PRODUIT // FORMAT' (ex: 'CAD // ALCHIMISTE AUTHENTIQUE // BLONDE // 473x12').

    Retourne (DataFrame ou None, liste de diagnostics par fichier) pour faciliter le dépannage.
    """
    service = get_gdrive_service()
    debug = []
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        items = service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives',
        ).execute().get('files', [])
    except Exception as e:
        return None, [{'fichier': '(accès dossier)', 'statut': f"❌ Erreur d'accès au dossier Drive : {e}"}]

    if not items:
        return None, [{'fichier': '(dossier)', 'statut': "❌ Dossier vide ou introuvable (0 fichier retourné par l'API)."}]

    fichiers_valides = [
        i for i in items
        if i['name'].lower().endswith(('.xls', '.xlsx'))
        or i.get('mimeType') == 'application/vnd.google-apps.spreadsheet'
    ]
    if not fichiers_valides:
        noms = [f"{i['name']} ({i.get('mimeType', '?')})" for i in items]
        return None, [{'fichier': '(dossier)', 'statut': f"❌ {len(items)} fichier(s) trouvé(s) mais aucun .xls/.xlsx reconnu : {noms}"}]

    lignes = []
    for item in fichiers_valides:
        try:
            mime = item.get('mimeType', '')
            if mime == 'application/vnd.google-apps.spreadsheet':
                # Fichier auto-converti en Google Sheet par Drive : il faut l'exporter, pas le télécharger
                content = service.files().export(
                    fileId=item['id'],
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                ).execute()
                df_sheet = pd.read_excel(io.BytesIO(content), header=None, engine='openpyxl')
            else:
                content = service.files().get_media(fileId=item['id']).execute()
                df_sheet = _read_excel_any(content, item['name'])

            # Ligne d'en-tête = celle dont la colonne 1 vaut exactement "Date"
            header_matches = df_sheet.index[df_sheet[1].astype(str).str.strip() == 'Date']
            if len(header_matches) == 0:
                debug.append({'fichier': item['name'], 'statut': "⚠️ Ligne d'en-tête 'Date' introuvable en colonne B — structure inattendue."})
                continue
            header_row = header_matches[0]

            df_data = df_sheet.iloc[header_row + 1:].copy()
            df_data.columns = [str(c).strip() for c in df_sheet.iloc[header_row]]
            df_data = df_data.dropna(subset=['Date'])

            if 'Mémo/description' not in df_data.columns:
                debug.append({'fichier': item['name'], 'statut': f"⚠️ Colonne 'Mémo/description' introuvable. Colonnes lues : {list(df_data.columns)}"})
                continue

            lignes_avant = len(lignes)
            lignes_ignorees = 0
            for _, row in df_data.iterrows():
                memo = str(row.get('Mémo/description', ''))
                parts = [p.strip() for p in memo.split('//')]
                if len(parts) < 4:
                    lignes_ignorees += 1
                    continue
                reseau, gamme_txt, produit_txt, format_txt = parts[0], parts[1], parts[2], parts[3]

                mult_caisse, litres_unite = parse_format_caisse_eq(format_txt)
                qte = pd.to_numeric(row.get('Qté', 0), errors='coerce')
                qte = 0 if pd.isna(qte) else qte
                montant = pd.to_numeric(row.get('Montant', 0), errors='coerce')
                montant = 0 if pd.isna(montant) else montant

                item_name = f"{gamme_txt} - {produit_txt}"
                item_code = f"{reseau}-{produit_txt}-{format_txt}".upper().replace(' ', '')

                lignes.append({
                    'DocDate': row.get('Date'),
                    'DateLivraison': pd.NaT,
                    'ItemCode': item_code,
                    'ItemName': item_name,
                    'CardName': row.get('Client', ''),
                    'GroupName': reseau,
                    'LineQty': qte,
                    'LineTotal': montant,
                    'CAISSE_EQ_PRECALC': qte * mult_caisse,
                    'Litres': qte * litres_unite if litres_unite else 0.0,
                    'Source': reseau,
                })
            statut = f"✅ {len(lignes) - lignes_avant} ligne(s) importée(s)"
            if lignes_ignorees:
                statut += f" ({lignes_ignorees} ligne(s) ignorée(s), Mémo mal formé)"
            debug.append({'fichier': item['name'], 'statut': statut})
        except Exception as e:
            debug.append({'fichier': item.get('name', '?'), 'statut': f"❌ Erreur de lecture : {e}"})
            continue

    return (pd.DataFrame(lignes) if lignes else None), debug


@st.cache_data(ttl=600)
def load_keg_access_from_drive(folder_id):
    """
    Charge et normalise les rapports Keg Access (ventes CSP via courtier).
    Pas de date par ligne dans ce rapport : on utilise la date du nom de fichier
    si présente (format AAAA-MM-JJ), sinon la date de dernière modification du fichier Drive.

    Retourne (DataFrame ou None, liste de diagnostics par fichier) pour faciliter le dépannage.
    """
    service = get_gdrive_service()
    debug = []
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        items = service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives',
        ).execute().get('files', [])
    except Exception as e:
        return None, [{'fichier': '(accès dossier)', 'statut': f"❌ Erreur d'accès au dossier Drive : {e}"}]

    if not items:
        return None, [{'fichier': '(dossier)', 'statut': "❌ Dossier vide ou introuvable (0 fichier retourné par l'API)."}]

    fichiers_valides = [
        i for i in items
        if i['name'].lower().endswith('.xlsx')
        or i.get('mimeType') == 'application/vnd.google-apps.spreadsheet'
    ]
    if not fichiers_valides:
        noms = [f"{i['name']} ({i.get('mimeType', '?')})" for i in items]
        return None, [{'fichier': '(dossier)', 'statut': f"❌ {len(items)} fichier(s) trouvé(s) mais aucun .xlsx reconnu : {noms}"}]

    lignes = []
    for item in fichiers_valides:
        try:
            mime = item.get('mimeType', '')
            if mime == 'application/vnd.google-apps.spreadsheet':
                content = service.files().export(
                    fileId=item['id'],
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                ).execute()
            else:
                content = service.files().get_media(fileId=item['id']).execute()
            df_sheet = pd.read_excel(io.BytesIO(content), header=None, engine='openpyxl')

            header_matches = df_sheet.index[
                df_sheet.apply(lambda r: r.astype(str).str.contains('Account Name', na=False).any(), axis=1)
            ]
            if len(header_matches) == 0:
                debug.append({'fichier': item['name'], 'statut': "⚠️ En-tête 'Account Name' introuvable — structure inattendue."})
                continue
            header_row = header_matches[0]

            df_data = df_sheet.iloc[header_row + 1:].copy()
            df_data.columns = [str(c).replace('↑', '').strip() for c in df_sheet.iloc[header_row]]

            if 'Account Name' not in df_data.columns or 'Product: Product Name' not in df_data.columns:
                debug.append({'fichier': item['name'], 'statut': f"⚠️ Colonnes attendues introuvables. Colonnes lues : {list(df_data.columns)}"})
                continue

            df_data['Account Name'] = df_data['Account Name'].ffill()
            df_data = df_data[df_data['Account Name'].astype(str).str.strip().str.upper() != 'GRAND TOTAL']

            date_fichier = _date_from_filename_or_meta(item)
            lignes_avant = len(lignes)

            for _, row in df_data.iterrows():
                code = row.get('Product: Product Name')
                if pd.isna(code) or not str(code).strip():
                    continue
                item_name, item_code = decode_keg_access_sku(code)
                qte = pd.to_numeric(row.get('Quantity (#)', 0), errors='coerce')
                qte = 0 if pd.isna(qte) else qte
                montant = pd.to_numeric(row.get('Invoice Line Item Total (#)', 0), errors='coerce')
                montant = 0 if pd.isna(montant) else montant

                lignes.append({
                    'DocDate': date_fichier,
                    'DateLivraison': pd.NaT,
                    'ItemCode': item_code,
                    'ItemName': item_name,
                    'CardName': row.get('Account Name', ''),
                    'GroupName': 'Courtier (Keg Access)',
                    'LineQty': qte,
                    'LineTotal': montant,
                    'CAISSE_EQ_PRECALC': qte * 2,  # '-CS' = caisse de 24 -> 2 caisses éq. de 12
                    'Litres': 0.0,
                    'Source': 'KegAccess',
                })
            debug.append({'fichier': item['name'], 'statut': f"✅ {len(lignes) - lignes_avant} ligne(s) importée(s)"})
        except Exception as e:
            debug.append({'fichier': item.get('name', '?'), 'statut': f"❌ Erreur de lecture : {e}"})
            continue

    return (pd.DataFrame(lignes) if lignes else None), debug


# --- LISTE EXPLICITE DES CODES 4-PACK À DOUBLER ---
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

# --- MAPPING GAMMES PAR MOTS-CLÉS (ordre important : du plus spécifique au plus général) ---
# Chaque entrée : (sous-chaîne à chercher dans ItemName en majuscules, gamme assignée)
# L'ordre est crucial : Sans Gluten et Sans Alcool avant les autres pour éviter les faux positifs.
GAMME_RULES = [
    # Sans Gluten (priorité max — contient souvent "SANS ALCOOL" aussi)
    ('SANS GLUTEN',         'Sans Gluten'),
    # Sans Alcool
    ('SANS ALCOOL',         'Sans Alcool'),
    # 4 Pack (standalone, pas Vilains ni Authentique)
    ('4 PACK',              '4 Pack'),
    # Quatuor
    ('QUATUOR',             'Quatuor'),
    # Vilains — noms de produits spécifiques à cette gamme
    ('PILON',               'Vilains'),
    ('CALIFORNIA',          'Vilains'),
    ('FLEUR',               'Vilains'),
    ('FORÊT',               'Vilains'),
    ('FORET',               'Vilains'),
    ('PARASOL',             'Vilains'),
    ('YUKON',               'Vilains'),
    ('ARIZONA',             'Vilains'),
    ('BIG SURF',            'Vilains'),
    # Autre — produits spéciaux/saisonniers
    ('BLONDE CLASSIQUE',    'Autre'),
    ('CABANA',              'Autre'),
    ('IPA SESSION',         'Autre'),
    ('MANGUE',              'Autre'),
    ('PLUME',               'Autre'),
    ('PÊCHE',               'Autre'),
    ('PECHE',               'Autre'),
    ('TOKYO',               'Autre'),
    # Projet Tropical : Autre si 4 pack (règle 4 PACK déjà capturée plus haut),
    # Vilains si caisse de 12 — on arrive ici seulement si "4 PACK" n'a pas matché
    ('PROJET TROPIC',       'Vilains'),
    ('PROJET TROPICAL',     'Vilains'),
    # Authentique — tout le reste de la gamme principale
    ('BOCK',                'Authentique'),
    ('BLONDE',              'Authentique'),
    ('DRY STOUT',           'Authentique'),
    ('ECOSSAISE',           'Authentique'),
    ('ÉCOSSAISE',           'Authentique'),
    ('IPA',                 'Authentique'),
    ('BITTER',              'Authentique'),
    ('BLANCHE',             'Authentique'),
    ('GOSE',                'Authentique'),
    ('ROUSSE',              'Authentique'),
    ('PALE ALE',            'Authentique'),
    ('SURE FRAMBOISE',      'Authentique'),
]


def get_gamme(item_name):
    """Retourne la gamme d'un ItemName via correspondance par mots-clés."""
    name_up = str(item_name).strip().upper()
    for keyword, gamme in GAMME_RULES:
        if keyword.upper() in name_up:
            return gamme
    return 'Non classé'


# --- LOGIQUE DE CONVERSION ---
def harmoniser_formats_alc(row):
    # Nouvelles sources (CAD/CSP/Keg Access) : Caisse Éq. déjà calculée à l'ingestion
    if row.get('Source') in ('CAD', 'CSP', 'KegAccess'):
        return pd.Series([row['CAISSE_EQ_PRECALC'], row['ItemCode']])

    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        if code in CODES_4PACK:
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])
    else:
        if code.endswith('SG4P') or code in CODES_4PACK or (not code.endswith('12')):
            return pd.Series([qty * 2, code])
        return pd.Series([qty, code])

# --- CORRECTION SKU SANS ALCOOL (BLONDE vs BLANCHE) ---
def corriger_sku_sans_alcool(row):
    code = str(row['ItemCode']).strip().upper()
    name = str(row['ItemName']).strip()
    if 'SANS ALCOOL' not in name.upper():
        return name
    if code == 'MABLONSA12':
        return re.sub(r'SANS ALCOOL\s*B?', 'BLONDE SANS ALCOOL', name, flags=re.IGNORECASE).strip()
    elif code == 'MABLANSA12':
        return re.sub(r'SANS ALCOOL\s*B?', 'BLANCHE SANS ALCOOL', name, flags=re.IGNORECASE).strip()
    return name

# --- AFFICHAGE D'UNE SECTION DÉDIÉE À UN CANAL (CAD / CSP / Keg Access) ---
def render_section_canal(df_canal, titre, icone, date_range, debug_info=None):
    """
    Affiche une section autonome pour un canal de vente donné.
    Ne touche à aucune donnée du pipeline ERP principal — totalement indépendant.
    """
    st.divider()
    st.header(f"{icone} {titre}")

    if df_canal is None or df_canal.empty:
        st.info(f"Aucune donnée trouvée pour « {titre} ».")
        if debug_info:
            with st.expander("🔍 Diagnostic (pourquoi aucune donnée ?)", expanded=True):
                st.dataframe(pd.DataFrame(debug_info), use_container_width=True, hide_index=True)
        else:
            st.caption("Vérifiez que le dossier Drive est bien configuré (ID renseigné + accès partagé avec le compte de service) et qu'il contient des fichiers.")
        return

    if debug_info:
        with st.expander("🔍 Diagnostic des fichiers chargés"):
            st.dataframe(pd.DataFrame(debug_info), use_container_width=True, hide_index=True)

    df_c = df_canal.copy()
    df_c['DocDate'] = pd.to_datetime(df_c['DocDate'], errors='coerce')
    df_c['Gamme'] = df_c['ItemName'].apply(get_gamme)
    df_c = df_c.rename(columns={'CAISSE_EQ_PRECALC': 'Caisse Eq.'})

    # Filtre par la même plage de dates que le reste du dashboard (sidebar)
    if isinstance(date_range, tuple) and len(date_range) == 2:
        mask = (df_c['DocDate'].dt.date >= date_range[0]) & (df_c['DocDate'].dt.date <= date_range[1])
        df_c = df_c[mask]

    if df_c.empty:
        st.info(f"Aucune donnée pour « {titre} » sur la période sélectionnée dans la barre latérale.")
        return

    total_caisses = df_c['Caisse Eq.'].sum()
    total_dollars = df_c['LineTotal'].sum()
    total_litres = df_c['Litres'].sum() if 'Litres' in df_c.columns else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Caisses Éq.", f"{total_caisses:,.0f}")
    k2.metric("Total Ventes", f"{total_dollars:,.2f} $")
    if total_litres > 0:
        k3.metric("Total Litres (fûts)", f"{total_litres:,.0f} L")

    gammes_dispo = sorted(df_c['Gamme'].unique().tolist())
    gamme_sel = st.selectbox(
        "Filtrer par gamme", ['Toutes les gammes'] + gammes_dispo,
        key=f"gamme_{titre}"
    )
    if gamme_sel != 'Toutes les gammes':
        df_c = df_c[df_c['Gamme'] == gamme_sel]

    tab_sku, tab_clients = st.tabs(["📦 Par SKU", "👥 Par client"])
    with tab_sku:
        sku_tbl = (
            df_c.groupby(['ItemName', 'Gamme'])
            .agg({'Caisse Eq.': 'sum', 'LineTotal': 'sum'})
            .reset_index()
            .rename(columns={'ItemName': 'SKU', 'LineTotal': 'Ventes ($)'})
            .sort_values('Ventes ($)', ascending=False)
        )
        st.dataframe(
            sku_tbl.style.format({'Caisse Eq.': '{:.2f}', 'Ventes ($)': '{:,.2f} $'}),
            use_container_width=True, hide_index=True
        )
    with tab_clients:
        client_tbl = (
            df_c.groupby('CardName')
            .agg({'Caisse Eq.': 'sum', 'LineTotal': 'sum'})
            .reset_index()
            .rename(columns={'CardName': 'Client', 'LineTotal': 'Ventes ($)'})
            .sort_values('Ventes ($)', ascending=False)
        )
        st.dataframe(
            client_tbl.style.format({'Caisse Eq.': '{:.2f}', 'Ventes ($)': '{:,.2f} $'}),
            use_container_width=True, hide_index=True
        )


# --- EXCEL PRO ---
def generate_styled_excel(df_week_comp, pivot_vol, pivot_val, pivot_sku, pivot_banner):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        fmt_money = workbook.add_format({'num_format': '#,##0.00 $'})
        fmt_qty   = workbook.add_format({'num_format': '#,##0'})
        fmt_perc  = workbook.add_format({'num_format': '0.0%'})
        fmt_text  = workbook.add_format({'bold': False})

        def save_sheet(df, name, is_money=False, add_row_total=True, with_gamme=False):
            df_t = df.copy()
            # Injecter colonne Gamme en 1ère position (avant les données)
            if with_gamme:
                df_t.insert(0, 'Gamme', df_t.index.map(get_gamme))
            if add_row_total:
                df_t.loc['TOTAL GLOBAL'] = df_t.sum(numeric_only=True)
            df_t.to_excel(writer, sheet_name=name)
            ws = writer.sheets[name]
            ws.set_column(0, 0, 42)  # colonne index (ItemName)
            for i, col in enumerate(df_t.columns):
                excel_col = i + 1
                if col == 'Gamme':
                    ws.set_column(excel_col, excel_col, 18, fmt_text)
                else:
                    f = fmt_perc if 'Variation %' in str(col) else (fmt_money if is_money else fmt_qty)
                    ws.set_column(excel_col, excel_col, 20, f)

        # Onglets SKU — colonne Gamme ajoutée
        save_sheet(df_week_comp, 'Comparaison Semaine', add_row_total=True,  with_gamme=True)
        save_sheet(pivot_sku,    'Détail SKU 2026',     add_row_total=True,  with_gamme=True)
        # Onglets mensuels — index = Mois_Nom, pas de Gamme
        save_sheet(pivot_vol,    'Vol Mensuel YOY',     add_row_total=False, with_gamme=False)
        save_sheet(pivot_val,    'Dollars Mensuels YOY',is_money=True, add_row_total=False, with_gamme=False)
        # Bannières — index = GroupName, pas de Gamme
        save_sheet(pivot_banner, 'Bannières 2026',      add_row_total=True,  with_gamme=False)
    return output.getvalue()

# --- MAIN APP ---
st.sidebar.title("🍺 Navigation Master")
if st.sidebar.button("🔄 Forcer le rechargement des données (vider le cache)"):
    st.cache_data.clear()
    st.rerun()
page = st.sidebar.radio("Marque :", ["Alchimiste", "LOOP"])
df_raw_all = load_data_from_drive(ID_DOSSIER_ALCHIMISTE if page == "Alchimiste" else ID_DOSSIER_LOOP)

if df_raw_all is not None:
    # --- CHARGEMENT DES 3 CANAUX SUPPLÉMENTAIRES (Alchimiste seulement) ---
    # NOTE : ces canaux sont volontairement gardés SÉPARÉS du pipeline ERP (df_raw_all)
    # pour éviter tout risque de double comptage si ces ventes sont aussi déjà
    # présentes dans l'export ERP. Ils sont affichés dans leurs propres sections
    # plus bas dans la page plutôt que mélangés aux totaux/graphiques existants.
    df_cad, debug_cad = None, None
    df_csp, debug_csp = None, None
    df_keg, debug_keg = None, None
    if page == "Alchimiste":
        df_cad, debug_cad = load_ventes_reseau_from_drive(ID_DOSSIER_VENTES_CAD)
        df_csp, debug_csp = load_ventes_reseau_from_drive(ID_DOSSIER_VENTES_CSP)
        df_keg, debug_keg = load_keg_access_from_drive(ID_DOSSIER_KEG_ACCESS)

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

    # --- CORRECTION SKU SANS ALCOOL ---
    if 'ItemCode' in df_raw.columns and 'ItemName' in df_raw.columns:
        df_raw['ItemName'] = df_raw.apply(corriger_sku_sans_alcool, axis=1)

    # --- AJOUT COLONNE GAMME ---
    df_raw['Gamme'] = df_raw['ItemName'].apply(get_gamme)

    # --- FILTRES SIDEBAR ---
    st.sidebar.divider()
    
    start_ytd = date(2026, 1, 1) if page == "Alchimiste" else date(2025, 11, 1)
    
    if "date_range" not in st.session_state or st.session_state.get("last_page") != page:
        st.session_state["date_range"] = (start_ytd, date.today())
        st.session_state["last_page"] = page

    if st.sidebar.button("🔄 Reset YTD"):
        st.session_state["date_range"] = (start_ytd, date.today())

    date_sel = st.sidebar.date_input("Analyse détaillée (Graphs)", value=st.session_state["date_range"], key="date_range")

    # --- FILTRE DATE ---
    df_filtered = df_raw.copy()
    if isinstance(date_sel, tuple) and len(date_sel) == 2:
        mask = (
            (df_raw['DateAnalyse'].dt.date >= date_sel[0]) &
            (df_raw['DateAnalyse'].dt.date <= date_sel[1])
        )
        df_filtered = df_raw[mask].copy()

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

        # --- FILTRE GAMME : SEMAINE ---
        gammes_disponibles_sem = sorted(df_derniere_semaine['Gamme'].unique().tolist())
        options_sem = ['Toutes les gammes'] + gammes_disponibles_sem
        col_titre_sem, col_filtre_sem = st.columns([3, 2])
        with col_titre_sem:
            st.subheader(f"Semaine #{int(derniere_semaine)} — 2026")
        with col_filtre_sem:
            gamme_sel_sem = st.selectbox(
                "Filtrer par gamme",
                options=options_sem,
                index=0,
                key="filtre_gamme_semaine"
            )

        if gamme_sel_sem != 'Toutes les gammes':
            df_derniere_semaine = df_derniere_semaine[df_derniere_semaine['Gamme'] == gamme_sel_sem]

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
            gamme_label = f" — {gamme_sel_sem}" if gamme_sel_sem != 'Toutes les gammes' else ""
            st.metric("Gamme", gamme_label if gamme_label else "Toutes")
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

    # --- FILTRE GAMME : YTD ---
    gammes_disponibles_ytd = sorted(
        set(df_2026_full['Gamme'].unique().tolist() + df_2025_ytd['Gamme'].unique().tolist())
    )
    options_ytd = ['Toutes les gammes'] + gammes_disponibles_ytd
    col_titre_ytd, col_filtre_ytd = st.columns([3, 2])
    with col_titre_ytd:
        st.subheader("Comparaison YTD 2025 vs 2026")
    with col_filtre_ytd:
        gamme_sel_ytd = st.selectbox(
            "Filtrer par gamme",
            options=options_ytd,
            index=0,
            key="filtre_gamme_ytd"
        )

    df_2026_ytd_filtered = df_2026_full.copy()
    df_2025_ytd_filtered = df_2025_ytd.copy()

    if gamme_sel_ytd != 'Toutes les gammes':
        df_2026_ytd_filtered = df_2026_ytd_filtered[df_2026_ytd_filtered['Gamme'] == gamme_sel_ytd]
        df_2025_ytd_filtered = df_2025_ytd_filtered[df_2025_ytd_filtered['Gamme'] == gamme_sel_ytd]

    sku_2026_ytd = df_2026_ytd_filtered.groupby('ItemName')['CAISSE EQ'].sum()
    sku_2025_ytd_val = df_2025_ytd_filtered.groupby('ItemName')['CAISSE EQ'].sum()
    sku_yoy = pd.DataFrame({'2025 (YTD)': sku_2025_ytd_val, '2026 (YTD)': sku_2026_ytd}).fillna(0)
    sku_yoy['Variation'] = sku_yoy['2026 (YTD)'] - sku_yoy['2025 (YTD)']
    st.dataframe(
        sku_yoy.sort_values('2026 (YTD)', ascending=False)
              .style.format("{:.0f}")
              .bar(subset=['Variation'], align='mid', color=['#ff9999', '#99ff99']),
        use_container_width=True
    )

    # --- SECTIONS INDÉPENDANTES PAR CANAL (CAD / CSP / Courtier) ---
    if page == "Alchimiste":
        render_section_canal(df_cad, "Ventes locales CAD", "🏠", date_sel, debug_cad)
        render_section_canal(df_csp, "Ventes locales CSP", "🏠", date_sel, debug_csp)
        render_section_canal(df_keg, "Ventes via courtier (Keg Access)", "🤝", date_sel, debug_keg)

else:
    st.error("Données introuvables. Vérifiez vos dossiers Drive.")

# --- PRIX DE VENTE MOYEN PAR GAMME ---
st.divider()
st.header("💲 Prix de vente moyen par gamme")

def calc_prix_gamme(df: pd.DataFrame) -> pd.DataFrame:
    """Retourne le prix moyen par caisse équivalente, groupé par Gamme."""
    df_p = df[df['CAISSE EQ'] > 0].copy()
    return (
        df_p.groupby('Gamme')
        .apply(lambda g: g['LineTotal'].sum() / g['CAISSE EQ'].sum())
        .reset_index(name='Prix / Caisse éq.')
    )

prix_2026 = calc_prix_gamme(df_2026_full)
prix_2025 = calc_prix_gamme(df_2025_ytd)

prix_compare = (
    prix_2026
    .merge(prix_2025, on='Gamme', suffixes=(' 2026', ' 2025'), how='outer')
    .fillna(0)
    .sort_values('Prix / Caisse éq. 2026', ascending=False)
)
prix_compare['Variation $'] = (
    prix_compare['Prix / Caisse éq. 2026'] - prix_compare['Prix / Caisse éq. 2025']
)
prix_compare['Variation %'] = prix_compare['Variation $'] / (
    prix_compare['Prix / Caisse éq. 2025'].replace(0, pd.NA)
)

# --- Graphique comparatif groupé ---
df_chart_prix = prix_compare.melt(
    id_vars='Gamme',
    value_vars=['Prix / Caisse éq. 2025', 'Prix / Caisse éq. 2026'],
    var_name='Année',
    value_name='Prix ($/caisse éq.)'
)
# Masquer les gammes absentes d'une année (prix == 0)
df_chart_prix = df_chart_prix[df_chart_prix['Prix ($/caisse éq.)'] > 0]

fig_prix = px.bar(
    df_chart_prix,
    x='Gamme',
    y='Prix ($/caisse éq.)',
    color='Année',
    barmode='group',
    text_auto='.2f',
    color_discrete_map={
        'Prix / Caisse éq. 2025': '#636EFA',
        'Prix / Caisse éq. 2026': '#00CC96'
    },
    title=f"Prix moyen par caisse équivalente (YTD — jusqu'au jour {int(max_day_2026)})"
)
fig_prix.update_traces(texttemplate='%{y:.2f} $', textposition='outside')
fig_prix.update_layout(yaxis_ticksuffix=' $', uniformtext_minsize=9, uniformtext_mode='hide')
st.plotly_chart(fig_prix, use_container_width=True)

# --- Tableau détaillé avec variation ---
st.dataframe(
    prix_compare[
        ['Gamme', 'Prix / Caisse éq. 2025', 'Prix / Caisse éq. 2026', 'Variation $', 'Variation %']
    ].style.format({
        'Prix / Caisse éq. 2025': '{:.2f} $',
        'Prix / Caisse éq. 2026': '{:.2f} $',
        'Variation $':            lambda v: f"{v:+.2f} $",
        'Variation %':            lambda v: f"{v:+.1%}" if pd.notna(v) else 'N/A',
    }).bar(
        subset=['Variation $'],
        align='mid',
        color=['#ff9999', '#99ff99']
    ),
    use_container_width=True,
    hide_index=True
)
