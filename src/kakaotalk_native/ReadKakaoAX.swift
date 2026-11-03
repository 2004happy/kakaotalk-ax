import AppKit
import ApplicationServices
import Foundation

// Read-only bridge for exposed AX transcript rows.
// Only an exact, already-open chat window is read. Never traverse its composer contents.
enum ReadFailure: Error { case blocked(String) }
let started = ProcessInfo.processInfo.systemUptime
let deadline = started + 8.0
let maximumRows = 1000
var nodesVisited = 0
var textBytes = 0
var optionalAttributeFailures: [String: Int] = [:]

func checkBudget() throws {
    guard ProcessInfo.processInfo.systemUptime < deadline, nodesVisited <= 20_000,
          textBytes <= 4 * 1024 * 1024 else {
        throw ReadFailure.blocked("accessibility_read_limit")
    }
}

func attribute(_ element: AXUIElement, _ name: String, optional: Bool = false) throws -> CFTypeRef? {
    try checkBudget()
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    if result == .success { return value }
    if [.attributeUnsupported, .noValue, .notImplemented].contains(result) { return nil }
    // Kakao returns AXError.failure for some advertised but empty descriptions/help.
    // Only these optional strings may be absent; a failed body/value read still blocks.
    if result == .failure && optional {
        optionalAttributeFailures[name, default: 0] += 1
        return nil
    }
    throw ReadFailure.blocked("accessibility_read_failed_\(name)_\(result.rawValue)")
}

func string(_ element: AXUIElement, _ name: String, optional: Bool = false) throws -> String {
    let value = try attribute(element, name, optional: optional)
    if let text = value as? String { return text }
    if let text = value as? NSAttributedString { return text.string }
    return ""
}

func elements(_ element: AXUIElement, _ name: String) throws -> [AXUIElement] {
    return try attribute(element, name) as? [AXUIElement] ?? []
}

func textValue(_ value: CFTypeRef?) -> String {
    if let text = value as? String { return text }
    if let text = value as? NSAttributedString { return text.string }
    return ""
}

func nodeAttributes(_ element: AXUIElement) throws -> [String: CFTypeRef] {
    try checkBudget()
    // One IPC round trip per node instead of separate role/name/help/children reads.
    // AXValue is deliberately excluded until the composer check has passed.
    let names = ["AXRole", "AXTitle", "AXDescription", "AXHelp", "AXChildren"]
    var copied: CFArray?
    let result = AXUIElementCopyMultipleAttributeValues(element, names as CFArray, [], &copied)
    guard result == .success, let values = copied as? [CFTypeRef], values.count == names.count else {
        throw ReadFailure.blocked("accessibility_batch_read_failed_\(result.rawValue)")
    }
    var output: [String: CFTypeRef] = [:]
    for (index, name) in names.enumerated() {
        let value = values[index]
        if CFGetTypeID(value) == CFNullGetTypeID() { continue }
        if CFGetTypeID(value) == AXValueGetTypeID() {
            let boxed = value as! AXValue
            if AXValueGetType(boxed) == .axError {
                var error = AXError.success
                guard AXValueGetValue(boxed, .axError, &error) else {
                    throw ReadFailure.blocked("invalid_accessibility_batch_error")
                }
                if [.attributeUnsupported, .noValue, .notImplemented].contains(error) { continue }
                if ["AXTitle", "AXDescription", "AXHelp"].contains(name) && error == .failure {
                    optionalAttributeFailures[name, default: 0] += 1
                    continue
                }
                throw ReadFailure.blocked("accessibility_read_failed_\(name)_\(error.rawValue)")
            }
        }
        output[name] = value
    }
    return output
}

struct IdentitySet {
    var buckets: [CFHashCode: [AXUIElement]] = [:]
    mutating func insert(_ element: AXUIElement) -> Bool {
        let key = CFHash(element)
        if buckets[key, default: []].contains(where: { CFEqual($0, element) }) { return false }
        buckets[key, default: []].append(element)
        return true
    }
}

func controls(_ window: AXUIElement) throws -> (composers: Int, tables: [AXUIElement]) {
    var seen = IdentitySet(), composers = 0, tables: [AXUIElement] = []
    func walk(_ element: AXUIElement, _ depth: Int) throws {
        guard seen.insert(element) else { return }
        nodesVisited += 1
        try checkBudget()
        guard depth <= 16 else { throw ReadFailure.blocked("accessibility_tree_depth") }
        let role = try string(element, "AXRole")
        if ["AXSheet", "AXDialog"].contains(role) { throw ReadFailure.blocked("modal_dialog_present") }
        if role == "AXTable" { tables.append(element); return }
        if ["AXRow", "AXCell"].contains(role) { return }
        if role == "AXTextArea" {
            if try ["메시지 입력", "Message", "Type a message", "Enter a message"].contains(string(element, "AXDescription")) {
                composers += 1
            }
            return
        }
        for child in try elements(element, "AXChildren") { try walk(child, depth + 1) }
    }
    if !(try elements(window, "AXSheets")).isEmpty { throw ReadFailure.blocked("modal_dialog_present") }
    try walk(window, 0)
    return (composers, tables)
}

func rows(_ table: AXUIElement) throws -> (items: [AXUIElement], count: Int, start: Int, source: String) {
    try checkBudget()
    var count: CFIndex = 0
    let result = AXUIElementGetAttributeValueCount(table, "AXRows" as CFString, &count)
    if result == .success {
        let start = max(0, count - maximumRows)
        if count == 0 { return ([], 0, 0, "AXRows") }
        var value: CFArray?
        let copied = AXUIElementCopyAttributeValues(table, "AXRows" as CFString, start, count - start, &value)
        guard copied == .success, let items = value as? [AXUIElement], items.count == count - start else {
            throw ReadFailure.blocked("transcript_rows_unavailable")
        }
        return (items, count, start, "AXRows")
    }
    guard [.attributeUnsupported, .noValue, .notImplemented].contains(result) else {
        throw ReadFailure.blocked("transcript_rows_unavailable")
    }
    let children = try elements(table, "AXChildren").filter { try string($0, "AXRole") == "AXRow" }
    let start = max(0, children.count - maximumRows)
    return (Array(children.suffix(maximumRows)), children.count, start, "AXChildren")
}

func readRow(_ row: AXUIElement, _ index: Int) throws -> [String: Any] {
    var seen = IdentitySet(), parts: [[String: String]] = []
    func walk(_ element: AXUIElement, _ depth: Int) throws {
        guard seen.insert(element) else { return }
        nodesVisited += 1
        try checkBudget()
        guard depth <= 16 else { throw ReadFailure.blocked("accessibility_tree_depth") }
        let attributes = try nodeAttributes(element)
        let role = textValue(attributes["AXRole"])
        guard !role.isEmpty else { throw ReadFailure.blocked("accessibility_role_unavailable") }
        var part = ["role": role]
        let description = textValue(attributes["AXDescription"])
        if role == "AXTextArea", ["메시지 입력", "Message", "Type a message", "Enter a message"].contains(description) {
            throw ReadFailure.blocked("composer_inside_transcript")
        }
        if !description.isEmpty { part["AXDescription"] = description; textBytes += description.utf8.count }
        if !["AXRow", "AXCell"].contains(role) {
            let value = try string(element, "AXValue", optional: !["AXTextArea", "AXStaticText"].contains(role))
            if !value.isEmpty { part["AXValue"] = value; textBytes += value.utf8.count }
        }
        for name in ["AXTitle", "AXHelp"] {
            let text = textValue(attributes[name])
            if !text.isEmpty { part[name] = text; textBytes += text.utf8.count }
        }
        try checkBudget()
        if part.count > 1 { parts.append(part) }
        // A text area's value already contains its text, including newlines.
        if role == "AXTextArea" { return }
        for child in attributes["AXChildren"] as? [AXUIElement] ?? [] { try walk(child, depth + 1) }
    }
    try walk(row, 0)
    return ["rowIndex": index, "parts": parts]
}

func run() throws -> [String: Any] {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 16_384,
          let request = try JSONSerialization.jsonObject(with: input) as? [String: Any],
          let action = request["action"] as? String, ["windows", "read"].contains(action) else {
        throw ReadFailure.blocked("invalid_read_request")
    }
    guard AXIsProcessTrusted() else { throw ReadFailure.blocked("accessibility_permission_required") }
    guard let app = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "com.kakao.KakaoTalkMac" }) else {
        throw ReadFailure.blocked("app_not_running")
    }
    let application = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(application, 0.2)
    let windows = try elements(application, "AXWindows")
    if action == "windows" {
        var items: [[String: Any]] = []
        let titles = try windows.map { try string($0, "AXTitle") }
        for window in windows {
            let title = try string(window, "AXTitle")
            let matches = titles.filter { $0 == title }.count
            let minimized = (try? attribute(window, "AXMinimized")) as? Bool
            do {
                let found = try controls(window)
                var item: [String: Any] = ["windowTitle": title, "titleMatches": matches,
                    "minimized": minimized as Any? ?? NSNull(),
                    "readable": !title.isEmpty && matches == 1 && found.composers == 1 && found.tables.count == 1]
                if matches > 1 { item["reason"] = "target_room_window_ambiguous" }
                items.append(item)
            } catch ReadFailure.blocked(let reason) {
                items.append(["windowTitle": title, "readable": false, "reason": reason])
            }
        }
        return ["status": "ok", "items": items, "processId": app.processIdentifier]
    }
    guard let titles = request["windowTitles"] as? [String], titles.count == 1, !titles[0].isEmpty else {
        throw ReadFailure.blocked("exact_window_title_required")
    }
    let matches = try windows.filter { try string($0, "AXTitle") == titles[0] }
    guard matches.count == 1 else {
        throw ReadFailure.blocked(matches.isEmpty ? "target_room_window_not_open" : "target_room_window_ambiguous")
    }
    let window = matches[0]
    let found = try controls(window)
    guard found.composers == 1, found.tables.count == 1 else {
        throw ReadFailure.blocked("chat_transcript_not_unique")
    }
    let before = try rows(found.tables[0])
    var seen = IdentitySet(), result: [[String: Any]] = []
    for (offset, row) in before.items.enumerated() {
        guard try string(row, "AXRole") == "AXRow", seen.insert(row) else {
            throw ReadFailure.blocked("invalid_transcript_rows")
        }
        result.append(try readRow(row, before.start + offset))
    }
    let after = try rows(found.tables[0])
    let currentWindows = try elements(application, "AXWindows").filter { try string($0, "AXTitle") == titles[0] }
    guard currentWindows.count == 1, CFEqual(currentWindows[0], window),
          try string(window, "AXTitle") == titles[0],
          before.count == after.count, before.items.count == after.items.count,
          zip(before.items, after.items).allSatisfy({ CFEqual($0.0, $0.1) }) else {
        throw ReadFailure.blocked("transcript_changed_during_read")
    }
    return ["status": "ok", "windowTitle": titles[0], "processId": app.processIdentifier,
            "rows": result, "exposedRowCount": before.count, "rowStart": before.start,
            "rowsSource": before.source, "rowsTruncated": before.start > 0]
}

var report: [String: Any]
do { report = try run() }
catch ReadFailure.blocked(let reason) { report = ["status": "blocked", "reason": reason] }
catch { report = ["status": "bridge_error"] }
report["source"] = "macos_accessibility"
report["attributeReadMode"] = "batched_metadata"
report["databaseCalls"] = 0
report["uiActions"] = 0
report["optionalAttributeFailures"] = optionalAttributeFailures
report["dispatchAttempted"] = false
report["nodesVisited"] = nodesVisited
report["elapsedMs"] = Int((ProcessInfo.processInfo.systemUptime - started) * 1000)
if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
   let output = String(data: data, encoding: .utf8) { print(output) }
