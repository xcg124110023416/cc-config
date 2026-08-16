"""
Academic paper OCR extractor with two-column layout support.

Dependencies: pip install pymupdf pytesseract pillow
Also requires tesseract-ocr installed on the system.

Usage:
    python paper_ocr.py input.pdf [--lang eng] [--output extracted.txt] [--tesseract-path PATH]
    python paper_ocr.py input.png [--lang eng] [--output extracted.txt]
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
import pytesseract
from PIL import Image


@dataclass
class TextBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    block_type: str = "text"  # text, title, abstract_header, etc.


def pdf_to_images(pdf_path: Path, dpi: int = 300) -> List[Tuple[Image.Image, int]]:
    """Convert PDF pages to PIL Images. Returns list of (image, page_number)."""
    doc = fitz.open(str(pdf_path))
    images = []
    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append((img, i + 1))
    doc.close()
    return images


def image_to_blocks(image: Image.Image, page_num: int) -> List[TextBlock]:
    """Run OCR on an image and return text blocks with bounding boxes."""
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    blocks = []
    current_block_text = []
    current_block_num = -1
    min_x, min_y, max_x, max_y = 99999, 99999, 0, 0

    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        block_num = data["block_num"][i]
        conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0

        if not text or conf < 30:
            continue

        if block_num != current_block_num:
            if current_block_text:
                blocks.append(TextBlock(
                    text=" ".join(current_block_text),
                    x0=min_x, y0=min_y, x1=max_x, y1=max_y,
                    page=page_num
                ))
            current_block_text = []
            current_block_num = block_num
            min_x, min_y, max_x, max_y = 99999, 99999, 0, 0

        current_block_text.append(text)
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)

    # Don't forget the last block
    if current_block_text:
        blocks.append(TextBlock(
            text=" ".join(current_block_text),
            x0=min_x, y0=min_y, x1=max_x, y1=max_y,
            page=page_num
        ))

    return blocks


def detect_columns(blocks: List[TextBlock]) -> Optional[float]:
    """
    Detect if page uses two-column layout and return the x-coordinate
    that splits left/right columns. Returns None if single column.
    """
    if len(blocks) < 4:
        return None

    # Get horizontal centers of all blocks
    centers = [(b.x0 + b.x1) / 2 for b in blocks]
    page_width = max(b.x1 for b in blocks)

    # Count blocks in left and right halves
    left_blocks = [c for c in centers if c < page_width / 2]
    right_blocks = [c for c in centers if c >= page_width / 2]

    # Need significant content on both sides to consider it two-column
    if len(left_blocks) >= 2 and len(right_blocks) >= 2:
        # Find the best split point: midpoint between the rightmost left block
        # and the leftmost right block
        left_edge = max(b.x1 for b in blocks if (b.x0 + b.x1) / 2 < page_width / 2)
        right_edge = min(b.x0 for b in blocks if (b.x0 + b.x1) / 2 >= page_width / 2)
        return (left_edge + right_edge) / 2

    return None


def is_full_width(block: TextBlock, page_width: float, threshold: float = 0.85) -> bool:
    """Check if a block spans most of the page width (title, abstract, figure caption)."""
    block_width = block.x1 - block.x0
    return block_width > page_width * threshold


def group_blocks(blocks: List[TextBlock]) -> List[str]:
    """
    Group text blocks into logical sections, handling two-column layout.
    Returns list of text sections in reading order.
    """
    if not blocks:
        return []

    page_width = max(b.x1 for b in blocks)
    split_x = detect_columns(blocks)
    sections = []

    if split_x is None:
        # Single column: just sort top to bottom
        blocks_sorted = sorted(blocks, key=lambda b: (b.y0, b.x0))
        sections = [b.text for b in blocks_sorted]
    else:
        # Two columns: separate full-width and column blocks
        full_width = [b for b in blocks if is_full_width(b, page_width)]
        col_blocks = [b for b in blocks if not is_full_width(b, page_width)]

        left_col = [b for b in col_blocks if (b.x0 + b.x1) / 2 < split_x]
        right_col = [b for b in col_blocks if (b.x0 + b.x1) / 2 >= split_x]

        # Sort each column top-to-bottom
        full_width.sort(key=lambda b: b.y0)
        left_col.sort(key=lambda b: b.y0)
        right_col.sort(key=lambda b: b.y0)

        # Merge: interleave full-width blocks with column blocks
        all_items = (
            [("fw", b) for b in full_width]
            + [("left", b) for b in left_col]
            + [("right", b) for b in right_col]
        )
        all_items.sort(key=lambda x: (x[1].y0, 0 if x[0] == "fw" else (1 if x[0] == "left" else 2)))

        # Build sections: all full-width blocks appear in order,
        # followed by left column, then right column
        fw_texts = [b.text for b in full_width]
        left_texts = [" ".join(b.text for b in left_col)]
        right_texts = [" ".join(b.text for b in right_col)]

        sections = fw_texts + left_texts + right_texts

    return sections


# Caption regex patterns
FIG_CAPTION_RE = re.compile(
    r'^\s*(Fig(?:ure)?\.?\s*\d+)', re.IGNORECASE
)
TABLE_CAPTION_RE = re.compile(
    r'^\s*(TABLE\s+[IVX\d]+|Table\.?\s*\d+)', re.IGNORECASE
)


def tag_caption_blocks(blocks: List[TextBlock]):
    """Tag blocks that start with figure/table caption patterns."""
    for b in blocks:
        stripped = b.text.strip()
        if FIG_CAPTION_RE.match(stripped):
            b.block_type = "figure"
            # Rewrite text to include placeholder marker
            b.text = "【" + stripped + "】"
        elif TABLE_CAPTION_RE.match(stripped):
            b.block_type = "table"
            b.text = "【" + stripped + "】"


def insert_placeholders(sections: List[str]) -> List[str]:
    """Already handled by tag_caption_blocks at the block level."""
    return sections


def extract_text(file_path: Path, lang: str = "eng") -> str:
    """
    Main entry: extract text from PDF or image with column joining.
    """
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        images = pdf_to_images(file_path)
    elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
        img = Image.open(str(file_path))
        images = [(img, 1)]
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    all_sections = []
    for img, page_num in images:
        blocks = image_to_blocks(img, page_num)
        if not blocks:
            all_sections.append(f"\n--- Page {page_num} (no text detected) ---\n")
            continue

        tag_caption_blocks(blocks)
        sections = group_blocks(blocks)
        all_sections.append(f"\n--- Page {page_num} ---\n")
        all_sections.extend(sections)

    return "\n\n".join(all_sections)


def main():
    parser = argparse.ArgumentParser(
        description="Extract text from academic paper PDF/images with two-column support"
    )
    parser.add_argument("input", type=Path, help="Input PDF or image file")
    parser.add_argument("--lang", default="eng", help="Tesseract language (default: eng)")
    parser.add_argument("--output", "-o", type=Path, help="Output text file (default: stdout)")
    parser.add_argument("--tesseract-path", type=Path, help="Path to tesseract.exe (if not in PATH)")
    args = parser.parse_args()

    # Auto-detect tesseract on Windows
    if args.tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = str(args.tesseract_path)
    elif sys.platform == "win32":
        import subprocess
        # Search common paths
        candidates = [
            r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS Inspection 2024 Standalone\TrainableOCR\tesseract.exe",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for p in candidates:
            if Path(p).exists():
                pytesseract.pytesseract.tesseract_cmd = p
                break
        # Fallback: try PATH
        if not hasattr(pytesseract.pytesseract, 'tesseract_cmd') or not pytesseract.pytesseract.tesseract_cmd:
            try:
                result = subprocess.run(["where", "tesseract"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    pytesseract.pytesseract.tesseract_cmd = result.stdout.strip().split("\n")[0].strip()
            except Exception:
                pass

    if not args.input.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"Extracting text from: {args.input}")

    try:
        text = extract_text(args.input, lang=args.lang)
    except Exception as e:
        print(f"Error during extraction: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Output written to: {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
