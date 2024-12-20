from pathlib import Path
import pandas as pd
import requests
from bs4 import BeautifulSoup
from lib.tools.progressBar import SteppableProgressBar
import json
import concurrent.futures as cf

def getMetadata(filePath: Path, outputFile: Path):
    metadataFolder = outputFile.parent / "metadata"
    metadataFolder.mkdir(exist_ok=True)

    df = pd.read_csv(filePath, sep="\t")
    session = requests.Session()

    ids = df[df["URL"].notna()]
    progress = SteppableProgressBar(len(ids), callsPerUpdate=10, processName="Scraping")

    # for _, row in ids.iterrows():
    #     url = row["URL"]
    #     id = row["ID"]

    #     file = metadataFolder / f"{id}.txt"
    #     if not file.exists():
    #         response = session.get(url)
    #         soup = BeautifulSoup(response.content, "html.parser")
    #         content = soup.find("p", {"class": "result"})

    #         with open(outputFile, "w", encoding="utf-8") as fp:
    #             fp.write(content.text)

    #     progress.update()

    parsedFolder = outputFile.parent / "parsedMetadata"
    parsedFolder.mkdir(exist_ok=True)

    records = []
    progress = SteppableProgressBar(len(list(metadataFolder.glob("*.txt"))), callsPerUpdate=10, processName="Parsing")
    
    # with cf.ProcessPoolExecutor(max_workers=5) as executor:
    #     futures = (executor.submit(processFile, file, parsedFolder) for file in metadataFolder.iterdir())

    #     for future in cf.as_completed(futures):
    #         record = future.result()
    #         records.append(record)
    #         progress.update()

    for file in metadataFolder.iterdir():
        record = processFile(file, parsedFolder)
        records.append(record)
        progress.update()

    df2 = pd.DataFrame.from_records(records)
    df = df.merge(df2, "left", left_on="ID", right_on="id")
    df.to_csv(outputFile, index=False)

def processFile(file: Path, outputFolder: Path) -> dict:
    outputFilePath = outputFolder / f"{file.stem}.json"

    if outputFilePath.exists():
        with open(outputFilePath) as fp:
            return json.load(fp)
        
    with open(file, encoding="utf-8") as fp:
        data = fp.read()

    record = (parseGenus if file.stem.startswith("G") else parseSpecies)(file.stem, data)

    with open(outputFilePath, "w") as fp:
        json.dump(record, fp, indent=4)

    return record

def parseGenus(id: str, data: str) -> dict:
    record = {"id": id}

    data = data.replace("  ", "•")
    frontHalf, backHalf = data.split("•", 1)
    genus, frontHalf = frontHalf.split(" ", 1)
    record["genus"] = genus

    genders = {"Fem.": "feminine", "Masc.": "masculine", "Neut.": "neuter"}
    for gender in genders:
        if frontHalf.find(gender) >= 0:
            break

    record["genderVerbatim"] = gender
    record["gender"] = genders.get(gender)

    authorsPage, typeInfo = frontHalf.split(gender)

    authors = []
    nextAuthorEnd = authorsPage.find("]")
    while nextAuthorEnd > 0:
        authors.append(authorsPage[:nextAuthorEnd+1].strip(" &"))
        authorsPage = authorsPage[nextAuthorEnd+1:]
        nextAuthorEnd = authorsPage.find("]")

    record["author"] = " & ".join(authors)
    record["simpleAuthor"] = " & ".join(author.split("[")[0].strip() for author in authors)

    if ":" not in authorsPage:
        inYear = authorsPage
        page = ""
    else:
        inYear, page = authorsPage.split(":", 1)

    record["page"] = page.strip()
    
    inYear = inYear.strip()
    if " " not in inYear:
        record["in"] = ""
        record["year"] = inYear
    else:
        record["in"], record["year"] = inYear.lstrip(" in").rsplit(" ", 1)

    typeInfo = typeInfo.strip(" .").split(". ", 2)
    typeInfo += ["" for _ in range(3 - len(typeInfo))]
    record["typeSpecies"], record["typification"], record["typeJustification"] = typeInfo

    record["scientificName"] = f"{genus} {record['simpleAuthor']}, {record['year']}"

    references, currentStatus = backHalf.split("Current status: ")
    currentStatus = currentStatus.rstrip(" .").rsplit(". ", 1)
    currentStatus += ["" for _ in range(2 - len(currentStatus))]
    currentStatus, familyStatus = currentStatus
    currentStatus, _, currentName = currentStatus.split(" ", 2)
    
    record["currentStatus"] = currentStatus
    record["acceptedAs"] = currentName[::-1].replace(" ", " ,", 1)[::-1]
    familyStatus = familyStatus.split(": ")
    familyStatus += ["" for _ in range(2 - len(familyStatus))]
    record["family"], record["subfamily"] = familyStatus

    return record

def parseSpecies(id: str, data: str) -> dict:
    record = {"id": id}

    if "•" in data:
        frontHalf, backHalf = data.split("•", 1)
    else:
        frontHalf, backHalf = data.split(" Valid as", 1)

    specificEpithet, genus, frontHalf = frontHalf.split(" ", 2)
    record["specificEpithet"] = specificEpithet.strip(", ")
    record["genus"] = genus

    if frontHalf.find(":") == frontHalf.find(": "): # Didn't find year:page split
        frontSections = frontHalf.split()
        for section in frontSections:
            if section.isdigit():
                break

        authorInfo, typeInfo = frontHalf.split(section, 1)
        record["year"] = section
    else:
        authorYear, typeInfo = frontHalf.split(":", 1)
        authorInfo, year = authorYear.rsplit(" ", 1)
        record["year"] = year

    authors = []
    nextAuthorEnd = authorInfo.find("]")
    while nextAuthorEnd > 0:
        authors.append(authorInfo[:nextAuthorEnd].strip(" &"))
        authorInfo = authorInfo[nextAuthorEnd+1:]
        nextAuthorEnd = authorInfo.find("]")

    record["author"] = " & ".join(authors)
    record["simpleAuthor"] = " & ".join(author.split("[")[0].strip() for author in authors)
    record["in"] = authorInfo.strip(" in")

    page, typeInfo = typeInfo.split(" [", 1)
    record["page"] = page
    origDescription, typeInfo = typeInfo.split("] ", 1)
    record["originalDescription"] = origDescription
    locationInfo, types = typeInfo.split(". ", 1)

    # Making sure not to split at St.
    while locationInfo.endswith("St"):
        additionalLocInfo, types = types.split(". ", 1)
        locationInfo = f"{locationInfo}, {additionalLocInfo}"

    locationItems = locationInfo.split()
    remainingItems = []
    for item in locationItems:
        if "°" in item:
            coordinate = item.strip("[ ]")
            if "E" in item or "W" in item:
                record["latitude"] = coordinate
            elif "N" in item or "S" in item:
                record["longitude"] = coordinate

        elif "depth" in item:
            record["depth"] = item.split(" ", 1)[-1].strip()

        elif "miles" in item:
            continue

        else:
            remainingItems.append(item)

    record["typeLocation"] = " ".join(remainingItems).strip(" ,")
    record["coordsVerbatim"] = f"{record.get('latitude', '')}, {record.get('longitude', '')}".strip(",") 
    record["digitalCoordSystem"] = "WGS 84"

    types = types.rstrip(". ").split(". ", 1)
    types += ["" for _ in range(2 - len(types))]
    typeStyle, otherTypes = types
    typeStyle = typeStyle.split(": ")
    if len(typeStyle) == 1:
        record["typeStyle"] = None
        record["typeRegistration"] = ""
    else:
        record["typeStyle"] = typeStyle[0]
        record["typeRegistration"] = ", ".join(typeStyle[1:])

    record["otherTypes"] = otherTypes

    references, currentStatus = backHalf.split("Current status: Valid as ")
    currentStatus, otherFields = currentStatus.rstrip(". ").split(". ", 1)
    record["currentValidName"] = currentStatus

    validLabels = (
        "Distribution",
        "Habitat"
    )

    for idx, field in enumerate(otherFields.split(". ")):
        if ":" not in field:
            if idx == 0:
                record["family"] = field
                record["subfamily"] = ""
            continue

        label, value = [item.strip(". ") for item in field.split(":", 1)]
        if label not in validLabels:
            if idx == 0:
                record["family"] = label
                record["subfamily"] = value
            continue

        record[label.lower()] = value

    return record

def combine(filePath: Path, outputFilePath: Path):
    otherFiles = outputFilePath.parent / "otherFiles"

    df = pd.read_csv(filePath, low_memory=False)

    def loadCSV(fileName: str) -> pd.DataFrame:
        df = pd.read_csv(otherFiles / fileName, sep="\t")
        newColumns = {column: column.replace(" ", "_") for column in df.columns}
        df.rename(newColumns)
        return df
    
    status = loadCSV("Species_all_status.txt")
    references = loadCSV("References_all.txt")
    speciesReference = loadCSV("CAS_SPC-CAS_REF_NO.txt")

    status = status.merge(speciesReference, "left", "CAS_SPC")
    status = status.merge(references, "left", "CAS_REF_NO")
    status["CAS_SPC"] = status["CAS_SPC"].apply(lambda x: f"S{x}")

    df = df.merge(status, "left", left_on="ID", right_on="CAS_SPC", suffixes=("_scraped", "_provided"))
    df.to_csv(outputFilePath, index=False)