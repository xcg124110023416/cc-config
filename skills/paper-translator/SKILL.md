---
name: paper-translator
description: Use when the user provides an academic paper (PDF or image) and wants it translated to Chinese, or asks to translate/analyze a paper. Triggers include: "翻译这篇文献", "translate this paper", "帮我翻译", "翻一下这篇", "帮我看看这篇论文", or any request involving a PDF/image that needs extraction, column-stitching, and Chinese translation output as PDF plus a summary MD.
---

# Paper Translator

## Overview

Extract text from academic paper PDFs via MinerU (formulas as LaTeX, clean structure, no column-joining needed), translate to Chinese, and optionally output a translated PDF. Always generate a structured summary MD.

## When to Use

- User provides a PDF/image of an academic paper and asks for translation
- User says "翻译这篇文献/论文", "translate this paper", "帮我翻一下"
- User wants both translation + structured summary (创新点, 实验, 结论)
- Paper text is garbled when copy-pasted (common with PDF text extraction)

**Skip for:** Simple text snippets, non-academic documents, or when user only wants raw extraction without translation.

## Core Workflow

**Default (fast path):** MinerU extract → chat overview → save `_总结.md` → ask whether to translate → cleanup after answer.
**Full pipeline (on request only):** MinerU extract → full translation → PDF → update summary MD → cleanup.

```
Input PDF/Image  ->  [MinerU flash-extract]  ->  [Chat overview + save 总结.md]
                                                          |
                                              ask: "需要全文翻译吗？"
                                              user says "翻译" / "不用"
                                                    |            |
                                         [Translate + PDF]     cleanup
                                               |
                                         [update 总结.md + cleanup]
```

### Step 1: Environment Check

MinerU CLI must be installed:
```bash
npm install -g mineru-open-api
```

Verify: `mineru-open-api version`

No token needed for `flash-extract` mode. No Python dependencies required for extraction.

### Step 2: Extract Text with MinerU

**First, check if `原名_提取.md` already exists** alongside the PDF. If yes, skip extraction — read it directly.

If not, run MinerU:

```bash
mineru-open-api flash-extract "input.pdf" -o ./extracted/
```

The output is `extracted/论文名.md`. Immediately move it to `论文名_提取.md` alongside the original PDF:

```bash
mv ./extracted/*.md "论文名_提取.md"
rm -rf ./extracted/
```

The `_提取.md` file features:
- Proper heading hierarchy (H1 title, H2 sections)
- Math formulas preserved as `$$...$$` LaTeX blocks
- Tables as Markdown tables
- Images marked with `<!-- image-->` placeholders
- No column-joining needed — MinerU handles layout natively

**IF the paper is >20 pages or >10MB**, use `extract` mode (requires free token from https://mineru.net/apiManage/token):
```bash
mineru-open-api auth
mineru-open-api extract "input.pdf" -o ./extracted/
```

### Step 3: Translate and Generate PDF

Read the `_提取.md` file directly. Translate and embed into a Python build script — **no separate `translated.txt` file needed.**

Translation rules:
- **Preserve structure:** Keep section headings (Abstract, Introduction, Related Work, Method, Experiment, Conclusion, References)
- **Technical terms:** Translate consistently. Include English term in parentheses on first occurrence
- **Numbers, formulas, citations:** Keep as-is. Do not translate LaTeX math (`$$...$$` blocks), numbers, or citation markers like [1], [23]
- **Figure markers:** Keep `<!-- image-->` and Fig.X captions intact
- **Table data:** Translate captions, keep numeric data as-is

PDF generation uses **ReportLab + msyh.ttc** (NOT fpdf2). SimHei is not used (monospace Latin causes uneven spacing). Embed translated text directly in a triple-quoted string in the build script, run it, then clean up the script:

```python
# build_pdf.py — translated text embedded, read & run once then delete
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("CJK", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
# ... styles, doc setup, then story.append(Paragraph(translated_line, body_style))
doc.build(story)
```

### Step 5: Generate Summary MD

Create `原文件名_总结.md` with these four sections:

```markdown
# 《论文标题》- 论文总结

## 论文概述
[1-2段：这篇文献研究什么问题，背景是什么，核心目标是什么]

## 创新点
- **创新点1：** [具体描述]
- **创新点2：** [具体描述]
- **创新点3：** [如有]

## 实验与验证
[描述论文做了什么实验来验证方法有效性]
- **数据集：** [使用的数据集]
- **对比方法：** [baseline/对比方法]
- **评价指标：** [指标名称]
- **主要结果：** [关键数据与发现]

## 结论
[1段：论文的核心结论与未来工作方向]
```

The summary should be **analytical**, not just listing. Explain the WHY behind innovations and results.

### Step 4: Clean Up

After output verified, delete: `build_pdf.py`, any `extracted/` directory, MinerU temp outputs. Keep only: original PDF, `_提取.md`, `_总结.md`, `_翻译.pdf` (if translated).

## Common Mistakes

- **Using Tesseract/paper_ocr.py as primary extractor** — MinerU is now the default. Only fall back to Tesseract if MinerU is unavailable or the paper has unusual layout issues.
- **Translating captions without context** — Figure/table captions reference visual content. Note the figure number but don't fabricate descriptions.
- **Omitting references** — Keep the reference list in the translation.
- **Skipping the summary** — The MD summary is half the value.
- **Forgetting CJK fonts in PDF** — Use msyh.ttc, not simhei.ttf.
- **Discarding the extracted Markdown** — Keep it; it's clean and useful.

## Tool Selection Guide

| Task | Tool | Why |
|------|------|-----|
| PDF -> Markdown | MinerU flash-extract | Clean output, formulas as LaTeX, native layout handling |
| Fallback extraction | paper_ocr.py + Tesseract | When MinerU unavailable |
| Translation | Claude (in-session) | Best quality for academic/technical translation |
| PDF generation | ReportLab + TTFont (msyh.ttc) | Reliable CJK embedding, correct Latin-CJK spacing |
| Summary writing | Claude (in-session) | Requires analytical reasoning about paper content |
