"""Pré-check: o PDF tem camada de texto?

Statement escaneado não é escopo. Aborta com mensagem clara em vez de
devolver um parser vazio que parece ter corrido bem.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

__all__ = ["ScannedPdfError", "TextLayerReport", "text_layer_report", "assert_text_layer"]


class ScannedPdfError(RuntimeError):
    """O PDF não tem camada de texto utilizável."""


@dataclass(frozen=True)
class TextLayerReport:
    path: str
    page_count: int
    pages_with_text: int
    chars_total: int

    @property
    def ratio(self) -> float:
        return self.pages_with_text / self.page_count if self.page_count else 0.0

    def __str__(self) -> str:
        return (
            f"{self.path}: {self.page_count} páginas, "
            f"{self.pages_with_text} com texto ({self.ratio:.0%}), "
            f"{self.chars_total} caracteres"
        )


def text_layer_report(pdf_path: str | Path) -> TextLayerReport:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF não encontrado: {path}")

    pages_with_text = 0
    chars_total = 0
    with fitz.open(path) as doc:
        page_count = doc.page_count
        for page in doc:
            text = page.get_text().strip()
            if text:
                pages_with_text += 1
                chars_total += len(text)

    return TextLayerReport(
        path=str(path),
        page_count=page_count,
        pages_with_text=pages_with_text,
        chars_total=chars_total,
    )


def assert_text_layer(pdf_path: str | Path, *, min_ratio: float = 0.5) -> TextLayerReport:
    """Levanta ScannedPdfError se o PDF não tiver texto suficiente."""
    report = text_layer_report(pdf_path)
    if report.page_count == 0:
        raise ScannedPdfError(f"{report.path}: PDF sem páginas.")
    if report.pages_with_text == 0:
        raise ScannedPdfError(
            f"{report.path}: nenhuma página tem camada de texto. "
            "Parece um documento escaneado — fora do escopo deste parser (não há OCR)."
        )
    if report.ratio < min_ratio:
        raise ScannedPdfError(
            f"{report.path}: só {report.ratio:.0%} das páginas têm texto "
            f"(mínimo {min_ratio:.0%}). Documento provavelmente misto/escaneado."
        )
    return report


if __name__ == "__main__":  # pragma: no cover
    import sys

    for arg in sys.argv[1:]:
        print(assert_text_layer(arg))
