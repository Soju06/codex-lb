import Cocoa
import Foundation
import ServiceManagement

private let defaultBaseURL = "http://127.0.0.1:2455"
private let appDisplayName = "Codex LB Status"

private final class SettingsStore {
    private let defaults = UserDefaults.standard
    private let baseURLKey = "codexLBBaseURL"

    var baseURLString: String {
        get {
            defaults.string(forKey: baseURLKey) ?? defaultBaseURL
        }
        set {
            defaults.set(Self.normalizedBaseURL(newValue), forKey: baseURLKey)
        }
    }

    var baseURL: URL {
        URL(string: baseURLString) ?? URL(string: defaultBaseURL)!
    }

    private static func normalizedBaseURL(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return defaultBaseURL
        }
        return trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

private enum ClientError: LocalizedError {
    case invalidURL(String)
    case unauthorized
    case server(statusCode: Int, body: String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL(let url):
            return "Invalid URL: \(url)"
        case .unauthorized:
            return "Dashboard login required"
        case .server(let statusCode, let body):
            if body.isEmpty {
                return "Server returned HTTP \(statusCode)"
            }
            return "Server returned HTTP \(statusCode): \(body)"
        case .transport(let message):
            return message
        }
    }
}

private struct AuthSession: Decodable {
    let authenticated: Bool
    let passwordRequired: Bool
    let totpRequiredOnLogin: Bool
    let guestAccessEnabled: Bool
    let guestPasswordRequired: Bool
    let role: String
}

private struct DashboardOverview: Decodable {
    let lastSyncAt: Date?
    let accounts: [AccountSummary]
}

private struct AccountSummary: Decodable {
    let accountId: String
    let email: String
    let alias: String?
    let displayName: String
    let planType: String
    let routingPolicy: String
    let status: String
    let usage: AccountUsage?
    let resetAtPrimary: Date?
    let resetAtSecondary: Date?
    let resetAtMonthly: Date?
    let windowMinutesPrimary: Int?
    let windowMinutesSecondary: Int?
    let windowMinutesMonthly: Int?
    let lastRefreshAt: Date?
    let deactivationReason: String?
    let securityWorkAuthorized: Bool?
    let limitWarmupEnabled: Bool?
    let limitWarmup: AccountLimitWarmupStatus?
    let availableResetCredits: Int?
    let resetCreditNearestExpiresAt: Date?
}

private struct AccountUsage: Decodable {
    let primaryRemainingPercent: Double?
    let secondaryRemainingPercent: Double?
    let monthlyRemainingPercent: Double?
}

private struct AccountLimitWarmupStatus: Decodable {
    let window: String
    let status: String
    let model: String
    let attemptedAt: Date
    let completedAt: Date?
}

private final class CodexLBClient {
    private let settings: SettingsStore
    private let session: URLSession
    private let decoder: JSONDecoder

    init(settings: SettingsStore) {
        self.settings = settings
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieStorage = HTTPCookieStorage.shared
        configuration.httpCookieAcceptPolicy = .always
        configuration.httpShouldSetCookies = true
        configuration.timeoutIntervalForRequest = 10
        self.session = URLSession(configuration: configuration)
        self.decoder = JSONDecoder.codexLBDecoder()
    }

    func getSession() async throws -> AuthSession {
        try await request(path: "/api/dashboard-auth/session")
    }

    func fetchOverview() async throws -> DashboardOverview {
        try await request(path: "/api/dashboard/overview?timeframe=7d")
    }

    func loginPassword(_ password: String) async throws -> AuthSession {
        let payload = try JSONSerialization.data(withJSONObject: ["password": password], options: [])
        return try await request(path: "/api/dashboard-auth/password/login", method: "POST", body: payload)
    }

    func loginGuest(password: String?) async throws -> AuthSession {
        let body: Data?
        if let password, !password.isEmpty {
            body = try JSONSerialization.data(withJSONObject: ["password": password], options: [])
        } else {
            body = nil
        }
        return try await request(path: "/api/dashboard-auth/guest/login", method: "POST", body: body)
    }

    func verifyTotp(_ code: String) async throws -> AuthSession {
        let payload = try JSONSerialization.data(withJSONObject: ["code": code], options: [])
        return try await request(path: "/api/dashboard-auth/totp/verify", method: "POST", body: payload)
    }

    private func request<T: Decodable>(path: String, method: String = "GET", body: Data? = nil) async throws -> T {
        let url = try endpoint(path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw ClientError.transport("Unexpected non-HTTP response")
            }
            if httpResponse.statusCode == 401 {
                throw ClientError.unauthorized
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                throw ClientError.server(
                    statusCode: httpResponse.statusCode,
                    body: String(data: data, encoding: .utf8) ?? ""
                )
            }
            return try decoder.decode(T.self, from: data)
        } catch let error as ClientError {
            throw error
        } catch {
            throw ClientError.transport(error.localizedDescription)
        }
    }

    private func endpoint(_ path: String) throws -> URL {
        let base = settings.baseURLString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let suffix = path.hasPrefix("/") ? String(path.dropFirst()) : path
        let raw = "\(base)/\(suffix)"
        guard let url = URL(string: raw) else {
            throw ClientError.invalidURL(raw)
        }
        return url
    }
}

private extension JSONDecoder {
    static func codexLBDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            if let date = DateFormatters.iso8601Fractional.date(from: value) {
                return date
            }
            if let date = DateFormatters.iso8601.date(from: value) {
                return date
            }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid ISO-8601 date: \(value)")
        }
        return decoder
    }
}

private enum DateFormatters {
    static let iso8601: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static let iso8601Fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    static let timeOnly: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .none
        formatter.timeStyle = .medium
        return formatter
    }()
}

private enum VisualStyle {
    static let menuWidth: CGFloat = 440
    static let contentWidth: CGFloat = 416
    static let accountCardHeight: CGFloat = 152
    static let cardRadius: CGFloat = 10
    static let cardBackground = NSColor(calibratedWhite: 0.12, alpha: 1)
    static let panelBackground = NSColor(calibratedWhite: 0.04, alpha: 1)
    static let border = NSColor(calibratedWhite: 0.20, alpha: 1)
    static let textPrimary = NSColor(calibratedWhite: 0.94, alpha: 1)
    static let textSecondary = NSColor(calibratedWhite: 0.67, alpha: 1)
    static let textMuted = NSColor(calibratedWhite: 0.50, alpha: 1)
    static let track = NSColor(calibratedWhite: 0.24, alpha: 1)
    static let trackDim = NSColor(calibratedWhite: 0.16, alpha: 1)
    static let green = NSColor(calibratedRed: 0.07, green: 0.78, blue: 0.45, alpha: 1)
    static let greenDim = NSColor(calibratedRed: 0.02, green: 0.28, blue: 0.20, alpha: 1)
    static let amber = NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 1)
    static let amberDim = NSColor(calibratedRed: 0.30, green: 0.20, blue: 0.03, alpha: 1)
    static let red = NSColor(calibratedRed: 1.00, green: 0.22, blue: 0.34, alpha: 1)
    static let redDim = NSColor(calibratedRed: 0.36, green: 0.08, blue: 0.11, alpha: 1)
    static let blue = NSColor(calibratedRed: 0.19, green: 0.50, blue: 0.78, alpha: 1)
    static let blueDim = NSColor(calibratedRed: 0.05, green: 0.22, blue: 0.30, alpha: 1)
}

private class RoundedPanelView: NSView {
    init(width: CGFloat, height: CGFloat, background: NSColor = VisualStyle.panelBackground) {
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: height))
        wantsLayer = true
        layer?.backgroundColor = background.cgColor
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class DotView: NSView {
    private let color: NSColor

    init(color: NSColor, size: CGFloat = 7) {
        self.color = color
        super.init(frame: NSRect(x: 0, y: 0, width: size, height: size))
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.backgroundColor = color.cgColor
        layer?.cornerRadius = size / 2
        NSLayoutConstraint.activate([
            widthAnchor.constraint(equalToConstant: size),
            heightAnchor.constraint(equalToConstant: size),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class BadgeView: NSView {
    enum Tone {
        case neutral
        case active
        case warning
        case burnFirst
        case preserve

        var background: NSColor {
            switch self {
            case .neutral:
                return NSColor(calibratedWhite: 0.10, alpha: 1)
            case .active:
                return VisualStyle.greenDim
            case .warning:
                return VisualStyle.redDim
            case .burnFirst:
                return VisualStyle.amberDim
            case .preserve:
                return VisualStyle.blueDim
            }
        }

        var border: NSColor {
            switch self {
            case .neutral:
                return NSColor(calibratedWhite: 0.24, alpha: 1)
            case .active:
                return NSColor(calibratedRed: 0.02, green: 0.45, blue: 0.31, alpha: 1)
            case .warning:
                return NSColor(calibratedRed: 0.55, green: 0.12, blue: 0.18, alpha: 1)
            case .burnFirst:
                return NSColor(calibratedRed: 0.55, green: 0.36, blue: 0.03, alpha: 1)
            case .preserve:
                return NSColor(calibratedRed: 0.08, green: 0.42, blue: 0.56, alpha: 1)
            }
        }

        var text: NSColor {
            switch self {
            case .neutral:
                return VisualStyle.textSecondary
            case .active:
                return VisualStyle.green
            case .warning:
                return VisualStyle.red
            case .burnFirst:
                return VisualStyle.amber
            case .preserve:
                return NSColor(calibratedRed: 0.20, green: 0.72, blue: 0.92, alpha: 1)
            }
        }
    }

    init(text: String, tone: Tone, dot: Bool = false, symbolName: String? = nil) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.backgroundColor = tone.background.cgColor
        layer?.borderColor = tone.border.cgColor
        layer?.borderWidth = 1
        layer?.cornerRadius = 13

        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        var markerWidth: CGFloat = 0
        if let symbolName {
            let image = NSImageView()
            image.translatesAutoresizingMaskIntoConstraints = false
            image.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: text)
            image.contentTintColor = tone.text
            image.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 11, weight: .medium)
            NSLayoutConstraint.activate([
                image.widthAnchor.constraint(equalToConstant: 12),
                image.heightAnchor.constraint(equalToConstant: 12),
            ])
            stack.addArrangedSubview(image)
            markerWidth = 18
        } else if dot {
            stack.addArrangedSubview(DotView(color: tone.text, size: 7))
            markerWidth = 13
        }
        let label = makeLabel(text, size: 12, weight: .medium, color: tone.text)
        stack.addArrangedSubview(label)
        let leadingPadding: CGFloat = markerWidth > 0 ? 10 : 12
        let contentWidth = label.intrinsicContentSize.width + leadingPadding + markerWidth + 12

        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 26),
            widthAnchor.constraint(equalToConstant: ceil(contentWidth)),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: leadingPadding),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class QuotaProgressView: NSView {
    private let percent: Double?

    init(percent: Double?) {
        self.percent = percent
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 6),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }

    override var intrinsicContentSize: NSSize {
        NSSize(width: NSView.noIntrinsicMetric, height: 6)
    }

    override func draw(_ dirtyRect: NSRect) {
        let rect = bounds.insetBy(dx: 0, dy: 0)
        let track = NSBezierPath(roundedRect: rect, xRadius: 3, yRadius: 3)
        (percent.map(quotaTrackColor) ?? VisualStyle.trackDim).setFill()
        track.fill()

        guard let percent else {
            return
        }
        let clamped = min(max(percent, 0), 100)
        guard clamped > 0 else {
            return
        }
        let fillWidth = max(4, rect.width * CGFloat(clamped / 100))
        let fillRect = NSRect(x: rect.minX, y: rect.minY, width: min(fillWidth, rect.width), height: rect.height)
        let fill = NSBezierPath(roundedRect: fillRect, xRadius: 3, yRadius: 3)
        quotaColor(percent).setFill()
        fill.fill()
    }
}

private final class QuotaMiniView: NSView {
    init(label: String, remainingPercent: Double?, resetAt: Date?) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.spacing = 6
        topRow.translatesAutoresizingMaskIntoConstraints = false

        let name = makeLabel(label, size: 12, weight: .regular, color: VisualStyle.textSecondary)
        let percent = makeLabel(formatOptionalPercent(remainingPercent), size: 12, weight: .semibold, color: VisualStyle.textPrimary)
        percent.alignment = .right
        topRow.addArrangedSubview(name)
        topRow.addArrangedSubview(NSView.spacer())
        topRow.addArrangedSubview(percent)

        let progress = QuotaProgressView(percent: remainingPercent)
        let resetText = resetAt.map { "Reset in \(relativeTime($0))" } ?? "Reset --"
        let reset = makeLabel(resetText, size: 11, weight: .regular, color: VisualStyle.textMuted)

        let stack = NSStackView(views: [topRow, progress, reset])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 5
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            topRow.widthAnchor.constraint(equalTo: stack.widthAnchor),
            progress.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class AccountCardView: NSView {
    init(account: AccountSummary) {
        super.init(frame: NSRect(x: 0, y: 0, width: VisualStyle.contentWidth, height: VisualStyle.accountCardHeight))
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.backgroundColor = VisualStyle.cardBackground.cgColor
        layer?.borderColor = VisualStyle.border.cgColor
        layer?.borderWidth = 0.8
        layer?.cornerRadius = 8

        let title = makeLabel(accountTitle(account), size: 16, weight: .semibold, color: VisualStyle.textPrimary)
        title.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let subtitle = makeLabel(accountSubtitle(account), size: 12, weight: .regular, color: VisualStyle.textMuted)
        subtitle.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let identity = NSStackView(views: [title, subtitle])
        identity.orientation = .vertical
        identity.alignment = .leading
        identity.spacing = 1
        identity.translatesAutoresizingMaskIntoConstraints = false

        let routingPresentation = routingBadgePresentation(account.routingPolicy)
        let routingTone: BadgeView.Tone
        switch routingPresentation.tone {
        case .neutral:
            routingTone = .neutral
        case .burnFirst:
            routingTone = .burnFirst
        case .preserve:
            routingTone = .preserve
        }
        let routing = BadgeView(
            text: routingPresentation.label,
            tone: routingTone,
            symbolName: routingPresentation.symbolName
        )
        let statusTone: BadgeView.Tone = account.status == "active" ? .active : .warning
        let status = BadgeView(text: account.status.capitalized, tone: statusTone, dot: account.status == "active")

        var topViews: [NSView] = [identity, routing]
        if account.securityWorkAuthorized == true {
            topViews.append(shieldView())
        }
        topViews.append(status)
        let topRow = NSStackView(views: topViews)
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.distribution = .fill
        topRow.spacing = 8
        topRow.translatesAutoresizingMaskIntoConstraints = false
        addSubview(topRow)

        let resetCreditLabel = compactResetCreditLabel(
            count: account.availableResetCredits ?? 0,
            expiresAt: account.resetCreditNearestExpiresAt
        ).map { makeLabel($0, size: 10, weight: .semibold, color: VisualStyle.blue) }
        if let resetCreditLabel {
            addSubview(resetCreditLabel)
            NSLayoutConstraint.activate([
                resetCreditLabel.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -14),
                resetCreditLabel.topAnchor.constraint(equalTo: topAnchor, constant: 4),
            ])
        }

        let quotaRow = NSStackView()
        quotaRow.orientation = .horizontal
        quotaRow.alignment = .top
        quotaRow.distribution = .fillEqually
        quotaRow.spacing = 12
        quotaRow.translatesAutoresizingMaskIntoConstraints = false
        addSubview(quotaRow)

        let quotaViews = quotaViewsForAccount(account)
        if quotaViews.isEmpty {
            quotaRow.addArrangedSubview(makeLabel("Quota unavailable", size: 12, color: VisualStyle.textMuted))
        } else {
            for view in quotaViews.prefix(2) {
                quotaRow.addArrangedSubview(view)
            }
        }

        let warmupLeft = makeLabel(warmupStatus(account), size: 11, weight: .regular, color: VisualStyle.textMuted)
        let warmupRight = makeLabel(warmupAttempt(account), size: 11, weight: .regular, color: VisualStyle.textMuted)
        warmupRight.alignment = .right
        let bottomRow = NSStackView(views: [warmupLeft, NSView.spacer(), warmupRight])
        bottomRow.orientation = .horizontal
        bottomRow.alignment = .centerY
        bottomRow.spacing = 8
        bottomRow.translatesAutoresizingMaskIntoConstraints = false
        addSubview(bottomRow)

        NSLayoutConstraint.activate([
            widthAnchor.constraint(equalToConstant: VisualStyle.contentWidth),
            heightAnchor.constraint(equalToConstant: VisualStyle.accountCardHeight),
            topRow.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            topRow.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            topRow.topAnchor.constraint(equalTo: topAnchor, constant: resetCreditLabel == nil ? 14 : 18),
            quotaRow.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            quotaRow.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            quotaRow.topAnchor.constraint(equalTo: topRow.bottomAnchor, constant: 18),
            bottomRow.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            bottomRow.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            bottomRow.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -14),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class AccountsPanelView: RoundedPanelView {
    init(
        accounts: [AccountSummary],
        isRefreshing: Bool,
        lastRefreshedAt: Date?,
        refreshTarget: AnyObject,
        refreshAction: Selector
    ) {
        let cardHeight = VisualStyle.accountCardHeight
        let gap: CGFloat = 8
        let headerHeight: CGFloat = 32
        let maxCardsVisible: CGFloat = 4
        let contentHeight = headerHeight + CGFloat(accounts.count) * cardHeight + CGFloat(max(accounts.count - 1, 0)) * gap + 18
        let maxHeight = headerHeight + maxCardsVisible * cardHeight + (maxCardsVisible - 1) * gap + 18
        let height = min(contentHeight, maxHeight)
        super.init(width: VisualStyle.menuWidth, height: height)

        let refreshControl: NSView
        if isRefreshing {
            let indicator = NSProgressIndicator()
            indicator.style = .spinning
            indicator.controlSize = .small
            indicator.translatesAutoresizingMaskIntoConstraints = false
            indicator.startAnimation(nil)
            NSLayoutConstraint.activate([
                indicator.widthAnchor.constraint(equalToConstant: 16),
                indicator.heightAnchor.constraint(equalToConstant: 16),
            ])
            refreshControl = indicator
        } else {
            let button = NSButton()
            button.image = NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh")
            button.imagePosition = .imageOnly
            button.isBordered = false
            button.bezelStyle = .texturedRounded
            button.setButtonType(.momentaryPushIn)
            button.target = refreshTarget
            button.action = refreshAction
            button.translatesAutoresizingMaskIntoConstraints = false
            button.contentTintColor = VisualStyle.textSecondary
            NSLayoutConstraint.activate([
                button.widthAnchor.constraint(equalToConstant: 22),
                button.heightAnchor.constraint(equalToConstant: 22),
            ])
            refreshControl = button
        }

        let title = makeLabel("Accounts", size: 15, weight: .bold, color: VisualStyle.textPrimary)
        let accountCount = "\(accounts.filter { $0.status == "active" }.count) active / \(accounts.count) total"
        let refreshStatus = refreshStatusLabel(isRefreshing: isRefreshing, lastRefreshedAt: lastRefreshedAt)
        let count = makeLabel([accountCount, refreshStatus].compactMap { $0 }.joined(separator: " | "), size: 11, color: VisualStyle.textMuted)
        count.alignment = .right

        let header = NSStackView(views: [title, refreshControl, NSView.spacer(), count])
        header.orientation = .horizontal
        header.alignment = .centerY
        header.translatesAutoresizingMaskIntoConstraints = false
        addSubview(header)

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = gap
        stack.translatesAutoresizingMaskIntoConstraints = false

        for account in accounts.sorted(by: accountSort) {
            stack.addArrangedSubview(AccountCardView(account: account))
        }

        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.borderType = .noBorder
        scrollView.drawsBackground = false
        scrollView.hasVerticalScroller = contentHeight > maxHeight
        scrollView.autohidesScrollers = true
        scrollView.documentView = stack
        addSubview(scrollView)

        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            header.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            header.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            scrollView.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            scrollView.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            scrollView.topAnchor.constraint(equalTo: header.bottomAnchor, constant: 8),
            scrollView.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -10),
            stack.widthAnchor.constraint(equalToConstant: VisualStyle.contentWidth),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class StatusMessageView: RoundedPanelView {
    init(title: String, message: String, detail: String? = nil) {
        super.init(width: VisualStyle.menuWidth, height: detail == nil ? 96 : 118)
        let titleLabel = makeLabel(title, size: 15, weight: .bold, color: VisualStyle.textPrimary)
        let messageLabel = makeLabel(message, size: 12, color: VisualStyle.textSecondary)
        messageLabel.maximumNumberOfLines = 2
        var views: [NSView] = [titleLabel, messageLabel]
        if let detail {
            let detailLabel = makeLabel(detail, size: 11, color: VisualStyle.textMuted)
            detailLabel.maximumNumberOfLines = 2
            views.append(detailLabel)
        }
        let stack = NSStackView(views: views)
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 18),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -18),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }
}

private final class MenuContentContainerView: NSView {
    init(content: NSView) {
        super.init(frame: content.frame)
        replaceContent(with: content)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }

    func replaceContent(with content: NSView) {
        subviews.forEach { $0.removeFromSuperview() }
        frame.size = content.frame.size
        content.frame = bounds
        content.autoresizingMask = [.width, .height]
        addSubview(content)
    }
}

private extension NSView {
    static func spacer() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return view
    }
}

private func makeLabel(
    _ text: String,
    size: CGFloat,
    weight: NSFont.Weight = .regular,
    color: NSColor = VisualStyle.textPrimary
) -> NSTextField {
    let label = NSTextField(labelWithString: text)
    label.translatesAutoresizingMaskIntoConstraints = false
    label.font = .systemFont(ofSize: size, weight: weight)
    label.textColor = color
    label.lineBreakMode = .byTruncatingTail
    label.maximumNumberOfLines = 1
    label.backgroundColor = .clear
    return label
}

private func shieldView() -> NSView {
    let imageView = NSImageView()
    imageView.translatesAutoresizingMaskIntoConstraints = false
    imageView.image = NSImage(systemSymbolName: "shield.checkered", accessibilityDescription: "authorized")
    imageView.contentTintColor = VisualStyle.green
    imageView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 13, weight: .medium)
    NSLayoutConstraint.activate([
        imageView.widthAnchor.constraint(equalToConstant: 18),
        imageView.heightAnchor.constraint(equalToConstant: 18),
    ])
    return imageView
}

private func quotaColor(_ percent: Double) -> NSColor {
    switch quotaTone(for: percent) {
    case .green:
        return VisualStyle.green
    case .amber:
        return VisualStyle.amber
    case .red:
        return VisualStyle.red
    }
}

private func quotaTrackColor(_ percent: Double) -> NSColor {
    switch quotaTone(for: percent) {
    case .green:
        return VisualStyle.greenDim
    case .amber:
        return VisualStyle.amberDim
    case .red:
        return VisualStyle.redDim
    }
}

private func accountTitle(_ account: AccountSummary) -> String {
    if let alias = account.alias, !alias.isEmpty {
        return alias
    }
    return account.displayName
}

private func accountSubtitle(_ account: AccountSummary) -> String {
    if accountTitle(account).localizedCaseInsensitiveCompare(account.email) != .orderedSame {
        return "\(account.planType.capitalized) | \(account.email)"
    }
    let accountId = account.accountId.count > 12 ? String(account.accountId.prefix(12)) + "..." : account.accountId
    return "\(account.planType.capitalized) | \(accountId)"
}

private func accountSort(_ lhs: AccountSummary, _ rhs: AccountSummary) -> Bool {
    if lhs.status == "active", rhs.status != "active" {
        return true
    }
    if lhs.status != "active", rhs.status == "active" {
        return false
    }
    return accountTitle(lhs).localizedCaseInsensitiveCompare(accountTitle(rhs)) == .orderedAscending
}

private func quotaViewsForAccount(_ account: AccountSummary) -> [NSView] {
    var rows: [NSView] = []
    if account.windowMinutesPrimary != nil || account.usage?.primaryRemainingPercent != nil {
        rows.append(QuotaMiniView(label: "5h", remainingPercent: account.usage?.primaryRemainingPercent, resetAt: account.resetAtPrimary))
    }
    if account.windowMinutesSecondary != nil || account.usage?.secondaryRemainingPercent != nil {
        rows.append(QuotaMiniView(label: "Weekly", remainingPercent: account.usage?.secondaryRemainingPercent, resetAt: account.resetAtSecondary))
    }
    if account.windowMinutesMonthly != nil || account.usage?.monthlyRemainingPercent != nil {
        rows.append(QuotaMiniView(label: "Monthly", remainingPercent: account.usage?.monthlyRemainingPercent, resetAt: account.resetAtMonthly))
    }
    return rows
}

private func warmupStatus(_ account: AccountSummary) -> String {
    if account.limitWarmupEnabled == true {
        return "Warm-up on"
    }
    return "Warm-up off"
}

private func warmupAttempt(_ account: AccountSummary) -> String {
    guard let warmup = account.limitWarmup else {
        return "No attempts"
    }
    if let completedAt = warmup.completedAt {
        return "\(warmup.status.capitalized) \(relativeTime(completedAt)) ago"
    }
    return "\(warmup.status.capitalized) \(relativeTime(warmup.attemptedAt)) ago"
}

@MainActor
private final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let settings = SettingsStore()
    private lazy var client = CodexLBClient(settings: settings)
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var overview: DashboardOverview?
    private var authSession: AuthSession?
    private var latestError: String?
    private var isRefreshing = false
    private var isMenuOpen = false
    private var lastRefreshedAt: Date?
    private var refreshTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem.button?.title = "..."
        rebuildMenu()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refresh()
            }
        }
        Task {
            await refresh()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
    }

    func menuWillOpen(_ menu: NSMenu) {
        isMenuOpen = true
    }

    func menuDidClose(_ menu: NSMenu) {
        isMenuOpen = false
    }

    private func refresh(menuWasOpen: Bool? = nil) async {
        guard !isRefreshing else {
            return
        }
        let wasMenuOpen = menuWasOpen ?? isMenuOpen
        isRefreshing = true
        statusItem.button?.title = "..."
        rebuildMenu(updateVisiblePanel: wasMenuOpen)
        defer {
            isRefreshing = false
            rebuildMenu(updateVisiblePanel: isMenuOpen)
        }

        do {
            authSession = try await client.getSession()
            overview = try await client.fetchOverview()
            lastRefreshedAt = Date()
            latestError = nil
            updateStatusTitle()
        } catch ClientError.unauthorized {
            overview = nil
            latestError = "Dashboard login required"
            statusItem.button?.title = "Login"
        } catch {
            overview = nil
            latestError = error.localizedDescription
            statusItem.button?.title = "Error"
        }
    }

    private func updateStatusTitle() {
        guard let overview else {
            statusItem.button?.title = latestError == nil ? "Status" : "Error"
            return
        }

        let activeAccounts = overview.accounts.filter { $0.status == "active" }
        statusItem.button?.title = quotaSummaryTitle(
            primary: averageRemaining(activeAccounts.map { $0.usage?.primaryRemainingPercent }),
            secondary: averageRemaining(activeAccounts.map { $0.usage?.secondaryRemainingPercent }),
            monthly: averageRemaining(activeAccounts.map { $0.usage?.monthlyRemainingPercent }),
            activeCount: activeAccounts.count,
            totalCount: overview.accounts.count
        )
    }

    private func rebuildMenu(updateVisiblePanel: Bool = false) {
        let previousMenu = statusItem.menu
        let menu = NSMenu()
        menu.delegate = self

        if let overview {
            if !overview.accounts.isEmpty {
                menu.addItem(viewItem(AccountsPanelView(
                    accounts: overview.accounts,
                    isRefreshing: isRefreshing,
                    lastRefreshedAt: lastRefreshedAt,
                    refreshTarget: self,
                    refreshAction: #selector(refreshNow)
                )))
            } else {
                menu.addItem(viewItem(StatusMessageView(
                    title: appDisplayName,
                    message: "No accounts imported yet.",
                    detail: "Server: \(settings.baseURLString)"
                )))
            }
        } else if let latestError {
            menu.addItem(viewItem(StatusMessageView(
                title: latestError == "Dashboard login required" ? "Login required" : "Codex LB Connection Error",
                message: latestError,
                detail: "Server: \(settings.baseURLString)"
            )))
        } else {
            menu.addItem(viewItem(StatusMessageView(
                title: appDisplayName,
                message: isRefreshing ? "Refreshing usage data..." : "No data loaded yet.",
                detail: "Server: \(settings.baseURLString)"
            )))
        }

        menu.addItem(.separator())
        menu.addItem(actionItem("Open Dashboard", #selector(openDashboard)))
        menu.addItem(actionItem("Set Server URL...", #selector(setServerURL)))
        menu.addItem(launchAtLoginItem())
        menu.addItem(actionItem("Admin Login...", #selector(loginAdmin)))
        menu.addItem(actionItem("Guest Login...", #selector(loginGuest)))
        menu.addItem(.separator())
        menu.addItem(actionItem("Quit", #selector(quit)))
        if updateVisiblePanel,
           let visibleItem = previousMenu?.items.first,
           let container = visibleItem.view as? MenuContentContainerView,
           let updatedView = menu.items.first?.view as? MenuContentContainerView,
           let updatedContent = updatedView.subviews.first {
            container.replaceContent(with: updatedContent)
            previousMenu?.update()
        } else {
            statusItem.menu = menu
        }
    }

    private func viewItem(_ view: NSView) -> NSMenuItem {
        let item = NSMenuItem()
        item.view = MenuContentContainerView(content: view)
        return item
    }

    @objc private func refreshNow() {
        let menuWasOpen = isMenuOpen
        Task {
            await refresh(menuWasOpen: menuWasOpen)
        }
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(settings.baseURL)
    }

    @objc private func toggleLaunchAtLogin() {
        let service = SMAppService.mainApp
        do {
            switch service.status {
            case .enabled:
                try service.unregister()
            case .requiresApproval:
                SMAppService.openSystemSettingsLoginItems()
            case .notRegistered, .notFound:
                try service.register()
            @unknown default:
                try service.register()
            }
            rebuildMenu()
        } catch {
            showError("Could not update Launch at Login: \(error.localizedDescription)")
        }
    }

    @objc private func setServerURL() {
        guard let value = promptText(
            title: "Set codex-lb server",
            message: "Enter the codex-lb dashboard base URL.",
            defaultValue: settings.baseURLString,
            secure: false
        ) else {
            return
        }
        settings.baseURLString = value
        client = CodexLBClient(settings: settings)
        Task {
            await refresh()
        }
    }

    @objc private func loginAdmin() {
        guard let password = promptText(
            title: "Admin login",
            message: "Enter the codex-lb dashboard password.",
            defaultValue: "",
            secure: true
        ) else {
            return
        }
        Task {
            do {
                let session = try await client.loginPassword(password)
                if session.totpRequiredOnLogin {
                    guard let code = promptText(
                        title: "TOTP required",
                        message: "Enter the dashboard TOTP code.",
                        defaultValue: "",
                        secure: false
                    ) else {
                        return
                    }
                    _ = try await client.verifyTotp(code)
                }
                await refresh()
            } catch {
                showError(error.localizedDescription)
            }
        }
    }

    @objc private func loginGuest() {
        guard let password = promptText(
            title: "Guest login",
            message: "Enter guest password, or leave blank for passwordless guest access.",
            defaultValue: "",
            secure: true
        ) else {
            return
        }
        Task {
            do {
                _ = try await client.loginGuest(password: password.isEmpty ? nil : password)
                await refresh()
            } catch {
                showError(error.localizedDescription)
            }
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func promptText(title: String, message: String, defaultValue: String, secure: Bool) -> String? {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")

        let field: NSTextField = secure
            ? NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
            : NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.stringValue = defaultValue
        alert.accessoryView = field

        let response = alert.runModal()
        guard response == .alertFirstButtonReturn else {
            return nil
        }
        return field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func showError(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Codex LB Status"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func actionItem(_ title: String, _ selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        return item
    }

    private func launchAtLoginItem() -> NSMenuItem {
        let item = actionItem("Launch at Login", #selector(toggleLaunchAtLogin))
        switch SMAppService.mainApp.status {
        case .enabled:
            item.state = .on
        case .requiresApproval:
            item.state = .mixed
            item.toolTip = "Approval required in System Settings"
        case .notRegistered, .notFound:
            item.state = .off
        @unknown default:
            item.state = .off
        }
        return item
    }

}

private func formatPercent(_ value: Double) -> String {
    "\(Int(round(value)))%"
}

private func formatOptionalPercent(_ value: Double?) -> String {
    guard let value else {
        return "--"
    }
    return formatPercent(value)
}

private func relativeTime(_ date: Date) -> String {
    let seconds = Int(date.timeIntervalSinceNow)
    if seconds <= 0 {
        return "now"
    }
    let days = seconds / 86_400
    let hours = (seconds % 86_400) / 3_600
    let minutes = (seconds % 3_600) / 60
    if days > 0 {
        return "\(days)d \(hours)h"
    }
    if hours > 0 {
        return "\(hours)h \(minutes)m"
    }
    return "\(max(1, minutes))m"
}

@main
private enum CodexLBStatusBarMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
