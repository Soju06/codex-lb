import AppKit
import Foundation

/// Self-check for the one failure mode a LaunchAgent cannot see: the process
/// runs, but the `MenuBarExtra` status item never registered (launched too
/// early at login, or from a non-GUI context), so the menu bar shows nothing.
///
/// A registered status item owns an `NSStatusBarWindow` in `NSApp.windows`.
/// If none exists at both `checkDelays`, the app exits with `exitCode` so the
/// LaunchAgent (`KeepAlive: SuccessfulExit=false`) relaunches it into a ready
/// session. A counter file caps consecutive self-exits so a false positive
/// can never turn into a restart loop; it resets to zero once the item is seen.
enum StatusItemWatch {
  static let exitCode: Int32 = 3
  static let maxConsecutiveExits = 5
  static let checkDelays: [Duration] = [.seconds(15), .seconds(45)]

  static var counterURL: URL {
    SingleInstanceGuard.defaultLockURL()
      .deletingLastPathComponent()
      .appendingPathComponent("status-item-restarts")
  }

  @MainActor
  static func schedule() {
    Task { @MainActor in
      for delay in checkDelays {
        try? await Task.sleep(for: delay)
        if hasStatusItemWindow(NSApp.windows) {
          writeCounter(0)
          return
        }
      }
      let exits = readCounter() + 1
      writeCounter(exits)
      let note: String
      if exits > maxConsecutiveExits {
        note = "AgentLB: status item missing after \(exits - 1) relaunches; staying up.\n"
        FileHandle.standardError.write(Data(note.utf8))
        return
      }
      note = "AgentLB: status item never registered; exiting \(exitCode) for launchd relaunch (\(exits)/\(maxConsecutiveExits)).\n"
      FileHandle.standardError.write(Data(note.utf8))
      exit(exitCode)
    }
  }

  /// True when any window's class name marks it as a status bar host.
  nonisolated static func hasStatusItemWindow(_ windows: [NSWindow]) -> Bool {
    windows.contains { String(describing: type(of: $0)).contains("StatusBarWindow") }
  }

  nonisolated static func readCounter(at url: URL? = nil) -> Int {
    let url = url ?? counterURL
    guard let text = try? String(contentsOf: url, encoding: .utf8) else { return 0 }
    return Int(text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
  }

  nonisolated static func writeCounter(_ value: Int, at url: URL? = nil) {
    let url = url ?? counterURL
    try? String(value).write(to: url, atomically: true, encoding: .utf8)
  }
}
