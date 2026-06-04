import re
from pathlib import Path
from typing import Any, Iterable, Set


KNOWN_PAGE_ALIAS_GROUPS = (
    {
        "jpgazettefornumbersearch",
        "jpgazettefornumbersearch.do",
        "jpgazettefornumbersearch.jsp",
        "gazettemainframe",
        "gazettemainframe.do",
        "gazettemainframe.jsp",
    },
    {
        "jpnonjavascreeningforeasysearch",
        "jpnonjavascreeningforeasysearch.do",
        "jpnonjavascreeningforeasysearch.jsp",
        "nonjavascreeningmainframe",
        "nonjavascreeningmainframe.do",
        "nonjavascreeningmainframe.jsp",
    },
    {
        "wwpersonalnamedicdispforeasysearch",
        "wwpersonalnamedicdispforeasysearch.do",
        "wwpersonalnamedicdispforeasysearch.jsp",
        "wwpersonalnamedicsearch",
        "wwpersonalnamedicsearch.do",
        "wwpersonalnamedicsearch.jsp",
        "wwpersonaidmain",
        "wwpersonaidmain.do",
        "wwpersonaidmain.jsp",
        "wwpersonaidtitledisp",
        "wwpersonaidtitledisp.do",
        "wwpersonaidtitledisp.jsp",
        "wwpersonaidsearchdisp",
        "wwpersonaidsearchdisp.do",
        "wwpersonaidsearchdisp.jsp",
        "wwpersonaidresultdisp",
        "wwpersonaidresultdisp.do",
        "wwpersonaidresultdisp.jsp",
    },
    {
        "wwprintview",
        "wwprintview.do",
        "wwprintview.jsp",
        "wwexpprint",
        "wwexpprint.do",
        "wwexpprint.jsp",
    },
)


def _leaf(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("'\"").lower()
    if not text:
        return ""
    return Path(text).name or text.rsplit("/", 1)[-1]


def _add_suffix_aliases(aliases: Set[str], name: str) -> None:
    if not name:
        return
    aliases.add(name)
    if name.endswith(".do"):
        stem = name[:-3]
        aliases.update({stem, f"{stem}.jsp"})
    elif name.endswith(".jsp"):
        stem = name[:-4]
        aliases.update({stem, f"{stem}.do"})
    elif name.endswith(".action"):
        stem = name[:-7]
        aliases.update({stem, f"{stem}.do", f"{stem}.jsp"})
    else:
        aliases.update({f"{name}.do", f"{name}.jsp"})


def page_aliases(value: Any) -> Set[str]:
    leaf = _leaf(value)
    if not leaf:
        return set()

    aliases: Set[str] = set()
    queryless = re.split(r"[?#]", leaf, maxsplit=1)[0].strip()
    _add_suffix_aliases(aliases, leaf)
    _add_suffix_aliases(aliases, queryless)

    for group in KNOWN_PAGE_ALIAS_GROUPS:
        if aliases & group:
            aliases.update(group)
    return {alias for alias in aliases if alias}


def page_matches(left: Any, right: Any) -> bool:
    return bool(page_aliases(left) & page_aliases(right))


def expand_page_aliases(values: Iterable[Any]) -> Set[str]:
    aliases: Set[str] = set()
    for value in values:
        aliases.update(page_aliases(value))
    return aliases
