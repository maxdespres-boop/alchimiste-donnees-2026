"""
Portrait magasin par magasin pour un acquéreur ou un partenaire.

Produit, sur une période donnée (par défaut les 12 derniers mois), une ligne par magasin avec :
- le volume net livré/facturé (retours inclus) en litres, HL, caisses métriques (24 x 341 ml) et caisses éq. 12 ;
- la répartition bière avec alcool / sans alcool, sans gluten / régulier, Alchimiste / Vilains ;
- la tranche de volume demandée par l'acquéreur (moins de 50, 50 à 119, 120 caisses métriques et plus) ;
- la catégorie de canal (IGA, Métro, Pasquier, dépanneurs corporatifs, dépanneurs indépendants, etc.) ;
- les caisses gratuites et le rabais moyen facturé, utiles pour estimer les investissements.

Utilisé par streamlit_app.py (bouton « Portrait magasins ») et par scripts/portrait_magasins_cli.py.
Ce module ne dépend pas de Streamlit.
"""

import io
import re

import pandas as pd

# Une caisse métrique = 24 bouteilles de 341 ml (unité utilisée par les acquéreurs et les bannières).
LITRES_CAISSE_METRIQUE = 24 * 0.341
# Une caisse éq. 12 de l'app = 12 canettes de 473 ml.
LITRES_CAISSE_EQ12 = 12 * 0.473

# Tranches demandées par l'acquéreur, en caisses métriques par année.
TRANCHES = [
    (0, 50, 'Moins de 50 cs (< 4 HL)'),
    (50, 120, '50 à 119 cs (4 à 9 HL)'),
    (120, float('inf'), '120 cs et plus (10 HL et +)'),
]

# --- CLASSIFICATION DES CANAUX ---------------------------------------------------------------
# Bannières (GroupName) considérées comme des dépanneurs indépendants ou regroupements de dépanneurs.
# À ajuster au besoin : tout GroupName absent de ces listes tombe dans « Autre (à classer) ».
GROUPES_DEP_INDEPENDANTS = {
    'AMDEQ', 'VOISIN NE DROP PAS', 'BONISOIR NE DROP PAS', 'INTER', 'DEP 7 JOURS', 'DEP. LECAVALIER',
    'PROVI-SOIR', 'AXEP', 'SAGAMIE', 'PETITE SURFACE SOBEY',
}
GROUPES_EPICERIES_SPECIALISEES = {'SPECIALISE', 'AVRIL', 'GROUPE EPICIA', 'ADONIS', 'EUROMARCHE'}
# Mots-clés dans le nom d'un client « INDEPENDANT » en petite surface qui indiquent un dépanneur.
MOTS_DEPANNEUR = ('DEP ', 'DEP.', 'DÉP', 'DEPANNEUR', 'DÉPANNEUR', 'TABAGIE', 'VARIETE', 'VARIÉTÉ',
                  'BEAUSOIR', 'BONISOIR', 'VOISIN', 'PROVISOIR', 'PROVI-SOIR', 'BONSOIR')


def _txt(v):
    return '' if pd.isna(v) else str(v).upper().strip()


def classer_canal(row):
    """Retourne (catégorie, sous-catégorie) selon les questions de l'acquéreur."""
    groupe = _txt(row.get('GroupName'))
    nom = _txt(row.get('CardName'))
    payeur = _txt(row.get('ParentPayeur'))
    surface = _txt(row.get('Surface'))
    tout = f"{groupe} | {nom} | {payeur}"

    # Dépanneurs corporatifs (question 6) — testés en premier
    if 'FILGO' in tout:
        return 'Dépanneurs corporatifs', 'Filgo'
    if 'SPRINT' in tout or 'DUFRESNE' in tout:
        return 'Dépanneurs corporatifs', 'Groupe Dufresne (Sprint)'
    if 'MARCHE EXPRESS' in tout or 'MARCHÉ EXPRESS' in tout or 'PARKLAND' in tout:
        return 'Dépanneurs corporatifs', 'Parkland (Marché Express)'
    if 'COUCHE-TARD' in tout or 'COUCHE TARD' in tout or 'CIRCLE K' in tout:
        return 'Dépanneurs corporatifs', 'Couche-Tard'
    if 'HARNOIS' in tout:
        return 'Dépanneurs corporatifs', 'Harnois'

    # Grandes surfaces
    if 'PASQUIER' in tout:
        return 'Pasquier', 'Pasquier'
    if 'COSTCO' in tout:
        return 'Costco', 'Costco'
    if 'SUPER C' in nom:
        return 'Escompte', 'Super C'
    if groupe == 'MAXI' or nom.startswith('MAXI'):
        return 'Escompte', 'Maxi'
    if groupe == 'PROVIGO':
        return 'Loblaw', 'Provigo'
    if 'RACHELLE' in nom and 'BERY' in nom:
        return 'Sobeys - autres', 'Rachelle-Béry'
    if groupe in ('IGA CORPORATIF', 'IGA') or 'IGA' in nom or 'TRADITION' in nom:
        if 'CORPO' in nom:
            return 'IGA', 'IGA corporatif'
        if 'BONISOIR' in nom or 'VOISIN' in nom:
            return 'Dépanneurs indépendants', 'Bonisoir / Voisin (via IGA)'
        return 'IGA', 'IGA affilié (indépendant)'
    if groupe.startswith('MÉTRO') or groupe.startswith('METRO') or groupe.startswith('M?TRO'):
        return 'Métro', 'Métro franchisé'

    # Dépanneurs indépendants (question 7)
    if groupe in GROUPES_DEP_INDEPENDANTS:
        return 'Dépanneurs indépendants', groupe.title()
    if groupe in GROUPES_EPICERIES_SPECIALISEES:
        return 'Épiceries spécialisées', groupe.title()
    if groupe == 'INDEPENDANT':
        if surface == 'PS' and any(m in f" {nom}" for m in MOTS_DEPANNEUR):
            return 'Dépanneurs indépendants', 'Indépendant'
        if surface == 'PS':
            return 'Autres indépendants (petite surface)', 'Indépendant PS'
        return 'Autres indépendants (grande surface)', 'Indépendant GS'
    return 'Autre (à classer)', groupe.title() or 'Inconnu'


# --- PRODUITS ---------------------------------------------------------------------------------
def litres_par_unite(fmt, code, caisse_eq_par_unite=None):
    """Litres contenus dans une unité facturée, à partir du format (ex. « 1/12/473ML », « 6/4/473ML »).

    Si le format est illisible, on se rabat sur la caisse éq. 12 de l'app (12 x 473 ml)."""
    m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+(?:[.,]\d+)?)\s*ML', str(fmt).upper())
    if m:
        a, b, ml = int(m.group(1)), int(m.group(2)), float(m.group(3).replace(',', '.'))
        return a * b * ml / 1000.0
    if caisse_eq_par_unite is not None and not pd.isna(caisse_eq_par_unite):
        return caisse_eq_par_unite * LITRES_CAISSE_EQ12
    return LITRES_CAISSE_EQ12


def est_sans_alcool(code, nom):
    code, nom = _txt(code), _txt(nom)
    if 'SANS ALCOOL' in nom or 'S/A' in nom or nom.endswith(' SA') or ' SA ' in f"{nom} ":
        return True
    if code == 'MABSGU' or 'SGSA' in code or re.search(r'SA(12|4P|4)?$', code):
        return True
    return False


def est_sans_gluten(code, nom):
    code, nom = _txt(code), _txt(nom)
    return 'GLUTEN' in nom or 'ULTRA' in nom or 'SG' in code


def est_vilains(row):
    fab = _txt(row.get('Fabricant'))
    if 'VILAINS' in fab:
        return True
    return _txt(row.get('Gamme')) == 'VILAINS'


# --- PRÉPARATION ------------------------------------------------------------------------------
def _num(s):
    if s.dtype == 'object':
        s = s.astype(str).str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0)


def enrichir_lignes(df):
    """Ajoute litres, drapeaux produit et canal à chaque ligne. df doit contenir DateAnalyse."""
    df = df.copy()
    for col in ['LineQty', 'LineTotal', 'Rabais']:
        if col in df.columns:
            df[col] = _num(df[col])
        else:
            df[col] = 0.0
    for col in ['ItemCode', 'ItemName', 'Format', 'CardCode', 'CardName', 'GroupName', 'Surface',
                'ParentPayeur', 'CityS', 'ZipCodeS', 'Fabricant', 'DocType']:
        if col not in df.columns:
            df[col] = None

    # Caisse éq. par unité (si l'app l'a calculée) pour le repli sur les formats illisibles
    if 'CAISSE EQ' in df.columns:
        par_unite = (df['CAISSE EQ'] / df['LineQty'].where(df['LineQty'] != 0)).fillna(1)
    else:
        par_unite = pd.Series(1.0, index=df.index)
    df['Litres/unité'] = [litres_par_unite(f, c, p) for f, c, p in zip(df['Format'], df['ItemCode'], par_unite)]
    df['Litres'] = df['LineQty'] * df['Litres/unité']

    df['Sans alcool'] = [est_sans_alcool(c, n) for c, n in zip(df['ItemCode'], df['ItemName'])]
    df['Sans gluten'] = [est_sans_gluten(c, n) for c, n in zip(df['ItemCode'], df['ItemName'])]
    df['Vilains'] = df.apply(est_vilains, axis=1)
    df['Gratuit'] = (df['LineQty'] > 0) & (df['LineTotal'] == 0)

    canaux = df.apply(classer_canal, axis=1, result_type='expand')
    df['Catégorie'] = canaux[0]
    df['Sous-catégorie'] = canaux[1]
    return df


def tranche(cs_metriques):
    for bas, haut, label in TRANCHES:
        if bas <= cs_metriques < haut:
            return label
    return TRANCHES[0][2] if cs_metriques < 0 else TRANCHES[-1][2]


# --- PORTRAIT ---------------------------------------------------------------------------------
def construire_portrait(df_lignes, debut, fin, inclure_vilains=True):
    """Retourne un dict de DataFrames : magasins, magasin_produit, synthese, controles.

    df_lignes : lignes brutes (DRB) avec DateAnalyse. debut/fin : dates incluses."""
    df = df_lignes[(df_lignes['DateAnalyse'] >= pd.Timestamp(debut)) &
                   (df_lignes['DateAnalyse'] <= pd.Timestamp(fin))].copy()
    df = enrichir_lignes(df)
    if not inclure_vilains:
        df = df[~df['Vilains']]

    df['L sans alcool'] = df['Litres'].where(df['Sans alcool'], 0)
    df['L avec alcool'] = df['Litres'].where(~df['Sans alcool'], 0)
    df['L sans gluten'] = df['Litres'].where(df['Sans gluten'], 0)
    df['L Vilains'] = df['Litres'].where(df['Vilains'], 0)
    df['Unités gratuites'] = df['LineQty'].where(df['Gratuit'], 0)
    df['L gratuits'] = df['Litres'].where(df['Gratuit'], 0)
    df['Rabais total $'] = df['Rabais'] * df['LineQty'].clip(lower=0)
    df['Unités vendues'] = df['LineQty'].clip(lower=0)
    df['Unités retournées'] = (-df['LineQty']).clip(lower=0)

    cles = ['CardCode']
    info = (df.sort_values('DateAnalyse')
              .groupby(cles)
              .agg(Magasin=('CardName', 'last'), Bannière=('GroupName', 'last'), Surface=('Surface', 'last'),
                   Payeur=('ParentPayeur', 'last'), Ville=('CityS', 'last'), CodePostal=('ZipCodeS', 'last'),
                   Catégorie=('Catégorie', 'last'), SousCat=('Sous-catégorie', 'last'),
                   Première=('DateAnalyse', 'min'), Dernière=('DateAnalyse', 'max')))
    vols = df.groupby(cles).agg(
        Litres=('Litres', 'sum'), L_avec=('L avec alcool', 'sum'), L_sans=('L sans alcool', 'sum'),
        L_sg=('L sans gluten', 'sum'), L_vil=('L Vilains', 'sum'), U_gratuites=('Unités gratuites', 'sum'),
        L_grat=('L gratuits', 'sum'), Rabais=('Rabais total $', 'sum'), U_vendues=('Unités vendues', 'sum'),
        U_retour=('Unités retournées', 'sum'), Ventes=('LineTotal', 'sum'), Nb_SKU=('ItemCode', 'nunique'))
    mag = info.join(vols).reset_index()

    mag['HL'] = mag['Litres'] / 100
    mag['Caisses métriques'] = mag['Litres'] / LITRES_CAISSE_METRIQUE
    mag['Caisses éq. 12'] = mag['Litres'] / LITRES_CAISSE_EQ12
    mag['HL avec alcool'] = mag['L_avec'] / 100
    mag['HL sans alcool'] = mag['L_sans'] / 100
    mag['HL sans gluten'] = mag['L_sg'] / 100
    mag['HL Vilains'] = mag['L_vil'] / 100
    mag['Tranche (cs métriques/an)'] = mag['Caisses métriques'].apply(tranche)
    mag['Rabais moyen $/unité vendue'] = (mag['Rabais'] / mag['U_vendues'].where(mag['U_vendues'] > 0)).round(2)
    mag['Rabais $/HL'] = (mag['Rabais'] / mag['HL'].where(mag['HL'] > 0)).round(2)
    mag['RTA'] = mag['CodePostal'].astype(str).str.upper().str.replace(' ', '').str[:3].where(mag['CodePostal'].notna())

    mag = mag.rename(columns={
        'CardCode': 'Code client', 'SousCat': 'Sous-catégorie', 'CodePostal': 'Code postal',
        'Première': 'Première livraison', 'Dernière': 'Dernière livraison',
        'U_gratuites': 'Unités gratuites', 'L_grat': 'Litres gratuits', 'Rabais': 'Rabais total $',
        'U_vendues': 'Unités vendues', 'U_retour': 'Unités retournées', 'Ventes': 'Ventes nettes $',
        'Nb_SKU': 'Nb SKU'})
    colonnes = ['Catégorie', 'Sous-catégorie', 'Bannière', 'Code client', 'Magasin', 'Surface', 'Payeur',
                'Ville', 'Code postal', 'RTA', 'HL', 'Caisses métriques', 'Caisses éq. 12',
                'Tranche (cs métriques/an)', 'HL avec alcool', 'HL sans alcool', 'HL sans gluten', 'HL Vilains',
                'Ventes nettes $', 'Unités vendues', 'Unités retournées', 'Unités gratuites', 'Litres gratuits',
                'Rabais total $', 'Rabais moyen $/unité vendue', 'Rabais $/HL', 'Nb SKU',
                'Première livraison', 'Dernière livraison']
    mag = mag[colonnes].sort_values(['Catégorie', 'Sous-catégorie', 'HL'], ascending=[True, True, False])

    # Détail magasin x produit
    mp = (df.groupby(['CardCode', 'CardName', 'GroupName', 'Catégorie', 'ItemCode', 'ItemName', 'Format',
                      'Sans alcool', 'Sans gluten', 'Vilains'], dropna=False)
            .agg(Unités=('LineQty', 'sum'), Litres=('Litres', 'sum'), Ventes=('LineTotal', 'sum'))
            .reset_index())
    mp['HL'] = mp['Litres'] / 100
    mp['Caisses métriques'] = mp['Litres'] / LITRES_CAISSE_METRIQUE
    mp = mp.rename(columns={'CardCode': 'Code client', 'CardName': 'Magasin', 'GroupName': 'Bannière',
                            'ItemCode': 'Code produit', 'ItemName': 'Produit', 'Ventes': 'Ventes nettes $'})

    synthese = synthese_par_canal(mag)
    controles = controles_donnees(df, debut, fin)
    return {'magasins': mag, 'magasin_produit': mp, 'synthese': synthese, 'controles': controles}


def synthese_par_canal(mag):
    """Nombre de magasins et volumes par catégorie / sous-catégorie, avec le décompte par tranche."""
    actifs = mag[mag['HL'] > 0]
    g = actifs.groupby(['Catégorie', 'Sous-catégorie'])
    s = g.agg(**{'Nb magasins actifs': ('Code client', 'nunique'), 'HL': ('HL', 'sum'),
                 'HL avec alcool': ('HL avec alcool', 'sum'), 'HL sans alcool': ('HL sans alcool', 'sum'),
                 'HL sans gluten': ('HL sans gluten', 'sum'), 'Caisses métriques': ('Caisses métriques', 'sum'),
                 'Unités gratuites': ('Unités gratuites', 'sum'), 'Rabais total $': ('Rabais total $', 'sum')})
    tr = actifs.pivot_table(index=['Catégorie', 'Sous-catégorie'], columns='Tranche (cs métriques/an)',
                            values='Code client', aggfunc='nunique', fill_value=0)
    for _, _, label in TRANCHES:
        if label not in tr.columns:
            tr[label] = 0
    s = s.join(tr[[t[2] for t in TRANCHES]]).fillna(0)
    s['HL moyen / magasin'] = s['HL'] / s['Nb magasins actifs']
    s['Rabais $/HL'] = s['Rabais total $'] / s['HL'].where(s['HL'] > 0)
    s = s.reset_index()
    tot = s.drop(columns=['Catégorie', 'Sous-catégorie']).sum(numeric_only=True)
    tot['HL moyen / magasin'] = tot['HL'] / tot['Nb magasins actifs'] if tot['Nb magasins actifs'] else 0
    tot['Rabais $/HL'] = tot['Rabais total $'] / tot['HL'] if tot['HL'] else 0
    tot['Catégorie'], tot['Sous-catégorie'] = 'TOTAL', ''
    return pd.concat([s, tot.to_frame().T], ignore_index=True)


def controles_donnees(df, debut, fin):
    """Quelques contrôles pour valider l'export avant de l'utiliser."""
    cle_doublon = [c for c in ['DocType', 'DocNum', 'CardCode', 'ItemCode', 'LineQty', 'LineTotal', 'DateAnalyse']
                   if c in df.columns]
    doublons = int(df.duplicated(subset=cle_doublon).sum()) if cle_doublon else 0
    fmt_ko = df[~df['Format'].astype(str).str.upper().str.match(r'^\s*\d+\s*/\s*\d+\s*/\s*\d')]
    lignes = [
        ('Période demandée', f"{pd.Timestamp(debut).date()} → {pd.Timestamp(fin).date()}"),
        ('Première date dans les données', str(df['DateAnalyse'].min().date()) if len(df) else '—'),
        ('Dernière date dans les données', str(df['DateAnalyse'].max().date()) if len(df) else '—'),
        ('Nombre de lignes', len(df)),
        ('Lignes possiblement en double (même doc, magasin, produit, qté, montant, date)', doublons),
        ('Lignes au format illisible (volume estimé en caisses éq. 12)', len(fmt_ko)),
        ('Magasins classés « Autre (à classer) »', int(df.loc[df['Catégorie'] == 'Autre (à classer)', 'CardCode'].nunique())),
        ('HL total', round(df['Litres'].sum() / 100, 1)),
        ('Caisse métrique', f"24 x 341 ml = {LITRES_CAISSE_METRIQUE:.3f} L"),
    ]
    out = pd.DataFrame(lignes, columns=['Contrôle', 'Valeur'])
    out['Valeur'] = out['Valeur'].astype(str)
    return out


REGLES = pd.DataFrame([
    ('Volume', "Net livré/facturé : livraisons moins retours (LineQty signé), toutes les lignes de la période."),
    ('Litres', "Calculés à partir du format (ex. 6/4/473ML = 24 x 473 ml = 11,35 L par unité)."),
    ('Caisse métrique', "24 x 341 ml = 8,184 L. 50 cs métriques ≈ 4,1 HL ; 120 cs métriques ≈ 9,8 HL."),
    ('Tranches', "Moins de 50 / 50 à 119 / 120 caisses métriques et plus sur la période (12 mois = annuel)."),
    ('Sans alcool', "Nom contenant SANS ALCOOL, S/A ou finissant par SA ; codes en SA12, SA4P, SGSA, MABSGU."),
    ('Sans gluten', "Nom contenant GLUTEN ou ULTRA, ou code contenant SG."),
    ('Gratuités', "Lignes livrées avec un montant à 0 $ (quantité positive)."),
    ('Rabais', "Rabais par unité facturé (colonne Rabais) x unités vendues. N'inclut pas les ristournes hors facture."),
    ('Dépanneurs indépendants', "GroupName : " + ', '.join(sorted(GROUPES_DEP_INDEPENDANTS))
     + " ; plus les clients INDEPENDANT en petite surface dont le nom évoque un dépanneur."),
    ('Métro', "Tous les magasins du groupe MÉTRO FRANCHISÉ sauf Super C. Les données ne distinguent pas franchisé et corporatif."),
    ('IGA', "Affilié = indépendant ; « CORPO » dans le nom = corporatif. Rachelle-Béry séparé."),
], columns=['Règle', 'Description'])


def exporter_excel(portrait):
    """Écrit le portrait dans un fichier Excel et retourne les octets."""
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as w:
        wb = w.book
        f_hl = wb.add_format({'num_format': '#,##0.0'})
        f_int = wb.add_format({'num_format': '#,##0'})
        f_money = wb.add_format({'num_format': '#,##0.00 $'})
        f_date = wb.add_format({'num_format': 'yyyy-mm-dd'})

        def ecrire(df, nom, largeurs):
            df.to_excel(w, sheet_name=nom, index=False)
            ws = w.sheets[nom]
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)
            for i, col in enumerate(df.columns):
                c = str(col)
                if c.startswith('HL') or c in ('Litres', 'Litres gratuits', 'Caisses métriques', 'Caisses éq. 12',
                                               'HL moyen / magasin'):
                    fmt = f_hl
                elif '$' in c:
                    fmt = f_money
                elif 'livraison' in c:
                    fmt = f_date
                elif c.startswith(('Nb', 'Unités')) or 'cs' in c:
                    fmt = f_int
                else:
                    fmt = None
                ws.set_column(i, i, largeurs.get(c, 14), fmt)

        ecrire(portrait['synthese'], 'Synthèse par canal', {'Catégorie': 30, 'Sous-catégorie': 30})
        ecrire(portrait['magasins'], 'Magasins', {'Catégorie': 26, 'Sous-catégorie': 26, 'Bannière': 22,
                                                  'Magasin': 34, 'Ville': 22, 'Payeur': 20,
                                                  'Tranche (cs métriques/an)': 26})
        ecrire(portrait['magasin_produit'], 'Magasin x produit', {'Magasin': 34, 'Produit': 38, 'Bannière': 22,
                                                                  'Catégorie': 26})
        ecrire(portrait['controles'], 'Contrôles', {'Contrôle': 70, 'Valeur': 30})
        ecrire(REGLES, 'Règles', {'Règle': 24, 'Description': 120})
    return out.getvalue()
