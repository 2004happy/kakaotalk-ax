import AppKit
import ApplicationServices
import Foundation

// Only inspect window/control metadata. Message tables and their contents are never traversed.
// JSON on stdin keeps message text out of process arguments. There is exactly one AXPress site.
enum BridgeFailure: Error {
    case blocked(String)
}

let started = ProcessInfo.processInfo.systemUptime
let deadline = started + 8.0
var visited = 0
var dispatchAttempted = false
var composerChanged = false

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

func string(_ element: AXUIElement, _ name: String) -> String {
    let value = attribute(element, name)
    if let text = value as? String { return text }
    if let text = value as? NSAttributedString { return text.string }
    return ""
}

func composerText(_ element: AXUIElement) throws -> String {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, "AXValue" as CFString, &value)
    if result == .noValue { return "" }
    guard result == .success else { throw BridgeFailure.blocked("composer_value_unavailable") }
    if let text = value as? String { return text }
    if let text = value as? NSAttributedString { return text.string }
    throw BridgeFailure.blocked("composer_value_unavailable")
}

func settable(_ element: AXUIElement, _ name: String) -> Bool {
    var result = DarwinBoolean(false)
    return AXUIElementIsAttributeSettable(element, name as CFString, &result) == .success && result.boolValue
}

func actionNames(_ element: AXUIElement) -> [String] {
    var result: CFArray?
    _ = AXUIElementCopyActionNames(element, &result)
    return result as? [String] ?? []
}

func descendants(_ element: AXUIElement, depth: Int = 0) throws -> [AXUIElement] {
    visited += 1
    guard visited <= 600, depth <= 10, ProcessInfo.processInfo.systemUptime < deadline else {
        throw BridgeFailure.blocked("control_tree_limit")
    }
    let role = string(element, "AXRole")
    if ["AXTable", "AXRow", "AXCell"].contains(role) { return [] }
    if ["AXSheet", "AXDialog"].contains(role) { throw BridgeFailure.blocked("modal_dialog_present") }
    var result = [element]
    for child in attribute(element, "AXChildren") as? [AXUIElement] ?? [] {
        result += try descendants(child, depth: depth + 1)
    }
    return result
}

func controls(_ window: AXUIElement) throws -> (AXUIElement, AXUIElement) {
    let nodes = try descendants(window)
    let composers = nodes.filter {
        string($0, "AXRole") == "AXTextArea" && settable($0, "AXValue") &&
        ["메시지 입력", "Message", "Type a message", "Enter a message"].contains(string($0, "AXDescription"))
    }
    let buttons = nodes.filter {
        string($0, "AXRole") == "AXButton" && ["전송", "Send"].contains(string($0, "AXTitle"))
    }
    guard composers.count == 1, buttons.count == 1 else {
        throw BridgeFailure.blocked("composer_or_send_button_not_unique")
    }
    guard actionNames(buttons[0]).contains("AXPress") else {
        throw BridgeFailure.blocked("send_button_has_no_axpress")
    }
    return (composers[0], buttons[0])
}

func run() throws -> [String: Any] {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 128 * 1024,
          let request = try JSONSerialization.jsonObject(with: input) as? [String: Any],
          let titles = request["windowTitles"] as? [String], !titles.isEmpty,
          ["inspect", "raise", "send"].contains(request["action"] as? String ?? "") else {
        throw BridgeFailure.blocked("invalid_request")
    }
    guard AXIsProcessTrusted() else { throw BridgeFailure.blocked("accessibility_permission_required") }
    guard let app = NSWorkspace.shared.runningApplications.first(where: {
        $0.bundleIdentifier == "com.kakao.KakaoTalkMac"
    }) else { throw BridgeFailure.blocked("app_not_running") }
    let application = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(application, 0.25)
    let windows = attribute(application, "AXWindows") as? [AXUIElement] ?? []
    let matches = windows.filter { titles.contains(string($0, "AXTitle")) }
    guard matches.count == 1 else {
        throw BridgeFailure.blocked(matches.isEmpty ? "target_room_window_not_open" : "target_room_window_ambiguous")
    }
    let window = matches[0]
    if let sheets = attribute(window, "AXSheets") as? [AXUIElement], !sheets.isEmpty {
        throw BridgeFailure.blocked("modal_dialog_present")
    }
    if request["action"] as? String == "raise" {
        guard actionNames(window).contains("AXRaise"),
              AXUIElementPerformAction(window, "AXRaise" as CFString) == .success else {
            throw BridgeFailure.blocked("target_window_raise_failed")
        }
        return ["status": "window_raised"]
    }
    let (composer, _) = try controls(window)
    let draft = try composerText(composer)
    guard draft.isEmpty else { throw BridgeFailure.blocked("existing_draft_preserved") }
    if request["action"] as? String == "inspect" {
        return ["status": "ready", "composerFound": true, "sendAction": "AXPress", "draftPresent": false]
    }
    guard let text = request["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          text.utf8.count <= 16_000 else { throw BridgeFailure.blocked("invalid_message_text") }
    let setResult = AXUIElementSetAttributeValue(composer, "AXValue" as CFString, text as CFString)
    guard setResult == .success else { throw BridgeFailure.blocked("composer_set_failed_\(setResult.rawValue)") }
    composerChanged = true
    guard try composerText(composer) == text else { throw BridgeFailure.blocked("composer_value_mismatch") }
    // Re-resolve controls after the UI change; no persistent indices or screen coordinates.
    var (currentComposer, currentButton) = try controls(window)
    for _ in 0..<10 {
        if (attribute(currentButton, "AXEnabled") as? Bool) == true { break }
        Thread.sleep(forTimeInterval: 0.03)
        (currentComposer, currentButton) = try controls(window)
    }
    guard titles.contains(string(window, "AXTitle")),
          try composerText(currentComposer) == text,
          (attribute(currentButton, "AXEnabled") as? Bool) == true else {
        throw BridgeFailure.blocked("send_precondition_changed")
    }
    // No retry even when AXPress reports an error: the application may already have handled it.
    dispatchAttempted = true
    let result = AXUIElementPerformAction(currentButton, "AXPress" as CFString)
    return ["status": result == .success ? "dispatch_attempted" : "dispatch_result_unknown",
            "axResult": result.rawValue, "actions": ["AXValue", "AXPress"]]
}

var report: [String: Any]
do {
    report = try run()
} catch BridgeFailure.blocked(let reason) {
    report = ["status": "blocked", "reason": reason]
} catch {
    report = ["status": "bridge_error"]
}
report["dispatchAttempted"] = dispatchAttempted
report["composerChanged"] = composerChanged
report["controlNodesVisited"] = visited
report["conversationRead"] = false
report["elapsedMs"] = Int((ProcessInfo.processInfo.systemUptime - started) * 1000)
if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
   let output = String(data: data, encoding: .utf8) {
    print(output)
}
