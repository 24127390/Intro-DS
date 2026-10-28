import re
from typing import Dict, List, Optional, Tuple


class PublicationHierarchyBuilder:

    HEADING_RANKS = {
        "part": 1,
        "chapter": 2,
        "section": 3,
        "subsection": 4,
        "subsubsection": 5,
        "paragraph": 6,
        "subparagraph": 7,
    }


    REFERENCE_TITLES = {
        "references",
        "bibliography",
        "works cited",
        "literature cited",
    }


    CLEANUP_COMMANDS_WITH_ARG = re.compile(
        r"\\(?:vspace|hspace|vskip|hskip|phantom|hphantom|vphantom)\*?\s*\{[^}]*\}"
    )
    CLEANUP_SIMPLE_COMMANDS = re.compile(
        r"\\(?:centering|raggedright|raggedleft|noindent|indent|newpage|clearpage|"
        r"cleardoublepage|pagebreak|linebreak|allowbreak|sloppy|fussy|toprule|"
        r"midrule|bottomrule|hline|cline\{[^}]*\}|small|footnotesize|scriptsize|"
        r"tiny|large|Large|LARGE|huge|Huge|normalfont|itshape|bfseries|scshape)\b"
    )
    PLACEMENT_OPTIONS = re.compile(r"\[\s*[!htbpH]+\s*\]")


    BLOCK_MATH_ENV_NAMES = (
        r"equation\*?|align\*?|gather\*?|multline\*?|flalign\*?|eqnarray\*?|displaymath"
    )


    FIGURE_ENV_NAMES = r"figure\*?|table\*?|wraptable|wrapfigure"


    LIST_ENV_NAMES = r"itemize|enumerate|description"

    ABBREVIATIONS = (
        r"\b(?:Fig|Figs|Tab|Tabs|Eq|Eqs|Sec|Secs|Ch|Chap|App|Alg|Def|Thm|Lem|Prop|Cor|"
        r"Ref|Refs|Prof|Dr|Mr|Mrs|Ms|Jr|Sr|vs|etc|al|cf|No|Vol|pp|ca)\."
    )

    def __init__(self, arxiv_id: str):
        """
        Args:
            arxiv_id: Mã bài báo (ví dụ '2310.12345' hoặc '2310-12345').
        """
        self.raw_arxiv_id = arxiv_id

        self.pub_key = arxiv_id.replace(".", "-").replace("/", "-")

        self._dedup_lookup: Dict[Tuple[str, str], str] = {}
        self.elements_rows: List[Dict[str, str]] = []
        self.hierarchy_rows: List[Dict[str, object]] = []

        self._counters = {
            "heading": 0,
            "itemize": 0,
            "item": 0,
            "sentence": 0,
            "block_formula": 0,
            "figure": 0,
        }
        self._prefix_map = {
            "heading": "h",
            "itemize": "l",
            "item": "i",
            "sentence": "s",
            "block_formula": "eq",
            "figure": "fig",
        }

    def _get_or_create_element(self, kind: str, content: str) -> str:

        key = (kind, content)
        if key in self._dedup_lookup:
            return self._dedup_lookup[key]

        self._counters[kind] = self._counters.get(kind, 0) + 1
        prefix = self._prefix_map.get(kind, "el")
        element_id = f"{self.pub_key}#{prefix}{self._counters[kind]}"

        self._dedup_lookup[key] = element_id
        self.elements_rows.append(
            {
                "element_id": element_id,
                "arxiv_id": self.raw_arxiv_id,
                "kind": kind,
                "content": content,
            }
        )
        return element_id

    def _add_edge(self, version: int, element_id: str, parent_id: str) -> None:
        self.hierarchy_rows.append(
            {
                "arxiv_id": self.raw_arxiv_id,
                "version": int(version),
                "element_id": element_id,
                "parent_id": parent_id,
            }
        )

    @classmethod
    def normalize_inline_math(cls, text: str) -> str:

        text = re.sub(
            r"\\\((.*?)\\\)",
            lambda m: f"${m.group(1).strip()}$",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r"\\begin\{math\}(.*?)\\end\{math\}",
            lambda m: f"${m.group(1).strip()}$",
            text,
            flags=re.DOTALL,
        )
        return text

    @classmethod
    def normalize_block_math_content(cls, raw_block: str) -> str:

        inner = raw_block.strip()

        if inner.startswith("$$") and inner.endswith("$$"):
            inner = inner[2:-2]
        elif inner.startswith(r"\[") and inner.endswith(r"\]"):
            inner = inner[2:-2]
        else:
            env_match = re.match(
                rf"^\\begin\{{({cls.BLOCK_MATH_ENV_NAMES})\}}(.*)\\end\{{\1\}}$",
                inner,
                flags=re.DOTALL,
            )
            if env_match:
                inner = env_match.group(2)

        # Dọn các lệnh trình bày không cần thiết bên trong công thức
        inner = cls.PLACEMENT_OPTIONS.sub("", inner)
        inner = re.sub(r"\s+", " ", inner).strip()
        return f"\\begin{{equation}} {inner} \\end{{equation}}"

    @classmethod
    def clean_latex_text(cls, text: str) -> str:
        """
        Làm sạch các lệnh định dạng không mang ngữ nghĩa (\\centering, [htpb], \\midrule, ...)
        và chuẩn hóa khoảng trắng.
        """
        text = cls.normalize_inline_math(text)
        text = cls.PLACEMENT_OPTIONS.sub("", text)
        text = cls.CLEANUP_COMMANDS_WITH_ARG.sub(" ", text)
        text = cls.CLEANUP_SIMPLE_COMMANDS.sub(" ", text)

        # Bóc các lệnh định dạng chữ đơn giản như \textbf{...}, \textit{...}, \emph{...} giữ lại nội dung
        for _ in range(2):
            text = re.sub(
                r"\\(?:textbf|textit|textrm|texttt|textsf|textsc|emph|underline)\s*\{([^{}]*)\}",
                r"\1",
                text,
            )

        # Chuẩn hóa nhiều khoảng trắng liên tiếp thành 1 khoảng trắng
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def split_into_sentences(cls, paragraph_text: str) -> List[str]:

        cleaned = cls.clean_latex_text(paragraph_text)
        if not cleaned:
            return []

        masks: List[str] = []

        def _mask(match: re.Match) -> str:
            idx = len(masks)
            masks.append(match.group(0))
            return f"__LATEX_MASK_{idx}__"

        # 1. Che inline math $...$
        masked_text = re.sub(r"\$(?:\\\$|[^$])+\$", _mask, cleaned)
        # 2. Che các lệnh LaTeX có tham số như \cite{...}, \ref{...}, \url{...}
        masked_text = re.sub(
            r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})+", _mask, masked_text
        )
        # 3. Che URL
        masked_text = re.sub(r"https?://[^\s{}]+", _mask, masked_text)
        # 4. Che cụm viết tắt đa từ: i.e., e.g., w.r.t., a.k.a., et al.
        masked_text = re.sub(
            r"\b(?:i\.e\.|e\.g\.|w\.r\.t\.|a\.k\.a\.|et\s+al\.)",
            _mask,
            masked_text,
            flags=re.IGNORECASE,
        )
        # 5. Che các từ viết tắt học thuật phổ biến (Fig., Eq., Sec., ...)
        masked_text = re.sub(cls.ABBREVIATIONS, _mask, masked_text)
        # 6. Che số thập phân (ví dụ 3.14, 0.05)
        masked_text = re.sub(r"\b\d+\.\d+\b", _mask, masked_text)

        # Tách câu tại dấu '.', '!', '?' theo sau là khoảng trắng và chữ cái viết hoa / mở ngoặc / mask
        raw_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z($\"'_\\])", masked_text)

        sentences: List[str] = []
        for part in raw_parts:
            # Khôi phục các mask
            restored = part
            # Lặp ngược để khôi phục cả trường hợp mask lồng nhau
            for idx in range(len(masks) - 1, -1, -1):
                token = f"__LATEX_MASK_{idx}__"
                if token in restored:
                    restored = restored.replace(token, masks[idx])
            restored = re.sub(r"\s+", " ", restored).strip()
            if restored:
                sentences.append(restored)

        return sentences

    @classmethod
    def _extract_body_and_strip_references(cls, compiled_tex: str) -> str:

        body_match = re.search(
            r"\\begin\{document\}(.*?)\\end\{document\}",
            compiled_tex,
            flags=re.DOTALL,
        )
        body = body_match.group(1) if body_match else compiled_tex

        # Xóa môi trường thebibliography và filecontents
        body = re.sub(
            r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}",
            "",
            body,
            flags=re.DOTALL,
        )
        body = re.sub(
            r"\\bibliography(?:style)?\s*\{[^}]*\}",
            "",
            body,
        )
        return body

    def _parse_content_block(
        self, text_block: str, parent_id: str, version: int
    ) -> None:

        token_pattern = re.compile(
            rf"(?P<list>\\begin\{{(?P<list_env>{self.LIST_ENV_NAMES})\}}.*?\\end\{{(?P=list_env)\}})"
            rf"|(?P<figure>\\begin\{{(?P<fig_env>{self.FIGURE_ENV_NAMES})\}}.*?\\end\{{(?P=fig_env)\}})"
            rf"|(?P<math>\\begin\{{(?P<math_env>{self.BLOCK_MATH_ENV_NAMES})\}}.*?\\end\{{(?P=math_env)\}}"
            rf"|\$\$.*?\$\$|\\\[.*?\\\])",
            flags=re.DOTALL,
        )

        pos = 0
        for match in token_pattern.finditer(text_block):
            # 1. Xử lý phần văn bản prose nằm trước khối đặc biệt
            prose_before = text_block[pos : match.start()]
            for sent in self.split_into_sentences(prose_before):
                sent_id = self._get_or_create_element("sentence", sent)
                self._add_edge(version, sent_id, parent_id)

            # 2. Xử lý khối đặc biệt tìm được
            if match.group("list"):
                list_raw = match.group("list")
                list_clean = self.clean_latex_text(list_raw)
                list_id = self._get_or_create_element("itemize", list_clean)
                self._add_edge(version, list_id, parent_id)

                # Bóc phần ruột của môi trường danh sách và tách theo \item
                inner_list = re.sub(
                    rf"^\\begin\{{({self.LIST_ENV_NAMES})\}}(?:\[[^\]]*\])?(.*)\\end\{{\1\}}$",
                    r"\2",
                    list_raw.strip(),
                    flags=re.DOTALL,
                )
                items = re.split(r"\\item(?:\s*\[[^\]]*\])?", inner_list)
                for item_chunk in items[1:]:  # Bỏ phần rỗng trước \item đầu tiên
                    item_clean = self.clean_latex_text(item_chunk)
                    if not item_clean:
                        continue
                    item_id = self._get_or_create_element("item", item_clean)
                    self._add_edge(version, item_id, list_id)
                    # Bên trong một \item có thể chứa các câu văn hoặc công thức toán
                    self._parse_content_block(item_chunk, item_id, version)

            elif match.group("figure"):
                fig_clean = self.clean_latex_text(match.group("figure"))
                if fig_clean:
                    fig_id = self._get_or_create_element("figure", fig_clean)
                    self._add_edge(version, fig_id, parent_id)

            elif match.group("math"):
                norm_math = self.normalize_block_math_content(match.group("math"))
                math_id = self._get_or_create_element("block_formula", norm_math)
                self._add_edge(version, math_id, parent_id)

            pos = match.end()


        remaining_prose = text_block[pos:]
        for sent in self.split_into_sentences(remaining_prose):
            sent_id = self._get_or_create_element("sentence", sent)
            self._add_edge(version, sent_id, parent_id)

    def parse_version(self, version: int, compiled_tex: str) -> Optional[str]:
        """
        Xây dựng cây phân cấp cho một version cụ thể của bài báo.
        Nếu compiled_tex rỗng (version chỉ có PDF/docx), giữ bản ghi rỗng (không tạo element nào).

        Returns:
            root_id của version này (ví dụ '2310-12345#root1'), hoặc None nếu version rỗng.
        """
        if not compiled_tex or not compiled_tex.strip():
            return None

        # Khởi tạo node Root của version theo đúng quy ước đặc tả: 2310-12345#root{version}
        root_id = f"{self.pub_key}#root{version}"
        root_content = f"Version {version} root"
        self.elements_rows.append(
            {
                "element_id": root_id,
                "arxiv_id": self.raw_arxiv_id,
                "kind": "document",
                "content": root_content,
            }
        )

        body = self._extract_body_and_strip_references(compiled_tex)

        # Biểu thức nhận diện các lệnh phân cấp (\section, \subsection, ..., và cả \appendix)
        heading_pattern = re.compile(
            r"\\(?P<cmd>part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
            r"\*?\s*(?:\[[^\]]*\])?\s*\{(?P<title>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
            flags=re.DOTALL,
        )


        hierarchy_stack: List[Tuple[int, str]] = [(0, root_id)]
        skip_current_section = False

        matches = list(heading_pattern.finditer(body))


        if matches:
            preamble_body = body[: matches[0].start()]
        else:
            preamble_body = body

        # Nếu có môi trường \begin{abstract}...\end{abstract}, tạo một heading Abstract riêng
        abstract_match = re.search(
            r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
            preamble_body,
            flags=re.DOTALL,
        )
        if abstract_match:
            abs_heading_id = self._get_or_create_element("heading", "Abstract")
            self._add_edge(version, abs_heading_id, root_id)
            self._parse_content_block(abstract_match.group(1), abs_heading_id, version)
            preamble_rest = (
                preamble_body[: abstract_match.start()]
                + preamble_body[abstract_match.end() :]
            )
            self._parse_content_block(preamble_rest, root_id, version)
        else:
            self._parse_content_block(preamble_body, root_id, version)

        # Duyệt qua từng Heading trong bài báo
        for idx, match in enumerate(matches):
            cmd = match.group("cmd")
            raw_title = match.group("title")
            cleaned_title = self.clean_latex_text(raw_title)
            rank = self.HEADING_RANKS[cmd]

            # Kiểm tra xem có phải mục References cần loại bỏ không
            normalized_title_check = re.sub(
                r"[^a-zA-Z\s]", "", cleaned_title
            ).strip().lower()

            if normalized_title_check in self.REFERENCE_TITLES:
                skip_current_section = True
                skip_rank = rank
                continue

            if skip_current_section:
 
                if rank <= skip_rank:
                    skip_current_section = False
                else:
                    continue

            while len(hierarchy_stack) > 1 and hierarchy_stack[-1][0] >= rank:
                hierarchy_stack.pop()

            parent_id = hierarchy_stack[-1][1]
            heading_id = self._get_or_create_element(
                "heading", cleaned_title or cmd.capitalize()
            )
            self._add_edge(version, heading_id, parent_id)
            hierarchy_stack.append((rank, heading_id))

            content_start = match.end()
            content_end = (
                matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
            )
            section_content = body[content_start:content_end]

            self._parse_content_block(section_content, heading_id, version)

        return root_id