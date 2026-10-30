"""Read KakaoTalk's exposed accessibility surface without changing the app."""

import json
import shutil
import subprocess
import sys
from importlib.resources import files


def snapshot(window_title="", max_nodes=2000, status_only=False):
    if sys.platform != "darwin":
        return {"status": "unsupported_platform", "readOnly": True}
    if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or not 1 <= max_nodes <= 5000:
        raise ValueError("max_nodes must be between 1 and 5000")
    if len(window_title) > 300:
        raise ValueError("window_title is too long")
    swift = shutil.which("swift")
    if not swift:
        return {"status": "swift_unavailable", "readOnly": True,
                "detail": "The optional app reader needs Apple's command-line developer tools"}
    source = files("kakaotalk_native").joinpath("ReadKakao.swift")
    try:
        result = subprocess.run([swift, str(source), "status" if status_only else "snapshot",
                                 window_title, str(max_nodes)], capture_output=True, text=True, timeout=12)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "readOnly": True, "source": "macos_accessibility"}
    if result.returncode:
        return {"status": "bridge_error", "readOnly": True, "source": "macos_accessibility"}
    try:
        output = json.loads(result.stdout)
    except ValueError:
        return {"status": "bridge_error", "readOnly": True, "source": "macos_accessibility"}
    if not status_only:
        for window in output.get("windows", []):
            rows, other, drafts = [], [], []

            def texts(node):
                found = []
                if node.get("editable"):
                    if node.get("hasDraft"):
                        drafts.append(True)
                    return found
                if node.get("role") in ("AXStaticText", "AXTextArea", "AXTextField", "AXRow"):
                    for key in ("value", "title"):
                        value = node.get(key)
                        if value and value not in found:
                            found.append(value)
                for child in node.get("children", []):
                    for value in texts(child):
                        if value not in found:
                            found.append(value)
                return found

            def walk(node):
                if node.get("role") == "AXRow":
                    values = texts(node)
                    if values:
                        rows.append({"texts": values})
                else:
                    if node.get("editable") and node.get("hasDraft"):
                        drafts.append(True)
                    if node.get("role") in ("AXStaticText", "AXTextArea") and not node.get("editable"):
                        value = node.get("value") or node.get("title")
                        if value and value not in other:
                            other.append(value)
                    for child in node.get("children", []):
                        walk(child)

            walk(window.pop("tree", {}))
            window.update(rows=rows, otherTexts=other, draftPresent=bool(drafts))
        output["coverage"] = "Currently exposed KakaoTalk windows only; virtualized history and image contents may be absent. Draft text is omitted."
    return output
