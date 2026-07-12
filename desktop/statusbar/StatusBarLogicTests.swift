import Foundation

@main
private enum StatusBarLogicTests {
    static func main() {
        assert(quotaTone(for: 70) == .green)
        assert(quotaTone(for: 69.9) == .amber)
        assert(quotaTone(for: 30) == .amber)
        assert(quotaTone(for: 29.9) == .red)

        let now = Date(timeIntervalSince1970: 0)
        let eightDays = now.addingTimeInterval(8 * 86_400)
        assert(compactResetCreditLabel(count: 2, expiresAt: eightDays, now: now) == "Reset 2 / 8d")
        assert(compactResetCreditLabel(count: 0, expiresAt: eightDays, now: now) == nil)
        assert(compactResetCreditLabel(count: 3, expiresAt: nil, now: now) == "Reset 3")
        assert(elapsedTime(since: now.addingTimeInterval(-(86_400 + 3_600)), now: now) == "1d 1h")
        assert(elapsedTime(since: now.addingTimeInterval(-3_600), now: now) == "1h 0m")

        assert(refreshStatusLabel(isRefreshing: true, lastRefreshedAt: nil) == "Refreshing...")
        assert(refreshStatusLabel(isRefreshing: false, lastRefreshedAt: nil) == nil)
        assert(refreshStatusLabel(isRefreshing: false, lastRefreshedAt: now)?.hasPrefix("Updated ") == true)

        assert(averageRemaining([90, 95, 94]) == 93)
        assert(averageRemaining([nil, 50]) == 50)
        assert(averageRemaining([nil, nil]) == nil)
        assert(quotaSummaryTitle(primary: 93, secondary: 7, monthly: nil, activeCount: 3, totalCount: 3) == "5h 93% W 7% (3/3)")

        let burnFirst = routingBadgePresentation("burn_first")
        assert(burnFirst.label == "Burn first")
        assert(burnFirst.tone == .burnFirst)
        assert(burnFirst.symbolName == "flame.fill")

        let preserve = routingBadgePresentation("preserve")
        assert(preserve.label == "Preserve")
        assert(preserve.tone == .preserve)
        assert(preserve.symbolName == "shield")

        let normal = routingBadgePresentation("normal")
        assert(normal.label == "Normal")
        assert(normal.tone == .neutral)
        assert(normal.symbolName == nil)
    }
}
