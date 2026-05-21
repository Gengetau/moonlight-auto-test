import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Union, List

class StrutsResolver:
    """
    Parses struts-config.xml (or directories of them) to resolve view pages (JSP) 
    back to their entry actions (.do).
    """
    def __init__(self, config_paths: Optional[Union[str, Path, List[Union[str, Path]]]] = None):
        self.route_map: Dict[str, str] = {}
        if config_paths:
            self.load_configs(config_paths)

    def load_configs(self, paths: Union[str, Path, List[Union[str, Path]]]):
        if isinstance(paths, (str, Path)):
            # Support comma-separated list
            if isinstance(paths, str) and "," in paths:
                actual_paths = [p.strip() for p in paths.split(",")]
            else:
                actual_paths = [paths]
        else:
            actual_paths = paths

        for path_item in actual_paths:
            p = Path(path_item)
            if not p.exists():
                print(f"[STRUTS_RESOLVER] Path not found: {p}")
                continue
            
            if p.is_dir():
                for xml_file in p.rglob("*.xml"):
                    self.load_config_file(xml_file)
            else:
                self.load_config_file(p)

    def load_config_file(self, config_path: Path):
        try:
            content = config_path.read_text(encoding="utf-8", errors="replace")
            content = re.sub(r'<!DOCTYPE.*?>', '', content, flags=re.DOTALL)
            root = ET.fromstring(content)
            
            for action in root.findall(".//action"):
                action_path = action.get("path", "")
                if not action_path:
                    continue
                
                entry_url = f"{action_path.lstrip('/')}.do"
                
                for forward in action.findall("forward"):
                    forward_path = forward.get("path", "")
                    if forward_path.endswith(".jsp"):
                        jsp_name = Path(forward_path).name
                        # Map JSP name to the .do entry
                        self.route_map[jsp_name] = entry_url
        except Exception as e:
            print(f"[STRUTS_RESOLVER] Failed to parse {config_path}: {e}")

    def resolve_entry_url(self, jsp_path: str) -> str:
        """
        Resolves a JSP filename to its Struts entry URL.
        If no mapping exists, returns the JSP path as fallback.
        """
        jsp_name = Path(jsp_path).name
        return self.route_map.get(jsp_name, jsp_path)
