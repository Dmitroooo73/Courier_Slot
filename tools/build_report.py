"""Build the formatted report (.docx) from docs/report.md.

Run from the project root after `pip install -r requirements-docs.txt`:
    python tools/build_report.py
Supported Markdown: # title, ## / ### headings, paragraphs, - and 1. lists, pipe tables with a preceding
"Таблица: caption" line, images ![caption](path), **bold**, `code`, [text](link).
"""
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "report.md"
OUTPUT = ROOT / "docs" / "deliverables" / "Пульт_доставки_отчёт.docx"
FONT = "Times New Roman"
INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")
# Title page in the university's usual layout.
INSTITUTION = (
    "Министерство цифрового развития, связи и массовых коммуникаций Российской Федерации",
    "Ордена Трудового Красного Знамени",
    "федеральное государственное бюджетное образовательное учреждение высшего образования",
    "МОСКОВСКИЙ ТЕХНИЧЕСКИЙ УНИВЕРСИТЕТ СВЯЗИ И ИНФОРМАТИКИ",
)
AUTHOR = ("Выполнил:", "студент Риц Д. А.", "группы БВТ2304")
SUPERVISOR = ("Проверил:", "Тетерин Н. Н.")
CITY_YEAR = "Москва 2026"


def field(paragraph, code: str) -> None:
    run = paragraph.add_run()
    begin, instruction, separate, end = (OxmlElement("w:fldChar"), OxmlElement("w:instrText"),
                                         OxmlElement("w:fldChar"), OxmlElement("w:fldChar"))
    begin.set(qn("w:fldCharType"), "begin")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = code
    separate.set(qn("w:fldCharType"), "separate")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction, separate):
        run._r.append(element)
    placeholder = paragraph.add_run("Обновите поле: правой кнопкой → «Обновить поле»" if "TOC" in code else "1")
    placeholder._r.append(end)


def styles(document: Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(14)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    fmt = normal.paragraph_format
    fmt.line_spacing = 1.5
    fmt.first_line_indent = Cm(1.25)
    fmt.space_after = Pt(0)
    fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for name, size in (("Heading 1", 16), ("Heading 2", 14)):
        style = document.styles[name]
        style.font.name = FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        style.paragraph_format.first_line_indent = Cm(1.25)
        style.paragraph_format.space_before = Pt(12)
        style.paragraph_format.space_after = Pt(12)
        style.paragraph_format.keep_with_next = True
    for name in ("List Bullet",):
        style = document.styles[name]
        style.font.name = FONT
        style.font.size = Pt(14)
        style.paragraph_format.line_spacing = 1.5
        style.paragraph_format.first_line_indent = Cm(0)


def inline(paragraph, text: str) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(12)
        elif part.startswith("["):
            label, target = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            paragraph.add_run(label if not target.startswith("http") or label == target else f"{label} ({target})")
        else:
            paragraph.add_run(part)


def centered(document: Document, text: str = "", *, bold=False, size=14, highlight=False, space_after=0):
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.first_line_indent = Cm(0)
    paragraph.paragraph_format.space_after = Pt(space_after)
    if text:
        run = paragraph.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        if highlight:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    return paragraph


def title_line(document: Document, text: str, *, align=WD_ALIGN_PARAGRAPH.CENTER, before=0, after=6):
    paragraph = document.add_paragraph()
    paragraph.alignment = align
    fmt = paragraph.paragraph_format
    fmt.first_line_indent = Cm(0)
    fmt.line_spacing = 1.15
    fmt.space_before, fmt.space_after = Pt(before), Pt(after)
    paragraph.add_run(text)
    return paragraph


def title_page(document: Document, title: str) -> None:
    for line in INSTITUTION:
        title_line(document, line)
    title_line(document, "Проектная работа", before=110)
    title_line(document, "По дисциплине “Проектный практикум”")
    title_line(document, "Тема № 15 «Доставка готовой еды»")
    title_line(document, f"«{title}»")
    for index, line in enumerate(AUTHOR + SUPERVISOR):
        before = 110 if index == 0 else 12 if line == SUPERVISOR[0] else 0
        title_line(document, line, align=WD_ALIGN_PARAGRAPH.RIGHT, before=before, after=4)
    # City and year sit in the first page's own footer, so they stay at the bottom of the title page.
    footer = document.sections[0].first_page_footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.first_line_indent = Cm(0)
    run = footer.add_run(CITY_YEAR)
    run.font.name, run.font.size = FONT, Pt(14)
    document.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)


def contents(document: Document) -> None:
    centered(document, "СОДЕРЖАНИЕ", bold=True, size=16, space_after=12)
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.first_line_indent = Cm(0)
    field(paragraph, 'TOC \\o "1-2" \\h \\z \\u')


def table(document: Document, rows: list[list[str]], caption: str | None, number: int) -> None:
    if caption:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.first_line_indent = Cm(0)
        paragraph.paragraph_format.keep_with_next = True
        paragraph.add_run(f"Таблица {number} — {caption}")
    header, *body = rows
    grid = document.add_table(rows=1, cols=len(header))
    grid.style = "Table Grid"
    grid.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row_cells, values, bold in ((grid.rows[0].cells, header, True), *((None, row, False) for row in body)):
        cells = row_cells or grid.add_row().cells
        for cell, value in zip(cells, values, strict=True):
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.first_line_indent = Cm(0)
            paragraph.paragraph_format.line_spacing = 1.0
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            inline(paragraph, value)
            for run in paragraph.runs:
                run.font.size = Pt(12)
                run.bold = run.bold or bold
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def figure(document: Document, path: Path, caption: str, number: int, *, max_width=16.5, max_height=22.0) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.first_line_indent = Cm(0)
    paragraph.paragraph_format.keep_with_next = True
    from PIL import Image

    with Image.open(path) as image:
        ratio = image.height / image.width
    width = Cm(max_width) if ratio * max_width <= max_height else Cm(max_height / ratio)
    paragraph.add_run().add_picture(str(path), width=width)
    caption_paragraph = centered(document, f"Рисунок {number} — {caption}", space_after=12)
    caption_paragraph.runs[0].font.size = Pt(14)


def orient(section, *, landscape: bool) -> None:
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = (Cm(29.7), Cm(21)) if landscape else (Cm(21), Cm(29.7))
    section.left_margin, section.right_margin = Cm(3), Cm(1.5)
    section.top_margin, section.bottom_margin = Cm(2), Cm(2)


def build() -> Path:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    document = Document()
    section = document.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.left_margin, section.right_margin = Cm(3), Cm(1.5)
    section.top_margin, section.bottom_margin = Cm(2), Cm(2)
    section.different_first_page_header_footer = True
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field(footer, "PAGE")
    styles(document)

    title = lines[0].lstrip("# ").strip()
    title_page(document, title)
    contents(document)
    tables = figures = 0
    caption = None
    index = 1
    while index < len(lines):
        line = lines[index].rstrip()
        if not line:
            index += 1
            continue
        if line.startswith("Отчёт по проектной работе"):
            index += 1  # repository note; the title page carries this information
            continue
        if line.startswith("## "):
            # Each top-level section starts on a new page.
            document.add_heading(line[3:], level=1).paragraph_format.page_break_before = True
        elif line.startswith("### "):
            document.add_heading(line[4:], level=2)
        elif line.startswith("Таблица: "):
            caption = line[len("Таблица: "):]
        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            tables += 1
            table(document, rows, caption, tables)
            caption = None
            continue
        elif match := re.fullmatch(r"!\[([^\]]+)\]\(([^)]+)\)", line):
            figures += 1
            if match.group(2).endswith("architecture.png"):
                # The architecture scheme gets its own landscape page so its labels stay readable.
                orient(document.add_section(WD_SECTION.NEW_PAGE), landscape=True)
                figure(document, SOURCE.parent / match.group(2), match.group(1), figures, max_width=24.5, max_height=15)
                orient(document.add_section(WD_SECTION.NEW_PAGE), landscape=False)
            else:
                figure(document, SOURCE.parent / match.group(2), match.group(1), figures)
        elif re.match(r"\d+\. ", line):
            # Written numbers: Word's shared list numbering would continue across separate lists.
            inline(document.add_paragraph(), line)
        elif line.startswith("- "):
            inline(document.add_paragraph(style="List Bullet"), line[2:])
        else:
            inline(document.add_paragraph(), line)
        index += 1
    # Ask Word to refresh the table of contents and page numbers when the file is opened.
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    document.settings.element.append(update)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(f"Сохранено: {build().relative_to(ROOT)}")
