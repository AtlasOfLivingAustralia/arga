FROM python:3.11
LABEL org.opencontainers.image.source="https://github.com/ARGA-Genomes/arga-data"
LABEL org.opencontainers.image.description="A container image with the data processing tools of ARGA"
LABEL org.opencontainers.image.licenses="AGPL-3.0"

WORKDIR /usr/src/arga-data
RUN apt-get update && apt-get install -y jq curl zlib1g libxml2 libxslt1.1 && rm -rf /var/lib/apt/lists/*
COPY . .

RUN pip install --no-cache-dir -r reqs.txt

RUN mkdir logs data

ENV PYTHONPATH="${PYTHONPATH}:/usr/src/arga-data/src"

VOLUME /usr/src/arga-data/data
VOLUME /usr/src/arga-data/logs
