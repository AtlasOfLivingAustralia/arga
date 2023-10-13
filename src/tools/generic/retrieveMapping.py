import pandas as pd
import json
import lib.config as cfg
import numpy as np
import urllib.error

def filterEntry(value: any) -> list:
    if not isinstance(value, str): # Ignore float/int
        return []
    
    if value in ("", "0", "1", "nan", "NaN", np.nan, np.NaN, np.NAN): # Ignore these values
        return []
    
    if any(value.startswith(val) for val in ("ARGA", '"', "/")): # Ignore values with these prefixes
        return []

    return [elem.strip() for elem in value.split(",")]

def getMappings(df: pd.DataFrame) -> dict:
    fields = "Field Name"
    eventColumns = [col for col in df.columns if col[0] == "T" and col[1].isdigit()]

    mappings = {}
    for column in eventColumns:
        subDF = df[[fields, column]]
        for _, row in subDF.iterrows():
            filteredValue = filterEntry(row[column])
            if filteredValue:
                mappings[f"#{column[1]}:{row[fields]}"] = filteredValue

    return mappings

if __name__ == "__main__":
    documentID = "1dglYhHylG5_YvpslwuRWOigbF5qhU-uim11t_EE_cYE"

    sheetIDs = {
        "42bp-genomeArk": 84855374,
        "ala-avh": 404635334,
        "anemone-db": 286004534,
        "bold-datapackage": 1154592624,
        "bold-tsv": 78385490,
        "bold-xml": 984983691,
        "bpa-portal": 1982878906,
        "bvbrc-db": 685936034,
        "csiro-dap": 16336602,
        "csiro-genomes": 215504073,
        "dnazoo-db": 570069681,
        "ena-genomes": 1058330275,
        "ncbi-biosample": 109194600,
        "ncbi-nucleotide": 1759032197,
        "ncbi-refseq": 2003682060,
        "ncbi-taxonomy": 240630744,
        "ncbi-genbank": 1632006425,
        "tern-portal": 1651969444,
        "tsi-koala": 975794491
    }

    written = []
    for database, sheetID in sheetIDs.items():
        location, db = database.split("-")
        print(f"Reading {database}")

        try:
            df = pd.read_csv(f"https://docs.google.com/spreadsheets/d/{documentID}/export?format=csv&gid={sheetID}", keep_default_na=False)
        except urllib.error.HTTPError:
            print(f"Unable to read sheet for {database}")
            continue

        mappings = getMappings(df)
        mapFile = cfg.folderPaths.mapping / f"{location}.json"

        if mapFile.exists() and location not in written: # Old map file
            mapFile.unlink()

        if not mapFile.exists(): # First data source from location
            with open(mapFile, "w") as fp:
                json.dump(mappings, fp, indent=4)
            written.append(location)
            print(f"Created new {location} map")
            continue

        with open(mapFile) as fp:
            columnMap = json.load(fp)

        for keyword, names in mappings.items():
            if keyword not in columnMap:
                columnMap[keyword] = names
            else:
                columnMap[keyword].extend(name for name in names if name not in columnMap[keyword])

        with open(mapFile, "w") as fp:
            json.dump(columnMap, fp, indent=4)

        print(f"Added new values to {location} map")
        if location not in written:
            written.append(location)
