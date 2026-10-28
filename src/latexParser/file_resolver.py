import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class LatexFileResolver:
    """
    SCRUM-19: Xác định nhánh biên dịch chính (main compilation path) từ thư mục source LaTeX
    của một version và ghép nối đệ quy các file theo lệnh \\input và \\include.
    """

    # Regex xóa comment LaTeX: khớp từ dấu '%' (không có dấu '\' đứng trước) đến hết dòng.
    # Sử dụng negative lookbehind (?<!\\) kết hợp xử lý trường hợp '\\' đứng trước '%'.
    COMMENT_PATTERN = re.compile(r"(?<!\\)(?:\\\\)*%.*")

    # Regex nhận diện \documentclass[...]{...}
    DOCUMENTCLASS_PATTERN = re.compile(
        r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{[^}]+\}"
    )

    # Regex nhận diện \input{...}, \include{...}, \subfile{...} và cả cú pháp TeX cổ \input filename
    INCLUDE_PATTERN = re.compile(
        r"\\(?:input|include|subfile)\s*(?:\{\s*([^}]+?)\s*\}|([^\s%{}]+))"
    )

    def __init__(self, strip_comments: bool = True):
        """
        Args:
            strip_comments: Nếu True, loại bỏ hoàn toàn các phần comment (%) trong nội dung trả về.
        """
        self.strip_comments = strip_comments

    @classmethod
    def remove_comments(cls, text: str) -> str:
        """
        Xóa các comment LaTeX khỏi văn bản nhưng giữ nguyên ký tự '\\%' hợp lệ.
        """
        cleaned_lines = []
        for line in text.splitlines():
            # Tìm vị trí dấu % không bị escape (số lượng dấu \ ngay trước nó là số chẵn)
            match = re.search(r"(?<!\\)(?:\\\\)*%", line)
            if match:
                # Vị trí chính xác của dấu '%' là ký tự cuối cùng của chuỗi match trước phần comment
                percent_idx = match.end() - 1
                # Nếu cả dòng chỉ là comment (chỉ có khoảng trắng trước dấu %), bỏ qua dòng đó
                prefix = line[:percent_idx]
                if prefix.strip() == "" and line.lstrip().startswith("%"):
                    continue
                cleaned_lines.append(prefix)
            else:
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines)

    @staticmethod
    def read_tex_file(file_path: Path) -> str:
        """
        Đọc file .tex an toàn với nhiều bảng mã (tránh lỗi UnicodeDecodeError trên các bài arXiv cũ).
        """
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return file_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return file_path.read_text(encoding="utf-8", errors="ignore")

    def find_root_tex_file(self, version_dir: Path) -> Optional[Path]:
        """
        Tìm file .tex gốc chứa khai báo \\documentclass (không nằm trong comment).
        Nếu có nhiều file chứa \\documentclass (ví dụ có file rebuttal/supplement),
        ưu tiên file có chứa \\begin{document} và có tên chuẩn (main.tex, paper.tex, ms.tex)
        hoặc kích thước lớn nhất.
        """
        if not version_dir.exists() or not version_dir.is_dir():
            return None

        tex_files = list(version_dir.rglob("*.tex"))
        if not tex_files:
            return None

        candidates: List[Tuple[int, int, Path]] = []
        priority_names = {"main.tex", "paper.tex", "ms.tex", "article.tex", "root.tex"}

        for tex_file in tex_files:
            raw_content = self.read_tex_file(tex_file)
            uncommented = self.remove_comments(raw_content)

            if self.DOCUMENTCLASS_PATTERN.search(uncommented):
                has_begin_doc = 1 if r"\begin{document}" in uncommented else 0
                is_priority_name = 1 if tex_file.name.lower() in priority_names else 0
                file_size = len(uncommented)
                # Sắp xếp ưu tiên: có \begin{document} -> nằm ở thư mục gốc -> tên ưu tiên -> độ dài
                is_top_level = 1 if tex_file.parent == version_dir else 0
                score = (
                    has_begin_doc * 1000
                    + is_top_level * 100
                    + is_priority_name * 10
                )
                candidates.append((score, file_size, tex_file))

        if not candidates:
            # Fallback hiếm gặp: nếu tác giả dùng file định nghĩa macro riêng bọc \documentclass
            # thì chọn file có \begin{document}
            for tex_file in tex_files:
                uncommented = self.remove_comments(self.read_tex_file(tex_file))
                if r"\begin{document}" in uncommented:
                    candidates.append((1, len(uncommented), tex_file))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    @staticmethod
    def _resolve_target_path(
        target_ref: str, current_file_dir: Path, root_dir: Path
    ) -> Optional[Path]:
        """
        Ánh xạ đường dẫn trong \\input{target_ref} thành đường dẫn file thực tế.
        LaTeX mặc định tìm tương đối so với thư mục chứa file gốc (root_dir),
        hoặc tương đối so với file hiện tại (current_file_dir), và tự động thêm '.tex' nếu thiếu.
        """
        target_ref = target_ref.strip().strip('"').strip("'")
        if not target_ref:
            return None

        # Thử cả 2 gốc: thư mục của file root (chuẩn của trình biên dịch LaTeX) và thư mục của file hiện tại
        base_dirs = [root_dir, current_file_dir]
        # Thử tên nguyên bản và tên có nối thêm đuôi .tex
        candidate_names = [target_ref]
        if not target_ref.endswith(".tex"):
            candidate_names.append(f"{target_ref}.tex")

        for base in base_dirs:
            for name in candidate_names:
                candidate_path = (base / name).resolve()
                if candidate_path.is_file() and candidate_path.suffix.lower() in {
                    ".tex",
                    ".sty",
                    ".cls",
                    "",
                }:
                    # Chỉ ghép nội dung các file .tex (không nhúng nội dung file .bbl hoặc .sty vào cây văn bản)
                    if candidate_path.suffix.lower() == ".tex":
                        return candidate_path
        return None

    def resolve_version(self, version_dir: Path) -> Dict[str, object]:
        """
        Xử lý một thư mục version: tìm root file, đệ quy ghép toàn bộ \\input / \\include,
        và trả về văn bản LaTeX hợp nhất cùng thống kê các file đã dùng / bị bỏ qua.

        Returns:
            Dict chứa:
                - is_empty (bool): True nếu version không có TeX source hợp lệ (ví dụ PDF-only).
                - root_file (Optional[str]): Đường dẫn tương đối của file gốc.
                - compiled_tex (str): Toàn bộ nội dung LaTeX đã ghép nối theo nhánh biên dịch.
                - included_files (List[str]): Danh sách các file .tex nằm trên nhánh biên dịch.
                - ignored_files (List[str]): Danh sách các file .tex dư thừa bị loại bỏ.
        """
        version_dir = Path(version_dir).resolve()
        all_tex_files = {p.resolve() for p in version_dir.rglob("*.tex")}

        root_file = self.find_root_tex_file(version_dir)
        if root_file is None:
            return {
                "is_empty": True,
                "root_file": None,
                "compiled_tex": "",
                "included_files": [],
                "ignored_files": [
                    str(p.relative_to(version_dir)) for p in sorted(all_tex_files)
                ],
            }

        root_file = root_file.resolve()
        root_dir = root_file.parent
        visited_stack: Set[Path] = set()
        included_files_ordered: List[Path] = []

        def _expand_file(file_path: Path) -> str:
            if file_path in visited_stack:
                # Chặn đệ quy vòng (circular \input)
                return ""

            visited_stack.add(file_path)
            if file_path not in included_files_ordered:
                included_files_ordered.append(file_path)

            raw_text = self.read_tex_file(file_path)
            # BẮT BUỘC: Xóa comment trước khi tìm \input / \include để không ghép nhầm lệnh bị comment
            working_text = self.remove_comments(raw_text)

            def _replace_include(match: re.Match) -> str:
                target = match.group(1) or match.group(2)
                resolved_child = self._resolve_target_path(
                    target, file_path.parent, root_dir
                )
                if resolved_child is not None:
                    child_content = _expand_file(resolved_child)
                    return f"\n{child_content}\n"
                # Nếu không tìm thấy file .tex tương ứng (ví dụ \input{table.pstex_t} hoặc file thiếu),
                # xóa lệnh \input đó để không làm bẩn văn bản
                return ""

            expanded_text = self.INCLUDE_PATTERN.sub(_replace_include, working_text)
            visited_stack.remove(file_path)
            return expanded_text

        compiled_tex = _expand_file(root_file)
        included_set = set(included_files_ordered)
        ignored_files = sorted(all_tex_files - included_set)

        return {
            "is_empty": False,
            "root_file": str(root_file.relative_to(version_dir)),
            "compiled_tex": compiled_tex,
            "included_files": [
                str(p.relative_to(version_dir)) for p in included_files_ordered
            ],
            "ignored_files": [
                str(p.relative_to(version_dir)) for p in ignored_files
            ],
        }