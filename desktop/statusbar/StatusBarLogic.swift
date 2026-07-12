import Foundation

enum QuotaTone {
    case green
    case amber
    case red
}

enum RoutingBadgeTone {
    case neutral
    case burnFirst
    case preserve
}

struct RoutingBadgePresentation {
    let label: String
    let tone: RoutingBadgeTone
    let symbolName: String?
}

func routingBadgePresentation(_ value: String) -> RoutingBadgePresentation {
    switch value {
    case "burn_first":
        return RoutingBadgePresentation(label: "Burn first", tone: .burnFirst, symbolName: "flame.fill")
    case "preserve":
        return RoutingBadgePresentation(label: "Preserve", tone: .preserve, symbolName: "shield")
    case "normal":
        return RoutingBadgePresentation(label: "Normal", tone: .neutral, symbolName: nil)
    default:
        return RoutingBadgePresentation(label: value, tone: .neutral, symbolName: nil)
    }
}

func quotaTone(for percent: Double) -> QuotaTone {
    if percent >= 70 {
        return .green
    }
    if percent >= 30 {
        return .amber
    }
    return .red
}

func compactResetCreditLabel(count: Int, expiresAt: Date?, now: Date = Date()) -> String? {
    guard count > 0 else {
        return nil
    }
    guard let expiresAt else {
        return "Reset \(count)"
    }

    let seconds = max(0, Int(expiresAt.timeIntervalSince(now)))
    let value: String
    if seconds >= 86_400 {
        value = "\(seconds / 86_400)d"
    } else if seconds >= 3_600 {
        value = "\(seconds / 3_600)h"
    } else if seconds > 0 {
        value = "\(max(1, seconds / 60))m"
    } else {
        value = "now"
    }
    return "Reset \(count) / \(value)"
}

func refreshStatusLabel(isRefreshing: Bool, lastRefreshedAt: Date?) -> String? {
    if isRefreshing {
        return "Refreshing..."
    }
    guard let lastRefreshedAt else {
        return nil
    }
    let formatter = DateFormatter()
    formatter.dateStyle = .none
    formatter.timeStyle = .medium
    return "Updated \(formatter.string(from: lastRefreshedAt))"
}

func averageRemaining(_ values: [Double?]) -> Double? {
    let available = values.compactMap { $0 }
    guard !available.isEmpty else {
        return nil
    }
    return available.reduce(0, +) / Double(available.count)
}

func quotaSummaryTitle(
    primary: Double?,
    secondary: Double?,
    monthly: Double?,
    activeCount: Int,
    totalCount: Int
) -> String {
    var parts: [String] = []
    if let primary {
        parts.append("5h \(Int(round(primary)))%")
    }
    if let secondary {
        parts.append("W \(Int(round(secondary)))%")
    }
    if primary == nil, secondary == nil, let monthly {
        parts.append("M \(Int(round(monthly)))%")
    }
    parts.append("(\(activeCount)/\(totalCount))")
    return parts.joined(separator: " ")
}
