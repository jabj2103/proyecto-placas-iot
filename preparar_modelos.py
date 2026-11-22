"""Descarga el detector y el lector desde sus fuentes originales."""

from pathlib import Path

from fast_plate_ocr.inference.hub import download_model
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError


def main():
    repositorio = "Koushim/yolov8-license-plate-detection"
    try:
        detector = hf_hub_download(repositorio, "best.pt", local_files_only=True)
    except LocalEntryNotFoundError:
        detector = hf_hub_download(repositorio, "best.pt")
    print(f"Detector disponible en la caché de Hugging Face: {detector}")

    carpeta = Path(__file__).resolve().parent / "work" / "ocr_comparacion" / "modelos" / "fastplate"
    archivos = ("cct_s_v2_global.onnx", "cct_s_v2_global_plate_config.yaml")
    if not all((carpeta / nombre).is_file() for nombre in archivos):
        carpeta.mkdir(parents=True, exist_ok=True)
        download_model("cct-s-v2-global-model", save_directory=carpeta)
    print(f"FastPlateOCR disponible en: {carpeta}")


if __name__ == "__main__":
    main()
