
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw"
REPORT_DIR = BASE_DIR / "data" / "reports"

REPORT_DIR.mkdir(parents=True, exist_ok=True)


def inspect_dataframe(df, name):
    print("\n" + "=" * 65)
    print(f"INSPECTION : {name}")
    print("=" * 65)

    print(f"Nombre de lignes : {len(df)}")
    print(f"Nombre de colonnes : {len(df.columns)}")

    print("\nColonnes :")
    print(df.columns.tolist())

    print("\nTypes de données :")
    print(df.dtypes)

    print("\nValeurs manquantes :")
    print(df.isna().sum())

    print("\nLignes entièrement dupliquées :")
    print(df.duplicated().sum())

    print("\nAperçu :")
    print(df.head())

    report = pd.DataFrame({
        "colonne": df.columns,
        "type": [str(df[col].dtype) for col in df.columns],
        "valeurs_manquantes": [
            int(df[col].isna().sum()) for col in df.columns
        ],
        "valeurs_distinctes": [
            int(df[col].nunique()) for col in df.columns
        ]
    })

    output = REPORT_DIR / f"{name}_inspection.csv"
    report.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\nRapport créé : {output}")


def inspect_excel(file_path):
    print(f"\nCLASSEUR EXCEL : {file_path.name}")

    sheets = pd.read_excel(
        file_path,
        sheet_name=None,
        engine="openpyxl"
    )

    print(f"Nombre de feuilles : {len(sheets)}")

    for sheet_name, df in sheets.items():
        inspect_dataframe(df, sheet_name)


def inspect_csv(file_path):
    encodings = ["utf-8-sig", "cp1252", "latin1"]

    for encoding in encodings:
        try:
            df = pd.read_csv(file_path, encoding=encoding)
            inspect_dataframe(df, file_path.stem)
            return
        except UnicodeDecodeError:
            continue

    print(f"Impossible de lire le fichier : {file_path.name}")


def main():
    files = (
        list(RAW_DIR.glob("*.xlsx"))
        + list(RAW_DIR.glob("*.xls"))
        + list(RAW_DIR.glob("*.csv"))
    )

    if not files:
        print("Aucun fichier Excel ou CSV trouvé dans data/raw.")
        return

    for file_path in files:
        if file_path.suffix.lower() in [".xlsx", ".xls"]:
            inspect_excel(file_path)
        elif file_path.suffix.lower() == ".csv":
            inspect_csv(file_path)


if __name__ == "__main__":
    main()
