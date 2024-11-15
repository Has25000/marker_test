import functools
from typing import Dict, List, Optional, Set, Tuple

import pypdfium2 as pdfium
from pdftext.extraction import dictionary_output
from PIL import Image
from pydantic import BaseModel

from marker.ocr.heuristics import detect_bad_ocr
from marker.v2.providers import BaseProvider
from marker.v2.schema.polygon import PolygonBox
from marker.v2.schema.text.line import Line, Span

PdfPageProviderLine = Tuple[List[Line], List[List[Span]]]
PdfPageProviderLines = Dict[int, PdfPageProviderLine]


class PdfProvider(BaseProvider):
    page_range: List[int] | None = None
    pdftext_workers: int = 4
    flatten_pdf: bool = True

    def __init__(self, filepath: str, config: Optional[BaseModel] = None):
        super().__init__(filepath, config)

        self.page_lines: PdfPageProviderLines = {}
        self.doc: pdfium.PdfDocument

        self.setup()

    def __len__(self) -> int:
        return len(self.doc)

    def __del__(self):
        self.doc.close()

    def font_flags_to_format(self, flags: int) -> Set[str]:
        flag_map = {
            1: "FixedPitch",
            2: "Serif",
            3: "Symbolic",
            4: "Script",
            6: "Nonsymbolic",
            7: "Italic",
            17: "AllCap",
            18: "SmallCap",
            19: "ForceBold",
            20: "UseExternAttr"
        }
        set_flags = set()
        for bit_position, flag_name in flag_map.items():
            if flags & (1 << (bit_position - 1)):
                set_flags.add(flag_name)
        if not set_flags:
            set_flags.add("Plain")

        formats = set()
        if set_flags == {"Symbolic", "Italic"} or \
                set_flags == {"Symbolic", "Italic", "UseExternAttr"}:
            formats.add("math")
        elif set_flags == {"UseExternAttr"}:
            formats.add("plain")
        elif set_flags == {"Plain"}:
            formats.add("plain")
        else:
            if set_flags & {"Italic"}:
                formats.add("italic")
            if set_flags & {"ForceBold"}:
                formats.add("bold")
            if set_flags & {"FixedPitch", "Serif", "Script", "Nonsymbolic", "AllCap", "SmallCap", "UseExternAttr"}:
                formats.add("plain")
        return formats

    def font_names_to_format(self, font_name: str) -> Set[str]:
        formats = set()
        if "bold" in font_name.lower():
            formats.add("bold")
        if "ital" in font_name.lower():
            formats.add("italic")
        return formats

    def setup(self):
        self.doc = pdfium.PdfDocument(self.filepath)
        page_char_blocks = dictionary_output(
            self.filepath,
            page_range=self.page_range,
            keep_chars=False,
            workers=self.pdftext_workers,
            flatten_pdf=self.flatten_pdf
        )
        for page in page_char_blocks:
            page_id = page["page"]
            lines: List[Line] = []
            line_spans: List[List[Span]] = []
            for block in page["blocks"]:
                for line in block["lines"]:
                    spans: List[Span] = []
                    for span in line["spans"]:
                        if not span["text"].strip():
                            continue
                        font_formats = self.font_flags_to_format(span["font"]["flags"]).union(self.font_names_to_format(span["font"]["name"]))
                        spans.append(
                            Span(
                                polygon=PolygonBox.from_bbox(span["bbox"]),
                                text=span["text"].strip(),
                                font=span["font"]["name"],
                                font_weight=span["font"]["weight"],
                                font_size=span["font"]["size"],
                                minimum_position=span["char_start_idx"],
                                maximum_position=span["char_end_idx"],
                                formats=list(font_formats),
                                page_id=page_id,
                            )
                        )
                    lines.append(
                        Line(
                            polygon=PolygonBox.from_bbox(line["bbox"]),
                            page_id=page_id,
                        )
                    )
                    line_spans.append(spans)
            if self.check_line_spans(line_spans):
                self.page_lines[page_id] = (lines, line_spans)

    def check_line_spans(self, line_spans_list: List[List[Span]]) -> bool:
        if not len(sum(line_spans_list, [])):
            return False
        text = ""
        for line_spans in line_spans_list:
            for span in line_spans:
                text = text + " " + span.text
            text = text + "\n"
        if len(text.strip()) == 0:
            return False
        if detect_bad_ocr(text):
            return False
        return True

    @ functools.lru_cache(maxsize=None)
    def get_image(self, idx: int, dpi: int) -> Image.Image:
        page = self.doc[idx]
        image = page.render(scale=dpi / 72, draw_annots=False).to_pil()
        image = image.convert("RGB")
        return image

    def get_page_bbox(self, idx: int) -> List[float]:
        page = self.doc[idx]
        return page.get_bbox()

    def get_page_lines(self, idx: int) -> PdfPageProviderLine:
        return self.page_lines[idx]
