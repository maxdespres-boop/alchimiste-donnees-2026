"""
Génère le portrait magasin par magasin (Excel) sans passer par Streamlit.

Usage (sur votre ordinateur) :
    python scripts/portrait_magasins_cli.py --creds /chemin/vers/cle_service.json \
        --debut 2025-10-21 --fin 2026-10-20 --out portrait_magasins.xlsx

    # ou à partir de fichiers locaux (CSV DRB ou XLSX) au lieu du Drive :
    python scripts/portrait_magasins_cli.py --fichiers donnees/*.CSV --out portrait_magasins.xlsx

Sans --debut/--fin : les 12 mois qui se terminent à la dernière date présente dans les données.
--sans-vilains : exclut les produits Vilains Brasseurs.
"""

import argparse
import io
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portrait_magasins import construire_portrait, exporter_excel  # noqa: E402


def lire_local(chemins):
    frames = []
    for p in chemins:
        if p.lower().endswith('.xlsx'):
            frames.append(pd.read_excel(p, engine='openpyxl'))
        else:
            raw = open(p, 'rb').read().decode('latin1')
            try:
                frames.append(pd.read_csv(io.StringIO(raw), sep=',', quotechar='"', on_bad_lines='skip'))
            except Exception:
                frames.append(pd.read_csv(io.StringIO(raw), sep=';', quotechar='"', on_bad_lines='skip'))
    return pd.concat(frames, ignore_index=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--creds', help='Clé JSON du compte de service Google (lit le dossier Drive ALCHIMISTE)')
    src.add_argument('--fichiers', nargs='+', help='Fichiers CSV/XLSX locaux')
    p.add_argument('--debut', help='Date de début AAAA-MM-JJ (incluse)')
    p.add_argument('--fin', help='Date de fin AAAA-MM-JJ (incluse)')
    p.add_argument('--sans-vilains', action='store_true', help='Exclure les produits Vilains Brasseurs')
    p.add_argument('--out', default='portrait_magasins.xlsx', help='Fichier Excel de sortie')
    a = p.parse_args()

    if a.creds:
        from ventes_representant import charger_drive
        df = charger_drive(a.creds)
    else:
        df = lire_local(a.fichiers)

    df['DocDate'] = pd.to_datetime(df.get('DocDate'), errors='coerce')
    df['DateLivraison'] = pd.to_datetime(df.get('DateLivraison'), errors='coerce')
    df['DateAnalyse'] = df['DateLivraison'].fillna(df['DocDate'])

    fin = pd.Timestamp(a.fin) if a.fin else df['DateAnalyse'].max().normalize()
    debut = pd.Timestamp(a.debut) if a.debut else fin - pd.Timedelta(days=364)

    portrait = construire_portrait(df, debut, fin, inclure_vilains=not a.sans_vilains)
    with open(a.out, 'wb') as f:
        f.write(exporter_excel(portrait))

    print(f"Période : {debut.date()} → {fin.date()}")
    print(portrait['synthese'][['Catégorie', 'Sous-catégorie', 'Nb magasins actifs', 'HL']].to_string(index=False))
    print(f"\nFichier écrit : {a.out}")


if __name__ == '__main__':
    main()
