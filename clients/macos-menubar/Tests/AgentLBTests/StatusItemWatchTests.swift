import AppKit
import XCTest
@testable import AgentLB

final class StatusItemWatchTests: XCTestCase {

  private var counterURL: URL!

  override func setUp() {
    super.setUp()
    counterURL = FileManager.default.temporaryDirectory
      .appendingPathComponent("StatusItemWatchTests-\(UUID().uuidString)")
  }

  override func tearDown() {
    try? FileManager.default.removeItem(at: counterURL)
    super.tearDown()
  }

  private final class FakeStatusBarWindow: NSWindow {}

  @MainActor
  func testDetectsStatusBarWindowByClassName() {
    let plain = NSWindow(contentRect: .zero, styleMask: [], backing: .buffered, defer: true)
    let status = FakeStatusBarWindow(contentRect: .zero, styleMask: [], backing: .buffered, defer: true)
    XCTAssertFalse(StatusItemWatch.hasStatusItemWindow([]))
    XCTAssertFalse(StatusItemWatch.hasStatusItemWindow([plain]))
    XCTAssertTrue(StatusItemWatch.hasStatusItemWindow([plain, status]))
  }

  func testCounterRoundTripsAndDefaultsToZero() {
    XCTAssertEqual(StatusItemWatch.readCounter(at: counterURL), 0)
    StatusItemWatch.writeCounter(4, at: counterURL)
    XCTAssertEqual(StatusItemWatch.readCounter(at: counterURL), 4)
    StatusItemWatch.writeCounter(0, at: counterURL)
    XCTAssertEqual(StatusItemWatch.readCounter(at: counterURL), 0)
  }

  func testCounterIgnoresGarbage() {
    try? "nope".write(to: counterURL, atomically: true, encoding: .utf8)
    XCTAssertEqual(StatusItemWatch.readCounter(at: counterURL), 0)
  }
}
