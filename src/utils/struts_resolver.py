import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Union

class StrutsResolver:
    """
    Parses struts-config.xml to resolve view pages (JSP) back to their entry actions (.do).
    """
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self.route_map: Dict[str, str] = {}
        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: Union[str, Path]):
        path = Path(config_path)
        if not path.exists():
            return

        try:
            # Handle potential encoding issues in legacy XML
            content = path.read_text(encoding="utf-8", errors="replace")
            # Remove DOCTYPE to avoid remote entity resolution issues if not configured
            content = re.sub(r'<!DOCTYPE.*?>', '', content, flags=re.DOTALL)
            
            root = ET.fromstring(content)
            
            # Find all action mappings
            for action in root.findall(".//action"):
                action_path = action.get("path", "")
                if not action_path:
                    continue
                
                # Normalize action path to .do
                entry_url = f"{action_path.lstrip('/')}.do"
                
                # Check for forwards within this action
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

if __name__ == "__main__":
    # Quick test
    resolver = StrutsResolver()
    # Mock some data if needed for testing
    print("Struts Resolver initialized.")
