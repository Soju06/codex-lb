(() => {
	"use strict";
	const API_ENDPOINTS = {
		accounts: "/api/accounts",
		accountsImport: "/api/accounts/import",
		accountReactivate: (accountId) =>
			`/api/accounts/${encodeURIComponent(accountId)}/reactivate`,
		accountPause: (accountId) =>
			`/api/accounts/${encodeURIComponent(accountId)}/pause`,
		accountDelete: (accountId) =>
			`/api/accounts/${encodeURIComponent(accountId)}`,
		usageSummary: "/api/usage/summary",
		usageWindow: (window) =>
			`/api/usage/window?window=${encodeURIComponent(window)}`,
		requestLogs: (limit) => `/api/request-logs?limit=${limit}`,
		oauthStart: "/api/oauth/start",
		oauthStatus: "/api/oauth/status",
		oauthComplete: "/api/oauth/complete",
		settings: "/api/settings",
	};

	const PAGES = [
		{
			id: "dashboard",
			tabId: "tab-dashboard",
			label: "Dashboard",
			title: "Codex Load Balancer - Dashboard",
			path: "/dashboard",
		},
		{
			id: "accounts",
			tabId: "tab-accounts",
			label: "Accounts",
			title: "Codex Load Balancer - Accounts",
			path: "/accounts",
		},
		{
			id: "settings",
			tabId: "tab-settings",
			label: "Settings",
			title: "Codex Load Balancer - Settings",
			path: "/settings",
		},
	];

	const BASE_PATH = "/dashboard";
	const PATH_TO_VIEW = PAGES.reduce((acc, page) => {
		acc[page.path] = page.id;
		return acc;
	}, {});
	PATH_TO_VIEW[BASE_PATH] = "dashboard";

	const getViewFromPath = (pathname) => {
		const normalized = pathname.replace(/\/+$/, "") || BASE_PATH;
		return PATH_TO_VIEW[normalized] || "dashboard";
	};

	const getPathFromView = (viewId) => {
		const page = PAGES.find((p) => p.id === viewId);
		return page?.path || BASE_PATH;
	};

	const STATUS_LABELS = {
		active: "Active",
		paused: "Paused",
		limited: "Rate limited",
		exceeded: "Quota exceeded",
		deactivated: "Deactivated",
	};

	const REQUEST_STATUS_LABELS = {
		ok: "OK",
		rate_limit: "Rate limit",
		quota: "Quota",
		error: "Error",
	};

	const REQUEST_STATUS_CLASSES = {
		ok: "active",
		rate_limit: "limited",
		quota: "exceeded",
		error: "error",
	};

	const KNOWN_PLAN_TYPES = new Set([
		"free",
		"plus",
		"pro",
		"team",
		"business",
		"enterprise",
		"edu",
	]);

	const ROUTING_LABELS = {
		usage_weighted: "usage weighted",
		round_robin: "round robin",
		sticky: "sticky",
	};

	const ERROR_LABELS = {
		rate_limit: "rate limit",
		quota: "quota",
		timeout: "timeout",
		upstream: "upstream",
		rate_limit_exceeded: "rate limit",
		usage_limit_reached: "quota",
		insufficient_quota: "quota",
		usage_not_included: "quota",
		quota_exceeded: "quota",
		upstream_error: "upstream",
	};

	const PROGRESS_CLASS_BY_STATUS = {
		paused: "paused",
		limited: "paused",
		exceeded: "error",
	};

	const ACCOUNT_STATUS_MAP = {
		paused: "paused",
		rate_limited: "limited",
		quota_exceeded: "exceeded",
	};

	const MESSAGE_TONE_META = {
		success: {
			label: "Success",
			className: "active",
			defaultTitle: "Import complete",
		},
		error: {
			label: "Error",
			className: "deactivated",
			defaultTitle: "Import failed",
		},
		warning: {
			label: "Warning",
			className: "limited",
			defaultTitle: "Attention",
		},
		info: {
			label: "Info",
			className: "limited",
			defaultTitle: "Message",
		},
		question: {
			label: "Question",
			className: "limited",
			defaultTitle: "Confirm",
		},
	};

	const AUTH_STATUS_LABELS = {
		pendingBrowser: "Waiting for browser sign-in...",
		pendingDevice: "Waiting for device verification...",
		success: "Account linked. Return to the dashboard.",
		error: "Authorization failed.",
	};

	const DONUT_COLORS = [
		"#7bb661",
		"#d9a441",
		"#4b6ea8",
		"#c35d5d",
		"#8d6bd6",
		"#4aa0a8",
	];
	const CONSUMED_COLOR = "#d3d3d3";
	const RESET_ERROR_LABEL = "--";

	const numberFormatter = new Intl.NumberFormat("en-US");
	const compactFormatter = new Intl.NumberFormat("en-US", {
		notation: "compact",
		maximumFractionDigits: 2,
	});
	const currencyFormatter = new Intl.NumberFormat("en-US", {
		style: "currency",
		currency: "USD",
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	});
	const timeLongFormatter = new Intl.DateTimeFormat("en-US", {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	});

	const createEmptyDashboardData = () => ({
		lastSyncAt: "",
		routing: {
			strategy: "usage_weighted",
			rotationEnabled: true,
		},
		metrics: {
			requests7d: 0,
			tokensSecondaryWindow: null,
			cachedTokensSecondaryWindow: null,
			cost7d: 0,
			errorRate7d: null,
			topError: "",
		},
		usage: {
			primary: {
				remaining: 0,
				capacity: 0,
				resetAt: null,
				windowMinutes: null,
				byAccount: [],
			},
			secondary: {
				remaining: 0,
				capacity: 0,
				resetAt: null,
				windowMinutes: null,
				byAccount: [],
			},
		},
		recentRequests: [],
	});

	const createEmptyDashboardView = () => ({
		badges: [],
		stats: [],
		donuts: [],
		accountCards: [],
		requests: [],
	});

	const createUiConfig = () => ({
		usageWindows: buildUsageWindowConfig(null),
	});

	const createPages = () => PAGES.map((page) => ({ ...page }));

	const toNumber = (value) => (Number.isFinite(value) ? value : null);

	const formatNumber = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return "--";
		}
		return numberFormatter.format(numeric);
	};

	const formatCompactNumber = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return "--";
		}
		return compactFormatter.format(numeric);
	};

	const formatTokensWithCached = (totalTokens, cachedInputTokens) => {
		const total = toNumber(totalTokens);
		if (total === null) {
			return "--";
		}
		const cached = toNumber(cachedInputTokens);
		if (cached === null || cached <= 0) {
			return formatCompactNumber(total);
		}
		return `${formatCompactNumber(total)} (${formatCompactNumber(cached)} Cached)`;
	};

	const formatCachedTokensMeta = (totalTokens, cachedInputTokens) => {
		const total = toNumber(totalTokens);
		const cached = toNumber(cachedInputTokens);
		if (total === null || total <= 0 || cached === null || cached <= 0) {
			return "Cached: --";
		}
		const percent = Math.min(100, Math.max(0, (cached / total) * 100));
		return `Cached: ${formatCompactNumber(cached)} (${Math.round(percent)}%)`;
	};

	const formatModelLabel = (model, reasoningEffort) => {
		const base = (model || "").trim();
		if (!base) {
			return "--";
		}
		const effort = (reasoningEffort || "").trim();
		if (!effort) {
			return base;
		}
		return `${base} (${effort})`;
	};

	const formatCurrency = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return "--";
		}
		return currencyFormatter.format(numeric);
	};

	const formatPercent = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return "0%";
		}
		return `${Math.round(numeric)}%`;
	};

	const formatWindowMinutes = (value) => {
		const minutes = toNumber(value);
		if (minutes === null || minutes <= 0) {
			return "--";
		}
		if (minutes % 1440 === 0) {
			return `${minutes / 1440}d`;
		}
		if (minutes % 60 === 0) {
			return `${minutes / 60}h`;
		}
		return `${minutes}m`;
	};

	const formatWindowLabel = (key, minutes) => {
		const formatted = formatWindowMinutes(minutes);
		if (formatted !== "--") {
			return formatted;
		}
		if (key === "secondary") {
			return "7d";
		}
		if (key === "primary") {
			return "5h";
		}
		return "--";
	};

	const formatPercentValue = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return 0;
		}
		return Math.round(numeric);
	};

	const formatRate = (value) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return "--";
		}
		return `${(numeric * 100).toFixed(1)}%`;
	};

	const truncateText = (value, maxLen = 80) => {
		if (!value) {
			return "";
		}
		const text = String(value);
		if (text.length <= maxLen) {
			return text;
		}
		if (maxLen <= 3) {
			return text.slice(0, maxLen);
		}
		return `${text.slice(0, maxLen - 3)}...`;
	};

	const parseDate = (iso) => {
		if (!iso) {
			return null;
		}
		const date = new Date(iso);
		if (Number.isNaN(date.getTime())) {
			return null;
		}
		return date;
	};

	const formatTimeLong = (iso) => {
		const date = parseDate(iso);
		if (!date) {
			return "--";
		}
		return timeLongFormatter.format(date);
	};

	const formatRelative = (ms) => {
		const minutes = Math.ceil(ms / 60000);
		if (minutes < 60) {
			return `in ${minutes}m`;
		}
		const hours = Math.ceil(minutes / 60);
		if (hours < 24) {
			return `in ${hours}h`;
		}
		const days = Math.ceil(hours / 24);
		return `in ${days}d`;
	};

	const formatCountdown = (seconds) => {
		const clamped = Math.max(0, Math.floor(seconds || 0));
		const minutes = Math.floor(clamped / 60);
		const remainder = clamped % 60;
		return `${minutes}:${String(remainder).padStart(2, "0")}`;
	};

	const formatQuotaResetLabel = (resetAt) => {
		const date = parseDate(resetAt);
		if (!date || date.getTime() <= 0) {
			return RESET_ERROR_LABEL;
		}
		const diffMs = date.getTime() - Date.now();
		if (diffMs <= 0) {
			return "now";
		}
		return formatRelative(diffMs);
	};

	const formatQuotaResetMeta = (resetAtSecondary, windowMinutesSecondary) => {
		const labelSecondary = formatQuotaResetLabel(resetAtSecondary);
		const windowSecondary = formatWindowLabel(
			"secondary",
			windowMinutesSecondary,
		);
		const secondaryOk = labelSecondary !== RESET_ERROR_LABEL;
		if (!secondaryOk) {
			return "Quota reset unavailable";
		}
		return `Quota reset (${windowSecondary}) · ${labelSecondary}`;
	};

	const buildUsageWindowTitle = (key, minutes) =>
		`Remaining quota by account (${formatWindowLabel(key, minutes)})`;

	const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

	const adjustHexColor = (hex, amount) => {
		if (typeof hex !== "string" || !hex.startsWith("#") || hex.length !== 7) {
			return hex;
		}
		const raw = hex.slice(1);
		const intValue = Number.parseInt(raw, 16);
		if (!Number.isFinite(intValue)) {
			return hex;
		}
		const r = (intValue >> 16) & 255;
		const g = (intValue >> 8) & 255;
		const b = intValue & 255;
		const mix = amount >= 0 ? 255 : 0;
		const factor = clamp(Math.abs(amount), 0, 1);
		const next = (channel) => Math.round(channel + (mix - channel) * factor);
		const toHex = (channel) =>
			clamp(channel, 0, 255).toString(16).padStart(2, "0");
		return `#${toHex(next(r))}${toHex(next(g))}${toHex(next(b))}`;
	};

	const buildDonutPalette = (count) => {
		const base = DONUT_COLORS.slice();
		if (count <= base.length) {
			return base.slice(0, count);
		}
		const palette = base.slice();
		const shifts = [0.2, -0.18, 0.32, -0.28];
		let index = 0;
		while (palette.length < count) {
			const baseColor = base[index % base.length];
			const shift = shifts[index % shifts.length];
			palette.push(adjustHexColor(baseColor, shift));
			index += 1;
		}
		return palette;
	};

	const buildUsageWindowConfig = (summary) => {
		const primaryMinutes = toNumber(summary?.primaryWindow?.windowMinutes);
		const secondaryMinutes = toNumber(summary?.secondaryWindow?.windowMinutes);
		return [
			{
				key: "primary",
				title: buildUsageWindowTitle("primary", primaryMinutes),
				range: formatWindowLabel("primary", primaryMinutes),
				windowMinutes: primaryMinutes ?? null,
			},
			{
				key: "secondary",
				title: buildUsageWindowTitle("secondary", secondaryMinutes),
				range: formatWindowLabel("secondary", secondaryMinutes),
				windowMinutes: secondaryMinutes ?? null,
			},
		];
	};

	const statusBadgeText = (status) =>
		STATUS_LABELS[status] || status || "unknown";
	const statusLabel = (status) => STATUS_LABELS[status] || "Unknown";
	const requestStatusLabel = (status) =>
		REQUEST_STATUS_LABELS[status] || "Unknown";
	const requestStatusClass = (status) =>
		REQUEST_STATUS_CLASSES[status] || "deactivated";
	const normalizePlanType = (plan) => {
		if (plan === null || plan === undefined) {
			return null;
		}
		const value = String(plan).trim().toLowerCase();
		return KNOWN_PLAN_TYPES.has(value) ? value : null;
	};
	const titleCase = (value) =>
		value ? value.charAt(0).toUpperCase() + value.slice(1).toLowerCase() : "";
	const planLabel = (plan) => {
		const normalized = normalizePlanType(plan);
		return normalized ? titleCase(normalized) : "Unknown";
	};
	const routingLabel = (strategy) => ROUTING_LABELS[strategy] || "unknown";
	const errorLabel = (code) => ERROR_LABELS[code] || "--";
	const calculateProgressClass = (status, remainingPercent) => {
		if (status === "exceeded") return "error";
		if (status === "paused" || status === "deactivated") return "";
		const percent = toNumber(remainingPercent) || 0;
		if (percent <= 20) return "error";
		if (percent <= 50) return "limited";
		return "success";
	};
	const calculateProgressTextClass = (status, remainingPercent) => {
		const cls = calculateProgressClass(status, remainingPercent);
		return cls ? `text-${cls}` : "";
	};

	const calculateTextUsageClass = (status, remainingPercent) => {
		if (status === "exceeded") return "error";
		// For text, we show usage color even if paused. Only deactivated is plain.
		if (status === "deactivated") return "";
		const percent = toNumber(remainingPercent) || 0;
		if (percent <= 20) return "error";
		if (percent <= 50) return "limited";
		return "success";
	};

	const calculateTextUsageTextClass = (status, remainingPercent) => {
		const cls = calculateTextUsageClass(status, remainingPercent);
		return cls ? `text-${cls}` : "";
	};
	const progressClass = (status) => PROGRESS_CLASS_BY_STATUS[status] || "";

	const normalizeSearchInput = (value) =>
		String(value ?? "")
			.trim()
			.toLowerCase();

	const buildSearchTokens = (value) => {
		const normalized = normalizeSearchInput(value);
		if (!normalized) {
			return [];
		}
		return normalized.split(/\s+/).filter(Boolean);
	};

	const buildAccountSearchHaystack = (account) => {
		if (!account || typeof account !== "object") {
			return "";
		}
		const parts = [
			account.id,
			account.email,
			account.displayName,
			account.plan,
			planLabel(account.plan),
			account.status,
			statusLabel(account.status),
		]
			.filter(Boolean)
			.map((part) => String(part).trim())
			.filter(Boolean);
		return normalizeSearchInput(parts.join(" "));
	};

	const filterAccountsByQuery = (accounts, query) => {
		const list = Array.isArray(accounts) ? accounts : [];
		const tokens = buildSearchTokens(query);
		if (!tokens.length) {
			return list;
		}
		return list.filter((account) => {
			const haystack = buildAccountSearchHaystack(account);
			return tokens.every((token) => haystack.includes(token));
		});
	};

	const normalizeAccountStatus = (status) =>
		ACCOUNT_STATUS_MAP[status] || status || "deactivated";

	const normalizeAccount = (account) => {
		const usage = account.usage || {};
		const email = account.displayName || account.email || account.accountId;
		return {
			id: account.accountId,
			email,
			plan: account.planType,
			status: normalizeAccountStatus(account.status),
			usage: {
				primaryRemainingPercent: toNumber(usage.primaryRemainingPercent) ?? 0,
				secondaryRemainingPercent:
					toNumber(usage.secondaryRemainingPercent) ?? 0,
			},
			resetAtPrimary: account.resetAtPrimary ?? null,
			resetAtSecondary: account.resetAtSecondary ?? null,
			auth: account.auth ?? {},
			displayName: email,
		};
	};

	const formatAccountLabel = (accountId, accounts) => {
		if (!accountId) {
			return "";
		}
		const account = accounts?.find((item) => item.id === accountId);
		return account?.displayName || account?.email || accountId;
	};

	const normalizeAccountsPayload = (payload) => {
		return payload.accounts.map(normalizeAccount);
	};

	const normalizeUsageEntry = (entry) => {
		return {
			accountId: entry.accountId,
			requestCount: entry.requestCount ?? 0,
			remainingPercentAvg: toNumber(entry.remainingPercentAvg),
			capacityCredits: toNumber(entry.capacityCredits) ?? 0,
			remainingCredits: toNumber(entry.remainingCredits) ?? 0,
			costUsd: toNumber(entry.costUsd) ?? 0,
		};
	};

	const normalizeUsagePayload = (payload) => {
		return payload.accounts.map(normalizeUsageEntry);
	};

	const normalizeRequestLog = (entry) => {
		return {
			timestamp: entry.requestedAt,
			accountId: entry.accountId,
			requestId: entry.requestId,
			model: entry.model,
			reasoningEffort: entry.reasoningEffort ?? null,
			status: entry.status,
			tokens: toNumber(entry.tokens),
			cachedInputTokens: toNumber(entry.cachedInputTokens),
			cost: toNumber(entry.costUsd),
			errorCode: entry.errorCode ?? null,
			errorMessage: entry.errorMessage ?? null,
		};
	};

	const normalizeRequestLogsPayload = (payload) => {
		return payload.requests.map(normalizeRequestLog);
	};

	const buildUsageIndex = (entries) =>
		entries.reduce((acc, entry) => {
			if (entry.accountId) {
				acc[entry.accountId] = entry;
			}
			return acc;
		}, {});

	const mergeUsageIntoAccounts = (accounts, primaryUsage, secondaryUsage) => {
		const primaryMap = buildUsageIndex(primaryUsage || []);
		const secondaryMap = buildUsageIndex(secondaryUsage || []);
		return accounts.map((account) => {
			const primaryRow = primaryMap[account.id];
			const secondaryRow = secondaryMap[account.id];
			const primaryRemainingPercent = toNumber(primaryRow?.remainingPercentAvg);
			const secondaryRemainingPercent = toNumber(
				secondaryRow?.remainingPercentAvg,
			);
			const mergedSecondaryRemaining =
				secondaryRemainingPercent ??
				account.usage?.secondaryRemainingPercent ??
				0;
			const mergedPrimaryRemaining =
				primaryRemainingPercent ??
				account.usage?.primaryRemainingPercent ??
				0;
			const effectivePrimaryRemaining =
				mergedSecondaryRemaining <= 0 ? 0 : mergedPrimaryRemaining;
			return {
				...account,
				usage: {
					primaryRemainingPercent: effectivePrimaryRemaining,
					secondaryRemainingPercent: mergedSecondaryRemaining,
				},
				resetAtPrimary: account.resetAtPrimary ?? null,
				resetAtSecondary: account.resetAtSecondary ?? null,
			};
		});
	};

	const buildUsageWindow = (entries, summaryWindow) => {
		const entryList = entries || [];
		const capacityFromEntries = entryList.reduce(
			(acc, entry) => acc + (toNumber(entry.capacityCredits) || 0),
			0,
		);
		const remainingFromEntries = entryList.reduce(
			(acc, entry) => acc + (toNumber(entry.remainingCredits) || 0),
			0,
		);
		const capacity = Math.max(
			toNumber(summaryWindow?.capacityCredits) || 0,
			capacityFromEntries,
			remainingFromEntries,
		);
		const remaining = Math.max(
			toNumber(summaryWindow?.remainingCredits) || 0,
			remainingFromEntries,
		);
		return {
			capacity,
			remaining,
			resetAt: summaryWindow?.resetAt ?? null,
			windowMinutes: summaryWindow?.windowMinutes ?? null,
			byAccount: entryList.map((entry) => ({
				accountId: entry.accountId,
				capacityCredits: toNumber(entry.capacityCredits) || 0,
				remainingCredits: toNumber(entry.remainingCredits) || 0,
				remainingPercentAvg: toNumber(entry.remainingPercentAvg),
			})),
		};
	};

	const buildDashboardDataFromApi = ({
		summary,
		primaryUsage,
		secondaryUsage,
		requestLogs,
	}) => {
		const metrics = summary?.metrics || {};
		const requests7d = toNumber(metrics.requests7d) ?? 0;
		const tokensSecondaryWindow = toNumber(metrics.tokensSecondaryWindow);
		const cachedTokensSecondaryWindow = toNumber(metrics.cachedTokensSecondaryWindow);
		const errorRate7d = toNumber(metrics.errorRate7d);
		const topError = metrics.topError || "";
		return {
			lastSyncAt: new Date().toISOString(),
			routing: {
				strategy: "usage_weighted",
				rotationEnabled: true,
			},
			metrics: {
				requests7d,
				tokensSecondaryWindow,
				cachedTokensSecondaryWindow,
				cost7d: toNumber(summary?.cost?.totalUsd7d) || 0,
				errorRate7d,
				topError,
			},
			usage: {
				primary: buildUsageWindow(primaryUsage || [], summary?.primaryWindow),
				secondary: buildUsageWindow(
					secondaryUsage || [],
					summary?.secondaryWindow,
				),
			},
			recentRequests: requestLogs,
		};
	};

	const avgPerHour = (value, windowMinutes) => {
		const numeric = toNumber(value);
		if (numeric === null) {
			return 0;
		}
		const hours =
			typeof windowMinutes === "number" && windowMinutes > 0
				? windowMinutes / 60
				: 24 * 7;
		return Math.round(numeric / hours);
	};

	const countByStatus = (accounts) =>
		accounts.reduce((acc, account) => {
			const status = account.status || "unknown";
			acc[status] = (acc[status] || 0) + 1;
			return acc;
		}, {});

	const buildRemainingItems = (entries, accounts, capacity, windowKey) => {
		const accountMap = new Map(
			(accounts || []).map((account) => [account.id, account]),
		);
		const items = (entries || []).map((entry) => {
			const account = accountMap.get(entry.accountId);
			const label = account ? account.email : entry.accountId;
			const value = toNumber(entry.remainingCredits) || 0;
			const percentFromApi = toNumber(entry.remainingPercentAvg);
			const percentFromAccount =
				windowKey === "primary"
					? toNumber(account?.usage?.primaryRemainingPercent)
					: windowKey === "secondary"
						? toNumber(account?.usage?.secondaryRemainingPercent)
						: null;
			const entryCapacity = toNumber(entry.capacityCredits) || 0;
			const denominator = entryCapacity > 0 ? entryCapacity : capacity;
			const rawPercent =
				percentFromApi !== null
					? percentFromApi
					: percentFromAccount !== null
						? percentFromAccount
						: denominator > 0
							? (value / denominator) * 100
							: 0;
			const remainingPercent = Math.min(100, Math.max(0, rawPercent));
			return {
				accountId: entry.accountId,
				label,
				value,
				remainingPercent,
			};
		});
		items.sort((a, b) => {
			const labelA = String(a.label || "").toLowerCase();
			const labelB = String(b.label || "").toLowerCase();
			if (labelA < labelB) {
				return -1;
			}
			if (labelA > labelB) {
				return 1;
			}
			return String(a.accountId || "").localeCompare(String(b.accountId || ""));
		});
		const palette = buildDonutPalette(items.length);
		return items.map((item, index) => ({
			...item,
			color: palette[index % palette.length],
		}));
	};

	const buildSecondaryExhaustedIndex = (accounts) => {
		const exhausted = new Set();
		(accounts || []).forEach((account) => {
			const remaining = toNumber(account?.usage?.secondaryRemainingPercent);
			if (remaining !== null && remaining <= 0 && account?.id) {
				exhausted.add(account.id);
			}
		});
		return exhausted;
	};

	const applySecondaryExhaustedToPrimary = (entries, exhaustedIds) => {
		if (!entries?.length || !exhaustedIds?.size) {
			return entries || [];
		}
		return entries.map((entry) => {
			if (entry?.accountId && exhaustedIds.has(entry.accountId)) {
				return {
					...entry,
					remainingCredits: 0,
				};
			}
			return entry;
		});
	};

	const sumRemainingCredits = (entries) =>
		(entries || []).reduce(
			(acc, entry) => acc + (toNumber(entry?.remainingCredits) || 0),
			0,
		);

	const buildDonutGradient = (items, total) => {
		if (!items.length || total <= 0) {
			return `conic-gradient(${CONSUMED_COLOR} 0 100%)`;
		}
		const values = items.map((item) => Math.max(0, item.value || 0));
		const remainingTotal = values.reduce((acc, value) => acc + value, 0);
		if (remainingTotal <= 0) {
			return `conic-gradient(${CONSUMED_COLOR} 0 100%)`;
		}
		const minPositive = Math.min(...values.filter((value) => value > 0));
		const fallback =
			Number.isFinite(minPositive) && minPositive > 0 ? minPositive * 0.05 : 0;
		const displayValues =
			fallback > 0
				? values.map((value) => (value > 0 ? value : fallback))
				: values;
		const displayTotal = displayValues.reduce((acc, value) => acc + value, 0);
		const remainingPercentTotal = Math.min(
			100,
			((displayTotal > 0 ? remainingTotal : 0) / total) * 100,
		);
		let start = 0;
		const segments = displayValues.map((value, index) => {
			const percent =
				displayTotal > 0 ? (value / displayTotal) * remainingPercentTotal : 0;
			const end = start + percent;
			const color = items[index]?.color || CONSUMED_COLOR;
			const segment = `${color} ${start}% ${end}%`;
			start = end;
			return segment;
		});
		if (remainingPercentTotal < 100) {
			segments.push(`${CONSUMED_COLOR} ${start}% 100%`);
		}
		return `conic-gradient(${segments.join(", ")})`;
	};

	const buildDashboardView = (state) => {
		const accounts = state.accounts.rows;
		const statusCounts = countByStatus(accounts);
		const secondaryWindowMinutes =
			state.dashboardData.usage?.secondary?.windowMinutes ?? null;
		const secondaryExhaustedAccounts = buildSecondaryExhaustedIndex(accounts);

		const badges = ["active", "paused", "limited", "exceeded", "deactivated"]
			.map((status) => {
				const count = statusCounts[status] || 0;
				if (!count) {
					return null;
				}
				return {
					status,
					label: `${count} ${statusBadgeText(status)}`,
				};
			})
			.filter(Boolean);

		const metrics = state.dashboardData.metrics;
		const stats = [
			{
				title: `Tokens (${formatWindowLabel("secondary", secondaryWindowMinutes)})`,
				value: formatCompactNumber(metrics.tokensSecondaryWindow),
				meta: formatCachedTokensMeta(
					metrics.tokensSecondaryWindow,
					metrics.cachedTokensSecondaryWindow,
				),
			},
			{
				title: "Cost (7d)",
				value: formatCurrency(metrics.cost7d),
				meta: `Avg per hour: ${formatCurrency(
					avgPerHour(metrics.cost7d, secondaryWindowMinutes),
				)}`,
			},
			{
				title: "Active accounts",
				value: `${statusCounts.active || 0} / ${accounts.length}`,
				meta: `Rotation: ${routingLabel(state.dashboardData.routing?.strategy)}`,
			},
			{
				title: "Error rate (7d)",
				value: formatRate(metrics.errorRate7d),
				meta: `Top error: ${errorLabel(metrics.topError)}`,
			},
		];

		const donuts = state.ui.usageWindows.map((window) => {
			const usage = state.dashboardData.usage?.[window.key] || {
				capacity: 0,
				remaining: 0,
				resetAt: null,
				byAccount: [],
			};
			const rawEntries = usage.byAccount || [];
			const hasPrimaryAdjustments =
				window.key === "primary" &&
				rawEntries.some(
					(entry) =>
						entry?.accountId && secondaryExhaustedAccounts.has(entry.accountId),
				);
			const entries =
				window.key === "primary"
					? applySecondaryExhaustedToPrimary(
						rawEntries,
						secondaryExhaustedAccounts,
					)
					: rawEntries;
			const remaining =
				hasPrimaryAdjustments
					? sumRemainingCredits(entries)
					: toNumber(usage.remaining) || 0;
			const capacity = Math.max(remaining, toNumber(usage.capacity) || 0);
			const consumed = Math.max(0, capacity - remaining);
			const items = buildRemainingItems(
				entries,
				accounts,
				capacity,
				window.key,
			);
			const gradient = buildDonutGradient(items, capacity);
			const legendItems = items.map((item) => {
				const percent = item.remainingPercent;
				let valueClass = "success";
				if (percent <= 20) {
					valueClass = "error";
				} else if (percent <= 50) {
					valueClass = "limited";
				}
				return {
					label: truncateText(item.label, 28),
					fullLabel: item.label,
					detailLabel: "Remaining",
					detailValue: formatPercent(item.remainingPercent),
					valueClass,
					color: item.color,
				};
			});
			if (capacity > 0) {
				const consumedPercent = Math.min(
					100,
					Math.max(0, (consumed / capacity) * 100),
				);
				let consumedClass = "success";
				if (consumedPercent >= 80) {
					consumedClass = "error";
				} else if (consumedPercent >= 50) {
					consumedClass = "limited";
				}
				legendItems.push({
					label: "Consumed",
					fullLabel: "Consumed",
					detailLabel: "",
					detailValue: formatPercent(consumedPercent),
					valueClass: consumedClass,
					color: CONSUMED_COLOR,
				});
			}
			return {
				title: window.title,
				total: `${formatCompactNumber(remaining)}/${formatCompactNumber(capacity)}`,
				range: window.range,
				gradient,
				items: legendItems,
			};
		});

		const accountCards = accounts.map((account) => {
			const secondaryRemaining =
				toNumber(account.usage?.secondaryRemainingPercent) || 0;
			const remainingRounded = formatPercentValue(secondaryRemaining);
			return {
				email: account.email,
				accountId: account.id,
				plan: planLabel(account.plan),
				status: {
					class: account.status,
					label: statusLabel(account.status),
				},
				remaining: remainingRounded,
				remainingText: formatPercent(secondaryRemaining),
				progressClass: calculateProgressClass(account.status, secondaryRemaining),
				marquee: account.status === "deactivated",
				meta: formatQuotaResetMeta(
					account.resetAtSecondary,
					secondaryWindowMinutes,
				),
				actions: buildAccountActions(account),
			};
		});

		const requests = state.dashboardData.recentRequests.map((request) => {
			const rawError = request.errorMessage || request.errorCode || "";
			const accountLabel = formatAccountLabel(request.accountId, accounts);
			const modelLabel = formatModelLabel(request.model, request.reasoningEffort);
			return {
				key: `${request.requestId}-${request.timestamp}`,
				requestId: request.requestId,
				time: formatTimeLong(request.timestamp),
				account: accountLabel,
				model: modelLabel,
				status: {
					class: requestStatusClass(request.status),
					label: requestStatusLabel(request.status),
				},
				tokens: formatTokensWithCached(request.tokens, request.cachedInputTokens),
				cost: formatCurrency(request.cost),
				error: rawError ? truncateText(rawError, 80) : "--",
				errorTitle: rawError,
			};
		});

		return {
			heroTitle: state.ui.heroTitle,
			heroBody: state.ui.heroBody,
			badges,
			stats,
			donuts,
			accountCards,
			requests,
		};
	};

	const buildAccountActions = (account) => {
		if (account.status === "deactivated") {
			return [
				{ label: "Details", type: "details" },
				{ label: "Re-authenticate", type: "reauth" },
			];
		}
		if (account.status === "paused") {
			return [
				{ label: "Details", type: "details" },
				{ label: "Resume", type: "resume" },
			];
		}
		return [{ label: "Details", type: "details" }];
	};

	const formatAccessTokenLabel = (auth) => {
		const expiresAt = auth?.access?.expiresAt;
		if (!expiresAt) {
			return "Missing";
		}
		const expiresDate = parseDate(expiresAt);
		if (!expiresDate) {
			return "Unknown";
		}
		const diffMs = expiresDate.getTime() - Date.now();
		if (diffMs <= 0) {
			return "Expired";
		}
		return `Valid (${formatRelative(diffMs)})`;
	};

	const formatRefreshTokenLabel = (auth) => {
		const state = auth?.refresh?.state;
		const map = {
			stored: "Stored",
			missing: "Missing",
			expired: "Expired",
		};
		return map[state] || "Unknown";
	};

	const formatIdTokenLabel = (auth) => {
		const state = auth?.idToken?.state;
		const map = {
			parsed: "Parsed",
			unknown: "Unknown",
		};
		return map[state] || "Unknown";
	};

	const readResponsePayload = async (response) => response.json();

	const extractErrorMessage = (payload) => {
		if (!payload) {
			return "";
		}
		if (typeof payload === "string") {
			return payload;
		}
		if (payload.error?.message) {
			return payload.error.message;
		}
		if (payload.message) {
			return payload.message;
		}
		return "";
	};

	const buildImportSummary = (payload) => {
		if (!payload || typeof payload !== "object") {
			return "auth.json imported.";
		}
		const email = payload.email || "Account";
		const id = payload.accountId;
		const plan = payload.planType;
		const status = payload.status ? normalizeAccountStatus(payload.status) : "";
		const details = [];
		if (id) {
			details.push(id);
		}
		if (plan) {
			details.push(planLabel(plan));
		}
		if (status) {
			details.push(statusLabel(status));
		}
		if (details.length) {
			return `${email} (${details.join(" | ")}) imported.`;
		}
		return `${email} imported.`;
	};

	const fetchJson = async (url, label) => {
		const response = await fetch(url, { cache: "no-store" });
		const payload = await readResponsePayload(response);
		if (!response.ok) {
			const message = extractErrorMessage(payload);
			throw new Error(
				message || `Failed to load ${label} (${response.status})`,
			);
		}
		return payload;
	};

	const postJson = async (url, payload, label) => {
		const response = await fetch(url, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload || {}),
		});
		const responsePayload = await readResponsePayload(response);
		if (!response.ok) {
			const message = extractErrorMessage(responsePayload);
			throw new Error(message || `Failed to ${label} (${response.status})`);
		}
		return responsePayload;
	};

	const putJson = async (url, payload, label) => {
		const response = await fetch(url, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload || {}),
		});
		const responsePayload = await readResponsePayload(response);
		if (!response.ok) {
			const message = extractErrorMessage(responsePayload);
			throw new Error(message || `Failed to ${label} (${response.status})`);
		}
		return responsePayload;
	};

	const deleteJson = async (url, label) => {
		const response = await fetch(url, { method: "DELETE" });
		const responsePayload = await readResponsePayload(response);
		if (!response.ok) {
			const message = extractErrorMessage(responsePayload);
			throw new Error(message || `Failed to ${label} (${response.status})`);
		}
		return responsePayload;
	};

	const fetchAccounts = async () => {
		const payload = await fetchJson(API_ENDPOINTS.accounts, "accounts");
		return normalizeAccountsPayload(payload);
	};

	const fetchUsageSummary = async () =>
		fetchJson(API_ENDPOINTS.usageSummary, "usage summary");

	const fetchUsageWindow = async (window) =>
		fetchJson(API_ENDPOINTS.usageWindow(window), `usage window (${window})`);

	const fetchRequestLogs = async (limit) =>
		fetchJson(API_ENDPOINTS.requestLogs(limit), "request logs");

	const normalizeSettingsPayload = (payload) => ({
		stickyThreadsEnabled: Boolean(payload?.stickyThreadsEnabled),
		preferEarlierResetAccounts: Boolean(payload?.preferEarlierResetAccounts),
	});

	const fetchSettings = async () => {
		const payload = await fetchJson(API_ENDPOINTS.settings, "settings");
		return normalizeSettingsPayload(payload);
	};

	const registerApp = () => {
		Alpine.data("feApp", () => ({
			view: "dashboard",
			pages: createPages(),
			backendPath: "/backend-api",
			ui: createUiConfig(),
			dashboardData: createEmptyDashboardData(),
			dashboard: createEmptyDashboardView(),
			settings: {
				stickyThreadsEnabled: false,
				preferEarlierResetAccounts: false,
				isSaving: false,
			},
			accounts: {
				selectedId: "",
				rows: [],
				searchQuery: "",
			},
			importState: {
				isLoading: false,
				fileName: "",
			},
			messageBox: {
				open: false,
				title: "",
				message: "",
				details: "",
				iconTone: "",
				mode: "alert",
				confirmLabel: "OK",
				cancelLabel: "Cancel",
			},
			messageBoxResolver: null,
			authDialog: {
				open: false,
				stage: "intro",
				selectedMethod: "browser",
				isLoading: false,
				authorizationUrl: "",
				verificationUrl: "",
				userCode: "",
				deviceAuthId: "",
				intervalSeconds: 5,
				expiresInSeconds: 0,
				expiresAt: 0,
				remainingSeconds: 0,
				status: "idle",
				statusLabel: "",
				errorMessage: "",
				pollTimerId: null,
				countdownTimerId: null,
			},
			isLoading: true,
			hasInitialized: false,
			refreshPromise: null,
			async init() {
				if (this.hasInitialized) {
					return;
				}
				this.hasInitialized = true;
				this.view = getViewFromPath(window.location.pathname);
				await this.loadData();
				this.syncTitle();
				this.syncUrl(true);
				this.$watch("view", () => {
					this.syncTitle();
					this.syncUrl(false);
				});
				this.$watch("accounts.searchQuery", () => {
					this.syncAccountSearchSelection();
				});
				window.addEventListener("popstate", (event) => {
					const newView =
						event.state?.view || getViewFromPath(window.location.pathname);
					if (newView !== this.view) {
						this.view = newView;
					}
				});
			},
			async loadData() {
				try {
					await this.refreshAll({ silent: true });
					if (!this.pages.some((page) => page.id === this.view)) {
						this.view = this.pages[0].id;
					}
				} catch (error) {
					const message = error.message || "Failed to load data.";
					this.openMessageBox({
						tone: "error",
						title: "Dashboard load failed",
						message,
					});
				} finally {
					this.isLoading = false;
				}
			},
			async refreshAll(options = {}) {
				if (this.refreshPromise) {
					return this.refreshPromise;
				}
				const { preferredId, silent = false } = options;
				this.refreshPromise = (async () => {
					const [
						accountsResult,
						summaryResult,
						primaryResult,
						secondaryResult,
						requestLogsResult,
						settingsResult,
					] = await Promise.allSettled([
						fetchAccounts(),
						fetchUsageSummary(),
						fetchUsageWindow("primary"),
						fetchUsageWindow("secondary"),
						fetchRequestLogs(50),
						fetchSettings(),
					]);
					const errors = [];
					if (accountsResult.status !== "fulfilled") {
						throw accountsResult.reason;
					}
					const summary =
						summaryResult.status === "fulfilled" ? summaryResult.value : null;
					if (summaryResult.status === "rejected") {
						errors.push(summaryResult.reason);
					}
					const primaryUsage =
						primaryResult.status === "fulfilled"
							? normalizeUsagePayload(primaryResult.value)
							: [];
					if (primaryResult.status === "rejected") {
						errors.push(primaryResult.reason);
					}
					const secondaryUsage =
						secondaryResult.status === "fulfilled"
							? normalizeUsagePayload(secondaryResult.value)
							: [];
					if (secondaryResult.status === "rejected") {
						errors.push(secondaryResult.reason);
					}
					const requestLogs =
						requestLogsResult.status === "fulfilled"
							? normalizeRequestLogsPayload(requestLogsResult.value)
							: [];
					if (requestLogsResult.status === "rejected") {
						errors.push(requestLogsResult.reason);
					}

					const settings =
						settingsResult.status === "fulfilled" ? settingsResult.value : null;
					if (settingsResult.status === "rejected") {
						errors.push(settingsResult.reason);
					}

					const mergedAccounts = mergeUsageIntoAccounts(
						accountsResult.value,
						primaryUsage,
						secondaryUsage,
					);
					this.applyData(
						{
							accounts: mergedAccounts,
							summary,
							primaryUsage,
							secondaryUsage,
							requestLogs,
							settings,
						},
						preferredId,
					);

					if (errors.length && !silent) {
						this.openMessageBox({
							tone: "warning",
							title: "Partial data loaded",
							message:
								"Some usage endpoints failed. Account list loaded, but usage data may be incomplete.",
							details: errors
								.map((err) => err?.message || String(err))
								.join("\n"),
						});
					}
				})();
				try {
					await this.refreshPromise;
				} finally {
					this.refreshPromise = null;
				}
			},
			applyData(data, preferredId) {
				this.accounts.rows = Array.isArray(data.accounts) ? data.accounts : [];
				const hasPreferred =
					preferredId &&
					this.accounts.rows.some((account) => account.id === preferredId);
				if (hasPreferred) {
					this.accounts.selectedId = preferredId;
				} else if (
					this.accounts.selectedId &&
					!this.accounts.rows.some(
						(account) => account.id === this.accounts.selectedId,
					)
				) {
					this.accounts.selectedId = this.accounts.rows[0]?.id || "";
				} else if (!this.accounts.selectedId && this.accounts.rows.length > 0) {
					this.accounts.selectedId = this.accounts.rows[0].id;
				}
				this.dashboardData = buildDashboardDataFromApi({
					summary: data.summary,
					primaryUsage: data.primaryUsage,
					secondaryUsage: data.secondaryUsage,
					requestLogs: data.requestLogs,
					settings: data.settings,
				});
				if (data.settings) {
					this.settings.stickyThreadsEnabled = Boolean(
						data.settings.stickyThreadsEnabled,
					);
					this.settings.preferEarlierResetAccounts = Boolean(
						data.settings.preferEarlierResetAccounts,
					);
				}
				this.ui.usageWindows = buildUsageWindowConfig(data.summary);
				this.dashboard = buildDashboardView(this);
				this.syncAccountSearchSelection();
			},
			async saveSettings() {
				if (this.settings.isSaving) {
					return;
				}
				this.settings.isSaving = true;
				try {
					const payload = {
						stickyThreadsEnabled: this.settings.stickyThreadsEnabled,
						preferEarlierResetAccounts: this.settings.preferEarlierResetAccounts,
					};
					const updated = await putJson(
						API_ENDPOINTS.settings,
						payload,
						"save settings",
					);
					const normalized = normalizeSettingsPayload(updated);
					this.settings.stickyThreadsEnabled = normalized.stickyThreadsEnabled;
					this.settings.preferEarlierResetAccounts =
						normalized.preferEarlierResetAccounts;
					this.openMessageBox({
						tone: "success",
						title: "Settings saved",
						message: "Routing settings updated.",
					});
				} catch (error) {
					this.openMessageBox({
						tone: "error",
						title: "Settings save failed",
						message: error.message || "Failed to save settings.",
					});
				} finally {
					this.settings.isSaving = false;
				}
			},
			focusAccountSearch() {
				this.$refs.accountSearch?.focus();
			},
			clearAccountSearch() {
				this.accounts.searchQuery = "";
				this.focusAccountSearch();
			},
			syncAccountSearchSelection() {
				const query = normalizeSearchInput(this.accounts.searchQuery);
				if (!query) {
					return;
				}
				const filtered = filterAccountsByQuery(this.accounts.rows, query);
				if (!filtered.length) {
					return;
				}
				const hasSelected = filtered.some(
					(account) => account.id === this.accounts.selectedId,
				);
				if (!hasSelected) {
					this.accounts.selectedId = filtered[0].id;
				}
			},
			async handleAuthImport(event) {
				console.info("[auth-import] change event", {
					hasFiles: Boolean(event?.target?.files?.length),
				});
				const file = event.target.files && event.target.files[0];
				event.target.value = "";
				if (!file) {
					console.warn("[auth-import] no file selected");
					return;
				}
				await this.importAuthFile(file);
			},
			async importAuthFile(file) {
				console.info("[auth-import] start", {
					name: file?.name,
					size: file?.size,
					type: file?.type,
				});
				this.importState.isLoading = true;
				this.importState.fileName = file.name || "auth.json";
				try {
					const formData = new FormData();
					formData.append("auth_json", file, file.name || "auth.json");
					const response = await fetch(API_ENDPOINTS.accountsImport, {
						method: "POST",
						body: formData,
					});
					if (!response.ok) {
						const payload = await readResponsePayload(response);
						const message = extractErrorMessage(payload);
						throw new Error(message || `Import failed (${response.status})`);
					}
					const payload = await readResponsePayload(response);
					const summary = buildImportSummary(payload);
					const preferredId = payload?.accountId;
					let details = "";
					let tone = "success";
					let title = "";
					try {
						await this.refreshAll({ preferredId, silent: true });
					} catch (error) {
						tone = "error";
						title = "Import complete, refresh failed";
						details =
							error.message ||
							"Import completed but the account list could not refresh.";
					}
					this.openMessageBox({ tone, title, message: summary, details });
					console.info("[auth-import] complete via backend");
				} catch (error) {
					console.error("[auth-import] failed", error);
					this.openMessageBox({
						tone: "error",
						message: error.message || "Import failed.",
					});
				} finally {
					this.importState.isLoading = false;
					this.importState.fileName = "";
				}
			},
			logImportClick(source) {
				console.info("[auth-import] click", { source });
			},
			openMessageBox({ tone = "info", title, message, details } = {}) {
				if (this.messageBoxResolver) {
					const resolve = this.messageBoxResolver;
					this.messageBoxResolver = null;
					resolve(false);
				}
				const toneKey = MESSAGE_TONE_META[tone] ? tone : "info";
				const meta = MESSAGE_TONE_META[toneKey] || MESSAGE_TONE_META.info;
				this.messageBox.open = true;
				this.messageBox.title = title || meta.defaultTitle;
				this.messageBox.message = message || "";
				this.messageBox.details = details || "";
				this.messageBox.iconTone = toneKey;
				this.messageBox.mode = "alert";
				this.messageBox.confirmLabel = "OK";
				this.messageBox.cancelLabel = "Cancel";
			},
			openConfirmBox({
				tone = "question",
				title,
				message,
				details,
				confirmLabel = "OK",
				cancelLabel = "Cancel",
			} = {}) {
				if (this.messageBoxResolver) {
					const resolve = this.messageBoxResolver;
					this.messageBoxResolver = null;
					resolve(false);
				}
				const toneKey = MESSAGE_TONE_META[tone] ? tone : "question";
				const meta = MESSAGE_TONE_META[toneKey] || MESSAGE_TONE_META.info;
				this.messageBox.open = true;
				this.messageBox.title = title || meta.defaultTitle;
				this.messageBox.message = message || "";
				this.messageBox.details = details || "";
				this.messageBox.iconTone = toneKey;
				this.messageBox.mode = "confirm";
				this.messageBox.confirmLabel = confirmLabel;
				this.messageBox.cancelLabel = cancelLabel;
				return new Promise((resolve) => {
					this.messageBoxResolver = resolve;
				});
			},
			resolveMessageBox(confirmed) {
				if (this.messageBoxResolver) {
					const resolve = this.messageBoxResolver;
					this.messageBoxResolver = null;
					resolve(Boolean(confirmed));
				}
				this.messageBox.open = false;
				this.messageBox.mode = "alert";
			},
			confirmMessageBox() {
				this.resolveMessageBox(true);
			},
			cancelMessageBox() {
				this.resolveMessageBox(false);
			},
			closeMessageBox() {
				this.resolveMessageBox(false);
			},
			openAddAccountDialog() {
				this.resetAuthDialogState();
				this.authDialog.open = true;
			},
			closeAddAccountDialog() {
				this.authDialog.open = false;
				this.resetAuthDialogState();
			},
			resetAuthDialogState() {
				this.stopAuthTimers();
				this.authDialog.stage = "intro";
				this.authDialog.selectedMethod = "browser";
				this.authDialog.isLoading = false;
				this.authDialog.authorizationUrl = "";
				this.authDialog.verificationUrl = "";
				this.authDialog.userCode = "";
				this.authDialog.deviceAuthId = "";
				this.authDialog.intervalSeconds = 5;
				this.authDialog.expiresInSeconds = 0;
				this.authDialog.expiresAt = 0;
				this.authDialog.remainingSeconds = 0;
				this.authDialog.status = "idle";
				this.authDialog.statusLabel = "";
				this.authDialog.errorMessage = "";
			},
			async startOAuth() {
				if (this.authDialog.isLoading) {
					return;
				}
				this.stopAuthTimers();
				this.authDialog.isLoading = true;
				this.authDialog.errorMessage = "";
				this.authDialog.statusLabel = "Requesting authorization...";
				const forceMethod = this.authDialog.selectedMethod;
				let autoCompleteDevice = false;
				try {
					const payload = await postJson(
						API_ENDPOINTS.oauthStart,
						forceMethod ? { forceMethod } : {},
						"start OAuth",
					);
					if (payload?.method === "browser") {
						this.authDialog.stage = "browser";
						this.authDialog.authorizationUrl = payload.authorizationUrl || "";
						this.authDialog.status = "pending";
						this.authDialog.statusLabel = AUTH_STATUS_LABELS.pendingBrowser;
						this.startAuthPolling();
					} else if (payload?.method === "device") {
						this.authDialog.stage = "device";
						this.authDialog.verificationUrl = payload.verificationUrl || "";
						this.authDialog.userCode = payload.userCode || "";
						this.authDialog.deviceAuthId = payload.deviceAuthId || "";
						this.authDialog.intervalSeconds =
							Number(payload.intervalSeconds) || 5;
						this.authDialog.expiresInSeconds =
							Number(payload.expiresInSeconds) || 0;
						this.authDialog.expiresAt =
							Date.now() + this.authDialog.expiresInSeconds * 1000;
						this.authDialog.status = "pending";
						this.authDialog.statusLabel = AUTH_STATUS_LABELS.pendingDevice;
						this.startAuthPolling();
						this.startAuthCountdown();
						autoCompleteDevice = true;
					} else {
						throw new Error("Unexpected OAuth response.");
					}
				} catch (error) {
					this.authDialog.stage = "error";
					this.authDialog.status = "error";
					this.authDialog.statusLabel = AUTH_STATUS_LABELS.error;
					this.authDialog.errorMessage =
						error.message || "Failed to start OAuth flow.";
				} finally {
					this.authDialog.isLoading = false;
				}
				if (autoCompleteDevice) {
					await this.completeDeviceAuth();
				}
			},
			openAuthorizationUrl() {
				if (this.authDialog.authorizationUrl) {
					window.open(this.authDialog.authorizationUrl, "_blank", "noopener");
				}
			},
			openVerificationUrl() {
				if (this.authDialog.verificationUrl) {
					window.open(this.authDialog.verificationUrl, "_blank", "noopener");
				}
			},
			calculateProgressClass(status, remainingPercent) {
				return calculateProgressClass(status, remainingPercent);
			},
			calculateProgressTextClass(status, remainingPercent) {
				return calculateProgressTextClass(status, remainingPercent);
			},
			async copyToClipboard(value, label, e) {
				if (!value) return;

				// Localized feedback in the button
				let btn = null;
				let originalText = "";
				if (e && e.target) {
					btn = e.target.tagName === "BUTTON" ? e.target : e.target.closest("button");
					if (btn) {
						originalText = btn.textContent;
						btn.textContent = "Copied!";
						btn.classList.add("copy-success");
						window.setTimeout(() => {
							btn.textContent = originalText;
							btn.classList.remove("copy-success");
						}, 4000);
					}
				}

				try {
					if (navigator.clipboard && navigator.clipboard.writeText) {
						await navigator.clipboard.writeText(value);
					} else {
						const textarea = document.createElement("textarea");
						textarea.value = value;
						textarea.setAttribute("readonly", "");
						textarea.style.position = "absolute";
						textarea.style.left = "-9999px";
						document.body.appendChild(textarea);
						textarea.select();
						document.execCommand("copy");
						document.body.removeChild(textarea);
					}

					// Only show message box if auth dialog is not open and button feedback wasn't possible
					if (!this.authDialog.open && !btn) {
						this.openMessageBox({
							tone: "success",
							title: "Copied",
							message: `${label} copied to clipboard.`,
						});
					}
				} catch (err) {
					console.error("Clipboard error:", err);
					if (btn) {
						btn.textContent = "Failed";
						btn.classList.remove("copy-success");
						window.setTimeout(() => { btn.textContent = originalText; }, 2000);
					} else if (!this.authDialog.open) {
						this.openMessageBox({
							tone: "error",
							title: "Copy failed",
							message: `Could not copy ${label}.`,
						});
					}
				}
			},
			stopAuthPolling() {
				if (this.authDialog.pollTimerId) {
					clearInterval(this.authDialog.pollTimerId);
					this.authDialog.pollTimerId = null;
				}
			},
			stopAuthCountdown() {
				if (this.authDialog.countdownTimerId) {
					clearInterval(this.authDialog.countdownTimerId);
					this.authDialog.countdownTimerId = null;
				}
			},
			stopAuthTimers() {
				this.stopAuthPolling();
				this.stopAuthCountdown();
			},
			startAuthPolling() {
				this.stopAuthPolling();
				const intervalMs = Math.max(this.authDialog.intervalSeconds, 2) * 1000;
				this.authDialog.pollTimerId = window.setInterval(() => {
					this.checkAuthStatus();
				}, intervalMs);
				this.checkAuthStatus();
			},
			startAuthCountdown() {
				this.stopAuthCountdown();
				const update = () => {
					const remaining = Math.ceil(
						(this.authDialog.expiresAt - Date.now()) / 1000,
					);
					this.authDialog.remainingSeconds = Math.max(0, remaining);
					if (this.authDialog.remainingSeconds <= 0) {
						if (this.authDialog.status === "pending") {
							this.authDialog.stage = "error";
							this.authDialog.status = "error";
							this.authDialog.statusLabel = AUTH_STATUS_LABELS.error;
							this.authDialog.errorMessage =
								"Device code expired. Start the sign-in again.";
							this.stopAuthTimers();
						}
					}
				};
				update();
				this.authDialog.countdownTimerId = window.setInterval(update, 1000);
			},
			async checkAuthStatus() {
				if (!this.authDialog.open) {
					return;
				}
				try {
					const payload = await fetchJson(
						API_ENDPOINTS.oauthStatus,
						"oauth status",
					);
					if (payload?.status === "success") {
						this.authDialog.status = "success";
						this.authDialog.statusLabel = AUTH_STATUS_LABELS.success;
						this.authDialog.stage = "success";
						this.stopAuthTimers();
						try {
							await this.refreshAll({ silent: true });
						} catch (error) {
							console.warn("[oauth] refresh accounts failed", error);
						}
						return;
					}
					if (payload?.status === "error") {
						this.authDialog.status = "error";
						this.authDialog.statusLabel = AUTH_STATUS_LABELS.error;
						this.authDialog.stage = "error";
						this.authDialog.errorMessage =
							payload?.errorMessage || "Authorization failed.";
						this.stopAuthTimers();
						return;
					}
					this.authDialog.status = "pending";
				} catch (error) {
					this.authDialog.status = "error";
					this.authDialog.statusLabel = AUTH_STATUS_LABELS.error;
					this.authDialog.stage = "error";
					this.authDialog.errorMessage =
						error.message || "Failed to fetch OAuth status.";
					this.stopAuthTimers();
				}
			},
			async completeDeviceAuth() {
				if (this.authDialog.isLoading) {
					return;
				}
				this.authDialog.isLoading = true;
				try {
					const payload = {};
					if (this.authDialog.deviceAuthId) {
						payload.deviceAuthId = this.authDialog.deviceAuthId;
					}
					if (this.authDialog.userCode) {
						payload.userCode = this.authDialog.userCode;
					}
					await postJson(
						API_ENDPOINTS.oauthComplete,
						payload,
						"complete OAuth",
					);
					this.authDialog.statusLabel = AUTH_STATUS_LABELS.pendingDevice;
				} catch (error) {
					this.authDialog.status = "error";
					this.authDialog.statusLabel = AUTH_STATUS_LABELS.error;
					this.authDialog.stage = "error";
					this.authDialog.errorMessage =
						error.message || "Failed to complete device code flow.";
					this.stopAuthTimers();
				} finally {
					this.authDialog.isLoading = false;
				}
			},
			syncTitle() {
				document.title = this.currentPage.title;
			},
			syncUrl(replace = false) {
				const targetPath = getPathFromView(this.view);
				const currentPath =
					window.location.pathname.replace(/\/+$/, "") || BASE_PATH;
				if (currentPath !== targetPath) {
					const state = { view: this.view };
					if (replace) {
						window.history.replaceState(state, "", targetPath);
					} else {
						window.history.pushState(state, "", targetPath);
					}
				}
			},
			setView(id) {
				if (this.view !== id) {
					this.view = id;
				}
			},
			navigateTo(path) {
				const viewId = getViewFromPath(path);
				this.setView(viewId);
			},
			get currentPage() {
				return (
					this.pages.find((page) => page.id === this.view) ||
					this.pages[0] || {
						title: "Codex ChatGPT Proxy - FE Concepts",
						status: [],
					}
				);
			},
			get statusItems() {
				const lastSync = formatTimeLong(this.dashboardData.lastSyncAt);
				const items =
					this.view === "accounts"
						? [
							`Selection: ${this.accounts.selectedId || "--"}`,
							`Rotation: ${this.dashboardData.routing?.rotationEnabled ? "enabled" : "disabled"}`,
							`Last sync: ${lastSync}`,
						]
						: [
							`Last sync: ${lastSync}`,
							`Routing: ${routingLabel(this.dashboardData.routing?.strategy)}`,
							`Backend: ${this.backendPath}`,
						];
				if (this.importState.isLoading) {
					items.unshift(
						`Importing ${this.importState.fileName || "auth.json"}...`,
					);
				}
				return items;
			},
			get filteredAccounts() {
				return filterAccountsByQuery(
					this.accounts.rows,
					this.accounts.searchQuery,
				);
			},
			get selectedAccount() {
				return (
					this.accounts.rows.find(
						(account) => account.id === this.accounts.selectedId,
					) ||
					this.accounts.rows[0] ||
					{}
				);
			},
			selectAccount(id) {
				this.accounts.selectedId = id;
			},
			handleAccountAction(action, card) {
				if (!action || !card) {
					return;
				}
				if (action.type === "details") {
					this.view = "accounts";
					this.selectAccount(card.accountId);
					return;
				}
				if (action.type === "reauth") {
					this.startReauthFlow();
					return;
				}
				if (action.type === "resume") {
					this.resumeAccount(card.accountId);
				}
			},
			startReauthFlow() {
				this.openAddAccountDialog();
			},
			async resumeAccount(accountId) {
				if (!accountId) {
					this.openMessageBox({
						tone: "warning",
						title: "No account selected",
						message: "Select an account before resuming.",
					});
					return;
				}
				const accountLabel = formatAccountLabel(accountId, this.accounts.rows);
				const confirmed = await this.openConfirmBox({
					title: "Resume account?",
					message: `Resume account ${accountLabel}? Routing will include it again.`,
					confirmLabel: "Resume",
					cancelLabel: "Cancel",
				});
				if (!confirmed) {
					return;
				}
				this.authDialog.isLoading = true;
				postJson(
					API_ENDPOINTS.accountReactivate(accountId),
					{},
					"resume account",
				)
					.then(() => this.refreshAll({ preferredId: accountId }))
					.then(() => {
						this.openMessageBox({
							tone: "success",
							title: "Account resumed",
							message: `${accountLabel} is active again.`,
						});
					})
					.catch((error) => {
						this.openMessageBox({
							tone: "error",
							title: "Resume failed",
							message: error.message || "Failed to resume account.",
						});
					})
					.finally(() => {
						this.authDialog.isLoading = false;
					});
			},
			async resumeSelectedAccount() {
				return this.resumeAccount(this.selectedAccount.id);
			},
			async pauseSelectedAccount() {
				const accountId = this.selectedAccount.id;
				if (!accountId) {
					this.openMessageBox({
						tone: "warning",
						title: "No account selected",
						message: "Select an account before pausing.",
					});
					return;
				}
				const accountLabel = formatAccountLabel(accountId, this.accounts.rows);
				const confirmed = await this.openConfirmBox({
					title: "Pause account?",
					message: `Pause account ${accountLabel}? Routing will skip it until resumed.`,
					confirmLabel: "Pause",
					cancelLabel: "Cancel",
				});
				if (!confirmed) {
					return;
				}
				this.authDialog.isLoading = true;
				postJson(API_ENDPOINTS.accountPause(accountId), {}, "pause account")
					.then(() => this.refreshAll({ preferredId: accountId }))
					.then(() => {
						this.openMessageBox({
							tone: "success",
							title: "Account paused",
							message: `${accountLabel} paused.`,
						});
					})
					.catch((error) => {
						this.openMessageBox({
							tone: "error",
							title: "Pause failed",
							message: error.message || "Failed to pause account.",
						});
					})
					.finally(() => {
						this.authDialog.isLoading = false;
					});
			},
			async deleteSelectedAccount() {
				const accountId = this.selectedAccount.id;
				if (!accountId) {
					this.openMessageBox({
						tone: "warning",
						title: "No account selected",
						message: "Select an account before deleting.",
					});
					return;
				}
				const accountLabel = formatAccountLabel(accountId, this.accounts.rows);
				const confirmed = await this.openConfirmBox({
					title: "Delete account?",
					message: `Delete account ${accountLabel}? This cannot be undone.`,
					confirmLabel: "Delete",
					cancelLabel: "Cancel",
				});
				if (!confirmed) {
					return;
				}
				try {
					await deleteJson(
						API_ENDPOINTS.accountDelete(accountId),
						"delete account",
					);
					await this.refreshAll({ preferredId: "" });
					this.openMessageBox({
						tone: "success",
						title: "Account deleted",
						message: `${accountLabel} removed.`,
					});
				} catch (error) {
					this.openMessageBox({
						tone: "error",
						title: "Delete failed",
						message: error.message || "Failed to delete account.",
					});
				}
			},
			statusBadgeText,
			statusLabel,
			requestStatusLabel,
			requestStatusClass,
			calculateTextUsageTextClass,
			progressClass,
			planLabel,
			routingLabel,
			errorLabel,
			formatNumber,
			formatCompactNumber,
			formatPercent,
			formatWindowMinutes,
			formatWindowLabel,
			formatPercentValue,
			formatRate,
			formatTimeLong,
			formatCountdown,
			formatQuotaResetLabel,
			formatAccessTokenLabel,
			formatRefreshTokenLabel,
			formatAccessTokenLabel,
			formatRefreshTokenLabel,
			formatIdTokenLabel,
			theme: localStorage.getItem('theme') || 'dark',
			toggleTheme() {
				this.theme = this.theme === 'dark' ? 'light' : 'dark';
				localStorage.setItem('theme', this.theme);
			},
		}));
	};

	if (window.Alpine) {
		registerApp();
	} else {
		document.addEventListener("alpine:init", registerApp, { once: true });
	}
})();
