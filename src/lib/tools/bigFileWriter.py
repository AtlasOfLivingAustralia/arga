import csv
from pathlib import Path
import lib.commonFuncs as cmn
import pandas as pd
import sys
from enum import Enum
import pyarrow as pa
import pyarrow.parquet as pq
from lib.tools.logger import Logger
from typing import Iterator
from lib.tools.progressBar import SteppableProgressBar

class Format(Enum):
    CSV = ".csv"
    TSV = ".tsv"
    PARQUET = ".parquet"

class Subfile:

    fileFormat = Format.CSV

    def __new__(cls, *args):
        subclassMap = {subclass.fileFormat: subclass for subclass in cls.__subclasses__()}
        subclass = subclassMap.get(args[-1], cls)
        return super().__new__(subclass)

    def __init__(self, location: Path, fileName: str, format: Format) -> 'Subfile':
        self.fileName = fileName
        self.filePath = location / f"{fileName}{Format(format).value}"
        self.size = self.filePath.stat().st_size if self.filePath.exists() else 0

    def __repr__(self) -> str:
        return f"{self.filePath}"
    
    @classmethod
    def fromFilePath(cls, filePath: Path) -> 'Subfile':
        location = filePath.parent
        fileName = filePath.stem
        fileFormat = Format(filePath.suffix.lower())
        return cls(location, fileName, fileFormat)
    
    def write(self, df: pd.DataFrame) -> None:
        df.to_csv(self.filePath, index=False)
    
    def read(self, **kwargs) -> pd.DataFrame | None:
        try:
            return pd.read_csv(self.filePath, **kwargs)
        except pd.errors.EmptyDataError:
            return None
    
    def readChunks(self, chunkSize: int, **kwargs) -> Iterator[pd.DataFrame] | None:
        return self.read(chunksize=chunkSize, **kwargs)

    def rename(self, newFilePath: Path, newFileFormat: Format) -> None:
        if newFileFormat == self.fileFormat:
            self.filePath.rename(newFilePath)
            return
        
        df = self.read()
        
        if newFileFormat == Format.CSV:
            df.to_csv(newFilePath, index=False)

        elif newFileFormat == Format.TSV:
            df.to_csv(newFilePath, sep="\t", index=False)
            
        elif newFileFormat == Format.PARQUET:
            df.to_parquet(newFilePath, "pyarrow", index=False)

        self.remove()
        
    def remove(self) -> None:
        self.filePath.unlink()

    def getColumns(self) -> list[str]:
        df = self.read(nrows=1)
        if df is None:
            return []
        return list(df.columns)

class TSVSubfile(Subfile):

    fileFormat = Format.TSV

    def write(self, df: pd.DataFrame) -> None:
        df.to_csv(self.filePath, sep="\t", index=False)

    def read(self, **kwargs) -> pd.DataFrame | None:
        return super().read(sep="\t", **kwargs)
    
class PARQUETSubfile(Subfile):

    fileFormat = Format.PARQUET

    def write(self, df: pd.DataFrame) -> None:
        df.to_parquet(self.filePath, "pyarrow", index=False)

    def read(self, **kwargs) -> pd.DataFrame | None:
        # return pd.read_parquet(self.filePath, "pyarrow", **kwargs)
        try:
            return pq.read_table(self.filePath, **kwargs).to_pandas()
        except pd.errors.EmptyDataError:
            return None
    
    def readChunks(self, chunkSize: int, **kwargs) -> Iterator[pd.DataFrame]:
        parquetFile = pq.ParquetFile(self.filePath)
        return (batch.to_pandas() for batch in parquetFile.iter_batches(batch_size=chunkSize, **kwargs))
    
    def getColumns(self) -> list[str]:
        pf = pq.read_schema(self.filePath)
        return pf.names

class BigFileWriter:
    def __init__(self, outputFile: Path, subDirName: str = "chunks", subsectionPrefix: str = "chunk", subfileType: Format = Format.PARQUET) -> 'BigFileWriter':
        self.outputFile = outputFile
        self.outputFileType = Format(outputFile.suffix)
        self.subfileDir = outputFile.parent / subDirName
        self.sectionPrefix = subsectionPrefix
        self.subfileType = subfileType

        self.writtenFiles: list[Subfile] = []
        self.globalColumns: list[str] = []

        maxInt = sys.maxsize
        while True:
            try:
                csv.field_size_limit(maxInt)
                return
            except OverflowError:
                maxInt = int(maxInt/10)

    def populateFromFolder(self, folderPath: Path = None, logIndividually: bool = False) -> None:
        if folderPath is None:
            folderPath = self.subfileDir
        elif not folderPath.exists():
            return
        
        fileCount = 0
        for filePath in folderPath.iterdir():
            if not filePath.suffix in Format._value2member_map_.keys():
                continue

            subFile = Subfile.fromFilePath(filePath)
            columns = subFile.getColumns()
            if not columns:
                filePath.unlink()
                continue

            self.writtenFiles.append(subFile)
            self.globalColumns = cmn.extendUnique(self.globalColumns, columns)

            if logIndividually:
                Logger.info(f"Added file: {subFile.filePath}")

            fileCount += 1

        Logger.info(f"Added {fileCount} files to written files list")

    def getSubfileCount(self) -> int:
        return len(self.writtenFiles)
    
    def getSubfileNames(self) -> list[str]:
        return [subfile.fileName for subfile in self.writtenFiles]

    def writeDF(self, df: pd.DataFrame, customName: str = "", format: Format = None) -> None:
        if not self.subfileDir.exists():
            self.subfileDir.mkdir(parents=True)

        if format is None:
            format = self.subfileType

        if customName:
            fileName = customName
            suffix = 0

            while fileName in self.getSubfileNames():
                fileName = f"{customName}_{suffix}"
                suffix += 1

        else:
            fileName = f"{self.sectionPrefix}_{len(self.writtenFiles)}"

        subfile = Subfile(self.subfileDir, fileName, format)
        subfile.write(df)

        self.writtenFiles.append(subfile)
        self.globalColumns = cmn.extendUnique(self.globalColumns, df.columns)

    def oneFile(self, removeOld: bool = True) -> None:
        if self.outputFile.exists():
            Logger.info(f"Removing old file {self.outputFile}")
            self.outputFile.unlink()

        if len(self.writtenFiles) == 1:
            Logger.info(f"Only single subfile, moving {self.writtenFiles[0]} to {self.outputFile}")

            self.writtenFiles[0].rename(self.outputFile, self.outputFileType)
            self.subfileDir.rmdir()
            return

        Logger.info("Combining into one file")
        if self.outputFileType in (Format.CSV, Format.TSV):
            self._oneCSV(removeOld)
        elif self.outputFileType == Format.PARQUET:
            self._oneParquet(removeOld)

        Logger.info(f"\nCreated a single file at {self.outputFile}")
        if removeOld:
            self.subfileDir.rmdir()
            self.writtenFiles.clear()

    def _oneCSV(self, removeOld: bool = True):
        delim = "\t" if self.outputFileType == Format.TSV else ","
        chunkSize = 1024

        # Create empty file with columns
        pd.DataFrame(columns=self.globalColumns).to_csv(self.outputFile, mode="a", sep=delim, index=False)

        progress = SteppableProgressBar(50, len(self.writtenFiles), "Writing")
        for file in self.writtenFiles:
            progress.update()

            chunkIterator = file.readChunks(chunkSize)
            if chunkIterator is not None:
                for chunk in chunkIterator:
                    chunk = chunk.reindex(self.globalColumns, axis=1, fill_value="")
                    chunk.to_csv(self.outputFile, mode="a", sep=delim, index=False, header=False)

            if removeOld:
                file.remove()
        
    def _oneParquet(self, removeOld: bool = True):
        schema = pa.schema([(column, pa.string()) for column in self.globalColumns])
        with pq.ParquetWriter(self.outputFile, schema=schema) as writer:
            progress = SteppableProgressBar(50, len(self.writtenFiles), "Writing")
            for file in self.writtenFiles:
                progress.update()
                
                writer.write_table(pq.read_table(str(file)))

                if removeOld:
                    file.remove()
