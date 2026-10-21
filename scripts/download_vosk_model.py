from pathlib import Path
import shutil
import tempfile
from urllib.request import urlretrieve
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_ROOT = ROOT / "models"
MODEL_PATH = MODEL_ROOT / MODEL_NAME


def main():
    if (MODEL_PATH / "am" / "final.mdl").is_file():
        print(f"Vosk English model is already installed at {MODEL_PATH}")
        return
    if MODEL_PATH.exists():
        raise RuntimeError(f"Incomplete model directory already exists: {MODEL_PATH}")

    MODEL_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=MODEL_ROOT, prefix="vosk-download-") as temporary:
        temporary_path = Path(temporary)
        archive_path = temporary_path / f"{MODEL_NAME}.zip"
        extraction_path = temporary_path / "extracted"
        print(f"Downloading {MODEL_URL}")
        urlretrieve(MODEL_URL, archive_path)

        extraction_path.mkdir()
        with ZipFile(archive_path) as archive:
            root = extraction_path.resolve()
            for member in archive.infolist():
                destination = (extraction_path / member.filename).resolve()
                if not destination.is_relative_to(root):
                    raise RuntimeError("Model archive contains an unsafe file path.")
            archive.extractall(extraction_path)

        downloaded_model = extraction_path / MODEL_NAME
        if not (downloaded_model / "am" / "final.mdl").is_file():
            raise RuntimeError("Downloaded archive did not contain a valid Vosk English model.")
        shutil.move(str(downloaded_model), str(MODEL_PATH))

    print(f"Vosk English model installed at {MODEL_PATH}")


if __name__ == "__main__":
    main()
