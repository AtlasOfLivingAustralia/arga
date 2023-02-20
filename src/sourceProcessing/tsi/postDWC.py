def process(df, database):
    if database == "mouse":
        df['scientificName'] = "Mastacomys fuscus"
        df['bibliographicCitation'] = "This data was produced by Museums Victoria as part of the Genomic Analysis of Broad-toothed Rats project with funding from the Victorian and Australian Government’s Bushfire Biodiversity Response and Recovery program, further support was provided by the University of Sydney, Amazon Web Services Open Data Sets, and the Australian Genome Research Facility." 

    elif database == "koala":
        df['scientificName'] = "Phascolarctos cinereus"

    return df