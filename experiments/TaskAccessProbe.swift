import AppKit
import Darwin
import Foundation

let running = NSRunningApplication.runningApplications(withBundleIdentifier: "com.kakao.KakaoTalkMac")
var report: [String: Any] = ["attempted": false, "memoryRead": false, "memoryWritten": false]
if let app = running.first {
    var port: mach_port_t = 0
    let status = task_for_pid(mach_task_self_, app.processIdentifier, &port)
    if status == KERN_SUCCESS {
        mach_port_deallocate(mach_task_self_, port)
    }
    report["attempted"] = true
    report["acquired"] = status == KERN_SUCCESS
    report["kernelStatus"] = status
} else {
    report["reason"] = "app_not_running"
}
let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
