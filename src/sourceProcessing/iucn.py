import requests
from pathlib import Path
import json
import pandas as pd
import time
from lib.tools.bigFileWriter import BigFileWriter

def retrieve(apiKeyPath: Path, outputFilePath: Path):
    with open(apiKeyPath) as fp:
        apiKey = fp.read().rstrip(" \n")

    baseURL = "https://api.iucnredlist.org/api/v4"
    headers = {
        "accept": "application/json",
        "Authorization": apiKey
    }

    session = requests.Session()

    # Get version
    response = session.get(f"{baseURL}/information/red_list_version", headers=headers)
    version = list(response.json().values())[0]
    print(f"Version: {version}")
    
    writer = BigFileWriter(outputFilePath)
    writer.populateFromFolder(writer.subfileDir)
    onPage = len(writer.writtenFiles) + 1

    running = True
    while running:
        for _ in range(5):
            response = session.get(f"{baseURL}/scopes/1?page={onPage}&latest=true", headers=headers)
            try:
                data: dict = response.json()
                assessmentIDs = [assessment["assessment_id"] for assessment in data["assessments"]]
                break
            except requests.exceptions.JSONDecodeError:
                session = requests.Session()
        else:
            print("Failed")
            return

        if len(assessmentIDs) < 100 or not assessmentIDs:
            running = False

        records = []

        for idx, assessmentID in enumerate(assessmentIDs, start=1):
            print(f"Processing assessment ID #{((onPage - 1) * 100) + idx}: {assessmentID}", end= "\r")
            response = session.get(f"{baseURL}/assessment/{assessmentID}", headers=headers)
            try:
                data: dict = response.json()
            except requests.exceptions.JSONDecodeError:
                continue

            taxonomy = data.pop("taxon")

            commonNames = taxonomy.pop("common_names")
            taxonomy["common_names"] = []
            for name in commonNames:
                if isinstance(name["language"], dict):
                    name["language"] = name["language"]["description"]["en"]
                taxonomy["common_names"].append(name)

            # Flatten description from following items
            for item in ("population_trend", "red_list_category", "biogeographical_realms", "systems"):
                if isinstance(data[item], dict):
                    data[item] = data[item]["description"]["en"]

                if isinstance(data[item], list):
                    data[item] = [element["description"]["en"] for element in data[item]]

            supplementaryInfo = data.pop("supplementary_info")

            # Remove scopes
            data.pop("scopes")

            records.append(data | taxonomy | supplementaryInfo)

        df = pd.DataFrame.from_records(records)
        writer.writeDF(df)
        onPage += 1

    writer.oneFile(False)
    print()
