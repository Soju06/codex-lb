import Darwin
import SwiftUI

@main
struct AgentLBApp: App {
  @State private var appState: AppState

  init() {
    if !SingleInstanceGuard.acquire(lockURL: SingleInstanceGuard.defaultLockURL()) {
      FileHandle.standardError.write(
        Data("AgentLB: another instance holds the single-instance lock; exiting.\n".utf8)
      )
      exit(0)
    }

    let state = AppState()
    state.startBackgroundPolling()
    _appState = State(initialValue: state)
    Self.scheduleWindowDiagnostics(state)
    StatusItemWatch.schedule()
  }

  // AGENTLB_DEBUG_WINDOWS=1: dump NSApp.windows + AppState data counts so a
  // headless session can tell whether the status item exists, the panel's
  // frame, and whether the open-state fetches actually populated the model.
  private static func scheduleWindowDiagnostics(_ state: AppState) {
    guard ProcessInfo.processInfo.environment["AGENTLB_DEBUG_WINDOWS"] == "1" else { return }
    Task { @MainActor in
      for second in [5, 30] {
        try? await Task.sleep(for: .seconds(second))
        let lines = NSApp.windows.map { w in
          "\(type(of: w)) frame=\(w.frame) visible=\(w.isVisible) " +
            "occlusion=\(w.occlusionState.contains(.visible) ? "visible" : "occluded")"
        }
        let screens = NSScreen.screens.map { "\($0.frame)" }.joined(separator: " ")
        let data = "state: status=\(state.serviceStatus) accounts=\(state.accounts.count) "
          + "summary=\(state.summary != nil) recent=\(state.recent.count) "
          + "errors=\(state.sectionErrors)"
        let report = "t+\(second)s windows=\(NSApp.windows.count) screens=\(screens)\n"
          + data + "\n"
          + lines.joined(separator: "\n") + "\n"
          + panelDump() + "---\n"
        if let data = report.data(using: .utf8),
          let handle = FileHandle(forWritingAtPath: "/tmp/agentlb-windows.log")
            ?? (FileManager.default.createFile(atPath: "/tmp/agentlb-windows.log", contents: nil)
              ? FileHandle(forWritingAtPath: "/tmp/agentlb-windows.log") : nil)
        {
          handle.seekToEndOfFile()
          handle.write(data)
          handle.closeFile()
        }
      }
    }
  }

  // Renders the menu bar panel's content view to /tmp/agentlb-panel.png and
  // returns its AppKit subtree (type + frame per view) — works even when the
  // panel window is occluded (e.g. locked screen during headless QA).
  @MainActor
  private static func panelDump() -> String {
    guard
      let panel = NSApp.windows.first(where: {
        String(describing: type(of: $0)).contains("MenuBarExtraWindow")
      }),
      let content = panel.contentView
    else { return "panel: none\n" }

    if let rep = content.bitmapImageRepForCachingDisplay(in: content.bounds) {
      content.cacheDisplay(in: content.bounds, to: rep)
      if let png = rep.representation(using: .png, properties: [:]) {
        try? png.write(to: URL(fileURLWithPath: "/tmp/agentlb-panel.png"))
      }
    }

    var out = ""
    func walk(_ view: NSView, _ depth: Int) {
      out += String(repeating: "  ", count: depth)
        + "\(type(of: view)) \(view.frame)\n"
      guard depth < 8 else { return }
      for sub in view.subviews { walk(sub, depth + 1) }
    }
    walk(content, 0)
    return out
  }

  var body: some Scene {
    MenuBarExtra {
      // §9.1: 460 pt wide; height is deterministic — RootView pins itself
      // to the PanelLayout-computed height (≤ 720) and PanelResizer keeps
      // the panel window in sync (macOS 26 panels never re-measure alone).
      RootView()
        .environment(appState)
        .frame(width: PanelMetrics.width)
    } label: {
      Image(nsImage: appState.statusIcon)
    }
    .menuBarExtraStyle(.window)
  }
}
