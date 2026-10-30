import AppKit
import ApplicationServices
import Foundation

// This bridge only copies attributes. It does not activate windows, invoke
// accessibility actions, inject keys, change controls, or read composer drafts.
let arguments = Array(CommandLine.arguments.dropFirst())
let mode = arguments.first ?? "status"
let requestedTitle = arguments.count > 1 ? arguments[1] : ""
let maxNodes = arguments.count > 2 ? min(max(Int(arguments[2]) ?? 2000, 1), 5000) : 2000
let deadline = ProcessInfo.processInfo.systemUptime + 3.0
var nodeCount = 0
var truncated = false

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    if AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success { return value }
    return nil
}

func stringValue(_ value: CFTypeRef?) -> String? {
    if let string = value as? String { return string }
    if let attributed = value as? NSAttributedString { return attributed.string }
    return nil
}

func tree(_ element: AXUIElement, _ depth: Int = 0) -> [String: Any]? {
    if nodeCount >= maxNodes || depth > 18 || ProcessInfo.processInfo.systemUptime > deadline {
        truncated = true
        return nil
    }
    nodeCount += 1
    let role = stringValue(attribute(element, "AXRole")) ?? ""
    var result: [String: Any] = ["role": role]
    let textRole = ["AXWindow", "AXStaticText", "AXTextArea", "AXTextField", "AXComboBox", "AXRow"].contains(role)
    if textRole, let title = stringValue(attribute(element, "AXTitle")), !title.isEmpty {
        result["title"] = String(title.prefix(10000))
    }
    if role == "AXTextArea" || role == "AXTextField" || role == "AXComboBox" {
        var editable = DarwinBoolean(false)
        AXUIElementIsAttributeSettable(element, "AXValue" as CFString, &editable)
        if editable.boolValue {
            result["editable"] = true
            if let count = attribute(element, "AXNumberOfCharacters") as? NSNumber {
                result["hasDraft"] = count.intValue > 0
            }
        } else if let value = stringValue(attribute(element, "AXValue")), !value.isEmpty {
            result["value"] = String(value.prefix(10000))
            if value.count > 10000 { truncated = true }
        }
    } else if textRole, let value = stringValue(attribute(element, "AXValue")), !value.isEmpty {
        result["value"] = String(value.prefix(10000))
        if value.count > 10000 { truncated = true }
    }
    if let children = attribute(element, "AXChildren") as? [AXUIElement] {
        var nodes: [[String: Any]] = []
        for child in children {
            if let node = tree(child, depth + 1) { nodes.append(node) }
            if nodeCount >= maxNodes || ProcessInfo.processInfo.systemUptime > deadline {
                truncated = true
                break
            }
        }
        if !nodes.isEmpty { result["children"] = nodes }
    }
    return result
}

var output: [String: Any] = ["source": "macos_accessibility", "readOnly": true,
                            "trusted": AXIsProcessTrusted(), "status": "app_not_running"]
if let app = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "com.kakao.KakaoTalkMac" }) {
    let application = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(application, 0.15)
    output["running"] = true
    if !AXIsProcessTrusted() {
        output["status"] = "accessibility_permission_required"
    } else if let windows = attribute(application, "AXWindows") as? [AXUIElement] {
        var results: [[String: Any]] = []
        for window in windows.prefix(12) {
            let title = stringValue(attribute(window, "AXTitle")) ?? ""
            if !requestedTitle.isEmpty && title != requestedTitle { continue }
            var item: [String: Any] = ["title": title]
            if mode == "snapshot", let node = tree(window) { item["tree"] = node }
            results.append(item)
        }
        output["windows"] = results
        output["status"] = results.isEmpty && !requestedTitle.isEmpty ? "window_not_found" : "ok"
    } else {
        output["status"] = "accessibility_unavailable"
    }
} else {
    output["running"] = false
}
output["truncated"] = truncated
output["nodeCount"] = nodeCount
if let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]),
   let string = String(data: data, encoding: .utf8) {
    print(string)
}
