"""Local document parsers with bounded image and scanned-PDF OCR."""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pypdf
from pypdf import PdfReader

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
SUPPORTED_PARSE_EXTENSIONS = {
    ".md",
    ".html",
    ".htm",
    ".pdf",
    ".doc",
    ".docx",
    ".pptx",
    ".xlsx",
} | IMAGE_EXTENSIONS
MAX_TEXT_CHARS = 2_000_000
MAX_PDF_TEXT_CHARS = 12_000_000
MAX_PDF_PAGES = 8_000
MAX_OCR_PAGES = 80
OCR_TIMEOUT_SECONDS = 90


class ParseError(Exception):
    pass


@dataclass
class ParseOutput:
    parser_name: str
    parser_version: str
    markdown: str
    structure: dict


def _run_ocr(path: Path) -> str:
    try:
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", "chi_sim+eng"],
            capture_output=True,
            text=True,
            timeout=OCR_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except FileNotFoundError as exc:
        raise ParseError("OCR 组件不可用，请联系管理员检查 Tesseract") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParseError("OCR 处理超时，请缩小文件或降低图片分辨率") from exc
    if result.returncode != 0:
        raise ParseError("OCR 识别失败，请检查图片是否清晰且未损坏")
    return result.stdout.strip()


def _ocr_pdf_page(path: Path, number: int) -> str:
    with tempfile.TemporaryDirectory(prefix="pkb-ocr-") as directory:
        output = Path(directory) / "page"
        try:
            result = subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-r",
                    "150",
                    "-singlefile",
                    "-png",
                    str(path),
                    str(output),
                ],
                capture_output=True,
                timeout=OCR_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ParseError("OCR 页面渲染组件不可用，请联系管理员检查 Poppler") from exc
        except subprocess.TimeoutExpired as exc:
            raise ParseError(f"第 {number} 页 OCR 页面渲染超时") from exc
        image = output.with_suffix(".png")
        if result.returncode != 0 or not image.is_file():
            raise ParseError(f"第 {number} 页无法转换为 OCR 图片")
        return _run_ocr(image)


def _open_office(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ParseError("Office 文件无法读取") from exc
    if sum(item.file_size for item in archive.infolist()) > MAX_TEXT_CHARS * 20:
        archive.close()
        raise ParseError("Office 文件解压后内容超过当前上限")
    return archive


def _office_blocks(path: Path, extension: str) -> list[dict]:
    archive = _open_office(path)
    try:
        if extension == ".docx":
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            paragraphs = []
            for paragraph in root.findall(".//{*}p"):
                text = "".join(paragraph.itertext()).strip()
                if text:
                    paragraphs.append({"type": "paragraph", "text": text})
            return [{"number": 1, "blocks": paragraphs}]
        if extension == ".pptx":
            pages = []
            slides = sorted(
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            for number, name in enumerate(slides, start=1):
                root = ElementTree.fromstring(archive.read(name))
                text = " ".join(part.strip() for part in root.itertext() if part.strip())
                pages.append(
                    {"number": number, "blocks": [{"type": "slide", "text": text}] if text else []}
                )
            return pages
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in shared_root.findall("{*}si")]
        pages = []
        sheets = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        for number, name in enumerate(sheets, start=1):
            root = ElementTree.fromstring(archive.read(name))
            rows = []
            for row in root.findall(".//{*}row"):
                values = []
                for cell in row.findall("{*}c"):
                    value = cell.findtext("{*}v", default="")
                    if cell.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    if value:
                        values.append(value)
                if values:
                    rows.append({"type": "table_row", "text": " | ".join(values)})
            pages.append({"number": number, "blocks": rows})
        return pages
    except (KeyError, ElementTree.ParseError) as exc:
        raise ParseError("Office 文档结构无法解析") from exc
    finally:
        archive.close()


def _render_word_pages(path: Path, extension: str) -> list[dict] | None:
    """Use LibreOffice pagination when available; XML text remains the fallback."""
    with tempfile.TemporaryDirectory(prefix="pkb-word-pages-") as directory:
        work = Path(directory)
        staged = work / f"source{extension}"
        shutil.copyfile(path, staged)
        try:
            result = subprocess.run(
                [
                    "soffice",
                    f"-env:UserInstallation={work.joinpath('profile').as_uri()}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(work),
                    str(staged),
                ],
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        rendered = work / "source.pdf"
        if result.returncode != 0 or not rendered.is_file():
            return None
        try:
            reader = PdfReader(rendered)
            if len(reader.pages) > MAX_PDF_PAGES:
                return None
            pages = []
            total_chars = 0
            for number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                total_chars += len(text)
                if total_chars > MAX_TEXT_CHARS:
                    return None
                pages.append(
                    {
                        "number": number,
                        "blocks": [{"type": "paragraph", "text": text}] if text else [],
                    }
                )
            return pages if any(page["blocks"] for page in pages) else None
        except (OSError, ValueError, IndexError, pypdf.errors.PdfReadError):
            return None


def _check_length(text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        raise ParseError("解析文本超过当前上限")


def _markdown_blocks(source: str) -> list[dict]:
    blocks = []
    paragraph = []
    in_code = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if paragraph:
                blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
                paragraph = []
            in_code = not in_code
            blocks.append({"type": "code_fence", "text": stripped})
        elif in_code:
            blocks.append({"type": "code", "text": line})
        elif match := re.match(r"^(#{1,6})\s+(.+)$", stripped):
            if paragraph:
                blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
                paragraph = []
            blocks.append({"type": "heading", "level": len(match.group(1)), "text": match.group(2)})
        elif not stripped:
            if paragraph:
                blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
                paragraph = []
        else:
            paragraph.append(line)
    if paragraph:
        blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
    return blocks


class _HTMLBlocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.parts: list[str] = []
        self.active_tag = "p"
        self.skipped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "noscript"}:
            self.skipped += 1
            return
        if self.skipped:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre"}:
            self._flush()
            self.active_tag = tag
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "noscript"}:
            self.skipped = max(0, self.skipped - 1)
            return
        if not self.skipped and tag in {
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "li",
            "pre",
            "div",
        }:
            self._flush()
            self.active_tag = "p"

    def handle_data(self, data: str) -> None:
        if not self.skipped:
            self.parts.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self.parts).split())
        self.parts = []
        if not text:
            return
        if self.active_tag.startswith("h") and self.active_tag[1:].isdigit():
            self.blocks.append({"type": "heading", "level": int(self.active_tag[1:]), "text": text})
        elif self.active_tag == "li":
            self.blocks.append({"type": "list_item", "text": text})
        elif self.active_tag == "pre":
            self.blocks.append({"type": "code", "text": text})
        else:
            self.blocks.append({"type": "paragraph", "text": text})

    def finish(self) -> list[dict]:
        self._flush()
        return self.blocks


def _blocks_markdown(blocks: list[dict]) -> str:
    lines = []
    for block in blocks:
        match block["type"]:
            case "heading":
                lines.append(f"{'#' * block['level']} {block['text']}")
            case "list_item":
                lines.append(f"- {block['text']}")
            case "code":
                lines.append(f"```\n{block['text']}\n```")
            case _:
                lines.append(block["text"])
    return "\n\n".join(lines)


def parse_file(path: Path, filename: str) -> ParseOutput:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_PARSE_EXTENSIONS:
        raise ParseError("此格式尚未接入解析器")
    if not path.is_file():
        raise ParseError("原文件暂不可用")
    if extension == ".doc":
        with tempfile.TemporaryDirectory(prefix="pkb-doc-") as directory:
            work = Path(directory)
            source = work / "source.doc"
            shutil.copyfile(path, source)
            try:
                result = subprocess.run(
                    [
                        "soffice",
                        f"-env:UserInstallation={work.joinpath('profile').as_uri()}",
                        "--headless",
                        "--convert-to",
                        "docx:Office Open XML Text",
                        "--outdir",
                        str(work),
                        str(source),
                    ],
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ParseError("旧版 Word 转换组件不可用，请联系管理员检查 LibreOffice") from exc
            except subprocess.TimeoutExpired as exc:
                raise ParseError("旧版 Word 转换超时，请检查文件大小或内容") from exc
            converted = work / "source.docx"
            if result.returncode != 0 or not converted.is_file():
                raise ParseError("旧版 Word 转换失败，请检查文件是否损坏或受密码保护")
            output = parse_file(converted, "source.docx")
            if output.parser_name == "docx-basic":
                output.parser_name = "libreoffice-docx"
            output.structure["parser_config"]["converted_from"] = "doc"
            return output
    if extension in IMAGE_EXTENSIONS:
        text = _run_ocr(path)
        _check_length(text)
        if not text:
            raise ParseError("图片 OCR 未识别到可用文字，请检查清晰度")
        return ParseOutput(
            "tesseract-image",
            "1",
            text,
            {
                "parser_config": {"ocr": True, "languages": "chi_sim+eng"},
                "pages": [{"number": 1, "blocks": [{"type": "paragraph", "text": text}]}],
            },
        )
    if extension in {".docx", ".pptx", ".xlsx"}:
        if extension == ".docx" and (pages := _render_word_pages(path, extension)):
            markdown = "\n\n".join(
                f"## 第 {page['number']} 页\n\n{page['blocks'][0]['text']}"
                for page in pages
                if page["blocks"]
            )
            return ParseOutput(
                "libreoffice-word-pdf",
                pypdf.__version__,
                markdown,
                {"parser_config": {"ocr": False, "pagination": "rendered_pdf"}, "pages": pages},
            )
        pages = _office_blocks(path, extension)
        if extension == ".docx":
            pages[0]["number"] = None
        blocks = [block for page in pages for block in page["blocks"]]
        markdown = _blocks_markdown(blocks)
        _check_length(markdown)
        if not blocks:
            raise ParseError("Office 文档没有可解析的文字")
        parser = {".docx": "docx-basic", ".pptx": "pptx-basic", ".xlsx": "xlsx-basic"}[extension]
        return ParseOutput(parser, "1", markdown, {"parser_config": {"ocr": False}, "pages": pages})
    if extension == ".md":
        source = path.read_text(encoding="utf-8")
        _check_length(source)
        blocks = _markdown_blocks(source)
        if not blocks:
            raise ParseError("文件没有可解析的正文")
        return ParseOutput(
            "markdown-basic",
            "1",
            source,
            {
                "parser_config": {"max_text_chars": MAX_TEXT_CHARS},
                "pages": [{"number": None, "blocks": blocks}],
            },
        )
    if extension in {".html", ".htm"}:
        source = path.read_text(encoding="utf-8")
        _check_length(source)
        parser = _HTMLBlocks()
        parser.feed(source)
        blocks = parser.finish()
        if not blocks:
            raise ParseError("文件没有可解析的正文")
        markdown = _blocks_markdown(blocks)
        _check_length(markdown)
        return ParseOutput(
            "html-basic",
            "1",
            markdown,
            {
                "parser_config": {"max_text_chars": MAX_TEXT_CHARS, "strip_scripts": True},
                "pages": [{"number": None, "blocks": blocks}],
            },
        )

    try:
        reader = PdfReader(path)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ParseError("PDF 已加密，需要先解除密码")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ParseError("PDF 页数超过当前解析上限")
        pages = []
        markdown_parts = []
        total_chars = 0
        ocr_pages = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                ocr_pages += 1
                if ocr_pages > MAX_OCR_PAGES:
                    raise ParseError(f"扫描页超过 {MAX_OCR_PAGES} 页 OCR 上限，请拆分 PDF")
                text = _ocr_pdf_page(path, number)
            total_chars += len(text)
            if total_chars > MAX_PDF_TEXT_CHARS:
                raise ParseError("解析文本超过当前上限")
            pages.append(
                {"number": number, "blocks": [{"type": "paragraph", "text": text}] if text else []}
            )
            if text:
                markdown_parts.append(f"## 第 {number} 页\n\n{text}")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("PDF 文本提取失败") from exc
    if not markdown_parts:
        raise ParseError("PDF OCR 未识别到可用文字，请检查扫描质量")
    return ParseOutput(
        "pypdf+tesseract" if ocr_pages else "pypdf",
        pypdf.__version__,
        "\n\n".join(markdown_parts),
        {
            "parser_config": {
                "max_pages": MAX_PDF_PAGES,
                "max_text_chars": MAX_PDF_TEXT_CHARS,
                "ocr": ocr_pages > 0,
                "ocr_pages": ocr_pages,
            },
            "pages": pages,
        },
    )
