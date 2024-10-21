import requests
import json
from pathlib import Path
import pandas as pd
from io import BytesIO
from lib.tools.bigFileWriter import BigFileWriter, Format
from bs4 import BeautifulSoup
from lib.tools.progressBar import SteppableProgressBar
import re
import traceback

class EntryData:
    def __init__(self, rawData: dict):
        data = rawData.get("data", {})
        self.title = data["title"]

        metadata = rawData.get("metadata", {})
        self.key = metadata["nameKey"]
        self.ident = metadata["rank-key"]
        self.description = metadata["rank-desc"]
        self.prefix = metadata["rank-prefix"]
        self.rank = metadata["rank-with-prefix"]
        self.higherTaxon = metadata["is-higher-taxon"]
        self.specialTaxon = metadata["is-special-taxon"]
        
        self.state = rawData.get("state", "")
        self.children = [EntryData(child) for child in rawData.get("children", [])]

def retrieve(outputFilePath: Path):
    writer = BigFileWriter(outputFilePath, "sections", "section")

    checklist = "https://biodiversity.org.au/afd/mainchecklist"
    response = requests.get(checklist).text

    start = response.find("[", response.find("var data ="))
    end = response.rfind("]", start, response.rfind("var checklist;")) + 1

    kingdomData = [EntryData(kingdom) for kingdom in json.loads(response[start:end])]
    downloadChildCSVs(kingdomData, writer, [])

    writer.oneFile(False)

def downloadChildCSVs(entryData: list[EntryData], writer: BigFileWriter, parentRanks: list[str]) -> None:
    for entry in entryData:
        content = getCSVData(entry.key)
        higherTaxonomy = parentRanks + [entry.rank]
        if content is not None:
            df = buildDF(content)
            # df["higher_taxonomy"] = ";".join(higherTaxonomy)
            writer.writeDF(df)
            print(f"Wrote file #{len(writer.writtenFiles)}", end="\r")
            continue

        # Content was too large to download
        children = entry.children if entry.children else findChildren(entry.key)
        downloadChildCSVs(children, writer, higherTaxonomy)

def getCSVData(taxonKey: str) -> str | None:
    url = f"https://biodiversity.org.au/afd/taxa/{taxonKey}/names/csv/{taxonKey}.csv"
    response = requests.get(url)
    if not response.headers["Content-Type"].startswith("application/csv"):
        return None
    
    return response.content

def buildDF(content: bytes) -> pd.DataFrame:
    return pd.read_csv(BytesIO(content), encoding="iso-8859-1")

def findChildren(taxonKey: str) -> list[EntryData]:
    response = requests.get(f"https://biodiversity.org.au/afd/taxa/{taxonKey}/checklist-subtaxa.json")
    try:
        children = response.json()
    except requests.exceptions.JSONDecodeError:
        print(f"Unable to get children for item: {taxonKey}")
        return []

    return [EntryData(child) for child in children]

def cleanup(filePath: Path, outputFilePath: Path) -> None:
    df = pd.read_csv(filePath, dtype=object)

    df = df.drop([
        "CAVS_CODE",
        "CAAB_CODE",
        "PUB_PUB_FORMATTED",
        "PUB_PUB_QUALIFICATION",
        "PUBLICATION_LAST_UPDATE",
        "PARENT_PUBLICATION_GUID"
    ], axis=1)

    df = df.rename(columns={
        "NAME_TYPE": "taxonomic_status",
        "RANK": "taxon_rank",
        "NAME_GUID": "name_id",
        "TAXON_GUID": "taxon_id",
        "TAXON_LAST_UPDATE": "updated_at",
        "PARENT_TAXON_GUID": "parent_taxon_id",
        "PUB_PUB_AUTHOR": "publication_author",
        "PUB_PUB_YEAR": "publication_year",
        "PUB_PUB_TITLE": "publication_title",
        "PUB_PUB_PAGES": "publication_pages",
        "PUB_PUB_PUBLICATION_DATE": "publication_date",
        "PUB_PUB_PUBLISHER": "publisher",
        "PUB_PUB_TYPE": "publication_type",
        "PUBLICATION_GUID": "publication_id"
    })

    df = df.rename(columns={column: column.lower() for column in df.columns})
    df = df.rename(columns={"qualification": "notes"})
    df = df[df["scientific_name"] != "Unplaced Synonym(s)"]
    
    datasetID = "ARGA:TL:0001000"
    df["dataset_id"] = datasetID
    df["entity_id"] = f"{datasetID};" + df["scientific_name"] + ";" + df["taxonomic_status"]
    df["nomenclatural_code"] = "ICZN"
    df["created_at"] = ""
    df["qualification"] = ""

    inquirenda = df[df["taxon_rank"] == "Species Inquirenda"]
    df = df.drop(inquirenda.index)
    inqValidName = inquirenda[inquirenda["taxonomic_status"] == "Valid Name"]
    inquirenda = inquirenda.drop(inqValidName.index)
    inquirenda["taxon_rank"] = "Species"
    inquirenda["taxonomic_status"] = "Valid Name"
    inquirenda["qualification"] = "species inquirendum"
    inquirenda = inquirenda.drop("family", axis=1)
    inquirenda = inquirenda.merge(inqValidName[["taxon_id", "family"]], "left", "taxon_id")

    incertae = df[df["taxon_rank"] == "Incertae Sedis"]
    df = df.drop(incertae.index)
    incValidName = incertae[incertae["taxonomic_status"] == "Valid Name"]
    incertae = incertae.drop(incValidName.index)
    incertae["taxon_rank"] = "Family"
    incertae["taxonomic_status"] = "Valid Name"
    incertae["qualification"] = "incertae sedis"
    incertae = incertae.drop("family", axis=1)
    incertae = incertae.merge(incValidName[["taxon_id", "family"]], "left", "taxon_id")

    df = pd.concat([df, inquirenda, incertae], axis=0)

    df = df.fillna("")
    df["year"] = df["year"].apply(lambda x: x.split(".")[0])
    df["canonical_genus"] = df.apply(lambda row: f"{row['genus']} ({row['subgenus']})" if row["subgenus"] else row["genus"], axis=1)
    df["canonical_name"] = df.apply(lambda row: f"{row['canonical_genus']} {row['species']}" if row["taxon_rank"] == "Species" else f"{row['canonical_genus']} {row['species']} {row['subspecies']}" if row["taxon_rank"] == "subspecies" else row["names_various"], axis=1)
    df["authorship"] = df.apply(lambda row: f"{row['author']}, {row['year']}" if row["author"] not in ("", "NaN", "nan") else "", axis=1)
    df["scientific_name_authorship"] = df.apply(lambda row: f"({row['authorship']})" if row['orig_combination'] == 'N' and row["authorship"] not in ("", "NaN", "nan") else row["authorship"], axis=1)
    
    df["published_media_title"] = df["PUB_PUB_PARENT_BOOK_TITLE"] + df["PUB_PUB_PARENT_JOURNAL_TITLE"] + df["PUB_PUB_PARENT_ARTICLE_TITLE"]
    df = df.drop([
        "PUB_PUB_PARENT_BOOK_TITLE",
        "PUB_PUB_PARENT_JOURNAL_TITLE",
        "PUB_PUB_PARENT_ARTICLE_TITLE"
    ], axis=1)

    df.to_csv(outputFilePath, index=False)

def addParents(filePath: Path, outputFilePath: Path) -> None:
    df = pd.read_csv(filePath, dtype=object)
    parentDF = df[df["taxonomic_status"] == "Valid Name"]

    remap = {
        "taxon_id": "parent_taxon_id",
        "scientific_name": "parent_taxon",
        "taxon_rank": "parent_rank"
    }

    parentDF = parentDF[remap.keys()]
    parentDF = parentDF.rename(columns=remap)
    parentDF = parentDF[parentDF["parent_taxon_id"].notna()]

    df = df.merge(parentDF, "left", "parent_taxon_id")

    parentRemap = {
        "parent_taxon_id": "accepted_usage_taxon_id",
        "parent_taxon": "accepted_usage_taxon",
        "parent_rank": "accepted_usage_taxon_rank"
    }

    parentDF = parentDF.rename(columns=parentRemap)

    df = df.merge(parentDF, "left", left_on="taxon_id", right_on="accepted_usage_taxon_id")
    df = df[df["taxonomic_status"].isin(("Valid Name", "Synonym"))]

    df.to_csv(outputFilePath, index=False)

def enrich(filePath: Path, outputFilePath: Path) -> None:
    df = pd.read_csv(filePath, dtype=object)
    session = requests.Session()

    for rank in ("Species", "Genus"):
        subDF = df[df["taxon_rank"] == rank]

        enrichmentPath = outputFilePath.parent / f"{rank}.csv"
        if enrichmentPath.exists():
            continue

        writer = BigFileWriter(enrichmentPath, rank, subfileType=Format.CSV)
        writer.populateFromFolder(writer.subfileDir)
        subfileNames = [file.fileName for file in writer.writtenFiles]

        uniqueSeries = subDF["taxon_id"].unique()
        uniqueSeries = [item for item in uniqueSeries if item not in subfileNames]
        
        bar = SteppableProgressBar(50, len(uniqueSeries), f"{rank} Progress")
        for taxonID in uniqueSeries:
            bar.update()

            response = session.get(f"https://biodiversity.org.au/afd/taxa/{taxonID}/complete")
            try:
                records = _parseContent(response.text, taxonID, rank.lower())
            except:
                print(taxonID)
                print(traceback.format_exc())
                return
            
            recordDF = pd.DataFrame.from_records(records)
            writer.writeDF(recordDF, taxonID)

        writer.oneFile(False)
        enrichmentDF = pd.read_csv(enrichmentPath, dtype=object)
        df = df.merge(enrichmentDF, "left", ["taxon_id", rank.lower()])

    df.to_csv(outputFilePath, index=False)

def _parseContent(content: str, taxonID: str, rank: str) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")

    distribution = soup.find("div", {"id": "afdDistribution"})
    distributionData = {}
    if distribution is not None:
        for heading in distribution.find_all("h4"):
            key = heading.text.lower().replace(" ", "_")

            if key in ("australian_region", "afrotropical_region"):
                regionData = {}
                countries = heading.find_next("ul")
                if countries is None:
                    continue
                
                for countryDotPoints in countries.findChildren("li"):
                    countryName = countryDotPoints.find_next("strong").text
                    stateData = {}

                    stateDotPoints = countryDotPoints.findChild("ul")
                    if stateDotPoints is not None:
                        for item in stateDotPoints.find_all("li"):
                            itemData = item.text.replace("\n", " ").split(":")
                            if len(itemData) == 1:
                                stateData[itemData[0].strip()] = ""
                            else:
                                itemKey, itemValue = itemData
                                stateData[itemKey.strip()] = ", ".join(i.strip() for i in itemValue.split(","))

                    regionData[countryName] = stateData

                distributionData[key] = regionData

            else:
                value = heading.find_next("p")
                if value is None:
                    continue

                text = value.text.replace("\t", " ").replace("\n", " ").strip()
                text = re.sub(" +", " ", text)
                distributionData[key] = text

    descriptors = soup.find("div", {"id": "afdEcologicalDescriptors"})
    descriptorList = []
    if descriptors is not None:
        for desc in descriptors.find_all("p"):
            text = desc.text.replace("\t", " ").strip()
            if text:
                descriptorList.append(text)
    descriptorData = {"descriptors": "|".join(descriptorList)}

    records = []
    synonyms = soup.find("div", {"id": "afdSynonyms"})
    if synonyms is None:
        return [{"taxon_id": taxonID} | distributionData | descriptorData]

    for synonmn in synonyms.find_all("li"):
        synonymTitle = synonmn.find_next("div")
        synonymData = synonymTitle.find_next("div")

        if synonymData.parent != synonymTitle.parent: # No type data if next div is at a lower level
            continue

        data = {}
        for typeData in synonymData.find_all("div"):
            data[typeData.find("h5").text.lower().replace(" ", "_")[:-1]] = synonymData.find("span").text

        record = {"taxon_id": taxonID, rank: synonymTitle.find("strong").text} | data
        records.append(record | distributionData | descriptorData)

    return records
