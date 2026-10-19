"""
Compile les caisses vendues par un représentant depuis une date donnée.

Usage (sur votre ordinateur, PAS dans Streamlit) :
    python scripts/ventes_representant.py --creds /chemin/vers/cle_service.json --rep "Brie" --since 2026-01-01

--creds : fichier JSON de la clé du compte de service Google (le même que dans les secrets Streamlit,
          section [connections.gcs]). Ne pas committer ce fichier.
--rep   : sous-chaîne recherchée dans SalesEmployee (insensible à la casse).
--since : date de début (incluse), format AAAA-MM-JJ.

La conversion en caisses reprend exactement la logique de streamlit_app.py (harmoniser_formats_alc).
"""

import argparse
import io
import json
import sys
from datetime import date

import pandas as pd

ID_DOSSIER_ALCHIMISTE = "1eTeWop4EVTDB9GbAPPixJZDcVYeZnauD"

# Copié de streamlit_app.py : garder ces listes synchronisées avec l'app.
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
CODES_ALREADY_12EQ = {
    'MABSGU',
}


def harmoniser_caisses(row):
    """Même logique que harmoniser_formats_alc dans streamlit_app.py. Retourne le nombre de caisses équivalentes."""
    code = str(row['ItemCode']).strip().upper()
    qty = row['LineQty']
    if row['Année'] == 2025:
        return qty * 2 if code in CODES_4PACK else qty
    if code in CODES_ALREADY_12EQ:
        return qty
    if code.endswith('SG4P') or code in CODES_4PACK or (not code.endswith('12')):
        return qty * 2
    return qty


def preparer(df):
    """Ajoute les colonnes de date, d'année et de caisses équivalentes."""
    df = df.copy()
    for col in ['LineQty']:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.').str.replace(r'[^\d.-]', '', regex=True),
                                errors='coerce').fillna(0)
    df['DocDate'] = pd.to_datetime(df['DocDate'], errors='coerce')
    df['DateLivraison'] = pd.to_datetime(df['DateLivraison'], errors='coerce')
    df['DateAnalyse'] = df['DateLivraison'].fillna(df['DocDate'])
    df['Année'] = df['DateAnalyse'].dt.year
    df['Mois'] = df['DateAnalyse'].dt.strftime('%Y-%m')
    df['CAISSE EQ'] = df.apply(harmoniser_caisses, axis=1)
    return df


def compiler(df, rep, since):
    """Filtre le représentant et la période, puis retourne (total, par_mois, par_sku, par_magasin)."""
    df = preparer(df)
    mask = (
        df['SalesEmployee'].astype(str).str.contains(rep, case=False, na=False)
        & (df['DateAnalyse'] >= pd.Timestamp(since))
    )
    sel = df[mask]
    total = sel['CAISSE EQ'].sum()
    par_mois = sel.groupby('Mois')['CAISSE EQ'].sum().reset_index()
    par_sku = sel.groupby(['ItemCode', 'ItemName'])['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
    par_magasin = sel.groupby(['CardCode', 'CardName'])['CAISSE EQ'].sum().reset_index().sort_values('CAISSE EQ', ascending=False)
    return total, par_mois, par_sku, par_magasin


def charger_drive(creds_path):
    """Télécharge tous les CSV/XLSX du dossier ALCHIMISTE (même principe que load_data_from_drive)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=['https://www.googleapis.com/auth/drive.readonly'])
    service = build('drive', 'v3', credentials=creds)
    query = (f"'{ID_DOSSIER_ALCHIMISTE}' in parents and (name contains '.csv' or name contains '.xlsx' "
             f"or name contains '.CSV') and trashed = false")
    items = service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
    frames = []
    for item in items:
        content = service.files().get_media(fileId=item['id']).execute()
        try:
            if item['name'].lower().endswith('.xlsx'):
                df = pd.read_excel(io.BytesIO(content), engine='openpyxl')
            else:
                raw = content.decode('latin1')
                try:
                    df = pd.read_csv(io.StringIO(raw), sep=',', quotechar='"', on_bad_lines='skip')
                except Exception:
                    df = pd.read_csv(io.StringIO(raw), sep=';', quotechar='"', on_bad_lines='skip')
            frames.append(df)
        except Exception as e:
            print(f"Fichier ignoré : {item['name']} ({e})", file=sys.stderr)
    if not frames:
        raise SystemExit("Aucun fichier lu dans le dossier Drive.")
    return pd.concat(frames, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--creds', required=True, help='Fichier JSON de la clé du compte de service')
    parser.add_argument('--rep', required=True, help='Nom (ou partie du nom) du représentant')
    parser.add_argument('--since', default='2026-01-01', help='Date de début AAAA-MM-JJ')
    parser.add_argument('--out', default=None, help='Fichier Excel de sortie (optionnel)')
    args = parser.parse_args()

    df = charger_drive(args.creds)
    total, par_mois, par_sku, par_magasin = compiler(df, args.rep, args.since)

    print(f"\nReprésentant contenant « {args.rep} » depuis le {args.since}")
    print(f"Total caisses équivalentes : {total:,.0f}\n")
    print("Par mois :")
    print(par_mois.to_string(index=False))
    print("\nPar SKU :")
    print(par_sku.to_string(index=False))
    print(f"\nMagasins distincts : {par_magasin.shape[0]}")

    if args.out:
        with pd.ExcelWriter(args.out, engine='xlsxwriter') as w:
            pd.DataFrame([{'Total caisses': total}]).to_excel(w, sheet_name='Résumé', index=False)
            par_mois.to_excel(w, sheet_name='Par mois', index=False)
            par_sku.to_excel(w, sheet_name='Par SKU', index=False)
            par_magasin.to_excel(w, sheet_name='Par magasin', index=False)
        print(f"\nFichier écrit : {args.out}")


if __name__ == '__main__':
    main()
