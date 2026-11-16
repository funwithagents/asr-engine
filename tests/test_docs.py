"""Documentation drift guards: relative links resolve, the module tables match
the registry, and each module page lists its spec's fields (specs/project.md,
"Documentation checks")."""

import re
from pathlib import Path

import pytest

from asr_engine.modules import REGISTRY, LazyModule

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The Markdown a reader can reach from the repository: the root files and the
# tracked documentation trees. Left out: `plans/` (records of what was built,
# not rewritten when a file they link to later moves) and the untracked note
# folders (`analysis/`, `reports/`, `research_notes/`).
_ROOT_FILES = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
_DOC_DIRS = ["docs", "specs", "examples", "tests", "tests-e2e"]


def _markdown_files() -> list[Path]:
    files = [_REPO_ROOT / name for name in _ROOT_FILES]
    for directory in _DOC_DIRS:
        files.extend(sorted((_REPO_ROOT / directory).rglob("*.md")))
    return files


# --- Relative links -----------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _links(text: str) -> list[str]:
    """The link targets of `text`, with code blocks and code spans removed
    first: a link inside code is an example, not a link. (A link whose label is
    a code span keeps its target and still counts.)"""
    return _LINK.findall(_CODE_SPAN.sub("", _FENCE.sub("", text)))


def _slug(heading: str) -> str:
    """GitHub's heading anchor: markup dropped, lower-cased, punctuation
    removed, spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for heading in _HEADING.findall(_FENCE.sub("", path.read_text(encoding="utf-8"))):
        slug = _slug(heading)
        candidate, n = slug, 0
        while candidate in anchors:  # GitHub numbers repeated headings
            n += 1
            candidate = f"{slug}-{n}"
        anchors.add(candidate)
    return anchors


def _broken_links(path: Path) -> list[str]:
    broken: list[str] = []
    for target in _links(path.read_text(encoding="utf-8")):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            broken.append(target)
        elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
            broken.append(f"{target} (no such heading)")
    return broken


@pytest.mark.parametrize(
    "path", _markdown_files(), ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_relative_links_resolve(path: Path):
    assert not _broken_links(path), f"broken links in {path.relative_to(_REPO_ROOT)}"


# --- Paths named in code ------------------------------------------------------

# The documentation names example configs as code (`examples/config/x.json`,
# `cp examples/config/x.json config.json`) at least as often as it links them,
# and a rename breaks those silently: the copy command fails for the reader.
_EXAMPLE_CONFIG = re.compile(r"examples/config/[\w.-]+\.json")


@pytest.mark.parametrize(
    "path", _markdown_files(), ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_example_configs_named_in_the_text_exist(path: Path):
    named = set(_EXAMPLE_CONFIG.findall(path.read_text(encoding="utf-8")))
    missing = sorted(name for name in named if not (_REPO_ROOT / name).exists())
    assert not missing, f"{path.relative_to(_REPO_ROOT)} names missing configs"


# --- The module tables --------------------------------------------------------

# The full table in the module index, and the README's short copy. Both name
# each module as `type`, linked to its page.
_MODULE_INDEX = _REPO_ROOT / "docs" / "modules" / "index.md"
_MODULE_TABLES = [_MODULE_INDEX, _REPO_ROOT / "README.md"]
_MODULE_HEADER = re.compile(r"^\| Module \| Kind \| Extra \|")
_NAME = re.compile(r"`([^`]+)`")


def _table(lines: list[str], start: int) -> list[dict[str, str]]:
    """The table whose header row is `lines[start]`, as one dict per row."""
    columns = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        # A `|` inside a cell is written `\|` (e.g. the type `float \| None`).
        cells = [
            cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))
        ]
        rows.append(dict(zip(columns, cells, strict=True)))
    return rows


def _tables(path: Path, header: re.Pattern[str]) -> list[list[dict[str, str]]]:
    """Every table of `path` whose header row matches."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_table(lines, i) for i, line in enumerate(lines) if header.match(line)]


def _module_rows(path: Path) -> dict[str, dict[str, str]]:
    """The module table of `path` as {type: row}."""
    (table,) = _tables(path, _MODULE_HEADER)
    rows: dict[str, dict[str, str]] = {}
    for row in table:
        match = _NAME.search(row["Module"])
        assert match, f"no `type` in the Module cell {row['Module']!r} of {path.name}"
        rows[match.group(1)] = row
    return rows


@pytest.mark.parametrize(
    "path", _MODULE_TABLES, ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_module_table_lists_exactly_the_registered_modules(path: Path):
    assert set(_module_rows(path)) == set(REGISTRY)


@pytest.mark.parametrize(
    "path", _MODULE_TABLES, ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_module_table_names_the_extra_the_registry_asks_for(path: Path):
    # The registry names an extra only where it raises the install hint itself;
    # kyutai and mlx-audio raise their own, and fake needs none.
    rows = _module_rows(path)
    for name, entry in REGISTRY.items():
        if isinstance(entry, LazyModule) and entry.extra is not None:
            assert _NAME.findall(rows[name]["Extra"]) == [entry.extra], name


@pytest.mark.parametrize(
    "path", _MODULE_TABLES, ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_module_table_links_each_module_to_an_existing_page(path: Path):
    for name, row in _module_rows(path).items():
        match = re.search(r"\]\(([^)#]+)", row["Module"])
        assert match, f"the {name!r} row of {path.name} does not link to a page"
        assert (path.parent / match.group(1)).exists(), name


# --- Module pages against their specs -----------------------------------------

# A module page's option tables are the user's copy of its spec's: the same
# fields, in user words. Pages mirror `specs/modules/` file for file, so a
# backend with several modules (deepgram) has one page, as it has one spec.
_FIELD_HEADER = re.compile(r"^\| Field \|")
_MODULE_SPECS = sorted((_REPO_ROOT / "specs" / "modules").glob("*.md"))


def _fields(path: Path) -> set[str]:
    return {
        name
        for table in _tables(path, _FIELD_HEADER)
        for row in table
        for name in _NAME.findall(row["Field"])
    }


@pytest.mark.parametrize("spec", _MODULE_SPECS, ids=lambda p: p.stem)
def test_module_page_lists_the_fields_of_its_spec(spec: Path):
    page = _REPO_ROOT / "docs" / "modules" / spec.name
    assert page.exists(), f"no page for {spec.stem}: add docs/modules/{spec.name}"
    assert _fields(spec), f"{spec.name} has no `| Field |` table to compare with"
    assert _fields(page) == _fields(spec)
