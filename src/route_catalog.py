import argparse
import hashlib
import json
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import networkx as nx


DEFAULT_TRACER_ROOT = Path(r"C:\work\python\home\struts-tracer\struts-tracer-main")
DEFAULT_RUNTIME_ENTRY_HINTS = (
    "PatlicsMenu.jsp",
    "/docroot/PatlicsMenu.jsp",
    "PatlicsTopMain.jsp",
    "/docroot/PatlicsTopMain.jsp",
)


@dataclass
class RouteCatalogConfig:
    tracer_root: Path = DEFAULT_TRACER_ROOT
    cache_path: Optional[Path] = None
    project_dir: Optional[Path] = None
    max_depth: int = 12
    limit_per_target: int = 20
    max_sources: int = 20
    limit_per_source: int = 1
    target_timeout_seconds: float = 8.0


def _normal_page_name(value: str) -> str:
    value = str(value or "").replace("\\", "/").strip()
    return Path(value).name.lower()


def _cache_path_for_project(tracer_root: Path, project_dir: Path) -> Path:
    digest = hashlib.md5(str(project_dir.resolve()).encode("utf-8")).hexdigest()
    return tracer_root / ".tracer_cache" / f"graph_{digest}.pkl"


def resolve_cache_path(config: RouteCatalogConfig) -> Path:
    if config.cache_path:
        return config.cache_path
    if config.project_dir:
        return _cache_path_for_project(config.tracer_root, config.project_dir)

    cache_dir = config.tracer_root / ".tracer_cache"
    candidates = sorted(cache_dir.glob("graph_*.pkl"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No struts-tracer cache found under {cache_dir}")
    return candidates[0]


def load_tracer_graph(config: RouteCatalogConfig) -> Any:
    tracer_root = config.tracer_root.resolve()
    if str(tracer_root) not in sys.path:
        sys.path.insert(0, str(tracer_root))

    cache_path = resolve_cache_path(config)
    with cache_path.open("rb") as handle:
        cached = pickle.load(handle)
    return cached["graph"]


def _node_labels(trace_path: Any) -> List[Dict[str, str]]:
    labels = []
    for node_type, name in trace_path.labels():
        labels.append({"type": str(node_type), "name": str(name)})
    return labels


def _path_labels(graph: Any, nodes: Sequence[str]) -> List[Dict[str, str]]:
    return [{"type": str(graph.node_type_label(node)), "name": str(node)} for node in nodes]


def _route_id(target: str, labels: Sequence[Dict[str, str]], index: int) -> str:
    raw = json.dumps({"target": target, "labels": labels, "index": index}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_graph_node(graph: Any, value: str, *, is_file: bool = True) -> Optional[str]:
    if graph.has_node(value):
        return value
    resolved = graph._resolve_node(value, is_file=is_file)
    if graph.has_node(resolved):
        return resolved
    matches = graph.fuzzy_find(value, limit=1)
    return matches[0] if matches else None


def _source_nodes(graph: Any, entries: Optional[Sequence[str]]) -> List[str]:
    def add_unique(target: List[str], value: Optional[str]) -> None:
        if value and value not in target:
            target.append(value)

    if entries:
        sources = []
        for entry in entries:
            resolved = _resolve_graph_node(graph, entry, is_file=True)
            add_unique(sources, resolved)
        return sources

    sources: List[str] = []
    for entry in DEFAULT_RUNTIME_ENTRY_HINTS:
        add_unique(sources, _resolve_graph_node(graph, entry, is_file=True))

    root_sources = [
        node
        for node in graph.g.nodes
        if graph.g.in_degree(node) == 0 and str(node).lower().endswith(".jsp")
    ]
    for node in sorted(root_sources, key=lambda item: (graph.g.out_degree(item), len(str(item)), str(item))):
        add_unique(sources, node)
    return sources


def _iter_short_candidate_paths(
    graph: Any,
    source: str,
    target: str,
    *,
    max_depth: int,
    limit: int,
) -> Iterable[List[str]]:
    if limit <= 1:
        try:
            path = nx.shortest_path(graph.g, source=source, target=target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return
        if len(path) <= max_depth:
            yield path
        return

    yielded = 0
    try:
        path_iter = nx.shortest_simple_paths(graph.g, source=source, target=target, weight="weight")
        for path in path_iter:
            if len(path) <= max_depth:
                yielded += 1
                yield path
            if yielded >= limit:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return


def build_candidate_routes(
    targets: Iterable[str],
    *,
    entries: Optional[Sequence[str]] = None,
    config: Optional[RouteCatalogConfig] = None,
) -> Dict[str, Any]:
    """
    Convert struts-tracer reverse paths into a JSON-friendly route catalog.

    This is still static information. Runtime automation must verify each route
    by clicking from a real logged-in entry state before using it in regression.
    """
    config = config or RouteCatalogConfig()
    graph = load_tracer_graph(config)

    routes: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for target in targets:
        target_node = _resolve_graph_node(graph, target, is_file=True)
        if not target_node:
            warnings.append({"target": target, "warnings": ["target node was not found in struts-tracer graph"]})
            continue

        found_for_target = 0
        started_at = time.monotonic()
        timed_out = False
        for source in _source_nodes(graph, entries)[: config.max_sources]:
            if time.monotonic() - started_at > config.target_timeout_seconds:
                timed_out = True
                break
            if source == target_node:
                continue
            for path in _iter_short_candidate_paths(
                graph,
                source,
                target_node,
                max_depth=config.max_depth,
                limit=config.limit_per_source,
            ):
                found_for_target += 1
                labels = _path_labels(graph, path)
                route_entries = [item["name"] for item in labels if item["type"] == "JSP"]
                routes.append(
                    {
                        "route_id": _route_id(target, labels, found_for_target),
                        "target_page": target,
                        "target_page_name": _normal_page_name(target),
                        "entry_hint": route_entries[0] if route_entries else "",
                        "length": len(labels),
                        "nodes": labels,
                        "source": "struts-tracer",
                        "status": "candidate",
                    }
                )
                if found_for_target >= config.limit_per_target:
                    break
            if found_for_target >= config.limit_per_target:
                break

        if timed_out:
            warnings.append(
                {
                    "target": target,
                    "warnings": [
                        f"route search timed out after {config.target_timeout_seconds:.1f}s; kept {found_for_target} shortest candidate route(s)"
                    ],
                }
            )

        if found_for_target == 0:
            warnings.append({"target": target, "warnings": ["no candidate route found"]})

    routes = sorted(routes, key=lambda item: (item["target_page_name"], item["length"], item["route_id"]))
    return {
        "schema": "moonlight.route_candidates.v1",
        "tracer_root": str(config.tracer_root),
        "cache_path": str(resolve_cache_path(config)),
        "entries": list(entries or []),
        "targets": list(targets),
        "routes": routes,
        "warnings": warnings,
    }


def write_route_catalog(catalog: Dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _targets_from_mapping(mapping_path: Path) -> List[str]:
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    return [
        str(item.get("page_id") or "")
        for item in mapping.get("page_mappings", [])
        if item.get("page_id")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export struts-tracer candidate routes for Moonlight runtime verification.")
    parser.add_argument("--mapping-path", type=Path, default=Path("generated/valid/page_mapping.json"))
    parser.add_argument("--tracer-root", type=Path, default=DEFAULT_TRACER_ROOT)
    parser.add_argument("--cache-path", type=Path, default=None)
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--entry", action="append", default=None, help="Optional entry JSP hint. Can be supplied multiple times.")
    parser.add_argument("--target", action="append", default=None, help="Optional target JSP/page. Defaults to all page_mapping pages.")
    parser.add_argument("--output", type=Path, default=Path("generated/valid/route_candidates.json"))
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--limit-per-target", type=int, default=20)
    parser.add_argument("--max-sources", type=int, default=20)
    parser.add_argument("--limit-per-source", type=int, default=1)
    parser.add_argument("--target-timeout-seconds", type=float, default=8.0)
    args = parser.parse_args()

    targets = args.target or _targets_from_mapping(args.mapping_path)
    config = RouteCatalogConfig(
        tracer_root=args.tracer_root,
        cache_path=args.cache_path,
        project_dir=args.project_dir,
        max_depth=args.max_depth,
        limit_per_target=args.limit_per_target,
        max_sources=args.max_sources,
        limit_per_source=args.limit_per_source,
        target_timeout_seconds=args.target_timeout_seconds,
    )
    catalog = build_candidate_routes(targets, entries=args.entry, config=config)
    write_route_catalog(catalog, args.output)
    print(f"Wrote {len(catalog['routes'])} candidate route(s): {args.output}")


if __name__ == "__main__":
    main()
